"""Multi-scene model decision and adaptation evaluation suite.

Compares:
1. Fixed Profile baseline
2. Algorithmic Adaptation (adaptation.py)
3. Model-Directed Adaptation (Qwen3-VL-4B-Instruct)
4. Zonal-Enhanced Result (zones.py)

Measures:
- Highlight preservation (clipping prevention)
- Shadow recovery & black blockout
- Skin tone harmony and naturalness
- Dynamic range utilization
- Model qualitative reasoning accuracy
"""
import copy
import json
import math
import os
from pathlib import Path
import numpy as np
from PIL import Image

from .adaptation import analyze_scene, adapt_stack
from .recipes import profiles, make_stack, merge, validate_proposal
from .settings import STATE, ROOT
from .zones import generate_zone_masks, apply_zone_adjustments, DEFAULT_ZONE_PARAMS


def evaluate_image_quality(im):
    """Compute objective photographic quality metrics on a rendered image."""
    rgb = np.asarray(im.convert('RGB'), dtype=np.float32) / 255.0
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    flat = lum.ravel()

    white_clip = float(np.mean(flat > 0.98))
    black_clip = float(np.mean(flat < 0.02))
    p5 = float(np.percentile(flat, 5))
    p95 = float(np.percentile(flat, 95))
    dr_ev = float(math.log2(max(1e-4, p95) / max(1e-4, p5)))

    # Skin tone naturalness metric
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

    skin = (hsv_h >= 0.025) & (hsv_h <= 0.11) & (sat >= 0.15) & (sat <= 0.68) & (lum >= 0.18) & (r > g) & (g > b)
    skin_fraction = float(np.mean(skin))

    skin_harmony_score = 1.0
    if skin_fraction > 0.015:
        # Expected golden skin line: G is roughly between 0.60*R and 0.85*R
        skin_r = r[skin]
        skin_g = g[skin]
        skin_b = b[skin]
        expected_g = 0.70 * skin_r + 0.15 * skin_b
        dev = np.mean(np.abs(skin_g - expected_g) / (skin_r + 1e-4))
        skin_harmony_score = float(max(0.0, 1.0 - dev * 3.5))

    # Overall technical quality score (0.0 to 100.0)
    clip_penalty = (white_clip * 150.0) + (black_clip * 40.0)
    dr_bonus = min(25.0, dr_ev * 4.5)
    skin_component = skin_harmony_score * 25.0 if skin_fraction > 0.015 else 20.0
    quality_score = max(0.0, min(100.0, 60.0 + dr_bonus + skin_component - clip_penalty))

    return {
        'white_clip': white_clip,
        'black_clip': black_clip,
        'dr_ev': dr_ev,
        'skin_fraction': skin_fraction,
        'skin_harmony': skin_harmony_score,
        'quality_score': round(quality_score, 1),
    }


