"""Scientific regression tests.

hops.core (the code behind the web interface and the desktop app) must reproduce what the original
Tk pipeline computed for the synthetic dataset (tests/golden/synthetic.json, see make_golden_tk.py),
and the injected transit must be recovered.

    .venv/bin/python -m pytest tests -v
"""

import numpy as np
import pytest

import hops_testlib as t


def close(a, b, rel=1e-9, abs_=1e-9):
    return np.allclose(np.asarray(a, dtype=float), np.asarray(b, dtype=float), rtol=rel, atol=abs_, equal_nan=True)


# reduction

def test_same_frames(core_run, golden):
    _, result = core_run
    assert [f['name'] for f in result['frames']] == [f['name'] for f in golden['frames']]


def test_reduced_images_identical(core_run, golden):
    _, result = core_run
    assert [f['data_sum'] for f in result['frames']] == [f['data_sum'] for f in golden['frames']]


@pytest.mark.parametrize('key', ['mean', 'std', 'psf', 'jd', 'airmass'])
def test_frame_statistics(core_run, golden, key):
    _, result = core_run
    assert close([f[key] for f in result['frames']], [f[key] for f in golden['frames']], rel=1e-9)


def test_skipped_frames(core_run, golden):
    _, result = core_run
    assert [f['skip'] for f in result['frames']] == [f['skip'] for f in golden['frames']]


# alignment

@pytest.mark.parametrize('key', ['align_x0', 'align_y0', 'align_u0'])
def test_alignment(core_run, golden, key):
    _, result = core_run
    assert close([f[key] for f in result['frames']], [f[key] for f in golden['frames']], rel=1e-9, abs_=1e-9)


def test_stars_and_fov(core_run, golden):
    _, result = core_run
    assert result['stars'] == golden['stars']
    assert result['stars_in_fov'] == golden['stars_in_fov']
    assert close(result['fov'], golden['fov'])


# photometry

def test_selected_stars(core_run, golden):
    _, result = core_run
    for key, value in golden['positions'].items():
        assert result['positions'][key] == pytest.approx(value, rel=1e-9, abs=1e-9), key


@pytest.mark.parametrize('curve', ['aperture', 'gauss'])
def test_light_curves(core_run, golden, curve):
    _, result = core_run
    for key in ['jd', 'flux', 'flux_unc']:
        assert close(result[curve][key], golden[curve][key], rel=1e-9), key


def test_photometry_files(core_run, golden):
    _, result = core_run
    assert result['photometry_files'] == golden['photometry_files']


# fitting

def test_fitting_curve_fit(core_run, golden):
    _, result = core_run
    for key, value in golden['fitting_test'].items():
        assert result['fitting_test'][key] == pytest.approx(value, rel=1e-7), key


def test_fitting_mcmc(core_run, golden):
    """Same seed and same algorithm: the MCMC must agree well within its own uncertainties."""
    _, result = core_run
    mine, ref = result['fitting_mcmc'], golden['fitting_mcmc']
    assert abs(mine['mid_time'] - ref['mid_time']) < 0.1 * ref['mid_time_error']
    assert abs(mine['rp_over_rs'] - ref['rp_over_rs']) < 0.1 * ref['rp_over_rs_error']


# physics: the injected transit is recovered

def test_injected_transit_recovered(core_run):
    _, result = core_run
    fit = result['fitting_mcmc']
    assert abs(fit['mid_time'] - t.injected_mid_time()) < 3 * fit['mid_time_error']
    assert abs(fit['rp_over_rs'] - t.PLANET['rp_over_rs']) < 3 * fit['rp_over_rs_error']


def test_light_curve_noise(core_run):
    """The relative photometry should be close to the photon-noise level of the synthetic stars."""
    _, result = core_run
    assert result['fitting_mcmc']['res_std'] < 0.002
