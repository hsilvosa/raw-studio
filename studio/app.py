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
from . import library, model, adaptation, zones, kirkify
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
    response = await call_next(request)
    # Prevent browser caching of HTML, CSS, JS and API responses in local dev
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    return response


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




class ZoneAdjustRequest(BaseModel):
    zones: dict[str, dict] = Field(default_factory=dict)
    manual_masks: dict[str, dict] = Field(default_factory=dict)


@app.get('/api/renders/{identifier}/zones')
def get_render_zones(identifier: str):
    folder = STATE / 'renders' / identifier
    if not folder.is_dir():
        raise HTTPException(404, 'Render not found')
    recipe = json.loads(recipe_path(identifier).read_text(encoding='utf-8'))
    zones_params = recipe.get('zones_params', copy.deepcopy(zones.DEFAULT_ZONE_PARAMS))
    manual_masks = recipe.get('manual_masks', {})
    has_zonal = (folder / 'zonal.png').is_file()
    return {
        'render_id': identifier,
        'has_zonal': has_zonal,
        'zones_params': zones_params,
        'manual_masks': manual_masks,
        'available_zones': ['subject', 'sky', 'skin', 'background', 'foreground'],
        'manual_tools': ['brush', 'radial', 'linear'],
    }


@app.post('/api/renders/{identifier}/zones/apply')
def apply_render_zones(identifier: str, req: ZoneAdjustRequest):
    folder = STATE / 'renders' / identifier
    if not folder.is_dir():
        raise HTTPException(404, 'Render not found')
    recipe_file = recipe_path(identifier)
    recipe = json.loads(recipe_file.read_text(encoding='utf-8'))

    # Base image is adapted.png if model ran, else preview.png
    input_path = folder / 'adapted.png' if (folder / 'adapted.png').is_file() else folder / 'preview.png'
    if not input_path.is_file():
        raise HTTPException(404, 'Base image preview not found')

    with Image.open(input_path) as im:
        masks = zones.generate_zone_masks(im)
        manuals = zones.build_manual_masks((im.height, im.width), req.manual_masks)
        adjusted_im = zones.apply_zone_adjustments(im, masks, req.zones, manuals)
        zonal_path = folder / 'zonal.png'
        adjusted_im.save(zonal_path, 'PNG')

    recipe['zones_params'] = req.zones
    recipe['manual_masks'] = req.manual_masks
    recipe['has_zonal'] = True
    library.save_json(recipe_file, recipe)
    return {
        'render_id': identifier,
        'url': f'/api/renders/{identifier}/image?t={int(time.time() * 1000)}',
        'has_zonal': True,
        'zones_params': req.zones,
        'manual_masks': req.manual_masks,
    }


@app.post('/api/renders/{identifier}/zones/reset')
def reset_render_zones(identifier: str):
    folder = STATE / 'renders' / identifier
    if not folder.is_dir():
        raise HTTPException(404, 'Render not found')
    zonal_path = folder / 'zonal.png'
    if zonal_path.is_file():
        try:
            zonal_path.unlink()
        except Exception:
            pass
    recipe_file = recipe_path(identifier)
    recipe = json.loads(recipe_file.read_text(encoding='utf-8'))
    recipe['has_zonal'] = False
    recipe['zones_params'] = copy.deepcopy(zones.DEFAULT_ZONE_PARAMS)
    recipe['manual_masks'] = {}
    library.save_json(recipe_file, recipe)
    return {
        'render_id': identifier,
        'url': f'/api/renders/{identifier}/image?t={int(time.time() * 1000)}',
        'has_zonal': False,
    }


@app.post('/api/renders/{identifier}/zones/auto-balance')
def auto_balance_render_zones(identifier: str):
    folder = STATE / 'renders' / identifier
    if not folder.is_dir():
        raise HTTPException(404, 'Render not found')
    input_path = folder / 'adapted.png' if (folder / 'adapted.png').is_file() else folder / 'preview.png'
    if not input_path.is_file():
        raise HTTPException(404, 'Base image preview not found')

    with Image.open(input_path) as im:
        masks = zones.generate_zone_masks(im)
        ai_params = zones.compute_ai_zone_adjustments(im, masks)

    return {
        'render_id': identifier,
        'zones_params': ai_params,
        'message': 'AI balanced adjustments computed for subject, sky, skin, and background.',
    }


