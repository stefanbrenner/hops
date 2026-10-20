
import threading
import traceback

from hops.core.common import ERRORS, Cancelled, HOPSError
from hops.core.jobs import Job, JobManager


class TkJobRunner:
    """Runs a hops.core job function in a worker thread and mirrors it in a Tk window.

    The worker never touches Tk: the window polls the Job with `after` and updates its progress
    bars, preview and dialogs from the main thread.
    """

    def __init__(self, window, kind, function, *args,
                 progressbars=None, message_label=None, preview=None, on_finished=None, interval=100):
        self.window = window
        self.job = Job(kind)
        self.function = function
        self.args = args
        self.progressbars = progressbars or {}
        self.message_label = message_label
        self.preview = preview
        self.on_finished = on_finished
        self.interval = interval
        self.preview_version = 0
        self.live_version = 0
        self.thread = None

    def start(self):

        def runner():
            job = self.job
            try:
                job.result = self.function(job, *self.args)
                job.status = 'done'
            except Cancelled:
                job.status = 'cancelled'
            except HOPSError as e:
                job.status = 'error'
                job.error = str(e)
            except ERRORS as e:
                traceback.print_exc()
                job.status = 'error'
                job.error = '{0}: {1}'.format(type(e).__name__, e)

        previous = threading.stack_size()
        threading.stack_size(JobManager.stack_size)
        try:
            self.thread = threading.Thread(target=runner, daemon=True)
            self.thread.start()
        finally:
            threading.stack_size(previous)

        self.window.after(self._poll, self.interval)

    def cancel(self):
        self.job.cancel()

    def _poll(self):

        job = self.job

        for task in job.tasks:
            bar = self.progressbars.get(task['key'])
            if bar is not None:
                bar.set_state(task['current'], task['total'], task['eta'], task['message'])

        if self.message_label is not None:
            self.message_label.set(job.message or ' ')

        if self.preview is not None and job.preview is not None and job.preview_version != self.preview_version:
            self.preview_version = job.preview_version
            preview = job.preview
            self.preview(preview['data'], preview['name'], preview['overlays'], preview['header'])

        if job.question is not None:
            question = job.question
            answer = self.window.askyesno('HOPS - {0}'.format(job.kind), question['text'])
            job.answer(question['options'][0] if answer else question['options'][1])

        if self.thread.is_alive():
            self.window.after(self._poll, self.interval)
        elif self.on_finished is not None:
            self.on_finished(job)
