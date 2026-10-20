
import time
from hops.thirdparty import twirl
import numpy as np

from astropy.io import fits as pf

from hops.hops_tools.image_analysis import image_find_stars, cartesian_to_polar
from hops.hops_tools.centroids_and_stars import _find_centroids

from .common import HOPSError


def _write_alignment(project, name, x0, y0, u0):
    log = project.log
    fits = pf.open(project.reduced(name), memmap=False, mode='update')
    fits[1].header.set(log.align_x0_key, x0)
    fits[1].header.set(log.align_y0_key, y0)
    fits[1].header.set(log.align_u0_key, u0)
    fits.flush()
    fits.close()


def run_alignment(job, project):
    """Port of AlignmentWindow: finds the reference star and rotation of every frame."""

    log = project.log
    shift_tolerance_p = log.get_param('shift_tolerance_p')
    rotation_tolerance = log.get_param('rotation_tolerance')
    min_calibration_stars_number = int(log.get_param('min_calibration_stars_number'))
    centroids_snr = log.get_param('centroids_snr')
    stars_snr = log.get_param('stars_snr')
    faint_target_mode = log.get_param('faint_target_mode')
    moving_target_mode = log.get_param('moving_target_mode')
    rotating_field_mode = log.get_param('rotating_field_mode')

    log.set_param('alignment_complete', False)
    project.save_log()

    all_frames = project.load_all_frames()
    science_files = []
    for science_file in all_frames:
        if not all_frames[science_file][log.skip_key]:
            science_files.append([all_frames[science_file][log.time_key], science_file])
        else:
            all_frames[science_file][log.align_x0_key] = False
            all_frames[science_file][log.align_y0_key] = False
            all_frames[science_file][log.align_u0_key] = False
            _write_alignment(project, science_file, False, False, False)

    science_files.sort()
    science_files = [ff[1] for ff in science_files]

    if len(science_files) == 0:
        raise HOPSError('All frames are excluded, please check the inspection step.')

    fits_data, fits_header = project.open_reduced(science_files[0])
    shift_tolerance = int(max(fits_data.shape) * (shift_tolerance_p / 100.0))
    circles_diameter = 12 * fits_header[log.psf_key]

    job.task('alignment', 'Aligning frames', len(science_files))

    # find all stars in the first frame

    job.set_message('Analysing first frame...')
    job.set_preview(fits_data, science_files[0], header=fits_header)

    stars = image_find_stars(fits_data, fits_header,
                             mean=fits_header[log.mean_key], std=fits_header[log.std_key],
                             burn_limit=fits_header[log.hops_saturation_key],
                             centroids_snr=centroids_snr, stars_snr=stars_snr,
                             psf=fits_header[log.psf_key],
                             progress_window=job,
                             verbose='shallow')
    job.check()
    if stars is None:
        raise HOPSError('No stars detected in the first frame. Please exclude it in the inspection step.')

    stars = np.array(stars)

    all_stars_dict = {'all_stars': stars,
                      'x-length': len(fits_data[0]),
                      'y-length': len(fits_data),
                      'psf': fits_header[log.psf_key],
                      'burn_limit': fits_header[log.hops_saturation_key],
                      }
    project.save_all_stars(all_stars_dict)

    # choose calibration stars

    job.set_message('Choosing calibrating stars...')

    psf = np.array(all_stars_dict['psf'])
    burn_limit = all_stars_dict['burn_limit']
    length = np.sqrt((all_stars_dict['x-length'] / 2) ** 2 + (all_stars_dict['y-length'] / 2) ** 2)

    stars = stars[np.where(stars[:, 2] > 2)]
    stars = stars[np.where(stars[:, 7] > 2)]

    min_flux = np.log10(np.min(stars[:, 2]) - 1)
    max_flux = np.log10(0.5 * burn_limit)

    if faint_target_mode or moving_target_mode:
        weight_distance = 1
        weight_flux = 4
    else:
        weight_distance = 2
        weight_flux = 1

    stars = sorted(stars,
                   key=lambda x: - (weight_flux * (([min_flux, np.log10(x[2])][int(x[2] < 0.5 * burn_limit)] - min_flux) / (max_flux - min_flux)) + weight_distance * (1 - np.sqrt((x[0] - all_stars_dict['x-length'] / 2) ** 2 + (x[1] - all_stars_dict['y-length'] / 2) ** 2) / length))
                   / (weight_distance + weight_flux)
                   )

    if faint_target_mode or moving_target_mode:
        x0, y0 = stars[1][0], stars[1][1]
    else:
        x0, y0 = stars[0][0], stars[0][1]
    u0 = 0.0

    x0_fits0 = 1.0 * x0
    y0_fits0 = 1.0 * y0

    del stars[0]

    stars = np.array(stars)
    min_flux = np.log10(np.min(stars[:, 7]) - 1)
    max_flux = np.log10(np.max(stars[:, 7]))
    weight_distance = 1
    weight_flux = 3
    stars = sorted(stars,
                   key=lambda x: - (weight_flux * ((np.log10(x[7]) - min_flux) / (max_flux - min_flux)) + weight_distance * (1 - np.sqrt((x[0] - all_stars_dict['x-length'] / 2) ** 2 + (x[1] - all_stars_dict['y-length'] / 2) ** 2) / length))
                   / (weight_distance + weight_flux)
                   )

    # take the rest as calibration stars and calculate their polar coordinates relatively to the first
    comparisons = []
    for star in stars:
        r_position, u_position = cartesian_to_polar(star[0], star[1], x0, y0)
        if r_position > 5 * psf:
            comparisons.append([r_position, u_position])
        if len(comparisons) >= 500:
            break

    if faint_target_mode:
        min_calibration_stars_number = 3
        check_num = 2.5
    else:
        check_num = max(9.5, len(comparisons) / 10.0)

    ustep = np.arcsin(psf / comparisons[int(len(comparisons) / 2)][0])
    small_angles = np.append(np.arange(-rotation_tolerance, rotation_tolerance, ustep),
                             np.arange(-rotation_tolerance, rotation_tolerance, ustep) + np.pi)

    large_angles = np.array([np.pi, 0])
    for ff in range(1, int(np.pi / ustep) + 1):
        large_angles = np.append(large_angles, np.pi - ff * ustep)
        large_angles = np.append(large_angles, np.pi + ff * ustep)
        large_angles = np.append(large_angles, 0 - ff * ustep)
        large_angles = np.append(large_angles, 0 + ff * ustep)

    job.set_message('')

    # align each frame

    for science_file in science_files:

        job.check()

        fits_data, fits_header = project.open_reduced(science_file)
        y_length, x_length = fits_data.shape
        fits_datax, fits_datay = np.meshgrid(np.arange(0, x_length) + 0.5, np.arange(0, y_length) + 0.5)
        int_psf = int(max(1, round(fits_header[log.psf_key])))
        skip_time = 0

        def check_settings(settings_to_check):
            """Port of check_star: returns the first (x, y, u) whose predicted comparisons are bright enough."""
            for x, y, u, star in settings_to_check:
                job.check()
                test = 0
                for comp in comparisons:
                    check_x = int(x + comp[0] * np.cos(u + comp[1]))
                    check_y = int(y + comp[0] * np.sin(u + comp[1]))
                    if 0 < check_x < x_length and 0 < check_y < y_length:
                        check_sum = np.sum(fits_data[check_y - int_psf:check_y + int_psf + 1,
                                           check_x - int_psf:check_x + int_psf + 1])
                        check_lim = (fits_header[log.mean_key] +
                                     centroids_snr * fits_header[log.std_key]) * ((2 * int_psf + 1) ** 2)
                        if check_sum > check_lim:
                            test += 1
                        else:
                            test -= 1
                    if abs(test) > check_num:
                        break
                if test >= check_num:
                    return x, y, u
            return None

        job.task_message('alignment', ' ')
        stars_detected = False

        # test level 1: small shift around the previous position

        frame_stars = _find_centroids(fits_data,
                                      x0 - shift_tolerance, x0 + shift_tolerance,
                                      y0 - shift_tolerance, y0 + shift_tolerance,
                                      fits_header[log.mean_key], fits_header[log.std_key],
                                      2 * fits_header[log.hops_saturation_key], fits_header[log.psf_key],
                                      snr=centroids_snr)
        settings_to_check = []
        if len(frame_stars) > 0:
            frame_stars = np.array(frame_stars)[:, 1:]
            frame_stars = sorted(frame_stars, key=lambda x: (x[0] - x0) ** 2 + (x[1] - y0) ** 2)
            for star in frame_stars:
                settings_to_check.append([star[0], star[1], u0, star])
                if rotating_field_mode:
                    for rotation in large_angles + u0:
                        settings_to_check.append([star[0], star[1], rotation, star])

        found = check_settings(settings_to_check)

        # test level 2: large shift & rotation, matching the star pattern of the first frame

        frame_stars = None
        if found is None:
            job.set_message('Analysing frame...')
            job.task_message('alignment', 'Testing large shift & rotation...')
            job.set_preview(fits_data, science_file, header=fits_header)

            frame_stars = image_find_stars(fits_data, fits_header,
                                           mean=fits_header[log.mean_key], std=fits_header[log.std_key],
                                           burn_limit=2 * fits_header[log.hops_saturation_key],
                                           psf=fits_header[log.psf_key],
                                           centroids_snr=centroids_snr, stars_snr=stars_snr,
                                           sky_inner_aperture=2.0, sky_outer_aperture=5.0,
                                           verbose=True, order_by_flux=True, progress_window=job)
            job.check()
            job.set_message('')

            settings_to_check = []

            if frame_stars and len(frame_stars) > min_calibration_stars_number:

                check_num = max(min_calibration_stars_number - 0.5, len(frame_stars) / 10.0)

                all_stars = np.array(project.load_all_stars()['all_stars'])

                all_stars_to_check_x = np.array([all_stars[0][0]])
                all_stars_to_check_y = np.array([all_stars[0][1]])
                for star in all_stars:
                    if min(np.sqrt((star[0] - all_stars_to_check_x) ** 2 + (star[1] - all_stars_to_check_y) ** 2)) > 6 * fits_header[log.psf_key]:
                        all_stars_to_check_x = np.append(all_stars_to_check_x, star[0])
                        all_stars_to_check_y = np.append(all_stars_to_check_y, star[1])

                new_stars_to_check_x = np.array([frame_stars[0][0]])
                new_stars_to_check_y = np.array([frame_stars[0][1]])
                for star in frame_stars:
                    if min(np.sqrt((star[0] - new_stars_to_check_x) ** 2 + (star[1] - new_stars_to_check_y) ** 2)) > 6 * fits_header[log.psf_key]:
                        new_stars_to_check_x = np.append(new_stars_to_check_x, star[0])
                        new_stars_to_check_y = np.append(new_stars_to_check_y, star[1])

                if len(new_stars_to_check_y) > min_calibration_stars_number:

                    X = twirl.utils.find_transform(
                        np.array([[all_stars_to_check_x[ii], all_stars_to_check_y[ii]] for ii in range(min(len(all_stars_to_check_y) - 1, 20))]),
                        np.array([[new_stars_to_check_x[ii], new_stars_to_check_y[ii]] for ii in range(min(len(new_stars_to_check_y) - 1, 20))]),
                        n=20, tolerance=3 * fits_header[log.psf_key])

                    center, check = twirl.utils.affine_transform(X)(np.array([[x0_fits0, y0_fits0],
                                                                              [x0_fits0 + 10, y0_fits0]]))

                    for star in sorted(frame_stars, key=lambda x: np.sqrt((x[0] - center[0]) ** 2 + (x[1] - center[1]) ** 2))[:5]:
                        angles = large_angles if rotating_field_mode else small_angles
                        for rotation in angles + cartesian_to_polar(check[0], check[1], center[0], center[1])[1]:
                            settings_to_check.append([star[0], star[1], rotation, star])

            found = check_settings(settings_to_check)

        # test level 3: ask the user

        if found is None:
            job.task_message('alignment', 'Testing all shifts & rotations...')
            t_ask = time.time()
            answer = job.ask('Stars not detected in {0}.\nDo you want to skip this frame?'.format(science_file))
            skip_time = time.time() - t_ask
            settings_to_check = []
            if answer != 'Yes' and frame_stars:
                for star in frame_stars:
                    for rotation in large_angles:
                        settings_to_check.append([star[0], star[1], rotation, star])
            found = check_settings(settings_to_check)

        overlays = []

        if found is not None:

            stars_detected = True
            x0, y0, u0 = found

            # refine the rotation with the centroids of the comparison stars

            test_cos = []
            test_sin = []
            for ii in comparisons:
                check_x = x0 + ii[0] * np.cos(u0 + ii[1])
                check_y = y0 + ii[0] * np.sin(u0 + ii[1])
                if 0 < check_x < x_length and 0 < check_y < y_length:
                    search_window = int(round(3 * fits_header[log.psf_key]))
                    y_min = int(max(int(check_y) - search_window, 0))
                    y_max = int(min(int(check_y) + search_window + 1, y_length))
                    x_min = int(max(int(check_x) - search_window, 0))
                    x_max = int(min(int(check_x) + search_window + 1, x_length))
                    test_window = fits_data[y_min: y_max, x_min: x_max]
                    test_windowx = fits_datax[y_min: y_max, x_min: x_max]
                    test_windowy = fits_datay[y_min: y_max, x_min: x_max]
                    test_window_sum = np.sum(test_window)
                    polar = cartesian_to_polar(np.sum(test_window * test_windowx) / test_window_sum,
                                               np.sum(test_window * test_windowy) / test_window_sum, x0, y0)
                    diff = polar[1] - ii[1]
                    if diff < 0:
                        diff += 2 * np.pi
                    test_cos.append(np.cos(diff))
                    test_sin.append(np.sin(diff))
                if len(test_cos) >= 20:
                    break

            test_cos = np.median(test_cos)
            test_sin = np.median(test_sin)
            u0 = np.arccos(test_cos)
            if test_sin < 0:
                u0 = np.pi + (np.pi - u0)

            _write_alignment(project, science_file, x0, y0, u0)

            overlays.append({'type': 'circle', 'x': x0, 'y': y0, 'r': circles_diameter, 'color': '#ff5a5a', 'width': 2})
            for ii in comparisons[:int(check_num + 0.5)]:
                overlays.append({'type': 'circle', 'x': x0 + ii[0] * np.cos(u0 + ii[1]),
                                 'y': y0 + ii[0] * np.sin(u0 + ii[1]), 'r': circles_diameter, 'color': '#ffd33d'})
        else:
            _write_alignment(project, science_file, False, False, False)

        # as in the Tk version the frame keeps the last known position in all_frames,
        # while the header (used by photometry) is set to False
        all_frames[science_file][log.align_x0_key] = x0
        all_frames[science_file][log.align_y0_key] = y0
        all_frames[science_file][log.align_u0_key] = u0
        if not x0:
            all_frames[science_file][log.skip_key] = True

        job.set_preview(fits_data, science_file, overlays=overlays, header=fits_header)
        job.step('alignment', message=science_file if stars_detected else 'Not aligned: {0}'.format(science_file),
                 skip=skip_time)

    job.set_message('Aligning all stars in all frames...')
    project.save_all_frames(all_frames)

    # check which stars stay inside the field of view in all frames

    all_stars_dict = project.load_all_stars()
    stars = np.array(all_stars_dict['all_stars'])

    polar_coords = []
    for star in all_stars_dict['all_stars']:
        polar_coords.append(cartesian_to_polar(star[0], star[1],
                                               all_frames[science_files[0]][log.align_x0_key],
                                               all_frames[science_files[0]][log.align_y0_key]))

    in_fov = np.ones(len(polar_coords))

    for science_file in science_files:
        job.check()
        metadata = all_frames[science_file]
        ref_x_position = metadata[log.align_x0_key]
        ref_y_position = metadata[log.align_y0_key]
        ref_u_position = metadata[log.align_u0_key]
        psf = metadata[log.psf_key]

        if ref_x_position:
            in_fov_single = []
            for star in polar_coords:
                cartesian_x = ref_x_position + star[0] * np.cos(ref_u_position + star[1])
                cartesian_y = ref_y_position + star[0] * np.sin(ref_u_position + star[1])
                if (3 * psf < cartesian_x < all_stars_dict['x-length'] - 3 * psf
                        and 3 * psf < cartesian_y < all_stars_dict['y-length'] - 3 * psf):
                    in_fov_single.append(1)
                else:
                    in_fov_single.append(0)
            in_fov *= in_fov_single

    all_stars_dict['in_fov'] = np.array(in_fov)

    log.set_param('min_x', float(np.min(stars[np.where(in_fov), 0]) - 3 * psf))
    log.set_param('min_y', float(np.min(stars[np.where(in_fov), 1]) - 3 * psf))
    log.set_param('max_x', float(np.max(stars[np.where(in_fov), 0]) + 3 * psf))
    log.set_param('max_y', float(np.max(stars[np.where(in_fov), 1]) + 3 * psf))

    project.save_all_stars(all_stars_dict)

    log.set_param('alignment_complete', True)
    log.set_param('alignment_version', log.version)
    project.save_log()
    job.set_message('')

    aligned = sum(1 for ff in science_files if all_frames[ff][log.align_x0_key] and not all_frames[ff][log.skip_key])
    return {'frames': len(science_files), 'aligned': aligned, 'stars': int(np.sum(in_fov))}
