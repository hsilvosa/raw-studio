import base64
import json
from urllib.parse import urlparse
import httpx
from .recipes import LIMITS, validate_proposal
from .settings import MODEL_URL


def local_url():
    parsed = urlparse(MODEL_URL)
    if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise ValueError('El modelo debe ejecutarse en localhost')
    return MODEL_URL.rstrip('/')


def status():
    try:
        with httpx.Client(timeout=2, trust_env=False) as client:
            response = client.get(local_url() + '/models')
            response.raise_for_status()
            models = response.json().get('data', [])
            return {'available': bool(models), 'name': models[0]['id'] if models else None}
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return {'available': False, 'name': None, 'detail': str(exc)}


def propose(preview, stack, measurements, intent):
    system = (
        'You are a photographic colorist controlling darktable. Return ONLY a JSON object '
        'with reason (brief Spanish explanation) and adjustments (array of {operation,params}). '
        'Propose ABSOLUTE numeric parameter values, not deltas. Never output images, code, '
        'paths, commands, masks, tool calls or binary parameters. Keep the requested style, '
        'preserve plausible skin and neutral surfaces. Do not brighten every night scene '
        'into daylight. At most 6 module adjustments. You may leave adjustments empty. '
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
            'temperature': .1, 'max_tokens': 900,
            'response_format': {'type': 'json_object'},
        })
        response.raise_for_status()
    value = json.loads(response.json()['choices'][0]['message']['content'])
    return validate_proposal(value), name
