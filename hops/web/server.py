
import os
import json
import string
import asyncio
import threading

from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from exoclock.errors import ExoClockError

from hops import __message__
from hops.application_log import HOPSLog
from hops.core import data_target, fitting, inspection, photometry, planner, status
from hops.core.project import Project
from hops.core.common import HOPSError, to_builtin
from hops.pylightcurve41.errors import PyLCError
from hops.web import tasks
from hops.web.engine import Engine
from hops.web.imaging import image_response, region_from_query

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')


class AppState:

    def __init__(self):
        self.log = HOPSLog()
        status.check_versions(self.log)
        # long computations run in the engine process (see engine.py)
        self.jobs = Engine()
        self.jobs.on_finished.append(self.job_finished)
        self.jobs.start()
        self.science = data_target.ScienceCache()
        self.photometry_frame = None
        self.photometry_frame_key = None
        self.photometry_holder = {}
        self.plate_solution = None
        # requests run in a thread pool, but the log and the working directory are process-wide
        self.lock = threading.RLock()

    def job_finished(self, job):
        """Adopt what the engine changed: the log parameters and the results kept by the server."""
        with self.lock:
            payload = job.payload or {}
            if 'log_params' in payload:
                self.log.params.update(payload['log_params'])
            else:
                try:
                    self.log.load_local_log()
                except Exception:
                    pass
            if job.status == 'done':
                if job.kind == 'photometry':
                    self.photometry_holder = {'run': payload['data']}
                elif job.kind == 'plate solving':
                    self.plate_solution = payload['data']

    def require_idle(self):
        if self.jobs.busy():
            raise HOPSError('Please wait until "{0}" has finished.'.format(self.jobs.current.kind))

    @property
    def project(self):
        return Project(self.log)

    def first_frame(self):
        """First frame for star selection, re-read whenever the reduction / alignment results change."""
        project = self.project
        key = (project.directory, os.path.getmtime(project.all_frames_path), os.path.getmtime(project.all_stars_path))
        if key != self.photometry_frame_key:
            self.photometry_frame = photometry.FirstFrame(project)
            self.photometry_frame_key = key
            self.plate_solution = None
        return self.photometry_frame

    def require_directory(self):
        if not os.path.isdir(self.log.get_param('directory')):
            raise HOPSError('Choose a data directory first.')


state: Optional[AppState] = None


def get_state():
    global state
    if state is None:
        state = AppState()
    return state


app = FastAPI(title='HOPS')


@app.exception_handler(HOPSError)
def hops_error_handler(request: Request, exc: HOPSError):
    return JSONResponse(status_code=400, content={'detail': str(exc)})


@app.middleware('http')
async def library_error_middleware(request: Request, call_next):
    # exoclock / pylightcurve errors derive from BaseException, which Starlette's handlers do not accept
    try:
        return await call_next(request)
    except (ExoClockError, PyLCError) as exc:
        return JSONResponse(status_code=400, content={'detail': '{0}: {1}'.format(type(exc).__name__, exc)})


# general

@app.get('/api/state')
def api_state():
    s = get_state()
    log = s.log
    with s.lock:
        stages = status.stages(s.project)
        job = s.jobs.get()
        return to_builtin({
            'version': log.version,
            'latest_version': log.latest_version,
            'new_version': log.new_avail,
            'version_message': log.latest_version_message or __message__,
            'directory': log.get_param('directory'),
            'directory_short': log.get_param('directory_short'),
            'directory_ok': os.path.isdir(log.get_param('directory')),
            'target_name': log.get_param('target_name'),
            'target_ra_dec': log.get_param('target_ra_dec'),
            'location': log.get_param('location'),
            'filter': log.get_param('filter'),
            'stages': stages,
            'job': job.to_dict() if job else None,
        })


