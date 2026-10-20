"""Header handling of the Data & target step and the observing planner (fast, no pipeline run)."""

import os

import pytest

import hops_testlib as t


@pytest.fixture(scope='module')
def log(synthetic_dir):
    from hops.application_log import HOPSLog
    cwd = os.getcwd()
    log = HOPSLog()
    t.configure(log, synthetic_dir)
    yield log
    os.chdir(cwd)


@pytest.fixture
def draft(log):
    from hops.core import data_target
    values = data_target.form_values(log)
    values['observation_time_key'] = 'TIME-OBS'
    return values


def test_check_reads_the_header(log, draft):
    from hops.core import data_target
    from hops.core.project import Project
    check = data_target.check(Project(log), data_target.ScienceCache(), draft)
    assert check['science_files'] == t.N_FRAMES
    assert check['counts'] == {'bias_files': 5, 'dark_files': 5, 'darkf_files': 0, 'flat_files': 5}
    # DATE-OBS contains the time, so the time key follows the date key (as in the Tk window)
    assert check['time_key_locked'] and check['observation_time_key'] == 'DATE-OBS'
    assert check['filter'] == 'R'
    assert check['auto_target_ra_dec'].startswith('11:00:40')
    assert check['auto_location'].startswith('+48:05:17')
    assert check['image_shape'] == list(t.SHAPE)
    assert check['can_save']


def test_check_rejects_bad_values(log, draft):
    from hops.core import data_target
    from hops.core.project import Project
    draft.update({'exposure_time_key': 'NOPE', 'filter': 'xyz', 'target_ra_dec': 'nonsense', 'observation_files': 'missing'})
    check = data_target.check(Project(log), data_target.ScienceCache(), draft)
    assert not check['status']['exposure_time_key']
    assert not check['status']['target']
    assert not check['status']['observation_files']
    assert not check['can_save']


@pytest.mark.parametrize('value, expected', [('Rc', 'R'), ('lum', 'Luminance'), ('gp', "g'"), ('Clear', 'Clear')])
def test_filter_translations(log, value, expected):
    from hops.core import data_target
    assert data_target.normalise_filter(value, {}, log) == (expected, True)


def test_planner_plot():
    from hops.core import planner
    values = {'observatory': 'Test', 'latitude': t.SITE_LAT, 'longitude': t.SITE_LONG, 'timezone': '1',
              'year_month': '2026 03', 'name': 'Synthetic-1', 'ra': t.TARGET_RA, 'dec': t.TARGET_DEC}
    values.update({'horizon_{0}'.format(d): '10' for d, _ in planner.HORIZON_DIRECTIONS})
    svg = planner.plot(values, 'svg')
    assert svg.startswith(b'<?xml') and b'<svg' in svg[:500]


@pytest.mark.parametrize('ra, dec, ym, ok', [
    (t.TARGET_RA, t.TARGET_DEC, '2026 03', (True, True)),
    ('not an angle', t.TARGET_DEC, '2026 3', (False, False)),
])
def test_planner_validation(ra, dec, ym, ok):
    from hops.core import planner
    assert (planner.test_coordinates(ra, dec), planner.test_date(ym)) == ok
