import math
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
