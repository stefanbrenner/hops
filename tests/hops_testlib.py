"""Shared pieces of the regression tests: a synthetic transit dataset, the pipeline configuration
and the collection of the results that are compared with the golden files.

The same configuration and collection are used for the original Tk pipeline (make_golden_tk.py)
and for hops.core (test_regression.py), so both are compared on identical inputs and outputs.
"""

import os
import json
import datetime
import numpy as np

from astropy.io import fits as pf
from astropy.time import Time
from astropy.coordinates import SkyCoord, EarthLocation
import astropy.units as u

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'golden')

# KPS-1b-like system (the planet the HOPS examples are made with)
TARGET_RA = '11:00:40.18'
TARGET_DEC = '+64:57:50.3'
SITE_LAT = '+48:05:17.15'
SITE_LONG = '+15:45:24.40'
SITE_LAT_DEG = 48 + 5 / 60 + 17.15 / 3600
SITE_LONG_DEG = 15 + 45 / 60 + 24.40 / 3600

PLANET = {
    'planet': 'Synthetic-1b',
    'target_ra_dec': '{0} {1}'.format(TARGET_RA, TARGET_DEC),
    'period': 1.706291,
    'rp_over_rs': 0.1145,
    'sma_over_rs': 6.37,
    'inclination': 83.2,
    'eccentricity': 0.0,
    'periastron': 0.0,
    'metallicity': 0.22,
    'temperature': 5165.0,
    'logg': 4.47,
}

# claret coefficients used for the injected transit (R band, sun-like star)
INJECTED_LDC = [0.55, -0.15, 0.62, -0.25]

SHAPE = (900, 1200)
N_FRAMES = 40
CADENCE = 180.0
EXPTIME = 120.0
START = datetime.datetime(2026, 3, 10, 21, 0, 0)

TARGET_XY = (600.3, 449.6)
COMPARISON_OFFSETS = [(-180.0, 95.0), (210.0, -120.0), (-250.0, -160.0), (150.0, 170.0)]


def frame_times():
    """Exposure start times (UTC datetime) and their BJD_TDB at mid-exposure (for the injection)."""
    starts = [START + datetime.timedelta(seconds=i * CADENCE) for i in range(N_FRAMES)]
    mid = Time([s + datetime.timedelta(seconds=EXPTIME / 2) for s in starts], scale='utc')
    target = SkyCoord(TARGET_RA, TARGET_DEC, unit=(u.hourangle, u.deg))
    site = EarthLocation.from_geodetic(SITE_LONG_DEG * u.deg, SITE_LAT_DEG * u.deg, 300 * u.m)
    bjd = mid.tdb + mid.light_travel_time(target, location=site)
    return starts, bjd.jd


def injected_mid_time():
    _, bjd = frame_times()
    # transit centred 55% into the observation
    return float(bjd[0] + 0.55 * (bjd[-1] - bjd[0]))


def _stamp(image, x, y, flux, sigma):
    r = int(5 * sigma) + 2
    x0, y0 = int(x) - r, int(y) - r
    x1, y1 = int(x) + r + 1, int(y) + r + 1
    if x1 <= 0 or y1 <= 0 or x0 >= image.shape[1] or y0 >= image.shape[0]:
        return
    xx, yy = np.meshgrid(np.arange(max(x0, 0), min(x1, image.shape[1])) + 0.5,
                         np.arange(max(y0, 0), min(y1, image.shape[0])) + 0.5)
    image[max(y0, 0):min(y1, image.shape[0]), max(x0, 0):min(x1, image.shape[1])] += \
        flux / (2 * np.pi * sigma ** 2) * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma ** 2))


def _write(path, data, header):
    hdu = pf.PrimaryHDU(np.clip(np.round(data), 0, 65535).astype(np.uint16))
    for key, value in header.items():
        hdu.header[key] = value
    hdu.writeto(path, overwrite=True)