def run_evaluation(images_sample=None, profile_id='09_PORTRA_WARM'):
    """Run full comparative evaluation across test scenes and generate visual report."""
    from . import library, model
    from .darktable import Darktable

    reports_dir = STATE / 'reports'
    reports_dir.mkdir(parents=True, exist_ok=True)

    dt = Darktable()
    all_imgs = library.all_images()
    if images_sample:
        target_imgs = [img for img in all_imgs if img['id'] in images_sample or img['name'] in images_sample]
    else:
        # Pick diverse sample of up to 4 scenes
        target_imgs = all_imgs[:4]

    catalog = profiles()
    profile = next((p for p in catalog if p['id'] == profile_id), catalog[0])

    evaluations = []

    for img in target_imgs:
        source = Path(img['copy'])
        preview_path = STATE / 'previews' / (img['id'] + '.png')
        if not preview_path.exists():
            dt.export(source, [], preview_path, 1200)

        img_eval_dir = reports_dir / img['id']
        img_eval_dir.mkdir(parents=True, exist_ok=True)

        telemetry = analyze_scene(preview_path)

        # 1. Baseline: Fixed Profile (no adapt, no model)
        base_stack = make_stack(profile, 0.0, 0.8)
        base_stack.append({'operation': 'sharpen', 'params': {'amount': .45, 'radius': .7, 'threshold': 1.}})
        fixed_path = img_eval_dir / '1_fixed_profile.png'
        dt.export(source, base_stack, fixed_path, 900)
        with Image.open(fixed_path) as im:
            m_fixed = evaluate_image_quality(im)

        # 2. Algorithmic Adaptation
        adapt_stack_recipe, adapt_reason, _ = adapt_stack(make_stack(profile, 0.0, 0.8), telemetry, 0.0, 0.8)
        adapt_path = img_eval_dir / '2_algorithmic_adapted.png'
        dt.export(source, adapt_stack_recipe, adapt_path, 900)
        with Image.open(adapt_path) as im:
            m_adapt = evaluate_image_quality(im)

        # 3. Model-Directed Adaptation (Qwen3-VL)
        model_name = None
        model_reason = "Modelo no disponible"
        model_proposal = None
        model_path = img_eval_dir / '3_model_directed.png'
        m_model = m_adapt

        if model.status().get('available'):
            try:
                proposal, model_name = model.propose(
                    fixed_path,
                    adapt_stack_recipe,
                    telemetry,
                    f"{profile['title']}. Optimizar preservando piel natural, luces sin quemar y contraste estético."
                )
                model_stack = merge(adapt_stack_recipe, proposal)
                dt.export(source, model_stack, model_path, 900)
                model_reason = proposal.reason
                model_proposal = proposal.model_dump()
                with Image.open(model_path) as im:
                    m_model = evaluate_image_quality(im)
            except Exception as e:
                model_reason = f"Error en modelo: {e}"

        # 4. Model + Zonal Refinement
        zonal_path = img_eval_dir / '4_zonal_refined.png'
        source_for_zones = model_path if model_path.exists() else adapt_path
        with Image.open(source_for_zones) as im:
            masks = generate_zone_masks(im)
            zonal_im = apply_zone_adjustments(im, masks, DEFAULT_ZONE_PARAMS)
            zonal_im.save(zonal_path, 'PNG')
            m_zonal = evaluate_image_quality(zonal_im)

        evaluations.append({
            'image_id': img['id'],
            'image_name': img['name'],
            'telemetry': telemetry,
            'fixed': {'metrics': m_fixed, 'path': str(fixed_path)},
            'adapted': {'metrics': m_adapt, 'path': str(adapt_path), 'reason': adapt_reason},
            'model': {'metrics': m_model, 'path': str(model_path) if model_path.exists() else str(adapt_path), 'name': model_name, 'reason': model_reason, 'proposal': model_proposal},
            'zonal': {'metrics': m_zonal, 'path': str(zonal_path)},
        })

    dt.close()

    # Generate visual HTML report
    html_report = generate_html_report(evaluations, profile['title'])
    report_file = reports_dir / 'evaluation_report.html'
    report_file.write_text(html_report, encoding='utf-8')

    json_file = reports_dir / 'evaluation_summary.json'
    json_file.write_text(json.dumps(evaluations, indent=2, ensure_ascii=False, default=str), encoding='utf-8')

    return evaluations, report_file