@app.get('/api/fs')
def api_fs(path: Optional[str] = None):
    """Folder browser for choosing the data directory."""
    s = get_state()
    if not path:
        path = s.log.get_param('directory')
        if not os.path.isdir(path):
            path = os.path.expanduser('~')
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        raise HOPSError('Folder not found: {0}'.format(path))
    dirs = []
    fits = 0
    try:
        for entry in sorted(os.scandir(path), key=lambda e: e.name.lower()):
            if entry.name.startswith('.'):
                continue
            if entry.is_dir():
                dirs.append(entry.name)
            elif entry.name.lower().endswith(('.fits', '.fit', '.fts')):
                fits += 1
    except PermissionError:
        pass
    roots = [os.path.expanduser('~')]
    if os.name == 'nt':
        roots += ['{0}:\\'.format(d) for d in string.ascii_uppercase if os.path.isdir('{0}:\\'.format(d))]
    else:
        roots += ['/']
        if os.path.isdir('/Volumes'):
            roots.append('/Volumes')
    parent = os.path.dirname(path)
    return {'path': path, 'parent': parent if parent != path else None, 'dirs': dirs, 'fits': fits, 'roots': roots}


class DirectoryBody(BaseModel):
    path: str


@app.post('/api/directory')
def api_directory(body: DirectoryBody):
    """Port of DataTargetWindow.change_directory."""
    s = get_state()
    log = s.log
    with s.lock:
        s.require_idle()
        new_directory = os.path.abspath(os.path.expanduser(body.path))
        if not os.path.isdir(new_directory):
            raise HOPSError('Not valid directory')
        current_directory = log.get_param('directory')
        try:
            log.set_param('directory', new_directory)
            log.set_param('directory_short', os.path.split(new_directory)[1])
            log.load_main_log()
            log.load_local_log_profile()
            try:
                log.load_local_log()
            except Exception:
                log.initiate_local_log()
            log.save_local_log_user()
            status.check_versions(log)
        except Exception:
            if os.path.isdir(current_directory):
                log.set_param('directory', current_directory)
                log.set_param('directory_short', os.path.split(current_directory)[1])
                log.load_main_log()
                log.load_local_log_profile()
                log.initiate_local_log()
            log.save_local_log_user()
            raise HOPSError('Not valid directory')
        s.science = data_target.ScienceCache()
    return api_state()


# profile

@app.get('/api/profile')
def api_profile():
    s = get_state()
    log = s.log
    fields = []
    for key in log.main_log_profile.keys():
        if '___' in key:
            fields.append({'key': key, 'spacer': True})
            continue
        value = log.local_log_profile.get(key, log.main_log_profile[key]) if log.local_log_profile else log.main_log_profile[key]
        fields.append({'key': key, 'value': '' if value is None else str(value)})
    return {'fields': fields}


class ProfileBody(BaseModel):
    values: Dict[str, Any]


@app.put('/api/profile')
def api_profile_save(body: ProfileBody):
    """Port of HOPS.update_local_log_profile (values are stored as entered, like the Tk entries)."""
    s = get_state()
    log = s.log
    with s.lock:
        profile = {}
        for key in log.main_log_profile.keys():
            if '___' in key:
                profile[key] = log.main_log_profile[key]
            else:
                profile[key] = body.values.get(key, log.main_log_profile[key])
        log.save_local_log_profile(profile)
        for key, value in profile.items():
            log.set_param(key, value)
    return api_profile()


# data & target

@app.get('/api/data-target')
def api_data_target():
    s = get_state()
    with s.lock:
        s.require_directory()
        values = data_target.form_values(s.log)
        return {
            'values': values,
            'filters': ['No filter chosen'] + list(data_target.filter_map.keys()),
            'time_stamps': ['exposure start', 'mid-exposure', 'exposure end'],
        }


class DraftBody(BaseModel):
    values: Dict[str, Any]


@app.post('/api/data-target/check')
def api_data_target_check(body: DraftBody):
    s = get_state()
    with s.lock:
        s.require_directory()
        return data_target.check(s.project, s.science, body.values)


@app.get('/api/data-target/files')
def api_data_target_files():
    s = get_state()
    with s.lock:
        s.require_directory()
        return {'files': data_target.file_list(s.project)}


@app.get('/api/data-target/header')
def api_data_target_header(observation_files: str):
    s = get_state()
    with s.lock:
        s.require_directory()
        s.science.get(s.project, observation_files)
        return {'file': s.science.name, 'header': data_target.header_list(s.science.header)}


