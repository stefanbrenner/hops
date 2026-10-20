
import os
import shutil
import contextlib
import numpy as np
import exoclock
import matplotlib.image as mpimg
import matplotlib.gridspec as gridspec
import hops.pylightcurve41 as plc

from matplotlib.figure import Figure

from hops.pylightcurve41.processes.counter import Counter

from .common import ERRORS, HOPSError, filter_map, to_builtin

PLANET_FIELDS = ['planet', 'target_ra_dec', 'period', 'mid_time', 'rp_over_rs', 'sma_over_rs', 'inclination',
                 'eccentricity', 'periastron', 'metallicity', 'temperature', 'logg']
FLOAT_FIELDS = ['period', 'mid_time', 'rp_over_rs', 'sma_over_rs', 'inclination', 'eccentricity', 'periastron',
                'metallicity', 'temperature', 'logg']


def catalogue(log):
    """Planet parameters from the ExoClock catalogue at the target position (the "auto" column)."""
    try:
        ra_target, dec_target = log.get_param('target_ra_dec').split(' ')
        ecc_data = exoclock.locate_planet(exoclock.Hours(ra_target), exoclock.Degrees(dec_target))
        return to_builtin({
            'planet': ecc_data['name'],
            'target_ra_dec': '{0} {1}'.format(ecc_data['star']['ra'], ecc_data['star']['dec']),
            'metallicity': ecc_data['planet']['meta'],
            'temperature': ecc_data['planet']['teff'],
            'logg': ecc_data['planet']['logg'],
            'period': ecc_data['planet']['ephem_period'],
            'mid_time': ecc_data['planet']['ephem_mid_time'],
            'rp_over_rs': ecc_data['planet']['rp_over_rs'],
            'sma_over_rs': ecc_data['planet']['sma_over_rs'],
            'eccentricity': ecc_data['planet']['eccentricity'],
            'inclination': ecc_data['planet']['inclination'],
            'periastron': ecc_data['planet']['periastron'],
        })
    except ERRORS:
        values = {key: 0.0 for key in FLOAT_FIELDS}
        values.update({'planet': 'None', 'target_ra_dec': 'None'})
        return values


def setup(project):
    log = project.log
    files = project.light_curve_files()
    if log.get_param('light_curve_file') not in files:
        log.set_param('light_curve_file', 'Choose Light-curve file')

    auto = catalogue(log)
    manual = {key: log.get_param(key) for key in PLANET_FIELDS}
    if manual['planet'] == 'Choose Planet':
        manual = dict(auto)

    all_frames = project.load_all_frames()
    frames = sorted([[all_frames[ff][log.time_key], ff] for ff in all_frames if not all_frames[ff][log.skip_key]])

    return to_builtin({
        'files': files,
        'settings': {
            'light_curve_file': log.get_param('light_curve_file'),
            'iterations': log.get_param('iterations'),
            'burn': log.get_param('burn'),
            'a_i_fit': bool(log.get_param('a_i_fit')),
            'detrending': log.get_param('detrending'),
            'manual_planet': bool(log.get_param('manual_planet')),
        },
        'auto': auto,
        'manual': manual,
        'exposure_time': project.median_exposure_time(all_frames),
        'filter': log.get_param('filter'),
        'observer': log.get_param('observer'),
        'observatory': log.get_param('observatory'),
        'telescope': log.get_param('telescope'),
        'camera': log.get_param('camera'),
        'frames': [{'jd': ff[0], 'name': ff[1]} for ff in frames],
    })


def _check_file(project, light_curve_file):
    if light_curve_file not in project.light_curve_files():
        raise HOPSError('Choose a light-curve file first.')


def light_curve(project, light_curve_file):
    _check_file(project, light_curve_file)
    lc = np.loadtxt(project.path(light_curve_file), unpack=True)
    lc[2] = np.abs(lc[2])
    return to_builtin({
        'time': lc[0], 'flux': lc[1], 'flux_unc': lc[2],
        'date': exoclock.Moment(jd_utc=lc[0][0]).utc().isoformat()[:16].replace('T', ' '),
        'duration': round(24 * (lc[0][-1] - lc[0][0]), 1),
    })


def _planet_values(settings, manual, auto):
    values = manual if settings['manual_planet'] else auto
    result = dict(values)
    for key in FLOAT_FIELDS:
        result[key] = float(result[key])
    return result


