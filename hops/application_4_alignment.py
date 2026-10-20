
from hops.application_windows import MainWindow
from hops.application_jobs import TkJobRunner
from hops.core import alignment
from hops.core.project import Project


class AlignmentWindow(MainWindow):
    """Progress window of the alignment; the work is done by hops.core.alignment."""

    def __init__(self, log):

        MainWindow.__init__(self, log, name='HOPS - Alignment', position=2)

        self.project = Project(log)
        self.runner = None

        first = self.project.science_frames(include_skipped=True)[0]
        fits_data, fits_header = self.project.open_reduced(first)

        self.progress_figure = self.FitsWindow(fits_data=fits_data, fits_header=fits_header, input_name=first)
        self.progress_all_stars = self.Label(text='')
        self.progress_alignment = self.Progressbar(task="Aligning frames")
        self.progress_half_frame = self.CheckButton(text='Show smaller frame', initial=0)

        self.setup_window([
            [[self.progress_figure, 0, 2]],
            [[self.progress_all_stars, 0]],
            [[self.progress_alignment, 0], [self.progress_half_frame, 1]],
            [[self.Button(text='STOP ALIGNMENT & RETURN TO MAIN MENU', command=self.trigger_exit), 0, 2]],
            []
        ])

        self.set_close_button_function(self.trigger_exit)

    def trigger_exit(self):
        if self.runner is not None:
            self.runner.cancel()
        else:
            self.def_close()

    def run_alignment(self):

        self.progress_figure.adjust_size()

        if self.log.get_param('alignment_complete'):
            if self.askyesno('Overwrite files', 'Alignment has been completed, do you want to run again?'):
                self.log.set_param('alignment_complete', False)
                self.log.save_local_log()
            else:
                self.log.set_param('proceed', True)
                self.def_close()
                return

        self.runner = TkJobRunner(self, 'alignment', alignment.run_alignment, self.project,
                                  progressbars={'alignment': self.progress_alignment},
                                  message_label=self.progress_all_stars,
                                  preview=self.show_preview, on_finished=self.finished)
        self.runner.start()

    def show_preview(self, data, name, overlays, header):
        self.progress_figure.load_fits(data, header, name, draw=False, show_half=self.progress_half_frame.get())
        self.progress_figure.draw_overlays(overlays)
        self.progress_figure.draw()

    def finished(self, job):
        if job.status == 'done':
            self.log.set_param('proceed', True)
        elif job.status == 'error':
            self.showinfo('Alignment failed', job.error)
        self.def_close()
