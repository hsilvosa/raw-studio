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


def test_validate_proposal_bilat():
    # Valid bilat adjustment including sigma_r, sigma_s, midtone, and detail
    valid_bilat = {
        'reason': 'Ajuste de contraste local con filtro bilateral',
        'adjustments': [
            {'operation': 'bilat', 'params': {'detail': 0.22, 'sigma_r': 0.5, 'sigma_s': 0.5, 'midtone': 0.5}},
        ]
    }
    prop = validate_proposal(valid_bilat)
    assert len(prop.adjustments) == 1
    assert prop.adjustments[0].params['sigma_r'] == 0.5


def test_sanitize_proposal():
    raw_llm_output = {
        'reason': 'Ajuste adaptativo',
        'adjustments': [
            # sigma_r slightly out of bounds (1.05) -> clamped to 1.0
            # unlisted parameter 'extra_foo' -> pruned
            {'operation': 'bilat', 'params': {'detail': 0.22, 'sigma_r': 1.05, 'extra_foo': 42.0}},
            # Disallowed module -> pruned
            {'operation': 'inpaint', 'params': {'amount': 0.5}},
        ]
    }
    sanitized = model.sanitize_proposal(raw_llm_output)
    prop = validate_proposal(sanitized)
    assert len(prop.adjustments) == 1
    assert prop.adjustments[0].operation == 'bilat'
    assert prop.adjustments[0].params['sigma_r'] == 1.0
    assert 'extra_foo' not in prop.adjustments[0].params

