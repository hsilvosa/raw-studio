from pathlib import Path
import numpy as np
from PIL import Image
from studio.kirkify import get_kirk_asset, get_kirk_face_data, detect_faces, apply_kirkify


def test_kirk_asset_and_face_data():
    asset = get_kirk_asset()
    assert asset.mode == 'RGBA'
    assert asset.width > 100
    assert asset.height > 100

    bgr, alpha, src_pts, kw, kh = get_kirk_face_data()
    assert bgr.shape[:2] == (kh, kw)
    assert len(src_pts) == 4
    assert alpha.shape == (kh, kw)


def test_kirkify_synthetic_no_face():
    # Plain green/blue image with no people or faces
    arr = np.zeros((300, 300, 3), dtype=np.uint8)
    arr[..., 1] = 180
    im = Image.fromarray(arr, mode='RGB')
    faces = detect_faces(im)
    assert len(faces) == 0
    res, count = apply_kirkify(im)
    assert count == 0
    assert res.size == im.size
    # Exact pixel match (image was completely untouched!)
    assert np.array_equal(np.asarray(res), arr)


def test_kirkify_with_real_face():
    preview = Path('.studio/previews/1d50937d5e24629cec78.png')
    if not preview.is_file():
        preview = Path('studio/assets/charlie_kirk.png')
    im = Image.open(preview).convert('RGB')
    faces = detect_faces(im)
    assert len(faces) >= 1
    face = faces[0]
    assert face['width'] > 20
    assert face['height'] > 20

    # Test true face fusion
    res_fusion, count = apply_kirkify(im, mode='fusion', intensity=0.75)
    assert count >= 1
    assert res_fusion.size == im.size
    # Pixel content has been fused
    assert not np.array_equal(np.asarray(res_fusion), np.asarray(im))


def test_kirkify_glasses_and_tilt():
    preview = Path('.studio/previews/4ae69cfda67b64b2f55c.png')
    if preview.is_file():
        im = Image.open(preview).convert('RGB')
        faces = detect_faces(im)
        assert len(faces) >= 1
        res, count = apply_kirkify(im, mode='fusion', intensity=0.75)
        assert count >= 1
        assert res.size == im.size


def test_kirkify_scale_adjustment():
    preview = Path('.studio/previews/4ae69cfda67b64b2f55c.png')
    if preview.is_file():
        im = Image.open(preview).convert('RGB')
        res_small, _ = apply_kirkify(im, mode='fusion', intensity=0.75, scale_multiplier=0.70)
        res_large, _ = apply_kirkify(im, mode='fusion', intensity=0.75, scale_multiplier=1.20)
        # Verify both scales produce valid images and differ from each other
        assert res_small.size == im.size
        assert res_large.size == im.size
        assert not np.array_equal(np.asarray(res_small), np.asarray(res_large))


