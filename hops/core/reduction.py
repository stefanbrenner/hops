
import os
import sys
import shutil
import tempfile
import numpy as np
import exoclock
import hops.pylightcurve41 as plc
import multiprocessing as mp

from concurrent.futures import ProcessPoolExecutor, FIRST_COMPLETED, wait
from concurrent.futures.process import BrokenProcessPool
from astropy.io import fits as pf

from hops.hops_tools.fits import get_fits_data_and_header
from hops.hops_tools.image_analysis import image_mean_std, image_burn_limit, image_psf, bin_frame

from .common import HOPSError, downsample

PREVIEW_SIZE = 1600
COMBINE_ROWS = 256


def _combine(frames, method):
    """Pixel-wise median / mean of a list of frames.

    Same values as the row-by-row loop of the Tk version, but vectorised over blocks of rows
    (which also keeps the extra memory small). A single frame is its own median / mean.
    """
    if len(frames) == 1:
        return np.array(frames[0], dtype=float)
    combine = np.nanmean if method == 'mean' else np.nanmedian
    result = np.empty_like(frames[0], dtype=float)
    for start in range(0, len(frames[0]), COMBINE_ROWS):
        rows = slice(start, start + COMBINE_ROWS)
        result[rows] = combine(np.stack([frame[rows] for frame in frames]), axis=0)
    return result


def _clean_flat(master_flat):
    """Replace bad pixels (<= 0, NaN, inf) of the master flat by interpolating along each row."""

    bp_map = np.zeros_like(master_flat)
    bp_map = np.where(master_flat <= 0, 1, bp_map)
    bp_map = np.where(np.isnan(master_flat), 1, bp_map)
    bp_map = np.where(master_flat == np.inf, 1, bp_map)

    # The original code runs `master_flat[np.int_(bp_map)] = 1`: indexing with the 0/1 map selects
    # whole rows, so it only sets row 0 (if there is a good pixel) and row 1 (if there is a bad pixel)
    # to 1 - but it writes every row once per pixel, which takes ~15 s for a 26 Mpixel frame.
    # Same result, directly:
    if (bp_map == 0).any():
        master_flat[0] = 1
    if (bp_map == 1).any():
        master_flat[1] = 1
    bp_map[:, 0] = 0
    bp_map[:, -1] = 0

    # The Tk version used scipy's interp1d per row, which for these inputs (integer positions, float
    # values, no extrapolation) evaluates with numpy.interp; calling numpy.interp directly gives the
    # same values without building an interpolator per row. Rows without bad pixels are returned
    # unchanged by the interpolation, so only the others are processed.
    columns = np.arange(master_flat.shape[1])
    for line in np.where(bp_map.any(axis=1))[0]:
        clean_pixs = np.where(bp_map[line] == 0)[0]
        master_flat[line] = np.interp(columns, clean_pixs, master_flat[line][clean_pixs])

    return master_flat


class _FileChecker:

    def __init__(self):
        self.file_info = None

    def check(self, fits_name, fits_data, fits_header):
        info = [len(fits_data[0]), len(fits_data), fits_header['BITPIX']]
        if self.file_info is None:
            self.file_info = info
        elif info != self.file_info:
            raise HOPSError(_size_message(fits_name, info))

    def load(self, fits_name):
        fits_data, fits_header = get_fits_data_and_header(fits_name)
        self.check(fits_name, fits_data, fits_header)
        return fits_data, fits_header


def _size_message(fits_name, info):
    return ('File {0} has different size from the other files in the dataset:\n'
            'x-size: {1} pixels\ny-size: {2} pixels\n{3}-bit\n'
            'Reduction will terminate, please check your files!'.format(os.path.basename(fits_name), *info))


def _read_header(path):
    """The header get_fits_data_and_header would return, without reading the image data."""
    with pf.open(path, memmap=False) as fits_file:
        fits_file.verify('fix')
        if 'SCI' in fits_file:
            header = fits_file['SCI'].header
            return {key: header[key] for key in header}
        for hdu in fits_file:
            if hdu.header.get('NAXIS', 0) > 0:
                return {key: hdu.header[key] for key in hdu.header if key not in ['HISTORY', 'COMMENT', '']}
        header = fits_file[0].header
        return {key: header[key] for key in header if key not in ['HISTORY', 'COMMENT', '']}


# science frames: one function per frame, so the frames can be reduced in parallel

_MASTERS = {}


def _load_master(value):
    """Masters are numbers (no calibration) or .npy files, memory-mapped so all processes share them."""
    if isinstance(value, str):
        return np.load(value, mmap_mode='r')
    return value