def generate(directory, seed=7):
    """Writes light, bias, dark and flat frames into `directory`. Deterministic for a given seed."""

    import hops.pylightcurve41 as plc

    os.makedirs(directory, exist_ok=True)
    rng = np.random.default_rng(seed)
    ny, nx = SHAPE

    yy, xx = np.mgrid[0:ny, 0:nx]
    rr = ((xx - nx / 2) ** 2 + (yy - ny / 2) ** 2) / ((nx / 2) ** 2 + (ny / 2) ** 2)
    flat_field = (1 - 0.12 * rr) * (1 + 0.01 * rng.standard_normal(SHAPE))
    bias_level = 300 + 3 * np.sin(xx / 37.0)
    dark_rate = 0.4 + 0.05 * rng.random(SHAPE)
    read_noise = 8.0

    # star field: random stars + the target + four comparisons of similar flux
    n_stars = 140
    stars_x = rng.uniform(30, nx - 30, n_stars)
    stars_y = rng.uniform(30, ny - 30, n_stars)
    stars_f = 10 ** rng.uniform(3.3, 5.6, n_stars)
    keep = np.hypot(stars_x - TARGET_XY[0], stars_y - TARGET_XY[1]) > 40
    for dx, dy in COMPARISON_OFFSETS:
        keep &= np.hypot(stars_x - TARGET_XY[0] - dx, stars_y - TARGET_XY[1] - dy) > 40
    stars_x, stars_y, stars_f = stars_x[keep], stars_y[keep], stars_f[keep]

    target_flux = 1.6e5
    comparison_flux = [1.4e5, 1.75e5, 1.5e5, 1.9e5]

    starts, bjd = frame_times()
    mid_time = injected_mid_time()
    relative_flux = plc.transit(INJECTED_LDC, PLANET['rp_over_rs'], PLANET['period'], PLANET['sma_over_rs'],
                                PLANET['eccentricity'], PLANET['inclination'], PLANET['periastron'],
                                mid_time, bjd, method='claret')

    common = {
        'OBJCTRA': TARGET_RA.replace(':', ' '),
        'OBJCTDEC': TARGET_DEC.replace(':', ' '),
        'SITELAT': SITE_LAT,
        'SITELONG': SITE_LONG,
        'FILTER': 'R',
        'TELESCOP': 'Synthetic 30cm',
        'INSTRUME': 'Synthetic CMOS',
        'OBSERVER': 'HOPS tests',
    }

    def noisy(electrons):
        return rng.poisson(np.clip(electrons, 0, None)).astype(float) + read_noise * rng.standard_normal(SHAPE)

    for i, start in enumerate(starts):
        dx = 0.12 * i + rng.normal(0, 0.4)
        dy = -0.07 * i + rng.normal(0, 0.4)
        sigma = 1.8 + 0.25 * np.sin(i / 6.0) + rng.normal(0, 0.03)
        sky = 120 + 15 * i / N_FRAMES

        image = np.full(SHAPE, sky * EXPTIME / 60.0)
        for x, y, f in zip(stars_x, stars_y, stars_f):
            _stamp(image, x + dx, y + dy, f, sigma)
        _stamp(image, TARGET_XY[0] + dx, TARGET_XY[1] + dy, target_flux * relative_flux[i], sigma)
        for (ox, oy), f in zip(COMPARISON_OFFSETS, comparison_flux):
            _stamp(image, TARGET_XY[0] + ox + dx, TARGET_XY[1] + oy + dy, f, sigma)

        raw = bias_level + noisy(image * flat_field + dark_rate * EXPTIME)
        header = dict(common, EXPTIME=EXPTIME, **{'DATE-OBS': start.isoformat(timespec='milliseconds')})
        _write(os.path.join(directory, 'synthetic_light_{0:04d}.fits'.format(i + 1)), raw, header)

    for i in range(5):
        _write(os.path.join(directory, 'synthetic_bias_{0:04d}.fits'.format(i + 1)),
               bias_level + read_noise * rng.standard_normal(SHAPE), dict(common, EXPTIME=0.0))
        _write(os.path.join(directory, 'synthetic_dark_{0:04d}.fits'.format(i + 1)),
               bias_level + noisy(dark_rate * EXPTIME), dict(common, EXPTIME=EXPTIME))
        _write(os.path.join(directory, 'synthetic_flat_{0:04d}.fits'.format(i + 1)),
               bias_level + noisy(20000 * flat_field + dark_rate * 5.0), dict(common, EXPTIME=5.0))

    return {'mid_time': mid_time, 'rp_over_rs': PLANET['rp_over_rs']}


def configure(log, directory):
    """The settings the Data & target step would save for the synthetic dataset."""

    os.chdir(directory)
    log.set_param('directory', directory)
    log.set_param('directory_short', os.path.split(directory)[1])
    log.load_main_log()
    log.set_param('observation_files', 'light')
    log.set_param('bias_files', 'bias')
    log.set_param('dark_files', 'dark')
    log.set_param('darkf_files', '---')
    log.set_param('flat_files', 'flat')
    log.set_param('exposure_time_key', 'EXPTIME')
    log.set_param('observation_date_key', 'DATE-OBS')
    log.set_param('observation_time_key', 'DATE-OBS')
    log.set_param('time_stamp', 'exposure start')
    log.set_param('target_ra_dec', '{0} {1}'.format(TARGET_RA, TARGET_DEC))
    log.set_param('target_name', 'Synthetic-1')
    log.set_param('location', '{0} {1}'.format(SITE_LAT, SITE_LONG))
    log.set_param('filter', 'R')
    log.set_param('observer', 'HOPS tests')
    log.set_param('observatory', 'Synthetic')
    log.set_param('telescope', 'Synthetic 30cm')
    log.set_param('camera', 'Synthetic CMOS')
    for key in ['bin_fits']:
        log.set_param(key, 1)
    for key in ['crop_x1', 'crop_x2', 'crop_y1', 'crop_y2', 'crop_edge_pixels']:
        log.set_param(key, 0)
    for key in ['faint_target_mode', 'moving_target_mode', 'rotating_field_mode', 'colour_camera_mode']:
        log.set_param(key, False)
    log.set_param('data_target_complete', True)
    log.set_param('data_target_version', log.version)
    log.save_local_log()


