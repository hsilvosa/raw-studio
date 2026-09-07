"""Zonal editing engine for local RAW studio.

Provides intelligent segmentation for:
- Sujeto (Subject / Foreground)
- Cielo (Sky)
- Fondo (Background)

Allows precision manual and model-driven zonal corrections:
- Luz (Exposure & contrast)
- Color (Temperature, tint & saturation)
- Detalle (Clarity & micro-contrast)
- Desenfoque (Defocus / Bokeh blur)
"""
import math
from pathlib import Path
import numpy as np
from PIL import Image, ImageFilter
from scipy import ndimage


DEFAULT_ZONE_PARAMS = {
    'subject': {'light': 0.0, 'contrast': 0.0, 'temp': 0.0, 'tint': 0.0, 'saturation': 1.0, 'detail': 0.20, 'blur': 0.0, 'feather': 12, 'sensitivity': 0, 'invert': False},
    'sky': {'light': -0.25, 'contrast': 0.10, 'temp': -10.0, 'tint': 0.0, 'saturation': 1.15, 'detail': 0.0, 'blur': 0.0, 'feather': 18, 'sensitivity': 0, 'invert': False},
    'background': {'light': 0.0, 'contrast': -0.05, 'temp': 0.0, 'tint': 0.0, 'saturation': 0.95, 'detail': -0.10, 'blur': 4.0, 'feather': 15, 'sensitivity': 0, 'invert': False},
}


def generate_zone_masks(im_or_path):
    """Compute normalized [0.0, 1.0] float masks for subject, sky, and background."""
    if isinstance(im_or_path, (str, Path)):
        with Image.open(im_or_path) as im:
            return generate_zone_masks(im)

    im = im_or_path.convert('RGB')
    w, h = im.size
    # Work at working resolution ~800px for responsive mask generation
    scale = min(1.0, 800.0 / max(w, h))
    rw, rh = int(w * scale), int(h * scale)
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
    vert_prior = np.clip(1.0 - (y_coords / (rh * 0.65)), 0.0, 1.0) ** 1.6

    # Gradient / texture smoothness
    sobel_y = ndimage.sobel(lum, axis=0)
    sobel_x = ndimage.sobel(lum, axis=1)
    grad_mag = np.hypot(sobel_x, sobel_y)
    smoothness = np.clip(1.0 - (grad_mag / 0.25), 0.0, 1.0)

    # Sky color characteristics: blue sky or bright overcast/neutral
    blue_chroma = np.clip((b - np.maximum(r, g * 0.90)) / 0.15, 0.0, 1.0)
    overcast_chroma = (sat < 0.18) & (lum > 0.45)
    sky_chroma = np.maximum(blue_chroma, overcast_chroma.astype(np.float32) * 0.85)

    sky_raw = vert_prior * sky_chroma * smoothness * (lum > 0.25)
    # Threshold and feather
    sky_mask = np.clip((sky_raw - 0.12) / 0.35, 0.0, 1.0)
    sky_mask = ndimage.gaussian_filter(sky_mask, sigma=max(2.0, rh * 0.015))

    # 2. Subject Mask Detection
    # Center-weighted spatial prior
    cy, cx = rh * 0.46, rw * 0.50
    dist_sq = ((y_coords - cy) / (rh * 0.42)) ** 2 + ((x_coords - cx) / (rw * 0.38)) ** 2
    center_prior = np.exp(-0.5 * dist_sq)

    # Skin tone saliency
    hsv_h = np.zeros_like(lum)
    mask_r = (max_c == r) & (delta_c > 1e-4)
    mask_g = (max_c == g) & (delta_c > 1e-4)
    mask_b = (max_c == b) & (delta_c > 1e-4)
    hsv_h[mask_r] = ((g[mask_r] - b[mask_r]) / (delta_c[mask_r] + 1e-6)) % 6.0
    hsv_h[mask_g] = ((b[mask_g] - r[mask_g]) / (delta_c[mask_g] + 1e-6)) + 2.0
    hsv_h[mask_b] = ((r[mask_b] - g[mask_b]) / (delta_c[mask_b] + 1e-6)) + 4.0
    hsv_h = (hsv_h / 6.0) % 1.0
    skin = (hsv_h >= 0.025) & (hsv_h <= 0.11) & (sat >= 0.15) & (sat <= 0.68) & (lum >= 0.16)

    # Sharpness / detail saliency (subject is typically in focus)
    local_detail = np.clip(grad_mag / 0.15, 0.0, 1.0)
    smooth_detail = ndimage.gaussian_filter(local_detail, sigma=max(3.0, rh * 0.02))

    # Luminance contrast against local surroundings
    local_mean_lum = ndimage.gaussian_filter(lum, sigma=max(8.0, rh * 0.06))
    lum_contrast = np.abs(lum - local_mean_lum)

    subject_raw = (
        0.35 * center_prior +
        0.35 * skin.astype(np.float32) +
        0.20 * smooth_detail +
        0.10 * np.clip(lum_contrast * 3.0, 0.0, 1.0)
    ) * (1.0 - 0.90 * sky_mask)

    subject_mask = np.clip((subject_raw - 0.22) / 0.38, 0.0, 1.0)
    subject_mask = ndimage.gaussian_filter(subject_mask, sigma=max(3.0, rh * 0.018))

    # 3. Background Mask Detection
    # Residual non-subject, non-sky region
    bg_raw = np.clip(1.0 - subject_mask - 0.75 * sky_mask, 0.0, 1.0)
    bg_mask = ndimage.gaussian_filter(bg_raw, sigma=max(3.0, rh * 0.015))

    # Re-normalize to target original image resolution (w, h)
    def resize_mask(m):
        m_uint = (np.clip(m, 0.0, 1.0) * 255).astype(np.uint8)
        m_im = Image.fromarray(m_uint, mode='L').resize((w, h), Image.Resampling.BILINEAR)
        return np.asarray(m_im, dtype=np.float32) / 255.0

    return {
        'subject': resize_mask(subject_mask),
        'sky': resize_mask(sky_mask),
        'background': resize_mask(bg_mask),
    }


