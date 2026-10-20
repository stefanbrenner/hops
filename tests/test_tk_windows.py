"""The Tkinter windows on the synthetic dataset: they now run hops.core, so they must reproduce the
golden results too. The windows stay hidden and the dialogs answer themselves.

Needs a display; skipped where Tk cannot start.
"""

import os
import numpy as np
import pytest

import hops_testlib as t


@pytest.fixture(scope='module')
def tk_run(tmp_path_factory):

    if os.environ.get('HOPS_SKIP_TK') or (os.name == 'posix' and os.uname().sysname == 'Linux'
                                          and not os.environ.get('DISPLAY')):
        pytest.skip('no display for Tk')

    from hops import application_windows
    from hops.application_log import HOPSLog

    patches = {
        'show': lambda self: self.reposition(),
        'showinfo': lambda self, *a, **k: None,
        'askyesno': lambda self, *a, **k: True,
    }
    originals = {name: getattr(application_windows.HOPSWindow, name) for name in patches}
    for name, function in patches.items():
        setattr(application_windows.HOPSWindow, name, function)

    from hops.application_2_reduction import ReductionWindow
    from hops.application_4_alignment import AlignmentWindow
    from hops.application_5_photometry import PhotometryWindow, PhotometryProgressWindow
    from hops.application_6_fitting import FittingWindow

    directory = str(tmp_path_factory.mktemp('synthetic_tk'))
    t.generate(directory)
    cwd = os.getcwd()

    try:
        log = HOPSLog()
        t.configure(log, directory)

        window = ReductionWindow(log)
        window.run(f_after=window.run_reduction)

        window = AlignmentWindow(log)
        window.run(f_after=window.run_alignment)

        positions, _ = t.star_positions(log, directory)
        window = PhotometryWindow(log)
        for i, (x, y) in enumerate(positions):
            window.targets_indication.set(i)
            window.targets[i][1].set(0)
            window.targets[i][2].set(x)
            window.targets[i][3].set(y)
            window.targets[i][10].set(0)
        window.update_window()
        window.save_settings()
        window.def_close()

        class Progress(PhotometryProgressWindow):
            def saved(self, folder):
                PhotometryProgressWindow.saved(self, folder)
                self.def_close()

        window = Progress(log)
        window.run(f_after=window.run_photometry)

        window = FittingWindow(log)
        window.light_curve_file.set(t.FITTING_SETTINGS['light_curve_file'])
        window.manual_planet.set(True)
        for key, value in t.manual_planet().items():
            getattr(window, key).set(value)
        window.update_lc()
        np.random.seed(0)
        window.run_test()
        test_title = window.parts['title1'].get_text()
        window.def_close()

        yield t.collect(log, directory=directory), test_title

    finally:
        for name, function in originals.items():
            setattr(application_windows.HOPSWindow, name, function)
        os.chdir(cwd)


def test_tk_reduction_and_alignment(tk_run, golden):
    result, _ = tk_run
    assert [f['data_sum'] for f in result['frames']] == [f['data_sum'] for f in golden['frames']]
    for key in ['mean', 'std', 'psf', 'align_x0', 'align_y0', 'align_u0']:
        assert np.allclose([f[key] for f in result['frames']], [f[key] for f in golden['frames']], rtol=1e-9), key


def test_tk_photometry(tk_run, golden):
    result, _ = tk_run
    assert result['positions'] == pytest.approx(golden['positions'], rel=1e-9)
    for curve in ['aperture', 'gauss']:
        assert np.allclose(result[curve]['flux'], golden[curve]['flux'], rtol=1e-9), curve
    assert result['photometry_files'] == golden['photometry_files']


def test_tk_fitting_window(tk_run):
    _, title = tk_run
    assert t.PLANET['planet'] in title
