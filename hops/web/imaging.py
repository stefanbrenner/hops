
import json
import numpy as np

from fastapi import Response

from hops.core.common import downsample, image_statistics


def image_response(data, header, log, name=None, max_size=1600, extra=None, region=None, prebinned=1):
    """Send an image as raw little-endian float32 with its metadata in the X-Image-Info header.

    The browser renders it on a canvas, so contrast/flip/zoom changes need no round trip.
    Without `region` the whole image is sent, block-averaged to at most `max_size` pixels; with
    `region` = (x0, x1, y0, y1) only that part is sent, so a zoomed-in view gets full resolution.

    Coordinates: pixel (i, j) of the original covers data coordinates [j, j+1] x [i, i+1] (as with
    matplotlib imshow and extent=(0, width, 0, height) in the Tk version). A pixel (r, c) of the
    sent image covers x from x_offset + c * factor, y from y_offset + r * factor.
    """
    data = np.asarray(data, dtype=float)
    # statistics of the whole frame, so the contrast does not jump between overview and region
    mean, std = image_statistics(data, header or {}, log)
    # an already reduced preview (prebinned > 1) is addressed in original pixel coordinates
    height, width = data.shape

    x_offset = y_offset = 0
    part = data
    if region is not None:
        x0, x1, y0, y1 = [value / prebinned for value in region]
        x_offset = int(np.clip(np.floor(min(x0, x1)), 0, width - 1))
        y_offset = int(np.clip(np.floor(min(y0, y1)), 0, height - 1))
        x_end = int(np.clip(np.ceil(max(x0, x1)), x_offset + 1, width))
        y_end = int(np.clip(np.ceil(max(y0, y1)), y_offset + 1, height))
        part = data[y_offset:y_end, x_offset:x_end]

    small, factor = downsample(part, max_size)
    info = {
        'width': int(small.shape[1]),
        'height': int(small.shape[0]),
        'factor': factor * prebinned,
        'x_offset': x_offset * prebinned,
        'y_offset': y_offset * prebinned,
        'orig_width': int(width * prebinned),
        'orig_height': int(height * prebinned),
        # no full resolution behind a reduced preview, so zooming in cannot load more detail
        'prebinned': prebinned > 1,
        'mean': mean,
        'std': std,
        'min': float(np.nanmin(data)),
        'max': float(np.nanmax(data)),
        'name': name,
        'low_std': log.frame_low_std,
        'upper_std': log.frame_upper_std,
    }
    if extra:
        info.update(extra)
    return Response(content=small.astype('<f4').tobytes(), media_type='application/octet-stream',
                    headers={'X-Image-Info': json.dumps(info), 'Cache-Control': 'no-store'})


def region_from_query(x0=None, x1=None, y0=None, y1=None):
    if None in (x0, x1, y0, y1):
        return None
    return float(x0), float(x1), float(y0), float(y1)
