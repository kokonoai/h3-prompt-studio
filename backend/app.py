from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
import os
import secrets
import subprocess
import threading
import time
import uuid
import zipfile
import httpx
from functools import lru_cache
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from PIL import Image, ImageOps, UnidentifiedImageError

from .projects import new_project, safe_id, atomic_json, check_project, merge_plan, merge_assist, ALLOWED_SHOT_FIELDS
from .resources import ResourceManager, ResourceError, local_url, gpu_snapshot

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('H3_STUDIO_DATA', ROOT / 'data')).resolve()
for folder in ('projects', 'assets', 'history', 'exports', 'productions', 'production_archive', 'production_films', 'video_library_films', 'series', 'series_archive', 'series_films', 'card_collections', 'card_collection_archive', 'video_workflows', 'library/templates', 'library/versions'):
    (DATA / folder).mkdir(parents=True, exist_ok=True)
Image.MAX_IMAGE_PIXELS = 40_000_000
TOKEN, BRIDGE_TOKEN = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
STATE_LOCK = threading.RLock()
TRANSFERS = {}
TRANSFER_TTL = 15 * 60
VIDEO_RUNS = None
STORIES = None
ASSET_RUNS = None
MOTION_LAB = None
PRODUCTIONS = None
SERIES = None
VIDEO_WORKFLOWS = None
VIDEO_FILE_LOCKS = {}
DEFAULT_SETTINGS = {'lm_url': 'http://127.0.0.1:11434/v1', 'model': '', 'context_length': 8192,
                    'comfy_urls': ['http://127.0.0.1:8188', 'http://127.0.0.1:8000', 'http://127.0.0.1:8010'], 'persona': 'universal', 'last_project': '',
                    'ai_memory_mode': 'exclusive'}
SETTINGS = {**DEFAULT_SETTINGS}
if (DATA / 'settings.json').exists():
    SETTINGS.update(json.loads((DATA / 'settings.json').read_text(encoding='utf-8')))

@lru_cache(maxsize=4)
def _assistant_client(base_url):
    from .lmstudio import LMStudioClient
    return LMStudioClient(base_url=base_url, timeout=180)


def client():
    # Keep capability knowledge across stages; diagnostics are context-local.
    # A changed endpoint gets a separate client and never inherits its cache.
    return _assistant_client(SETTINGS['lm_url'])

RESOURCES = ResourceManager(lambda: copy.deepcopy(SETTINGS), client, state_path=DATA / 'resource_state.json')
app = FastAPI(title='H3 Prompt Studio', version='1.23.1', docs_url='/api/docs')
BRIDGE_PORTS = ('8188', '8000', '8010')
LOCAL_ORIGINS = [f'http://{host}:{port}' for host in ('127.0.0.1', 'localhost') for port in (8766, 8188, 8010, 8000)]
app.add_middleware(CORSMiddleware, allow_origins=LOCAL_ORIGINS, allow_methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'], allow_headers=['Content-Type', 'X-H3-Bridge', 'X-H3-Token'])


def _localise_candidate_for_h3(lm, model, candidate):
    """Attach a source-bound English-direction/target-dialogue prompt."""
    from .compiler import compile_project
    from .prompt_language import localise_h3_prompt

    raw_candidate = copy.deepcopy(candidate)
    raw_candidate.pop('h3_prompt_translation', None)
    raw = compile_project(raw_candidate)
    if not raw['valid']:
        errors = [item['message'] for item in raw['issues'] if item['severity'] == 'error']
        raise ValueError('\n'.join(errors) or 'This scene could not produce a valid H3 prompt.')
    RESOURCES.stage = 'Finalising English direction and project-language dialogue'
    language_options = {}
    if candidate.get('h3_verbatim_blocks'):
        language_options['verbatim_blocks'] = candidate['h3_verbatim_blocks']
    record = localise_h3_prompt(
        lm, model, raw['prompt'], candidate.get('production_language') or 'zh-CN',
        **language_options,
    )
    result = copy.deepcopy(candidate)
    result['h3_prompt_translation'] = record
    compiled = compile_project(result)
    if not compiled['valid'] or compiled['prompt'] != record['prompt']:
        errors = [item['message'] for item in compiled['issues'] if item['severity'] == 'error']
        raise ValueError('\n'.join(errors) or 'The final H3 language pass could not be verified.')
    return result, compiled

@app.middleware('http')
async def local_boundary(request: Request, call_next):
    host = request.headers.get('host', '').split(':')[0]
    if host not in ('127.0.0.1', 'localhost', 'testserver'):
        return JSONResponse({'detail': 'Local connections only.'}, status_code=403)
    origin = request.headers.get('origin')
    if origin and origin not in LOCAL_ORIGINS:
        return JSONResponse({'detail': 'This origin is not connected to Studio.'}, status_code=403)
    transfer_read = request.method in ('GET', 'OPTIONS') and request.url.path.startswith('/api/comfy/transfers/')
    if origin and origin.rsplit(':', 1)[-1] in BRIDGE_PORTS and request.url.path != '/api/gpu/prepare-h3' and not transfer_read:
        return JSONResponse({'detail': 'ComfyUI bridge access is limited to prepared workflow transfers and GPU hand-off.'}, status_code=403)
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        normal = secrets.compare_digest(request.headers.get('x-h3-token', ''), TOKEN)
        scoped = request.url.path == '/api/gpu/prepare-h3' and secrets.compare_digest(request.headers.get('x-h3-bridge', ''), BRIDGE_TOKEN)
        if not normal and not scoped:
            return JSONResponse({'detail': 'Studio session expired. Reload this page.'}, status_code=403)
    try:
        length = int(request.headers.get('content-length', '0'))
        if length > 140_000_000:
            return JSONResponse({'detail': 'This upload is too large.'}, status_code=413)
    except ValueError:
        return JSONResponse({'detail': 'Invalid content length.'}, status_code=400)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors " + ' '.join(LOCAL_ORIGINS)
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response

@app.exception_handler(ValueError)
async def bad_value(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=400)

@app.exception_handler(ResourceError)
async def resource_error(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=409)

@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    # Never log an image, API credential, request body or user dialogue.
    return JSONResponse({'detail': f'{type(exc).__name__}: {str(exc)[:1200]}'}, status_code=502)

def load_project(project_id):
    path = DATA / 'projects' / (safe_id(project_id) + '.json')
    if not path.exists():
        raise HTTPException(404, 'Project not found.')
    return check_project(json.loads(path.read_text(encoding='utf-8')))

def list_projects():
    values = []
    for path in (DATA / 'projects').glob('*.json'):
        try:
            value = check_project(json.loads(path.read_text(encoding='utf-8')))
            values.append({'id': value['id'], 'title': value['title'], 'mode': value['mode'], 'duration': value['duration'], 'updated': path.stat().st_mtime})
        except (ValueError, KeyError):
            continue
    return sorted(values, key=lambda item: item['updated'], reverse=True)

def save_project(project):
    check_project(project)
    with STATE_LOCK:
        path = DATA / 'projects' / (safe_id(project['id']) + '.json')
        if path.exists():
            previous = path.read_bytes()
            # At most one automatic history snapshot each minute per project.
            history = DATA / 'history' / project['id'] / f'{int(time.time() // 60)}.json'
            if not history.exists():
                history.parent.mkdir(parents=True, exist_ok=True)
                history.write_bytes(previous)
        atomic_json(path, project)
        SETTINGS['last_project'] = project['id']
        atomic_json(DATA / 'settings.json', SETTINGS)
    return {'saved': True, 'id': project['id']}

@app.get('/api/bootstrap')
def bootstrap():
    from .prompts import PERSONAS
    projects = list_projects()
    last = SETTINGS.get('last_project')
    project = load_project(last) if last and any(p['id'] == last for p in projects) else new_project()
    return {'version': app.version, 'token': TOKEN, 'resource_token': BRIDGE_TOKEN, 'settings': SETTINGS,
            'project': project, 'projects': projects, 'personas': PERSONAS}

def output_locations():
    # Only these application-owned locations can be opened; never accept a path
    # or shell command from the browser.
    locations = {
        'videos': ('Local video playback copies', DATA / 'video_runs',
                   'Watched videos are cached by run ID. Original videos remain in the connected ComfyUI output/h3_prompt_studio/runs folder.'),
        'examples': ('Published examples', ROOT / 'demo',
                     'Optional public demonstration clips and sample projects included with this release.'),
        'projects': ('Saved projects & references', DATA,
                     'Your edits save automatically here. Open projects in Studio with the Projects button; use Export with images for a portable backup.'),
        'exports': ('Project export copies', DATA / 'exports',
                    'Export with images keeps a ZIP copy here and sends a download to your browser. Prompt text downloads go to your browser’s download location.'),
        'production-films': ('Long-form production films', DATA / 'production_films',
                              'Final films assembled in storyboard order from each production clip’s adopted take.'),
        'series-films': ('Script and episode films', DATA / 'series_films',
                         'Saved episode, selected-episode and full-script films. Use Script management to open an exact result folder.'),
    }
    # A local environment setting enables Explorer shortcuts without assuming a
    # particular Desktop, portable, or source-checkout ComfyUI installation.
    output = os.environ.get('H3_STUDIO_COMFY_OUTPUT', '').strip()
    if output:
        output = Path(output).expanduser().resolve()
        locations.update({
            'comfy-videos': ('Original ComfyUI videos', output / 'h3_prompt_studio',
                             'Original renders in the configured ComfyUI output folder.'),
            'comfy-states': ('Saved continuation states', output / 'mmh3',
                             'MMH3 working files for continuing generated clips. Keep these with the original videos.'),
        })
    return locations


def _open_generated_folder(path):
    """Open only a server-derived film directory, never a browser-provided path."""
    path = path.resolve()
    allowed_roots = ((DATA / 'production_films').resolve(), (DATA / 'series_films').resolve())
    if not any(path == root or path.is_relative_to(root) for root in allowed_roots):
        raise HTTPException(400, 'Only application-owned film folders may be opened.')
    if not path.is_dir():
        raise HTTPException(404, 'This film folder is not available yet.')
    if not hasattr(os, 'startfile'):
        raise HTTPException(409, 'Automatic folder opening is supported on Windows. Use the displayed path instead.')
    try:
        os.startfile(str(path))
    except OSError as exc:
        raise HTTPException(409, 'Windows could not open this film folder. Use the displayed path instead.') from exc
    return {'opened': True, 'path': str(path)}

@app.get('/api/files')
def files_index():
    return {'locations': [{'id': key, 'title': title, 'path': str(path.resolve()),
                           'description': description, 'available': path.is_dir()}
                          for key, (title, path, description) in output_locations().items()]}

@app.post('/api/files/open')
def open_files(body: dict):
    if set(body) != {'id'} or not isinstance(body['id'], str) or body['id'] not in output_locations():
        raise ValueError('Choose one of the listed Studio folders.')
    title, path, _ = output_locations()[body['id']]
    path = path.resolve()
    if not path.is_dir():
        raise HTTPException(404, 'This folder is not available on this computer yet.')
    if not hasattr(os, 'startfile'):
        raise HTTPException(409, 'Automatic folder opening is supported on Windows. Use the displayed path to open this folder.')
    try:
        os.startfile(str(path))
    except OSError as exc:
        raise HTTPException(409, 'Windows could not open this folder. Use the displayed path to open it manually.') from exc
    return {'opened': True, 'id': body['id'], 'title': title}

@app.post('/api/projects/new')
def create_project():
    project = new_project()
    save_project(project)
    return project

@app.get('/api/projects')
def projects_index():
    return list_projects()

@app.get('/api/projects/{project_id}')
def project_get(project_id: str):
    return load_project(project_id)

@app.post('/api/projects')
def project_save(project: dict):
    return save_project(project)

def library_folder(kind):
    if kind not in ('templates', 'versions'):
        raise ValueError('Choose saved setups or saved versions.')
    return DATA / 'library' / kind

def library_text(body, key, limit, default=''):
    value = body.get(key, default)
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f'{key} must be text with at most {limit} characters.')
    return value

def library_rating(body):
    rating = body.get('rating', 0)
    if type(rating) is not int or not 0 <= rating <= 5:
        raise ValueError('Choose a rating from zero to five stars.')
    return rating

@app.get('/api/library')
def library_index():
    result = {'templates': [], 'versions': []}
    with STATE_LOCK:
        for kind in result:
            for path in library_folder(kind).glob('*.json'):
                record = json.loads(path.read_text(encoding='utf-8'))
                project = record['project']
                result[kind].append({k: record[k] for k in ('id', 'name', 'created_at', 'notes', 'rating')} |
                    {'mode': project['mode'], 'duration': project['duration'], 'shot_count': len(project['shots']),
                     'asset_count': len(project['assets']), 'prompt_preview': record.get('prompt', '')[:240]})
            result[kind].sort(key=lambda value: value['created_at'], reverse=True)
    return result

@app.get('/api/library/{kind}/{record_id}')
def library_get(kind: str, record_id: str):
    path = library_folder(kind) / (safe_id(record_id) + '.json')
    if not path.is_file():
        raise HTTPException(404, 'This saved item no longer exists.')
    return json.loads(path.read_text(encoding='utf-8'))

@app.post('/api/library/{kind}')
def library_save(kind: str, body: dict):
    from .compiler import compile_project
    folder = library_folder(kind)
    project = copy.deepcopy(check_project(body.get('project')))
    name = library_text(body, 'name', 120).strip()
    if not name:
        raise ValueError('Give this saved item a short name.')
    prompt = library_text(body, 'prompt', 250_000)
    if kind == 'versions' and prompt:
        compiled = compile_project(project)
        if not compiled['valid'] or prompt != compiled['prompt']:
            raise ValueError('The prompt changed. Make a fresh prompt before saving this version.')
    record = {'id': str(uuid.uuid4()), 'name': name,
              'created_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'project': project, 'prompt': prompt if kind == 'versions' else '',
              'notes': library_text(body, 'notes', 5000), 'rating': library_rating(body)}
    with STATE_LOCK:
        atomic_json(folder / (record['id'] + '.json'), record)
    return record