def refine_mask(mask, sensitivity=0, feather=12, invert=False):
    """Fine-tune a raw zone mask with user sensitivity, feathering, and inversion."""
    m = mask.copy()
    if invert:
        m = 1.0 - m

    # Sensitivity / Threshold shift (-50 to +50)
    shift = sensitivity / 100.0
    if shift != 0:
        m = np.clip((m - (0.5 - shift * 0.4)) / max(0.1, 0.5 - abs(shift) * 0.2), 0.0, 1.0)

    # Feathering blur radius
    if feather > 1:
        m = ndimage.gaussian_filter(m, sigma=feather * 0.5)

    return np.clip(m, 0.0, 1.0)


def create_ruby_overlay(im, mask, color=(240, 45, 55), alpha=0.45):
    """Generate a visual ruby mask overlay on the image for UI display."""
    im_rgb = im.convert('RGB')
    arr = np.asarray(im_rgb, dtype=np.float32)

    overlay = np.zeros_like(arr)
    overlay[..., 0] = color[0]
    overlay[..., 1] = color[1]
    overlay[..., 2] = color[2]

    m3 = np.repeat(mask[..., np.newaxis], 3, axis=2) * alpha
    blended = arr * (1.0 - m3) + overlay * m3
    return Image.fromarray(np.clip(blended, 0, 255).astype(np.uint8), mode='RGB')


def apply_zone_adjustments(im, masks, zone_params):
    """Apply calibrated photographic adjustments to each zone.

    Per zone:
    - light: EV exposure (-2.0 to +2.0)
    - contrast: contrast boost (-0.5 to +0.5)
    - temp: warm/cool (-50 to +50)
    - tint: green/magenta (-50 to +50)
    - saturation: 0.0 to 2.0
    - detail: clarity/micro-contrast (-0.5 to +1.0)
    - blur: Gaussian defocus in pixels (0.0 to 30.0)
    """
    im_rgb = im.convert('RGB')
    w, h = im_rgb.size
    rgb = np.asarray(im_rgb, dtype=np.float32) / 255.0

    # Work in linear-like sRGB float
    for zone_name in ('background', 'sky', 'subject'):
        if zone_name not in zone_params:
            continue
        params = zone_params[zone_name]
        raw_mask = masks.get(zone_name)
        if raw_mask is None:
            continue

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
            # Blur current buffer
            blurred = np.zeros_like(rgb)
            for c in range(3):
                blurred[..., c] = ndimage.gaussian_filter(rgb[..., c], sigma=blur_radius)
            rgb = rgb * (1.0 - m3) + blurred * m3

        # 2. Detail / Clarity
        detail = float(params.get('detail', 0.0))
        if abs(detail) > 0.02:
            # Multi-scale unsharp high-pass
            base_blur = np.zeros_like(rgb)
            for c in range(3):
                base_blur[..., c] = ndimage.gaussian_filter(rgb[..., c], sigma=2.0)
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
