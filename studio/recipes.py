"""A model can change numeric darktable parameters, never pixels or paths."""
import copy
import json
import math
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .settings import ROOT

# Conservative artistic limits, narrower than darktable's physical ranges.
LIMITS = {
    'exposure': {'exposure': (-2., 4.)},
    'sigmoid': {'middle_grey_contrast': (1., 2.), 'display_black_target': (.015, .6)},
    'colorbalancergb': {
        'shadows_Y': (-.1, .15), 'highlights_Y': (-.15, .1),
        'shadows_C': (0., .025), 'shadows_H': (0., 360.),
        'midtones_C': (0., .015), 'midtones_H': (0., 360.),
        'highlights_C': (0., .015), 'highlights_H': (0., 360.),
        'global_C': (0., .008), 'global_H': (0., 360.),
        'chroma_global': (-.5, .3), 'saturation_global': (-1., .3),
        'contrast': (-.08, .12), 'vibrance': (-.3, .3)},
    'bilat': {'detail': (0., .4)},
    'sharpen': {'amount': (0., 1.), 'radius': (.4, 1.2), 'threshold': (.5, 2.)},
}


class Adjustment(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    operation: str
    params: dict[str, float]

    @field_validator('params')
    @classmethod
    def finite(cls, params):
        if not params or any(not math.isfinite(v) for v in params.values()):
            raise ValueError('Se requieren parámetros numéricos finitos')
        return params


class Proposal(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    reason: str = Field(min_length=1, max_length=1200)
    adjustments: list[Adjustment] = Field(max_length=6)


def validate_proposal(value):
    proposal = Proposal.model_validate(value)
    seen = set()
    for adjustment in proposal.adjustments:
        if adjustment.operation in seen or adjustment.operation not in LIMITS:
            raise ValueError('Módulo duplicado o no permitido')
        seen.add(adjustment.operation)
        for key, value in adjustment.params.items():
            bounds = LIMITS[adjustment.operation].get(key)
            if not bounds or not bounds[0] <= value <= bounds[1]:
                raise ValueError(f'Ajuste fuera de límites: {adjustment.operation}.{key}')
    return proposal


def merge(stack, proposal):
    result = copy.deepcopy(stack)
    for adjustment in proposal.adjustments:
        module = next((m for m in result if m['operation'] == adjustment.operation), None)
        if module is None:
            module = {'operation': adjustment.operation, 'params': {}}
            result.append(module)
        if 'blob_hex' in module:
            raise ValueError('El modelo no puede modificar parámetros binarios')
        module.setdefault('params', {}).update(adjustment.params)
    return result


def profiles():
    return json.loads((ROOT / 'profiles' / 'catalog.json').read_text(encoding='utf-8'))


def make_stack(profile, exposure, intensity):
    stack = copy.deepcopy(profile['stack'])
    stack.insert(0, {'operation': 'exposure', 'params': {'exposure': exposure, 'black': -.0001, 'compensate_exposure_bias': True}})
    # Interpolate around darktable defaults, preserving angular hue targets.
    for m in stack:
        if m['operation'] == 'colorbalancergb':
            for key in m.get('params', {}):
                if not key.endswith('_H'):
                    m['params'][key] *= intensity
        elif m['operation'] == 'colorequal':
            for key, value in m.get('params', {}).items():
                neutral = 1. if key.startswith(('sat_', 'bright_')) else 0.
                m['params'][key] = neutral + (value - neutral) * intensity
        elif m['operation'] == 'sigmoid':
            for key, neutral in [('middle_grey_contrast', 1.5), ('display_black_target', .015)]:
                if key in m['params']:
                    m['params'][key] = neutral + (m['params'][key] - neutral) * intensity
        elif m['operation'] == 'bilat':
            m['params']['detail'] *= intensity
    return stack