@app.patch('/api/library/versions/{record_id}')
def library_update_version(record_id: str, body: dict):
    if not body or set(body) - {'name', 'notes', 'rating'}:
        raise ValueError('Only the saved name, rating and test notes can be changed.')
    with STATE_LOCK:
        record = library_get('versions', record_id)
        if 'name' in body:
            record['name'] = library_text(body, 'name', 120).strip()
            if not record['name']:
                raise ValueError('Give this saved item a short name.')
        if 'notes' in body:
            record['notes'] = library_text(body, 'notes', 5000)
        if 'rating' in body:
            record['rating'] = library_rating(body)
        atomic_json(library_folder('versions') / (safe_id(record_id) + '.json'), record)
    return record

@app.post('/api/settings')
def save_settings(body: dict):
    if RESOURCES.lock.locked():
        raise ResourceError('Wait for AI to finish before changing its connection.')
    allowed = {k: body[k] for k in DEFAULT_SETTINGS if k in body and k != 'last_project'}
    if 'lm_url' in allowed:
        allowed['lm_url'] = local_url(allowed['lm_url'])
    if 'comfy_urls' in allowed:
        if not isinstance(allowed['comfy_urls'], list) or not 1 <= len(allowed['comfy_urls']) <= 4:
            raise ValueError('Choose one to four local ComfyUI instances.')
        allowed['comfy_urls'] = [local_url(u) for u in allowed['comfy_urls']]
    if allowed.get('context_length', 8192) not in (4096, 8192, 12288, 16384):
        raise ValueError('Choose a supported context length.')
    if 'model' in allowed and (not isinstance(allowed['model'], str) or len(allowed['model']) > 500 or (allowed['model'] and not allowed['model'].strip())):
        raise ValueError('Choose a valid installed local model.')
    if allowed.get('ai_memory_mode', 'exclusive') not in ('exclusive', 'resident_small'):
        raise ValueError('Choose automatic model switching or the resident 0.8B assistant.')
    proposed = {**SETTINGS, **allowed}
    if proposed.get('ai_memory_mode') == 'resident_small':
        from .lmstudio import LMStudioClient
        LMStudioClient(base_url=proposed['lm_url']).resident_model_info(proposed['model'])
        allowed['context_length'] = 4096
    SETTINGS.update(allowed)
    atomic_json(DATA / 'settings.json', SETTINGS)
    return SETTINGS

@app.get('/api/connections')
def connections():
    result = {'stage': RESOURCES.stage, 'busy': RESOURCES.lock.locked(), 'error': RESOURCES.last_error,
              'gpu': gpu_snapshot(), 'instance_id': RESOURCES.instance_id, 'model': RESOURCES.model_key,
              'ai_memory_mode': SETTINGS['ai_memory_mode']}
    try:
        result['lm'] = {'online': True, 'models': client().models(), 'loaded': client().loaded_instances()}
    except Exception as exc:
        result['lm'] = {'online': False, 'models': [], 'loaded': [], 'error': str(exc)[:400]}
    try:
        result['comfy'] = RESOURCES.queues()
        if (not result['busy'] and RESOURCES.last_error and
                RESOURCES.last_error.startswith('Cannot confirm ComfyUI is idle at ') and
                all(not item['running'] and not item['pending'] for item in result['comfy'])):
            # A fresh successful /queue response supersedes an earlier transient
            # read timeout. Do not leave the connection panel showing a stale
            # failure after ComfyUI has demonstrably recovered.
            RESOURCES.last_error = None
            RESOURCES.stage = 'idle'
            result['error'] = None
            result['stage'] = 'idle'
    except Exception as exc:
        result['comfy'] = []
        result['comfy_error'] = str(exc)
    return result

@app.get('/api/comfy/options')
def comfy_options():
    from .comfy_transfer import installed_transfer_options
    return installed_transfer_options(copy.deepcopy(SETTINGS))

@app.post('/api/comfy/prepare')
def comfy_prepare(body: dict):
    project = copy.deepcopy(check_project(body.get('project')))
    transfer = build_studio_transfer(project, body.get('prompt'))
    ticket = secrets.token_urlsafe(32)
    expiry = time.time() + TRANSFER_TTL
    transfer['manifest']['title'] = 'Prompt Studio · ' + project['title']
    record = {**transfer, 'ticket': ticket, 'title': transfer['manifest']['title'],
              'comfy_origin': transfer['comfy_url'], 'resource_token': BRIDGE_TOKEN,
              'expires_at': datetime.fromtimestamp(expiry, timezone.utc).isoformat()}
    folder = DATA / 'exports' / ('comfy-' + transfer['id'])
    folder.mkdir(parents=True, exist_ok=False)
    for name, value in [('workflow.json', transfer['workflow']), ('api-workflow.json', transfer['prompt']), ('manifest.json', transfer['manifest'])]:
        atomic_json(folder / name, value)
    with STATE_LOCK:
        for old in [key for key, value in TRANSFERS.items() if value['expiry'] <= time.time()]:
            del TRANSFERS[old]
        if len(TRANSFERS) >= 40:
            del TRANSFERS[next(iter(TRANSFERS))]
        TRANSFERS[ticket] = {'expiry': expiry, 'record': record}
    return {'ticket': ticket, 'manifest': transfer['manifest'], 'expires_at': record['expires_at'],
            'comfy_url': transfer['comfy_url'], 'open_url': transfer['comfy_url'] + '/?h3studio_transfer=' + ticket,
            'export_folder': str(folder)}

def build_studio_transfer(project, prompt):
    from .compiler import compile_project
    from .comfy_transfer import build_transfer
    project = copy.deepcopy(check_project(project))
    compiled = compile_project(project)
    if not compiled['valid']:
        raise ValueError('Fix the highlighted prompt issues before sending to ComfyUI.')
    if prompt != compiled['prompt']:
        raise ValueError('The prompt changed. Review its current version before sending to ComfyUI.')
    render = project.get('comfy_render', {})
    if not isinstance(render, dict):
        raise ValueError('The ComfyUI settings must be an object.')
    config = {**render, 'comfy_urls': copy.deepcopy(SETTINGS['comfy_urls'])}
    def resolve_asset(asset):
        meta = asset_meta(asset['id'])
        folder = (DATA / 'assets' / safe_id(asset['id'])).resolve()
        path = (folder / meta['filename']).resolve()
        if not path.is_relative_to(folder) or meta.get('media_type') != 'image':
            raise ValueError('This photo is not available in the Studio library.')
        return path
    profile_id = render.get('workflow_profile_id', 'builtin')
    profile = video_workflow_manager().get(profile_id)
    if project.get('mode') not in profile['modes']:
        raise ValueError('The selected ComfyUI workflow does not support this H3 input mode.')
    from .video_workflows import REF8_WORKFLOW_ID, ref8_recipe_settings
    if profile_id == REF8_WORKFLOW_ID:
        config.update(ref8_recipe_settings())
    return build_transfer(project, compiled['prompt'], config, resolve_asset,
                          template_graph=profile.get('graph'), template_name=profile['name'])

@app.get('/api/comfy/transfers/{ticket}')
def comfy_transfer_get(ticket: str, request: Request):
    if len(ticket) != 43 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in ticket):
        raise HTTPException(404, 'This workflow transfer is unavailable. Send it again from Studio.')
    with STATE_LOCK:
        value = TRANSFERS.get(ticket)
        if not value or value['expiry'] <= time.time():
            TRANSFERS.pop(ticket, None)
            raise HTTPException(404, 'This workflow transfer expired. Send it again from Studio.')
        record = value['record']
        origin = request.headers.get('origin')
        if origin and origin.rsplit(':', 1)[-1] in BRIDGE_PORTS and origin != record['comfy_origin']:
            raise HTTPException(403, 'This workflow was prepared for a different ComfyUI instance.')
        return record

@app.get('/api/system-prompt')
def system_prompt(persona: str = 'universal', mode: str = 'ref2va', version: str = 'classic'):
    from .prompts import export_system_prompt
    if mode not in ('ref2va', 'fl2va', 'i2va', 'l2va', 't2va'):
        raise ValueError('Choose a supported H3 mode.')
    from .prompts import PERSONAS
    if persona not in {item['id'] for item in PERSONAS}:
        raise ValueError('Choose a listed system prompt persona.')
    return {'prompt': export_system_prompt(persona, mode, version)}

def video_manager():
    global VIDEO_RUNS
    with STATE_LOCK:
        if VIDEO_RUNS is None:
            from .video_runs import VideoRunManager
            VIDEO_RUNS = VideoRunManager(DATA, build_studio_transfer, RESOURCES)
        return VIDEO_RUNS

@app.get('/api/video/runs')
def video_runs_list(project_id: str | None = None):
    return {'runs': video_manager().list(safe_id(project_id) if project_id else None)}

@app.post('/api/video/runs')
def video_run_create(body: dict):
    from .compiler import compile_project
    project = copy.deepcopy(check_project(body.get('project')))
    link = project.get('production_link')
    if isinstance(link, dict) and link.get('production_id'):
        manager = production_manager()
        try:
            production = manager.get(link['production_id'])
        except ValueError:
            production = None  # A detached Studio copy may outlive its production.
        if production and manager.has_inherited_clip_directions(production, project):
            raise ValueError('This video prompt still contains directions inherited from an older clip. Regenerate this clip prompt before generating video.')
    compiled = compile_project(project)
    if not compiled['valid'] or body.get('prompt') != compiled['prompt']:
        raise ValueError('Make a current valid prompt before generating this video.')
    return video_manager().submit(body.get('request_id'), project, compiled['prompt'], parent_run_id=body.get('parent_run_id'))

@app.get('/api/video/runs/{run_id}')
def video_run_get(run_id: str):
    return video_manager().refresh(safe_id(run_id))

@app.get('/api/video/runs/{run_id}/live-progress')
def video_run_live_progress(run_id: str):
    return video_manager().live_progress(safe_id(run_id))

@app.get('/api/video/runs/{run_id}/live-progress/events')
def video_run_progress_events(run_id: str):
    manager = video_manager()
    manager.get(safe_id(run_id))
    return StreamingResponse(manager.live_progress_events(run_id), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

@app.get('/api/game/system')
def game_system():
    from .game_director import GAME_ENGINE_SYSTEM
    return {'text': GAME_ENGINE_SYSTEM, 'version': app.version}

@app.get('/api/integrations/upscale')
def upscale_capabilities():
    from .upscale_adapter import capabilities
    return capabilities()

@app.post('/api/integrations/upscale/open')
def upscale_open(body: dict):
    from .upscale_adapter import open_gui
    if set(body) - {'run_id'}:
        raise ValueError('Choose a completed Studio video or open UPSCALE without a file.')
    source = scene_video_path(safe_id(body['run_id'])) if body.get('run_id') else None
    return open_gui(source)

@app.patch('/api/video/runs/{run_id}')
def video_run_update(run_id: str, body: dict):
    return video_manager().update_metadata(safe_id(run_id), body)

@app.post('/api/video/runs/{run_id}/resolve')
def video_run_resolve(run_id: str):
    return video_manager().resolve_missing(safe_id(run_id))

@app.post('/api/video/runs/{run_id}/reroll')
def video_run_reroll(run_id: str, body: dict):
    return video_manager().reroll(body.get('request_id'), safe_id(run_id))

@app.post('/api/video/runs/{run_id}/cancel')
def video_run_cancel(run_id: str):
    return video_manager().cancel(safe_id(run_id))

@app.post('/api/video/runs/{run_id}/combine')
def video_run_combine(run_id: str, body: dict):
    return video_manager().combine(body.get('request_id'), safe_id(run_id))

@app.get('/api/video/runs/{run_id}/project')
def video_run_snapshot(run_id: str):
    return video_manager().snapshot(safe_id(run_id))

def cached_run_video(run_id: str):
    run_id = safe_id(run_id)
    manager = video_manager()
    descriptor = manager.media(run_id)
    folder = DATA / 'video_runs' / run_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'playback.mp4'
    with STATE_LOCK:
        lock = VIDEO_FILE_LOCKS.setdefault(run_id, threading.Lock())
    with lock:
        if path.is_file() and path.stat().st_size:
            return path
        target = local_url(descriptor['comfy_url'], '/view')
        temporary = folder / ('playback-' + uuid.uuid4().hex + '.part')
        try:
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=60) as client:
                with client.stream('GET', target, params={key: descriptor[key] for key in ('filename', 'subfolder', 'type')}) as response:
                    response.raise_for_status()
                    size = 0
                    with temporary.open('wb') as output:
                        for chunk in response.iter_bytes(1024 * 1024):
                            size += len(chunk)
                            if size > 512 * 1024 * 1024:
                                raise ValueError('This video is too large for the built-in preview cache.')
                            output.write(chunk)
                    if not size:
                        raise ValueError('ComfyUI returned an empty video file.')
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return path

@app.get('/api/video/runs/{run_id}/video')
def video_run_playback(run_id: str, download: bool = False):
    path = cached_run_video(run_id)
    return FileResponse(path, media_type='video/mp4', filename=f'H3-{safe_id(run_id)}.mp4' if download else None)

@app.post('/api/video/runs/{run_id}/ending-image')
def video_run_ending_image(run_id: str):
    run_id = safe_id(run_id)
    path = cached_run_video(run_id)
    metadata = path.parent / 'ending-asset.json'
    with STATE_LOCK:
        lock = VIDEO_FILE_LOCKS.setdefault('ending:' + run_id, threading.Lock())
    with lock:
        if metadata.is_file():
            result = json.loads(metadata.read_text(encoding='utf-8'))
            if (DATA / 'assets' / safe_id(result['id']) / 'source.png').is_file():
                return result
        image_path = path.parent / 'ending.png'
        # Decode only the final second, then take its actual last video frame.
        result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-sseof', '-1', '-i', str(path),
                                 '-an', '-vf', 'reverse,scale=min(1024\\,iw):-2', '-frames:v', '1', str(image_path)],
                                capture_output=True, timeout=45, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode or not image_path.is_file():
            raise ValueError('The ending image could not be read. Keep the video available and retry continuation.')
        asset = store_asset(image_path.read_bytes(), 'Previous video - actual ending.png', 'image/png')
        asset.update(role='context', semantic_role='pose', enabled=True, video_run_ending=run_id,
                     description='Actual final frame of the selected previous video. Use it to preserve final positions, clothing, camera framing and object holders. This is continuity context, not a new identity reference.')
        atomic_json(metadata, asset)
        return asset