def generate_html_report(evaluations, profile_title):
    """Render a visual HTML report comparing all stages."""
    rows = []
    for ev in evaluations:
        t = ev['telemetry']
        f_m = ev['fixed']['metrics']
        a_m = ev['adapted']['metrics']
        m_m = ev['model']['metrics']
        z_m = ev['zonal']['metrics']

        rows.append(f"""
        <div class="card">
            <h2>Foto: {ev['image_name']} <span class="badge">{ev['image_id']}</span></h2>
            <div class="telemetry">
                <span>Luminancia media: {t['median']:.3f}</span>
                <span>Piel detectada: {'Sí (' + str(round(t['skin_fraction']*100, 1)) + '%)' if t['skin_detected'] else 'No'}</span>
                <span>Sesgo temperatura: {t['temp_bias']:.3f}</span>
                <span>Ruido estimado: {t['noise_sigma']:.4f} ({'Ruidosa' if t['is_noisy'] else 'Limpia'})</span>
            </div>
            
            <div class="comparison-grid">
                <div class="comp-col">
                    <h3>1. Perfil Fijo (Base)</h3>
                    <img src="/api/reports/img?p={Path(ev['fixed']['path']).as_posix()}">
                    <div class="metric">Calidad: <strong>{f_m['quality_score']}</strong> / 100</div>
                    <div class="metric-sub">Blancos quemados: {f_m['white_clip']*100:.2f}% | Rango EV: {f_m['dr_ev']:.2f}</div>
                </div>
                <div class="comp-col">
                    <h3>2. Adaptación Fotográfica</h3>
                    <img src="/api/reports/img?p={Path(ev['adapted']['path']).as_posix()}">
                    <div class="metric">Calidad: <strong>{a_m['quality_score']}</strong> / 100</div>
                    <div class="metric-sub">Blancos quemados: {a_m['white_clip']*100:.2f}% | Rango EV: {a_m['dr_ev']:.2f}</div>
                    <p class="reason"><strong>Ajustes:</strong> {ev['adapted']['reason']}</p>
                </div>
                <div class="comp-col">
                    <h3>3. Modelo Qwen3-VL</h3>
                    <img src="/api/reports/img?p={Path(ev['model']['path']).as_posix()}">
                    <div class="metric">Calidad: <strong>{m_m['quality_score']}</strong> / 100</div>
                    <div class="metric-sub">Blancos quemados: {m_m['white_clip']*100:.2f}% | Rango EV: {m_m['dr_ev']:.2f}</div>
                    <p class="reason"><strong>Criterio visual:</strong> {ev['model']['reason']}</p>
                </div>
                <div class="comp-col">
                    <h3>4. Edición Zonal (Máscaras)</h3>
                    <img src="/api/reports/img?p={Path(ev['zonal']['path']).as_posix()}">
                    <div class="metric">Calidad: <strong>{z_m['quality_score']}</strong> / 100</div>
                    <div class="metric-sub">Sujeto realzado + Fondo bokeh suave + Cielo contrastado</div>
                </div>
            </div>
        </div>
        """)

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <title>Reporte de Evaluación - Revelado Local y Modelo Qwen3-VL</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #121416; color: #e4e6eb; margin: 0; padding: 24px; }}
        h1 {{ color: #d3b789; font-weight: 500; margin-bottom: 4px; }}
        .header-sub {{ color: #999; margin-bottom: 24px; font-size: 14px; }}
        .card {{ background: #1b1e21; border: 1px solid #33383d; border-radius: 8px; padding: 20px; margin-bottom: 24px; box-shadow: 0 4px 16px rgba(0,0,0,0.4); }}
        h2 {{ font-size: 18px; margin: 0 0 12px; color: #f0f0f0; }}
        .badge {{ font-size: 11px; background: #2a2e33; color: #d3b789; padding: 2px 8px; border-radius: 4px; }}
        .telemetry {{ display: flex; gap: 16px; font-size: 12px; color: #aaa; margin-bottom: 16px; background: #151719; padding: 8px 12px; border-radius: 4px; }}
        .comparison-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; }}
        .comp-col {{ background: #141719; border: 1px solid #2d3238; border-radius: 6px; padding: 10px; }}
        .comp-col h3 {{ font-size: 13px; font-weight: 600; margin: 0 0 8px; color: #ccc; }}
        .comp-col img {{ width: 100%; aspect-ratio: 3/2; object-fit: cover; border-radius: 4px; display: block; }}
        .metric {{ margin-top: 8px; font-size: 13px; color: #eee; }}
        .metric strong {{ color: #d3b789; font-size: 15px; }}
        .metric-sub {{ font-size: 11px; color: #888; margin-top: 2px; }}
        .reason {{ font-size: 11px; color: #bbb; line-height: 1.4; margin-top: 8px; background: #1a1d20; padding: 6px; border-radius: 4px; border-left: 2px solid #d3b789; }}
    </style>
</head>
<body>
    <h1>Reporte de Evaluación - Revelado Local y Modelo Qwen3-VL</h1>
    <div class="header-sub">Perfil evaluado: <strong>{profile_title}</strong> | Comparativa en escenas reales de la biblioteca</div>
    {''.join(rows)}
</body>
</html>
"""
