import pytest
from agent_system.domain.orchestration import AdviceResult


def test_advice_result_normalization_validator():
    # Empty string normalized to empty tuple
    res1 = AdviceResult(text="Hello", limitations="")
    assert res1.limitations == ()

    # None normalized to empty tuple
    res2 = AdviceResult(text="Hello", limitations=None)
    assert res2.limitations == ()

    # List of strings normalized to tuple
    res3 = AdviceResult(text="Hello", limitations=["provider_freshness", "sandbox"])
    assert res3.limitations == ("provider_freshness", "sandbox")

    # Single string normalized to 1-item tuple
    res4 = AdviceResult(text="Hello", limitations="provider_freshness")
    assert res4.limitations == ("provider_freshness",)
