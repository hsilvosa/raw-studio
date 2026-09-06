import copy
import json
import math
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
from .settings import ROOT, STATE, PHOTO_ROOT, OUTPUT, RAW_EXTENSIONS, DARKTABLE

engine = Darktable()
pool = ThreadPoolExecutor(max_workers=1)
jobs = {}
job_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app):
    yield
    pool.shutdown(wait=True)
    engine.close()


app = FastAPI(title='Revelado local', lifespan=lifespan)


@app.middleware('http')
async def local_requests(request: Request, call_next):
    # Browser-origin protection: local files never become a public file server.
    host = request.url.hostname
    origin = request.headers.get('origin')
    if host not in {'127.0.0.1', 'localhost', 'testserver'}:
        return JSONResponse({'detail': 'Solo acceso local'}, status_code=403)
    if origin and (urlparse(origin).netloc != request.headers.get('host')):
        return JSONResponse({'detail': 'Origen no permitido'}, status_code=403)
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
        jobs[identifier] = {'id': identifier, 'state': 'queued', 'message': 'En cola'}
    def run():
        try:
            update(identifier, state='running', message='Preparando imagen')
            result = fn(identifier, *args)
            update(identifier, state='done', message='Terminado', result=result)
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
        update(job_id, message=f'Importando {index + 1} de {len(paths)}')
        image = library.add(path)
        base_preview(image)
        results.append(image)
    return results


class ImportRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=100)


class DevelopRequest(BaseModel):
    image_id: str
    profile_id: str
    intensity: float = Field(default=.8, ge=0, le=1)
    exposure_offset: float = Field(default=0, ge=-2, le=2)
    adapt: bool = True
    use_model: bool = False
    intent: str = Field(default='Conserva el estilo y adapta su intensidad a la escena.', max_length=1500)
    width: int = Field(default=1600, ge=600, le=6000)


def develop(job_id, request):
    image = library.get(request.image_id)
    profile = next((p for p in profiles() if p['id'] == request.profile_id), None)
    if profile is None:
        raise ValueError('Perfil desconocido')
    source = Path(image['copy'])
    if library.digest(source) != image['sha256']:
        raise ValueError('La copia RAW ha cambiado; vuelve a importar el original')
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
    update(job_id, message='Revelando con darktable')
    engine.export(source, stack, candidate, request.width)
    reason = 'Perfil aplicado con corrección tonal limitada.' if request.adapt else 'Perfil aplicado sin adaptación tonal automática.'
    model_name = None
    proposal = None
    if request.use_model:
        update(job_id, message='El modelo local está proponiendo ajustes')
        # Render a small model input through darktable, never an image generator.
        model_preview = folder / 'model-input.png'
        engine.export(source, stack, model_preview, 768)
        proposal, model_name = model.propose(model_preview, stack, library.measure(candidate), profile['title'] + '. ' + request.intent)
        stack = merge(stack, proposal)
        update(job_id, message='Aplicando la receta del modelo en darktable')
        candidate = folder / 'adapted.png'
        engine.export(source, stack, candidate, request.width)
        reason = proposal.reason
    after = library.measure(candidate)
    warnings = []
    if after['white_fraction'] > .02:
        warnings.append('Hay luces cercanas al blanco: revisa los reflejos y letreros.')
    if after['black_fraction'] > .15:
        warnings.append('Hay sombras profundas: comprueba si conservan el detalle que buscas.')
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


@app.get('/api/browse')
def browse(path: str = ''):
    folder = library.inside(path or PHOTO_ROOT)
    if not folder.is_dir():
        raise ValueError('Carpeta no encontrada')
    items = []
    for p in sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if p.name.startswith('.') or p.is_symlink():
            continue
        if p.is_dir() or p.suffix.lower() in RAW_EXTENSIONS:
            items.append({'name': p.name, 'path': str(p), 'directory': p.is_dir()})
    return {'path': str(folder), 'parent': str(folder.parent) if folder != PHOTO_ROOT else None, 'items': items}


@app.post('/api/import')
def import_route(request: ImportRequest):
    # Validate all paths before starting a potentially long import.
    for path in request.paths:
        library.inside(path)
    return submit(import_images, request.paths)


@app.get('/api/images/{identifier}/preview')
def image_preview(identifier: str):
    library.get(identifier)
    path = STATE / 'previews' / (identifier + '.png')
    if not path.is_file():
        raise HTTPException(404, 'Previsualización pendiente')
    return FileResponse(path)


@app.post('/api/develop')
def develop_route(request: DevelopRequest):
    library.get(request.image_id)
    return submit(develop, request)


@app.get('/api/jobs/{identifier}')
def job_status(identifier: str):
    with job_lock:
        if identifier not in jobs:
            raise HTTPException(404, 'Trabajo no encontrado')
        return copy.deepcopy(jobs[identifier])


def recipe_path(identifier):
    if len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
        raise ValueError('Identificador no válido')
    path = STATE / 'renders' / identifier / 'recipe.json'
    if not path.is_file():
        raise HTTPException(404, 'Resultado no encontrado')
    return path


@app.get('/api/renders/{identifier}/image')
def rendered_image(identifier: str):
    recipe = json.loads(recipe_path(identifier).read_text(encoding='utf-8'))
    return FileResponse(recipe['preview'])


@app.post('/api/renders/{identifier}/export')
def export_route(identifier: str):
    recipe = json.loads(recipe_path(identifier).read_text(encoding='utf-8'))
    return submit(export_render, recipe)


def export_render(job_id, recipe):
    image = library.get(recipe['image_id'])
    target = OUTPUT / recipe['profile_id'] / ('studio_' + image['id'])
    target.mkdir(parents=True, exist_ok=True)
    # Fixed per-image location, immutable filenames; no folder per iteration.
    output = target / (job_id[:12] + '.png')
    update(job_id, message='Exportando PNG a 6000 píxeles')
    engine.export(Path(image['copy']), recipe['stack'], output, 6000)
    library.save_json(output.with_suffix('.json'), recipe)
    return {'path': str(output)}


app.mount('/', StaticFiles(directory=ROOT / 'studio' / 'static', html=True), name='ui')
