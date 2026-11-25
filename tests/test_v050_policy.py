import pytest

from app.v050_policy import DEFAULT_EXPECTED_STATUS_CODES, validate_check_policy


def test_legacy_config_keeps_get_and_2xx_3xx_defaults():
    policy = validate_check_policy({"name": "Demo"})
    assert policy["method"] == "GET"
    assert policy["expected_status_codes"] == list(DEFAULT_EXPECTED_STATUS_CODES)
    assert policy["slow_threshold_ms"] is None


def test_policy_normalizes_method_and_codes():
    policy = validate_check_policy({
        "method": "head",
        "expected_status_codes": [204, 200],
        "slow_threshold_ms": 250,
    })
    assert policy == {
        "method": "HEAD",
        "expected_status_codes": [200, 204],
        "slow_threshold_ms": 250.0,
    }


@pytest.mark.parametrize("payload", [
    {"method": "POST"},
    {"method": 3},
    {"expected_status_codes": []},
    {"expected_status_codes": "200"},
    {"expected_status_codes": [200, True]},
    {"expected_status_codes": [99]},
    {"expected_status_codes": [600]},
    {"expected_status_codes": [200, 200]},
    {"slow_threshold_ms": 0},
    {"slow_threshold_ms": True},
    {"slow_threshold_ms": 60001},
])
def test_invalid_policy_is_rejected(payload):
    with pytest.raises(ValueError):
        validate_check_policy(payload)
