"""Photographic scene adaptation engine for local RAW studio.

Analyzes scene luminance, white balance / color cast, skin tones, highlights,
and noise to dynamically produce balanced, protective Darktable parameters.
"""
import copy
import math
from pathlib import Path
import numpy as np
from PIL import Image


def analyze_scene(image_path_or_im):
    """Compute detailed photographic telemetry for an image."""
    if isinstance(image_path_or_im, (str, Path)):
        with Image.open(image_path_or_im) as im:
            return analyze_scene(im)

    im = image_path_or_im
    w, h = im.size
    # Work on a standardized resolution for consistent analysis speed (~800px max)
    scale = min(1.0, 800.0 / max(w, h))
    if scale < 1.0:
        im_small = im.resize((int(w * scale), int(h * scale)), Image.Resampling.BILINEAR)
    else:
        im_small = im

    rgb = np.asarray(im_small.convert('RGB'), dtype=np.float32) / 255.0
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]

    # Standard Rec.709 relative luminance
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    flat_lum = lum.ravel()

    # 1. Luminance & Dynamic Range Percentiles
    p5 = float(np.percentile(flat_lum, 5))
    p50 = float(np.percentile(flat_lum, 50))
    p95 = float(np.percentile(flat_lum, 95))
    p99 = float(np.percentile(flat_lum, 99))
    black_fraction = float(np.mean(flat_lum < 0.025))
    white_fraction = float(np.mean(flat_lum > 0.975))
    specular_fraction = float(np.mean(flat_lum > 0.995))
    dynamic_range_ev = float(math.log2(max(1e-4, p95) / max(1e-4, p5)))

    # 2. White Balance & Neutral Color Cast Analysis
    # Midtones with low saturation represent neutral reference candidates
    max_c = np.maximum(np.maximum(r, g), b)
    min_c = np.minimum(np.minimum(r, g), b)
    delta_c = max_c - min_c
    sat = np.where(max_c > 1e-4, delta_c / (max_c + 1e-6), 0.0)

    neutral_mask = (lum >= 0.18) & (lum <= 0.82) & (sat <= 0.28)
    neutral_count = int(np.sum(neutral_mask))
    total_pixels = flat_lum.size

    if neutral_count > total_pixels * 0.02:
        mean_r = float(np.mean(r[neutral_mask]))
        mean_g = float(np.mean(g[neutral_mask]))
        mean_b = float(np.mean(b[neutral_mask]))
        temp_bias = float((mean_r - mean_b) / (mean_r + mean_b + 1e-6))
        tint_bias = float((mean_g - 0.5 * (mean_r + mean_b)) / (mean_g + 0.5 * (mean_r + mean_b) + 1e-6))
    else:
        # Fallback to overall image midtone averages
        mean_r = float(np.mean(r))
        mean_g = float(np.mean(g))
        mean_b = float(np.mean(b))
        temp_bias = float((mean_r - mean_b) / (mean_r + mean_b + 1e-6))
        tint_bias = 0.0

    # 3. Skin Tone Detection & Protection
    # Normalized chromaticity & HSV bounding for human skin tones
    # Skin hues span ~ 12° to 36° (0.033 to 0.100 in 0-1 range) with moderate saturation
    hsv_h = np.zeros_like(lum)
    mask_r = (max_c == r) & (delta_c > 1e-4)
    mask_g = (max_c == g) & (delta_c > 1e-4)
    mask_b = (max_c == b) & (delta_c > 1e-4)
    hsv_h[mask_r] = ((g[mask_r] - b[mask_r]) / (delta_c[mask_r] + 1e-6)) % 6.0
    hsv_h[mask_g] = ((b[mask_g] - r[mask_g]) / (delta_c[mask_g] + 1e-6)) + 2.0
    hsv_h[mask_b] = ((r[mask_b] - g[mask_b]) / (delta_c[mask_b] + 1e-6)) + 4.0
    hsv_h = (hsv_h / 6.0) % 1.0

    # Classic skin detection in RGB: R > G > B with natural ratios
    skin_mask = (
        (hsv_h >= 0.025) & (hsv_h <= 0.11) &
        (sat >= 0.15) & (sat <= 0.68) &
        (lum >= 0.18) & (lum <= 0.90) &
        (r > g) & (g > b) &
        ((r - g) >= 0.04)
    )
    skin_fraction = float(np.mean(skin_mask))
    skin_detected = skin_fraction >= 0.015

    skin_mean_lum = float(np.mean(lum[skin_mask])) if skin_detected else 0.0
    skin_mean_sat = float(np.mean(sat[skin_mask])) if skin_detected else 0.0

    # 4. Noise & Granularity Estimation (High-frequency local variance in flat areas)
    # Using lightweight Laplacian kernel approximation via slices
    laplacian = np.abs(
        4 * lum[1:-1, 1:-1]
        - lum[:-2, 1:-1] - lum[2:, 1:-1]
        - lum[1:-1, :-2] - lum[1:-1, 2:]
    )
    # Exclude strong contrast edges from noise calculation
    flat_areas = laplacian < np.percentile(laplacian, 75)
    noise_sigma = float(np.std(laplacian[flat_areas])) if np.any(flat_areas) else 0.02
    is_noisy = noise_sigma > 0.032 or (p50 < 0.12 and black_fraction > 0.10)

    return {
        'median': p50,
        'p5': p5,
        'p95': p95,
        'p99': p99,
        'black_fraction': black_fraction,
        'white_fraction': white_fraction,
        'specular_fraction': specular_fraction,
        'dynamic_range_ev': dynamic_range_ev,
        'temp_bias': temp_bias,
        'tint_bias': tint_bias,
        'skin_detected': skin_detected,
        'skin_fraction': skin_fraction,
        'skin_mean_lum': skin_mean_lum,
        'skin_mean_sat': skin_mean_sat,
        'noise_sigma': noise_sigma,
        'is_noisy': is_noisy,
        'width': w,
        'height': h,
    }