@app.get('/api/renders/{identifier}/zones/mask')
def get_render_zone_mask(identifier: str, zone: str = 'subject', ruby: bool = True):
    folder = STATE / 'renders' / identifier
    if not folder.is_dir():
        raise HTTPException(404, 'Render not found')
    recipe = json.loads(recipe_path(identifier).read_text(encoding='utf-8'))
    zones_params = recipe.get('zones_params', copy.deepcopy(zones.DEFAULT_ZONE_PARAMS))
    manual_masks_cfg = recipe.get('manual_masks', {})
    z_cfg = zones_params.get(zone, {})

    input_path = folder / 'adapted.png' if (folder / 'adapted.png').is_file() else folder / 'preview.png'
    if not input_path.is_file():
        raise HTTPException(404, 'Base image preview not found')

    with Image.open(input_path) as im:
        h, w = im.height, im.width
        if zone in ('brush', 'radial', 'linear'):
            manuals = zones.build_manual_masks((h, w), manual_masks_cfg)
            raw_m = manuals.get(zone)
            if raw_m is None:
                # Default fallback for manual mask if not yet drawn
                if zone == 'radial':
                    raw_m = zones.generate_radial_mask((h, w), cx=0.5, cy=0.5, rx=0.3, ry=0.3)
                elif zone == 'linear':
                    raw_m = zones.generate_linear_mask((h, w), x1=0.5, y1=0.2, x2=0.5, y2=0.8)
                else:
                    raw_m = np.zeros((h, w), dtype=np.float32)
        else:
            masks = zones.generate_zone_masks(im)
            raw_m = masks.get(zone)
            if raw_m is None:
                raw_m = masks.get('subject', np.zeros((h, w), dtype=np.float32))

        refined = zones.refine_mask(
            raw_m,
            sensitivity=z_cfg.get('sensitivity', 0),
            feather=z_cfg.get('feather', 12),
            invert=z_cfg.get('invert', False)
        )
        if ruby:
            overlay_im = zones.create_ruby_overlay(im, refined)
            mask_preview_path = folder / f'mask_{zone}_ruby.png'
            overlay_im.save(mask_preview_path, 'PNG')
            return FileResponse(mask_preview_path, media_type='image/png')
        else:
            m_uint = (np.clip(refined, 0.0, 1.0) * 255).astype(np.uint8)
            mask_im = Image.fromarray(m_uint, mode='L')
            mask_preview_path = folder / f'mask_{zone}_mono.png'
            mask_im.save(mask_preview_path, 'PNG')
            return FileResponse(mask_preview_path, media_type='image/png')


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
def rendered_image(identifier: str, raw: bool = False):
    folder = STATE / 'renders' / identifier
    recipe = json.loads(recipe_path(identifier).read_text(encoding='utf-8'))
    if not raw:
        kirkified = folder / 'kirkified.png'
        if kirkified.is_file() and recipe.get('is_kirkified'):
            return FileResponse(kirkified)
        zonal = folder / 'zonal.png'
        if zonal.is_file():
            return FileResponse(zonal)
    preview = Path(recipe['preview'])
    if not preview.is_file():
        candidate = folder / preview.name
        if candidate.is_file():
            preview = candidate
    return FileResponse(preview)


class KirkifyRequest(BaseModel):
    render_id: str | None = None
    image_id: str | None = None
    mode: str = 'fusion'
    intensity: float = Field(default=0.75, ge=0.1, le=1.0)
    scale: float = Field(default=1.05, ge=0.5, le=2.0)
    match_lighting: bool = True


