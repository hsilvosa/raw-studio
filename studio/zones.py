"""Zonal editing engine for local RAW studio.

Provides intelligent segmentation for:
- Sujeto (Subject / Foreground)
- Cielo (Sky)
- Piel / Rostro (Skin Tone / Face)
- Fondo (Background)
- Primer Plano (Foreground)

Provides manual mask generation:
- Pincel manual (Brush)
- Degradado radial (Radial Gradient)
- Degradado lineal (Linear Gradient)

Allows precision manual and model-driven zonal photographic corrections:
- Luz (Calibrated EV exposure & S-curve contrast)
- Color (Temperature, tint & saturation)
- Detalle (Clarity & multi-scale micro-contrast)
- Desenfoque (Defocus / Gaussian Bokeh blur)
"""
import copy
import math
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy import ndimage


DEFAULT_ZONE_PARAMS = {
    'subject': {'light': 0.0, 'contrast': 0.0, 'temp': 0.0, 'tint': 0.0, 'saturation': 1.0, 'detail': 0.20, 'blur': 0.0, 'feather': 12, 'sensitivity': 0, 'invert': False},
    'sky': {'light': -0.25, 'contrast': 0.10, 'temp': -10.0, 'tint': 0.0, 'saturation': 1.15, 'detail': 0.0, 'blur': 0.0, 'feather': 18, 'sensitivity': 0, 'invert': False},
    'skin': {'light': 0.15, 'contrast': -0.05, 'temp': 5.0, 'tint': 0.0, 'saturation': 1.0, 'detail': -0.10, 'blur': 0.0, 'feather': 14, 'sensitivity': 0, 'invert': False},
    'background': {'light': 0.0, 'contrast': -0.05, 'temp': 0.0, 'tint': 0.0, 'saturation': 0.95, 'detail': -0.10, 'blur': 4.0, 'feather': 15, 'sensitivity': 0, 'invert': False},
    'foreground': {'light': 0.0, 'contrast': 0.05, 'temp': 0.0, 'tint': 0.0, 'saturation': 1.0, 'detail': 0.15, 'blur': 0.0, 'feather': 12, 'sensitivity': 0, 'invert': False},
    'brush': {'light': 0.35, 'contrast': 0.0, 'temp': 0.0, 'tint': 0.0, 'saturation': 1.0, 'detail': 0.20, 'blur': 0.0, 'feather': 10, 'sensitivity': 0, 'invert': False},
    'radial': {'light': 0.25, 'contrast': 0.05, 'temp': 0.0, 'tint': 0.0, 'saturation': 1.0, 'detail': 0.10, 'blur': 0.0, 'feather': 20, 'sensitivity': 0, 'invert': False},
    'linear': {'light': -0.30, 'contrast': 0.10, 'temp': -8.0, 'tint': 0.0, 'saturation': 1.10, 'detail': 0.0, 'blur': 0.0, 'feather': 25, 'sensitivity': 0, 'invert': False},
}


