
import os
import shutil
import warnings
import numpy as np
import exoclock
import matplotlib.patches as mpatches
import matplotlib.gridspec as gridspec

from matplotlib.figure import Figure
from matplotlib.cm import Greys_r
from astropy.coordinates import SkyCoord
from astropy.time import Time as astrotime
from photutils.aperture import CircularAperture, aperture_photometry

from hops.hops_tools.image_analysis import image_find_stars, image_plate_solve, cartesian_to_polar

from .common import ERRORS, HOPSError, to_builtin
from .project import photometry_order

SETTINGS = ['sky_inner_aperture', 'sky_outer_aperture', 'saturation', 'star_size_arcsec', 'camera_gain',
            'use_variable_aperture', 'use_geometric_center', 'show_good_comparisons', 'show_good_comparisons_percent']


class FirstFrame:
    """The first (non-excluded) frame, used to select the stars."""

    def __init__(self, project):
        self.name = project.science_frames()[0]
        self.data, self.header = project.open_reduced(self.name)
        stars = project.load_all_stars()
        self.in_fov = np.array(stars['in_fov'])
        self.all_stars = np.array(stars['all_stars'])
        self.psf = float(self.header[project.log.psf_key])


# selection

def get_star(log, frame, x, y, saturation):
    """Port of PhotometryWindow.get_star."""

    header = frame.header
    psf = header[log.psf_key]
    star = image_find_stars(
        frame.data, header,
        x_low=x - 3 * psf, x_upper=x + 3 * psf, y_low=y - 3 * psf, y_upper=y + 3 * psf,
        x_centre=x, y_centre=y,
        mean=header[log.mean_key], std=header[log.std_key],
        burn_limit=header[log.hops_saturation_key] * saturation,
        psf=psf,
        centroids_snr=log.get_param('centroids_snr'), stars_snr=log.get_param('stars_snr'),
        order_by_flux=False, verbose=False)

    if not star:
        return None

    star = star[0]
    app = round(3 * psf, 1)

    x1 = int(star[0] - app)
    x2 = x1 + int(2 * app) + 2
    y1 = int(star[1] - app)
    y2 = y1 + int(2 * app) + 2
    peak = round(float(np.max(frame.data[y1:y2, x1:x2])), 1)

    x1 = int(star[0] - 3 * psf)
    x2 = x1 + int(6 * psf) + 1
    y1 = int(star[1] - 3 * psf)
    y2 = y1 + int(6 * psf) + 1
    area = frame.data[y1:y2, x1:x2]
    area_x, area_y = np.meshgrid(np.arange(x1, x2) + 0.5, np.arange(y1, y2) + 0.5)

    return {
        'x': round(float(star[0]), 1),
        'y': round(float(star[1]), 1),
        'default_aperture': app,
        'peak': peak,
        'flux': round(float(star[7]), 1),
        'max_hwhm': round(float(0.5 * 2.355 * max(star[4], star[5])), 1),
        'wx': round(float(np.sum(area_x * area) / np.sum(area)), 1),
        'wy': round(float(np.sum(area_y * area) / np.sum(area)), 1),
    }


def empty_row():
    return {'x': 0, 'y': 0, 'aperture': 0, 'flux': 0, 'peak': 0, 'max_hwhm': 0, 'wx': 0, 'wy': 0,
            'colour': '-', 'warning': ''}


def rows_from_log(log, source=None):
    """Target + comparison rows from the log (or from the log of a previous run)."""
    source = source or log.params
    rows = []
    for i in range(log.get_param('max_comparisons') + 1):
        prefix = 'target' if i == 0 else 'comparison_{0}'.format(i)
        row = empty_row()
        try:
            row['aperture'] = float(source['{0}_aperture'.format(prefix)])
            row['x'] = float(source['{0}_x_position'.format(prefix)])
            row['y'] = float(source['{0}_y_position'.format(prefix)])
        except (KeyError, TypeError, ValueError):
            row = empty_row()
        rows.append(row)
    return rows


