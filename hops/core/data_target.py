
import os
import tempfile
import numpy as np
import exoclock
import hops.pylightcurve41 as plc

from astropy.io import fits as pf

from hops.hops_tools.fits import get_fits_data_and_header
from hops.hops_tools.image_analysis import bin_frame

from .common import ERRORS, filter_map, filter_translations, to_builtin

OK = 'OK'


class ScienceCache:
    """Keeps the first science frame of the current pattern in memory (loading large FITS is slow)."""

    def __init__(self):
        self.key = None
        self.name = None
        self.path = None
        self.data = None
        self.header = {}

    def get(self, project, observation_files):
        files = project.find_fits(observation_files)
        if len(files) == 0:
            self.key, self.name, self.data, self.header = None, None, None, {}
            return files
        key = (files[0], os.path.getmtime(files[0]))
        if key != self.key:
            self.data, self.header = get_fits_data_and_header(files[0])
            self.key = key
            self.name = os.path.basename(files[0])
            self.path = files[0]
        return files


def file_list(project):
    return [os.path.basename(ff) for ff in project.find_fits('*')]


def header_list(header):
    return [[str(key), str(header[key])] for key in header if key != '']


def _first_key(header, keys):
    for key in keys.split(','):
        if key in header:
            return header[key]
    return None


def auto_target_ra_dec(header, log):
    ra = _first_key(header, log.get_param('target_ra_key'))
    dec = _first_key(header, log.get_param('target_dec_key'))
    if ra and dec:
        try:
            if isinstance(ra, str):
                target = exoclock.FixedTarget(exoclock.Hours(ra.replace(',', '.').replace(' ', ':')),
                                              exoclock.Degrees(dec.replace(',', '.').replace(' ', ':')))
                return target.coord()
            elif isinstance(ra, float):
                target = exoclock.FixedTarget(exoclock.Degrees(ra), exoclock.Degrees(dec))
                return target.coord()
        except ERRORS:
            pass
    return None


# header keywords with the name of the observed object, used as the default SIMBAD search name
TARGET_NAME_KEYS = 'OBJECT'


def auto_target_name(header):
    name = _first_key(header, TARGET_NAME_KEYS)
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


def _describe(nearest):
    notes = ''
    if len(nearest.all_names) > 0:
        hosts = exoclock.exoclock_data.ecc()['hosts']
        test = list(set(nearest.all_names).intersection(list(hosts)))
        if len(test) > 0:
            notes = '/ Host of: ' + ', '.join(hosts[test[0]])
    if notes:
        return nearest.name + ' ' + notes
    return nearest.name


def resolve_target(choice, auto_ra_dec, simbad_name, manual_ra_dec):
    """Returns (target_ra_dec, target_name) following the three options of the target window."""

    if choice == 0:
        try:
            target = exoclock.FixedTarget(exoclock.Hours(auto_ra_dec.split(' ')[0]),
                                          exoclock.Degrees(auto_ra_dec.split(' ')[1]))
            try:
                nearest = exoclock.simbad_search_by_coordinates(target.ra, target.dec, radius=exoclock.Degrees(0.25))
                return nearest.coord(), _describe(nearest)
            except ERRORS:
                return auto_ra_dec, 'Name not resolved'
        except ERRORS:
            return 'Coordinates not found', 'Name not resolved'

    elif choice == 1:
        try:
            nearest = exoclock.simbad_search_by_name(simbad_name)
            return nearest.coord(), _describe(nearest)
        except ERRORS:
            return 'Coordinates not found', 'Name not resolved'

    else:
        try:
            if len(manual_ra_dec.split(':')) == 5:
                target = exoclock.FixedTarget(exoclock.Hours(manual_ra_dec.split(' ')[0]),
                                              exoclock.Degrees(manual_ra_dec.split(' ')[1]))
                nearest = exoclock.simbad_search_by_coordinates(target.ra, target.dec, radius=exoclock.Degrees(0.25))
                return nearest.coord(), _describe(nearest)
            return manual_ra_dec, ' '
        except ERRORS:
            return manual_ra_dec, ' '


def target_ok(target_ra_dec):
    try:
        exoclock.FixedTarget(exoclock.Hours(target_ra_dec.split(' ')[0]),
                             exoclock.Degrees(target_ra_dec.split(' ')[1]))
        return True
    except ERRORS:
        return False


def auto_location(header, log):
    lat = _first_key(header, log.get_param('observatory_latitude_key'))
    long = _first_key(header, log.get_param('observatory_longitude_key'))
    if lat and long:
        try:
            if isinstance(lat, str):
                return exoclock.Observatory(exoclock.Degrees(lat.replace(',', '.')),
                                            exoclock.Degrees(long.replace(',', '.'))).coord()
            elif isinstance(lat, float):
                return exoclock.Observatory(exoclock.Degrees(lat), exoclock.Degrees(long)).coord()
        except ERRORS:
            pass
    return None


def profile_location(log):
    try:
        return exoclock.Observatory(exoclock.Degrees(log.get_param('observatory_lat')),
                                    exoclock.Degrees(log.get_param('observatory_long'))).coord()
    except ERRORS:
        return None


def location_ok(location):
    try:
        exoclock.Observatory(exoclock.Degrees(location.split(' ')[0]), exoclock.Degrees(location.split(' ')[1]))
        return True
    except ERRORS:
        return False