class ResolveBody(BaseModel):
    choice: int
    auto_target_ra_dec: Optional[str] = ''
    simbad_target_name: Optional[str] = ''
    manual_target_ra_dec: Optional[str] = ''


@app.post('/api/data-target/resolve-target')
def api_resolve_target(body: ResolveBody):
    ra_dec, name = data_target.resolve_target(body.choice, body.auto_target_ra_dec or '',
                                              body.simbad_target_name or '', body.manual_target_ra_dec or '')
    return {'target_ra_dec': ra_dec, 'target_name': name, 'ok': data_target.target_ok(ra_dec)}


class LocationBody(BaseModel):
    location: str


@app.post('/api/data-target/check-location')
def api_check_location(body: LocationBody):
    return {'ok': data_target.location_ok(body.location)}


@app.post('/api/data-target/save')
def api_data_target_save(body: DraftBody):
    s = get_state()
    with s.lock:
        s.require_directory()
        s.require_idle()
        check = data_target.check(s.project, s.science, body.values)
        if not check['can_save']:
            raise HOPSError('Some settings are not valid, please check the fields marked in red.')
        values = dict(body.values)
        values['observation_time_key'] = check['observation_time_key']
        values['filter'] = check['filter']
        data_target.save(s.project, values)
    return api_state()


@app.get('/api/data-target/image')
def api_data_target_image(observation_files: str, max_size: int = 1600,
                          x0: Optional[float] = None, x1: Optional[float] = None, y0: Optional[float] = None, y1: Optional[float] = None):
    s = get_state()
    with s.lock:
        s.require_directory()
        s.science.get(s.project, observation_files)
        if s.science.data is None:
            raise HOPSError('No observation files found.')
        size = round(os.stat(s.science.path).st_size / 1024.0 / 1024.0, 2)
        return image_response(s.science.data, s.science.header, s.log, s.science.name, max_size,
                              extra={'size_mb': size}, region=region_from_query(x0, x1, y0, y1))


@app.get('/api/data-target/preview')
def api_data_target_preview(observation_files: str, crop_x1: int = 0, crop_x2: int = 0, crop_y1: int = 0,
                            crop_y2: int = 0, crop_edge_pixels: int = 0, bin_fits: int = 1, max_size: int = 1600,
                            x0: Optional[float] = None, x1: Optional[float] = None, y0: Optional[float] = None, y1: Optional[float] = None):
    s = get_state()
    with s.lock:
        s.require_directory()
        s.science.get(s.project, observation_files)
        if s.science.data is None:
            raise HOPSError('No observation files found.')
        frame = data_target.preview_frame(s.science.data, crop_x1, crop_x2, crop_y1, crop_y2,
                                          crop_edge_pixels, bin_fits)
        region = region_from_query(x0, x1, y0, y1)
        size = data_target.reduced_size_mb(frame) if region is None else None
        return image_response(frame, {}, s.log, 'This is only an example of the reduction output.',
                              max_size, extra={'size_mb': size}, region=region)


# reduction

class ReductionBody(BaseModel):
    show_all_frames: bool = False


@app.post('/api/reduction/run')
def api_reduction_run(body: ReductionBody):
    s = get_state()
    with s.lock:
        s.require_directory()
        s.jobs.set_flag('show_all_frames', body.show_all_frames)
        return s.jobs.submit('reduction', tasks.run_reduction, s.project).to_dict()


@app.post('/api/reduction/show-all')
def api_reduction_show_all(body: ReductionBody):
    get_state().jobs.set_flag('show_all_frames', body.show_all_frames)
    return {'show_all_frames': body.show_all_frames}


# inspection

@app.get('/api/inspection')
def api_inspection():
    s = get_state()
    with s.lock:
        s.require_directory()
        return inspection.series(s.project)


class InspectionBody(BaseModel):
    skip: Dict[str, bool]


@app.post('/api/inspection/save')
def api_inspection_save(body: InspectionBody):
    s = get_state()
    with s.lock:
        s.require_idle()
        inspection.save(s.project, body.skip)
    return api_state()


