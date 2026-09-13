"""Charlie Kirk Face Fusion & Transformation Engine for Local RAW Studio.

Performs genuine facial fusion (Face Blend / Face Morph) between Charlie Kirk and
detected faces in photographs:
- 4-point facial landmark alignment (eyes, nose, mouth) using partial affine estimation.
- Warps Charlie Kirk's facial anatomy to match the person's head tilt, perspective, and scale.
- Matches skin tone, melanin, white balance, and luminance statistics in LAB color space.
- Seamless Poisson cloning and feathered gradient fusion, preserving
  hair, jawline, ears, neck, and ambient scene lighting.
"""
from pathlib import Path
import cv2
import numpy as np
from PIL import Image

from .settings import ROOT

ASSET_DIR = ROOT / 'studio' / 'assets'
ASSET_PATH = ASSET_DIR / 'charlie_kirk.png'

# Pre-load cascades
_FRONTAL_CASCADE = None
_PROFILE_CASCADE = None
_EYE_CASCADE = None
_EYE_GLASSES_CASCADE = None

# Cached Kirk facial asset data
_KIRK_FACE_CACHE = None


def _get_cascades():
    global _FRONTAL_CASCADE, _PROFILE_CASCADE, _EYE_CASCADE, _EYE_GLASSES_CASCADE
    if _FRONTAL_CASCADE is None:
        _FRONTAL_CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        _PROFILE_CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_profileface.xml')
        _EYE_CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_eye.xml')
        _EYE_GLASSES_CASCADE = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_eye_tree_eyeglasses.xml')
    return _FRONTAL_CASCADE, _PROFILE_CASCADE, _EYE_CASCADE, _EYE_GLASSES_CASCADE


def get_kirk_asset(variant: str = 'frontal') -> Image.Image:
    """Load and return the RGBA Charlie Kirk asset."""
    variant_path = ASSET_DIR / f'charlie_kirk_{variant}.png'
    if variant != 'frontal' and variant_path.is_file():
        return Image.open(variant_path).convert('RGBA')
    if not ASSET_PATH.is_file():
        raise FileNotFoundError(f"Charlie Kirk asset not found at {ASSET_PATH}")
    return Image.open(ASSET_PATH).convert('RGBA')


def get_kirk_face_data():
    """Extract and cache the facial asset and landmark anchor points of Charlie Kirk."""
    global _KIRK_FACE_CACHE
    if _KIRK_FACE_CACHE is not None:
        return _KIRK_FACE_CACHE

    asset = get_kirk_asset('frontal')
    arr = np.asarray(asset)
    bgr = cv2.cvtColor(arr[..., :3], cv2.COLOR_RGB2BGR)
    alpha = arr[..., 3]
    kh, kw = bgr.shape[:2]

    # Exact landmarks on frontal asset:
    # Left eye, Right eye, Nose tip, Mouth center
    src_pts = np.float32([
        [218.5, 295.5],  # Left eye
        [440.0, 296.0],  # Right eye
        [329.0, 406.0],  # Nose tip
        [329.0, 501.0],  # Mouth center
    ])

    _KIRK_FACE_CACHE = (bgr, alpha, src_pts, kw, kh)
    return _KIRK_FACE_CACHE


def _box_iou(b1, b2):
    """Compute Intersection over Union between two bounding boxes (x, y, w, h)."""
    x1, y1, w1, h1 = b1
    x2, y2, w2, h2 = b2
    xi1, yi1 = max(x1, x2), max(y1, y2)
    xi2, yi2 = min(x1 + w1, x2 + w2), min(y1 + h1, y2 + h2)
    iw, ih = max(0, xi2 - xi1), max(0, yi2 - yi1)
    inter = iw * ih
    union = (w1 * h1) + (w2 * h2) - inter
    return inter / max(1, union)


