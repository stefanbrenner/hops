
import numpy as np

from matplotlib.backend_bases import MouseEvent as mpl_MouseEvent

from hops.application_windows import MainWindow, AddOnWindow
from hops.application_jobs import TkJobRunner
from hops.core import fitting
from hops.core.common import ERRORS, HOPSError
from hops.core.jobs import Job
from hops.core.project import Project


class FittingWindow(MainWindow):
    """Transit fitting; the fits, the result files and the export are done by hops.core.fitting."""

    def __init__(self, log):

        # define windows

        MainWindow.__init__(self, log, name='HOPS - Fitting', position=2)
        self.export_window = AddOnWindow(self, name='Export Results', position=1)
        self.mcmc_window = AddOnWindow(self, name='MCMC progress', position=5)

        self.project = Project(log)
        self.runner = None
        self.light_curve_info = None

        # data

        all_frames = self.project.load_all_frames()
        self.science_files = [[name, all_frames[name][self.log.time_key]] for name in self.project.science_frames(all_frames)]
        self.exposure_time = self.project.median_exposure_time(all_frames)

        # main window

        photometry_files = ['Choose Light-curve file'] + self.project.light_curve_files()
        if self.log.get_param('light_curve_file') not in photometry_files:
            self.log.set_param('light_curve_file', 'Choose Light-curve file')

        self.light_curve_file = self.DropDown(initial=self.log.get_param('light_curve_file'),
                                              instance=str,
                                              options=photometry_files,
                                              width=40, command=self.update_lc)

        self.iterations = self.Entry(value=self.log.get_param('iterations'), instance=int)
        self.burn = self.Entry(value=self.log.get_param('burn'), instance=int)
        self.a_i_fit = self.CheckButton(initial=self.log.get_param('a_i_fit'), text='Fit for a/Rs, i.',
                                        command=self.update_planet)
        self.detrending = self.DropDown(initial=self.log.get_param('detrending'),
                                        options=['Airmass', 'Quadratic', 'Linear'],
                                        instance=str, command=self.update_planet)
        self.manual_planet = self.CheckButton(initial=self.log.get_param('manual_planet'),
                                              text='Enter param. manually', command=self.update_planet)

        self.auto = fitting.catalogue(self.log)

        self.planet = self.Entry(value=self.log.get_param('planet'), command=self.update_planet)
        self.target_ra_dec = self.Entry(value=self.log.get_param('target_ra_dec'), command=self.update_planet)
        for key in fitting.FLOAT_FIELDS:
            setattr(self, key, self.Entry(value=self.log.get_param(key), instance=float, command=self.update_planet))

        self.auto_labels = {}
        for key in fitting.PLANET_FIELDS:
            instance = str if key in ['planet', 'target_ra_dec'] else float
            self.auto_labels[key] = self.Label(text=self.auto[key], instance=instance)

        if self.planet.get() == 'Choose Planet':
            for key in fitting.PLANET_FIELDS:
                getattr(self, key).set(self.auto[key])

        self.manual_widgets = [getattr(self, key) for key in fitting.PLANET_FIELDS]

        self.test_button = self.Button(text='RUN TEST', command=[self.disable, self.run_test],
                                       bg='green', highlightbackground='green')
        self.fitting_button = self.Button(text='RUN FITTING', command=[self.disable, self.run_fitting],
                                          bg='green', highlightbackground='green')

        self.export_button = self.Button(text='EXPORT FOR DATABASES (ExoClock / ETD)', command=self.export_window.show)

        # preview window

        self.preview_figure = self.FigureWindow(figsize=(6, 7), show_nav=True)
        self.preview_figure.figure.canvas.callbacks.connect('button_press_event', self.show_point_info)
        self.parts = fitting.results_layout(self.preview_figure.figure, self.log)
        self.dblclick = False

        labels = {'planet': 'Planet', 'target_ra_dec': 'Planet RA DEC\n(hh:mm:ss +/-dd:mm:ss)', 'period': 'Period [days]',
                  'mid_time': 'Mid-time [BJD_TDB]', 'rp_over_rs': 'Rp/Rs', 'sma_over_rs': 'a/Rs',
                  'inclination': 'Inclination [deg]', 'eccentricity': 'Eccentricity', 'periastron': 'Periastron [deg]',
                  'metallicity': 'M* [Fe/H, dex]', 'temperature': 'T* [K]', 'logg': 'log(g*) [cm/s^2]'}

        structure = [
            [[self.preview_figure, 0, 1, 24]],
            [[self.Label(text='Light-curve file'), 1, 3]],
            [[self.light_curve_file, 1, 3]],
            [[self.export_button, 1, 3]],
            [],
            [[self.Label(text='Catalogue param.'), 2], [self.manual_planet, 3]],
        ]
        for key in ['planet', 'target_ra_dec', 'period', 'mid_time', 'rp_over_rs', 'sma_over_rs', 'inclination',
                    'eccentricity', 'periastron', 'metallicity', 'temperature', 'logg']:
            structure.append([[self.Label(text=labels[key]), 1], [self.auto_labels[key], 2], [getattr(self, key), 3]])
        structure += [
            [[self.Label(text='MCMC options:'), 1, 2], [self.Label(text='De-trending:'), 3]],
            [[self.Label(text='Iterations (def=5000)'), 1], [self.iterations, 2], [self.detrending, 3]],
            [[self.Label(text='Burn-in (def=1000)'), 1], [self.burn, 2], [self.a_i_fit, 3]],
            [[self.Button(text='RETURN TO\nMAIN MENU', command=self.close), 1],
             [self.Button(text='SAVE OPTIONS &\nRETURN TO MAIN MENU', command=[self.save_settings, self.close]), 2],
             [self.Button(text='RETURN TO\nPHOTOMETRY', command=self.return_to_photometry), 3]],
            [[self.test_button, 1], [self.fitting_button, 2]],
            [],
        ]
        self.setup_window(structure)

        # MCMC progress window

        self.mcmc_progress = self.mcmc_window.Progressbar(task='MCMC')
        self.mcmc_window.setup_window([
            [[self.mcmc_progress, 0]],
            [[self.mcmc_window.Button(text='STOP', command=self.stop_fitting), 0]],
        ])

        # export window

        self.export_window_database = self.export_window.DropDown(initial='ExoClock', options=['ExoClock', 'ETD'],
                                                                  instance=str, command=self.export_window_update)
        self.export_window_camera_gain = self.export_window.Entry(value='1', instance=float)
        self.export_window_message = self.export_window.Label(text=' ')
        self.export_window.setup_window([
            [],
            [[self.export_window.Label(text='Database:'), 0]],
            [[self.export_window_database, 0]],
            [],
            [[self.export_window.Label(text='Camera gain:'), 0]],
            [[self.export_window_camera_gain, 0]],
            [],
            [[self.export_window_message, 0]],
            [],
            [[self.export_window.Button(text='EXPORT', command=self.export), 0]],
            []
        ])

        self.update_lc()
        self.update_planet()
        self.export_window_update()

    # values for hops.core

    def settings(self):
        return {
            'light_curve_file': self.light_curve_file.get(),
            'iterations': self.iterations.get(),
            'burn': self.burn.get(),
            'a_i_fit': bool(self.a_i_fit.get()),
            'detrending': self.detrending.get(),
            'manual_planet': bool(self.manual_planet.get()),
        }

    def manual(self):
        return {key: getattr(self, key).get() for key in fitting.PLANET_FIELDS}

    def set_manual_state(self):
        for widget in self.manual_widgets:
            if self.manual_planet.get():
                widget.activate()
            else:
                widget.disable()

    # window updates

    def valid_file(self):
        return self.light_curve_file.get() in self.project.light_curve_files()

    def update_lc(self, *entry):

        self.fitting_button.disable()
        ax1, ax2 = self.parts['ax1'], self.parts['ax2']
        ax1.cla()
        ax2.cla()

        if not self.valid_file():
            for widget in [self.export_button, self.iterations, self.burn, self.a_i_fit, self.detrending,
                           self.test_button, self.manual_planet] + self.manual_widgets:
                widget.disable()
            self.parts['title2'].set_text('')
            self.parts['title3'].set_text('')
        else:
            for widget in [self.export_button, self.iterations, self.burn, self.a_i_fit, self.detrending,
                           self.test_button, self.manual_planet]:
                widget.activate()
            self.set_manual_state()

            lc = fitting.light_curve(self.project, self.light_curve_file.get())
            self.light_curve_info = lc
            ax1.errorbar(lc['time'], lc['flux'], lc['flux_unc'], c='k', fmt='o', ms=2, lw=0.5, zorder=0)
            fitting.draw_titles(self.log, self.parts, self.planet_name(), lc, self.exposure_time)

        self.preview_figure.draw(update_level=2)

    def planet_name(self):
        return self.planet.get() if self.manual_planet.get() else self.auto['planet']

    def update_planet(self, *entry):
        self.fitting_button.disable()
        self.set_manual_state()
        self.parts['title1'].set_text(r'{0}{1}{2}'.format(r'$\mathbf{', self.planet_name(), '}$'))
        self.preview_figure.draw()

    def run_test(self):
        try:
            result = fitting.run_test(Job('fitting test'), self.project, self.settings(), self.manual(), self.auto)
            fitting.draw_results(self.log, self.parts, result['series'], self.light_curve_info, self.exposure_time)
            self.preview_figure.draw()
            self.activate()
        except ERRORS as e:
            self.showinfo('Test failed', str(e))
            self.activate()
            self.fitting_button.disable()
        self.set_manual_state()

    def run_fitting(self):
        self.mcmc_window.show()
        self.runner = TkJobRunner(self, 'fitting', fitting.run_fitting, self.project, self.settings(), self.manual(),
                                  self.auto, progressbars={'mcmc': self.mcmc_progress}, on_finished=self.fitting_finished)
        self.runner.start()

    def stop_fitting(self):
        if self.runner is not None:
            self.runner.cancel()

    def fitting_finished(self, job):
        self.mcmc_window.hide()
        self.activate()
        self.set_manual_state()
        if job.status == 'done':
            fitting.draw_results(self.log, self.parts, job.result['series'], self.light_curve_info, self.exposure_time)
            self.preview_figure.draw()
            self.showinfo('Results saved successfully',
                          'Your results have been saved successfully in {0}'.format(job.result['folder']))
        elif job.status == 'error':
            self.showinfo('Fitting failed', job.error)

    def show_point_info(self, event=None):
        if isinstance(event, mpl_MouseEvent):
            if event.inaxes:
                if event.dblclick and not self.dblclick:
                    if event.y > 0.33 * self.preview_figure.canvas.get_tk_widget().winfo_reqheight():
                        idx = np.argmin((event.xdata - np.array([ff[1] for ff in self.science_files])) ** 2)
                        self.showinfo('Point information', self.science_files[idx][0])
                    self.dblclick = True
                else:
                    self.dblclick = False

    def save_settings(self):
        fitting.save_settings(self.project, self.settings(), self.manual(), self.auto)

    def return_to_photometry(self):
        self.save_settings()
        self.log.set_param('proceed', 'return_to_photometry')
        self.close()

    def export(self):
        try:
            file_name = fitting.export(self.project, self.settings(), self.manual(), self.auto,
                                       self.export_window_database.get(), self.export_window_camera_gain.get())
            self.showinfo('Export', 'File saved at: ' + file_name)
        except HOPSError as e:
            self.showinfo('Export', str(e))

    def export_window_update(self):
        if not self.valid_file():
            return
        info = fitting.export_info(self.project, self.settings(), self.manual(), self.auto,
                                   self.export_window_database.get())
        if info['default_gain'] is not None:
            self.export_window_camera_gain.set(info['default_gain'])
        self.export_window_message.set(info['message'])
