# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

HOPS (HOlomon Photometry Software) is a Python 3 Tkinter GUI application for exoplanet detection and characterization via photometric analysis of ground-based telescope data. Users are astronomical researchers and citizen scientists (Exoworlds Spies project). Current version: see `hops/__init__.py`.

## Installation & Running

```bash
# Install dependencies
pip install -r requirements.txt

# Run the application
python -m hops
# or
python -c "import hops; hops.run_app()"
```

Platform-specific installers (shell/batch scripts) handle post-install setup and desktop shortcuts. `setup.py` dynamically generates `MANIFEST.in` to bundle package data files.

## Architecture

### Processing Pipeline (6 Sequential Stages)

The app is a linear workflow — each stage writes results consumed by the next:

1. **Data & Target Selection** (`application_1_data_target.py`) — FITS file management, target config, filter mapping
2. **Image Reduction** (`application_2_reduction.py`) — bias/dark/flat correction, frame statistics
3. **Frame Inspection** (`application_3_inspection.py`) — visual review of reduced frames
4. **Astrometric Alignment** (`application_4_alignment.py`) — star detection, plate solving (WCS)
5. **Photometry** (`application_5_photometry.py`) — aperture photometry, PSF analysis, flux extraction
6. **Exoplanet Fitting** (`application_6_fitting.py`) — MCMC light curve fitting, result export

### Key Subpackages

**`hops/pylightcurve41/`** — Core scientific library:
- `models/exoplanet_lc.py` — Transit/eclipse light curve models
- `models/exoplanet.py` — Exoplanet catalog interface (exoclock)
- `analysis/` — Statistical/numerical methods (MCMC, distributions, integration)
- `images/find_stars.py` — Stellar centroid detection
- `plots/plots_fitting.py` — Visualization

**`hops/hops_tools/`** — Utilities:
- `fits.py` — FITS file I/O helpers
- `image_analysis.py` — Image statistics, PSF, plate solving
- `centroids_and_stars.py` — Low-level astrometric computations

### GUI Framework

- `application_windows.py` — Base classes `MainWindow` / `AddOnWindow` (custom Tkinter wrappers)
- `application.py` — `HOPS` class (entry point, extends `MainWindow`)
- `application_log.py` — `HOPSLog`: YAML-based config persistence (`log.yaml`, `log_profile.yaml`, `log_user.yaml`), version tracking, update checks
- Matplotlib figures are embedded inline in Tkinter windows

### Configuration

YAML files written to the working/output directory track state across stages. The `HOPSLog` class is the central config manager — all stages read/write through it.

## Key Dependencies

| Library | Role |
|---|---|
| `astropy` | FITS I/O, coordinates, time |
| `photutils` | Aperture photometry |
| `emcee` | MCMC parameter fitting |
| `twirl` (vendored in `hops/thirdparty`) | Astrometric plate solving |
| `exoclock` / `exotethys` | Exoplanet database / limb darkening models |
| `h5py` | HDF5 data storage |
| `scipy`, `numpy` | Numerical computing |
