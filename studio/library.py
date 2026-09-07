import hashlib
import io
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path
from PIL import Image, ImageOps
from .settings import (
    STATE, PHOTO_ROOT, RAW_EXTENSIONS, CAMERA_RAW_EXTENSIONS,
    DEFAULT_PHOTO_ROOT, DARKTABLE, save_config
)


def connect():
    STATE.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STATE / 'library.sqlite', timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE IF NOT EXISTS images (id TEXT PRIMARY KEY, name TEXT, source TEXT, copy TEXT, sha256 TEXT)')
    cursor = conn.execute("PRAGMA table_info(images)")
    columns = {row['name'] for row in cursor.fetchall()}
    if 'folder' not in columns:
        conn.execute("ALTER TABLE images ADD COLUMN folder TEXT DEFAULT ''")
    if 'favorite' not in columns:
        conn.execute("ALTER TABLE images ADD COLUMN favorite INTEGER DEFAULT 0")
    conn.execute('CREATE TABLE IF NOT EXISTS image_tags (image_id TEXT, tag TEXT, PRIMARY KEY(image_id, tag))')
    return conn


def set_photo_root(path):
    global PHOTO_ROOT
    p = Path(path).resolve()
    if not p.is_dir():
        raise ValueError('Photo folder not found')
    if p.is_relative_to(STATE):
        raise ValueError('Cannot select state folder as photos folder')
    PHOTO_ROOT = p
    return PHOTO_ROOT


def inside(path):
    value = Path(path).resolve()
    if value.is_relative_to(STATE):
        raise ValueError('Please select a file inside the photos folder')
    if PHOTO_ROOT is not None and not value.is_relative_to(PHOTO_ROOT):
        raise ValueError('Please select a file inside the photos folder')
    return value


def get_browser_thumbnail(path_str: str) -> Path:
    target = Path(path_str).resolve()
    if not target.is_file():
        raise ValueError('File not found')
    if target.is_relative_to(STATE):
        raise ValueError('Cannot preview files in state folder')
    ext = target.suffix.lower()
    if ext not in RAW_EXTENSIONS and ext not in CAMERA_RAW_EXTENSIONS:
        raise ValueError('Unsupported file format for preview')

    cache_dir = STATE / 'cache' / 'browser_thumbs'
    cache_dir.mkdir(parents=True, exist_ok=True)
    mtime = target.stat().st_mtime
    key = hashlib.md5(f'{target}:{mtime}'.encode('utf-8')).hexdigest()
    out_file = cache_dir / f'{key}.jpg'
    if out_file.exists():
        return out_file

    # 1. Standard image formats: open with PIL
    if ext in {'.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff'}:
        try:
            with Image.open(target) as im:
                im = ImageOps.exif_transpose(im)
                im.thumbnail((240, 240))
                im.convert('RGB').save(out_file, 'JPEG', quality=85)
            return out_file
        except Exception:
            pass

    # 2. Camera RAW: extract embedded JPEG preview
    try:
        with open(target, 'rb') as f:
            data = f.read(8 * 1024 * 1024)
        best_im = None
        best_area = 0
        idx = 0
        while True:
            pos = data.find(b'\xff\xd8\xff', idx)
            if pos == -1:
                break
            end = data.find(b'\xff\xd9', pos)
            if end != -1 and (end - pos) > 5000:
                try:
                    seg = data[pos:end + 2]
                    im = Image.open(io.BytesIO(seg))
                    area = im.width * im.height
                    if area > best_area:
                        best_area = area
                        best_im = im
                except Exception:
                    pass
            idx = pos + 3

        if best_im is not None:
            best_im = ImageOps.exif_transpose(best_im)
            best_im.thumbnail((240, 240))
            best_im.convert('RGB').save(out_file, 'JPEG', quality=85)
            return out_file
    except Exception:
        pass

    # 3. Fallback to darktable-mcp
    try:
        cmd = [str(DARKTABLE), 'export', str(target), str(out_file), '--width', '240', '--height', '240']
        subprocess.run(cmd, capture_output=True, timeout=12)
        if out_file.exists():
            return out_file
    except Exception:
        pass

    raise RuntimeError('Could not extract thumbnail for image')



def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def add(path):
    source = inside(path)
    if not source.is_file() or source.suffix.lower() not in RAW_EXTENSIONS:
        raise ValueError('Unsupported image format')
    checksum = digest(source)
    identifier = checksum[:20]
    target = STATE / 'originals' / (identifier + source.suffix.lower())
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        temporary = target.with_suffix('.tmp')
        shutil.copyfile(source, temporary)
        if digest(temporary) != checksum:
            temporary.unlink()
            raise RuntimeError('Copy verification failed')
        temporary.replace(target)
    folder_name = source.parent.name or 'Main'
    with connect() as conn:
        conn.execute('INSERT OR IGNORE INTO images (id, name, source, copy, sha256, folder, favorite) VALUES (?,?,?,?,?,?,?)',
                     (identifier, source.name, str(source), str(target), checksum, folder_name, 0))
    return get(identifier)


