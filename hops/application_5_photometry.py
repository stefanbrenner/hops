
import warnings
import numpy as np
import matplotlib
import webbrowser
import matplotlib.patches as mpatches
import matplotlib.gridspec as gridspec

from matplotlib.cm import Greys_r

from hops.application_windows import MainWindow, AddOnWindow
from hops.application_jobs import TkJobRunner
from hops.core import photometry
from hops.core.common import ERRORS
from hops.core.jobs import Job
from hops.core.project import Project


class PhotometryWindow(MainWindow):
    """Selection of the target and comparison stars. Star finding, validation and saving are done
    by hops.core.photometry, the same code the web interface uses."""

    def __init__(self, log):

        MainWindow.__init__(self, log, name='HOPS - Photometry', position=2)

        self.project = Project(log)
        self.frame = photometry.FirstFrame(self.project)

        ##############################################################################
        # set up advanced settings window

        self.advanced_settings_window = AddOnWindow(self, name='Advanced settings', position=2)
        self.show_advanced_settings_button = self.Button(text='Advanced settings', command=self.advanced_settings_window.show)

        self.use_variable_aperture = self.advanced_settings_window.CheckButton(
            text='Vary the aperture size proportionally to the variations of the PSF size.',
            initial=self.log.get_param('use_variable_aperture'),
            command=self.update_window
        )

        self.use_geometric_center = self.advanced_settings_window.CheckButton(
            text='Align the aperture with the geometric center instead of the PSF peak.',
            initial=self.log.get_param('use_geometric_center'),
            command=self.update_window
        )

        self.sky_inner_aperture = self.advanced_settings_window.Entry(
            value=self.log.get_param('sky_inner_aperture'), instance=float, command=self.update_window)
        self.sky_outer_aperture = self.advanced_settings_window.Entry(
            value=self.log.get_param('sky_outer_aperture'), instance=float, command=self.update_window)
        self.saturation = self.advanced_settings_window.Entry(
            value=self.log.get_param('saturation'), instance=float, command=self.update_window)
        self.star_size_arcsec = self.advanced_settings_window.Entry(
            value=self.log.get_param('star_size_arcsec'), instance=float, command=self.update_window)
        self.camera_gain = self.advanced_settings_window.Entry(
            value=self.log.get_param('camera_gain'), instance=float, command=self.update_window)

        self.advanced_settings_window.setup_window([
            [],
            [
                [self.advanced_settings_window.Label(text='Inner sky-ring radius, relatively to the aperture'), 0],
                [self.sky_inner_aperture, 1],
                [self.advanced_settings_window.Label(text='(default = 1.7)'), 2]
             ],
            [
                [self.advanced_settings_window.Label(text='Outer sky-ring radius, relatively to the aperture'), 0],
                [self.sky_outer_aperture, 1],
                [self.advanced_settings_window.Label(text='(default = 2.4)'), 2]
            ],
            [[self.advanced_settings_window.Label(text='Note: the bright pixels inside the sky-ring\nare NOT taken into account for the sky background estimation.'), 0, 3]],
            [],
            [
                [self.advanced_settings_window.Label(text='Saturation limit, relatively to the full-well depth'), 0],
                [self.saturation, 1],
                [self.advanced_settings_window.Label(text='(default = 0.95)'), 2]],
            [],
            [
                [self.advanced_settings_window.Label(text='Star FWHM in arcseconds (approximate)'), 0],
                [self.star_size_arcsec, 1],
                [self.advanced_settings_window.Label(text='(default = 4.0)'), 2]],
            [],
            [
                [self.advanced_settings_window.Label(text='Camera gain in e/ADU'), 0],
                [self.camera_gain, 1],
                [self.advanced_settings_window.Label(text='(default = 1.0)'), 2]],
            [],
            [[self.use_variable_aperture, 0, 3]],
            [[self.use_geometric_center, 0, 3]],
            [],
        ])
        ##############################################################################

        ##############################################################################
        # set up variables

        self.max_targets = self.log.get_param('max_comparisons') + 1
        self.visible_fov_x_min = self.log.get_param('min_x')
        self.visible_fov_y_min = self.log.get_param('min_y')
        self.visible_fov_x_max = self.log.get_param('max_x')
        self.visible_fov_y_max = self.log.get_param('max_y')

        self.fits_data, self.fits_header = self.frame.data, self.frame.header
        self.psf = self.frame.psf

        self.show_good_comparisons = self.CheckButton(text='Show stars with flux similar\nto the target (+/- %):',
                                                      initial=self.log.get_param('show_good_comparisons'),
                                                      command=self.update_window)
        self.show_good_comparisons_percent = self.DropDown(initial=self.log.get_param('show_good_comparisons_percent'),
                                                           options=[10, 20, 30, 40, 50], instance=float,
                                                           command=self.update_window, width=8)
        self.plate_solution = None
        ##############################################################################

        ##############################################################################
        # set up plot

        self.fits_figure = self.FitsWindow(fits_data=self.fits_data, fits_header=self.fits_header, input_name=self.frame.name,
                                           input_options=self.log.get_param('photometry_fov_options'),
                                           show_controls=True, show_axes=True,
                                           subplots_adjust=(0.07, 0.99, 0.05, 0.85))

        self.fits_figure.canvas.callbacks.connect('button_press_event', self.select_star)

        self.fits_figure.ax.add_patch(mpatches.Rectangle((self.visible_fov_x_min + 1, self.visible_fov_y_min + 1),
                                                         self.visible_fov_x_max - self.visible_fov_x_min - 2,
                                                         self.visible_fov_y_max - self.visible_fov_y_min - 2,
                                                         ec='r', fill=False, label='Available FOV'))
        ##############################################################################

        ##############################################################################
        # set up targets: [radio, aperture, x, y, circle, inner ring, outer ring, text, clear,
        #                  Gbp-Grp, total counts, max counts, max hwhm, warning, wx, wy]

        self.targets_indication = self.IntVar(0)
        rows = photometry.rows_from_log(self.log)

        self.targets = []
        for i in range(self.max_targets):
            color = 'r' if i == 0 else '#07fefc'
            self.targets.append([
                self.Radiobutton(text='      Target           ' if i == 0 else 'Comparison {0}     '.format(i),
                                 variable=self.targets_indication, value=i),
                self.Entry(value=rows[i]['aperture'], instance=float, command=self.update_window),
                self.Label(text=rows[i]['x'], instance=float),
                self.Label(text=rows[i]['y'], instance=float),
                mpatches.Circle((-1000, -1000), 1, ec=color, fill=False),
                mpatches.Circle((-1000, -1000), 1, ec=color, fill=False),
                mpatches.Circle((-1000, -1000), 1, ec=color, fill=False),
                self.fits_figure.ax.text(-1000, -1000, 'T' if i == 0 else 'C{0}'.format(i), color=color, fontsize=15, va='top'),
                self.Label(text='') if i == 0 else self.Button(text='CLEAR', command=self.clear_target(i)),
                self.Label(text='-', instance=str),
                self.Label(text=0.0, instance=float),
                self.Label(text=0.0, instance=float),
                self.Label(text=0.0, instance=float),
                self.Label(),
                0, 0,
            ])

        for target in self.targets:
            self.fits_figure.ax.add_patch(target[4])
            self.fits_figure.ax.add_patch(target[5])
            self.fits_figure.ax.add_patch(target[6])
        ##############################################################################

        ##############################################################################
        # set up good comparisons

        self.good_comps_boxes = []
        for comparison in range(100):
            good_comps_box = mpatches.Rectangle((-1000, -1000), 10 * self.psf, 10 * self.psf, ec='y', fill=False,
                                                label='Stars of similar flux to the target' if comparison == 0 else None)
            self.good_comps_boxes.append(good_comps_box)
            self.fits_figure.ax.add_patch(good_comps_box)

        self.fits_figure.ax.legend(loc=(0, 1.01))
        ##############################################################################

        ##############################################################################
        # other widgets

        photometry_folders = ['Load options from previous run'] + self.project.photometry_folders()
        self.photometry_folder_to_load = self.DropDown(initial='Load options from previous run',
                                                       instance=str,
                                                       options=photometry_folders,
                                                       width=40, command=self.load_options_from_previous_run)

        self.run_message = self.Label(text='')

        self.photometry_button = self.Button(text='RUN PHOTOMETRY', command=self.run_photometry, bg='green',
                                             highlightbackground='green')

        self.proceed_button = self.Button(text='PROCEED TO FITTING', command=self.proceed)
        if self.log.get_param('photometry_complete'):
            self.proceed_button.activate()
        else:
            self.proceed_button.disable()

        self.save_and_return_button = self.Button(text='SAVE OPTIONS & RETURN TO MAIN MENU',
                                                  command=self.save_and_return)
        ##############################################################################

        ##############################################################################
        # place widgets
        setup_list = [
            [[self.show_good_comparisons, 0],
             [self.show_good_comparisons_percent, 1],
             [self.Button(text='Check SIMBAD', command=self.openweb_simbad), 2],
             [self.Label(text="Remember, the best comparison stars need to be:\n"
                              "a) close to your target, b) of similar magnitude to the target,\n"
                              "c) of similar colour to the target, d) photometrically stable, i.e. "
                              "not variables!"), 3, 9, 2]
             ],
            [[self.Button(text='PLATE SOLVE IMAGE (if connected to the internet)',
                          command=self.plate_solve_first), 0, 3]],
            [[self.fits_figure, 0, 3, 24], ],
            [[self.Label(text='Aperture\nradius (>1.5)'), 4],
             [self.Label(text='Gbp-Grp'), 6], [self.Label(text='Total\ncounts'), 7],
             [self.Label(text='Max\ncounts'), 8], [self.Label(text='Max\nHWHM'), 9],
             ],
        ]

        for target in self.targets:
            setup_list.append([[target[0], 3], [target[1], 4], [target[8], 5], [target[9], 6], [target[10], 7],
                               [target[11], 8], [target[12], 9], [target[13], 11]])

        setup_list += [
            [[self.show_advanced_settings_button, 7, 4]],
            [],
            [[self.photometry_folder_to_load, 3, 8]],
            [[self.Button(text='RETURN TO MAIN MENU', command=self.close), 3, 2],
             [self.save_and_return_button, 5, 6]],
            [[self.photometry_button, 3, 2],
             [self.proceed_button, 5, 6]],
            [[self.run_message, 3, 8]],
            []
        ]

        self.setup_window(setup_list, entries_wd=5)
        ##############################################################################

        self.update_window(None)

    # widgets <-> hops.core rows

    def settings(self):
        return {
            'sky_inner_aperture': self.sky_inner_aperture.get(),
            'sky_outer_aperture': self.sky_outer_aperture.get(),
            'saturation': self.saturation.get(),
            'star_size_arcsec': self.star_size_arcsec.get(),
            'camera_gain': self.camera_gain.get(),
            'use_variable_aperture': self.use_variable_aperture.get(),
            'use_geometric_center': self.use_geometric_center.get(),
            'show_good_comparisons': self.show_good_comparisons.get(),
            'show_good_comparisons_percent': self.show_good_comparisons_percent.get(),
        }

    def rows(self):
        return [{'x': t[2].get(), 'y': t[3].get(), 'aperture': t[1].get(), 'flux': t[10].get(), 'peak': t[11].get(),
                 'max_hwhm': t[12].get(), 'wx': t[14], 'wy': t[15], 'colour': t[9].get(), 'warning': t[13].get()}
                for t in self.targets]

    def set_rows(self, rows):
        for target, row in zip(self.targets, rows):
            target[1].set(row['aperture'])
            target[2].set(row['x'])
            target[3].set(row['y'])
            target[9].set(row['colour'])
            target[10].set(row['flux'])
            target[11].set(row['peak'])
            target[12].set(row['max_hwhm'])
            target[13].set(row['warning'])
            target[14] = row['wx']
            target[15] = row['wy']

    # actions

    def select_star(self, event=None):

        if isinstance(event, matplotlib.backend_bases.MouseEvent):

            if event.inaxes is None or not event.dblclick:
                return None

            star = photometry.get_star(self.log, self.frame, event.xdata, event.ydata, self.saturation.get())

            if not star:
                self.showinfo('Star not acceptable.', 'Star could not be located or it includes pixels above the saturation limit.')
            elif (star['x'] < self.visible_fov_x_min or star['x'] > self.visible_fov_x_max
                  or star['y'] < self.visible_fov_y_min or star['y'] > self.visible_fov_y_max):
                self.showinfo('Star not acceptable.', 'Star moves outside the FOV later.')
            else:
                target = self.targets[self.targets_indication.get()]
                target[2].set(star['x'])
                target[3].set(star['y'])
                target[10].set(0)
                self.update_window(None)

    def load_options_from_previous_run(self):

        if self.photometry_folder_to_load.get() not in self.project.photometry_folders():
            return

        previous = photometry.load_previous(self.project, self.photometry_folder_to_load.get())
        settings = previous['settings']
        for widget, key in [[self.use_variable_aperture, 'use_variable_aperture'],
                            [self.use_geometric_center, 'use_geometric_center'],
                            [self.sky_inner_aperture, 'sky_inner_aperture'],
                            [self.sky_outer_aperture, 'sky_outer_aperture'],
                            [self.saturation, 'saturation'],
                            [self.show_good_comparisons, 'show_good_comparisons'],
                            [self.show_good_comparisons_percent, 'show_good_comparisons_percent']]:
            widget.set(settings[key])

        self.targets_indication.set(0)
        self.set_rows(previous['rows'])
        self.update_window(None)

    def update_window(self, event=None):

        result = photometry.update_rows(self.log, self.frame, self.rows(), self.settings(), self.plate_solution)
        self.set_rows(result['rows'])
        settings = self.settings()

        fov_options = self.fits_figure.get_fov_options()
        text_y_drift = [-1, 1][int(fov_options[3])]
        text_x_drift = [1, -1][int(fov_options[4])]

        for target, row in zip(self.targets, result['rows']):
            if 0 in [row['x'], row['y']]:
                target[1].disable()
                for circle in target[4:7]:
                    circle.set_center((-10000, -10000))
                target[7].set_x(-10000)
                target[7].set_y(-10000)
                continue

            target[1].activate()
            center = (row['wx'], row['wy']) if settings['use_geometric_center'] else (row['x'], row['y'])
            for circle in target[4:7]:
                circle.set_center(center)
            target[4].set_radius(row['aperture'])
            target[5].set_radius(row['aperture'] * settings['sky_inner_aperture'])
            target[5].set_linestyle(':')
            target[6].set_radius(row['aperture'] * settings['sky_outer_aperture'])
            target[6].set_linestyle(':')
            target[7].set_x(row['x'] + text_x_drift * row['aperture'])
            target[7].set_y(row['y'] + text_y_drift * row['aperture'])

        for num, box in enumerate(self.good_comps_boxes):
            if num < len(result['good_comparisons']):
                x, y = result['good_comparisons'][num]
                box.set_xy((x - 5 * self.psf, y - 5 * self.psf))
            else:
                box.set_xy((-1000, -1000))
            box.set_alpha(1 if settings['show_good_comparisons'] else 0)

        self.run_message.set(result['message'])
        if result['can_run']:
            self.photometry_button.activate()
            self.save_and_return_button.activate()
        else:
            self.photometry_button.disable()
            self.save_and_return_button.disable()

        # radio buttons up to the first empty slot
        empty = [i for i, row in enumerate(result['rows']) if 0 in [row['x'], row['y']]]
        first_empty = empty[0] if empty else self.max_targets - 1
        for i, target in enumerate(self.targets):
            target[0]['state'] = self.NORMAL if i <= first_empty else self.DISABLED

        self.fits_figure.draw()

    def clear_target(self, num):

        def clear_target_num():
            self.targets[num][2].set(0.0)
            self.targets[num][3].set(0.0)
            self.targets_indication.set(0)
            self.update_window(None)

        return clear_target_num

    def openweb_simbad(self):

        radec_string = self.log.get_param('target_ra_dec').replace('+', '%2B').replace(' ', '+')
        webbrowser.open("http://simbad.u-strasbg.fr/simbad/sim-coo?Coord={0}&CooFrame=ICRS&"
                        "CooEpoch=2000&CooEqui=2000&CooDefinedFrames=none&Radius=20&"
                        "Radius.unit=arcmin&submit=submit+query&CoordList=".format(radec_string), new=1)

    def plate_solve_first(self):

        if self.plate_solution is None:
            try:
                self.plate_solution = photometry.plate_solve(Job('plate solving'), self.project, self.frame,
                                                             self.star_size_arcsec.get())
            except ERRORS as e:
                self.showinfo('Plate solution failed', str(e))
                return

        target_x, target_y = self.plate_solution['target_x'], self.plate_solution['target_y']
        dd = len(self.fits_data[0]) * 0.1
        self.fits_figure.ax.add_patch(mpatches.Arrow(target_x - 3 * self.psf - dd, target_y - 3 * self.psf - dd,
                                                     dd, dd, width=dd / 3, color='r'))
        self.fits_figure.ax.text(target_x - 0.4 * dd, target_y - 0.5 * dd, self.log.get_param('target_name'),
                                 color='r', va='top', ha='left')
        self.update_window()

    def save_settings(self):
        result = photometry.update_rows(self.log, self.frame, self.rows(), self.settings(), self.plate_solution)
        photometry.save_settings(self.project, self.frame, self.settings(), result['rows'],
                                 self.fits_figure.get_fov_options())
        self.targets_indication.set(0)

    def save_and_return(self):
        self.save_settings()
        self.close()

    def run_photometry(self):
        self.save_settings()
        self.log.set_param('proceed', 'run_photometry')
        self.close()

    def proceed(self):
        self.log.set_param('proceed', True)
        self.close()