def normalise_filter(value, header, log):
    """Same search order as the Tk window: chosen value, FITS header, log default."""

    def translate(candidate):
        if candidate in filter_map:
            return candidate
        for name in filter_translations:
            if candidate in filter_translations[name]:
                return name
        return candidate

    chosen = translate(value)
    if chosen not in filter_map:
        header_value = _first_key(header, log.get_param('filter_key'))
        if header_value is not None:
            chosen = translate(header_value)
    if chosen not in filter_map:
        chosen = translate(log.get_param('filter'))
    if chosen not in filter_map:
        return 'No filter chosen', False
    return chosen, True


def observing_info(values, header, log):
    """Fill observer / observatory / telescope / camera when they are 'default'."""
    result = {}
    for field in ['observer', 'observatory', 'telescope', 'camera']:
        value = values.get(field, log.get_param(field))
        if value == 'default':
            header_value = _first_key(header, log.get_param('{0}_key'.format(field)))
            value = header_value if header_value is not None else log.get_param(field)
        result[field] = value
    return result


def check(project, cache, draft):
    """Validate a draft of the Data & Target form. Mirrors all the `update_*` methods of the Tk window."""

    log = project.log
    files = cache.get(project, draft['observation_files'])
    header = cache.header

    counts = {}
    for key in ['bias_files', 'dark_files', 'darkf_files', 'flat_files']:
        counts[key] = len(project.find_fits(draft[key]))

    status = {'observation_files': len(files) > 0}

    exposure_key = draft['exposure_time_key']
    date_key = draft['observation_date_key']
    time_key = draft['observation_time_key']

    status['exposure_time_key'] = exposure_key in header
    status['observation_date_key'] = date_key in header
    time_key_locked = False
    if status['observation_date_key'] and len(str(header[date_key]).split('T')) == 2:
        time_key = date_key
        time_key_locked = True
    status['observation_time_key'] = time_key in header

    filter_value, status['filter'] = normalise_filter(draft['filter'], header, log)

    status['target'] = target_ok(draft['target_ra_dec'])
    status['location'] = location_ok(draft['location'])

    can_save = all(status.values())

    return to_builtin({
        'science_files': len(files),
        'first_file': cache.name,
        'counts': counts,
        'status': status,
        'observation_time_key': time_key,
        'time_key_locked': time_key_locked,
        'filter': filter_value,
        'auto_target_ra_dec': auto_target_ra_dec(header, log),
        'auto_target_name': auto_target_name(header),
        'auto_location': auto_location(header, log),
        'profile_location': profile_location(log),
        'observing_info': observing_info(draft, header, log),
        'image_shape': list(cache.data.shape) if cache.data is not None else None,
        'can_save': can_save,
    })


def preview_frame(data, crop_x1, crop_x2, crop_y1, crop_y2, crop_edge_pixels, bin_fits):
    frame = np.ones_like(data) * data
    if crop_x2 == 0:
        crop_x2 = len(data[0])
    if crop_y2 == 0:
        crop_y2 = len(data)
    frame = frame[crop_y1: crop_y2]
    frame = frame[:, crop_x1: crop_x2]
    if crop_edge_pixels > 0:
        frame = frame[crop_edge_pixels:-crop_edge_pixels, crop_edge_pixels:-crop_edge_pixels]
    if bin_fits > 1:
        frame = bin_frame(frame, bin_fits)
    return frame


def reduced_size_mb(frame):
    """Size of the frame once written like the reduction does (compressed int32)."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, 'test_size.fits')
        hdu = pf.CompImageHDU(data=np.array(frame, dtype=np.int32))
        plc.save_fits(pf.HDUList([pf.PrimaryHDU(), hdu]), path)
        return round(os.stat(path).st_size / 1024.0 / 1024.0, 2)


SAVED_FIELDS = [
    'observation_files', 'bias_files', 'dark_files', 'darkf_files', 'flat_files', 'bin_fits',
    'crop_x1', 'crop_x2', 'crop_y1', 'crop_y2', 'faint_target_mode', 'moving_target_mode',
    'rotating_field_mode', 'colour_camera_mode', 'crop_edge_pixels', 'target_ra_dec_choice',
    'auto_target_ra_dec', 'manual_target_ra_dec', 'simbad_target_name', 'target_ra_dec', 'target_name',
    'location_choice', 'auto_location', 'manual_location', 'location', 'time_stamp', 'exposure_time_key',
    'observation_date_key', 'observation_time_key', 'observer', 'observatory', 'telescope', 'camera', 'filter',
]

INT_FIELDS = ['bin_fits', 'crop_x1', 'crop_x2', 'crop_y1', 'crop_y2', 'crop_edge_pixels',
              'target_ra_dec_choice', 'location_choice']
BOOL_FIELDS = ['faint_target_mode', 'moving_target_mode', 'rotating_field_mode', 'colour_camera_mode']


def form_values(log):
    values = {key: log.get_param(key) for key in SAVED_FIELDS}
    for key in ['auto_location', 'profile_location']:
        values[key] = log.get_param(key) if key in log.params else ''
    return to_builtin(values)


def save(project, values):
    log = project.log
    for key in SAVED_FIELDS:
        value = values[key]
        if key in INT_FIELDS:
            value = int(value)
        elif key in BOOL_FIELDS:
            value = bool(value)
        elif value is None:
            value = ''
        log.set_param(key, value)

    if log.get_param('faint_target_mode'):
        log.set_param('centroids_snr', 2)
        log.set_param('psf_guess', 1)
        log.set_param('stars_snr', 2)

    log.set_param('data_target_complete', True)
    log.set_param('data_target_version', log.version)

    log.save_local_log_user()
    project.save_log()