def update_rows(log, frame, rows, settings, plate_solution=None):
    """Port of PhotometryWindow.update_window: measures new stars, shifts empty slots,
    finds stars of similar flux, sets warnings and validates the settings."""

    psf = frame.psf
    saturation = float(settings['saturation'])
    lost = []

    for i, row in enumerate(rows):
        if 0 in [row['x'], row['y']]:
            rows[i] = empty_row()
            continue
        if row['flux'] == 0:
            star = get_star(log, frame, row['x'], row['y'], saturation)
            if not star:
                lost.append(i)
                rows[i] = empty_row()
                continue
            if not row['aperture']:
                row['aperture'] = star['default_aperture']
            row.update({k: star[k] for k in ['x', 'y', 'flux', 'peak', 'max_hwhm', 'wx', 'wy']})
            row['colour'] = '-'

    # clear_target: move the following comparisons up
    comparisons = [r for r in rows[1:] if 0 not in [r['x'], r['y']]]
    rows = [rows[0]] + comparisons + [empty_row() for _ in range(len(rows) - 1 - len(comparisons))]

    if plate_solution:
        identified = plate_solution['identified_stars']
        for row in rows:
            if 0 in [row['x'], row['y']]:
                continue
            match = min(identified, key=lambda s: (row['x'] - s['x']) ** 2 + (row['y'] - s['y']) ** 2)
            if np.sqrt((row['x'] - match['x']) ** 2 + (row['y'] - match['y']) ** 2) < 3 * psf:
                row['colour'] = match['bp_rp']

    target = rows[0]
    good_comps = []
    if 0 not in [target['x'], target['y']]:
        percent = float(settings['show_good_comparisons_percent']) / 100
        for j, comp_star in enumerate(frame.all_stars):
            if np.sqrt((comp_star[0] - target['x']) ** 2 + (comp_star[1] - target['y']) ** 2) > 3 * psf:
                if frame.in_fov[j]:
                    if (1 - percent) * target['flux'] < comp_star[7] < (1 + percent) * target['flux']:
                        good_comps.append(comp_star)
        good_comps = sorted(good_comps, key=lambda s: np.sqrt((s[0] - target['x']) ** 2 + (s[1] - target['y']) ** 2))
        good_comps = [[float(s[0]), float(s[1])] for s in good_comps[:100]]

        for row in rows[1:]:
            if 0 not in [row['x'], row['y']]:
                if row['flux'] > 2 * target['flux']:
                    row['warning'] = 'Comp. too bright'
                elif row['flux'] < 0.5 * target['flux']:
                    row['warning'] = 'Comp. too faint'
                else:
                    row['warning'] = ''

    message = ''
    for row in rows:
        if 0 not in [row['x'], row['y']] and float(row['aperture']) < 1.5:
            message = 'All aperture radii must be larger than 1.5 pixels'
    if 0 in [rows[0]['x'], rows[0]['y'], rows[1]['x'], rows[1]['y']]:
        message = 'You need to select the target and at least one comparison star to proceed.'
    inner, outer = float(settings['sky_inner_aperture']), float(settings['sky_outer_aperture'])
    if inner <= 1.0 or outer <= 1.0:
        message = 'Inner and outer sky-ring radii must be larger than 1.'
    if inner >= outer:
        message = 'Inner sky-ring radius must be smaller than the outer sky-ring radius.'
    if saturation <= 0:
        message = 'Saturation limit must be larger than 0.'
    if saturation > 1:
        message = 'Saturation limit must be lower or equal to 1.'

    return to_builtin({
        'rows': rows,
        'good_comparisons': good_comps,
        'message': message,
        'can_run': message == '',
        'lost': lost,
    })


def setup(project, frame):
    log = project.log
    folders = project.photometry_folders()
    return to_builtin({
        'frame': frame.name,
        'psf': frame.psf,
        'fov': [log.get_param('min_x'), log.get_param('min_y'), log.get_param('max_x'), log.get_param('max_y')],
        'settings': {key: log.get_param(key) for key in SETTINGS},
        'rows': rows_from_log(log),
        'fov_options': log.get_param('photometry_fov_options'),
        'previous_runs': folders,
        'photometry_complete': bool(log.get_param('photometry_complete')),
        'target_ra_dec': log.get_param('target_ra_dec'),
        'target_name': log.get_param('target_name'),
        'max_comparisons': log.get_param('max_comparisons'),
    })