def adapt_stack(base_stack, telemetry, user_exposure=0.0, user_intensity=0.8):
    """Generate adaptive Darktable adjustments based on scene telemetry.

    Applies:
    - Exposure compensation bounded by highlight preservation.
    - White balance and temperature/tint neutrality or mood preservation.
    - Skin tone protection (capping excessive saturation and harsh contrast).
    - Highlight and white clipping protection (sigmoid and highlights compression).
    - Adaptive noise filtering and photo-specific sharpening.
    """
    stack = copy.deepcopy(base_stack)
    decisions = []

    # 1. Bounded Exposure Compensation
    target_median = 0.22
    if telemetry['median'] < 0.08:
        # Dark / night scene: preserve mood, do NOT brighten into daylight
        raw_exposure = math.log2(max(0.06, telemetry['median'] * 1.5) / max(0.02, telemetry['median']))
        raw_exposure = min(0.65, max(0.0, raw_exposure))
        decisions.append(f"Ambiente nocturno/oscuro preservado (+{raw_exposure:.2f} EV adaptativo)")
    else:
        raw_exposure = math.log2(target_median / max(0.04, telemetry['median']))
        raw_exposure = max(-0.6, min(1.10, raw_exposure))
        decisions.append(f"Exposición calibrada al punto medio (+{raw_exposure:.2f} EV adaptativo)")

    # Highlight protection clamp
    highlight_risk = telemetry['white_fraction'] > 0.012 or telemetry['specular_fraction'] > 0.004 or telemetry['p99'] > 0.98
    if highlight_risk:
        raw_exposure = min(raw_exposure, 0.15)
        decisions.append("Protección de altas luces activada (límite de exposición aplicado para evitar quemados)")

    final_exposure = max(-2.0, min(4.0, raw_exposure + user_exposure))

    # Apply to exposure module in stack
    exp_mod = next((m for m in stack if m['operation'] == 'exposure'), None)
    if exp_mod:
        exp_mod['params']['exposure'] = final_exposure
    else:
        stack.insert(0, {'operation': 'exposure', 'params': {'exposure': final_exposure, 'black': -0.0001, 'compensate_exposure_bias': True}})

    # 2. Color Balance & White Balance Adaptation
    cb_mod = next((m for m in stack if m['operation'] == 'colorbalancergb'), None)
    if cb_mod is None:
        cb_mod = {'operation': 'colorbalancergb', 'params': {}}
        stack.append(cb_mod)
    cb_params = cb_mod.setdefault('params', {})

    # Scene color cast compensation
    if telemetry['temp_bias'] < -0.18:
        # Scene has a heavy cold/blue cast: gentle warm compensation
        cb_params['global_H'] = 45.0
        cb_params['global_C'] = min(0.008, abs(telemetry['temp_bias']) * 0.02)
        decisions.append("Balance de blancos compensado hacia tonos neutros cálidos")
    elif telemetry['temp_bias'] > 0.35 and not telemetry['skin_detected']:
        # Excessive warm cast (e.g. tungsten) without skin: gentle cooling
        cb_params['global_H'] = 220.0
        cb_params['global_C'] = min(0.008, (telemetry['temp_bias'] - 0.2) * 0.015)
        decisions.append("Reducción de dominante cálida excesiva en iluminación artificial")

    # 3. Skin Tone Protection
    if telemetry['skin_detected']:
        # Cap vibrance and saturation to prevent artificial or reddish/orange skin
        current_vib = cb_params.get('vibrance', 0.0)
        current_sat = cb_params.get('saturation_global', 0.0)
        cb_params['vibrance'] = max(-0.10, min(0.08, current_vib))
        cb_params['saturation_global'] = max(-0.20, min(0.05, current_sat))
        # Keep midtone contrast natural for smooth skin gradients
        current_cont = cb_params.get('contrast', 0.0)
        cb_params['contrast'] = max(-0.04, min(0.04, current_cont))
        decisions.append(f"Protección de tonos de piel ({telemetry['skin_fraction']*100:.1f}% detectado): saturación y micro-contraste controlados")

    # 4. Highlight & White Tone Roll-off (Sigmoid)
    sig_mod = next((m for m in stack if m['operation'] == 'sigmoid'), None)
    if sig_mod is None:
        sig_mod = {'operation': 'sigmoid', 'params': {}}
        stack.append(sig_mod)
    sig_params = sig_mod.setdefault('params', {})

    if highlight_risk:
        # Softer contrast to compress specular whites and recover sky/reflection detail
        sig_params['middle_grey_contrast'] = 1.30
        sig_params['display_black_target'] = 0.025
        cb_params['highlights_Y'] = -0.06
        decisions.append("Curva sigmoide ajustada con compresión suave de altas luces")
    elif telemetry['dynamic_range_ev'] < 3.2:
        # Low contrast scene: gently enhance separation
        sig_params['middle_grey_contrast'] = 1.55
        sig_params['display_black_target'] = 0.015

    # 5. Noise-Adaptive Sharpening & Denoising
    if telemetry['is_noisy']:
        # High noise scene: activate bilateral smoothing and conservative sharpening
        bilat_mod = next((m for m in stack if m['operation'] == 'bilat'), None)
        if not bilat_mod:
            stack.append({'operation': 'bilat', 'params': {'detail': 0.16}})
        else:
            bilat_mod['params']['detail'] = max(0.12, bilat_mod['params'].get('detail', 0.0))

        # Sharpen with higher threshold so noise grain is NOT amplified
        sharpen_mod = next((m for m in stack if m['operation'] == 'sharpen'), None)
        if sharpen_mod:
            sharpen_mod['params'] = {'amount': 0.32, 'radius': 0.65, 'threshold': 1.4}
        else:
            stack.append({'operation': 'sharpen', 'params': {'amount': 0.32, 'radius': 0.65, 'threshold': 1.4}})
        decisions.append(f"Ajuste para ruido detectado (σ={telemetry['noise_sigma']:.3f}): reducción bilateral y enfoque con umbral protector")
    else:
        # Clean scene: apply crisp micro-contrast and fine-detail sharpening
        sharpen_mod = next((m for m in stack if m['operation'] == 'sharpen'), None)
        if sharpen_mod:
            sharpen_mod['params'] = {'amount': 0.55, 'radius': 0.65, 'threshold': 0.75}
        else:
            stack.append({'operation': 'sharpen', 'params': {'amount': 0.55, 'radius': 0.65, 'threshold': 0.75}})
        decisions.append("Escena limpia: enfoque nítido de alta definición aplicado")

    explanation = "; ".join(decisions) + "."
    return stack, explanation, telemetry
