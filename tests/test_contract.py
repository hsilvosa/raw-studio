import math
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from studio.app import app

from studio.recipes import validate_proposal, merge, make_stack, profiles
from studio import library


@pytest.mark.parametrize('value', [
    {'reason':'test','adjustments':[], 'image':'base64'},
    {'reason':'test','adjustments':[{'operation':'inpaint','params':{'amount':1.}}]},
    {'reason':'test','adjustments':[{'operation':'exposure','params':{'exposure':100.}}]},
    {'reason':'test','adjustments':[{'operation':'exposure','params':{'exposure':float('nan')}}]},
    {'reason':'test','adjustments':[{'operation':'exposure','params':{'exposure':True}}]},
    {'reason':'test','adjustments':[{'operation':'exposure','params':{'exposure':1.},'blob_hex':'00'}]},
])
def test_model_cannot_escape_numeric_contract(value):
    with pytest.raises(ValueError):
        validate_proposal(value)


def test_valid_adjustment_preserves_other_modules():
    stack=[{'operation':'exposure','params':{'exposure':0.,'black':-.0001}}, {'operation':'sharpen','params':{'amount':.5}}]
    p=validate_proposal({'reason':'Levantar sombras','adjustments':[{'operation':'exposure','params':{'exposure':.5}}]})
    result=merge(stack,p)
    assert result[0]['params']=={'exposure':.5,'black':-.0001}
    assert result[1]==stack[1]
    assert stack[0]['params']['exposure']==0.


def test_profiles_do_not_transfer_subject_geometry():
    for profile in profiles():
        assert all(m['operation'] not in {'crop','vignette','denoiseprofile'} for m in profile['stack'])
        result=make_stack(profile,.7,0)
        assert result[0]['params']['exposure']==.7


def test_paths_cannot_escape_photo_root(tmp_path,monkeypatch):
    root=tmp_path/'photos';root.mkdir()
    monkeypatch.setattr(library,'PHOTO_ROOT',root)
    with pytest.raises(ValueError):library.inside(root/'..'/'private.txt')


def test_import_preserves_source_and_sidecar(tmp_path,monkeypatch):
    root=tmp_path/'photos';root.mkdir()
    monkeypatch.setattr(library,'PHOTO_ROOT',root)
    monkeypatch.setattr(library,'STATE',root/'.state')
    original=root/'sample.arw';original.write_bytes(b'original raw bytes')
    sidecar=root/'sample.arw.xmp';sidecar.write_bytes(b'original history')
    image=library.add(original)
    assert original.read_bytes()==b'original raw bytes'
    assert sidecar.read_bytes()==b'original history'
    assert image['copy']!=str(original)
    assert library.add(original)['id']==image['id']


def test_local_origin_boundary():
    client=TestClient(app)
    assert client.get('/api/profiles').status_code==200
    assert client.post('/api/import',json={'paths':['x']},headers={'origin':'https://foreign.example'}).status_code==403
    assert client.get('/api/profiles',headers={'host':'foreign.example'}).status_code==403


def test_browse_and_set_default(tmp_path, monkeypatch):
    from studio import settings
    monkeypatch.setattr(settings, 'CONFIG_FILE', tmp_path / 'config.json')
    photos_dir = tmp_path / 'my_photos'
    photos_dir.mkdir()
    (photos_dir / 'test.arw').write_bytes(b'dummy raw')
    (photos_dir / 'test.jpg').write_bytes(b'dummy jpg')
    sub_dir = photos_dir / 'subfolder'
    sub_dir.mkdir()

    client = TestClient(app)
    # Test browse
    res = client.get(f'/api/browse?path={photos_dir}')

    assert res.status_code == 200
    data = res.json()
    assert data['path'] == str(photos_dir)
    assert 'drives' in data
    names = {item['name'] for item in data['items']}
    assert 'test.arw' in names
    assert 'test.jpg' in names
    assert 'subfolder' in names
    arw_item = next(i for i in data['items'] if i['name'] == 'test.arw')
    assert arw_item['is_raw'] is True
    jpg_item = next(i for i in data['items'] if i['name'] == 'test.jpg')
    assert jpg_item['is_raw'] is False

    # Test set default root
    res_set = client.post('/api/browse/set-default', json={'path': str(photos_dir)})
    assert res_set.status_code == 200
    assert res_set.json()['default_root'] == str(photos_dir)

    # Test browse thumbnail
    from PIL import Image
    valid_img = photos_dir / 'valid.jpg'
    Image.new('RGB', (100, 100), color='red').save(valid_img, 'JPEG')
    thumb_res = client.get(f'/api/browse/thumbnail?path={valid_img}')
    assert thumb_res.status_code == 200
    assert 'image/jpeg' in thumb_res.headers['content-type']


def test_delete_images(tmp_path, monkeypatch):
    root = tmp_path / 'photos'
    root.mkdir()
    state = tmp_path / '.state'
    monkeypatch.setattr(library, 'PHOTO_ROOT', root)
    monkeypatch.setattr(library, 'STATE', state)

    orig1 = root / 'pic1.arw'; orig1.write_bytes(b'photo 1')
    orig2 = root / 'pic2.arw'; orig2.write_bytes(b'photo 2')

    img1 = library.add(orig1)
    img2 = library.add(orig2)

    assert Path(img1['copy']).is_file()
    assert Path(img2['copy']).is_file()

    # Delete single image
    deleted = library.delete_images([img1['id']])
    assert deleted == [img1['id']]
    # Verify working copy was deleted
    assert not Path(img1['copy']).is_file()
    # Verify original file on disk is preserved!
    assert orig1.is_file()
    # Verify database entry is gone
    with pytest.raises(ValueError):
        library.get(img1['id'])

    # Delete second image
    client = TestClient(app)
    res = client.post('/api/images/delete', json={'image_ids': [img2['id']]})
    assert res.status_code == 200
    assert res.json()['count'] == 1
    assert orig2.is_file()


