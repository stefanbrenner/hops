"""The HOPS engine: long computations run in a separate Python process.

The web server stays responsive, a crash inside a C extension cannot take it down, and stopping a
job is real - if the job does not react to the stop request in time, the engine process is
terminated and restarted. Progress, previews and questions travel back through a queue and are
mirrored by an `EngineJob` in the server process, which has the same interface as hops.core.jobs.Job.
"""

import os
import time
import queue
import atexit
import threading
import traceback
import multiprocessing as mp

from hops.core.common import ERRORS, Cancelled, HOPSError, downsample, to_builtin
from hops.core.jobs import Job, JobManager

# values the server can change while a task runs (multiprocessing values must be inherited, so they
# are created with the engine process and handed over at its start)
SHARED_FLAGS = {}

CANCEL_GRACE = 8.0      # seconds a job gets to stop by itself before the engine is restarted
UPDATE_INTERVAL = 0.15  # minimum time between two progress messages of a job
PREVIEW_SIZE = 1600     # longest side of the previews sent to the server


# engine process

class RemoteJob(Job):
    """The Job a task sees inside the engine process; every change is sent to the server."""

    def __init__(self, job_id, kind, events, cancel_event, answers):
        Job.__init__(self, kind)
        self.id = job_id
        self._events = events
        self._cancel_event = cancel_event
        self._answers = answers
        self._last_sent = 0

    @property
    def exit(self):
        return self._cancel_event.is_set()

    def check(self):
        if self._cancel_event.is_set():
            raise Cancelled()

    def _send_state(self, force=False):
        now = time.time()
        if force or now - self._last_sent > UPDATE_INTERVAL:
            self._last_sent = now
            self._events.put(('state', self.id, {'tasks': self.tasks, 'message': self.message}))

    def task(self, *args, **kwargs):
        Job.task(self, *args, **kwargs)
        self._send_state(force=True)

    def step(self, *args, **kwargs):
        Job.step(self, *args, **kwargs)
        self._send_state()

    def task_message(self, *args, **kwargs):
        Job.task_message(self, *args, **kwargs)
        self._send_state()

    def task_done(self, *args, **kwargs):
        Job.task_done(self, *args, **kwargs)
        self._send_state(force=True)

    def set_message(self, message):
        Job.set_message(self, message)
        self._send_state(force=True)

    def set_preview(self, data, name=None, overlays=None, header=None, factor=1):
        # full frames are large (a 6000 x 4000 frame is 200 MB): send a reduced copy to the server
        if factor == 1:
            data, factor = downsample(data, PREVIEW_SIZE)
        self._events.put(('preview', self.id, {'data': data, 'name': name, 'overlays': to_builtin(overlays or []),
                                               'header': header, 'factor': factor}))

    def set_live(self, **values):
        self._events.put(('live', self.id, values))

    def ask(self, question, options=('Yes', 'No')):
        self._send_state(force=True)
        self._events.put(('question', self.id, {'text': question, 'options': list(options)}))
        while True:
            self.check()
            try:
                return self._answers.get(timeout=0.2)
            except queue.Empty:
                pass


def engine_main(commands, events, cancel_event, answers, flags):
    """Loop of the engine process: runs one task at a time."""

    SHARED_FLAGS.update(flags)

    # end together with the server, also when it is killed (e.g. by the desktop app on quit)
    parent = os.getppid()

    def watch_parent():
        while True:
            time.sleep(1)
            if os.getppid() != parent:
                os._exit(0)

    threading.Thread(target=watch_parent, daemon=True).start()
    os.environ.setdefault('MPLBACKEND', 'Agg')
    import matplotlib
    matplotlib.use('Agg')
    threading.stack_size(JobManager.stack_size)

    def run(command):
        job_id, kind, function, args = command
        cancel_event.clear()
        while not answers.empty():
            answers.get_nowait()
        job = RemoteJob(job_id, kind, events, cancel_event, answers)
        try:
            public, payload = function(job, *args)
            events.put(('done', job_id, public, payload))
        except Cancelled:
            events.put(('cancelled', job_id))
        except HOPSError as e:
            events.put(('error', job_id, str(e)))
        except ERRORS as e:
            traceback.print_exc()
            events.put(('error', job_id, '{0}: {1}'.format(type(e).__name__, e)))

    events.put(('ready',))
    while True:
        command = commands.get()
        if command is None:
            return
        # a large thread stack: pylightcurve's MCMC loop is recursive
        worker = threading.Thread(target=run, args=(command,))
        worker.start()
        worker.join()


# server side