def load_previous(project, folder):
    """Port of load_options_from_previous_run."""
    log = project.log
    if folder not in project.photometry_folders():
        raise HOPSError('Unknown photometry folder.')
    other_log = log.open_yaml(project.path(folder, 'log.yaml'))
    settings = {}
    for key in SETTINGS:
        settings[key] = other_log.get(key, log.get_param(key))
    return to_builtin({'settings': settings, 'rows': rows_from_log(log, other_log)})


def save_settings(project, frame, settings, rows, fov_options):
    """Port of PhotometryWindow.save_settings."""

    log = project.log
    for key in SETTINGS:
        value = settings[key]
        if key in ['use_variable_aperture', 'use_geometric_center', 'show_good_comparisons']:
            value = bool(value)
        else:
            value = float(value)
        log.set_param(key, value)
    if fov_options:
        log.set_param('photometry_fov_options', fov_options)

    x0 = frame.header[log.align_x0_key]
    y0 = frame.header[log.align_y0_key]

    for i, row in enumerate(rows):
        prefix = 'target' if i == 0 else 'comparison_{0}'.format(i)
        log.set_param('{0}_x_position'.format(prefix), float(row['x']))
        log.set_param('{0}_y_position'.format(prefix), float(row['y']))
        log.set_param('{0}_aperture'.format(prefix), float(row['aperture']))
        if 0 not in [row['x'], row['y']]:
            polar = cartesian_to_polar(row['x'], row['y'], x0, y0)
        else:
            polar = [0, 0]
        log.set_param('{0}_r_position'.format(prefix), float(polar[0]))
        log.set_param('{0}_u_position'.format(prefix), float(polar[1]))

    project.save_log()


def plate_solve(job, project, frame, star_size_arcsec):
    """Port of plate_solve_first: tries three pixel scales, keeps the first solution that identifies
    at least half of the stars."""

    log = project.log
    ra_target, dec_target = log.get_param('target_ra_dec').split(' ')
    ra = exoclock.Hours(ra_target).deg()
    dec = exoclock.Degrees(dec_target).deg_coord()
    header = frame.header

    job.task('plate', 'Plate solving', 3)
    solution = None
    for factor in [0.5, 0.25, 1.0]:
        job.check()
        job.task_message('plate', 'Trying pixel scale {0:.3f} arcsec/pixel...'.format(
            factor * star_size_arcsec / header[log.psf_key]))
        try:
            solution = image_plate_solve(frame.data, header, ra, dec,
                                         timestamp=astrotime(header[log.time_key], format='jd'),
                                         mean=header[log.mean_key], std=header[log.std_key],
                                         burn_limit=header[log.hops_saturation_key], psf=header[log.psf_key],
                                         stars=frame.all_stars,
                                         pixel=factor * star_size_arcsec / header[log.psf_key],
                                         verbose=True)
            if len(solution['identified_stars']) < 0.5 * len(frame.all_stars):
                print('Only {0} / {1} stars identified, solution rejected'.format(
                    len(solution['identified_stars']), len(frame.all_stars)))
                solution = None
        except ERRORS as e:
            print('Solution failed.', e)
            solution = None
        job.step('plate')
        if solution is not None:
            break

    if solution is None:
        raise HOPSError('Plate solution failed. Please check your internet connection and the '
                        '"Star FWHM in arcseconds" value, under the "Advanced settings".')

    target_x, target_y = SkyCoord([[ra, dec]], unit='deg').to_pixel(solution['plate_solution'])
    identified = []
    for gaia_id, star in solution['identified_stars'].items():
        try:
            bp_rp = round(float(star['phot_bp_mean_mag'] - star['phot_rp_mean_mag']), 2)
        except (TypeError, ValueError):
            bp_rp = '-'
        identified.append({'x': float(star['x']), 'y': float(star['y']), 'bp_rp': bp_rp})
    job.task_done('plate', 'Identified {0} stars'.format(len(identified)))

    return to_builtin({'target_x': float(target_x[0]), 'target_y': float(target_y[0]), 'identified_stars': identified})


# measurement