@app.post('/api/video/runs/{run_id}/suggest')
def video_run_suggest(run_id: str, body: dict):
    from .continuation_suggestions import suggest_continuations
    duration, direction = body.get('duration', 5), body.get('direction', '')
    if type(duration) is not int or not 4 <= duration <= 15:
        raise ValueError('Choose a next clip between 4 and 15 whole seconds.')
    if not isinstance(direction, str) or len(direction) > 1000:
        raise ValueError('Keep your next-scene direction within 1000 characters.')
    job = video_manager().refresh(safe_id(run_id))
    if job.get('operation') == 'combine' and job.get('can_continue') and job.get('continue_from_run_id'):
        job = video_manager().refresh(safe_id(job['continue_from_run_id']))
    if job.get('status') != 'succeeded' or not job.get('continuation_source'):
        raise ValueError('Select a finished take with saved motion before continuing it.')
    source = video_manager().snapshot(job['id'])
    started = time.monotonic()
    def generate(model):
        RESOURCES.stage = 'Reading the actual ending and suggesting next scenes'
        ending = video_run_ending_image(job['id'])
        result = suggest_continuations(client(), model, source, duration, image_data(ending['id']), direction,
                                       small_model=SETTINGS.get('ai_memory_mode') == 'resident_small')
        return {**result, 'ending_asset': ending, 'ending_image_url': '/api/assets/' + ending['id'] + '/file',
                'model': SETTINGS['model'], 'source_run_id': job['id']}
    result = RESOURCES.run_ai(SETTINGS['model'], generate)
    result['seconds'] = round(time.monotonic() - started, 3)
    return result

@app.post('/api/gpu/prepare-ai')
def prepare_ai(body: dict):
    return RESOURCES.run_ai(body.get('model') or SETTINGS['model'])

def asset_manager():
    global ASSET_RUNS
    with STATE_LOCK:
        if ASSET_RUNS is None:
            from .asset_runs import AssetRunManager
            ASSET_RUNS = AssetRunManager(DATA, RESOURCES, lambda: copy.deepcopy(SETTINGS), store_asset)
        return ASSET_RUNS

def production_manager():
    global PRODUCTIONS
    with STATE_LOCK:
        if PRODUCTIONS is None:
            from .productions import ProductionManager
            PRODUCTIONS = ProductionManager(DATA, load_project, save_project, asset_meta, store_asset)
        return PRODUCTIONS


def series_manager():
    global SERIES
    with STATE_LOCK:
        if SERIES is None:
            from .series import SeriesManager
            SERIES = SeriesManager(DATA, lambda ident: production_manager().get(ident))
        return SERIES


def video_workflow_manager():
    global VIDEO_WORKFLOWS
    with STATE_LOCK:
        if VIDEO_WORKFLOWS is None:
            from .video_workflows import VideoWorkflowManager
            VIDEO_WORKFLOWS = VideoWorkflowManager(DATA)
        return VIDEO_WORKFLOWS


@app.get('/api/video-workflows')
def video_workflows_index():
    return video_workflow_manager().list()


@app.post('/api/video-workflows')
def video_workflow_create(body: dict):
    return video_workflow_manager().create(body)


@app.delete('/api/video-workflows/{workflow_id}')
def video_workflow_delete(workflow_id: str):
    workflow_id = safe_id(workflow_id)
    usages = []
    manager = production_manager()
    for summary in manager.list():
        production = manager.get(summary['id'])
        for segment in production.get('segments', []):
            if segment.get('workflow_profile_id', 'builtin') == workflow_id:
                usages.append(f"{production['title']} / {segment['index']:02d} {segment['title']}")
                if len(usages) >= 5:
                    break
        if len(usages) >= 5:
            break
    if usages:
        raise ValueError(
            'This workflow is still assigned to production clips. Switch those clips to another '
            'workflow before deleting it: ' + '; '.join(usages))
    return video_workflow_manager().delete(workflow_id)

def story_manager():
    global STORIES
    with STATE_LOCK:
        if STORIES is None:
            from .stories import StoryManager
            def save_story_project(project):
                check_project(project)
                atomic_json(DATA / 'projects' / (project['id'] + '.json'), project)
            STORIES = StoryManager(DATA, video_manager, RESOURCES, client, lambda: copy.deepcopy(SETTINGS),
                                   save_story_project, video_run_ending_image, image_data, asset_manager)
        return STORIES

@app.get('/api/assets/generators')
def asset_generators():
    return asset_manager().options()

@app.post('/api/asset-runs')
def asset_run_create(body: dict):
    return asset_manager().submit(body.get('request_id'), body.get('spec', {}))

@app.get('/api/asset-runs/{run_id}')
def asset_run_status(run_id: str):
    return asset_manager().refresh(safe_id(run_id))

@app.post('/api/asset-runs/{run_id}/cancel')
def asset_run_cancel(run_id: str):
    return asset_manager().cancel(safe_id(run_id))

@app.post('/api/asset-runs/{run_id}/resume')
def asset_run_resume(run_id: str):
    return asset_manager().resume(safe_id(run_id))

@app.post('/api/asset-runs/{run_id}/retry')
def asset_run_retry(run_id: str, body: dict):
    return asset_manager().retry(safe_id(run_id), safe_id(body.get('request_id')))

@app.get('/api/series')
def series_index():
    return series_manager().list()


@app.post('/api/series')
def series_create(body: dict):
    collection_id = body.get('card_collection_id')
    if collection_id:
        production_manager().get_card_collection(safe_id(collection_id))
    return series_manager().create(body)


@app.get('/api/series/{series_id}')
def series_get(series_id: str):
    return series_manager().get(series_id)


@app.patch('/api/series/{series_id}')
def series_update(series_id: str, body: dict):
    collection_id = body.get('card_collection_id')
    if collection_id:
        production_manager().get_card_collection(safe_id(collection_id))
    return series_manager().update(series_id, body)


@app.delete('/api/series/{series_id}')
def series_delete(series_id: str):
    return series_manager().delete(series_id)


@app.get('/api/productions')
def productions_index():
    return production_manager().list()

@app.post('/api/productions')
def production_create(body: dict):
    return production_manager().create(body)

@app.get('/api/productions/{production_id}')
def production_get(production_id: str):
    return production_manager().get(production_id)

@app.get('/api/card-collections')
def card_collections_index():
    return production_manager().list_card_collections()

@app.get('/api/card-collections/{collection_id}')
def card_collection_get(collection_id: str):
    return production_manager().get_card_collection(collection_id)

@app.patch('/api/card-collections/{collection_id}')
def card_collection_rename(collection_id: str, body: dict):
    return production_manager().rename_card_collection(collection_id, body.get('name'))

@app.post('/api/card-collections/{collection_id}/duplicate')
def card_collection_duplicate(collection_id: str, body: dict):
    return production_manager().duplicate_card_collection(collection_id, body.get('name'))

@app.delete('/api/card-collections/{collection_id}')
def card_collection_delete(collection_id: str):
    return production_manager().delete_card_collection(collection_id)

@app.post('/api/productions/{production_id}/card-collection')
def card_collection_save(production_id: str, body: dict):
    return production_manager().save_card_collection(production_id, body.get('name'))

@app.post('/api/productions/{production_id}/card-collection/{collection_id}/apply')
def card_collection_apply(production_id: str, collection_id: str):
    return production_manager().apply_card_collection(production_id, collection_id)

@app.patch('/api/productions/{production_id}')
def production_update(production_id: str, body: dict):
    return production_manager().update(production_id, body)


@app.delete('/api/productions/{production_id}')
def production_delete(production_id: str):
    return production_manager().delete(production_id)

@app.post('/api/productions/{production_id}/cards/{kind}/{card_id}/assets')
async def production_card_asset(production_id: str, kind: str, card_id: str,
                                file: UploadFile = File(...)):
    data = await file.read(64 * 1024 * 1024 + 1)
    asset = store_asset(data, file.filename or 'reference', file.content_type or '')
    return production_manager().attach_card_asset(production_id, kind, card_id, asset)


@app.post('/api/productions/{production_id}/cards/{kind}/{card_id}/image/attach')
def production_card_generated_image_attach(production_id: str, kind: str, card_id: str, body: dict):
    """Bind only a completed image job explicitly made for this card."""
    production = production_manager().assert_active(production_id)
    if kind not in ('characters', 'wardrobe', 'props', 'environments'):
        raise ValueError('Only visual production cards can receive generated images.')
    card_id = safe_id(card_id)
    if not any(card['id'] == card_id for card in production['cards'][kind]):
        raise ValueError('Production card not found.')
    run = asset_manager().refresh(safe_id(body.get('run_id')))
    expected_tag = f'card-{production_id[:8]}-{card_id[:8]}'
    if run.get('prompt_tag') != expected_tag or run.get('status') != 'succeeded' or not run.get('asset'):
        raise ValueError('The selected image job is not a completed result for this card.')
    return production_manager().attach_generated_card_asset(production_id, kind, card_id, run['asset'])


@app.post('/api/productions/{production_id}/overviews/{kind}/asset')
async def production_overview_asset(production_id: str, kind: str,
                                    file: UploadFile = File(...)):
    data = await file.read(64 * 1024 * 1024 + 1)
    asset = store_asset(data, file.filename or 'category-overview', file.content_type or '')
    return production_manager().attach_overview_asset(production_id, kind, asset)


def _generate_production_text_cards(production_id, force=False):
    from .productions import (CARD_KINDS, CARD_PLANNER_SCHEMA, CARD_PLANNER_SYSTEM,
                              card_plan_source_hash, card_planning_payload,
                              planning_chunks)
    manager = production_manager()
    production = manager.assert_active(production_id)
    if not force and production.get('card_plan_source_hash') == card_plan_source_hash(production):
        return production
    # AI completion is additive: apply_card_plan fills blanks and adds missing
    # text cards, but never rewrites a user's cards or removes their media. This
    # lets a character-only library gain story props/environments before the
    # storyboard while keeping every authored identity untouched.

    def generate(model):
        pieces = planning_chunks(production['brief'], limit=5000)
        combined = {'series_voice_style': '', **{kind: [] for kind in CARD_KINDS}}
        seen = {kind: set() for kind in CARD_KINDS}
        limits = {**{kind: 64 for kind in CARD_KINDS}, 'styles': 8}
        for index, piece in enumerate(pieces):
            RESOURCES.stage = f'Building text asset cards {index + 1} of {len(pieces)}'
            answer = client().complete_json(
                model, CARD_PLANNER_SYSTEM,
                card_planning_payload(production, piece, index + 1, len(pieces)),
                CARD_PLANNER_SCHEMA, max_tokens=4096, temperature=0.2)
            if not combined['series_voice_style'] and answer.get('series_voice_style'):
                combined['series_voice_style'] = answer['series_voice_style']
            for kind in CARD_KINDS:
                for card in answer.get(kind, []):
                    if not isinstance(card, dict):
                        continue
                    identity = card.get('character_name') if kind == 'voices' else card.get('name')
                    key = identity.strip().casefold() if isinstance(identity, str) else ''
                    if not key or key in seen[kind] or len(combined[kind]) >= limits[kind]:
                        continue
                    seen[kind].add(key)
                    combined[kind].append(card)
        return combined

    try:
        planned = RESOURCES.run_ai(SETTINGS['model'], generate)
        return manager.apply_card_plan(production_id, planned, 'local_ai')
    except Exception as exc:
        production = manager.get(production_id)
        production['card_planner_warning'] = (
            'Local AI could not finish the text card library. Existing cards were preserved and planning can continue: '
            + str(exc)[:360])
        return manager.save(production)


@app.post('/api/productions/{production_id}/cards/plan')
def production_cards_plan(production_id: str, body: dict):
    force = body.get('force', True)
    if type(force) is not bool:
        raise ValueError('force must be true or false.')
    return _generate_production_text_cards(production_id, force)

@app.post('/api/productions/{production_id}/plan')
def production_plan(production_id: str, body: dict):
    from .productions import (PLANNER_SYSTEM, current_episode_story,
                              episode_timing_targets, fallback_segments, fit_planned_durations,
                              planning_chunks, planning_payload, production_schema_for_story,
                              timed_clip_groups)
    started = time.monotonic()
    production = production_manager().assert_active(production_id)
    story = current_episode_story(production)
    use_ai = body.get('use_ai', True)
    if type(use_ai) is not bool:
        raise ValueError('use_ai must be true or false.')
    if use_ai:
        production = _generate_production_text_cards(production_id, False)
        story = current_episode_story(production)
    planned, planner, warning = None, 'local_heuristic', None
    if use_ai:
        try:
            def generate(model):
                # Explicit source timecodes are clip-boundary authority. Plan
                # each merged 5-15 second group independently so a local model
                # cannot pull dialogue or action across an authored boundary.
                source_groups = timed_clip_groups(story)
                pieces = ([group['text'] for group in source_groups]
                          if source_groups else planning_chunks(story))
                segments, previous_ending = [], ''
                for index, piece in enumerate(pieces):
                    RESOURCES.stage = f'Planning story part {index + 1} of {len(pieces)}'
                    answer = client().complete_json(
                        model, PLANNER_SYSTEM,
                        planning_payload(production, piece, index + 1, len(pieces), previous_ending),
                        production_schema_for_story(piece), max_tokens=4096, temperature=0.25)
                    segments.extend(answer['segments'])
                    if len(segments) > 64:
                        raise ValueError('This story needs more than 64 H3 clips. Split it into episodes before planning.')
                    previous_ending = segments[-1]['ending'] if segments else previous_ending
                target = episode_timing_targets(production, story=story)['episode_target_seconds']
                return fit_planned_durations(segments, target, production['language'], story)
            planned = RESOURCES.run_ai(SETTINGS['model'], generate)
            planner = 'local_ai'
        except Exception as exc:
            warning = f'Local AI planning was unavailable, so safe dynamic timing was used instead: {str(exc)[:360]}'
    if planned is None:
        target = episode_timing_targets(production, story=story)['episode_target_seconds']
        planned = fallback_segments(story, target, production['language'])
    production_manager().apply_plan(production_id, planned, planner, warning)
    return production_manager().record_timing(production_id, 'storyboard_plan_seconds', time.monotonic() - started)

