import base64
import json
import math
import os
import shutil
import subprocess
import time
from urllib.parse import urlparse
import httpx
from .recipes import LIMITS, validate_proposal
from .settings import MODEL_URL, MODEL_FILE, MMPROJ_FILE

_server_process = None


def local_url():
    parsed = urlparse(MODEL_URL)
    if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('El modelo debe ejecutarse en localhost')
    return MODEL_URL.rstrip('/')


def status():
    downloaded = MODEL_FILE.is_file() and MMPROJ_FILE.is_file()
    try:
        with httpx.Client(timeout=2, trust_env=False) as client:
            response = client.get(local_url() + '/models')
            response.raise_for_status()
            models = response.json().get('data', [])
            return {
                'available': bool(models),
                'name': models[0]['id'] if models else None,
                'model_downloaded': downloaded,
            }
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return {
            'available': False,
            'name': None,
            'model_downloaded': downloaded,
            'detail': str(exc)
        }


def ensure_server():
    """Ensure the local llama-server is running. Start it if model weights exist."""
    global _server_process
    st = status()
    if st['available']:
        return True

    if not (MODEL_FILE.is_file() and MMPROJ_FILE.is_file()):
        return False

    llama_bin = shutil.which('llama-server') or shutil.which('llama-server.exe')
    if not llama_bin:
        return False

    if _server_process is None or _server_process.poll() is not None:
        cmd = [
            llama_bin,
            '-m', str(MODEL_FILE),
            '--mmproj', str(MMPROJ_FILE),
            '--host', '127.0.0.1',
            '--port', '8081',
            '-ngl', '99',
            '-c', '4096',
            '--parallel', '1',
        ]
        _server_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        )

    # Wait up to 15 seconds for startup
    deadline = time.time() + 15
    while time.time() < deadline:
        if status()['available']:
            return True
        time.sleep(0.5)

    return status()['available']


def sanitize_proposal(value):
    """Sanitize raw LLM output to strictly match LIMITS.
    Clamps numeric parameters into valid conservative ranges and drops disallowed modules
    or fields, preventing spurious failures due to minor rounding or extra fields.
    """
    if not isinstance(value, dict):
        return value
    adjustments = value.get('adjustments')
    if not isinstance(adjustments, list):
        return value
    sanitized_adjs = []
    seen = set()
    for adj in adjustments:
        if not isinstance(adj, dict):
            continue
        op = adj.get('operation')
        if op not in LIMITS or op in seen:
            continue
        seen.add(op)
        raw_params = adj.get('params', {})
        if not isinstance(raw_params, dict):
            continue
        valid_params = {}
        for k, v in raw_params.items():
            if k in LIMITS[op] and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                min_v, max_v = LIMITS[op][k]
                valid_params[k] = round(max(min_v, min(max_v, float(v))), 4)
        if valid_params:
            sanitized_adjs.append({'operation': op, 'params': valid_params})
    value['adjustments'] = sanitized_adjs
    return value


def propose(preview, stack, measurements, intent, is_prompt_style=False):
    ensure_server()
    if is_prompt_style:
        system = (
            'You are a world-class creative photographic colorist. Your mission is to DESIGN a complete, original '
            'color grading and tonal aesthetic from scratch for this image, strictly expressing the user prompt: "' + intent + '". '
            'Analyze the image composition, light, and telemetry. Formulate a rich photographic palette: set shadows and highlights '
            'color wheels (shadows_H, shadows_C, highlights_H, highlights_C, midtones_H, midtones_C), global vibrance, contrast, '
            'sigmoid middle grey contrast, exposure offset and local detail. '
            'Return ONLY a JSON object with reason (professional Spanish explanation of your artistic color grading choices) '
            'and adjustments (array of {operation,params}). '
            'Propose ABSOLUTE numeric parameter values. Never output images, code, paths, commands or masks. '
            'Preserve natural healthy skin if present, protect highlights from harsh clipping. At most 6 module adjustments. '
            'Use only these allowed modules, fields and inclusive bounds: ' + json.dumps(LIMITS)
        )
    else:
        system = (
            'You are a master photographic colorist controlling darktable. Analyze the provided image '
            'and telemetry (white balance, skin tones, dynamic range, noise). Return ONLY a JSON object '
            'with reason (concise professional Spanish explanation of your artistic and technical decisions) '
            'and adjustments (array of {operation,params}). '
            'Propose ABSOLUTE numeric parameter values, not deltas. Never output images, code, '
            'paths, commands, masks, tool calls or binary parameters. Keep the requested style, '
            'preserve plausible healthy skin, protect highlights from clipping, and maintain contrast. '
            'Do not brighten night scenes into daylight. At most 6 module adjustments. You may leave adjustments empty. '
            'Use only these allowed modules, fields and inclusive bounds: ' + json.dumps(LIMITS)
        )
    content = [
        {'type': 'text', 'text': json.dumps({'intent': intent, 'current_stack': stack, 'measurements': measurements}, ensure_ascii=False)},
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(preview.read_bytes()).decode()}},
    ]
    with httpx.Client(timeout=180, trust_env=False) as client:
        models = client.get(local_url() + '/models')
        models.raise_for_status()
        name = models.json()['data'][0]['id']
        response = client.post(local_url() + '/chat/completions', json={
            'model': name, 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': content}],
            'temperature': .25 if is_prompt_style else .1,
            'max_tokens': 900,
            'response_format': {'type': 'json_object'},
        })
        response.raise_for_status()
    value = json.loads(response.json()['choices'][0]['message']['content'])
    sanitized = sanitize_proposal(value)
    return validate_proposal(sanitized), name