def save_settings(project, settings, manual, auto):
    """Port of FittingWindow.save_settings."""
    log = project.log
    planet = _planet_values(settings, manual, auto)
    log.set_param('light_curve_file', settings['light_curve_file'])
    log.set_param('iterations', int(settings['iterations']))
    log.set_param('burn', int(settings['burn']))
    log.set_param('a_i_fit', bool(settings['a_i_fit']))
    log.set_param('detrending', settings['detrending'])
    log.set_param('manual_planet', bool(settings['manual_planet']))
    for key in PLANET_FIELDS:
        log.set_param(key, planet[key])
    project.save_log()


@contextlib.contextmanager
def _mcmc_progress(job):
    """pylightcurve reports the emcee progress through Counter.update every 10 iterations;
    forward it to the job (and use it as the place to stop the fit)."""
    original = Counter.update

    def update(counter, message=''):
        original(counter, message)
        job.step('mcmc', n=counter.increment, message=counter.text.split(',')[0])
        job.check()

    Counter.update = update
    try:
        yield
    finally:
        Counter.update = original


def call_plc_fitting(project, settings, planet_values, exposure_time, optimiser, output_folder=None):
    """Port of FittingWindow.call_plc_fitting."""

    log = project.log
    light_curve_data = np.loadtxt(project.path(settings['light_curve_file']), unpack=True)

    location_string = log.get_param('location').split(' ')
    observatory = exoclock.Observatory(exoclock.Degrees(location_string[0]), exoclock.Degrees(location_string[1]))

    ra_dec_string = planet_values['target_ra_dec'].split(' ')
    planet = plc.Planet(
        planet_values['planet'], exoclock.Hours(ra_dec_string[0]).deg(), exoclock.Degrees(ra_dec_string[1]).deg_coord(),
        planet_values['logg'], planet_values['temperature'], planet_values['metallicity'],
        planet_values['rp_over_rs'], planet_values['period'], planet_values['sma_over_rs'],
        planet_values['eccentricity'], planet_values['inclination'], planet_values['periastron'],
        planet_values['mid_time'])

    if settings['detrending'] == 'Airmass':
        detrending_series, detrending_order = 'airmass', 1
    elif settings['detrending'] == 'Linear':
        detrending_series, detrending_order = 'time', 1
    else:
        detrending_series, detrending_order = 'time', 2

    planet.add_observation(
        time=light_curve_data[0], time_format='JD_UTC', exp_time=float(exposure_time), time_stamp='start',
        flux=light_curve_data[1], flux_unc=light_curve_data[2], flux_format='flux',
        filter_name=filter_map[log.get_param('filter')],
        observatory_latitude=observatory.latitude.deg_coord(), observatory_longitude=observatory.longitude.deg(),
        detrending_series=detrending_series, detrending_order=detrending_order)

    fit = bool(settings['a_i_fit'])

    return planet, planet.transit_fitting(
        output_folder=output_folder, scale_uncertainties=True, filter_outliers=True,
        fit_sma_over_rs=fit, fit_inclination=fit, counter=None, window_counter=False,
        iterations=int(settings['iterations']), burn_in=int(settings['burn']), optimiser=optimiser)