@app.post('/api/productions/{production_id}/episodes/plan')
def production_episode_plan(production_id: str, body: dict):
    from .productions import EPISODE_PLANNER_SYSTEM, EPISODE_SCHEMA, episode_planning_payload, fallback_episodes
    started = time.monotonic()
    production = production_manager().assert_active(production_id)
    use_ai = body.get('use_ai', True)
    if type(use_ai) is not bool:
        raise ValueError('use_ai must be true or false.')
    if use_ai:
        production = _generate_production_text_cards(production_id, False)
    planned, planner, warning = None, 'local_heuristic', None
    if use_ai:
        try:
            def generate(model):
                episodes, batch_size, previous_ending = [], 4, ''
                while len(episodes) < production['episode_count']:
                    count = min(batch_size, production['episode_count'] - len(episodes))
                    RESOURCES.stage = f"Planning episodes {len(episodes) + 1}-{len(episodes) + count}"
                    answer = client().complete_json(
                        model, EPISODE_PLANNER_SYSTEM,
                        episode_planning_payload(production, len(episodes), count, previous_ending),
                        EPISODE_SCHEMA, max_tokens=4096, temperature=0.2)
                    batch = answer['episodes']
                    if len(batch) != count:
                        raise ValueError('The local model returned an incomplete episode batch.')
                    episodes.extend(batch)
                    previous_ending = batch[-1].get('continuity_notes') or batch[-1].get('logline', '')
                return episodes
            planned = RESOURCES.run_ai(SETTINGS['model'], generate)
            planner = 'local_ai'
        except Exception as exc:
            warning = f'Local AI episode planning was unavailable, so a safe ordered outline was used instead: {str(exc)[:360]}'
    if planned is None:
        planned = fallback_episodes(production)
    production_manager().apply_episode_plan(production_id, planned, planner, warning)
    return production_manager().record_timing(production_id, 'episode_plan_seconds', time.monotonic() - started)

@app.post('/api/productions/{production_id}/segments/{segment_id}/materialize')
def production_materialize(production_id: str, segment_id: str):
    production_manager().assert_active(production_id)
    result = production_manager().materialise(production_id, segment_id)
    from .compiler import compile_project
    result['compiled'] = compile_project(result['project'])
    return result

@app.post('/api/productions/{production_id}/materialize')
def production_materialize_all(production_id: str, body: dict):
    only_missing = body.get('only_missing', True)
    if type(only_missing) is not bool:
        raise ValueError('only_missing must be true or false.')
    production = production_manager().assert_active(production_id)
    prepared, skipped = [], []
    for segment in production['segments']:
        if only_missing and segment.get('status') == 'ready' and segment.get('project_id'):
            skipped.append(segment['id'])
            continue
        result = production_manager().materialise(production_id, segment['id'])
        prepared.append({'segment_id': segment['id'], 'project_id': result['project']['id']})
    return {'production': production_manager().get(production_id),
            'prepared': prepared, 'skipped': skipped}


def production_outputs(production_id, runs=None):
    """Join production clips to their local video runs without trusting client file paths."""
    production = production_manager().get(safe_id(production_id))
    runs = video_manager().list() if runs is None else runs
    by_project = {}
    for run in runs:
        if run.get('operation') == 'combine':
            continue
        by_project.setdefault(run.get('project_id'), []).append(run)
    segments, selected_ids = [], []
    for segment in production['segments']:
        candidates = by_project.get(segment.get('project_id'), [])
        ready = [run for run in candidates if run.get('status') == 'succeeded' and run.get('video_url')]
        manual_id = segment.get('selected_video_run_id')
        selected = next((run for run in ready if run['id'] == manual_id), None)
        selection = 'manual' if selected else 'latest'
        if selected is None and ready:
            selected = ready[0]
        if selected:
            selected_ids.append(selected['id'])
        segments.append({
            'segment_id': segment['id'], 'index': segment['index'], 'title': segment['title'],
            'project_id': segment.get('project_id'), 'project_status': segment['status'],
            'candidates': candidates[:24], 'selected': selected, 'selection': selection,
        })
    all_ready = bool(segments) and len(selected_ids) == len(segments)
    signature = hashlib.sha256(json.dumps(selected_ids, separators=(',', ':')).encode()).hexdigest()[:20] if all_ready else None
    output = DATA / 'production_films' / production['id'] / (signature + '.mp4') if signature else None
    final_ready = bool(output and output.is_file() and output.stat().st_size)
    adopted_video_seconds = round(sum(float((row['selected'] or {}).get('elapsed_seconds') or 0)
                                      for row in segments), 3)
    active_jobs = sum(run.get('status') in ('preparing', 'queued', 'running', 'uncertain')
                      for row in segments for run in row['candidates'])
    return {
        'production_id': production['id'], 'auto_merge': production.get('auto_merge', True),
        'segments': segments, 'selected_run_ids': selected_ids, 'all_ready': all_ready,
        'ready_count': len(selected_ids), 'segment_count': len(segments), 'signature': signature,
        'estimated_seconds': round(sum(float((row['selected'] or {}).get('new_seconds') or
                                              (row['selected'] or {}).get('duration') or 0)
                                       for row in segments), 3),
        'active_jobs': active_jobs,
        'timings': {
            'episode_plan_seconds': production.get('timings', {}).get('episode_plan_seconds'),
            'storyboard_plan_seconds': production.get('timings', {}).get('storyboard_plan_seconds'),
            'prompt_generation_seconds': round(sum(float(row.get('prompt_seconds') or 0)
                                                    for row in production['segments']), 3),
            'video_generation_seconds': adopted_video_seconds,
            'merge_seconds': production.get('timings', {}).get('merge_seconds'),
        },
        'final_ready': final_ready,
        'final_url': f"/api/productions/{production['id']}/film?signature={signature}" if final_ready else None,
        'download_url': f"/api/productions/{production['id']}/film?signature={signature}&download=1" if final_ready else None,
        'file_path': str(output.resolve()) if final_ready else None,
        'folder_path': str(output.parent.resolve()) if final_ready else None,
    }


@app.get('/api/productions/{production_id}/outputs')
def production_outputs_get(production_id: str):
    return production_outputs(production_id)


@app.patch('/api/productions/{production_id}/segments/{segment_id}/video')
def production_select_video(production_id: str, segment_id: str, body: dict):
    production = production_manager().get(safe_id(production_id))
    segment = next((item for item in production['segments'] if item['id'] == safe_id(segment_id)), None)
    if not segment:
        raise ValueError('Production clip not found.')
    run_id = body.get('run_id')
    if run_id in (None, ''):
        segment['selected_video_run_id'] = None
    else:
        run = video_manager().get(safe_id(run_id))
        if (run.get('project_id') != segment.get('project_id') or run.get('operation') == 'combine' or
                run.get('status') != 'succeeded' or not run.get('video_url')):
            raise ValueError('Choose a completed take generated by this exact production clip.')
        segment['selected_video_run_id'] = run['id']
    production_manager().save(production)
    return production_outputs(production_id)


def build_production_film(production_id):
    overview = production_outputs(production_id)
    if overview['active_jobs']:
        raise ValueError('Wait for the current ComfyUI video task to finish or stop it before assembling the final film.')
    if not overview['all_ready']:
        raise ValueError('Every storyboard clip needs a completed adopted take before the final film can be assembled.')
    if len(overview['segments']) > 100:
        raise ValueError('Assemble at most 100 clips in one production film.')
    folder = DATA / 'production_films' / safe_id(production_id)
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / (overview['signature'] + '.mp4')
    with STATE_LOCK:
        lock = VIDEO_FILE_LOCKS.setdefault('production-film:' + safe_id(production_id), threading.Lock())
    with lock:
        if output.is_file() and output.stat().st_size:
            return output
        first = overview['segments'][0]['selected']
        width, height = first.get('width'), first.get('height')
        if type(width) is not int or type(height) is not int or not 64 <= width <= 8192 or not 64 <= height <= 8192:
            width, height = 1344, 768
        width, height = width - width % 2, height - height % 2
        normalized = []
        for row in overview['segments']:
            run = row['selected']
            path = folder / (run['id'] + f'-{width}x{height}-a1.mp4')
            normalized.append(_normalize_series_file(scene_video_path(run['id']), path, width, height))
        listing = folder / (overview['signature'] + '.txt')
        listing.write_text('\n'.join("file '" + path.name + "'" for path in normalized), encoding='utf-8')
        temporary = output.with_name(output.stem + '-building.mp4')
        result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '1',
            '-i', str(listing), '-c', 'copy', '-map_metadata', '-1', '-movflags', '+faststart', str(temporary)],
            capture_output=True, timeout=600, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode or not temporary.is_file() or not temporary.stat().st_size:
            temporary.unlink(missing_ok=True)
            raise ValueError('The final production film could not be assembled. Individual videos remain available.')
        temporary.replace(output)
    return output


@app.post('/api/productions/{production_id}/film')
def production_film_build(production_id: str):
    production_manager().assert_active(production_id)
    existing = production_outputs(production_id)
    if existing['final_ready']:
        return existing
    started = time.monotonic()
    build_production_film(production_id)
    production_manager().record_timing(production_id, 'merge_seconds', time.monotonic() - started)
    return production_outputs(production_id)


@app.get('/api/productions/{production_id}/film')
def production_film_get(production_id: str, signature: str, download: bool = False):
    overview = production_outputs(production_id)
    if not overview['all_ready'] or signature != overview['signature']:
        raise HTTPException(409, 'The adopted takes changed. Assemble the current production again.')
    path = DATA / 'production_films' / safe_id(production_id) / (signature + '.mp4')
    if not path.is_file() or not path.stat().st_size:
        raise HTTPException(404, 'Assemble the final production film first.')
    return FileResponse(path, media_type='video/mp4',
                        filename=f'H3-Production-{safe_id(production_id)}.mp4' if download else None)


@app.post('/api/productions/{production_id}/film/open')
def production_film_open(production_id: str):
    overview = production_outputs(production_id)
    if not overview['final_ready']:
        raise HTTPException(404, 'Assemble this project film first.')
    return _open_generated_folder(DATA / 'production_films' / safe_id(production_id))


def video_library_overview():
    """Read-only adopted-video catalogue with explicit script/episode membership."""
    scripts, memberships = [], {}
    for summary in series_manager().list():
        try:
            series = series_manager().get(summary['id'])
        except (ValueError, OSError, KeyError, TypeError):
            continue
        episodes = []
        for episode in series['episodes']:
            episodes.append({'index': episode['index'], 'title': episode['title'],
                             'production_ids': list(episode['production_ids'])})
            for part_index, production_id in enumerate(episode['production_ids'], 1):
                memberships.setdefault(production_id, []).append({
                    'series_id': series['id'], 'series_title': series['title'],
                    'episode_index': episode['index'], 'episode_title': episode['title'],
                    'part_index': part_index,
                })
        scripts.append({'id': series['id'], 'title': series['title'], 'episodes': episodes})

    runs = video_manager().list()
    videos, productions = [], []
    for summary in production_manager().list():
        try:
            production = production_manager().get(summary['id'])
            output = production_outputs(summary['id'], runs)
        except (ValueError, OSError, KeyError, TypeError):
            continue
        productions.append({'id': production['id'], 'title': production['title'],
                            'current_episode': production['current_episode']})
        for row in output['segments']:
            selected = row.get('selected')
            job = None
            if selected:
                job = {key: selected.get(key) for key in (
                    'id', 'status', 'seed', 'duration', 'new_seconds', 'created_at',
                    'elapsed_seconds', 'width', 'height', 'video_url', 'scene_video_url',
                    'download_url', 'output_folder')}
            videos.append({
                'production_id': production['id'], 'production_title': production['title'],
                'production_episode': production['current_episode'],
                'segment_id': row['segment_id'], 'segment_index': row['index'],
                'segment_title': row['title'], 'project_id': row.get('project_id'),
                'project_status': row['project_status'], 'selected': job,
                'ready': bool(job), 'memberships': copy.deepcopy(memberships.get(production['id'], [])),
            })
    return {'scripts': scripts, 'productions': productions, 'videos': videos,
            'ready_count': sum(row['ready'] for row in videos), 'video_count': len(videos)}


@app.get('/api/video-library')
def video_library_get():
    return video_library_overview()


def series_outputs(series_id):
    """Read-only readiness view; projects are never silently substituted."""
    series = series_manager().get(series_id)
    episodes, signatures = [], []
    for episode in series['episodes']:
        parts = []
        for production_id in episode['production_ids']:
            try:
                production = production_manager().get(production_id)
                output = production_outputs(production_id)
                parts.append({'production_id': production_id, 'title': production['title'],
                              'ready_count': output['ready_count'], 'segment_count': output['segment_count'],
                              'all_ready': output['all_ready'], 'final_ready': output['final_ready'],
                              'signature': output['signature'], 'final_url': output['final_url'],
                              'active_jobs': output['active_jobs']})
            except (ValueError, OSError, KeyError):
                parts.append({'production_id': production_id, 'title': 'Missing project',
                              'ready_count': 0, 'segment_count': 0, 'all_ready': False,
                              'final_ready': False, 'signature': None, 'final_url': None,
                              'active_jobs': 0, 'missing': True})
        ready = bool(parts) and all(part['all_ready'] and not part['active_jobs'] for part in parts)
        signature_parts = [episode['index'], [part['production_id'] for part in parts],
                           [part['signature'] for part in parts]]
        signatures.append(signature_parts)
        episode_signature = (hashlib.sha256(json.dumps(signature_parts, separators=(',', ':')).encode()).hexdigest()[:20]
                             if ready else None)
        episodes.append({'index': episode['index'], 'title': episode['title'],
                          'parts': parts, 'part_count': len(parts), 'ready_count': sum(part['all_ready'] for part in parts),
                          'all_ready': ready, 'signature': episode_signature})
    all_ready = bool(episodes) and all(episode['all_ready'] for episode in episodes)
    signature = hashlib.sha256(json.dumps(signatures, separators=(',', ':')).encode()).hexdigest()[:20] if all_ready else None
    root = DATA / 'series_films' / series['id']
    folder = root / signature if signature else None
    for episode in episodes:
        path = (_series_episode_folder(series['id'], episode['index'], episode['signature']) /
                f"episode-{episode['index']:02d}.mp4") if episode['signature'] else None
        legacy = folder / f"episode-{episode['index']:02d}.mp4" if folder else None
        if path and (not path.is_file() or not path.stat().st_size) and legacy and legacy.is_file() and legacy.stat().st_size:
            path = legacy
        episode['film_ready'] = bool(path and path.is_file() and path.stat().st_size)
        episode['film_url'] = (f"/api/series/{series['id']}/film/episode/{episode['index']}?signature={signature}"
                               if episode['film_ready'] and path == legacy else
                               f"/api/series/{series['id']}/film/episode/{episode['index']}?signature={episode['signature']}"
                               if episode['film_ready'] else None)
        episode['file_path'] = str(path.resolve()) if episode['film_ready'] else None
        episode['folder_path'] = str(path.parent.resolve()) if episode['film_ready'] else None
    final = folder / 'complete.mp4' if folder else None
    final_ready = bool(final and final.is_file() and final.stat().st_size)
    return {'series_id': series['id'], 'title': series['title'], 'episodes': episodes,
             'all_ready': all_ready, 'signature': signature, 'final_ready': final_ready,
             'final_url': f"/api/series/{series['id']}/film?signature={signature}" if final_ready else None,
             'download_url': f"/api/series/{series['id']}/film?signature={signature}&download=1" if final_ready else None,
             'file_path': str(final.resolve()) if final_ready else None,
             'folder_path': str(final.parent.resolve()) if final_ready else None}