def generate_zone_masks(im_or_path):
    """Compute normalized [0.0, 1.0] float masks for subject, sky, skin, background, foreground."""
    if isinstance(im_or_path, (str, Path)):
        with Image.open(im_or_path) as im:
            return generate_zone_masks(im)

    im = im_or_path.convert('RGB')
    w, h = im.size
    # Work at working resolution ~800px for responsive mask generation
    scale = min(1.0, 800.0 / max(w, h))
    rw, rh = max(16, int(w * scale)), max(16, int(h * scale))
    im_scaled = im.resize((rw, rh), Image.Resampling.BILINEAR)

    rgb = np.asarray(im_scaled, dtype=np.float32) / 255.0
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b

    max_c = np.maximum(np.maximum(r, g), b)
    min_c = np.minimum(np.minimum(r, g), b)
    delta_c = max_c - min_c
    sat = np.where(max_c > 1e-4, delta_c / (max_c + 1e-6), 0.0)

    # 1. Sky Mask Detection
    y_coords, x_coords = np.mgrid[0:rh, 0:rw]
    vert_sky_prior = np.clip(1.0 - (y_coords / (rh * 0.65)), 0.0, 1.0) ** 1.6

    # Gradient / texture smoothness (sky has low high-frequency gradient)
    sobel_y = ndimage.sobel(lum, axis=0)
    sobel_x = ndimage.sobel(lum, axis=1)
    grad_mag = np.hypot(sobel_x, sobel_y)
    smoothness = np.clip(1.0 - (grad_mag / 0.25), 0.0, 1.0)

    # Sky color characteristics: blue sky or bright overcast/neutral
    blue_chroma = np.clip((b - np.maximum(r, g * 0.90)) / 0.15, 0.0, 1.0)
    overcast_chroma = (sat < 0.18) & (lum > 0.45)
    sky_chroma = np.maximum(blue_chroma, overcast_chroma.astype(np.float32) * 0.85)

    sky_raw = vert_sky_prior * sky_chroma * smoothness * (lum > 0.25)
    sky_mask = np.clip((sky_raw - 0.12) / 0.35, 0.0, 1.0)
    sky_mask = ndimage.gaussian_filter(sky_mask, sigma=max(2.0, rh * 0.015))

    # 2. Skin Tone / Face Mask Detection
    # HSV Hue calculation
    hsv_h = np.zeros_like(lum)
    mask_r = (max_c == r) & (delta_c > 1e-4)
    mask_g = (max_c == g) & (delta_c > 1e-4)
    mask_b = (max_c == b) & (delta_c > 1e-4)
    hsv_h[mask_r] = ((g[mask_r] - b[mask_r]) / (delta_c[mask_r] + 1e-6)) % 6.0
    hsv_h[mask_g] = ((b[mask_g] - r[mask_g]) / (delta_c[mask_g] + 1e-6)) + 2.0
    hsv_h[mask_b] = ((r[mask_b] - g[mask_b]) / (delta_c[mask_b] + 1e-6)) + 4.0
    hsv_h = (hsv_h / 6.0) % 1.0

    # Skin locus: 12°-36° hue (0.025-0.11), healthy saturation and luminance, R > G > B
    skin_raw = (
        (hsv_h >= 0.025) & (hsv_h <= 0.115) &
        (sat >= 0.14) & (sat <= 0.68) &
        (lum >= 0.16) & (lum <= 0.92) &
        (r > g) & (g > b) &
        ((r - g) >= 0.03)
    ).astype(np.float32)

    # Morphological cohesion to smooth skin clusters and faces
    skin_mask = ndimage.gaussian_filter(skin_raw, sigma=max(1.8, rh * 0.008))
    skin_mask = np.clip(skin_mask * 1.5, 0.0, 1.0)
    skin_mask = ndimage.gaussian_filter(skin_mask, sigma=max(1.5, rh * 0.006))

    # 3. Subject Mask Detection
    # Center-weighted spatial prior
    cy, cx = rh * 0.46, rw * 0.50
    dist_sq = ((y_coords - cy) / (rh * 0.42)) ** 2 + ((x_coords - cx) / (rw * 0.38)) ** 2
    center_prior = np.exp(-0.5 * dist_sq)

    # Sharpness / detail saliency (subject is typically in focus)
    local_detail = np.clip(grad_mag / 0.15, 0.0, 1.0)
    smooth_detail = ndimage.gaussian_filter(local_detail, sigma=max(3.0, rh * 0.02))

    # Luminance contrast against local surroundings
    local_mean_lum = ndimage.gaussian_filter(lum, sigma=max(8.0, rh * 0.06))
    lum_contrast = np.abs(lum - local_mean_lum)

    subject_raw = (
        0.30 * center_prior +
        0.35 * skin_mask +
        0.20 * smooth_detail +
        0.15 * np.clip(lum_contrast * 3.0, 0.0, 1.0)
    ) * (1.0 - 0.90 * sky_mask)

    subject_mask = np.clip((subject_raw - 0.20) / 0.38, 0.0, 1.0)
    subject_mask = ndimage.gaussian_filter(subject_mask, sigma=max(3.0, rh * 0.018))

    # 4. Foreground Mask Detection (Bottom perspective & sharp details)
    fg_prior = np.clip((y_coords - rh * 0.45) / (rh * 0.55), 0.0, 1.0) ** 1.3
    fg_raw = fg_prior * (0.5 * smooth_detail + 0.5) * (1.0 - 0.7 * sky_mask)
    foreground_mask = np.clip((fg_raw - 0.25) / 0.45, 0.0, 1.0)
    foreground_mask = ndimage.gaussian_filter(foreground_mask, sigma=max(3.0, rh * 0.018))

    # 5. Background Mask Detection
    # Residual non-subject, non-sky region
    bg_raw = np.clip(1.0 - 0.85 * subject_mask - 0.80 * sky_mask - 0.40 * foreground_mask, 0.0, 1.0)
    bg_mask = ndimage.gaussian_filter(bg_raw, sigma=max(3.0, rh * 0.015))

    # Re-normalize to target original image resolution (w, h)
    def resize_mask(m):
        m_uint = (np.clip(m, 0.0, 1.0) * 255).astype(np.uint8)
        m_im = Image.fromarray(m_uint, mode='L').resize((w, h), Image.Resampling.BILINEAR)
        return np.asarray(m_im, dtype=np.float32) / 255.0

    return {
        'subject': resize_mask(subject_mask),
        'sky': resize_mask(sky_mask),
        'skin': resize_mask(skin_mask),
        'background': resize_mask(bg_mask),
        'foreground': resize_mask(foreground_mask),
    }


