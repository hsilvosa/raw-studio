import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from PIL import Image
from .settings import STATE, PHOTO_ROOT, RAW_EXTENSIONS


def connect():
    STATE.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STATE / 'library.sqlite', timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE IF NOT EXISTS images (id TEXT PRIMARY KEY, name TEXT, source TEXT, copy TEXT, sha256 TEXT)')
    return conn


def inside(path):
    value = Path(path).resolve()
    if not value.is_relative_to(PHOTO_ROOT) or value.is_relative_to(STATE):
        raise ValueError('Selecciona un archivo dentro de la carpeta de fotos')
    return value


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def add(path):
    source = inside(path)
    if not source.is_file() or source.suffix.lower() not in RAW_EXTENSIONS:
        raise ValueError('Formato de imagen no admitido')
    checksum = digest(source)
    identifier = checksum[:20]
    target = STATE / 'originals' / (identifier + source.suffix.lower())
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        temporary = target.with_suffix('.tmp')
        shutil.copyfile(source, temporary)
        if digest(temporary) != checksum:
            temporary.unlink()
            raise RuntimeError('Falló la verificación de la copia')
        temporary.replace(target)
    with connect() as conn:
        conn.execute('INSERT OR IGNORE INTO images VALUES (?,?,?,?,?)', (identifier, source.name, str(source), str(target), checksum))
    return get(identifier)


def get(identifier):
    with connect() as conn:
        row = conn.execute('SELECT * FROM images WHERE id=?', (identifier,)).fetchone()
    if row is None:
        raise ValueError('Imagen no encontrada')
    return dict(row)


def all_images():
    with connect() as conn:
        return [dict(row) for row in conn.execute('SELECT * FROM images ORDER BY name')]


def measure(path):
    # Read-only image measurements; all photo processing is done by darktable.
    with Image.open(path) as im:
        hist = im.convert('L').histogram()
        count = sum(hist)
        def percentile(fraction):
            total = 0
            for i, n in enumerate(hist):
                total += n
                if total >= count * fraction:
                    return i / 255
        return {'median': percentile(.5), 'p95': percentile(.95),
                'black_fraction': sum(hist[:3]) / count,
                'white_fraction': sum(hist[253:]) / count,
                'width': im.width, 'height': im.height}


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')
    temp.replace(path)
