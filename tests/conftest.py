import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

# package defaults only: the tests must not read or write the user's ~/hops_log_*.yaml
os.environ['HOME'] = tempfile.mkdtemp(prefix='hops_home_')
os.environ['MPLBACKEND'] = 'Agg'

import numpy as np
import pytest

import hops_testlib as t


class AutoJob:
    """A hops.core Job for tests: runs in the calling thread and answers questions with 'Yes'."""

    def __new__(cls):
        from hops.core.jobs import Job

        class _AutoJob(Job):
            def ask(self, question, options=('Yes', 'No')):
                return 'Yes'

        return _AutoJob('test')


def run_core_pipeline(directory):
    """Reduction → alignment → photometry → fitting with hops.core, on the synthetic dataset."""

    from hops.application_log import HOPSLog
    from hops.core import alignment, fitting, photometry, reduction
    from hops.core.project import Project

    log = HOPSLog()
    t.configure(log, directory)
    project = Project(log)
    # hops.core must not depend on the working directory
    os.chdir(tempfile.mkdtemp(prefix='hops_elsewhere_'))

    reduction.run_reduction(AutoJob(), project, lambda: False)
    alignment.run_alignment(AutoJob(), project)

    positions, _ = t.star_positions(log, directory)
    frame = photometry.FirstFrame(project)
    settings = {key: log.get_param(key) for key in photometry.SETTINGS}
    rows = photometry.rows_from_log(log)
    for i, (x, y) in enumerate(positions):
        rows[i].update({'x': x, 'y': y, 'aperture': 0, 'flux': 0})
    checked = photometry.update_rows(log, frame, rows, settings)
    assert checked['can_run'], checked['message']
    photometry.save_settings(project, frame, settings, checked['rows'], None)
    photometry.run_photometry(AutoJob(), project, {})

    settings = dict(t.FITTING_SETTINGS)
    manual = t.manual_planet()
    planet_values = fitting._planet_values(settings, manual, manual)
    exposure_time = project.median_exposure_time()
    np.random.seed(0)
    _, fit_test = fitting.call_plc_fitting(project, settings, planet_values, exposure_time, 'curve_fit')
    np.random.seed(0)
    folder = os.path.join(directory, 'core_fitting')
    os.mkdir(folder)
    _, fit_mcmc = fitting.call_plc_fitting(project, settings, planet_values, exposure_time, 'emcee', folder)

    return log, t.collect(log, fit_test, fit_mcmc, directory)


@pytest.fixture(scope='session')
def synthetic_dir(tmp_path_factory):
    directory = str(tmp_path_factory.mktemp('synthetic'))
    t.generate(directory)
    return directory


@pytest.fixture(scope='session')
def core_run(synthetic_dir):
    cwd = os.getcwd()
    try:
        log, result = run_core_pipeline(synthetic_dir)
        yield log, result
    finally:
        os.chdir(cwd)


@pytest.fixture(scope='session')
def golden():
    return t.load_golden('synthetic.json')