def result_series(log, settings, planet, fitting_results, exposure_time, lc_type):
    """The data drawn by plot_results_to_axes, as plain arrays (the browser draws them)."""

    phot_filter = filter_map[log.get_param('filter')]
    new_mid_time = fitting_results['parameters']['mid_time']['value']
    epoch = round((new_mid_time - planet.mid_time) / planet.period)
    predicted_mid_time = planet.mid_time + epoch * planet.period
    obs = fitting_results['observations']['obs0']

    outliers = {'time': [], 'flux': []}
    if lc_type == 'Raw':
        non_outliers = obs['data_conversion_info']['non_outliers_map']
        outlier_idx = [ff for ff in np.arange(len(obs['original_data']['time'])) if ff not in non_outliers]
        time = obs['original_data']['time']
        flux = obs['original_data']['flux']
        flux_unc = obs['original_data']['flux_unc'] * obs['data_conversion_info']['scale_factor']
        best_fit_model_time = time[non_outliers]
        best_fit_model_flux = fitting_results['output_series']['model']
        predicted_model_time = time[non_outliers]
        predicted_model_flux = planet.transit_integrated(
            fitting_results['detrended_series']['time'], float(exposure_time), phot_filter
        ) * (best_fit_model_flux / fitting_results['detrended_series']['model'])
        std_res = fitting_results['statistics']['res_std']
        outliers = {'time': time[outlier_idx], 'flux': flux[outlier_idx]}
    else:
        time = fitting_results['detrended_series']['time']
        flux = fitting_results['detrended_series']['flux']
        flux_unc = fitting_results['detrended_series']['flux_unc']
        best_fit_model_time = time
        best_fit_model_flux = fitting_results['detrended_series']['model']
        predicted_model_time = fitting_results['detrended_series']['time']
        predicted_model_flux = planet.transit_integrated(
            fitting_results['detrended_series']['time'], float(exposure_time), phot_filter)
        std_res = fitting_results['detrended_statistics']['res_std']

    p = fitting_results['parameters']

    return to_builtin({
        'type': lc_type,
        'planet': planet.name,
        'time': time, 'flux': flux, 'flux_unc': flux_unc,
        'outliers': outliers,
        'best_fit': {'time': best_fit_model_time, 'flux': best_fit_model_flux},
        'expected': {'time': predicted_model_time, 'flux': predicted_model_flux},
        'residuals': {
            'phase': (np.array(predicted_model_time) - predicted_mid_time) / planet.period,
            'flux': fitting_results['detrended_series']['residuals'],
            'flux_unc': fitting_results['detrended_series']['flux_unc'],
        },
        'std_res': std_res,
        'detrended_std_res': fitting_results['detrended_statistics']['res_std'],
        'autocorr': fitting_results['statistics']['res_max_autocorr'],
        'autocorr_flag': bool(fitting_results['statistics']['res_max_autocorr_flag']),
        'mid_time': {k: p['mid_time'][k] for k in ['print_name', 'print_value', 'print_m_error', 'print_p_error', 'value', 'm_error', 'p_error']},
        'rp_over_rs': {k: p['rp_over_rs'][k] for k in ['print_name', 'print_value', 'print_m_error', 'print_p_error']},
        'predicted_mid_time': round(predicted_mid_time, 5),
        'predicted_rp_over_rs': round(planet.rp_over_rs, 5),
        'o_c': {
            'value': round((new_mid_time - predicted_mid_time) * 24 * 60, 1),
            'm_error': round(p['mid_time']['m_error'] * 24 * 60, 1) if p['mid_time']['m_error'] is not None else None,
            'p_error': round(p['mid_time']['p_error'] * 24 * 60, 1) if p['mid_time']['p_error'] is not None else None,
        },
        'detrending': settings['detrending'],
        'a_i': ['Fixed', 'Free'][int(bool(settings['a_i_fit']))],
    })


def results_layout(figure, log, fontsizes=(7, 9, 20)):
    """The HOPS results figure layout: logo, light curve with models, residuals and three titles.
    Used for detrended_model.jpg and by the Tk fitting window."""

    fcol, frow, fbottom, fright = 7, 5, 0.11, 0.02
    fssmall, fsmain, fsbig = fontsizes
    gs = gridspec.GridSpec(frow, fcol, figure, 0.03, fbottom, 1.0 - fright, 1.0, 0.0, 0.0)

    logo_ax = figure.add_subplot(gs[0, 0])
    logo_ax.imshow(mpimg.imread(log.files['holomon_logo']))
    for spine in logo_ax.spines.values():
        spine.set_visible(False)
    logo_ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)

    figure.text(0.04, fbottom + 2.5 * (1 - fbottom) / frow, 'Relative flux (norm)', fontsize=fsmain, va='center',
                ha='center', rotation='vertical')
    figure.text(0.04, fbottom + 0.5 * (1 - fbottom) / frow, 'Residuals (norm)', fontsize=fsmain, va='center',
                ha='center', rotation='vertical')

    return {
        'ax1': figure.add_subplot(gs[1:4, 1:]),
        'ax2': figure.add_subplot(gs[4, 1:]),
        'title1': figure.text(0.5, 0.94, '', fontsize=fsbig, va='center', ha='center'),
        'title2': figure.text(0.97, 0.97, '', fontsize=fsmain, va='top', ha='right'),
        'title3': figure.text(0.03 + (1 - fright) / fcol, 1 - (1 - fbottom) / frow, '', fontsize=fsmain, ha='left', va='bottom'),
        'fssmall': fssmall,
        'fsmain': fsmain,
    }