@app.post('/api/kirkify')
def apply_kirkify_route(req: KirkifyRequest):
    if not req.render_id and not req.image_id:
        raise HTTPException(400, 'Either render_id or image_id is required')

    if req.render_id:
        folder = STATE / 'renders' / req.render_id
        if not folder.is_dir():
            raise HTTPException(404, 'Render not found')
        recipe_file = recipe_path(req.render_id)
        recipe = json.loads(recipe_file.read_text(encoding='utf-8'))

        zonal_path = folder / 'zonal.png'
        if zonal_path.is_file():
            input_path = zonal_path
        elif (folder / 'adapted.png').is_file():
            input_path = folder / 'adapted.png'
        else:
            input_path = folder / 'preview.png'

        if not input_path.is_file():
            raise HTTPException(404, 'Image preview not found')

        with Image.open(input_path) as im:
            kirkified_im, faces_count = kirkify.apply_kirkify(
                im,
                mode=req.mode,
                intensity=req.intensity,
                scale_multiplier=req.scale,
                match_lighting=req.match_lighting
            )
            dest_path = folder / 'kirkified.png'
            kirkified_im.save(dest_path, 'PNG')

        recipe['is_kirkified'] = True
        recipe['kirkify_mode'] = req.mode
        recipe['kirkify_intensity'] = req.intensity
        recipe['kirkify_scale'] = req.scale
        recipe['kirkify_faces'] = faces_count
        library.save_json(recipe_file, recipe)

        return {
            'status': 'ok',
            'faces_found': faces_count,
            'render_id': req.render_id,
            'is_kirkified': True,
            'mode': req.mode,
            'url': f'/api/renders/{req.render_id}/image?t={int(time.time() * 1000)}',
            'message': f'Kirkificación ({req.mode}) aplicada a {faces_count} rostro(s).' if faces_count > 0 else 'No se detectaron rostros en la foto.',
        }
    else:
        preview_path = STATE / 'previews' / (req.image_id + '.png')
        if not preview_path.is_file():
            raise HTTPException(404, 'Library preview not found')

        dest_path = STATE / 'previews' / (req.image_id + '_kirkified.png')
        with Image.open(preview_path) as im:
            kirkified_im, faces_count = kirkify.apply_kirkify(
                im,
                mode=req.mode,
                intensity=req.intensity,
                scale_multiplier=req.scale,
                match_lighting=req.match_lighting
            )
            kirkified_im.save(dest_path, 'PNG')

        return {
            'status': 'ok',
            'faces_found': faces_count,
            'image_id': req.image_id,
            'is_kirkified': True,
            'mode': req.mode,
            'url': f'/api/images/{req.image_id}/kirkified?t={int(time.time() * 1000)}',
            'message': f'Kirkificación ({req.mode}) aplicada a {faces_count} rostro(s).' if faces_count > 0 else 'No se detectaron rostros en la foto.',
        }


@app.post('/api/renders/{identifier}/kirkify/reset')
def reset_kirkify_route(identifier: str):
    folder = STATE / 'renders' / identifier
    if not folder.is_dir():
        raise HTTPException(404, 'Render not found')
    recipe_file = recipe_path(identifier)
    recipe = json.loads(recipe_file.read_text(encoding='utf-8'))
    recipe['is_kirkified'] = False
    library.save_json(recipe_file, recipe)
    return {
        'status': 'ok',
        'render_id': identifier,
        'is_kirkified': False,
        'url': f'/api/renders/{identifier}/image?t={int(time.time() * 1000)}',
        'message': 'Kirkificación desactivada.',
    }


@app.get('/api/images/{identifier}/kirkified')
def image_kirkified(identifier: str):
    path = STATE / 'previews' / (identifier + '_kirkified.png')
    if not path.is_file():
        raise HTTPException(404, 'Kirkified preview not found')
    return FileResponse(path, media_type='image/png')


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
    if recipe.get('has_zonal') and recipe.get('zones_params'):
        try:
            with Image.open(output) as im:
                masks = zones.generate_zone_masks(im)
                manuals = zones.build_manual_masks((im.height, im.width), recipe.get('manual_masks', {}))
                adjusted_im = zones.apply_zone_adjustments(im, masks, recipe['zones_params'], manuals)
                adjusted_im.save(output, 'PNG')
        except Exception:
            pass
    if recipe.get('is_kirkified'):
        try:
            with Image.open(output) as im:
                kirk_im, _ = kirkify.apply_kirkify(
                    im,
                    mode=recipe.get('kirkify_mode', 'organic'),
                    intensity=recipe.get('kirkify_intensity', 0.70),
                    scale_multiplier=recipe.get('kirkify_scale', 1.05)
                )
                kirk_im.save(output, 'PNG')
        except Exception:
            pass
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
            if recipe.get('has_zonal') and recipe.get('zones_params'):
                try:
                    with Image.open(output) as im:
                        masks = zones.generate_zone_masks(im)
                        manuals = zones.build_manual_masks((im.height, im.width), recipe.get('manual_masks', {}))
                        adjusted_im = zones.apply_zone_adjustments(im, masks, recipe['zones_params'], manuals)
                        adjusted_im.save(output, 'PNG')
                except Exception:
                    pass
            if recipe.get('is_kirkified'):
                try:
                    with Image.open(output) as im:
                        kirk_im, _ = kirkify.apply_kirkify(
                            im,
                            mode=recipe.get('kirkify_mode', 'organic'),
                            intensity=recipe.get('kirkify_intensity', 0.70),
                            scale_multiplier=recipe.get('kirkify_scale', 1.05)
                        )
                        kirk_im.save(output, 'PNG')
                except Exception:
                    pass
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
