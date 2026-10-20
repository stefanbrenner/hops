
import numpy as np
import exoclock
import hops.pylightcurve41 as plc

from hops.pylightcurve41.errors import PyLCError



filter_map = {'Clear': 'clear',
              'Luminance': 'luminance',
              'U': 'JOHNSON_U',
              'B': 'JOHNSON_B',
              'V': 'JOHNSON_V',
              'R': 'COUSINS_R',
              'I': 'COUSINS_I',
              'H': '2mass_h',
              'J': '2mass_j',
              'K': '2mass_ks',
              'Astrodon ExoPlanet-BB': 'exoplanets_bb',
              'u\'': 'sdss_u',
              'g\'': 'sdss_g',
              'r\'': 'sdss_r',
              'z\'': 'sdss_z',
              'i\'': 'sdss_i',
              }

filter_translations = {
    'Clear': ['clear', 'None', 'Clear'],
    'Luminance': ['luminance', 'Luminance', 'lum', 'Lum', 'L'],
    'U': ['U', 'Uj', 'u'],
    'B': ['B', 'Bj', 'b'],
    'V': ['V', 'Vj', 'v'],
    'R': ['R', 'Rc', 'r'],
    'I': ['I', 'Ic', 'i'],
    'H': ['H', 'h'],
    'J': ['J', 'j'],
    'K': ['K', 'k', 'Ks', 'KS'],
    'Astrodon ExoPlanet-BB': ['exoplanets_bb', 'exoplanets'],
    'u\'': ['up', 'u\''],
    'g\'': ['gp', 'g\''],
    'r\'': ['rp', 'r\''],
    'z\'': ['zp', 'z\''],
    'i\'': ['ip', 'i\''],
}


# exoclock and pylightcurve errors derive from BaseException, so `except Exception` misses them
ERRORS = (Exception, exoclock.errors.ExoClockError, PyLCError)


class Cancelled(Exception):
    """Raised inside a job when the user stops it."""


class HOPSError(Exception):
    """An error with a message meant for the user."""


def to_builtin(value):
    """Convert numpy scalars/arrays (also nested) to plain Python for JSON / YAML."""
    if isinstance(value, dict):
        return {str(k): to_builtin(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_builtin(v) for v in value]
    if isinstance(value, np.ndarray):
        return to_builtin(value.tolist())
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        value = float(value)
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def image_statistics(data, header, log):
    try:
        return float(header[log.mean_key]), float(header[log.std_key])
    except (KeyError, TypeError, ValueError):
        mean, std = plc.mean_std_from_median_mad(data, samples=100000)
        return float(mean), float(std)


def downsample(data, max_size):
    """Block-average an image so that its longest side is <= max_size.

    Returns (image, factor); pixel i of the result covers [i*factor, (i+1)*factor) of the input.
    """
    factor = int(np.ceil(max(data.shape) / float(max_size))) if max_size else 1
    factor = max(factor, 1)
    if factor == 1:
        return np.asarray(data, dtype=np.float32), 1
    ny = data.shape[0] // factor
    nx = data.shape[1] // factor
    trimmed = data[:ny * factor, :nx * factor]
    binned = trimmed.reshape(ny, factor, nx, factor).mean(axis=(1, 3))
    return binned.astype(np.float32), factor
