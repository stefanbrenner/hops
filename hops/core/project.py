
import os
import glob
import numpy as np
import hops.pylightcurve41 as plc

from hops.hops_tools.fits import get_fits_data_and_header


def photometry_order(path):
    try:
        return float(os.path.basename(path).split('_')[1])
    except (IndexError, ValueError):
        return 1


class Project:
    """A data directory and its HOPS settings.

    Every path is absolute, so hops.core never depends on the process working directory (the Tk
    application still changes into the data directory, the core does not need it). Paths stored in
    the log - like `light_curve_file` - stay relative to the directory, as before, so log.yaml files
    remain compatible between the Tk and the web interface.
    """

    def __init__(self, log, directory=None):
        self.log = log
        self.directory = os.path.abspath(directory or log.get_param('directory'))

    def __repr__(self):
        return 'Project({0})'.format(self.directory)

    # paths

    def path(self, *parts):
        return os.path.join(self.directory, *parts)

    def relative(self, path):
        return os.path.relpath(path, self.directory)

    @property
    def reduction_directory(self):
        return self.path(self.log.reduction_directory)

    def reduced(self, name):
        return os.path.join(self.reduction_directory, name)

    @property
    def all_frames_path(self):
        return self.path(self.log.all_frames)

    @property
    def all_stars_path(self):
        return self.path(self.log.all_stars)

    # files

    def find_fits(self, name_identifier):
        """Absolute paths of the FITS files whose name contains `name_identifier`, sorted by name
        (same matching rules as hops_tools.fits.find_fits_files)."""
        if len(name_identifier) == 0:
            return []
        names = (glob.glob(os.path.join(glob.escape(self.directory), '*{0}*.f*t*'.format(name_identifier))) +
                 glob.glob(os.path.join(glob.escape(self.directory), '*{0}*.F*T*'.format(name_identifier))))
        return sorted(np.unique(names).tolist())

    def photometry_folders(self):
        """PHOTOMETRY* folders, relative to the directory, in run order."""
        folders = [os.path.basename(ff) for ff in glob.glob(self.path('{0}*'.format(self.log.photometry_directory_base)))
                   if os.path.isdir(ff)]
        return sorted(folders, key=photometry_order)

    def light_curve_files(self):
        """PHOTOMETRY*/PHOTOMETRY_APERTURE.txt and _GAUSS.txt, relative to the directory, in run order."""
        files = []
        for folder in self.photometry_folders():
            for name in [self.log.light_curve_aperture_file, self.log.light_curve_gauss_file]:
                if os.path.isfile(self.path(folder, name)):
                    files.append(os.path.join(folder, name))
        return sorted(files, key=lambda f: (photometry_order(os.path.dirname(f)), f))

    # data products

    def load_all_frames(self):
        return plc.open_dict(self.all_frames_path)

    def save_all_frames(self, all_frames):
        plc.save_dict(all_frames, self.all_frames_path)

    def load_all_stars(self):
        return plc.open_dict(self.all_stars_path)

    def save_all_stars(self, all_stars):
        plc.save_dict(all_stars, self.all_stars_path)

    def open_reduced(self, name):
        return get_fits_data_and_header(self.reduced(name))

    def science_frames(self, all_frames=None, include_skipped=False):
        """Reduced frame names sorted by time (non-skipped unless include_skipped)."""
        log = self.log
        if all_frames is None:
            all_frames = self.load_all_frames()
        frames = []
        for name in all_frames:
            if include_skipped or not all_frames[name][log.skip_key]:
                frames.append([all_frames[name][log.time_key], name])
        frames.sort()
        return [ff[1] for ff in frames]

    def median_exposure_time(self, all_frames=None):
        log = self.log
        if all_frames is None:
            all_frames = self.load_all_frames()
        exposure = [all_frames[ff][log.get_param('exposure_time_key')]
                    for ff in all_frames if not all_frames[ff][log.skip_key]]
        return float(np.median(exposure))

    # settings

    def save_log(self):
        self.log.save_local_log()

    def export_log(self, folder):
        """Copy log.yaml into a result folder (absolute or relative to the directory)."""
        self.log.export_local_log(self.path(folder))