@app.get('/api/frame')
def api_frame(name: str, max_size: int = 1400, x0: Optional[float] = None, x1: Optional[float] = None, y0: Optional[float] = None, y1: Optional[float] = None):
    """A reduced frame (from REDUCED_DATA)."""
    s = get_state()
    with s.lock:
        s.require_directory()
        if os.path.basename(name) != name or not os.path.isfile(s.project.reduced(name)):
            raise HOPSError('Frame not found: {0}'.format(name))
        data, header = s.project.open_reduced(name)
        return image_response(data, header, s.log, name, max_size, region=region_from_query(x0, x1, y0, y1))


# alignment

@app.post('/api/alignment/run')
def api_alignment_run():
    s = get_state()
    with s.lock:
        s.require_directory()
        return s.jobs.submit('alignment', tasks.run_alignment, s.project).to_dict()


# photometry

@app.get('/api/photometry')
def api_photometry():
    s = get_state()
    with s.lock:
        s.require_directory()
        frame = s.first_frame()
        result = photometry.setup(s.project, frame)
        result['plate_solution'] = s.plate_solution
        run = s.photometry_holder.get('run')
        result['has_results'] = bool(run is not None and run.finished)
        return result


@app.get('/api/photometry/image')
def api_photometry_image(max_size: int = 1600, x0: Optional[float] = None, x1: Optional[float] = None, y0: Optional[float] = None, y1: Optional[float] = None):
    s = get_state()
    with s.lock:
        frame = s.first_frame()
        return image_response(frame.data, frame.header, s.log, frame.name, max_size,
                              region=region_from_query(x0, x1, y0, y1))


class RowsBody(BaseModel):
    settings: Dict[str, Any]
    rows: list
    fov_options: Optional[list] = None


@app.post('/api/photometry/update')
def api_photometry_update(body: RowsBody):
    s = get_state()
    with s.lock:
        return photometry.update_rows(s.log, s.first_frame(), body.rows, body.settings, s.plate_solution)


class PickBody(BaseModel):
    x: float
    y: float
    saturation: float


@app.post('/api/photometry/pick')
def api_photometry_pick(body: PickBody):
    """Double-click on the image: find the star and check that it stays inside the FOV."""
    s = get_state()
    log = s.log
    with s.lock:
        star = photometry.get_star(log, s.first_frame(), body.x, body.y, body.saturation)
        if not star:
            raise HOPSError('Star not acceptable: it could not be located or it includes pixels above the saturation limit.')
        if (star['x'] < log.get_param('min_x') or star['x'] > log.get_param('max_x')
                or star['y'] < log.get_param('min_y') or star['y'] > log.get_param('max_y')):
            raise HOPSError('Star not acceptable: it moves outside the FOV later.')
        return star


class FolderBody(BaseModel):
    folder: str


@app.post('/api/photometry/load-previous')
def api_photometry_load_previous(body: FolderBody):
    s = get_state()
    with s.lock:
        return photometry.load_previous(s.project, body.folder)


@app.post('/api/photometry/save-settings')
def api_photometry_save_settings(body: RowsBody):
    s = get_state()
    with s.lock:
        s.require_idle()
        checked = photometry.update_rows(s.log, s.first_frame(), body.rows, body.settings, s.plate_solution)
        if not checked['can_run']:
            raise HOPSError(checked['message'])
        photometry.save_settings(s.project, s.first_frame(), body.settings, checked['rows'], body.fov_options)
    return api_state()


class PlateBody(BaseModel):
    star_size_arcsec: float


@app.post('/api/photometry/plate-solve')
def api_photometry_plate_solve(body: PlateBody):
    s = get_state()
    with s.lock:
        s.first_frame()
        return s.jobs.submit('plate solving', tasks.plate_solve, s.project, body.star_size_arcsec).to_dict()


@app.post('/api/photometry/run')
def api_photometry_run(body: RowsBody):
    s = get_state()
    with s.lock:
        s.require_idle()
        checked = photometry.update_rows(s.log, s.first_frame(), body.rows, body.settings, s.plate_solution)
        if not checked['can_run']:
            raise HOPSError(checked['message'])
        photometry.save_settings(s.project, s.first_frame(), body.settings, checked['rows'], body.fov_options)
        s.photometry_holder = {}
        return s.jobs.submit('photometry', tasks.run_photometry, s.project).to_dict()