def _series_episode_folder(series_id, index, signature):
    return DATA / 'series_films' / safe_id(series_id) / 'episodes' / f'episode-{index:02d}-{signature}'


def series_selection_outputs(series_id, indices):
    overview = series_outputs(series_id)
    if (not isinstance(indices, list) or not 1 <= len(indices) <= 100 or
            any(type(index) is not int or not 1 <= index <= 100 for index in indices) or
            len(set(indices)) != len(indices)):
        raise ValueError('Choose distinct episode numbers between 1 and 100.')
    wanted = set(indices)
    selected = [episode for episode in overview['episodes'] if episode['index'] in wanted]
    if len(selected) != len(indices):
        raise ValueError('A selected episode is not in this script.')
    ordered = [episode['index'] for episode in selected]
    ready = all(episode['all_ready'] for episode in selected)
    signature = (hashlib.sha256(json.dumps([[episode['index'], episode['signature']]
             for episode in selected], separators=(',', ':')).encode()).hexdigest()[:20] if ready else None)
    path = DATA / 'series_films' / safe_id(series_id) / 'selections' / signature / 'selection.mp4' if signature else None
    film_ready = bool(path and path.is_file() and path.stat().st_size)
    url = (f"/api/series/{safe_id(series_id)}/film/selection/video?signature={signature}&episodes={','.join(map(str, ordered))}"
           if film_ready else None)
    return {'series_id': safe_id(series_id), 'episode_indices': ordered, 'episodes': selected,
            'all_ready': ready, 'signature': signature, 'final_ready': film_ready,
            'final_url': url, 'download_url': url + '&download=1' if url else None,
            'file_path': str(path.resolve()) if film_ready else None,
            'folder_path': str(path.parent.resolve()) if film_ready else None}


def _parse_series_indices(value):
    if not isinstance(value, str) or not value or len(value) > 400:
        raise ValueError('Choose episode numbers to assemble.')
    try:
        return [int(piece) for piece in value.split(',')]
    except ValueError as exc:
        raise ValueError('Episode numbers must be comma-separated integers.') from exc


def _concat_series_files(files, destination):
    """Concat only generated files with internal UUID-derived paths."""
    listing = destination.with_suffix('.txt')
    listing.write_text('\n'.join("file '" + path.name + "'" for path in files), encoding='utf-8')
    temporary = destination.with_name(destination.stem + '-building.mp4')
    result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '1',
        '-i', str(listing), '-c', 'copy', '-map_metadata', '-1', '-movflags', '+faststart', str(temporary)],
        capture_output=True, timeout=600, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode or not temporary.is_file() or not temporary.stat().st_size:
        temporary.unlink(missing_ok=True)
        raise ValueError('A series film could not be assembled. Every source project video remains available.')
    temporary.replace(destination)


def _series_dimensions(first_production_id):
    first_run = production_outputs(first_production_id)['segments'][0]['selected']
    width, height = first_run.get('width'), first_run.get('height')
    if type(width) is not int or type(height) is not int or not 64 <= width <= 8192 or not 64 <= height <= 8192:
        width, height = 1344, 768
    return width - width % 2, height - height % 2