class PhotometryRun:
    """Port of PhotometryProgressWindow. Keeps the results in memory so the comparisons can be
    (de)activated and saved again after the run."""

    def __init__(self, project):

        log = project.log
        self.project = project
        self.log = log
        self.saturation = log.get_param('saturation')
        self.max_targets = log.get_param('max_comparisons') + 1
        self.use_geometric_center = log.get_param('use_geometric_center')
        self.use_variable_aperture = log.get_param('use_variable_aperture')
        self.sky_inner_aperture = log.get_param('sky_inner_aperture')
        self.sky_outer_aperture = log.get_param('sky_outer_aperture')
        self.camera_gain = log.get_param('camera_gain')
        self.faint_target_mode = log.get_param('faint_target_mode')
        self.moving_target_mode = log.get_param('moving_target_mode')
        self.centroids_snr = log.get_param('centroids_snr')
        self.stars_snr = log.get_param('stars_snr')

        all_frames = project.load_all_frames()
        self.science_files = sorted([ff for ff in all_frames if not all_frames[ff][log.skip_key]])
        self.exposure_time = project.median_exposure_time(all_frames)

        self.first_fits_data, self.first_fits_header = project.open_reduced(self.science_files[0])

        self.jd = [all_frames[ff][log.time_key] for ff in self.science_files]
        psf = [all_frames[ff][log.psf_key] for ff in self.science_files]
        if self.use_variable_aperture:
            self.psf_ratio = np.array(psf) / psf[0]
        else:
            self.psf_ratio = np.ones_like(psf)

        self.targets_aperture = [log.get_param('target_aperture')]
        self.targets_x_position = [log.get_param('target_x_position')]
        self.targets_y_position = [log.get_param('target_y_position')]
        self.targets_r_position = [log.get_param('target_r_position')]
        self.targets_u_position = [log.get_param('target_u_position')]

        for comparison in range(1, self.max_targets):
            if 0 not in [log.get_param('comparison_{0}_x_position'.format(comparison)),
                         log.get_param('comparison_{0}_y_position'.format(comparison))]:
                self.targets_aperture.append(log.get_param('comparison_{0}_aperture'.format(comparison)))
                self.targets_x_position.append(log.get_param('comparison_{0}_x_position'.format(comparison)))
                self.targets_y_position.append(log.get_param('comparison_{0}_y_position'.format(comparison)))
                self.targets_r_position.append(log.get_param('comparison_{0}_r_position'.format(comparison)))
                self.targets_u_position.append(log.get_param('comparison_{0}_u_position'.format(comparison)))

        self.targets = len(self.targets_aperture)
        self.star_image_lim = int(1.5 * np.max(self.targets_aperture) + 5)

        n = len(self.science_files)
        nan = lambda: np.full((self.targets, n), np.nan)
        self.results = {
            'jd': np.array(self.jd),
            'file_names': np.array(self.science_files),
            'gauss_x_position': nan(), 'gauss_y_position': nan(), 'gauss_x_std': nan(), 'gauss_y_std': nan(),
            'gauss_flux': nan(), 'gauss_flux_error': nan(), 'gauss_sky': nan(), 'gauss_sky_error': nan(),
            'aperture_x_position': nan(), 'aperture_y_position': nan(), 'aperture_flux': nan(),
            'aperture_flux_error': nan(), 'aperture_background': nan(), 'aperture_background_error': nan(),
        }
        self.target_fails = np.zeros(self.targets)
        self.active = [True] * self.targets
        self.cutouts = [None] * self.targets
        self.finished = False

    def run(self, job):

        log = self.log
        job.task('photometry', 'Photometry', len(self.science_files))

        for counter, science_file in enumerate(self.science_files):

            job.check()

            fits_data, fits_header = self.project.open_reduced(science_file)

            ref_x_position = fits_header[log.align_x0_key]
            ref_y_position = fits_header[log.align_y0_key]
            ref_u_position = fits_header[log.align_u0_key]

            for target in range(self.targets):

                x = ref_x_position + self.targets_r_position[target] * np.cos(ref_u_position + self.targets_u_position[target])
                y = ref_y_position + self.targets_r_position[target] * np.sin(ref_u_position + self.targets_u_position[target])

                star = image_find_stars(
                    fits_data, fits_header,
                    x_low=x - 3 * fits_header[log.psf_key], x_upper=x + 3 * fits_header[log.psf_key],
                    y_low=y - 3 * fits_header[log.psf_key], y_upper=y + 3 * fits_header[log.psf_key],
                    x_centre=x, y_centre=y,
                    mean=fits_header[log.mean_key], std=fits_header[log.std_key],
                    burn_limit=fits_header[log.hops_saturation_key] * self.saturation,
                    psf=fits_header[log.psf_key],
                    absolute_aperture=self.targets_aperture[target] * self.psf_ratio[counter],
                    sky_inner_aperture=self.sky_inner_aperture, sky_outer_aperture=self.sky_outer_aperture,
                    order_by_flux=False,
                    centroids_snr=self.centroids_snr, stars_snr=self.stars_snr)

                if star or self.faint_target_mode:

                    try:

                        if star:
                            star = star[0]
                            x_mean, y_mean, norm, floor = star[0], star[1], star[2], star[3]
                            x_std, y_std = star[4], star[5]
                            total_app_flux, sky_flux, sky_flux_unc = star[6], star[8], star[9]
                        else:
                            x_mean, y_mean = x, y
                            floor = fits_header[log.mean_key]
                            x_std = y_std = fits_header[log.psf_key]
                            total_app_flux = aperture_photometry(fits_data, CircularAperture(
                                np.array([x_mean - 0.5, y_mean - 0.5]), self.targets_aperture[target]))['aperture_sum'][0]
                            sky_flux = np.pi * (self.targets_aperture[target] ** 2) * fits_header[log.mean_key]
                            sky_flux_unc = np.sqrt(np.pi * (self.targets_aperture[target] ** 2)) * fits_header[log.std_key]
                            norm = (total_app_flux - sky_flux) / (2 * np.pi * x_std * y_std)

                        # psf photometry

                        self.results['gauss_x_position'][target][counter] = x_mean
                        self.results['gauss_y_position'][target][counter] = y_mean
                        self.results['gauss_x_std'][target][counter] = x_std
                        self.results['gauss_y_std'][target][counter] = y_std
                        self.results['gauss_flux'][target][counter] = 2 * np.pi * norm * x_std * y_std
                        self.results['gauss_flux_error'][target][counter] = np.sqrt(np.abs(2 * np.pi * norm * x_std * y_std))

                        if star and self.moving_target_mode:
                            target_polar = cartesian_to_polar(x_mean, y_mean, ref_x_position, ref_y_position)
                            self.targets_r_position[target] = target_polar[0]
                            self.targets_u_position[target] = target_polar[1] - ref_u_position

                        if star and self.use_geometric_center:
                            x1 = int(star[0] - 3 * fits_header[log.psf_key])
                            x2 = x1 + int(6 * fits_header[log.psf_key]) + 1
                            y1 = int(star[1] - 3 * fits_header[log.psf_key])
                            y2 = y1 + int(6 * fits_header[log.psf_key]) + 1
                            area = fits_data[y1:y2, x1:x2]
                            area_x, area_y = np.meshgrid(np.arange(x1, x2) + 0.5, np.arange(y1, y2) + 0.5)
                            x_mean = np.sum(area * area_x) / np.sum(area)
                            y_mean = np.sum(area * area_y) / np.sum(area)
                            total_app_flux = aperture_photometry(fits_data, CircularAperture(
                                np.array([x_mean - 0.5, y_mean - 0.5]), self.targets_aperture[target]))['aperture_sum'][0]

                        # cutout for the live view

                        lim = self.star_image_lim
                        x_min, y_min = int(x_mean - lim), int(y_mean - lim)
                        cutout = fits_data[max(y_min, 0): y_min + 2 * lim, max(x_min, 0): x_min + 2 * lim]
                        self.cutouts[target] = {
                            'data': np.round(cutout.astype(float), 1), 'x0': max(x_min, 0), 'y0': max(y_min, 0),
                            'x': float(x_mean), 'y': float(y_mean),
                            'r': float(self.targets_aperture[target] * self.psf_ratio[counter]),
                            'vmin': float(fits_header[log.mean_key] + log.frame_low_std * fits_header[log.std_key]),
                            'vmax': float(fits_header[log.mean_key] + log.frame_upper_std * fits_header[log.std_key]),
                        }

                        # aperture photometry

                        self.results['aperture_x_position'][target][counter] = x_mean
                        self.results['aperture_y_position'][target][counter] = y_mean
                        self.results['aperture_background'][target][counter] = sky_flux
                        self.results['aperture_background_error'][target][counter] = sky_flux_unc
                        self.results['aperture_flux'][target][counter] = total_app_flux - sky_flux
                        self.results['aperture_flux_error'][target][counter] = np.sqrt(
                            np.abs(total_app_flux - sky_flux) / self.camera_gain + sky_flux_unc ** 2)

                    except ERRORS as e:
                        print(e)
                        self.target_fails[target] += 1
                else:
                    self.target_fails[target] += 1

            job.set_live(**self.live_data(counter + 1))
            job.step('photometry', message=science_file)

        self.finished = True

    def curves(self, upto=None):
        """Relative light curves (normalised by the median), target and each comparison
        against the other active comparisons, as in replot_results."""

        active = np.where(self.active)[0]
        gauss_curves, aperture_curves = [], []
        g = self.results['gauss_flux']
        a = self.results['aperture_flux']
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            for target in range(self.targets):
                if target == 0:
                    gauss_lc = g[0] / np.sum(g[active][1:], 0)
                    aperture_lc = a[0] / np.sum(a[active][1:], 0)
                elif target in active and len(active) > 2:
                    others = [c for c in active if c not in (0, target)]
                    gauss_lc = g[target] / np.sum(g[others], 0)
                    aperture_lc = a[target] / np.sum(a[others], 0)
                else:
                    gauss_lc = g[target] * np.nan
                    aperture_lc = a[target] * np.nan
                gauss_lc = gauss_lc / np.nanmedian(gauss_lc[:upto])
                aperture_lc = aperture_lc / np.nanmedian(aperture_lc[:upto])
                gauss_curves.append(gauss_lc[:upto])
                aperture_curves.append(aperture_lc[:upto])
        return gauss_curves, aperture_curves

    def live_data(self, upto=None):
        gauss_curves, aperture_curves = self.curves(upto)
        hours = (self.results['jd'] - self.results['jd'][0]) * 24
        return to_builtin({
            'hours': hours[:upto],
            'files': self.results['file_names'][:upto],
            'gauss': gauss_curves,
            'aperture': aperture_curves,
            'fails': self.target_fails,
            'active': self.active,
            'cutouts': self.cutouts,
            'apertures': self.targets_aperture,
        })

    def save(self):
        """Port of PhotometryProgressWindow.save: writes a new PHOTOMETRY_n folder."""

        log = self.log
        project = self.project
        folders = project.photometry_folders()
        folder_name = '{0}_{1}'.format(
            log.photometry_directory_base, int(np.max([0] + [photometry_order(ff) for ff in folders]) + 1))
        photometry_directory = project.path(folder_name)
        os.mkdir(photometry_directory)

        self._save_fov(os.path.join(photometry_directory, log.fov_figure))
        self._save_results_figure(os.path.join(photometry_directory, log.lc_figure))

        for target in range(1, self.targets):
            log.set_param('comparison_{0}_active'.format(target), bool(self.active[target]))

        active = np.where(self.active)
        r = self.results

        gauss_lc = r['gauss_flux'][0] / np.sum(r['gauss_flux'][active][1:], 0)
        gauss_lc_error = np.sqrt(
            (r['gauss_flux_error'][0] / r['gauss_flux'][0]) ** 2 +
            (np.sqrt(np.sum(r['gauss_flux_error'][active][1:] ** 2, 0)) / np.sum(r['gauss_flux'][active][1:], 0)) ** 2
        ) * gauss_lc
        aperture_lc = r['aperture_flux'][0] / np.sum(r['aperture_flux'][active][1:], 0)
        aperture_lc_error = np.sqrt(
            (r['aperture_flux_error'][0] / r['aperture_flux'][0]) ** 2 +
            (np.sqrt(np.sum(r['aperture_flux_error'][active][1:] ** 2, 0)) / np.sum(r['aperture_flux'][active][1:], 0)) ** 2
        ) * aperture_lc

        valid_gauss = np.where(~np.isnan(gauss_lc))
        valid_aperture = np.where(~np.isnan(aperture_lc))

        targets_results = [r['file_names'][valid_gauss], r['jd'][valid_gauss]]
        for array in ['gauss_x_position', 'gauss_y_position', 'gauss_x_std', 'gauss_y_std', 'gauss_flux', 'gauss_flux_error']:
            for target in range(self.targets):
                targets_results.append(r[array][target][valid_gauss])
        np.savetxt(os.path.join(photometry_directory, log.photometry_file.replace('.txt', '_g.txt')),
                   np.swapaxes(targets_results, 0, 1), fmt='%s')
        np.savetxt(os.path.join(photometry_directory, log.light_curve_gauss_file), np.swapaxes([
            r['jd'][valid_gauss], gauss_lc[valid_gauss], gauss_lc_error[valid_gauss]], 0, 1))

        targets_results = [r['file_names'][valid_aperture], r['jd'][valid_aperture]]
        for array in ['aperture_x_position', 'aperture_y_position', 'aperture_flux', 'aperture_flux_error',
                      'aperture_background', 'aperture_background_error']:
            for target in range(self.targets):
                targets_results.append(r[array][target][valid_aperture])
        np.savetxt(os.path.join(photometry_directory, log.photometry_file.replace('.txt', '_a.txt')),
                   np.swapaxes(targets_results, 0, 1), fmt='%s')
        np.savetxt(os.path.join(photometry_directory, log.light_curve_aperture_file), np.swapaxes([
            r['jd'][valid_aperture], aperture_lc[valid_aperture], aperture_lc_error[valid_aperture]], 0, 1))

        shutil.copy(log.files['photometry_output_description'], photometry_directory)

        ra_dec_string = log.get_param('target_ra_dec').split(' ')
        try:
            planet = exoclock.locate_planet(exoclock.Hours(ra_dec_string[0]), exoclock.Degrees(ra_dec_string[1]))['name']
        except ERRORS:
            planet = 'Could not find a planet in the ExoClock catalogue at this location'

        with open(os.path.join(photometry_directory, 'ExoClock_info.txt'), 'w') as w:
            w.write('\n'.join([
                'The ExoClock Project is an effort to keep the ephemerides of exoplanets as precise as \n'
                'possible for planning future observations. If you have observed an exoplanet you can\n'
                'contribute your observation at: \n\nhttps://www.exoclock.space\n\n'
                'File to upload: PHOTOMETRY_APERTURE.txt \n(this is a suggestion based on passt experience, '
                'you can also try \nuploading PHOTOMETRY_GAUSS.txt)',
                '',
                'Planet: {0}'.format(planet),
                '',
                'Time format: JD_UTC \n(UTC-based Julian date)',
                '',
                'Time stamp: Exposure start \n(the time produced refers to the beginning of each exposure)',
                '',
                'Flux format: Flux \n(flux of target over summed flux of comparisons)',
                '',
                'Filter: {0}'.format(log.get_param('filter')),
                '',
                'Exposure time in seconds: {0}'.format(self.exposure_time),
            ]))

        log.set_param('photometry_complete', True)
        log.set_param('photometry_version', log.version)
        project.save_log()
        project.export_log(photometry_directory)

        return folder_name

    def _save_fov(self, path):
        log = self.log
        data, header = self.first_fits_data, self.first_fits_header
        mean, std = header[log.mean_key], header[log.std_key]

        figure = Figure(figsize=(8, 6))
        figure.subplots_adjust(left=0.01, right=0.99, bottom=0.05, top=0.89)
        ax = figure.add_subplot(111)
        ax.imshow(data, origin='lower', extent=(0, len(data[0]), 0, len(data)), cmap=Greys_r,
                  vmin=mean + log.frame_low_std * std, vmax=mean + log.frame_upper_std * std)
        ax.axis('off')
        ax.add_patch(mpatches.Rectangle((log.get_param('min_x') + 1, log.get_param('min_y') + 1),
                                        log.get_param('max_x') - log.get_param('min_x') - 2,
                                        log.get_param('max_y') - log.get_param('min_y') - 2, ec='r', fill=False))

        for target in range(self.targets):
            x, y, ap = self.targets_x_position[target], self.targets_y_position[target], self.targets_aperture[target]
            if target == 0:
                ax.text(x + ap, y + ap, '$T$({0})'.format(ap), color='r', fontsize=8, va='top', ha='left')
                ax.add_patch(mpatches.Circle((x, y), ap, ec='r', fill=False, label='Target (ap. radius)'))
            else:
                label = 'Comparisons (ap. radius)' if target == 2 else None
                if self.active[target]:
                    ax.text(x + ap, y + ap, '$C_{0}$({1})'.format(target, ap), color='#07fefc', fontsize=8, va='top', ha='left')
                    ax.add_patch(mpatches.Circle((x, y), ap, ec='#07fefc', fill=False, label=label))
                else:
                    ax.text(x + ap, y + ap, '$C_{0}$({1}) - Inactive'.format(target, ap), color='#07fefc', fontsize=8, va='top', ha='left')
                    ax.add_patch(mpatches.Circle((x, y), ap, ec='#07fefc', fill=False))

        ax.legend(loc=(0, 1.01))
        figure.savefig(path, dpi=200)

    def _save_results_figure(self, path):
        """Re-creates the progress figure of the Tk window: star cutouts and relative light curves."""
        gauss_curves, aperture_curves = self.curves()
        hours = (self.results['jd'] - self.results['jd'][0]) * 24
        figure = Figure(figsize=(8, max(3, 1.3 * self.targets)))
        xx = 1 / (self.targets + 2)
        gs = gridspec.GridSpec(self.targets, 4, figure, 0.01, xx, 0.99, 1 - xx, 1.0, 0.1)
        lims = [0.99, 1.01]
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            for curve in gauss_curves + aperture_curves:
                if np.isfinite(curve).any():
                    lo, hi = np.nanmin(curve), np.nanmax(curve)
                    if lo < lims[0]:
                        lims[0] -= (int((lims[0] - lo) / 0.01) + 1) * 0.01
                    if hi > lims[1]:
                        lims[1] += (int((hi - lims[1]) / 0.01) + 1) * 0.01
        for target in range(self.targets):
            ax_fits = figure.add_subplot(gs[target, 0])
            ax_fits.axis('off')
            cut = self.cutouts[target]
            if cut is not None:
                h, w = cut['data'].shape
                ax_fits.imshow(cut['data'], origin='lower', cmap=Greys_r, vmin=cut['vmin'], vmax=cut['vmax'],
                               extent=(cut['x0'], cut['x0'] + w, cut['y0'], cut['y0'] + h))
                ax_fits.add_patch(mpatches.Circle((cut['x'], cut['y']), cut['r'], ec='r', fill=False))
                lim = 1.5 * np.max(self.targets_aperture)
                ax_fits.set_xlim(cut['x'] - lim, cut['x'] + lim)
                ax_fits.set_ylim(cut['y'] - lim, cut['y'] + lim)
            ax_lc = figure.add_subplot(gs[target, 1:])
            ax_lc.plot(hours, gauss_curves[target], 'ro', ms=3, label='PSF')
            ax_lc.plot(hours, aperture_curves[target], 'ko', ms=3, label='Aperture')
            ax_lc.set_ylim(*lims)
            ax_lc.set_xlim(-0.1 / 60, hours[-1] + 0.1 / 60)
            ax_lc.set_ylabel('T' if target == 0 else 'C{0}'.format(target), fontsize=12)
            if target > 0 and not self.active[target]:
                ax_lc.text(hours[-1] / 2, 1, 'Inactive', fontsize=10, va='center', ha='center')
            if target != self.targets - 1:
                ax_lc.tick_params(labelbottom=False, labelsize=8)
            else:
                ax_lc.set_xlabel('Time (hours in observation)', fontsize=8)
                ax_lc.tick_params(labelsize=8)
        figure.axes[1].legend(bbox_to_anchor=(1, 2.2), fontsize='x-small')
        figure.savefig(path, dpi=200)


def run_photometry(job, project, holder):
    """Job entry point: measures all frames, then saves the results (like the Tk window does)."""
    run = PhotometryRun(project)
    holder['run'] = run
    run.run(job)
    folder = run.save()
    return {'folder': folder, 'fails': to_builtin(run.target_fails), 'targets': run.targets}
