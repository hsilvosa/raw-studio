import copy
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
import numpy as np
from PIL import Image
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import library, model, adaptation
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
        if identifier in jobs:
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


class SetFavoriteRequest(BaseModel):
    favorite: bool


class SetTagsRequest(BaseModel):
    tags: list[str] = Field(default_factory=list)


class BatchTagsRequest(BaseModel):
    image_ids: list[str] = Field(min_length=1)
    add_tags: list[str] = Field(default_factory=list)
    remove_tags: list[str] = Field(default_factory=list)


class BatchFolderRequest(BaseModel):
    image_ids: list[str] = Field(min_length=1)
    folder: str


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
    telemetry = adaptation.analyze_scene(preview)
    base_stack = make_stack(profile, request.exposure_offset, request.intensity)

    if request.adapt:
        stack, reason, measurements = adaptation.adapt_stack(base_stack, telemetry, request.exposure_offset, request.intensity)
    else:
        stack = base_stack
        stack.append({'operation': 'sharpen', 'params': {'amount': .45, 'radius': .7, 'threshold': 1.}})
        reason = 'Profile applied without automatic tone adaptation.'
        measurements = telemetry

    folder = STATE / 'renders' / job_id
    candidate = folder / 'preview.png'
    update(job_id, message='Developing with darktable')
    engine.export(source, stack, candidate, request.width)

    is_prompt_style = (profile['id'] == '00_PROMPT_IA')
    use_model = request.use_model or is_prompt_style

    model_name = None
    proposal = None
    model_warning = None
    if use_model:
        update(job_id, message='Local model is creating prompt style' if is_prompt_style else 'Local model is proposing adjustments')
        # Render a small model input through darktable, never an image generator.
        model_preview = folder / 'model-input.png'
        engine.export(source, stack, model_preview, 768)
        try:
            prompt_intent = (request.intent or '').strip()
            if is_prompt_style:
                if not prompt_intent or prompt_intent == 'Preserve style and adapt intensity to the scene.':
                    prompt_intent = 'Estilo fotográfico cinematográfico y armónico con paleta rica y tonos cuidados.'
            else:
                prompt_intent = profile['title'] + (f'. {prompt_intent}' if prompt_intent else '')

            proposal, model_name = model.propose(model_preview, stack, telemetry, prompt_intent, is_prompt_style=is_prompt_style)
            stack = merge(stack, proposal)
            update(job_id, message='Applying model recipe in darktable')
            adapted_candidate = folder / 'adapted.png'
            engine.export(source, stack, adapted_candidate, request.width)
            candidate = adapted_candidate
            reason = proposal.reason
        except Exception as exc:
            model_warning = f'Modelo local no pudo aplicarse ({exc}); se mantuvo el revelado adaptativo.'

    after = adaptation.analyze_scene(candidate)
    warnings = []
    if model_warning:
        warnings.append(model_warning)
    if after['white_fraction'] > .02:
        warnings.append('Highlights are near white: check specular reflections and signs.')
    if after['black_fraction'] > .15:
        warnings.append('Deep shadows detected: verify shadow detail meets expectations.')
    if after.get('skin_detected'):
        warnings.append(f"Skin tones present ({after['skin_fraction']*100:.1f}%): natural skin palette preserved.")

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


@app.post('/api/images/batch/tags')
def batch_image_tags_route(req: BatchTagsRequest):
    if req.add_tags:
        library.add_tags(req.image_ids, req.add_tags)
    if req.remove_tags:
        library.remove_tags(req.image_ids, req.remove_tags)
    return {'count': len(req.image_ids), 'message': f'Updated tags for {len(req.image_ids)} photo(s)'}


@app.post('/api/images/batch/folder')
def batch_image_folder_route(req: BatchFolderRequest):
    res = library.set_folder(req.image_ids, req.folder)
    return {'count': len(req.image_ids), 'folder': res['folder'], 'message': f'Moved {len(req.image_ids)} photo(s) to folder {res["folder"]}'}


@app.get('/api/library/metadata')
def library_metadata_route():
    return library.get_library_metadata()


@app.delete('/api/images/{identifier}')
def delete_single_image(identifier: str):
    deleted = library.delete_images([identifier])
    if not deleted:
        raise HTTPException(404, 'Image not found')
    return {'deleted': deleted, 'message': 'Photo removed from library'}


@app.post('/api/images/{identifier}/favorite')
def set_image_favorite_route(identifier: str, req: SetFavoriteRequest):
    return library.set_favorite(identifier, req.favorite)


@app.post('/api/images/{identifier}/tags')
def set_image_tags_route(identifier: str, req: SetTagsRequest):
    return library.set_tags(identifier, req.tags)



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




@app.get('/api/reports/evaluation')
def get_evaluation_report():
    report_file = STATE / 'reports' / 'evaluation_report.html'
    if not report_file.is_file():
        raise HTTPException(404, 'Evaluation report not yet generated. Run scripts/evaluate-model.py first.')
    return FileResponse(report_file, media_type='text/html')