def generate_radial_mask(shape, cx=0.5, cy=0.5, rx=0.3, ry=0.3, angle=0.0, feather=20):
    """Generate a feathered elliptical radial gradient mask in normalized coordinates (0..1)."""
    h, w = shape
    y, x = np.mgrid[0:h, 0:w]
    nx = x / float(max(1, w))
    ny = y / float(max(1, h))

    # Rotate around center
    rad = math.radians(angle)
    dx = nx - cx
    dy = ny - cy
    rot_x = dx * math.cos(rad) + dy * math.sin(rad)
    rot_y = -dx * math.sin(rad) + dy * math.cos(rad)

    # Elliptical distance metric
    rx = max(0.01, rx)
    ry = max(0.01, ry)
    dist = np.sqrt((rot_x / rx) ** 2 + (rot_y / ry) ** 2)

    # Falloff: 1.0 at center, drops smoothly to 0.0 at perimeter with feathering
    feather_ratio = max(0.05, min(1.0, feather / 50.0))
    mask = np.clip((1.0 - dist) / max(0.05, feather_ratio), 0.0, 1.0)
    # Cosine smoothstep for natural photographic optical falloff
    mask = 0.5 - 0.5 * np.cos(mask * math.pi)
    return mask.astype(np.float32)


def generate_linear_mask(shape, x1=0.5, y1=0.2, x2=0.5, y2=0.8, feather=25):
    """Generate a directional linear gradient mask with normalized coordinates (0..1)."""
    h, w = shape
    y, x = np.mgrid[0:h, 0:w]
    nx = x / float(max(1, w))
    ny = y / float(max(1, h))

    # Vector from p1 to p2
    vx = x2 - x1
    vy = y2 - y1
    v_len_sq = vx * vx + vy * vy
    if v_len_sq < 1e-6:
        return np.zeros((h, w), dtype=np.float32)

    # Project point onto line segment: t = dot(p - p1, v) / |v|^2
    t = ((nx - x1) * vx + (ny - y1) * vy) / v_len_sq
    # Cosine smoothstep falloff between 0 and 1
    t_clamped = np.clip(t, 0.0, 1.0)
    mask = 1.0 - (0.5 - 0.5 * np.cos(t_clamped * math.pi))
    if feather > 1:
        mask = ndimage.gaussian_filter(mask, sigma=feather * 0.3)
    return np.clip(mask, 0.0, 1.0).astype(np.float32)