def _normalize_series_file(source, target, width, height):
    if target.is_file() and target.stat().st_size:
        return target
    probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=index',
                            '-of', 'csv=p=0', str(source)], capture_output=True, timeout=30,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if probe.returncode:
        raise ValueError('A source video could not be inspected. Original videos remain available.')
    has_audio = bool(probe.stdout.strip())
    temporary = target.with_name(target.stem + '-building.mp4')
    command = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source)]
    if not has_audio:
        command += ['-f', 'lavfi', '-i', 'anullsrc=r=32000:cl=stereo']
    command += ['-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps=24,setsar=1',
        '-map', '0:v:0', '-map', '0:a:0' if has_audio else '1:a:0', '-map_metadata', '-1',
        '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-threads', '4',
        '-c:a', 'aac', '-ar', '32000', '-ac', '2', '-af', 'apad', '-shortest',
        '-movflags', '+faststart', str(temporary)]
    result = subprocess.run(command, capture_output=True, timeout=600,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode or not temporary.is_file() or not temporary.stat().st_size:
        temporary.unlink(missing_ok=True)
        raise ValueError('A script video could not be normalized. Original project films remain available.')
    temporary.replace(target)
    return target


def _video_library_runs(run_ids):
    if (not isinstance(run_ids, list) or not 2 <= len(run_ids) <= 100 or
            any(not isinstance(ident, str) for ident in run_ids)):
        raise ValueError('Select 2–100 completed videos to assemble.')
    clean = [safe_id(ident) for ident in run_ids]
    if len(set(clean)) != len(clean):
        raise ValueError('Select each video only once.')
    records = []
    for ident in clean:
        run = video_manager().get(ident)
        if (run.get('operation') == 'combine' or run.get('status') != 'succeeded' or
                not run.get('video_url')):
            raise ValueError('Every selected item must be a completed original video take.')
        records.append(run)
    return clean, records


def _video_library_signature(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{20}', value):
        raise ValueError('Invalid selected-film signature.')
    return value


def build_video_library_film(run_ids):
    clean, records = _video_library_runs(run_ids)
    signature = hashlib.sha256(json.dumps(clean, separators=(',', ':')).encode()).hexdigest()[:20]
    folder = DATA / 'video_library_films' / signature
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / 'selection.mp4'
    with STATE_LOCK:
        lock = VIDEO_FILE_LOCKS.setdefault('video-library:' + signature, threading.Lock())
    with lock:
        if not output.is_file() or not output.stat().st_size:
            width, height = records[0].get('width'), records[0].get('height')
            if type(width) is not int or type(height) is not int or not 64 <= width <= 8192 or not 64 <= height <= 8192:
                width, height = 1344, 768
            width, height = width - width % 2, height - height % 2
            normalized = []
            for position, record in enumerate(records, 1):
                target = folder / f"clip-{position:03d}-{record['id']}-{width}x{height}.mp4"
                normalized.append(_normalize_series_file(scene_video_path(record['id']), target, width, height))
            _concat_series_files(normalized, output)
    return signature, output


def video_library_film_result(signature, output, run_ids):
    ready = output.is_file() and bool(output.stat().st_size)
    url = f'/api/video-library/film/{signature}' if ready else None
    return {'signature': signature, 'run_ids': list(run_ids), 'final_ready': ready,
            'final_url': url, 'download_url': url + '?download=1' if url else None,
            'file_path': str(output.resolve()) if ready else None,
            'folder_path': str(output.parent.resolve()) if ready else None}


@app.post('/api/video-library/film')
def video_library_film_build(body: dict):
    if not isinstance(body, dict) or set(body) - {'run_ids'}:
        raise ValueError('Choose completed videos from the video overview.')
    run_ids = body.get('run_ids')
    signature, output = build_video_library_film(run_ids)
    return video_library_film_result(signature, output, run_ids)


@app.get('/api/video-library/film/{signature}')
def video_library_film_get(signature: str, download: bool = False):
    signature = _video_library_signature(signature)
    path = DATA / 'video_library_films' / signature / 'selection.mp4'
    if not path.is_file() or not path.stat().st_size:
        raise HTTPException(404, 'Assemble the selected videos first.')
    return FileResponse(path, media_type='video/mp4',
                        filename=f'H3-Video-Selection-{signature}.mp4' if download else None)


@app.post('/api/video-library/film/{signature}/open')
def video_library_film_open(signature: str):
    signature = _video_library_signature(signature)
    folder = DATA / 'video_library_films' / signature
    path = folder / 'selection.mp4'
    if not path.is_file() or not path.stat().st_size:
        raise HTTPException(404, 'Assemble the selected videos first.')
    return _open_generated_folder(folder)


def build_series_episode(series_id, index):
    overview = series_outputs(series_id)
    episode = next((item for item in overview['episodes'] if item['index'] == index), None)
    if not episode:
        raise ValueError('This episode is not in the script.')
    if not episode['all_ready']:
        raise ValueError('Complete every ordered part of this episode before assembling it.')
    folder = _series_episode_folder(series_id, index, episode['signature'])
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / f'episode-{index:02d}.mp4'
    with STATE_LOCK:
        lock = VIDEO_FILE_LOCKS.setdefault('series-episode:' + safe_id(series_id) + ':' + str(index), threading.Lock())
    with lock:
        if output.is_file() and output.stat().st_size:
            return output
        width, height = _series_dimensions(episode['parts'][0]['production_id'])
        normalized = []
        for part in episode['parts']:
            source = build_production_film(part['production_id'])
            target = folder / f"part-{part['production_id']}-{width}x{height}.mp4"
            normalized.append(_normalize_series_file(source, target, width, height))
        _concat_series_files(normalized, output)
        return output


def build_series_film(series_id):
    overview = series_outputs(series_id)
    if not overview['all_ready']:
        raise ValueError('Every episode needs ordered projects with completed adopted takes before series assembly.')
    folder = DATA / 'series_films' / safe_id(series_id) / overview['signature']
    folder.mkdir(parents=True, exist_ok=True)
    with STATE_LOCK:
        lock = VIDEO_FILE_LOCKS.setdefault('series-film:' + safe_id(series_id), threading.Lock())
    with lock:
        full = folder / 'complete.mp4'
        if full.is_file() and full.stat().st_size:
            return full
        width, height = _series_dimensions(overview['episodes'][0]['parts'][0]['production_id'])
        episode_files = []
        for episode in overview['episodes']:
            source = build_series_episode(series_id, episode['index'])
            episode_path = folder / f"episode-{episode['index']:02d}.mp4"
            episode_files.append(_normalize_series_file(source, episode_path, width, height))
        _concat_series_files(episode_files, full)
        return full


def build_series_selection(series_id, indices):
    selection = series_selection_outputs(series_id, indices)
    if not selection['all_ready']:
        raise ValueError('Every selected episode needs completed ordered parts before assembly.')
    folder = DATA / 'series_films' / safe_id(series_id) / 'selections' / selection['signature']
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / 'selection.mp4'
    with STATE_LOCK:
        lock = VIDEO_FILE_LOCKS.setdefault('series-selection:' + safe_id(series_id) + ':' + selection['signature'], threading.Lock())
    with lock:
        if output.is_file() and output.stat().st_size:
            return output
        width, height = _series_dimensions(selection['episodes'][0]['parts'][0]['production_id'])
        normalized = []
        for episode in selection['episodes']:
            source = build_series_episode(series_id, episode['index'])
            target = folder / f"episode-{episode['index']:02d}.mp4"
            normalized.append(_normalize_series_file(source, target, width, height))
        _concat_series_files(normalized, output)
        return output


@app.get('/api/series/{series_id}/outputs')
def series_outputs_get(series_id: str):
    return series_outputs(series_id)


@app.post('/api/series/{series_id}/film')
def series_film_build(series_id: str):
    build_series_film(series_id)
    return series_outputs(series_id)


@app.post('/api/series/{series_id}/film/open')
def series_film_open(series_id: str):
    overview = series_outputs(series_id)
    if not overview['final_ready']:
        raise HTTPException(404, 'Assemble the complete script film first.')
    return _open_generated_folder(Path(overview['folder_path']))


@app.get('/api/series/{series_id}/film')
def series_film_get(series_id: str, signature: str, download: bool = False):
    overview = series_outputs(series_id)
    if not overview['final_ready'] or overview['signature'] != signature:
        raise HTTPException(409, 'The project order or adopted takes changed. Assemble this series again.')
    path = DATA / 'series_films' / safe_id(series_id) / signature / 'complete.mp4'
    return FileResponse(path, media_type='video/mp4', filename=f'H3-Series-{safe_id(series_id)}.mp4' if download else None)


@app.post('/api/series/{series_id}/film/episode/{index}')
def series_episode_film_build(series_id: str, index: int):
    build_series_episode(series_id, index)
    return series_outputs(series_id)


@app.post('/api/series/{series_id}/film/episode/{index}/open')
def series_episode_film_open(series_id: str, index: int):
    overview = series_outputs(series_id)
    episode = next((item for item in overview['episodes'] if item['index'] == index), None)
    if not episode or not episode['film_ready']:
        raise HTTPException(404, 'Assemble this episode first.')
    return _open_generated_folder(Path(episode['folder_path']))


@app.get('/api/series/{series_id}/film/episode/{index}')
def series_episode_film_get(series_id: str, index: int, signature: str, download: bool = False):
    overview = series_outputs(series_id)
    episode = next((item for item in overview['episodes'] if item['index'] == index), None)
    if not episode or not episode['film_ready'] or signature not in (episode['signature'], overview['signature']):
        raise HTTPException(409, 'This episode film is not assembled for the current project order.')
    path = Path(episode['file_path'])
    return FileResponse(path, media_type='video/mp4', filename=f'H3-Series-Episode-{index:02d}.mp4' if download else None)


@app.get('/api/series/{series_id}/film/selection/status')
def series_selection_status(series_id: str, episodes: str):
    return series_selection_outputs(series_id, _parse_series_indices(episodes))


@app.post('/api/series/{series_id}/film/selection')
def series_selection_film_build(series_id: str, body: dict):
    if set(body) != {'episode_indices'}:
        raise ValueError('Choose episode_indices only.')
    build_series_selection(series_id, body['episode_indices'])
    return series_selection_outputs(series_id, body['episode_indices'])


@app.post('/api/series/{series_id}/film/selection/open')
def series_selection_film_open(series_id: str, body: dict):
    if set(body) != {'episode_indices'}:
        raise ValueError('Choose episode_indices only.')
    result = series_selection_outputs(series_id, body['episode_indices'])
    if not result['final_ready']:
        raise HTTPException(404, 'Assemble the selected episodes first.')
    return _open_generated_folder(Path(result['folder_path']))


@app.get('/api/series/{series_id}/film/selection/video')
def series_selection_film_get(series_id: str, episodes: str, signature: str, download: bool = False):
    result = series_selection_outputs(series_id, _parse_series_indices(episodes))
    if not result['final_ready'] or result['signature'] != signature:
        raise HTTPException(409, 'The selected episode order or adopted takes changed. Assemble again.')
    return FileResponse(Path(result['file_path']), media_type='video/mp4',
                        filename=f'H3-Series-Selection-{safe_id(series_id)}.mp4' if download else None)


@app.get('/api/productions/{production_id}/keyframe-suggestions')
def production_keyframe_suggestions(production_id: str):
    production = production_manager().get(production_id)
    return {'enabled': production['auto_keyframes_enabled'],
            'suggestions': production_manager().auto_keyframe_suggestions(production_id)}


@app.post('/api/productions/{production_id}/keyframe-suggestions/analyse')
def production_keyframe_analyse(production_id: str, body: dict):
    """Explicit local-LLM review; only recommends stills, never submits image jobs."""
    production = production_manager().assert_active(production_id)
    limit = body.get('limit', 24)
    if type(limit) is not int or not 1 <= limit <= 32:
        raise ValueError('Keyframe suggestion limit must be 1–32.')
    candidates = [segment for segment in production['segments']
                  if not segment.get('keyframe_asset_ids') and segment.get('image_prompt', '').strip()][:limit]
    if not candidates:
        return {'suggestions': [], 'source': 'local_ai', 'warning': None}
    schema = {'type': 'object', 'properties': {'suggestions': {'type': 'array', 'items': {
        'type': 'object', 'properties': {
            'segment_id': {'type': 'string'}, 'needed': {'type': 'boolean'},
            'reason': {'type': 'string'}, 'prompt': {'type': 'string'}},
        'required': ['segment_id', 'needed', 'reason', 'prompt'], 'additionalProperties': False}}},
        'required': ['suggestions'], 'additionalProperties': False}
    system = ('You are a storyboard reference-image planner. Return JSON only. '
              'For each listed clip, decide whether ONE additional still would materially help a video model '
              'understand a new location, distinctive prop, unique composition or complex action. '
              'Skip ordinary scenes already covered by existing references. '
              'Never introduce a person, costume, prop or event absent from the clip and its cards. '
              'For needed=true, write one concise ENGLISH text-to-image prompt with exactly the visible cast '
              'and the specified style; no duplicate characters, labels or text. '
              'For needed=false, use an empty prompt. Do not submit jobs.')
    allowed = {segment['id']: segment for segment in candidates}
    try:
        def generate(model):
            rows = []
            visual_style = ' '.join(filter(None, (
                next((card.get('image_analysis', '') for card in production['cards']['styles']
                      if card.get('image_analysis')), ''),
                production.get('style_bible', ''), production.get('visual_style_custom', ''))))[:1100]
            for start in range(0, len(candidates), 4):
                chunk = candidates[start:start + 4]
                RESOURCES.stage = f'Analysing storyboard keyframes {start + 1}–{start + len(chunk)}'
                def selected_cards(segment):
                    selection = segment.get('card_selection', {})
                    return {kind: [{'name': card['name'], 'description': card.get('description', '')[:220],
                                    'has_reference': bool(card.get('asset_ids'))}
                                   for card in production['cards'][kind]
                                   if card['name'].strip().casefold() in {
                                       value.strip().casefold() for value in selection.get(kind, []) if isinstance(value, str)}][:5]
                            for kind in ('characters', 'wardrobe', 'props', 'environments')}
                payload = {'visual_style': visual_style,
                           'clips': [{'segment_id': segment['id'], 'index': segment['index'],
                                      'setting': segment['setting'][:400], 'story': segment['story'][:800],
                                      'action': segment['action'][:700], 'image_prompt': segment['image_prompt'][:700],
                                      'selected_cards': selected_cards(segment),
                                      'previous_setting': next((item['setting'][:200] for item in production['segments']
                                                                if item['index'] == segment['index'] - 1), '')}
                                     for segment in chunk]}
                answer = client().complete_json(model, system, json.dumps(payload, ensure_ascii=False),
                                                schema, max_tokens=2400, temperature=0.1)
                rows.extend(answer.get('suggestions', []))
            return rows
        proposed = RESOURCES.run_ai(SETTINGS['model'], generate)
        suggestions, seen = [], set()
        for item in proposed:
            if not isinstance(item, dict) or item.get('needed') is not True:
                continue
            segment_id = item.get('segment_id')
            if segment_id not in allowed or segment_id in seen:
                continue
            prompt = item.get('prompt')
            if not isinstance(prompt, str) or not prompt.strip():
                continue
            segment = allowed[segment_id]
            suggestions.append({'segment_id': segment_id, 'index': segment['index'],
                                'reason': str(item.get('reason', ''))[:300], 'prompt': prompt.strip()[:2500]})
            seen.add(segment_id)
        return {'suggestions': suggestions, 'source': 'local_ai', 'warning': None}
    except Exception as exc:
        return {'suggestions': production_manager().auto_keyframe_suggestions(production_id, limit),
                'source': 'local_heuristic', 'warning': 'Local LLM keyframe analysis was unavailable: ' + str(exc)[:300]}


@app.post('/api/productions/{production_id}/segments/{segment_id}/image')
def production_image(production_id: str, segment_id: str, body: dict):
    production = production_manager().assert_active(production_id)
    segment = next((s for s in production['segments'] if s['id'] == safe_id(segment_id)), None)
    if not segment:
        raise ValueError('Production clip not found.')
    spec = {
        'prompt': body.get('prompt', segment.get('image_prompt', '')),
        'name': body.get('name', f"{production['title']} {segment['index']:02d} keyframe"),
        'semantic_role': body.get('semantic_role', 'background'), 'person_id': None,
        'prompt_tag': f"production-{segment['index']:02d}-{segment['id'][:8]}",
        'model': body.get('model', 'z_image_turbo_bf16.safetensors'),
        'width': body.get('width', 768), 'height': body.get('height', 432),
        'seed': body.get('seed', secrets.randbelow(2**32))}
    run = asset_manager().submit(body.get('request_id', str(uuid.uuid4())), spec)
    production_manager().set_image_run(production_id, segment_id, run['id'])
    return run


@app.post('/api/productions/{production_id}/segments/{segment_id}/assets')
async def production_segment_asset(production_id: str, segment_id: str,
                                   file: UploadFile = File(...)):
    production_manager().assert_active(production_id)
    data = await file.read(64 * 1024 * 1024 + 1)
    asset = store_asset(data, file.filename or 'clip-keyframe', file.content_type or '')
    if asset.get('media_type') != 'image':
        raise ValueError('Clip keyframes must be PNG, JPEG or WebP images.')
    production = production_manager().attach_asset(production_id, segment_id, asset)
    return {'production': production, 'asset': asset}


@app.patch('/api/productions/{production_id}/segments/{segment_id}/assets/{asset_id}')
def production_segment_asset_update(production_id: str, segment_id: str, asset_id: str, body: dict):
    if body != {'attached': False}:
        raise ValueError('Set attached to false to remove this keyframe from the clip.')
    return production_manager().detach_asset(production_id, segment_id, asset_id)

@app.post('/api/productions/{production_id}/segments/{segment_id}/image/sync')
def production_image_sync(production_id: str, segment_id: str):
    production = production_manager().get(production_id)
    segment = next((s for s in production['segments'] if s['id'] == safe_id(segment_id)), None)
    if not segment or not segment.get('image_run_id'):
        raise ValueError('This clip has no image request to refresh.')
    run = asset_manager().refresh(segment['image_run_id'])
    if run.get('status') == 'succeeded' and run.get('asset'):
        production = production_manager().attach_asset(production_id, segment_id, run['asset'])
    return {'run': run, 'production': production}


def _production_prompt_instructions(production, segment):
    previous = next((item for item in production.get('segments', [])
                     if item.get('index') == segment['index'] - 1), None)
    timeline = segment.get('cast_timeline') if isinstance(segment.get('cast_timeline'), dict) else {}
    visible_start = [str(name).strip() for name in timeline.get('visible_start', []) if str(name).strip()]
    visible_end = [str(name).strip() for name in timeline.get('visible_end', []) if str(name).strip()]
    stable_cast = bool(visible_start) and visible_start == visible_end
    dense_cast = len(visible_start) >= 4
    return '\n'.join([
        'Turn this production clip into one precise, filmable H3 scene plan.',
        f"Keep exactly one scene and exactly {segment['duration']} seconds.",
        'Preserve all story facts, declared people, card bindings, exact dialogue, output language and ending continuity.',
        'Use every selected character card\'s exact canonical name in action, staging and sound direction; never translate, shorten or replace that name with a role label.',
        'Improve only staging, visible performance, motivated camera and sound detail. Do not add plot events, people, dialogue or extra camera moves.',
        'This materialised clip has one continuous camera setup. Do not propose a reverse shot, cutaway, split screen, inset, montage or repeated view of the cast. Keep all visible actors as separate, non-overlapping silhouettes in one coherent shared space.',
        ('TEMPORAL CAST LOCK: the same visible cast remains on screen from opening through ending: '
         + ', '.join(visible_start)
         + '. Do not write any exit, entrance, move-out-of-frame, disappearance, re-entry, second reveal, replacement body or background duplicate for these identities.'
         if stable_cast else ''),
        ('DENSE ENSEMBLE SAFETY: use a fixed medium-wide or wide master composition from the first frame through the final frame. '
         'Do not start on a close-up and pull back to reveal the cast; do not pan, arc, track or reframe across them. '
         'Assign stable left-to-right screen lanes and move only one primary actor at a time while the others react inside their lanes.'
         if dense_cast else ''),
        'When authored internal timing is supplied, preserve its relative phase boundaries inside the continuous scene.',
        'Fit action density to the duration: overlap compatible supporting reactions in parallel, while preserving causal order for dependent actions.',
        'Be concise: do not repeat the full Character Bible, card library, overview instructions or the same identity block inside action and performance.',
        'Keep exact spoken words only in structured dialogue; never copy or paraphrase them into action.',
        'Treat segment-only keyframes as planning context, never as extra H3 image inputs.',
        ("This clip uses the previous saved motion and audio tail as a protected opening context. "
         "Previous planned final state: " + (previous.get('ending') or 'not specified')
         + ". Direct only the NEW action after that context; do not replay the preceding clip's events or dialogue."
         if production.get('auto_continue_previous') and segment.get('continue_previous', True) and previous else ''),
        ('Apply this user revision request: ' + segment['prompt_direction']) if segment.get('prompt_direction') else
        'Keep the current clip direction and make it concrete without changing its meaning.',
    ]).strip()


@app.post('/api/productions/{production_id}/segments/{segment_id}/prompt')
def production_segment_prompt(production_id: str, segment_id: str, body: dict):
    from .compiler import compile_project
    production = production_manager().assert_active(production_id)
    use_ai = body.get('use_ai', True)
    if type(use_ai) is not bool:
        raise ValueError('use_ai must be true or false.')
    segment = next((item for item in production['segments'] if item['id'] == safe_id(segment_id)), None)
    if not segment:
        raise ValueError('Production clip not found.')
    materialised = production_manager().materialise(production_id, segment_id)
    project = materialised['project']
    started = time.monotonic()
    if use_ai:
        planning_project = copy.deepcopy(project)

        def generate(model):
            lm = client()
            RESOURCES.stage = f"Building video prompt for clip {segment['index']}"
            instructions = _production_prompt_instructions(production, segment)
            if SETTINGS.get('ai_memory_mode') == 'resident_small':
                from .continuation_suggestions import compact_plan
                planning_project.setdefault('simple', {})['directed'] = True
                proposal = compact_plan(lm, model, planning_project, instructions, SETTINGS['persona'])
            else:
                proposal = lm.propose_plan(model, planning_project, instructions, SETTINGS['persona'])
            candidate = merge_plan(planning_project, proposal)
            candidate, compiled = _localise_candidate_for_h3(lm, model, candidate)
            return {'proposal': proposal, 'candidate': candidate, 'compiled': compiled}

        generated = RESOURCES.run_ai(SETTINGS['model'], generate)
        proposal, candidate, compiled = generated['proposal'], generated['candidate'], generated['compiled']
        source = 'local_ai'
    else:
        candidate, proposal, source = project, None, 'compiled'
        compiled = compile_project(candidate)
    if not compiled['valid']:
        errors = [item['message'] for item in compiled['issues'] if item['severity'] == 'error']
        raise ValueError('\n'.join(errors) or 'This clip could not produce a valid H3 prompt.')
    seconds = time.monotonic() - started
    candidate['simple_generation'] = {
        'seconds': round(seconds, 3), 'generated_at': datetime.now(timezone.utc).isoformat(),
        'method': 'ai' if use_ai else 'manual'}
    save_project(candidate)
    production = production_manager().set_segment_prompt(
        production_id, segment_id, compiled['prompt'], source, seconds, candidate['id'])
    return {'production': production, 'project': candidate, 'compiled': compiled,
            'proposal': proposal, 'seconds': round(seconds, 3)}


@app.post('/api/productions/{production_id}/segments/{segment_id}/video')
def production_segment_video(production_id: str, segment_id: str, body: dict):
    from .compiler import compile_project
    production = production_manager().assert_active(production_id)
    segment = next((item for item in production['segments'] if item['id'] == safe_id(segment_id)), None)
    if not segment:
        raise ValueError('Production clip not found.')
    if not segment.get('project_id') or segment.get('status') != 'ready':
        production = production_manager().materialise(production_id, segment_id)['production']
        segment = next(item for item in production['segments'] if item['id'] == safe_id(segment_id))
    project = copy.deepcopy(load_project(segment['project_id']))
    if production_manager().has_inherited_clip_directions(production, project):
        raise ValueError('This video prompt still contains directions inherited from an older clip. Regenerate this clip prompt before generating video.')
    profile = video_workflow_manager().get(project.get('comfy_render', {}).get('workflow_profile_id', 'builtin'))
    if project.get('mode') not in profile['modes']:
        raise ValueError('The selected ComfyUI workflow does not support this clip input mode.')
    render = project.setdefault('comfy_render', {})
    parent_run_id = None
    if production.get('auto_continue_previous'):
        if not profile.get('builtin') or project.get('mode') not in ('ref2va', 'fl2va'):
            raise ValueError('Automatic motion continuation currently needs a built-in H3 reference/first-last workflow. Turn it off for this workflow.')
        render['save_mmh3'] = True
        if segment['index'] > 1 and segment.get('continue_previous', True):
            from .video_timing import frame_budget
            frame_budget(segment['duration'], {
                'continuation_source': 'pending', 'continuation_overlap_frames': 39,
                'duration_basis': 'new_footage'})
            previous = next((item for item in production['segments'] if item['index'] == segment['index'] - 1), None)
            previous_row = next((row for row in production_outputs(production_id)['segments']
                                 if previous and row['segment_id'] == previous['id']), None)
            source = previous_row.get('selected') if previous_row else None
            if (not source or source.get('status') != 'succeeded' or not source.get('can_continue')
                    or not source.get('continuation_source') or source.get('project_id') != previous.get('project_id')):
                raise ValueError('The preceding clip has no adopted take with verified .mmh3 motion state. Render or adopt that clip with automatic continuation enabled, then retry this one.')
            parent_run_id = source['id']
            render.update(continuation_source=source['continuation_source'],
                          continuation_overlap_frames=39, duration_basis='new_footage')
        else:
            for key in ('continuation_source', 'continuation_overlap_frames', 'duration_basis'):
                render.pop(key, None)
    else:
        # A prepared clip may have been rendered while this option was on.
        # Switching it off must not silently reuse the earlier motion source.
        for key in ('continuation_source', 'continuation_overlap_frames', 'duration_basis'):
            render.pop(key, None)
    if body.get('new_seed', True):
        if type(body.get('new_seed', True)) is not bool:
            raise ValueError('new_seed must be true or false.')
        project.setdefault('comfy_render', {})['seed'] = secrets.randbelow(2**32)
        save_project(project)
    compiled = compile_project(project)
    if not compiled['valid']:
        errors = [item['message'] for item in compiled['issues'] if item['severity'] == 'error']
        raise ValueError('\n'.join(errors) or 'This clip needs a valid video prompt before generation.')
    production = production_manager().set_segment_prompt(
        production_id, segment_id, compiled['prompt'], segment.get('video_prompt_source') or 'compiled',
        segment.get('prompt_seconds') or 0, project['id'])
    run = video_manager().submit(body.get('request_id'), project, compiled['prompt'], parent_run_id=parent_run_id)
    production = production_manager().set_video_run(production_id, segment_id, run['id'])
    return {'run': run, 'production': production}

@app.get('/api/stories')
def stories_list():
    return {'stories': story_manager().list()}

@app.post('/api/stories')
def story_create(body: dict):
    return story_manager().create(body)

@app.get('/api/stories/{story_id}')
def story_get(story_id: str):
    return story_manager().get(story_id)

@app.patch('/api/stories/{story_id}')
def story_update(story_id: str, body: dict):
    return story_manager().update(story_id, body)

@app.post('/api/stories/{story_id}/branch')
def story_branch(story_id: str, body: dict):
    return story_manager().branch(story_id, body.get('run_id'), body.get('request_id'))

@app.post('/api/stories/{story_id}/attach')
def story_attach(story_id: str, body: dict):
    return story_manager().attach_run(story_id, body.get('run_id'), body.get('expected_parent'))

@app.post('/api/stories/{story_id}/alternates')
def story_alternate(story_id: str, body: dict):
    return story_manager().register_alternate(story_id, body.get('run_id'), body.get('original_run_id'))

@app.post('/api/stories/{story_id}/turns')
def story_turn_create(story_id: str, body: dict):
    return story_manager().submit(story_id, body)

@app.get('/api/stories/{story_id}/actions')
def story_available_actions(story_id: str, target_id: str | None = None):
    return story_manager().available_actions(story_id, target_id)

@app.post('/api/stories/{story_id}/scene-player')
def story_scene_player(story_id: str, body: dict):
    return story_manager().bind_scene_player(story_id, body)

@app.post('/api/stories/{story_id}/scene-inspection')
def story_scene_inspection(story_id: str, body: dict):
    return story_manager().refresh_scene(story_id, body)

@app.get('/api/video/runs/{run_id}/receipt')
def video_receipt(run_id: str):
    manager = video_manager()
    run = manager.get(safe_id(run_id))
    transfer = manager._load(run_id, 'transfer.json')
    manifest = transfer['manifest']
    graph = transfer['prompt']
    record = manager.records[run_id]
    return {'run_id': run_id, 'request_id': record['request_id'], 'comfy_prompt_id': record.get('prompt_id'),
            'graph_hash': hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest(),
            'manifest': manifest, 'graph': graph, 'status': run['status'], 'video_url': run.get('video_url'),
            'settings': {k: manifest.get(k) for k in ('seed', 'steps', 'resolution', 'width', 'height', 'frames', 'loras')}}

@app.post('/api/stories/{story_id}/preview')
def story_preview(story_id: str, body: dict):
    return story_manager().preview(story_id, body)

@app.get('/api/assistant/requests')
def assistant_pending():
    return story_manager().supervised_requests()

def motion_lab_manager():
    global MOTION_LAB
    from .motion_lab import MotionLabManager
    with STATE_LOCK:
        if MOTION_LAB is None:
            MOTION_LAB = MotionLabManager(DATA, video_manager)
        return MOTION_LAB

@app.get('/api/motion-lab/recipes')
def motion_recipes():
    from .motion_lab import recipes
    return {'recipes': recipes()}

@app.post('/api/motion-lab')
def motion_create(body: dict):
    return motion_lab_manager().create(safe_id(body.get('request_id')), check_project(body.get('project')),
        body.get('settings', {}), body.get('recipe_ids', []), body.get('seeds', []))

@app.get('/api/motion-lab/{comparison_id}')
def motion_get(comparison_id: str):
    return motion_lab_manager().get(safe_id(comparison_id))

@app.post('/api/motion-lab/{comparison_id}/advance')
def motion_advance(comparison_id: str, body: dict):
    return motion_lab_manager().advance(safe_id(comparison_id))

@app.post('/api/motion-lab/{comparison_id}/pause')
def motion_pause(comparison_id: str, body: dict):
    if type(body.get('paused')) is not bool:
        raise ValueError('Paused must be true or false.')
    return motion_lab_manager().set_paused(safe_id(comparison_id), body['paused'])

@app.post('/api/motion-lab/{comparison_id}/rating')
def motion_rating(comparison_id: str, body: dict):
    return motion_lab_manager().rate(safe_id(comparison_id), safe_id(body.get('request_id')), body.get('ratings', {}), body.get('notes', ''))

@app.post('/api/assistant/requests/{request_id}/complete')
def assistant_complete(request_id: str, body: dict):
    return story_manager().complete_supervised(safe_id(request_id), body)

@app.post('/api/stories/{story_id}/turns/{turn_id}/{action}')
def story_turn_action(story_id: str, turn_id: str, action: str, body: dict):
    return story_manager().action(story_id, turn_id, action, body)

@app.post('/api/video/runs/{run_id}/plan-continuation')
def plan_video_continuation(run_id: str, body: dict):
    manager = story_manager()
    run = manager._run(safe_id(run_id))
    if run['status'] != 'succeeded' or not run.get('continuation_source'):
        raise ValueError('Choose a completed take with a saved ending.')
    source = video_manager().snapshot(run['id'])
    story = {'mode': 'studio', 'player_name': '', 'premise': source['story']['text'], 'settings': {'style': ''},
             'branches': {'temporary': manager._lineage(run['id'])}, 'observed_by_run': {}}
    duration = body.get('duration', 5)
    from .stories import settings_for, text
    settings_for({'duration': duration})
    turn = {'branch_id': 'temporary', 'duration': duration, 'message': text(body.get('message', ''), 4000), 'parent_run_id': run['id']}
    if not turn['message']:
        raise ValueError('Describe what happens next or ask the assistant to choose.')
    plan = manager.plan(story, turn, source, video_run_ending_image(run['id']))
    if plan['asset_requests']:
        raise ValueError('This idea needs new images. Use Game to create them automatically, or add references in Studio first.')
    return {'plan': plan, 'source_run_id': run['id']}

@app.get('/api/video/runs/{run_id}/ending')
def video_ending_preview(run_id: str):
    asset = video_run_ending_image(safe_id(run_id))
    return FileResponse(DATA / 'assets' / safe_id(asset['id']) / 'source.png', media_type='image/png')

def scene_video_path(run_id):
    run_id = safe_id(run_id)
    source = cached_run_video(run_id)
    record = video_manager().get(run_id)
    overlap = record.get('overlap_frames')
    if overlap is None:
        # Old records retain their original frame budget; inspect the saved transfer
        # only when playback is requested, never during history polling.
        timing = video_manager()._load(run_id, 'transfer.json')['manifest'].get('mmh3', {})
        overlap = timing.get('overlap_frames', 0) if timing.get('source') else 0
    if not overlap:
        return source
    output = source.parent / f'scene-context-{overlap}.mp4'
    with STATE_LOCK:
        lock = VIDEO_FILE_LOCKS.setdefault('scene:' + run_id, threading.Lock())
    with lock:
        if output.is_file() and output.stat().st_size:
            return output
        temporary = output.with_name(output.stem + '-building.mp4')
        seconds = overlap / 24
        result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source),
            '-vf', f'trim=start_frame={overlap},setpts=PTS-STARTPTS', '-af', f'atrim=start={seconds},asetpts=PTS-STARTPTS',
            '-map_metadata', '-1', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-threads', '4',
            '-c:a', 'aac', '-b:a', '160k', '-movflags', '+faststart', str(temporary)],
            capture_output=True, timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise ValueError('The new-footage preview could not be prepared. The original video is still available.')
        temporary.replace(output)
        return output

