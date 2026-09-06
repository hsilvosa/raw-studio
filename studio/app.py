import copy
import json
import math
import os
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import library, model
from .darktable import Darktable
from .recipes import make_stack, merge, profiles, validate_proposal
from .settings import (
    ROOT, STATE, PHOTO_ROOT, OUTPUT, RAW_EXTENSIONS, CAMERA_RAW_EXTENSIONS,
    DEFAULT_PHOTO_ROOT, DARKTABLE, save_config
)

def get_system_drives():
    drives = []
    if os.name == 'nt':
        import string
        for letter in string.ascii_uppercase:
            dp = Path(f'{letter}:\\')
            if dp.exists():
                drives.append(str(dp))
    else:
        drives.append('/')
    return drives

engine = Darktable()
pool = ThreadPoolExecutor(max_workers=1)
jobs = {}
job_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app):
    yield
    pool.shutdown(wait=True)
    engine.close()


app = FastAPI(title='Local RAW Studio', lifespan=lifespan)


@app.middleware('http')
async def local_requests(request: Request, call_next):
    # Browser-origin protection: local files never become a public file server.
    host = request.url.hostname
    origin = request.headers.get('origin')
    if host not in {'127.0.0.1', 'localhost', 'testserver'}:
        return JSONResponse({'detail': 'Local access only'}, status_code=403)
    if origin and (urlparse(origin).netloc != request.headers.get('host')):
        return JSONResponse({'detail': 'Origin not allowed'}, status_code=403)
    return await call_next(request)


@app.exception_handler(ValueError)
async def invalid(request, exc):
    return JSONResponse({'detail': str(exc)}, status_code=400)


def update(identifier, **values):
    with job_lock:
        jobs[identifier].update(values)


def submit(fn, *args):
    identifier = uuid.uuid4().hex
    with job_lock:
        jobs[identifier] = {'id': identifier, 'state': 'queued', 'message': 'Queued'}
    def run():
        try:
            update(identifier, state='running', message='Preparing image')
            result = fn(identifier, *args)
            update(identifier, state='done', message='Done', result=result)
        except Exception as exc:
            update(identifier, state='error', message=str(exc))
    pool.submit(run)
    return {'job_id': identifier}


def base_preview(image):
    path = STATE / 'previews' / (image['id'] + '.png')
    if not path.exists():
        engine.export(Path(image['copy']), [], path, 1200)
    return path


def import_images(job_id, paths):
    results = []
    for index, path in enumerate(paths):
        update(job_id, message=f'Importing {index + 1} of {len(paths)}')
        image = library.add(path)
        base_preview(image)
        results.append(image)
    return results


class ImportRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=100)


class SetDefaultRootRequest(BaseModel):
    path: str


class DeleteImagesRequest(BaseModel):
    image_ids: list[str] = Field(min_length=1)



class DevelopRequest(BaseModel):
    image_id: str
    profile_id: str
    intensity: float = Field(default=.8, ge=0, le=1)
    exposure_offset: float = Field(default=0, ge=-2, le=2)
    adapt: bool = True
    use_model: bool = False
    intent: str = Field(default='Preserve style and adapt intensity to the scene.', max_length=1500)
    width: int = Field(default=1600, ge=600, le=6000)