def _init_worker(masters):
    _MASTERS.update({key: _load_master(value) for key, value in masters.items()})


def _reduce_frame(task):
    """Calibrate, crop, bin, measure and write one science frame. Runs in a worker process (or in the
    calling process); returns the frame statistics and a small preview."""

    s = task['settings']
    master_bias = _MASTERS['bias']
    master_dark = _MASTERS['dark']
    master_flat = _MASTERS['flat']
    bias_frames_exp = _MASTERS['bias_exp']

    fits_data, fits_header = get_fits_data_and_header(task['file'])
    info = [len(fits_data[0]), len(fits_data), fits_header['BITPIX']]
    if info != s['file_info']:
        raise HOPSError(_size_message(task['file'], info))

    saturation = image_burn_limit(fits_header, key=s['saturation_key'])
    exp_time = task['exp_time']

    # get_fits_data_and_header returns a fresh float array: calibrate it in place
    data_frame = fits_data
    dq_frame = data_frame == saturation
    data_frame -= master_bias
    data_frame -= (exp_time - bias_frames_exp) * master_dark
    data_frame /= master_flat
    data_frame[np.isnan(data_frame)] = 0

    crop_x1 = int(max(0, s['crop_x1']))
    crop_x2 = int(min(s['crop_x2'], len(data_frame[0])))
    crop_y1 = int(max(0, s['crop_y1']))
    crop_y2 = int(min(s['crop_y2'], len(data_frame)))

    if crop_x2 == 0:
        crop_x2 = len(data_frame[0])
    if crop_y2 == 0:
        crop_y2 = len(data_frame)

    if not (np.array([crop_x1, crop_x2, crop_y1, crop_y2]) == np.array([0, len(data_frame[0]), 0, len(data_frame)])).all():
        data_frame = data_frame[crop_y1: crop_y2, crop_x1: crop_x2]
        dq_frame = dq_frame[crop_y1: crop_y2, crop_x1: crop_x2]

    edge = s['crop_edge_pixels']
    if edge > 0:
        data_frame = data_frame[edge: -edge, edge: -edge]
        dq_frame = dq_frame[edge: -edge, edge: -edge]

    bin_fits = s['bin_fits']
    if bin_fits > 1:
        data_frame = bin_frame(data_frame, bin_fits)
        saturation = saturation * bin_fits * bin_fits
        dq_frame = bin_frame(dq_frame.astype(int), bin_fits) > 0

    data_frame[dq_frame] = saturation

    mean, std = image_mean_std(data_frame, samples=10000, mad_filter=5.0)

    psf = image_psf(data_frame, fits_header, mean, std, 0.8 * saturation,
                    centroids_snr=s['centroids_snr'], stars_snr=s['stars_snr'], psf_guess=s['psf_guess'])
    if np.isnan(psf):
        psf = 10
        skip = True
    else:
        skip = False

    primary = pf.PrimaryHDU()
    image = pf.CompImageHDU()
    image.data = np.array(data_frame, dtype=np.int32)
    image.header.set('BITPIX', fits_header['BITPIX'])
    image.header.set('NAXIS1', len(fits_data[0]))
    image.header.set('NAXIS2', len(fits_data))
    image.header.set('XBINNING', bin_fits)
    image.header.set('YBINNING', bin_fits)
    image.header.set('BZERO', 0)
    image.header.set('BSCALE', 1)
    for key, value in task['header']:
        image.header.set(key, value)
    image.header.set(s['keys']['mean'], mean)
    image.header.set(s['keys']['std'], std)
    image.header.set(s['keys']['saturation'], saturation)
    image.header.set(s['keys']['psf'], psf)
    image.header.set(s['keys']['skip'], skip)
    image.header.set(s['keys']['align_x0'], False)
    image.header.set(s['keys']['align_y0'], False)
    image.header.set(s['keys']['align_u0'], False)

    plc.save_fits(pf.HDUList([primary, image]), task['output'])

    preview, factor = downsample(data_frame, PREVIEW_SIZE)
    return {'mean': mean, 'std': std, 'psf': psf, 'skip': skip, 'preview': preview, 'factor': factor}