def rasterize_brush_mask(shape, strokes, feather=10):
    """Rasterize manual brush strokes into a float mask.

    Each stroke: {'x': float (norm 0..1), 'y': float (norm 0..1), 'radius': float (px), 'erase': bool}
    or list of connected path points: {'path': [{'x', 'y'}], 'radius': float, 'erase': bool}.
    """
    h, w = shape
    mask_im = Image.new('L', (w, h), 0)
    draw = ImageDraw.Draw(mask_im)

    for item in strokes:
        radius = float(item.get('radius', 24.0))
        erase = bool(item.get('erase', False))
        color = 0 if erase else 255

        if 'path' in item and isinstance(item['path'], list) and len(item['path']) > 0:
            pts = [(int(p['x'] * w), int(p['y'] * h)) for p in item['path']]
            if len(pts) == 1:
                x, y = pts[0]
                draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=color)
            else:
                for i in range(len(pts) - 1):
                    x0, y0 = pts[i]
                    x1, y1 = pts[i + 1]
                    draw.line([x0, y0, x1, y1], fill=color, width=int(radius * 2))
                    draw.ellipse([x1 - radius, y1 - radius, x1 + radius, y1 + radius], fill=color)
                # First cap
                x0, y0 = pts[0]
                draw.ellipse([x0 - radius, y0 - radius, x0 + radius, y0 + radius], fill=color)
        elif 'x' in item and 'y' in item:
            x = int(float(item['x']) * w)
            y = int(float(item['y']) * h)
            draw.ellipse([x - radius, y - radius, x + radius, y + radius], fill=color)

    mask_arr = np.asarray(mask_im, dtype=np.float32) / 255.0
    if feather > 1:
        mask_arr = ndimage.gaussian_filter(mask_arr, sigma=feather * 0.4)
    mask_arr = np.where(mask_arr < 1e-4, 0.0, mask_arr)
    return np.clip(mask_arr, 0.0, 1.0)


def build_manual_masks(shape, manual_configs):
    """Build raster masks for manual tools (brush, radial, linear) given shape (h, w)."""
    h, w = shape
    result = {}
    if not manual_configs or not isinstance(manual_configs, dict):
        return result

    if 'brush' in manual_configs and isinstance(manual_configs['brush'], dict):
        b_cfg = manual_configs['brush']
        strokes = b_cfg.get('strokes', [])
        if strokes:
            result['brush'] = rasterize_brush_mask((h, w), strokes, feather=b_cfg.get('feather', 10))

    if 'radial' in manual_configs and isinstance(manual_configs['radial'], dict):
        r_cfg = manual_configs['radial']
        result['radial'] = generate_radial_mask(
            (h, w),
            cx=float(r_cfg.get('cx', 0.5)),
            cy=float(r_cfg.get('cy', 0.5)),
            rx=float(r_cfg.get('rx', 0.3)),
            ry=float(r_cfg.get('ry', 0.3)),
            angle=float(r_cfg.get('angle', 0.0)),
            feather=float(r_cfg.get('feather', 20))
        )

    if 'linear' in manual_configs and isinstance(manual_configs['linear'], dict):
        l_cfg = manual_configs['linear']
        result['linear'] = generate_linear_mask(
            (h, w),
            x1=float(l_cfg.get('x1', 0.5)),
            y1=float(l_cfg.get('y1', 0.2)),
            x2=float(l_cfg.get('x2', 0.5)),
            y2=float(l_cfg.get('y2', 0.8)),
            feather=float(l_cfg.get('feather', 25))
        )

    return result


def refine_mask(mask, sensitivity=0, feather=12, invert=False):
    """Fine-tune a raw zone mask with user sensitivity, feathering, and inversion."""
    m = mask.copy()
    if invert:
        m = 1.0 - m

    # Sensitivity / Threshold shift (-50 to +50)
    shift = sensitivity / 100.0
    if shift != 0:
        center = 0.5 - shift * 0.35
        width = max(0.10, 0.50 - abs(shift) * 0.25)
        m = np.clip((m - (center - width * 0.5)) / width, 0.0, 1.0)

    # Feathering blur radius
    if feather > 1:
        m = ndimage.gaussian_filter(m, sigma=feather * 0.4)

    return np.clip(m, 0.0, 1.0)