def draw_titles(log, parts, planet_name, light_curve_info, exposure_time):
    parts['title1'].set_text(r'{0}{1}{2}'.format(r'$\mathbf{', planet_name, '}$'))
    parts['title2'].set_text('{0} (UT)\nDur: {1}h / Exp: {2}s\nFilter: {3}'.format(
        light_curve_info['date'], light_curve_info['duration'], exposure_time, log.get_param('filter')))
    parts['title3'].set_text('\n\n{0}\n{1}'.format(
        log.get_param('observer'), '{0} / {1} / {2}'.format(
            log.get_param('observatory'), log.get_param('telescope'), log.get_param('camera'))))


def draw_results(log, parts, series, light_curve_info, exposure_time):
    """Port of plot_results_to_axes: draws a result_series into a results_layout."""

    ax1, ax2 = parts['ax1'], parts['ax2']
    fssmall, fsmain = parts['fssmall'], parts['fsmain']
    draw_titles(log, parts, series['planet'], light_curve_info, exposure_time)

    ax1.cla()
    flux = np.array(series['flux'], dtype=float)
    std_res = series['std_res']
    if len(series['outliers']['time']):
        ax1.plot(series['outliers']['time'], series['outliers']['flux'], 'rx', ms=7, label='Outliers (not fitted)', zorder=1)
    ax1.errorbar(series['time'], flux, series['flux_unc'], c='k', fmt='o', ms=2, lw=0.5,
                 label=['de-trended data', 'raw data'][series['type'] == 'Raw'], zorder=0)

    data_ymin = min(flux) - 5 * std_res
    data_ymax = max(flux) + 2 * std_res
    ax1.set_yticks(ax1.get_yticks()[np.where(ax1.get_yticks() > data_ymin)])
    ax1.set_ylim(data_ymax - 1.1 * (data_ymax - data_ymin), data_ymax)
    ax1.tick_params(labelbottom=False, labelsize=fsmain)

    m, r = series['mid_time'], series['rp_over_rs']
    ax1.plot(series['best_fit']['time'], series['best_fit']['flux'], 'r-',
             label='Best-fit model (De-trending: {11}, a/Rs,i: {12})\n{0}{3}{0}={0}{4}_{1}-{5}{2}^{1}+{6}{2}{0}, '
                   '{0}{7}{0}={0}{8}_{1}-{9}{2}^{1}+{10}{2}{0}'.format(
                       '$', '{', '}', m['print_name'], m['print_value'], m['print_m_error'], m['print_p_error'],
                       r['print_name'], r['print_value'], r['print_m_error'], r['print_p_error'],
                       series['detrending'], series['a_i']), zorder=3)
    oc = series['o_c']
    ax1.plot(series['expected']['time'], series['expected']['flux'], 'c-',
             label='Expected model\n{0}{3}{0}={0}{4}{0}, {0}{5}{0}={0}{6}{0}, O-C={0}{7}_{1}-{8}{2}^{1}+{9}{2}{0} min'.format(
                 '$', '{', '}', m['print_name'], series['predicted_mid_time'], r['print_name'],
                 series['predicted_rp_over_rs'], oc['value'], oc['m_error'], oc['p_error']), zorder=2)
    ax1.legend(loc=3, fontsize=fssmall)

    ax2.cla()
    res = series['residuals']
    detrended_std_res = series['detrended_std_res']
    ax2.errorbar(res['phase'], res['flux'], res['flux_unc'], c='k', fmt='o', ms=2, lw=0.5)
    ax2.plot(res['phase'], np.zeros_like(res['phase']), 'r-')
    ax2.set_ylim(-6 * detrended_std_res, 6 * detrended_std_res)
    ax2.set_xlabel('Phase', fontsize=fsmain)
    ax2.tick_params(labelsize=fsmain)
    xlim, ylim = ax2.get_xlim(), ax2.get_ylim()
    ax2.text(xlim[0] + 0.02 * (xlim[1] - xlim[0]), ylim[1] - 0.15 * (ylim[1] - ylim[0]),
             r'STD = %.2f $‰$' % (detrended_std_res * 1000), fontsize=fssmall)
    ax2.text(xlim[0] + 0.02 * (xlim[1] - xlim[0]), ylim[0] + 0.07 * (ylim[1] - ylim[0]),
             r'AutoCorr = %.2f' % series['autocorr'] + [' (acceptable)', ' (too high)'][int(series['autocorr_flag'])],
             fontsize=fssmall)