@app.get('/api/photometry/live')
def api_photometry_live():
    s = get_state()
    job = s.jobs.get()
    if job is not None and job.kind == 'photometry' and job.status == 'running':
        return job.live or None
    run = s.photometry_holder.get('run')
    return run.live_data() if run is not None else None


class ActiveBody(BaseModel):
    active: list


@app.post('/api/photometry/active')
def api_photometry_active(body: ActiveBody):
    """(De)activate comparisons after a run, like the "Active" check-boxes."""
    s = get_state()
    run = s.photometry_holder.get('run')
    if run is None or not run.finished:
        raise HOPSError('Run the photometry first.')
    active = [True] + [bool(v) for v in body.active[1:]]
    if sum(active[1:]) < 1:
        raise HOPSError('At least one comparison star must stay active.')
    run.active = active
    job = s.jobs.get()
    if job is not None and job.kind == 'photometry':
        job.set_live(**run.live_data())
    return run.live_data()


@app.post('/api/photometry/save-results')
def api_photometry_save_results():
    s = get_state()
    with s.lock:
        s.require_idle()
        run = s.photometry_holder.get('run')
        if run is None or not run.finished:
            raise HOPSError('Run the photometry first.')
        return {'folder': run.save()}


# fitting

@app.get('/api/fitting')
def api_fitting():
    s = get_state()
    with s.lock:
        s.require_directory()
        return fitting.setup(s.project)


@app.get('/api/fitting/light-curve')
def api_fitting_light_curve(file: str):
    s = get_state()
    with s.lock:
        return fitting.light_curve(s.project, file)


class FittingBody(BaseModel):
    settings: Dict[str, Any]
    manual: Dict[str, Any]
    auto: Dict[str, Any]
    database: Optional[str] = 'ExoClock'
    camera_gain: Optional[float] = 1.0


@app.post('/api/fitting/save-settings')
def api_fitting_save_settings(body: FittingBody):
    s = get_state()
    with s.lock:
        s.require_idle()
        fitting.save_settings(s.project, body.settings, body.manual, body.auto)
    return api_state()


@app.post('/api/fitting/test')
def api_fitting_test(body: FittingBody):
    s = get_state()
    with s.lock:
        return s.jobs.submit('fitting test', tasks.fitting_test, s.project, body.settings, body.manual,
                             body.auto).to_dict()


@app.post('/api/fitting/run')
def api_fitting_run(body: FittingBody):
    s = get_state()
    with s.lock:
        return s.jobs.submit('fitting', tasks.run_fitting, s.project, body.settings, body.manual, body.auto).to_dict()


@app.post('/api/fitting/export-info')
def api_fitting_export_info(body: FittingBody):
    s = get_state()
    with s.lock:
        return fitting.export_info(s.project, body.settings, body.manual, body.auto, body.database)


@app.post('/api/fitting/export')
def api_fitting_export(body: FittingBody):
    s = get_state()
    with s.lock:
        project = s.project
        name = fitting.export(project, body.settings, body.manual, body.auto, body.database, body.camera_gain)
        return {'file': name, 'path': project.path(name)}


# observing planner

@app.get('/api/planner')
def api_planner():
    return planner.defaults(get_state().log)


class SearchBody(BaseModel):
    name: str


@app.post('/api/planner/search')
def api_planner_search(body: SearchBody):
    return planner.search(body.name)


class PlannerBody(BaseModel):
    values: Dict[str, Any]


@app.post('/api/planner/check')
def api_planner_check(body: PlannerBody):
    v = body.values
    return {'coordinates': planner.test_coordinates(v.get('ra', ''), v.get('dec', '')),
            'date': planner.test_date(v.get('year_month', ''))}


@app.post('/api/planner/plot')
def api_planner_plot(body: PlannerBody, format: str = 'svg'):
    if format not in ('svg', 'png'):
        raise HOPSError('Unknown format.')
    content = planner.plot(body.values, format)
    media = 'image/svg+xml' if format == 'svg' else 'image/png'
    return Response(content=content, media_type=media)