@app.get('/api/reports/img')
def get_report_img(p: str):
    file_path = Path(p).resolve()
    # Security: must reside inside STATE directory
    if not str(file_path).startswith(str(STATE.resolve())) or not file_path.is_file():
        raise HTTPException(403, 'Forbidden image path')
    return FileResponse(file_path, media_type='image/png')


@app.get('/api/renders/{identifier}/image')
def rendered_image(identifier: str):
    folder = STATE / 'renders' / identifier
    recipe = json.loads(recipe_path(identifier).read_text(encoding='utf-8'))
    preview = Path(recipe['preview'])
    if not preview.is_file():
        candidate = folder / preview.name
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
    profile = recipe.get('profile_id', 'custom')
    target = OUTPUT / profile
    target.mkdir(parents=True, exist_ok=True)
    image_stem = Path(image['name']).stem
    output = target / f"{image_stem}_{profile}.png"
    update(job_id, message=f'Exporting {output.name} at 6000 pixels')
    engine.export(Path(image['copy']), recipe['stack'], output, 6000)
    library.save_json(output.with_suffix('.json'), recipe)
    return {'path': str(output), 'folder': str(target), 'filename': output.name}


@app.post('/api/renders/batch-export')
def export_batch_route(data: dict):
    render_ids = data.get('render_ids', [])
    if not render_ids:
        raise HTTPException(400, 'No renders provided')
    recipes = []
    for rid in render_ids:
        r_file = recipe_path(rid)
        if r_file.is_file():
            try:
                recipes.append(json.loads(r_file.read_text(encoding='utf-8')))
            except Exception:
                pass
    if not recipes:
        raise HTTPException(404, 'No valid render recipes found')
    return submit(export_batch_renders, recipes)


def export_batch_renders(job_id, recipes):
    total = len(recipes)
    exported = []
    last_folder = str(OUTPUT)
    for idx, recipe in enumerate(recipes, 1):
        try:
            image = library.get(recipe['image_id'])
            if library.digest(Path(image['copy'])) != image['sha256']:
                continue
            profile = recipe.get('profile_id', 'custom')
            target = OUTPUT / profile
            target.mkdir(parents=True, exist_ok=True)
            image_stem = Path(image['name']).stem
            output = target / f"{image_stem}_{profile}.png"
            last_folder = str(target)
            update(job_id, message=f'[{idx}/{total}] Exporting {output.name} at 6000 pixels')
            engine.export(Path(image['copy']), recipe['stack'], output, 6000)
            library.save_json(output.with_suffix('.json'), recipe)
            exported.append(str(output))
        except Exception as e:
            update(job_id, message=f'[{idx}/{total}] Error exporting recipe: {str(e)}')
    return {'exported': exported, 'count': len(exported), 'path': exported[-1] if exported else '', 'folder': last_folder}


def open_in_file_manager(target: Path):
    """
    Opens the operating system file manager to the folder containing target (or target itself if dir).
    """
    folder = target if target.is_dir() else target.parent
    folder.mkdir(parents=True, exist_ok=True)
    folder_str = str(folder.resolve())

    if sys.platform == 'win32':
        if target.is_file() and target.exists():
            try:
                # IMPORTANT: In Windows, /select,"<path>" MUST NOT have quotes around /select,
                # Using a raw string avoids subprocess.list2cmdline putting quotes around "/select,path"
                subprocess.Popen(f'explorer.exe /select,"{str(target.resolve())}"')
                return True
            except Exception:
                pass
        try:
            os.startfile(folder_str)
            return True
        except Exception:
            pass
        try:
            subprocess.Popen(f'explorer.exe "{folder_str}"')
            return True
        except Exception:
            pass
        return False
    elif sys.platform == 'darwin':
        if target.is_file():
            subprocess.Popen(['open', '-R', str(target)])
        else:
            subprocess.Popen(['open', folder_str])
        return True
    else:
        subprocess.Popen(['xdg-open', folder_str])
        return True





@app.post('/api/system/open-folder')
def open_folder_route(data: dict):
    path_str = data.get('path', '').strip()
    target = None
    if path_str:
        p = Path(path_str).resolve()
        if p.exists():
            target = p
        elif p.parent.exists():
            target = p.parent
    if target is None:
        target = OUTPUT
    if not target.is_file():
        target.mkdir(parents=True, exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)

    folder = target if target.is_dir() else target.parent
    try:
        open_in_file_manager(target)
        return {'status': 'ok', 'opened': str(target), 'folder': str(folder.resolve())}
    except Exception as e:
        raise HTTPException(500, f'Cannot open file browser: {str(e)}')





app.mount('/', StaticFiles(directory=ROOT / 'studio' / 'static', html=True), name='ui')