def plot_results_figure(log, series, light_curve_info, exposure_time):
    """Matplotlib version of the results plot, for detrended_model.jpg."""
    funit, fcol, frow, fbottom, fright = 0.8, 7, 5, 0.11, 0.02
    figure = Figure(figsize=(funit * fcol / (1 - fright), funit * frow / (1 - fbottom)))
    draw_results(log, results_layout(figure, log), series, light_curve_info, exposure_time)
    return figure


def run_test(job, project, settings, manual, auto):
    """Port of run_test: quick least-squares fit, nothing is saved."""
    log = project.log
    _check_file(project, settings['light_curve_file'])
    job.set_message('Fitting with curve_fit...')
    planet_values = _planet_values(settings, manual, auto)
    exposure_time = project.median_exposure_time()
    try:
        planet, fitting = call_plc_fitting(project, settings, planet_values, exposure_time, 'curve_fit')
    except ERRORS as e:
        if log.get_param('location') == '+dd:mm:ss +dd:mm:ss':
            raise HOPSError('Test failed! Please update the location of your telescope in the Data & target step.')
        if auto['planet'] == 'None' and not settings['manual_planet']:
            raise HOPSError('Test failed! Please update the target coordinates in the Data & target step '
                            'or add the planet parameters manually.')
        raise HOPSError('Test failed! Please change the input parameters.\nMessage: {0}'.format(e))
    job.set_message('')
    return {'series': result_series(log, settings, planet, fitting, exposure_time, 'Raw')}


def run_fitting(job, project, settings, manual, auto):
    """Port of run_fitting: MCMC fit, output files in <light-curve>_FITTING[_n]."""

    log = project.log
    _check_file(project, settings['light_curve_file'])
    save_settings(project, settings, manual, auto)
    planet_values = _planet_values(settings, manual, auto)
    exposure_time = project.median_exposure_time()

    folder_name = '.'.join(settings['light_curve_file'].split('.')[:-1]) + '_' + log.fitting_directory_base
    if os.path.isdir(project.path(folder_name)):
        fi = 2
        while os.path.isdir(project.path('{0}_{1}'.format(folder_name, str(fi)))):
            fi += 1
        folder_name = '{0}_{1}'.format(folder_name, str(fi))
    fitting_directory = project.path(folder_name)
    os.mkdir(fitting_directory)

    job.task('mcmc', 'MCMC', int(settings['iterations']))

    try:
        with _mcmc_progress(job):
            planet, fitting = call_plc_fitting(project, settings, planet_values, exposure_time, 'emcee', fitting_directory)

        if not fitting['mcmc_run_complete']:
            raise HOPSError('The MCMC did not complete.')

        job.set_message('Saving results...')

        shutil.copy(log.files['fitting_output_description'], fitting_directory)
        shutil.move(os.path.join(fitting_directory, 'global_correlations.pdf'), os.path.join(fitting_directory, 'corner.pdf'))
        shutil.move(os.path.join(fitting_directory, 'global_traces.pdf'), os.path.join(fitting_directory, 'traces.pdf'))
        shutil.move(os.path.join(fitting_directory, 'obs0_results.txt'), os.path.join(fitting_directory, 'results.txt'))
        for name in ['global_results.pickle', 'global_results.txt', 'obs0_results.pickle', 'obs0_lightcurve.pdf']:
            os.remove(os.path.join(fitting_directory, name))

        phase = (fitting['input_series']['time'] - fitting['parameters']['mid_time']['value']) / fitting['parameters']['period']['value']
        np.savetxt(os.path.join(fitting_directory, 'model.txt'), np.swapaxes([
            fitting['input_series']['time'], phase, fitting['input_series']['flux'], fitting['input_series']['flux_unc'],
            fitting['output_series']['model'], fitting['output_series']['residuals']], 0, 1))
        np.savetxt(os.path.join(fitting_directory, 'detrended_model.txt'), np.swapaxes([
            fitting['input_series']['time'], phase, fitting['detrended_series']['flux'], fitting['detrended_series']['flux_unc'],
            fitting['detrended_series']['model'], fitting['detrended_series']['residuals']], 0, 1))

        series = result_series(log, settings, planet, fitting, exposure_time, 'De-trended')
        figure = plot_results_figure(log, series, light_curve(project, settings['light_curve_file']), exposure_time)
        figure.savefig(os.path.join(fitting_directory, 'detrended_model.jpg'), dpi=1200, transparent=False)

        log.set_param('fitting_complete', True)
        log.set_param('fitting_version', log.version)
        project.save_log()
        project.export_log(fitting_directory)

    except BaseException:
        shutil.rmtree(fitting_directory, ignore_errors=True)
        raise

    job.set_message('')
    return {'series': series, 'folder': folder_name,
            'files': sorted(os.listdir(fitting_directory))}


