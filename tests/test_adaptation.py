import numpy as np
from PIL import Image
from studio.adaptation import analyze_scene, adapt_stack
from studio.recipes import profiles, make_stack, LIMITS


def test_analyze_scene_synthetic():
    # Create an RGB image with skin tone region, bright highlight, and dark shadow
    arr = np.zeros((100, 100, 3), dtype=np.uint8)
    # Background neutral grey
    arr[:, :] = [100, 100, 100]
    # Skin tone rectangle (warm reddish-peach)
    arr[20:60, 20:60] = [210, 145, 115]
    # Highlight
    arr[0:5, 0:5] = [255, 255, 255]

    im = Image.fromarray(arr, mode='RGB')
    telemetry = analyze_scene(im)

    assert 0.0 < telemetry['median'] < 1.0
    assert telemetry['skin_detected'] is True
    assert telemetry['skin_fraction'] > 0.05
    assert telemetry['white_fraction'] > 0.0
    assert 'noise_sigma' in telemetry


def test_adapt_stack_skin_protection():
    # When skin is detected, vibrance and saturation must be bounded
    telemetry = {
        'median': 0.20,
        'p5': 0.02,
        'p95': 0.85,
        'p99': 0.92,
        'black_fraction': 0.02,
        'white_fraction': 0.001,
        'specular_fraction': 0.0,
        'dynamic_range_ev': 5.0,
        'temp_bias': 0.0,
        'tint_bias': 0.0,
        'skin_detected': True,
        'skin_fraction': 0.12,
        'skin_mean_lum': 0.40,
        'skin_mean_sat': 0.45,
        'noise_sigma': 0.01,
        'is_noisy': False,
        'width': 800,
        'height': 600,
    }

    profile = profiles()[0]
    base_stack = make_stack(profile, 0.0, 1.0)
    new_stack, explanation, _ = adapt_stack(base_stack, telemetry, 0.0, 1.0)

    cb = next(m for m in new_stack if m['operation'] == 'colorbalancergb')
    # Vibrance should be capped for skin protection
    assert cb['params']['vibrance'] <= 0.08
    assert cb['params']['saturation_global'] <= 0.05
    assert "tonos de piel" in explanation.lower()


def test_adapt_stack_highlight_and_noise():
    # Test highlight clipping risk & noise adaptation
    telemetry = {
        'median': 0.05,  # dark scene
        'p5': 0.01,
        'p95': 0.99,
        'p99': 0.995,
        'black_fraction': 0.15,
        'white_fraction': 0.025,  # high highlight risk!
        'specular_fraction': 0.01,
        'dynamic_range_ev': 6.5,
        'temp_bias': -0.25,  # cold cast
        'tint_bias': 0.0,
        'skin_detected': False,
        'skin_fraction': 0.0,
        'skin_mean_lum': 0.0,
        'skin_mean_sat': 0.0,
        'noise_sigma': 0.045,  # noisy!
        'is_noisy': True,
        'width': 800,
        'height': 600,
    }

    profile = profiles()[0]
    base_stack = make_stack(profile, 0.0, 0.8)
    new_stack, explanation, _ = adapt_stack(base_stack, telemetry, 0.0, 0.8)

    # Exposure must be conservative due to highlight risk
    exp_mod = next(m for m in new_stack if m['operation'] == 'exposure')
    assert exp_mod['params']['exposure'] <= 0.15

    # Sigmoid should have softer contrast to protect highlights
    sig_mod = next(m for m in new_stack if m['operation'] == 'sigmoid')
    assert sig_mod['params']['middle_grey_contrast'] <= 1.35

    # Bilateral smoothing should be added or boosted for noise
    assert any(m['operation'] == 'bilat' for m in new_stack)

    # Sharpen threshold should be higher to protect grain
    sh_mod = next(m for m in new_stack if m['operation'] == 'sharpen')
    assert sh_mod['params']['threshold'] >= 1.2