def develop(job_id, request):
    image = library.get(request.image_id)
    profile = next((p for p in profiles() if p['id'] == request.profile_id), None)
    if profile is None:
        raise ValueError('Unknown profile')
    source = Path(image['copy'])
    if library.digest(source) != image['sha256']:
        raise ValueError('The RAW copy has changed; please re-import the original')
    preview = base_preview(image)
    measurements = library.measure(preview)
    exposure = 0.
    if request.adapt:
        # Bounded technical correction; dark scenes are never normalized to daylight.
        exposure = max(-.5, min(1.25, math.log2(.22 / max(.03, measurements['median']))))
        if measurements['white_fraction'] > .015:
            exposure = min(exposure, .25)
    exposure = max(-2., min(4., exposure + request.exposure_offset))
    stack = make_stack(profile, exposure, request.intensity)
    stack.append({'operation': 'sharpen', 'params': {'amount': .45, 'radius': .7, 'threshold': 1.}})
    folder = STATE / 'renders' / job_id
    candidate = folder / 'preview.png'
    update(job_id, message='Developing with darktable')
    engine.export(source, stack, candidate, request.width)
    reason = 'Profile applied with bounded tone correction.' if request.adapt else 'Profile applied without automatic tone adaptation.'
    model_name = None
    proposal = None
    if request.use_model:
        update(job_id, message='Local model is proposing adjustments')
        # Render a small model input through darktable, never an image generator.
        model_preview = folder / 'model-input.png'
        engine.export(source, stack, model_preview, 768)
        proposal, model_name = model.propose(model_preview, stack, library.measure(candidate), profile['title'] + '. ' + request.intent)
        stack = merge(stack, proposal)
        update(job_id, message='Applying model recipe in darktable')
        candidate = folder / 'adapted.png'
        engine.export(source, stack, candidate, request.width)
        reason = proposal.reason
    after = library.measure(candidate)
    warnings = []
    if after['white_fraction'] > .02:
        warnings.append('Highlights are near white: check specular reflections and signs.')
    if after['black_fraction'] > .15:
        warnings.append('Deep shadows detected: verify shadow detail meets expectations.')
    recipe = {'image_id': image['id'], 'source_sha256': image['sha256'], 'profile_id': profile['id'],
              'profile_title': profile['title'], 'request': request.model_dump(), 'stack': stack,
              'reason': reason, 'model': model_name, 'proposal': proposal.model_dump() if proposal else None,
              'before': measurements, 'after': after, 'warnings': warnings,
              'preview': str(candidate), 'renderer': 'darktable-mcp'}
    library.save_json(folder / 'recipe.json', recipe)
    return {'render_id': job_id, 'url': f'/api/renders/{job_id}/image', 'recipe': recipe}


@app.get('/api/status')
def status():
    return {'darktable': DARKTABLE.is_file(), 'model': model.status(), 'root': str(PHOTO_ROOT)}


@app.get('/api/profiles')
def get_profiles():
    return profiles()


@app.get('/api/images')
def get_images():
    return library.all_images()


@app.get('/api/renders')
def get_renders():
    results = []
    for path in sorted((STATE / 'renders').glob('*/recipe.json'), key=lambda p: p.stat().st_mtime):
        recipe = json.loads(path.read_text(encoding='utf-8'))
        results.append({'render_id': path.parent.name, 'url': f'/api/renders/{path.parent.name}/image', 'recipe': recipe})
    return results


@app.api_route('/api/renders/clear', methods=['GET', 'POST'])
def clear_renders():
    renders_dir = STATE / 'renders'
    count = 0
    if renders_dir.exists():
        for item in renders_dir.iterdir():
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
                count += 1
    return {'deleted': count, 'message': f'Cleared {count} developed renders'}


@app.get('/api/browse')
def browse(path: str = ''):
    if not path or path == '__default__':
        folder = Path(library.PHOTO_ROOT or DEFAULT_PHOTO_ROOT).resolve()
    else:
        folder = Path(path).resolve()

    if not folder.is_dir():
        raise ValueError(f'Folder not found: {path}')
    if folder.is_relative_to(STATE):
        raise ValueError('Cannot browse internal state folder')

    try:
        library.set_photo_root(folder)
    except Exception:
        pass

    items = []
    try:
        entries = sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        raise ValueError(f'Permission denied accessing: {folder}')

    for p in entries:
        if p.name.startswith('.') or p.is_symlink():
            continue
        try:
            if p.is_dir():
                items.append({
                    'name': p.name,
                    'path': str(p),
                    'directory': True,
                    'is_raw': False,
                    'extension': '',
                    'size': None,
                })
            else:
                ext = p.suffix.lower()
                if ext in RAW_EXTENSIONS or ext in CAMERA_RAW_EXTENSIONS:
                    stat = p.stat()
                    items.append({
                        'name': p.name,
                        'path': str(p),
                        'directory': False,
                        'is_raw': ext in CAMERA_RAW_EXTENSIONS,
                        'extension': ext,
                        'size': stat.st_size,
                    })
        except Exception:
            continue

    has_parent = folder.parent != folder
    parent_path = str(folder.parent) if has_parent else None

    return {
        'path': str(folder),
        'parent': parent_path,
        'default_root': str(DEFAULT_PHOTO_ROOT),
        'is_default': str(folder) == str(DEFAULT_PHOTO_ROOT),
        'drives': get_system_drives(),
        'items': items,
    }