class PhotometryProgressWindow(MainWindow):
    """Live view of the photometry; the measurement and the saving are done by hops.core.photometry."""

    def __init__(self, log):

        MainWindow.__init__(self, log, name='HOPS - Photometry progress', position=2)

        self.project = Project(log)
        self.measurement = photometry.PhotometryRun(self.project)
        self.runner = None
        self.targets = self.measurement.targets

        # progress figure

        self.progress_figure = self.FigureWindow()

        self.axes_fits = []
        self.circles_fits = []
        self.images_fits = []
        self.star_plot_lim = 1.5 * np.max(self.measurement.targets_aperture)
        self.axes_lc = []
        self.images_text_lc = []
        self.images_gauss_lc = []
        self.images_aperture_lc = []

        hours = (self.measurement.results['jd'] - self.measurement.results['jd'][0]) * 24
        header = self.measurement.first_fits_header
        lim = self.measurement.star_image_lim

        xx = 1 / (self.targets + 2)
        gs = gridspec.GridSpec(self.targets, 4, self.progress_figure.figure, 0.01, xx, 0.99, 1 - xx, 1.0, 0.1)

        for target in range(self.targets):
            self.axes_fits.append(self.progress_figure.figure.add_subplot(gs[target, 0]))
            circle = mpatches.Circle((0, 0), self.measurement.targets_aperture[target], ec='r', fill=False)
            self.axes_fits[-1].add_patch(circle)
            self.circles_fits.append(circle)
            self.images_fits.append(self.axes_fits[-1].imshow(
                0 * self.measurement.first_fits_data[0: 2 * lim, 0: 2 * lim], origin='lower',
                extent=(- lim, lim, - lim, lim), cmap=Greys_r,
                vmin=header[log.mean_key] + log.frame_low_std * header[log.std_key],
                vmax=header[log.mean_key] + log.frame_upper_std * header[log.std_key]))
            self.axes_fits[-1].axis('off')
            self.axes_fits[-1].set_xlim(- self.star_plot_lim, self.star_plot_lim)
            self.axes_fits[-1].set_ylim(- self.star_plot_lim, self.star_plot_lim)

            self.axes_lc.append(self.progress_figure.figure.add_subplot(gs[target, 1:]))
            self.images_text_lc.append(self.axes_lc[-1].text(hours[-1] / 2, 1, ' ', fontsize=10, va='center', ha='center'))
            self.images_gauss_lc.append(self.axes_lc[-1].plot(hours, hours * np.nan, 'ro', ms=3, label='PSF'))
            self.images_aperture_lc.append(self.axes_lc[-1].plot(hours, hours * np.nan, 'ko', ms=3, label='Aperture'))
            self.axes_lc[-1].set_ylim(0.99, 1.01)
            self.axes_lc[-1].set_xlim(-0.1 / 60, hours[-1] + 0.1 / 60)
            self.axes_lc[-1].set_ylabel('T' if target == 0 else 'C{0}'.format(target), fontsize=12)
            if target != self.targets - 1:
                self.axes_lc[-1].tick_params(labelbottom=False, labelsize=8)
            else:
                self.axes_lc[-1].set_xlabel('Time (hours in observation)', fontsize=8)
                self.axes_lc[-1].tick_params(labelsize=8)

        self.axes_lc[0].legend(bbox_to_anchor=(1, 2.2), fontsize='x-small')

        if self.targets == 2:
            self.images_text_lc[1].set_text('Can\'t plot relative curve with only 1 active comparison')

        # progress bar and diagnostics

        self.progress_photometry = self.Progressbar(task="Photometry")
        self.progress_diagnostics = [self.Label(text='Target')]
        self.progress_active = [self.CheckButton(text='', initial=1)]
        for target in range(1, self.targets):
            self.progress_diagnostics.append(self.Label(text='Comparison {0}'.format(target)))
            self.progress_active.append(self.CheckButton(text='', initial=1, command=self.replot_results))

        # buttons

        self.stop_and_return_button = self.Button(text='STOP PHOTOMETRY', command=self.trigger_exit)
        self.save_button = self.Button(text='SAVE RESULTS', command=self.save)
        self.return_to_photomertry_button = self.Button(text='RETURN TO PHOTOMETRY MENU', command=self.close)
        self.proceed_button = self.Button(text='PROCEED TO FITTING MENU', command=self.proceed)

        structure = [
            [[self.Label(text=' '), 0], [self.Label(text='Active'), 1], [self.progress_figure, 2, 2, self.targets + 2]],
            [[self.progress_diagnostics[0], 0], [self.progress_active[0], 1]],
        ]
        for i in range(1, self.targets):
            structure.append([[self.progress_diagnostics[i], 0], [self.progress_active[i], 1]])
        structure.append([[self.Label(text='...'), 0]])
        structure.append([])
        structure.append([[self.progress_photometry, 2], [self.stop_and_return_button, 3]])
        structure.append([[self.save_button, 0, 2], [self.return_to_photomertry_button, 2], [self.proceed_button, 3]])
        structure.append([])

        self.set_close_button_function(self.trigger_exit)
        self.setup_window(structure, main_font='Courier')

        for checkbox in self.progress_active:
            checkbox.disable()
        self.stop_and_return_button.activate()
        self.save_button.disable()
        self.return_to_photomertry_button.disable()
        self.proceed_button.disable()

    def trigger_exit(self):
        if self.runner is not None and self.runner.thread.is_alive():
            self.runner.cancel()
        else:
            self.def_close()

    def run_photometry(self):

        self.log.set_param('proceed', 'return_to_photometry')

        def measure_and_save(job, run):
            run.run(job)
            return run.save()

        self.runner = TkJobRunner(self, 'photometry', measure_and_save, self.measurement,
                                  progressbars={'photometry': self.progress_photometry},
                                  on_finished=self.finished)
        self.runner.start()
        self.after(self.redraw_live, 500)

    def redraw_live(self):
        if self.runner.thread.is_alive():
            self.draw_live()
            self.after(self.redraw_live, 500)

    def draw_live(self):

        live = self.measurement.live_data()

        for target in range(self.targets):
            cutout = live['cutouts'][target]
            if cutout is not None:
                data = np.array(cutout['data'])
                h, w = data.shape
                self.images_fits[target].set_data(data)
                self.images_fits[target].set_clim(cutout['vmin'], cutout['vmax'])
                self.images_fits[target].set_extent((cutout['x0'], cutout['x0'] + w, cutout['y0'], cutout['y0'] + h))
                self.axes_fits[target].set_xlim(cutout['x'] - self.star_plot_lim, cutout['x'] + self.star_plot_lim)
                self.axes_fits[target].set_ylim(cutout['y'] - self.star_plot_lim, cutout['y'] + self.star_plot_lim)
                self.circles_fits[target].set_center((cutout['x'], cutout['y']))
                self.circles_fits[target].set_radius(cutout['r'])

            if live['fails'][target] > 0:
                name = 'Target' if target == 0 else 'Comparison {0}'.format(target)
                self.progress_diagnostics[target].set('{0}\nStar not found\nin {1} frame(s)'.format(name, int(live['fails'][target])))

        self.plot_curves()

    def plot_curves(self):

        gauss_curves, aperture_curves = self.measurement.curves()
        lims = [0.99, 1.01]
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            for target in range(self.targets):
                gauss_lc, aperture_lc = gauss_curves[target], aperture_curves[target]
                if np.isfinite(gauss_lc).any() or np.isfinite(aperture_lc).any():
                    lo, hi = np.nanmin([gauss_lc, aperture_lc]), np.nanmax([gauss_lc, aperture_lc])
                    if lo < lims[0]:
                        lims[0] = lims[0] - (int((lims[0] - lo) / 0.01) + 1) * 0.01
                    if hi > lims[1]:
                        lims[1] = lims[1] + (int((hi - lims[1]) / 0.01) + 1) * 0.01
                self.images_gauss_lc[target][0].set_ydata(gauss_lc)
                self.images_aperture_lc[target][0].set_ydata(aperture_lc)

        for ax_lc in self.axes_lc:
            ax_lc.set_ylim(lims[0], lims[1])

        self.progress_figure.draw()

    def finished(self, job):

        if job.status != 'done':
            if job.status == 'error':
                self.showinfo('Photometry failed', job.error)
            self.def_close()
            return

        self.draw_live()
        self.saved(job.result)
        if self.targets > 2:
            for checkbox in self.progress_active[1:]:
                checkbox.activate()

    def saved(self, folder):
        self.showinfo('Results saved successfully', 'Your results have been saved successfully in {0}'.format(folder))
        self.show()
        self.set_close_button_function(self.close)
        self.stop_and_return_button.disable()
        self.save_button.activate()
        self.return_to_photomertry_button.activate()
        self.proceed_button.activate()

    def replot_results(self):

        active = [True] + [bool(checkbox.get()) for checkbox in self.progress_active[1:]]

        if sum(active[1:]) == 1:
            for target in range(1, self.targets):
                if active[target]:
                    self.progress_active[target].disable()
                    self.images_text_lc[target].set_text('Can\'t plot relative curve with only 1 active comparison')
        else:
            for target in range(1, self.targets):
                self.progress_active[target].activate()
                self.images_text_lc[target].set_text(' ' if active[target] else 'Inactive')

        self.measurement.active = active
        self.plot_curves()

    def save(self):
        self.saved(self.measurement.save())

    def proceed(self):
        self.log.set_param('proceed', True)
        self.close()
