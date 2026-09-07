from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from studio.app import app
from studio import library


def test_library_schema_and_defaults(tmp_path, monkeypatch):
    root = tmp_path / 'photos'
    root.mkdir()
    subfolder = root / 'Vacaciones'
    subfolder.mkdir()
    state = tmp_path / '.state'
    monkeypatch.setattr(library, 'PHOTO_ROOT', root)
    monkeypatch.setattr(library, 'STATE', state)

    raw_file = subfolder / 'sample.arw'
    raw_file.write_bytes(b'dummy raw bytes content 1234')

    img = library.add(raw_file)
    assert img['id']
    assert img['folder'] == 'Vacaciones'
    assert img['favorite'] is False
    assert img['tags'] == []

    # Verify get() returns same structure
    fetched = library.get(img['id'])
    assert fetched['folder'] == 'Vacaciones'
    assert fetched['favorite'] is False
    assert fetched['tags'] == []


def test_library_tags_and_favorites(tmp_path, monkeypatch):
    root = tmp_path / 'photos'
    root.mkdir()
    state = tmp_path / '.state'
    monkeypatch.setattr(library, 'PHOTO_ROOT', root)
    monkeypatch.setattr(library, 'STATE', state)

    f1 = root / 'p1.arw'; f1.write_bytes(b'raw1')
    f2 = root / 'p2.arw'; f2.write_bytes(b'raw2')
    img1 = library.add(f1)
    img2 = library.add(f2)

    # Toggle favorite
    updated = library.set_favorite(img1['id'], True)
    assert updated['favorite'] is True

    # Set tags
    library.set_tags(img1['id'], ['#Retrato', 'Verano', ' exterior '])
    img1_tags = library.get(img1['id'])['tags']
    assert img1_tags == ['exterior', 'retrato', 'verano']

    # Batch add tags
    library.add_tags([img1['id'], img2['id']], ['#Seleccionadas'])
    assert 'seleccionadas' in library.get(img1['id'])['tags']
    assert 'seleccionadas' in library.get(img2['id'])['tags']

    # Batch remove tags
    library.remove_tags([img1['id']], ['exterior'])
    assert 'exterior' not in library.get(img1['id'])['tags']

    # Batch set folder
    library.set_folder([img1['id'], img2['id']], 'Album-Favoritas')
    assert library.get(img1['id'])['folder'] == 'Album-Favoritas'
    assert library.get(img2['id'])['folder'] == 'Album-Favoritas'

    # Check metadata aggregation
    meta = library.get_library_metadata()
    assert meta['favorites_count'] == 1
    folder_names = [f['folder'] for f in meta['folders']]
    assert 'Album-Favoritas' in folder_names
    tag_names = [t['tag'] for t in meta['tags']]
    assert 'seleccionadas' in tag_names


def test_library_api_endpoints(tmp_path, monkeypatch):
    root = tmp_path / 'photos'
    root.mkdir()
    state = tmp_path / '.state'
    monkeypatch.setattr(library, 'PHOTO_ROOT', root)
    monkeypatch.setattr(library, 'STATE', state)

    f = root / 'shot.arw'; f.write_bytes(b'shot raw bytes')
    img = library.add(f)

    client = TestClient(app)

    # 1. Favorite endpoint
    res_fav = client.post(f'/api/images/{img["id"]}/favorite', json={'favorite': True})
    assert res_fav.status_code == 200
    assert res_fav.json()['favorite'] is True

    # 2. Single tags endpoint
    res_tags = client.post(f'/api/images/{img["id"]}/tags', json={'tags': ['cine', 'noche']})
    assert res_tags.status_code == 200
    assert res_tags.json()['tags'] == ['cine', 'noche']

    # 3. Batch tags endpoint
    res_batch_tags = client.post('/api/images/batch/tags', json={
        'image_ids': [img['id']],
        'add_tags': ['urbano'],
        'remove_tags': ['cine']
    })
    assert res_batch_tags.status_code == 200

    # 4. Batch folder endpoint
    res_folder = client.post('/api/images/batch/folder', json={
        'image_ids': [img['id']],
        'folder': 'Tokyo-Night'
    })
    assert res_folder.status_code == 200
    assert res_folder.json()['folder'] == 'Tokyo-Night'

    # 5. Metadata endpoint
    res_meta = client.get('/api/library/metadata')
    assert res_meta.status_code == 200
    data = res_meta.json()
    assert data['favorites_count'] == 1
    assert any(f['folder'] == 'Tokyo-Night' for f in data['folders'])
    assert any(t['tag'] == 'urbano' for t in data['tags'])
