
from hops.hops_tools.fits import find_fits_files, get_fits_data_and_header
from hops.application_windows import MainWindow
from hops.application_jobs import TkJobRunner
from hops.core import reduction
from hops.core.project import Project


class ReductionWindow(MainWindow):
    """Progress window of the reduction; the work is done by hops.core.reduction."""

    def __init__(self, log):

        MainWindow.__init__(self, log, name='HOPS - Reduction', position=2)

        self.project = Project(log)
        self.runner = None

        files = {key: find_fits_files(self.log.get_param('{0}_files'.format(key)))
                 for key in ['bias', 'dark', 'darkf', 'flat']}
        science_files = find_fits_files(self.log.get_param('observation_files'))

        test_fits_data, test_fits_header = get_fits_data_and_header(science_files[0])

        self.progress_figure = self.FitsWindow(fits_data=test_fits_data, fits_header=test_fits_header,
                                               input_name=science_files[0])
        self.progressbars = {
            'bias': self.Progressbar(task="Loading bias frames"),
            'dark': self.Progressbar(task="Loading dark frames"),
            'darkf': self.Progressbar(task="Loading dark-flat frames"),
            'flat': self.Progressbar(task="Loading flat frames"),
            'science': self.Progressbar(task="Reducing science frames and calculating statistics"),
        }
        # read by the worker thread, so it is a plain Python value, not a Tk variable
        self.show_all_frames = [False]
        self.progress_science_loop = self.CheckButton(text='Show all frames', initial=0,
                                                      command=self.update_show_all_frames)

        setup_window = [
            [[self.progress_figure, 0, 2]]
        ]

        for key in ['bias', 'dark', 'darkf', 'flat']:
            if len(files[key]) > 0:
                setup_window.append([[self.progressbars[key], 0, 2]])

        setup_window += [
            [[self.progressbars['science'], 0], [self.progress_science_loop, 1]],
            [[self.Button(text='STOP REDUCTION & RETURN TO MAIN MENU', command=self.trigger_exit), 0, 2]],
            []
        ]

        self.setup_window(setup_window)

        self.set_close_button_function(self.trigger_exit)

    def update_show_all_frames(self):
        self.show_all_frames[0] = bool(self.progress_science_loop.get())

    def trigger_exit(self):
        if self.runner is not None:
            self.runner.cancel()
        else:
            self.close()

    def run_reduction(self):

        if self.log.get_param('reduction_complete'):
            if self.askyesno('Overwrite files', 'Reduction has been completed, do you want to run again?'):
                self.log.set_param('reduction_complete', False)
                self.log.save_local_log()
            else:
                self.log.set_param('proceed', True)
                self.close()
                return

        show_all_frames = self.show_all_frames
        self.runner = TkJobRunner(self, 'reduction', reduction.run_reduction, self.project,
                                  lambda: show_all_frames[0],
                                  progressbars=self.progressbars, preview=self.show_preview,
                                  on_finished=self.finished)
        self.runner.start()

    def show_preview(self, data, name, overlays, header):
        self.progress_figure.load_fits(data, header, name)
        self.progress_figure.draw()

    def finished(self, job):
        if job.status == 'done':
            self.log.set_param('proceed', True)
        elif job.status == 'error':
            self.showinfo('Reduction failed', job.error)
        self.close()