def star_positions(log, directory=None):
    """Target and comparison positions in the first aligned frame (for the photometry step)."""
    import hops.pylightcurve41 as plc
    directory = directory or os.getcwd()
    all_frames = plc.open_dict(os.path.join(directory, log.all_frames))
    first = sorted([[all_frames[f][log.time_key], f] for f in all_frames if not all_frames[f][log.skip_key]])[0][1]
    header = pf.open(os.path.join(directory, log.reduction_directory, first))[1].header
    # the first light frame was rendered with this drift (seed dependent but tiny); the star finder
    # snaps to the true centroid anyway
    x, y = TARGET_XY
    return [(x, y)] + [(x + dx, y + dy) for dx, dy in COMPARISON_OFFSETS], header


FITTING_SETTINGS = {
    'light_curve_file': os.path.join('PHOTOMETRY_1', 'PHOTOMETRY_APERTURE.txt'),
    'iterations': 1500,
    'burn': 500,
    'a_i_fit': False,
    'detrending': 'Airmass',
    'manual_planet': True,
}


def manual_planet():
    values = dict(PLANET)
    values['mid_time'] = injected_mid_time()
    return values


def collect(log, fitting_test=None, fitting_mcmc=None, directory=None):
    """Everything the regression compares, read back from the files the pipeline wrote."""

    import hops.pylightcurve41 as plc

    directory = directory or os.getcwd()
    path = lambda *parts: os.path.join(directory, *parts)
    all_frames = plc.open_dict(path(log.all_frames))
    frames = []
    for name in sorted(all_frames, key=lambda f: all_frames[f][log.time_key]):
        f = all_frames[name]
        with pf.open(path(log.reduction_directory, name)) as hdul:
            data = hdul[1].data.astype(np.int64)
            header = hdul[1].header
            frames.append({
                'name': name,
                'mean': float(f[log.mean_key]),
                'std': float(f[log.std_key]),
                'psf': float(f[log.psf_key]),
                'jd': float(f[log.time_key]),
                'airmass': float(f[log.airmass_key]),
                'skip': bool(f[log.skip_key]),
                'align_x0': float(header[log.align_x0_key] or 0),
                'align_y0': float(header[log.align_y0_key] or 0),
                'align_u0': float(header[log.align_u0_key] or 0),
                'data_sum': int(data.sum()),
            })

    stars = plc.open_dict(path(log.all_stars))

    def light_curve(name):
        jd, flux, unc = np.loadtxt(path('PHOTOMETRY_1', name), unpack=True)
        return {'jd': jd.tolist(), 'flux': flux.tolist(), 'flux_unc': unc.tolist()}

    result = {
        'frames': frames,
        'fov': [float(log.get_param(k)) for k in ['min_x', 'min_y', 'max_x', 'max_y']],
        'stars': int(len(stars['all_stars'])),
        'stars_in_fov': int(np.sum(stars['in_fov'])),
        'positions': {k: float(log.get_param(k)) for k in [
            'target_x_position', 'target_y_position', 'target_r_position', 'target_u_position', 'target_aperture']},
        'aperture': light_curve('PHOTOMETRY_APERTURE.txt'),
        'gauss': light_curve('PHOTOMETRY_GAUSS.txt'),
        'photometry_files': sorted(os.listdir(path('PHOTOMETRY_1'))),
    }
    for key, fitting in [['fitting_test', fitting_test], ['fitting_mcmc', fitting_mcmc]]:
        if fitting is not None:
            p = fitting['parameters']
            result[key] = {
                'mid_time': float(p['mid_time']['value']),
                'mid_time_error': float(max(p['mid_time']['m_error'], p['mid_time']['p_error'])),
                'rp_over_rs': float(p['rp_over_rs']['value']),
                'rp_over_rs_error': float(max(p['rp_over_rs']['m_error'], p['rp_over_rs']['p_error'])),
                'res_std': float(fitting['statistics']['res_std']),
            }
    return result


def save_golden(name, result):
    os.makedirs(GOLDEN_DIR, exist_ok=True)
    with open(os.path.join(GOLDEN_DIR, name), 'w') as f:
        json.dump(result, f, indent=1)


def load_golden(name):
    with open(os.path.join(GOLDEN_DIR, name)) as f:
        return json.load(f)