def create_ruby_overlay(im, mask, color=(240, 45, 55), alpha=0.45):
    """Generate a visual ruby mask overlay on the image for UI display."""
    im_rgb = im.convert('RGB')
    arr = np.asarray(im_rgb, dtype=np.float32)

    overlay = np.zeros_like(arr)
    overlay[..., 0] = color[0]
    overlay[..., 1] = color[1]
    overlay[..., 2] = color[2]

    # Mask scaled into alpha blend
    m3 = np.repeat(mask[..., np.newaxis], 3, axis=2) * alpha
    blended = arr * (1.0 - m3) + overlay * m3
    return Image.fromarray(np.clip(blended, 0, 255).astype(np.uint8), mode='RGB')


def compute_ai_zone_adjustments(im, masks):
    """Assess zone telemetry and compute balanced, protective regional adjustments.

    - Protects skin tone vibrancy & avoids orange/reddish casts.
    - Softens blown sky highlights and recovers atmospheric cloud detail.
    - Enhances subject illumination and micro-contrast.
    - Creates subtle depth separation in the background.
    """
    im_rgb = im.convert('RGB')
    arr = np.asarray(im_rgb, dtype=np.float32) / 255.0
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b

    params = copy.deepcopy(DEFAULT_ZONE_PARAMS)

    # 1. Sky Analysis & Calibration
    sky_m = masks.get('sky')
    if sky_m is not None and np.sum(sky_m > 0.3) > 100:
        sky_lum = float(np.mean(lum[sky_m > 0.3]))
        # If sky is bright/washed out, pull down exposure and boost cloud contrast
        if sky_lum > 0.65:
            params['sky']['light'] = -0.45
            params['sky']['contrast'] = 0.15
            params['sky']['saturation'] = 1.20
            params['sky']['detail'] = 0.15
        elif sky_lum > 0.40:
            params['sky']['light'] = -0.25
            params['sky']['contrast'] = 0.10
            params['sky']['saturation'] = 1.12

    # 2. Subject Analysis & Calibration
    subj_m = masks.get('subject')
    if subj_m is not None and np.sum(subj_m > 0.3) > 100:
        subj_lum = float(np.mean(lum[subj_m > 0.3]))
        # If subject is backlit or slightly underexposed relative to scene
        if subj_lum < 0.25:
            params['subject']['light'] = 0.40
            params['subject']['detail'] = 0.25
        elif subj_lum < 0.40:
            params['subject']['light'] = 0.20
            params['subject']['detail'] = 0.18

    # 3. Skin Tone Analysis & Protection
    skin_m = masks.get('skin')
    if skin_m is not None and np.sum(skin_m > 0.3) > 80:
        skin_lum = float(np.mean(lum[skin_m > 0.3]))
        # Gentle exposure lift, soften micro-contrast, natural warm glow without reddish boost
        if skin_lum < 0.30:
            params['skin']['light'] = 0.25
        else:
            params['skin']['light'] = 0.10
        params['skin']['contrast'] = -0.04
        params['skin']['temp'] = 4.0
        params['skin']['saturation'] = 1.02
        params['skin']['detail'] = -0.08

    # 4. Background & Bokeh Depth
    bg_m = masks.get('background')
    if bg_m is not None and np.sum(bg_m > 0.3) > 100:
        params['background']['blur'] = 3.5
        params['background']['detail'] = -0.10
        params['background']['contrast'] = -0.05

    return params