def get(identifier):
    with connect() as conn:
        row = conn.execute('SELECT * FROM images WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError('Image not found')
        data = dict(row)
        tags_rows = conn.execute('SELECT tag FROM image_tags WHERE image_id=? ORDER BY tag', (identifier,)).fetchall()
        data['tags'] = [r['tag'] for r in tags_rows]
    copy_path = Path(data['copy'])
    if not copy_path.exists():
        relocated = STATE / 'originals' / copy_path.name
        if relocated.exists():
            data['copy'] = str(relocated)
            with connect() as conn:
                conn.execute('UPDATE images SET copy=? WHERE id=?', (str(relocated), identifier))
    if not data.get('folder'):
        data['folder'] = Path(data['source']).parent.name or 'Main'
    data['favorite'] = bool(data.get('favorite', 0))
    return data


def all_images():
    with connect() as conn:
        rows = [dict(row) for row in conn.execute('SELECT * FROM images ORDER BY name')]
        tags_rows = conn.execute('SELECT image_id, tag FROM image_tags ORDER BY tag').fetchall()
        tags_map = {}
        for r in tags_rows:
            tags_map.setdefault(r['image_id'], []).append(r['tag'])
    for data in rows:
        copy_path = Path(data['copy'])
        if not copy_path.exists():
            relocated = STATE / 'originals' / copy_path.name
            if relocated.exists():
                data['copy'] = str(relocated)
                with connect() as conn:
                    conn.execute('UPDATE images SET copy=? WHERE id=?', (str(relocated), data['id']))
        if not data.get('folder'):
            data['folder'] = Path(data['source']).parent.name or 'Main'
        data['favorite'] = bool(data.get('favorite', 0))
        data['tags'] = tags_map.get(data['id'], [])
    return rows


def delete_images(identifiers):
    if isinstance(identifiers, str):
        identifiers = [identifiers]
    deleted = []
    with connect() as conn:
        for identifier in identifiers:
            row = conn.execute('SELECT * FROM images WHERE id=?', (identifier,)).fetchone()
            if row:
                data = dict(row)
                # 1. Delete working copy in .studio/originals (preserves original source on disk)
                copy_path = Path(data['copy'])
                if copy_path.is_file() and copy_path.is_relative_to(STATE):
                    try:
                        copy_path.unlink()
                    except Exception:
                        pass
                # 2. Delete base preview in .studio/previews
                preview_path = STATE / 'previews' / (identifier + '.png')
                if preview_path.is_file():
                    try:
                        preview_path.unlink()
                    except Exception:
                        pass
                # 3. Delete developed renders for this image
                renders_dir = STATE / 'renders'
                if renders_dir.is_dir():
                    for r_dir in renders_dir.iterdir():
                        if r_dir.is_dir():
                            r_recipe = r_dir / 'recipe.json'
                            if r_recipe.is_file():
                                try:
                                    rec = json.loads(r_recipe.read_text(encoding='utf-8'))
                                    if rec.get('image_id') == identifier:
                                        shutil.rmtree(r_dir, ignore_errors=True)
                                except Exception:
                                    pass
                # 4. Remove database records
                conn.execute('DELETE FROM image_tags WHERE image_id=?', (identifier,))
                conn.execute('DELETE FROM images WHERE id=?', (identifier,))
                deleted.append(identifier)
    return deleted


def set_favorite(identifier: str, favorite: bool):
    fav_val = 1 if favorite else 0
    with connect() as conn:
        conn.execute('UPDATE images SET favorite=? WHERE id=?', (fav_val, identifier))
    return get(identifier)


def set_tags(identifier: str, tags: list[str]):
    clean_tags = sorted(list({t.strip().lstrip('#').lower() for t in tags if t.strip()}))
    with connect() as conn:
        conn.execute('DELETE FROM image_tags WHERE image_id=?', (identifier,))
        for t in clean_tags:
            conn.execute('INSERT OR IGNORE INTO image_tags VALUES (?,?)', (identifier, t))
    return get(identifier)


def add_tags(identifiers: list[str], tags: list[str]):
    clean_tags = sorted(list({t.strip().lstrip('#').lower() for t in tags if t.strip()}))
    with connect() as conn:
        for i_id in identifiers:
            for t in clean_tags:
                conn.execute('INSERT OR IGNORE INTO image_tags VALUES (?,?)', (i_id, t))
    return {'count': len(identifiers), 'tags': clean_tags}


def remove_tags(identifiers: list[str], tags: list[str]):
    clean_tags = [t.strip().lstrip('#').lower() for t in tags if t.strip()]
    with connect() as conn:
        for i_id in identifiers:
            for t in clean_tags:
                conn.execute('DELETE FROM image_tags WHERE image_id=? AND tag=?', (i_id, t))
    return {'count': len(identifiers), 'removed': clean_tags}


def set_folder(identifiers: list[str], folder: str):
    clean_folder = folder.strip() or 'Main'
    with connect() as conn:
        for i_id in identifiers:
            conn.execute('UPDATE images SET folder=? WHERE id=?', (clean_folder, i_id))
    return {'count': len(identifiers), 'folder': clean_folder}


def get_library_metadata():
    with connect() as conn:
        tag_rows = conn.execute('SELECT tag, COUNT(image_id) as cnt FROM image_tags GROUP BY tag ORDER BY cnt DESC, tag ASC').fetchall()
        folder_rows = conn.execute('SELECT folder, COUNT(id) as cnt FROM images GROUP BY folder ORDER BY cnt DESC, folder ASC').fetchall()
        fav_row = conn.execute('SELECT COUNT(id) as cnt FROM images WHERE favorite=1').fetchone()

    tags = [{'tag': r['tag'], 'count': r['cnt']} for r in tag_rows]
    folders = [{'folder': (r['folder'] or 'Main'), 'count': r['cnt']} for r in folder_rows]
    favorites_count = fav_row['cnt'] if fav_row else 0
    return {
        'tags': tags,
        'folders': folders,
        'favorites_count': favorites_count
    }


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
