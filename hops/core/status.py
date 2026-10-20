
import os


def check_versions(log):
    """Backwards compatibility: results of versions older than 3.2.0 have to be produced again."""
    for stage, version_key in [['reduction', 'reduction_version'], ['alignment', 'reduction_version'],
                               ['photometry', 'photometry_version'], ['fitting', 'fitting_version']]:
        if log.get_param('{0}_complete'.format(stage)):
            if not log.is_version_new(log.get_param(version_key), '3.2.0'):
                log.set_param('{0}_complete'.format(stage), False)


def stages(project):
    """Port of HOPS.update_window: which steps are complete / available, based on the log and the files."""

    def stage(available, complete, text=''):
        return {'available': available, 'complete': complete, 'text': text}

    log = project.log
    directory_ok = os.path.isdir(project.directory)

    result = {
        'data_target': stage(directory_ok, False, 'You need to complete this step to proceed.'),
        'reduction': stage(False, False),
        'inspection': stage(False, False),
        'alignment': stage(False, False),
        'photometry': stage(False, False),
        'fitting': stage(False, False),
    }

    if not directory_ok or not log.get_param('data_target_complete'):
        return result

    result['data_target'] = stage(True, True, 'Data: {0} / Target: {1} / Location: {2}'.format(
        log.get_param('directory_short'), log.get_param('target_name'), log.get_param('location')))
    result['reduction'] = stage(True, False, 'You need to complete this step to proceed')

    trash = 0
    if not os.path.isfile(project.all_frames_path):
        log.set_param('reduction_complete', False)
    else:
        all_frames = project.load_all_frames()
        for i in all_frames:
            if not os.path.isfile(project.reduced(i)):
                log.set_param('reduction_complete', False)
                break
            if all_frames[i][log.skip_key]:
                trash += 1

    if not log.get_param('reduction_complete'):
        return result

    result['reduction'] = stage(True, True, 'Completed under v{0}'.format(log.get_param('reduction_version')))
    result['inspection'] = stage(True, True, 'Files discarded: {0}'.format(trash))
    result['alignment'] = stage(True, False, 'You need to complete this step to proceed')

    all_frames = project.load_all_frames()
    for i in all_frames:
        if not all_frames[i][log.skip_key] and not all_frames[i][log.align_x0_key]:
            log.set_param('alignment_complete', False)

    if not os.path.isfile(project.all_stars_path):
        log.set_param('alignment_complete', False)

    if not log.get_param('alignment_complete'):
        return result

    result['alignment'] = stage(True, True, 'Completed under v{0}'.format(log.get_param('alignment_version')))
    result['photometry'] = stage(True, False, 'You need to complete this step to proceed')

    if len(project.photometry_folders()) == 0:
        log.set_param('photometry_complete', False)

    if not log.get_param('photometry_complete'):
        return result

    result['photometry'] = stage(True, True, 'Completed under v{0}'.format(log.get_param('photometry_version')))
    result['fitting'] = stage(True, bool(log.get_param('fitting_complete')), '')
    if log.get_param('fitting_complete'):
        result['fitting']['text'] = 'Completed under v{0}'.format(log.get_param('fitting_version'))

    return result