@app.get('/api/browse/thumbnail')
def browse_thumbnail(path: str):
    try:
        thumb_path = library.get_browser_thumbnail(path)
        return FileResponse(thumb_path, media_type='image/jpeg')
    except Exception as exc:
        raise HTTPException(404, str(exc))


@app.post('/api/browse/set-default')
def set_default_root(req: SetDefaultRootRequest):
    folder = Path(req.path).resolve()
    if not folder.is_dir():
        raise ValueError('Folder does not exist')
    if folder.is_relative_to(STATE):
        raise ValueError('Cannot use state folder as photo root')
    save_config({'default_photo_root': str(folder)})
    library.set_photo_root(folder)
    return {'default_root': str(folder), 'message': f'Default photo folder set to {folder}'}


@app.post('/api/import')
def import_route(request: ImportRequest):
    if not request.paths:
        raise ValueError('No files selected for import')
    # If paths are outside current library.PHOTO_ROOT, adjust PHOTO_ROOT to their common parent
    parents = [Path(p).resolve().parent for p in request.paths if Path(p).resolve().exists()]
    if parents:
        try:
            import os
            common = Path(os.path.commonpath([str(p) for p in parents]))
            if library.PHOTO_ROOT is None or not common.is_relative_to(library.PHOTO_ROOT):
                library.PHOTO_ROOT = common
        except Exception:
            pass
    # Validate all paths before starting a potentially long import.
    for path in request.paths:
        library.inside(path)
    return submit(import_images, request.paths)



@app.get('/api/images/{identifier}/preview')
def image_preview(identifier: str):
    library.get(identifier)
    path = STATE / 'previews' / (identifier + '.png')
    if not path.is_file():
        raise HTTPException(404, 'Preview pending')
    return FileResponse(path)


@app.post('/api/images/delete')
def delete_images_route(request: DeleteImagesRequest):
    deleted = library.delete_images(request.image_ids)
    return {'deleted': deleted, 'count': len(deleted), 'message': f'Removed {len(deleted)} photo(s) from library'}


@app.delete('/api/images/{identifier}')
def delete_single_image(identifier: str):
    deleted = library.delete_images([identifier])
    if not deleted:
        raise HTTPException(404, 'Image not found')
    return {'deleted': deleted, 'message': 'Photo removed from library'}



@app.post('/api/develop')
def develop_route(request: DevelopRequest):
    library.get(request.image_id)
    return submit(develop, request)


@app.get('/api/jobs/{identifier}')
def job_status(identifier: str):
    with job_lock:
        if identifier not in jobs:
            raise HTTPException(404, 'Job not found')
        return copy.deepcopy(jobs[identifier])


def recipe_path(identifier):
    if len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
        raise ValueError('Invalid identifier')
    path = STATE / 'renders' / identifier / 'recipe.json'
    if not path.is_file():
        raise HTTPException(404, 'Result not found')
    return path


@app.get('/api/renders/{identifier}/image')
def rendered_image(identifier: str):
    recipe = json.loads(recipe_path(identifier).read_text(encoding='utf-8'))
    preview = Path(recipe['preview'])
    if not preview.is_file():
        candidate = STATE / 'renders' / identifier / preview.name
        if candidate.is_file():
            preview = candidate
    return FileResponse(preview)


@app.post('/api/renders/{identifier}/export')
def export_route(identifier: str):
    recipe = json.loads(recipe_path(identifier).read_text(encoding='utf-8'))
    return submit(export_render, recipe)


def export_render(job_id, recipe):
    image = library.get(recipe['image_id'])
    if library.digest(Path(image['copy'])) != image['sha256']:
        raise ValueError('The RAW copy has changed')
    target = OUTPUT / recipe['profile_id'] / ('studio_' + image['id'])
    target.mkdir(parents=True, exist_ok=True)
    # Fixed per-image location, immutable filenames; no folder per iteration.
    output = target / (job_id[:12] + '.png')
    update(job_id, message='Exporting PNG at 6000 pixels')
    engine.export(Path(image['copy']), recipe['stack'], output, 6000)
    library.save_json(output.with_suffix('.json'), recipe)
    return {'path': str(output)}


app.mount('/', StaticFiles(directory=ROOT / 'studio' / 'static', html=True), name='ui')
