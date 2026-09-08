import numpy as np
from PIL import Image
from studio.zones import (
    generate_zone_masks,
    generate_radial_mask,
    generate_linear_mask,
    rasterize_brush_mask,
    build_manual_masks,
    refine_mask,
    create_ruby_overlay,
    compute_ai_zone_adjustments,
    apply_zone_adjustments,
    DEFAULT_ZONE_PARAMS,
)
import json
from fastapi.testclient import TestClient
from studio.app import app
from studio.settings import STATE


def test_generate_zone_masks():
    im = Image.new('RGB', (160, 100), color=(100, 150, 200))
    masks = generate_zone_masks(im)

    for zone in ('subject', 'sky', 'skin', 'background', 'foreground'):
        assert zone in masks
        m = masks[zone]
        assert m.shape == (100, 160)
        assert np.min(m) >= 0.0
        assert np.max(m) <= 1.0


def test_radial_and_linear_masks():
    radial = generate_radial_mask((80, 80), cx=0.5, cy=0.5, rx=0.3, ry=0.3, angle=0.0, feather=20)
    assert radial.shape == (80, 80)
    assert radial[40, 40] > 0.8  # Center should be strong
    assert radial[5, 5] < 0.2    # Outer edge should fall off

    linear = generate_linear_mask((80, 80), x1=0.5, y1=0.1, x2=0.5, y2=0.9, feather=20)
    assert linear.shape == (80, 80)
    assert linear[10, 40] > linear[70, 40]  # Top is stronger than bottom


def test_brush_rasterization():
    strokes = [
        {'x': 0.5, 'y': 0.5, 'radius': 15, 'erase': False},
        {'path': [{'x': 0.2, 'y': 0.2}, {'x': 0.3, 'y': 0.3}], 'radius': 10, 'erase': False},
    ]
    mask = rasterize_brush_mask((100, 100), strokes, feather=5)
    assert mask.shape == (100, 100)
    assert mask[50, 50] > 0.5
    assert mask[5, 5] < 0.001


def test_refine_mask_invert_and_feather():
    mask = np.ones((50, 50), dtype=np.float32)
    mask[10:30, 10:30] = 0.0

    inverted = refine_mask(mask, feather=0, invert=True)
    assert inverted[0, 0] == 0.0
    assert inverted[20, 20] == 1.0

    feathered = refine_mask(mask, feather=5)
    assert feathered.shape == (50, 50)
    assert 0.0 <= np.min(feathered) <= np.max(feathered) <= 1.0


def test_create_ruby_overlay():
    im = Image.new('RGB', (60, 40), color=(128, 128, 128))
    mask = np.full((40, 60), 0.5, dtype=np.float32)

    overlay = create_ruby_overlay(im, mask)
    assert overlay.size == (60, 40)
    arr = np.asarray(overlay)
    # R channel should be elevated due to ruby red tint
    assert arr[20, 20, 0] > arr[20, 20, 1]


def test_compute_ai_zone_adjustments():
    im = Image.new('RGB', (100, 100), color=(140, 160, 220))
    masks = generate_zone_masks(im)
    ai_params = compute_ai_zone_adjustments(im, masks)
    assert isinstance(ai_params, dict)
    for k in ('subject', 'sky', 'skin', 'background'):
        assert k in ai_params
        assert 'light' in ai_params[k]


def test_apply_zone_adjustments():
    im = Image.new('RGB', (100, 100), color=(120, 120, 120))
    masks = {
        'subject': np.ones((100, 100), dtype=np.float32),
        'sky': np.zeros((100, 100), dtype=np.float32),
        'skin': np.zeros((100, 100), dtype=np.float32),
        'background': np.zeros((100, 100), dtype=np.float32),
        'foreground': np.zeros((100, 100), dtype=np.float32),
    }

    params = {
        'subject': {'light': 0.5, 'temp': 10, 'contrast': 0.1, 'detail': 20, 'blur': 0, 'saturation': 1.1},
        'sky': {'light': 0.0},
        'background': {'light': 0.0, 'blur': 5.0},
    }

    res = apply_zone_adjustments(im, masks, params)
    assert res.size == (100, 100)
    res_arr = np.asarray(res)
    # Brightness should increase because light = +0.5 EV on subject mask
    assert np.mean(res_arr) > 120


def test_build_manual_masks():
    shape = (100, 100)
    manual_configs = {
        'brush': {'strokes': [{'x': 0.5, 'y': 0.5, 'radius': 12, 'erase': False}], 'feather': 5},
        'radial': {'cx': 0.5, 'cy': 0.5, 'rx': 0.25, 'ry': 0.25, 'angle': 0, 'feather': 15},
        'linear': {'x1': 0.5, 'y1': 0.1, 'x2': 0.5, 'y2': 0.9, 'feather': 20},
    }
    res = build_manual_masks(shape, manual_configs)
    assert 'brush' in res
    assert 'radial' in res
    assert 'linear' in res
    assert res['brush'].shape == (100, 100)
    assert res['radial'].shape == (100, 100)
    assert res['linear'].shape == (100, 100)


def test_zonal_api_workflow():
    client = TestClient(app)
    render_id = '0123456789abcdef0123456789abcdef'
    folder = STATE / 'renders' / render_id
    folder.mkdir(parents=True, exist_ok=True)
    preview_img = Image.new('RGB', (100, 80), color=(140, 150, 180))
    preview_img.save(folder / 'preview.png')

    recipe_data = {
        'image_id': 'img1',
        'preview': str(folder / 'preview.png'),
        'profile_title': 'Test',
        'stack': []
    }
    (folder / 'recipe.json').write_text(json.dumps(recipe_data), encoding='utf-8')

    try:
        # Test GET zones
        res = client.get(f'/api/renders/{render_id}/zones')
        assert res.status_code == 200
        data = res.json()
        assert data['has_zonal'] is False
        assert 'subject' in data['zones_params']
        assert 'brush' in data['manual_tools']

        # Test auto-balance
        res = client.post(f'/api/renders/{render_id}/zones/auto-balance')
        assert res.status_code == 200
        assert 'zones_params' in res.json()

        # Test apply zones
        payload = {
            'zones': {'subject': {'light': 0.4, 'contrast': 0.1}},
            'manual_masks': {'radial': {'cx': 0.5, 'cy': 0.5, 'rx': 0.3, 'ry': 0.3, 'feather': 10}}
        }
        res = client.post(f'/api/renders/{render_id}/zones/apply', json=payload)
        assert res.status_code == 200
        assert (folder / 'zonal.png').is_file()

        # Test GET mask
        res = client.get(f'/api/renders/{render_id}/zones/mask?zone=subject&ruby=true')
        assert res.status_code == 200
        assert res.headers['content-type'] == 'image/png'

        # Test GET image (should return zonal image)
        res = client.get(f'/api/renders/{render_id}/image')
        assert res.status_code == 200

        # Test reset zones
        res = client.post(f'/api/renders/{render_id}/zones/reset')
        assert res.status_code == 200
        assert not (folder / 'zonal.png').is_file()
    finally:
        # Cleanup dummy render
        for f in folder.iterdir():
            try:
                f.unlink()
            except Exception:
                pass
        try:
            folder.rmdir()
        except Exception:
            pass

