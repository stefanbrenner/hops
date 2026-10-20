"""Engine tasks: hops.core functions wrapped for the engine process (see engine.py).

Each task receives the job and picklable arguments, and returns (public result, payload). The public
result goes to the browser, the payload stays in the server. Every task also returns the log
parameters as they are after the run, so the server can adopt what the engine changed (for
example `reduction_complete`).
"""

from hops.core import alignment, fitting, photometry, reduction


def _with_log(project, public, payload=None):
    return public, {'log_params': dict(project.log.params), 'data': payload}


def run_reduction(job, project):
    from hops.web.engine import SHARED_FLAGS
    show_all_frames = SHARED_FLAGS['show_all_frames']
    return _with_log(project, reduction.run_reduction(job, project, lambda: bool(show_all_frames.value)))


def run_alignment(job, project):
    return _with_log(project, alignment.run_alignment(job, project))


def plate_solve(job, project, star_size_arcsec):
    frame = photometry.FirstFrame(project)
    solution = photometry.plate_solve(job, project, frame, star_size_arcsec)
    return _with_log(project, solution, solution)


def run_photometry(job, project):
    holder = {}
    public = photometry.run_photometry(job, project, holder)
    run = holder['run']
    # the cutouts and results stay with the server for re-plotting and "save results again"
    return _with_log(project, public, run)


def fitting_test(job, project, settings, manual, auto):
    return _with_log(project, fitting.run_test(job, project, settings, manual, auto))


def run_fitting(job, project, settings, manual, auto):
    return _with_log(project, fitting.run_fitting(job, project, settings, manual, auto))