def worker_count(n_frames, frame_pixels):
    """How many frames to reduce at the same time: limited by the CPU cores and by the memory (a frame
    needs about six float copies while it is reduced). HOPS_WORKERS overrides it."""
    if os.environ.get('HOPS_WORKERS'):
        return max(1, min(int(os.environ['HOPS_WORKERS']), n_frames))
    cores = max(1, (os.cpu_count() or 2) - 1)
    try:
        memory = os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
    except (ValueError, OSError, AttributeError):
        memory = 8 * 1024 ** 3
    per_worker = max(frame_pixels * 8 * 6, 256 * 1024 ** 2)
    by_memory = max(1, int(0.5 * memory / per_worker))
    workers = min(cores, by_memory, n_frames)
    # starting worker processes costs a few seconds: not worth it for a handful of frames
    return workers if n_frames >= 2 * workers and workers > 1 else 1


def run_reduction(job, project, show_all_frames):
    """Port of ReductionWindow. `show_all_frames` is a callable returning the current checkbox state."""

    log = project.log
    colour_camera_mode = log.get_param('colour_camera_mode')
    exposure_time_key = log.get_param('exposure_time_key')

    bias_files = project.find_fits(log.get_param('bias_files'))
    dark_files = project.find_fits(log.get_param('dark_files'))
    darkf_files = project.find_fits(log.get_param('darkf_files'))
    flat_files = project.find_fits(log.get_param('flat_files'))
    science_files = project.find_fits(log.get_param('observation_files'))

    if len(science_files) == 0:
        raise HOPSError('No observation files found.')

    location_string = log.get_param('location').split(' ')
    observatory = exoclock.Observatory(exoclock.Degrees(location_string[0]), exoclock.Degrees(location_string[1]))
    ra_dec_string = log.get_param('target_ra_dec').split(' ')
    target = exoclock.FixedTarget(exoclock.Hours(ra_dec_string[0]), exoclock.Degrees(ra_dec_string[1]))
    phot_filter = log.get_param('filter')

    checker = _FileChecker()

    first_data, first_header = checker.load(science_files[0])
    preview, factor = downsample(first_data, PREVIEW_SIZE)
    job.set_preview(preview, os.path.basename(science_files[0]), factor=factor)
    frame_pixels = first_data.size
    del first_data

    log.set_param('reduction_complete', False)
    project.save_log()

    if os.path.isdir(project.reduction_directory):
        shutil.rmtree(project.reduction_directory)
    os.mkdir(project.reduction_directory)

    for key, label, files in [['bias', 'Loading bias frames', bias_files],
                              ['dark', 'Loading dark frames', dark_files],
                              ['darkf', 'Loading dark-flat frames', darkf_files],
                              ['flat', 'Loading flat frames', flat_files]]:
        if len(files) > 0:
            job.task(key, label, len(files))
    job.task('science', 'Reducing science frames and calculating statistics', len(science_files))

    # bias

    bias_frames = []
    bias_frames_exp = []
    for bias_file in bias_files:
        job.check()
        fits_data, fits_header = checker.load(bias_file)
        bias_frames.append(fits_data)
        try:
            bias_exptime = fits_header[exposure_time_key]
        except KeyError:
            bias_exptime = 0
        bias_frames_exp.append(bias_exptime)
        job.step('bias', message=os.path.basename(bias_file))

    if len(bias_frames) > 0:
        job.task_message('bias', 'Calculating master bias...')
        consistent_exp_time = np.array(bias_frames_exp) == np.median(bias_frames_exp)
        bias_frames = [bias_frames[ff] for ff in range(len(bias_frames)) if consistent_exp_time[ff]]
        master_bias = _combine(bias_frames, log.get_param('master_bias_method'))
        bias_frames_exp = np.median(bias_frames_exp)
        job.task_done('bias', 'Calculating master bias... Completed!')
    else:
        master_bias = 0.0
        bias_frames_exp = 0.0
    del bias_frames

    # dark

    dark_frames = []
    for dark_file in dark_files:
        job.check()
        fits_data, fits_header = checker.load(dark_file)
        dark_frames.append((fits_data - master_bias) / (fits_header[exposure_time_key] - bias_frames_exp))
        job.step('dark', message=os.path.basename(dark_file))

    if len(dark_frames) > 0:
        job.task_message('dark', 'Calculating master dark...')
        master_dark = _combine(dark_frames, log.get_param('master_dark_method'))
        job.task_done('dark', 'Calculating master dark... Completed!')
    else:
        master_dark = 0.0
    del dark_frames

    # dark-flat

    darkf_frames = []
    for darkf_file in darkf_files:
        job.check()
        fits_data, fits_header = checker.load(darkf_file)
        darkf_frames.append((fits_data - master_bias) / (fits_header[exposure_time_key] - bias_frames_exp))
        job.step('darkf', message=os.path.basename(darkf_file))

    if len(darkf_frames) > 0:
        job.task_message('darkf', 'Calculating master dark-flat...')
        master_darkf = _combine(darkf_frames, log.get_param('master_darkf_method'))
        job.task_done('darkf', 'Calculating master dark-flat... Completed!')
    else:
        master_darkf = master_dark
    del darkf_frames

    # flat

    flat_frames = []
    for flat_file in flat_files:
        job.check()
        fits_data, fits_header = checker.load(flat_file)
        flat_frames.append(fits_data - master_bias - (fits_header[exposure_time_key] - bias_frames_exp) * master_darkf)
        job.step('flat', message=os.path.basename(flat_file))

    if len(flat_frames) > 0:
        job.task_message('flat', 'Calculating master flat...')
        if log.get_param('master_flat_method') == 'mean':
            flat_frames = [ff / np.nanmean(ff) for ff in flat_frames]
            master_flat = _combine(flat_frames, 'mean')
        else:
            flat_frames = [ff / np.nanmedian(ff) for ff in flat_frames]
            master_flat = _combine(flat_frames, 'median')

        if colour_camera_mode:
            master_flat[::2, ::2] = master_flat[::2, ::2] / np.nanmedian(master_flat[::2, ::2])
            master_flat[::2, 1::2] = master_flat[::2, 1::2] / np.nanmedian(master_flat[::2, 1::2])
            master_flat[1::2, ::2] = master_flat[1::2, ::2] / np.nanmedian(master_flat[1::2, ::2])
            master_flat[1::2, 1::2] = master_flat[1::2, 1::2] / np.nanmedian(master_flat[1::2, 1::2])
        else:
            master_flat = master_flat / np.nanmedian(master_flat)

        master_flat = _clean_flat(master_flat)
        job.task_done('flat', 'Calculating master flat... Completed!')
    else:
        master_flat = 1.0
    del flat_frames

    sys.setrecursionlimit(max(sys.getrecursionlimit(), 100 * len(science_files)))

    # science: times and airmass here, the image work per frame (in parallel when it pays off)

    settings = {
        'file_info': checker.file_info,
        'saturation_key': log.hops_saturation_key,
        'crop_x1': log.get_param('crop_x1'), 'crop_x2': log.get_param('crop_x2'),
        'crop_y1': log.get_param('crop_y1'), 'crop_y2': log.get_param('crop_y2'),
        'crop_edge_pixels': int(log.get_param('crop_edge_pixels')),
        'bin_fits': log.get_param('bin_fits'),
        'centroids_snr': log.get_param('centroids_snr'),
        'stars_snr': log.get_param('stars_snr'),
        'psf_guess': log.get_param('psf_guess'),
        'keys': {'mean': log.mean_key, 'std': log.std_key, 'saturation': log.hops_saturation_key,
                 'psf': log.psf_key, 'skip': log.skip_key, 'align_x0': log.align_x0_key,
                 'align_y0': log.align_y0_key, 'align_u0': log.align_u0_key},
    }

    tasks = []
    for science_file in science_files:
        job.check()
        header = _read_header(science_file)
        exp_time = float(header[exposure_time_key])

        if log.get_param('observation_date_key') == log.get_param('observation_time_key'):
            observation_time = ' '.join(header[log.get_param('observation_date_key')].split('T'))
        else:
            observation_time = ' '.join([header[log.get_param('observation_date_key')].split('T')[0],
                                         header[log.get_param('observation_time_key')]])
        try:
            observation_time = exoclock.Moment(utc=observation_time)
            if log.get_param('time_stamp') == 'exposure start':
                pass
            elif log.get_param('time_stamp') == 'mid-exposure':
                observation_time = exoclock.Moment(jd_utc=observation_time.jd_utc() - 0.5 * exp_time / 24.0 / 3600.0)
            elif log.get_param('time_stamp') == 'exposure end':
                observation_time = exoclock.Moment(jd_utc=observation_time.jd_utc() - exp_time / 24.0 / 3600.0)
            else:
                raise HOPSError('Not acceptable time stamp.')
        except exoclock.errors.ExoClockInputError:
            print('Bad time data, skipping frame: ', science_file)
            job.step('science', message='Bad time data, skipping frame: {0}'.format(os.path.basename(science_file)))
            continue

        julian_date = observation_time.jd_utc()
        airmass = observatory.airmass(target, observation_time)

        # important to keep the name like this for windows!
        time_in_file = observation_time.utc().isoformat().split('.')[0]
        time_in_file = time_in_file.replace('-', '_').replace(':', '_').replace('T', '_')
        new_name = '{0}{1}_{2}'.format(log.reduction_prefix, time_in_file, os.path.basename(science_file))

        tasks.append({
            'file': science_file,
            'output': project.reduced(new_name),
            'name': new_name,
            'exp_time': exp_time,
            'julian_date': julian_date,
            'airmass': airmass,
            'settings': settings,
            'header': [
                (log.hops_observatory_latitude_key, observatory.latitude.deg_coord()),
                (log.hops_observatory_longitude_key, observatory.longitude.deg()),
                (log.hops_target_ra_key, target.ra.deg()),
                (log.hops_target_dec_key, target.dec.deg_coord()),
                (log.hops_datetime_key, observation_time.utc().isoformat()),
                (log.hops_exposure_key, exp_time),
                (log.hops_filter_key, phot_filter),
                (log.time_key, julian_date),
                (log.airmass_key, airmass),
            ],
        })

    if len(tasks) == 0:
        raise HOPSError('No frame could be reduced, please check the time keywords.')

    results = {}

    def finished(index, result):
        task = tasks[index]
        results[index] = result
        if index == 0 or show_all_frames():
            job.set_preview(result['preview'], task['name'], factor=result['factor'],
                            header={log.mean_key: result['mean'], log.std_key: result['std']})
        job.step('science', message=task['name'])

    masters_dir = tempfile.mkdtemp(prefix='hops_masters_')
    try:
        masters = {'bias_exp': float(bias_frames_exp)}
        for key, master in [['bias', master_bias], ['dark', master_dark], ['flat', master_flat]]:
            if isinstance(master, np.ndarray):
                path = os.path.join(masters_dir, key + '.npy')
                np.save(path, master)
                masters[key] = path
            else:
                masters[key] = master
        del master_bias, master_dark, master_darkf, master_flat

        workers = worker_count(len(tasks), frame_pixels)
        job.task_message('science', 'Using {0} process{1}'.format(workers, 'es' if workers > 1 else ''))

        if workers > 1:
            try:
                _run_parallel(job, tasks, masters, workers, finished)
            except (BrokenProcessPool, AssertionError, OSError):
                # e.g. started from a script without `if __name__ == '__main__':` - finish in this process
                print('Parallel reduction not possible, continuing in one process.')
                job.task_message('science', 'Using 1 process')
        _init_worker(masters)
        for index, task in enumerate(tasks):
            if index not in results:
                job.check()
                finished(index, _reduce_frame(task))
    finally:
        _MASTERS.clear()
        shutil.rmtree(masters_dir, ignore_errors=True)

    all_frames = {}
    for index, task in enumerate(tasks):
        result = results[index]
        all_frames[task['name']] = {
            log.mean_key: result['mean'],
            log.std_key: result['std'],
            log.psf_key: result['psf'],
            log.time_key: task['julian_date'],
            log.airmass_key: task['airmass'],
            exposure_time_key: task['exp_time'],
            log.skip_key: result['skip'],
            log.align_x0_key: False,
            log.align_y0_key: False,
            log.align_u0_key: False,
        }

    project.save_all_frames(all_frames)
    log.set_param('reduction_complete', True)
    log.set_param('reduction_version', log.version)
    project.save_log()

    return {'frames': len(all_frames), 'skipped': int(sum(1 for ff in all_frames.values() if ff[log.skip_key]))}


def _run_parallel(job, tasks, masters, workers, finished):
    """Reduce the frames in worker processes; the job can still be stopped at any time."""

    pool = ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context('spawn'),
                               initializer=_init_worker, initargs=(masters,))
    try:
        # keep only a few frames per worker queued, so a stop does not wait for the whole list
        pending = {}
        queue = list(enumerate(tasks))
        while queue or pending:
            while queue and len(pending) < 2 * workers:
                index, task = queue.pop(0)
                pending[pool.submit(_reduce_frame, task)] = index
            done, _ = wait(list(pending), timeout=0.3, return_when=FIRST_COMPLETED)
            job.check()
            for future in done:
                finished(pending.pop(future), future.result())
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        _terminate(pool)
        raise
    else:
        pool.shutdown(wait=True)


def _terminate(pool):
    """Stop the worker processes now instead of letting them finish their current frame."""
    for process in list((getattr(pool, '_processes', None) or {}).values()):
        if process.is_alive():
            process.terminate()