def apply_zone_adjustments(im, masks, zone_params, manual_masks=None):
    """Apply calibrated photographic adjustments to each zone.

    Per zone:
    - light: EV exposure (-2.0 to +2.0)
    - contrast: contrast boost (-0.5 to +0.5)
    - temp: warm/cool (-50 to +50)
    - tint: green/magenta (-50 to +50)
    - saturation: 0.0 to 2.0
    - detail: clarity/micro-contrast (-0.5 to +1.0)
    - blur: Gaussian defocus in pixels (0.0 to 25.0)
    """
    im_rgb = im.convert('RGB')
    w, h = im_rgb.size
    rgb = np.asarray(im_rgb, dtype=np.float32) / 255.0

    all_masks = dict(masks)
    if manual_masks:
        all_masks.update(manual_masks)

    # Process background and environmental zones first, then focal zones (subject, skin, manual brush)
    order = ['background', 'foreground', 'sky', 'linear', 'radial', 'subject', 'skin', 'brush']

    for zone_name in order:
        if zone_name not in zone_params:
            continue
        params = zone_params[zone_name]
        raw_mask = all_masks.get(zone_name)
        if raw_mask is None:
            continue

        # Resize mask if dimensions don't match image (e.g. at 6000px export)
        if raw_mask.shape != (h, w):
            m_uint = (np.clip(raw_mask, 0.0, 1.0) * 255).astype(np.uint8)
            m_im = Image.fromarray(m_uint, mode='L').resize((w, h), Image.Resampling.BILINEAR)
            raw_mask = np.asarray(m_im, dtype=np.float32) / 255.0

        mask = refine_mask(
            raw_mask,
            sensitivity=params.get('sensitivity', 0),
            feather=params.get('feather', 12),
            invert=params.get('invert', False)
        )
        if np.max(mask) < 0.01:
            continue

        m3 = np.repeat(mask[..., np.newaxis], 3, axis=2)

        # 1. Defocus / Blur
        blur_radius = float(params.get('blur', 0.0))
        if blur_radius > 0.4:
            # Scale blur appropriately if resolution is high (e.g. 6000px export)
            scaled_blur = blur_radius * (max(w, h) / 1200.0)
            blurred = np.zeros_like(rgb)
            for c in range(3):
                blurred[..., c] = ndimage.gaussian_filter(rgb[..., c], sigma=scaled_blur)
            rgb = rgb * (1.0 - m3) + blurred * m3

        # 2. Detail / Clarity
        detail = float(params.get('detail', 0.0))
        if abs(detail) > 0.02:
            detail_sigma = 2.0 * (max(w, h) / 1200.0)
            base_blur = np.zeros_like(rgb)
            for c in range(3):
                base_blur[..., c] = ndimage.gaussian_filter(rgb[..., c], sigma=detail_sigma)
            high_pass = rgb - base_blur
            rgb = rgb + high_pass * (detail * 1.5) * m3

        # 3. Light (Exposure & Contrast)
        light_ev = float(params.get('light', 0.0))
        if abs(light_ev) > 0.02:
            exp_factor = 2.0 ** (light_ev * mask)
            for c in range(3):
                rgb[..., c] = rgb[..., c] * exp_factor

        contrast = float(params.get('contrast', 0.0))
        if abs(contrast) > 0.02:
            # S-curve around mid-grey (0.18)
            pivot = 0.18
            cont_factor = 1.0 + contrast * mask
            for c in range(3):
                diff = rgb[..., c] - pivot
                rgb[..., c] = pivot + diff * cont_factor

        # 4. Color (Temperature, Tint & Saturation)
        temp = float(params.get('temp', 0.0))
        tint = float(params.get('tint', 0.0))
        if abs(temp) > 0.5 or abs(tint) > 0.5:
            # Temp: positive = warm (+R, -B); negative = cool (-R, +B)
            # Tint: positive = magenta (+R, +B, -G); negative = green (+G)
            r_gain = 1.0 + (temp * 0.006 + tint * 0.004) * mask
            g_gain = 1.0 + (-tint * 0.007) * mask
            b_gain = 1.0 + (-temp * 0.006 + tint * 0.004) * mask
            rgb[..., 0] = rgb[..., 0] * r_gain
            rgb[..., 1] = rgb[..., 1] * g_gain
            rgb[..., 2] = rgb[..., 2] * b_gain

        saturation = float(params.get('saturation', 1.0))
        if abs(saturation - 1.0) > 0.02:
            lum = 0.2126 * rgb[..., 0] + 0.7152 * rgb[..., 1] + 0.0722 * rgb[..., 2]
            lum3 = np.repeat(lum[..., np.newaxis], 3, axis=2)
            sat_factor = 1.0 + (saturation - 1.0) * m3
            rgb = lum3 + (rgb - lum3) * sat_factor

    rgb = np.clip(rgb, 0.0, 1.0)
    return Image.fromarray((rgb * 255.0).astype(np.uint8), mode='RGB')