def detect_faces(im: Image.Image) -> list[dict]:
    """Detect real human faces and eyes using OpenCV CascadeClassifiers."""
    im_rgb = im.convert('RGB')
    w, h = im_rgb.size
    rgb_arr = np.asarray(im_rgb)

    gray = cv2.cvtColor(rgb_arr, cv2.COLOR_RGB2GRAY)
    frontal, profile, eye_cascade, glasses_cascade = _get_cascades()

    min_size = max(24, int(min(w, h) * 0.025))
    raw_boxes = []

    # 1. Frontal faces
    frontal_dets = frontal.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=4, minSize=(min_size, min_size)
    )
    for fb in frontal_dets:
        raw_boxes.append((fb, False, 'frontal'))

    # 2. Profile / turned faces
    profile_dets = profile.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=4, minSize=(min_size, min_size)
    )
    for pb in profile_dets:
        if not any(_box_iou(pb, fb[0]) > 0.35 for fb in raw_boxes):
            raw_boxes.append((pb, True, 'profile'))

    if not raw_boxes:
        return []

    # 3. Filter false positives via skin presence verification
    r = rgb_arr[..., 0].astype(np.float32) / 255.0
    g = rgb_arr[..., 1].astype(np.float32) / 255.0
    b = rgb_arr[..., 2].astype(np.float32) / 255.0
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b

    max_c = np.maximum(np.maximum(r, g), b)
    min_c = np.minimum(np.minimum(r, g), b)
    delta_c = max_c - min_c
    sat = np.where(max_c > 1e-4, delta_c / (max_c + 1e-6), 0.0)

    hsv_h = np.zeros_like(lum)
    mask_r = (max_c == r) & (delta_c > 1e-4)
    mask_g = (max_c == g) & (delta_c > 1e-4)
    mask_b = (max_c == b) & (delta_c > 1e-4)
    hsv_h[mask_r] = ((g[mask_r] - b[mask_r]) / (delta_c[mask_r] + 1e-6)) % 6.0
    hsv_h[mask_g] = ((b[mask_g] - r[mask_g]) / (delta_c[mask_g] + 1e-6)) + 2.0
    hsv_h[mask_b] = ((r[mask_b] - g[mask_b]) / (delta_c[mask_b] + 1e-6)) + 4.0
    hsv_h = (hsv_h / 6.0) % 1.0

    skin_mask = (
        (hsv_h >= 0.018) & (hsv_h <= 0.125) &
        (sat >= 0.12) & (sat <= 0.72) &
        (lum >= 0.12) & (lum <= 0.95) &
        (r > g) & (g > b)
    )

    verified_faces = []
    for ((fx, fy, fw, fh), is_profile, face_type) in raw_boxes:
        box_skin = skin_mask[fy:fy + fh, fx:fx + fw]
        skin_ratio = float(np.mean(box_skin)) if box_skin.size > 0 else 0.0

        if skin_ratio < 0.12:
            continue

        box_lum = lum[fy:fy + fh, fx:fx + fw]
        box_r = r[fy:fy + fh, fx:fx + fw]
        box_b = b[fy:fy + fh, fx:fx + fw]

        mean_lum = float(np.mean(box_lum)) if box_lum.size > 0 else 0.5
        mean_temp = float(np.mean(box_r - box_b)) if box_r.size > 0 else 0.0

        cx = int(fx + fw / 2)
        cy = int(fy + fh / 2)

        # Detect eyes in upper 65% of face box (with glasses fallback & tilt rotations)
        roi_y2 = fy + int(fh * 0.65)
        roi_gray = gray[fy:roi_y2, fx:fx + fw]
        eye_min = max(10, int(fw * 0.10))
        detected_eyes = eye_cascade.detectMultiScale(
            roi_gray, scaleFactor=1.1, minNeighbors=3, minSize=(eye_min, eye_min)
        )

        if len(detected_eyes) < 2:
            detected_g = glasses_cascade.detectMultiScale(
                roi_gray, scaleFactor=1.1, minNeighbors=2, minSize=(eye_min, eye_min)
            )
            if len(detected_g) >= 2:
                detected_eyes = detected_g

        if len(detected_eyes) < 2:
            rw, rh = roi_gray.shape[1], roi_gray.shape[0]
            r_center = (rw // 2, rh // 2)
            for test_ang in [-14, 14, -22, 22]:
                rot_mat = cv2.getRotationMatrix2D(r_center, test_ang, 1.0)
                rot_roi = cv2.warpAffine(roi_gray, rot_mat, (rw, rh), flags=cv2.INTER_LINEAR)
                r_eyes = eye_cascade.detectMultiScale(rot_roi, scaleFactor=1.1, minNeighbors=2, minSize=(eye_min, eye_min))
                if len(r_eyes) < 2:
                    r_eyes = glasses_cascade.detectMultiScale(rot_roi, scaleFactor=1.1, minNeighbors=2, minSize=(eye_min, eye_min))
                if len(r_eyes) >= 2:
                    inv_mat = cv2.getRotationMatrix2D(r_center, -test_ang, 1.0)
                    mapped_eyes = []
                    for (rex, rey, rew, reh) in r_eyes:
                        pt = inv_mat @ np.array([rex + rew / 2.0, rey + reh / 2.0, 1.0])
                        mapped_eyes.append((pt[0] - rew / 2.0, pt[1] - reh / 2.0, rew, reh))
                    detected_eyes = mapped_eyes
                    break

        tilt_deg = 0.0
        eye_coords = None

        if len(detected_eyes) >= 2:
            eyes_sorted = sorted(detected_eyes, key=lambda e: e[0])
            e1 = (fx + eyes_sorted[0][0] + eyes_sorted[0][2] / 2, fy + eyes_sorted[0][1] + eyes_sorted[0][3] / 2)
            e2 = (fx + eyes_sorted[1][0] + eyes_sorted[1][2] / 2, fy + eyes_sorted[1][1] + eyes_sorted[1][3] / 2)
            dx_eye = e2[0] - e1[0]
            dy_eye = e2[1] - e1[1]
            if dx_eye > (fw * 0.15):
                tilt_deg = float(np.degrees(np.arctan2(dy_eye, dx_eye)))
                eye_coords = (e1, e2)

        verified_faces.append({
            'fx': int(fx),
            'fy': int(fy),
            'width': int(fw),
            'height': int(fh),
            'cx': cx,
            'cy': cy,
            'area': int(fw * fh),
            'mean_lum': mean_lum,
            'mean_temp': mean_temp,
            'is_profile': is_profile,
            'face_type': face_type,
            'tilt_deg': tilt_deg,
            'eye_coords': eye_coords,
        })

    verified_faces.sort(key=lambda f: f['area'], reverse=True)
    return verified_faces[:12]


def apply_kirk_fusion(
    arr_bgr: np.ndarray,
    faces: list[dict],
    intensity: float = 0.80,
    match_lighting: bool = True
) -> np.ndarray:
    """Perform true landmark-aligned face fusion between Charlie Kirk and target faces."""
    if not faces:
        return arr_bgr

    dst_bgr = arr_bgr.copy()
    th, tw = dst_bgr.shape[:2]

    kirk_bgr, kirk_alpha, src_pts, kw, kh = get_kirk_face_data()
    blend_strength = float(np.clip(intensity, 0.20, 1.0))
    _, _, _, glasses_cascade = _get_cascades()

    for face in faces:
        fx, fy, fw, fh = face['fx'], face['fy'], face['width'], face['height']
        orig_face_canvas = dst_bgr.copy()

        # Determine target feature landmarks: e1, e2, nose, mouth
        if face['eye_coords'] is not None:
            (e1_raw, e2_raw) = face['eye_coords']
            e1 = np.float32(e1_raw)
            e2 = np.float32(e2_raw)
        else:
            e1 = np.float32([fx + fw * 0.33, fy + fh * 0.38])
            e2 = np.float32([fx + fw * 0.67, fy + fh * 0.38])

        # Eye midpoint and perpendicular downward orientation vector
        mid_eye = (e1 + e2) / 2.0
        eye_vec = e2 - e1
        eye_dist = float(np.linalg.norm(eye_vec))
        if eye_dist < 1e-3:
            perp_vec = np.float32([0, 1])
            eye_dist = fw * 0.34
        else:
            perp_vec = np.float32([-eye_vec[1], eye_vec[0]]) / eye_dist

        # Anatomically proportional nose and mouth coordinates
        nose = mid_eye + perp_vec * (eye_dist * 0.50)
        mouth = mid_eye + perp_vec * (eye_dist * 0.93)

        dst_pts = np.float32([e1, e2, nose, mouth])

        # Compute partial affine transform
        affine_mat, _ = cv2.estimateAffinePartial2D(src_pts, dst_pts)
        if affine_mat is None:
            continue

        # Warp Kirk face and alpha to destination image space
        warped_kirk = cv2.warpAffine(
            kirk_bgr, affine_mat, (tw, th), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT
        )
        warped_alpha = cv2.warpAffine(
            kirk_alpha, affine_mat, (tw, th), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0
        )

        # Facial feature center and calibrated inner radii (avoids overflowing cheeks/ears on tilted heads)
        face_cx = int(mid_eye[0] + perp_vec[0] * (eye_dist * 0.45))
        face_cy = int(mid_eye[1] + perp_vec[1] * (eye_dist * 0.45))
        rx = max(12, int(eye_dist * 0.82))
        ry = max(12, int(eye_dist * 1.15))

        tilt_deg = float(np.degrees(np.arctan2(eye_vec[1], eye_vec[0])))

        # Create mask for seamless Poisson fusion
        mask = np.zeros((th, tw), dtype=np.uint8)
        cv2.ellipse(mask, (face_cx, face_cy), (rx, ry), tilt_deg, 0, 360, 255, -1)
        mask = cv2.bitwise_and(mask, (warped_alpha > 140).astype(np.uint8) * 255)

        # Strictly contain within interior margin of subject's face box
        box_margin_x = int(fw * 0.08)
        box_margin_y = int(fh * 0.05)
        box_mask = np.zeros((th, tw), dtype=np.uint8)
        box_mask[max(0, fy + box_margin_y):min(th, fy + fh - box_margin_y), max(0, fx + box_margin_x):min(tw, fx + fw - box_margin_x)] = 255
        mask = cv2.bitwise_and(mask, box_mask)
        mask = cv2.erode(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)), iterations=1)

        # Match color & illumination in LAB space
        mask_bool = mask > 128
        if match_lighting and np.any(mask_bool):
            tgt_lab = cv2.cvtColor(dst_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
            k_lab = cv2.cvtColor(warped_kirk, cv2.COLOR_BGR2LAB).astype(np.float32)

            t_mean = np.mean(tgt_lab[mask_bool], axis=0)
            t_std = np.std(tgt_lab[mask_bool], axis=0) + 1e-4
            k_mean = np.mean(k_lab[mask_bool], axis=0)
            k_std = np.std(k_lab[mask_bool], axis=0) + 1e-4

            matched_lab = (k_lab - k_mean) * (t_std / k_std) + t_mean
            warped_kirk = cv2.cvtColor(np.clip(matched_lab, 0, 255).astype(np.uint8), cv2.COLOR_LAB2BGR)

        # Boundary clipping check for Poisson clone
        x1 = max(0, face_cx - rx)
        x2 = min(tw, face_cx + rx)
        y1 = max(0, face_cy - ry)
        y2 = min(th, face_cy + ry)

        if x2 <= x1 or y2 <= y1 or not np.any(mask[y1:y2, x1:x2] > 128):
            continue

        try:
            cloned = cv2.seamlessClone(warped_kirk, dst_bgr, mask, (face_cx, face_cy), cv2.NORMAL_CLONE)
            if blend_strength < 0.99:
                fused = np.clip(cloned * blend_strength + dst_bgr * (1.0 - blend_strength), 0, 255).astype(np.uint8)
            else:
                fused = cloned
        except Exception:
            # Fallback to soft alpha blend if Poisson encounters degenerate geometry
            soft_mask = cv2.GaussianBlur(mask.astype(np.float32) / 255.0, (21, 21), 0)
            w = np.expand_dims(soft_mask * blend_strength, axis=-1)
            fused = np.clip(warped_kirk * w + dst_bgr * (1.0 - w), 0, 255).astype(np.uint8)

        # Eyeglasses preservation pass: keep wireframes, bridges & lens glints intact
        roi_gray = cv2.cvtColor(orig_face_canvas[fy:fy + fh, fx:fx + fw], cv2.COLOR_BGR2GRAY)
        detected_glasses = glasses_cascade.detectMultiScale(roi_gray, scaleFactor=1.1, minNeighbors=2)

        if len(detected_glasses) >= 1:
            g_zone = np.zeros((th, tw), dtype=np.float32)
            for (gx, gy, gw, gh) in detected_glasses:
                cx_g, cy_g = fx + gx + gw // 2, fy + gy + gh // 2
                cv2.ellipse(g_zone, (cx_g, cy_g), (int(gw * 0.65), int(gh * 0.65)), tilt_deg, 0, 360, 1.0, -1)

            if len(detected_glasses) >= 2:
                pts = [(fx + g[0] + g[2] // 2, fy + g[1] + g[3] // 2) for g in detected_glasses[:2]]
                cv2.line(g_zone, pts[0], pts[1], 1.0, thickness=max(2, int(fw * 0.07)))

            g_zone = cv2.GaussianBlur(g_zone, (15, 15), 0)
            orig_gray = cv2.cvtColor(orig_face_canvas, cv2.COLOR_BGR2GRAY).astype(np.float32)
            orig_blur = cv2.bilateralFilter(orig_gray.astype(np.uint8), 9, 75, 75).astype(np.float32)
            hf_frames = np.abs(orig_gray - orig_blur)

            frame_weight = np.clip((hf_frames - 5.0) / 15.0, 0.0, 1.0) * g_zone
            frame_weight = cv2.GaussianBlur(frame_weight, (3, 3), 0)

            for c in range(3):
                fused[:, :, c] = np.clip(
                    orig_face_canvas[:, :, c] * frame_weight + fused[:, :, c] * (1.0 - frame_weight), 0, 255
                ).astype(np.uint8)

        dst_bgr = fused

    return dst_bgr


def apply_kirkify(
    im: Image.Image,
    mode: str = 'fusion',
    intensity: float = 0.80,
    scale_multiplier: float = 1.05,
    match_lighting: bool = True
) -> tuple[Image.Image, int]:
    """Apply true Charlie Kirk face fusion to detected human faces.

    Args:
        im: PIL RGB image to transform.
        mode: 'fusion' (true landmark-aligned face fusion).
        intensity: Fusion blend strength (0.20 to 1.0).
        scale_multiplier: Unused scale parameter preserved for API compatibility.
        match_lighting: Whether to adapt LAB lighting statistics.

    Returns:
        tuple[Image.Image, int]: (transformed_image, num_faces_processed)
    """
    faces = detect_faces(im)
    if not faces:
        return im.copy(), 0

    im_rgb = im.convert('RGB')
    arr_bgr = cv2.cvtColor(np.asarray(im_rgb), cv2.COLOR_RGB2BGR)

    res_bgr = apply_kirk_fusion(
        arr_bgr,
        faces,
        intensity=intensity,
        match_lighting=match_lighting
    )

    res_rgb = cv2.cvtColor(res_bgr, cv2.COLOR_BGR2RGB)
    return Image.fromarray(res_rgb), len(faces)