def export_info(project, settings, manual, auto, database):
    """Port of export_window_update: header and explanation for ExoClock / ETD."""

    log = project.log
    planet = manual['planet'] if settings['manual_planet'] else auto['planet']
    exposure_time = project.median_exposure_time()
    phot_filter = log.get_param('filter')

    if database == 'ExoClock':
        time_shift = 0
        header = '\n'.join([
            '#Planet: {0}'.format(planet), '#Time format: JD_UTC', '#Time stamp: Exposure start',
            '#Flux format: Flux', '#Filter: {0}'.format(phot_filter),
            '#Exposure time in seconds: {0}'.format(exposure_time),
            '#Time                   Flux            Flux uncertainty\n'])
        message = ('No transformation is needed to upload your results to ExoClock (www.exoclock.space).\n\n'
                   'When uploading you will need to use the following details:\n'
                   'Planet: {0}\nTime format: JD_UTC\nTime stamp: Exposure start\nFlux format: Flux\n'
                   'Filter: {1}\nExposure time in seconds: {2}'.format(planet, phot_filter, exposure_time))
        default_gain = 1.0
    else:
        time_shift = exposure_time / 60 / 60 / 24 / 2
        header = '\n'.join([
            '#Planet: {0}'.format(planet), '#Time format: JD_UTC (geocentric)', '#Time stamp: Mid-exposure',
            '#Flux format: Flux (in flux)', '#Filter: {0}'.format(phot_filter),
            '#Exposure time in seconds: {0}'.format(exposure_time),
            '#Time                   Flux            Flux uncertainty\n'])
        message = ('To upload your results to ETD (var2.astro.cz/ETD) the following modifications are applied:\n\n'
                   '1. Half of the exposure time is added as the time-stamp required on ETD refers to the exposure mid-time\n'
                   '2. The uncertainties are multiplied by 1/SQRT(gain) to represent the uncertainties in electrons '
                   'rather than counts.\n\nWhen uploading you will need to use the following details:\n'
                   'Planet: {0}\nJD format: geocentric\nBrightness column: in flux'.format(planet))
        default_gain = None

    return {'planet': planet, 'header': header, 'message': message, 'time_shift': time_shift,
            'exposure_time': exposure_time, 'default_gain': default_gain}


def export(project, settings, manual, auto, database, camera_gain):
    """Port of FittingWindow.export. Returns the name of the written file (in the data directory)."""

    log = project.log
    _check_file(project, settings['light_curve_file'])
    info = export_info(project, settings, manual, auto, database)
    lc = np.loadtxt(project.path(settings['light_curve_file']), unpack=True)
    observation_date = exoclock.Moment(jd_utc=lc[0][0]).utc().isoformat()[:16].replace('T', ' ')

    file_name = 'HOPS_{5}_{0}_{1}_{2}_{3}s_for_{4}.txt'.format(
        observation_date[:10], info['planet'], log.get_param('filter'), info['exposure_time'], database,
        settings['light_curve_file'].replace(os.sep, '_').replace('.txt', ''))

    with open(project.path(file_name), 'w') as w:
        w.write(info['header'])
        for i in range(len(lc[0])):
            w.write('{0:.10f}\t{1:.10f}\t{2:.10f}\n'.format(
                lc[0][i] + info['time_shift'], lc[1][i], lc[2][i] * (1 / np.sqrt(float(camera_gain)))))

    return file_name
