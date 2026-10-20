
import time
import uuid
import datetime
import threading
import traceback

from .common import ERRORS, Cancelled, HOPSError, to_builtin


class Job:
    """A long-running task (reduction, alignment, photometry, fitting...) executed in a worker thread.

    The worker reports progress through `task` / `step` / `message` / `set_preview`, checks for
    cancellation with `check`, and can ask the user a yes/no question with `ask`.
    `exit` mirrors the attribute the old Tk windows exposed, so it can be passed as
    `progress_window` to hops_tools functions.
    """

    def __init__(self, kind):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.status = 'running'
        self.message = ''
        self.error = None
        self.result = None
        self.tasks = []
        self.preview = None
        self.preview_version = 0
        self.live = {}
        self.live_version = 0
        self.question = None
        self.started = time.time()
        self.finished = None
        self._cancel = threading.Event()
        self._answer_event = threading.Event()
        self._answer = None
        self._lock = threading.Lock()

    # cancellation

    @property
    def exit(self):
        return self._cancel.is_set()

    def cancel(self):
        self._cancel.set()
        self._answer_event.set()

    def check(self):
        if self._cancel.is_set():
            raise Cancelled()

    # progress

    def task(self, key, label, total):
        with self._lock:
            self.tasks = [t for t in self.tasks if t['key'] != key]
            self.tasks.append({'key': key, 'label': label, 'current': 0, 'total': max(int(total), 1),
                               'message': '', 'started': time.time(), 'skip': 0.0, 'eta': None, 'done': False})

    def _get_task(self, key):
        for t in self.tasks:
            if t['key'] == key:
                return t
        return None

    def step(self, key, n=1, message=None, skip=0.0):
        with self._lock:
            t = self._get_task(key)
            if t is None:
                return
            t['current'] = min(t['current'] + n, t['total'])
            t['skip'] += skip
            elapsed = time.time() - t['started'] - t['skip']
            if t['current'] > 0:
                t['eta'] = str(datetime.timedelta(seconds=int((t['total'] - t['current']) * elapsed / t['current'])))
            if message is not None:
                t['message'] = message
            if t['current'] >= t['total']:
                t['done'] = True

    def task_message(self, key, message):
        with self._lock:
            t = self._get_task(key)
            if t is not None:
                t['message'] = message

    def task_done(self, key, message=None):
        with self._lock:
            t = self._get_task(key)
            if t is not None:
                t['current'] = t['total']
                t['done'] = True
                if message is not None:
                    t['message'] = message

    def set_message(self, message):
        self.message = message

    def set_preview(self, data, name=None, overlays=None, header=None, factor=1):
        """Show an image (numpy array) with optional overlays in data coordinates. `factor` > 1 means the
        image was already block-averaged by that factor (overlays stay in original pixel coordinates)."""
        with self._lock:
            self.preview = {'data': data, 'name': name, 'overlays': overlays or [], 'header': header,
                            'factor': factor}
            self.preview_version += 1

    def set_live(self, **values):
        """Arbitrary JSON-serialisable live data (e.g. light curves while running)."""
        with self._lock:
            self.live.update(values)
            self.live_version += 1

    # user interaction

    def ask(self, question, options=('Yes', 'No')):
        """Block the worker until the user answers (returns the chosen option) or cancels."""
        self._answer_event.clear()
        self._answer = None
        self.question = {'id': uuid.uuid4().hex[:8], 'text': question, 'options': list(options)}
        self._answer_event.wait()
        self.question = None
        self.check()
        return self._answer

    def answer(self, value):
        self._answer = value
        self._answer_event.set()

    def to_dict(self):
        with self._lock:
            return to_builtin({
                'id': self.id,
                'kind': self.kind,
                'status': self.status,
                'message': self.message,
                'error': self.error,
                'result': self.result,
                'tasks': [{k: v for k, v in t.items() if k not in ('started', 'skip')} for t in self.tasks],
                'preview_version': self.preview_version if self.preview is not None else 0,
                'preview_overlays': self.preview['overlays'] if self.preview is not None else [],
                'preview_name': self.preview['name'] if self.preview is not None else None,
                'live_version': self.live_version,
                'question': self.question,
                'stopping': self._cancel.is_set() and self.status == 'running',
                'elapsed': (self.finished or time.time()) - self.started,
            })


class JobManager:
    """Runs one job at a time (all stages share the process working directory)."""

    # emcee's progress loop in pylightcurve recurses every 10 iterations and the reduction
    # raises the recursion limit, so worker threads get a large stack
    stack_size = 256 * 1024 * 1024

    def __init__(self):
        self.current = None
        self._lock = threading.Lock()

    def busy(self):
        return self.current is not None and self.current.status == 'running'

    def start(self, kind, function, *args, **kwargs):
        with self._lock:
            if self.busy():
                raise HOPSError('Another process ({0}) is still running.'.format(self.current.kind))
            job = Job(kind)
            self.current = job

        def runner():
            try:
                job.result = function(job, *args, **kwargs)
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
            finally:
                job.finished = time.time()
                job.question = None

        previous = threading.stack_size()
        threading.stack_size(self.stack_size)
        try:
            thread = threading.Thread(target=runner, name='hops-{0}'.format(kind), daemon=True)
            thread.start()
        finally:
            threading.stack_size(previous)

        return job

    def get(self, job_id=None):
        if self.current is None:
            return None
        if job_id is None or self.current.id == job_id:
            return self.current
        return None