class PlannerSaveBody(BaseModel):
    values: Dict[str, Any]
    path: str


@app.post('/api/planner/save')
def api_planner_save(body: PlannerSaveBody):
    """Desktop app: write the chart to the path chosen in the native "save as" dialog."""
    path = os.path.abspath(os.path.expanduser(body.path))
    if not path.lower().endswith('.png'):
        path += '.png'
    with open(path, 'wb') as f:
        f.write(planner.plot(body.values, 'png'))
    return {'path': path}


# files

@app.get('/api/file')
def api_file(path: str):
    """Serve a result file (PDF, JPG, TXT) from inside the data directory."""
    s = get_state()
    base = os.path.realpath(s.log.get_param('directory'))
    full = os.path.realpath(os.path.join(base, path))
    if not full.startswith(base + os.sep) or not os.path.isfile(full):
        raise HTTPException(status_code=404)
    return FileResponse(full)


@app.post('/api/reveal')
def api_reveal(body: FolderBody):
    """Open a result folder in the system file browser."""
    import subprocess
    import sys
    s = get_state()
    base = os.path.realpath(s.log.get_param('directory'))
    full = os.path.realpath(os.path.join(base, body.folder))
    if not (full == base or full.startswith(base + os.sep)) or not os.path.exists(full):
        raise HOPSError('Folder not found.')
    if sys.platform == 'darwin':
        subprocess.Popen(['open', full])
    elif os.name == 'nt':
        os.startfile(full)
    else:
        subprocess.Popen(['xdg-open', full])
    return {'ok': True}


# jobs

@app.get('/api/job')
def api_job():
    job = get_state().jobs.get()
    return job.to_dict() if job else None


@app.post('/api/job/cancel')
def api_job_cancel():
    job = get_state().jobs.get()
    if job:
        job.cancel()
    return job.to_dict() if job else None


class AnswerBody(BaseModel):
    value: str


@app.post('/api/job/answer')
def api_job_answer(body: AnswerBody):
    job = get_state().jobs.get()
    if job:
        job.answer(body.value)
    return job.to_dict() if job else None


@app.get('/api/job/preview')
def api_job_preview(max_size: int = 1200, x0: Optional[float] = None, x1: Optional[float] = None, y0: Optional[float] = None, y1: Optional[float] = None):
    s = get_state()
    job = s.jobs.get()
    if job is None or job.preview is None:
        raise HTTPException(status_code=404, detail='No preview')
    preview = job.preview
    return image_response(preview['data'], preview['header'] or {}, s.log, preview['name'], max_size,
                          extra={'version': job.preview_version}, region=region_from_query(x0, x1, y0, y1),
                          prebinned=preview.get('factor', 1))


# live job updates

@app.websocket('/api/ws')
async def api_ws(websocket: WebSocket):
    """Pushes the current job whenever it changes (progress, previews, questions)."""
    await websocket.accept()
    last = None
    try:
        while True:
            job = get_state().jobs.get()
            data = job.to_dict() if job else None
            key = json.dumps({k: v for k, v in data.items() if k != 'elapsed'}, sort_keys=True) if data else None
            if key != last:
                last = key
                await websocket.send_json(data)
            await asyncio.sleep(0.15)
    except (WebSocketDisconnect, RuntimeError):
        pass


# frontend

if os.path.isdir(os.path.join(STATIC_DIR, 'assets')):
    app.mount('/assets', StaticFiles(directory=os.path.join(STATIC_DIR, 'assets')), name='assets')


@app.get('/{path:path}')
def frontend(path: str):
    if path.startswith('api/'):
        raise HTTPException(status_code=404)
    candidate = os.path.join(STATIC_DIR, path)
    if path and os.path.isfile(candidate):
        return FileResponse(candidate)
    index = os.path.join(STATIC_DIR, 'index.html')
    if os.path.isfile(index):
        return FileResponse(index)
    return JSONResponse({'detail': 'Frontend not built. Run "npm run build" in hops/web/frontend.'}, status_code=404)
