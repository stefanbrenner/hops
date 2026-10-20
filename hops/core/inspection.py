
import numpy as np

from .common import to_builtin


def series(project):
    """Time series shown in the inspection plots (all frames, including excluded ones)."""

    log = project.log
    all_frames = project.load_all_frames()
    names = project.science_frames(all_frames, include_skipped=True)
    exposure_key = log.get_param('exposure_time_key')

    time_array, sky, psf, airmass, skip, jd = [], [], [], [], [], []

    for name in names:
        frame = all_frames[name]
        jd.append(frame[log.time_key])
        time_array.append(frame[log.time_key])
        sky.append(frame[log.mean_key] / frame[exposure_key])
        psf.append(frame[log.psf_key] * 2.355 / 2)
        airmass.append(frame.get(log.airmass_key, 1))
        skip.append(bool(frame[log.skip_key]))

    time_array = (np.array(time_array) - np.min(time_array)) * 24

    return to_builtin({
        'files': names,
        'jd': jd,
        'time': time_array,
        'sky': sky,
        'psf': psf,
        'airmass': airmass,
        'skip': skip,
    })


def save(project, skip):
    """skip: {frame name: bool}. Writes the flags back to all_frames.pickle."""

    log = project.log
    all_frames = project.load_all_frames()
    for name, value in skip.items():
        if name in all_frames:
            all_frames[name][log.skip_key] = bool(value)
    project.save_all_frames(all_frames)
    project.save_log()