@app.get('/api/video/runs/{run_id}/scene')
def video_scene_preview(run_id: str):
    return FileResponse(scene_video_path(run_id), media_type='video/mp4')

@app.get('/api/stories/{story_id}/video')
def story_film(story_id: str):
    story = story_manager().get(story_id)
    clips = story['clips']
    if not clips:
        raise ValueError('Generate a scene before saving the film.')
    if len(clips) > 100:
        raise ValueError('Export a branch with at most 100 clips.')
    digest = hashlib.sha256(json.dumps([r['id'] for r in clips]).encode()).hexdigest()[:20]
    folder = DATA / 'story_films' / safe_id(story_id)
    folder.mkdir(parents=True, exist_ok=True)
    output = folder / (digest + '.mp4')
    with STATE_LOCK:
        lock = VIDEO_FILE_LOCKS.setdefault('film:' + story_id, threading.Lock())
    with lock:
        if not output.is_file():
            width, height = clips[0]['width'], clips[0]['height']
            normalized = []
            for clip in clips:
                path = folder / (clip['id'] + f'-{width}x{height}.mp4')
                if not path.is_file():
                    source = scene_video_path(clip['id'])
                    result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source),
                        '-vf', f'scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps=24,setsar=1',
                        '-map_metadata', '-1', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-threads', '4',
                        '-c:a', 'aac', '-ar', '32000', '-ac', '2', str(path)], capture_output=True, timeout=180,
                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    if result.returncode:
                        path.unlink(missing_ok=True)
                        raise ValueError('The film could not be assembled. Your individual clips are saved.')
                normalized.append(path)
            listing = folder / (digest + '.txt')
            listing.write_text('\n'.join("file '" + p.name + "'" for p in normalized), 'utf-8')
            temporary = output.with_name(digest + '-building.mp4')
            result = subprocess.run(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '1',
                '-i', str(listing), '-c', 'copy', '-map_metadata', '-1', '-movflags', '+faststart', str(temporary)],
                capture_output=True, timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise ValueError('The film export did not finish. Individual clips are still available.')
            temporary.replace(output)
    return FileResponse(output, media_type='video/mp4', filename='H3-Story-' + story_id + '.mp4')

@app.post('/api/gpu/prepare-h3')
def prepare_h3(body: dict):
    return RESOURCES.prepare_h3()

def asset_meta(asset_id):
    path = DATA / 'assets' / safe_id(asset_id) / 'metadata.json'
    if not path.exists():
        raise HTTPException(404, 'This reference file is not in the local library. Add it again or import the portable project.')
    return json.loads(path.read_text(encoding='utf-8'))

def media_probe(path):
    run = subprocess.run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)], capture_output=True, text=True, timeout=20, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if run.returncode:
        raise ValueError('This media file could not be read.')
    return json.loads(run.stdout)

