import pytest
from studio import model
from studio.recipes import validate_proposal, LIMITS


def test_model_status():
    st = model.status()
    assert 'available' in st
    assert 'model_downloaded' in st
    assert st['model_downloaded'] is True


def test_validate_proposal_limits():
    valid = {
        'reason': 'Ajuste de contraste y balance para escena cálida.',
        'adjustments': [
            {'operation': 'exposure', 'params': {'exposure': 0.25}},
            {'operation': 'colorbalancergb', 'params': {'vibrance': 0.05, 'contrast': 0.02}},
        ]
    }
    prop = validate_proposal(valid)
    assert len(prop.adjustments) == 2

    # Out of bounds should raise ValueError
    invalid = {
        'reason': 'Exposición excesiva',
        'adjustments': [
            {'operation': 'exposure', 'params': {'exposure': 100.0}},
        ]
    }
    with pytest.raises(ValueError):
        validate_proposal(invalid)

    # Disallowed module should raise ValueError
    forbidden = {
        'reason': 'Módulo no permitido',
        'adjustments': [
            {'operation': 'watermark', 'params': {'opacity': 1.0}},
        ]
    }
    with pytest.raises(ValueError):
        validate_proposal(forbidden)