class EngineJob(Job):
    """Server-side mirror of a job running in the engine."""

    def __init__(self, engine, kind):
        Job.__init__(self, kind)
        self.engine = engine
        self.payload = None
        self.cancel_requested = None

    def cancel(self):
        Job.cancel(self)
        self.engine.cancel(self)

    def answer(self, value):
        self.question = None
        self.engine.answers.put(value)


class Engine:

    def __init__(self):
        self.context = mp.get_context('spawn')
        self.process = None
        self.current = None
        self.ready = threading.Event()
        self._lock = threading.Lock()
        self._listener = None
        self._atexit = False
        self.on_finished = []

    # process management

    def start(self):
        self.commands = self.context.Queue()
        self.events = self.context.Queue()
        self.answers = self.context.Queue()
        self.cancel_event = self.context.Event()
        self.flags = {'show_all_frames': self.context.Value('b', 0)}
        self.ready.clear()
        # not a daemon: the engine starts worker processes itself (parallel reduction), which daemons may
        # not. It still ends with the server: stop() at exit, and its own watch on the parent process.
        self.process = self.context.Process(target=engine_main, name='hops-engine', daemon=False,
                                            args=(self.commands, self.events, self.cancel_event, self.answers,
                                                  self.flags))
        self.process.start()
        self._listener = threading.Thread(target=self._listen, args=(self.events, self.process), daemon=True)
        self._listener.start()
        if not self._atexit:
            atexit.register(self.stop)
            self._atexit = True

    def stop(self):
        if self.process is not None and self.process.is_alive():
            self.cancel_event.set()
            self.commands.put(None)
            self.process.join(2)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(2)

    def restart(self):
        if self.process is not None and self.process.is_alive():
            self.process.terminate()
            self.process.join(5)
        self.start()

    def set_flag(self, name, value):
        if self.process is not None:
            self.flags[name].value = int(bool(value))

    # jobs

    def busy(self):
        return self.current is not None and self.current.status == 'running'

    def submit(self, kind, function, *args):
        """Run function(job, *args) in the engine. It must return (public result, payload): the public
        result is sent to the browser, the payload stays in the server (e.g. photometry results)."""
        with self._lock:
            if self.busy():
                raise HOPSError('Another process ({0}) is still running.'.format(self.current.kind))
            if self.process is None or not self.process.is_alive():
                self.start()
            job = EngineJob(self, kind)
            self.current = job
            self.commands.put((job.id, kind, function, args))
            return job

    def cancel(self, job):
        if job is not self.current or job.status != 'running':
            return
        self.cancel_event.set()
        job.cancel_requested = time.time()

        def watchdog():
            time.sleep(CANCEL_GRACE)
            if job.status == 'running':
                job.status = 'cancelled'
                job.message = ''
                job.finished = time.time()
                self.restart()
                self._finished(job)

        threading.Thread(target=watchdog, daemon=True).start()

    def get(self, job_id=None):
        if self.current is None:
            return None
        if job_id is None or self.current.id == job_id:
            return self.current
        return None

    def _finished(self, job):
        for callback in self.on_finished:
            try:
                callback(job)
            except Exception:
                traceback.print_exc()

    def _listen(self, events, process):
        while True:
            try:
                event = events.get(timeout=0.5)
            except queue.Empty:
                if not process.is_alive():
                    job = self.current
                    if job is not None and job.status == 'running' and self.process is process:
                        job.status = 'error'
                        job.error = 'The HOPS engine stopped unexpectedly (exit code {0}).'.format(process.exitcode)
                        job.finished = time.time()
                        self._finished(job)
                        self.start()
                    return
                continue
            except (EOFError, OSError):
                return

            if event[0] == 'ready':
                self.ready.set()
                continue

            job = self.current
            if job is None or event[1] != job.id:
                continue
            kind = event[0]
            if kind == 'state':
                with job._lock:
                    job.tasks = event[2]['tasks']
                    job.message = event[2]['message']
            elif kind == 'preview':
                p = event[2]
                job.set_preview(p['data'], p['name'], p['overlays'], p['header'], p['factor'])
            elif kind == 'live':
                job.set_live(**event[2])
            elif kind == 'question':
                job.question = {'id': str(time.time()), 'text': event[2]['text'], 'options': event[2]['options']}
            elif kind in ['done', 'error', 'cancelled']:
                if kind == 'done':
                    job.result, job.payload = event[2], event[3]
                    for task in job.tasks:
                        task['done'] = True
                        task['current'] = task['total']
                    job.status = 'done'
                elif kind == 'error':
                    job.error = event[2]
                    job.status = 'error'
                else:
                    job.status = 'cancelled'
                job.question = None
                job.message = ''
                job.finished = time.time()
                self._finished(job)