def store_asset(data, name, content_type=''):
    if not data or len(data) > 64 * 1024 * 1024:
        raise ValueError('Each reference must be nonempty and no larger than 64 MB.')
    asset_id = str(uuid.uuid4())
    folder = DATA / 'assets' / asset_id
    folder.mkdir()
    ext = Path(name).suffix.lower()
    if ext in ('.png', '.jpg', '.jpeg', '.webp', '.bmp') or content_type.startswith('image/'):
        try:
            with Image.open(io.BytesIO(data)) as source:
                image = ImageOps.exif_transpose(source).convert('RGB')
                image.thumbnail((4096, 4096))
                image.save(folder / 'source.png')
                width, height = image.size
                image.thumbnail((512, 512))
                image.save(folder / 'thumbnail.jpg', quality=88)
        except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise ValueError('Use a valid PNG, JPEG or WebP image.') from exc
        meta = {'id': asset_id, 'name': Path(name).stem[:100] or 'Reference', 'media_type': 'image', 'filename': 'source.png',
                'width': width, 'height': height, 'duration': None, 'mime': 'image/png'}
    elif ext in ('.mp4', '.webm', '.mov', '.mp3', '.wav', '.m4a', '.flac', '.ogg'):
        filename = 'source' + ext
        (folder / filename).write_bytes(data)
        probe = media_probe(folder / filename)
        video = next((s for s in probe['streams'] if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic')), None)
        audio = next((s for s in probe['streams'] if s.get('codec_type') == 'audio'), None)
        if not video and not audio:
            raise ValueError('No playable audio or video stream was found.')
        from .audio_tools import measured_duration
        duration = measured_duration(folder / filename, probe)
        if not 0 < duration <= 120:
            raise ValueError('Reference clips must be at most two minutes in the library; enable only H3-compatible lengths.')
        meta = {'id': asset_id, 'name': Path(name).stem[:100], 'media_type': 'video' if video else 'audio', 'filename': filename,
                'width': video.get('width') if video else None, 'height': video.get('height') if video else None,
                'duration': duration, 'mime': (('video/webm' if video else 'audio/webm') if ext == '.webm' else
                    ('video/mp4' if video else {'.wav': 'audio/wav', '.flac': 'audio/flac', '.ogg': 'audio/ogg', '.m4a': 'audio/mp4'}.get(ext, 'audio/mpeg')))}
    else:
        raise ValueError('Use images, common video clips, or audio files for references.')
    meta['sha256'] = hashlib.sha256((folder / meta['filename']).read_bytes()).hexdigest()
    atomic_json(folder / 'metadata.json', meta)
    return {**meta, 'role': 'reference_' + meta['media_type'], 'semantic_role': 'other', 'enabled': True,
            'description': '', 'observation': '', 'approved_observation': '', 'locked_order': False}

@app.post('/api/assets')
async def upload_asset(file: UploadFile = File(...)):
    data = await file.read(64 * 1024 * 1024 + 1)
    return store_asset(data, file.filename or 'image.png', file.content_type or '')

@app.post('/api/assets/from-data')
def data_asset(body: dict):
    data_url = body.get('data_url', '')
    if not isinstance(data_url, str) or len(data_url) > 24_000_000 or not data_url.startswith(('data:image/png;base64,', 'data:image/jpeg;base64,', 'data:image/webp;base64,')):
        raise ValueError('The ComfyUI bridge must send a bounded PNG/JPEG/WebP image.')
    try:
        data = base64.b64decode(data_url.split(',', 1)[1], validate=True)
    except ValueError:
        raise ValueError('Invalid image encoding.')
    return store_asset(data, body.get('name', 'Comfy reference.png'), data_url[5:].split(';')[0])

@app.get('/api/assets/{asset_id}/{variant}')
def get_asset(asset_id: str, variant: str):
    meta = asset_meta(asset_id)
    filename = 'thumbnail.jpg' if variant == 'thumbnail' and meta['media_type'] == 'image' else meta['filename']
    return FileResponse(DATA / 'assets' / safe_id(asset_id) / filename)

@app.get('/api/audio/capabilities')
def audio_capabilities():
    from .audio_tools import transcription_capabilities
    return transcription_capabilities()

@app.post('/api/audio/transcribe')
def audio_transcribe(body: dict):
    from .audio_tools import transcribe
    asset_id = safe_id(body.get('asset_id'))
    meta = asset_meta(asset_id)
    if meta['media_type'] not in ('audio', 'video'):
        raise ValueError('Choose a recording or audio clip to transcribe.')
    return transcribe(DATA / 'assets' / asset_id / meta['filename'], body.get('options', {}))

@app.post('/api/video/runs/{run_id}/soundtrack')
def video_soundtrack(run_id: str, body: dict):
    from .audio_tools import mix_soundtrack
    request_id = safe_id(body.get('request_id'))
    source = scene_video_path(safe_id(run_id))
    supplied = body.get('tracks', [])
    if not isinstance(supplied, list) or len(supplied) > 8:
        raise ValueError('Choose at most eight soundtrack layers.')
    tracks = []
    for track in supplied:
        aid = safe_id(track.get('asset_id'))
        meta = asset_meta(aid)
        if meta['media_type'] not in ('audio', 'video'):
            raise ValueError('Soundtrack layers need audio or video files.')
        tracks.append({**{k: v for k, v in track.items() if k in ('start_seconds', 'end_seconds', 'offset_seconds', 'gain', 'fade_in', 'fade_out', 'duck')},
                       'path': str(DATA / 'assets' / aid / meta['filename'])})
    folder = DATA / 'soundtracks' / run_id
    folder.mkdir(parents=True, exist_ok=True)
    request_path = folder / (request_id + '.json')
    with STATE_LOCK:
        lock = VIDEO_FILE_LOCKS.setdefault('soundtrack:' + request_id, threading.Lock())
    with lock:
        if request_path.exists() and json.loads(request_path.read_text('utf-8')) != supplied:
            raise ValueError('This soundtrack request already belongs to different layers.')
        atomic_json(request_path, supplied)
        target = folder / (request_id + '.mp4')
        result = mix_soundtrack(source, tracks, target) if not target.exists() else {'original_audio_preserved': True}
    return {**{k: v for k, v in result.items() if k != 'path'}, 'video_url': f'/api/video/runs/{run_id}/soundtrack/{request_id}', 'request_id': request_id}

@app.get('/api/video/runs/{run_id}/soundtrack/{request_id}')
def soundtrack_playback(run_id: str, request_id: str):
    path = DATA / 'soundtracks' / safe_id(run_id) / (safe_id(request_id) + '.mp4')
    if not path.is_file():
        raise HTTPException(404, 'The soundtrack is not ready.')
    return FileResponse(path, media_type='video/mp4')

def image_data(asset_id):
    meta = asset_meta(asset_id)
    if meta['media_type'] != 'image':
        raise ValueError('Vision analysis handles images. Describe audio/video references manually; the selected VLM does not hear them.')
    with Image.open(DATA / 'assets' / asset_id / meta['filename']) as source:
        image = source.convert('RGB')
        image.thumbnail((896, 896))
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG', quality=90)
    return 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode()

@app.post('/api/compile')
def compile_api(body: dict):
    from .compiler import compile_project
    project = check_project(body.get('project', body))
    return compile_project(project)

@app.post('/api/ai/analyse')
def analyse(body: dict):
    asset = body.get('asset', {})
    data_url = image_data(safe_id(asset.get('id')))
    start = time.monotonic()
    observation = RESOURCES.run_ai(SETTINGS['model'], lambda model: client().analyse_image(model, data_url, asset))
    return {'observation': observation, 'seconds': time.monotonic() - start}

@app.post('/api/ai/plan')
def plan(body: dict):
    from .compiler import compile_project
    project = check_project(body.get('project'))
    alias_codes = {'invalid_reference_tag', 'duplicate_reference_tag', 'unknown_reference_tag', 'disabled_reference_tag', 'inactive_reference_tag'}
    alias_errors = [i['message'] for i in compile_project(project)['issues'] if i['code'] in alias_codes and i['severity'] == 'error']
    if alias_errors:
        raise ValueError('\n'.join(alias_errors))
    start = time.monotonic()
    planning_project = copy.deepcopy(project)
    observations = []
    def generate(model):
        lm = client()
        if body.get('vision', False):
            images = [a for a in planning_project['assets'] if a.get('enabled') and a.get('media_type') == 'image']
            # Analyse style references first. Their transferable treatment is the
            # visual authority, while depicted people/objects/locations stay excluded.
            images.sort(key=lambda asset: (asset.get('semantic_role') != 'style', asset.get('semantic_role') != 'palette'))
            if len(images) > 12:
                raise ValueError('Select at most twelve images for one small-model planning pass; other images can stay in the library.')
            for index, asset in enumerate(images):
                if asset.get('approved_observation') and not body.get('refresh_vision'):
                    continue
                RESOURCES.stage = f'Reading image {index + 1} of {len(images)}'
                observation = lm.analyse_image(model, image_data(safe_id(asset['id'])), asset)
                asset['observation'] = observation['observation']
                asset['approved_observation'] = observation['observation']
                observations.append({'asset_id': asset['id'], 'name': asset['name'], **observation})
        RESOURCES.stage = 'Building the scene plan'
        if SETTINGS.get('ai_memory_mode') == 'resident_small':
            from .continuation_suggestions import compact_plan
            planning_project.setdefault('simple', {})['directed'] = True
            proposal = compact_plan(lm, model, planning_project, body.get('instructions', ''), body.get('persona', SETTINGS['persona']))
        else:
            proposal = lm.propose_plan(model, planning_project, body.get('instructions', ''), body.get('persona', SETTINGS['persona']))
        candidate = merge_plan(planning_project, proposal)
        candidate, compiled = _localise_candidate_for_h3(lm, model, candidate)
        return {'proposal': proposal, 'candidate': candidate, 'compiled': compiled}
    generated = RESOURCES.run_ai(SETTINGS['model'], generate)
    proposal, candidate, compiled = generated['proposal'], generated['candidate'], generated['compiled']
    return {'candidate': candidate, 'proposal': proposal, 'compiled': compiled, 'observations': observations, 'seconds': time.monotonic() - start,
            'notice': 'Review the suggested image observations and scene plan before applying. Accepting approves the shown observations for prompting. Source story, reference identities and exact dialogue were preserved; scene meaning still needs your review.'}

@app.post('/api/ai/assist')
def assist(body: dict):
    from .compiler import compile_project
    project = check_project(body.get('project'))
    if body.get('field') not in ALLOWED_SHOT_FIELDS - {'visible_subject_ids', 'offscreen_subject_ids', 'transition'}:
        raise ValueError('This field is protected from AI replacement.')
    if not any(s.get('id') == body.get('shot_id') for s in project['shots']):
        raise ValueError('The selected shot no longer exists.')
    start = time.monotonic()
    def generate(model):
        lm = client()
        proposal = lm.assist(model, project, body['shot_id'], body['field'], body.get('instructions', ''), body.get('persona', SETTINGS['persona']))
        candidate = merge_assist(project, body['shot_id'], body['field'], proposal['value'])
        candidate, compiled = _localise_candidate_for_h3(lm, model, candidate)
        return {'proposal': proposal, 'candidate': candidate, 'compiled': compiled}
    generated = RESOURCES.run_ai(SETTINGS['model'], generate)
    return {'candidate': generated['candidate'], 'proposal': generated['proposal'],
            'compiled': generated['compiled'], 'seconds': time.monotonic() - start}

@app.get('/api/projects/{project_id}/export')
def export_project(project_id: str):
    project = load_project(project_id)
    destination = DATA / 'exports' / f'{safe_id(project_id)}.h3studio.zip'
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('project.json', json.dumps(project, ensure_ascii=False, indent=2))
        for asset in project['assets']:
            meta = asset_meta(asset['id'])
            folder = DATA / 'assets' / safe_id(asset['id'])
            for name in (meta['filename'], 'metadata.json', 'thumbnail.jpg'):
                if (folder / name).exists():
                    archive.write(folder / name, f'assets/{asset["id"]}/{name}')
    return FileResponse(destination, filename=(project['title'][:80] or 'H3 project') + '.h3studio.zip')

@app.post('/api/projects/import')
async def import_project(file: UploadFile = File(...)):
    data = await file.read(128 * 1024 * 1024 + 1)
    if len(data) > 128 * 1024 * 1024:
        raise ValueError('Portable project is larger than 128 MB.')
    if not (file.filename or '').lower().endswith('.zip'):
        project = check_project(json.loads(data))
        for asset in project['assets']:
            actual = asset_meta(asset['id'])
            if asset['media_type'] != actual['media_type']:
                raise ValueError('A reference media type does not match its stored file.')
        project['id'] = str(uuid.uuid4())
        save_project(project)
        return project
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if sum(i.file_size for i in archive.infolist()) > 512 * 1024 * 1024:
            raise ValueError('Expanded project archive is too large.')
        project = check_project(json.loads(archive.read('project.json')))
        id_map = {}
        for asset in project['assets']:
            old_id = safe_id(asset['id'])
            meta = json.loads(archive.read(f'assets/{old_id}/metadata.json'))
            filename = meta.get('filename', '')
            if Path(filename).name != filename or '/' in filename or '\\' in filename:
                raise ValueError('Invalid portable asset filename.')
            imported = store_asset(archive.read(f'assets/{old_id}/{filename}'), meta['name'] + Path(filename).suffix, meta.get('mime', ''))
            if asset['media_type'] != imported['media_type']:
                raise ValueError('A portable reference media type does not match its actual file.')
            id_map[old_id] = imported['id']
            asset.update({k: imported[k] for k in ('id', 'filename', 'sha256', 'width', 'height', 'duration', 'mime')})
        for subject in project['subjects']:
            subject['asset_ids'] = [id_map.get(a, a) for a in subject.get('asset_ids', [])]
        project['id'] = str(uuid.uuid4())
        save_project(project)
        return project

@app.get('/{path:path}')
def frontend(path: str):
    candidate = (ROOT / 'dist' / path).resolve()
    base = (ROOT / 'dist').resolve()
    if not candidate.is_relative_to(base):
        raise HTTPException(404)
    if candidate.is_file():
        return FileResponse(candidate, media_type='application/javascript' if candidate.suffix.lower() in ('.js', '.mjs') else None)
    index = base / 'index.html'
    if index.exists():
        return FileResponse(index)
    return Response('Build the frontend first: npm run build in frontend.', status_code=503)
