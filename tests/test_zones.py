import numpy as np
from PIL import Image
from studio.zones import (
    generate_zone_masks,
    refine_mask,
    create_ruby_overlay,
    apply_zone_adjustments,
    DEFAULT_ZONE_PARAMS,
)


def test_generate_zone_masks():
    im = Image.new('RGB', (120, 80), color=(100, 150, 200))
    masks = generate_zone_masks(im)

    for zone in ('subject', 'sky', 'background'):
        assert zone in masks
        m = masks[zone]
        assert m.shape == (80, 120)
        assert np.min(m) >= 0.0
        assert np.max(m) <= 1.0


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


def test_apply_zone_adjustments():
    im = Image.new('RGB', (100, 100), color=(120, 120, 120))
    masks = {
        'subject': np.ones((100, 100), dtype=np.float32),
        'sky': np.zeros((100, 100), dtype=np.float32),
        'background': np.zeros((100, 100), dtype=np.float32),
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
