"""Robust z-score and EWMA behavior."""

from app.detector import evaluate


def test_robust_z_detects_lower_is_worse_drop():
    baseline = [20.0 + (i % 3) * 0.1 for i in range(30)]
    result = evaluate(
        baseline,
        observed=10.0,
        direction="lower",
        epsilon=0.05,
        threshold=3.0,
        ewma_prev=20.0,
        alpha=0.3,
    )
    assert result.robust_z > 0
    assert result.is_candidate
    assert result.combined_score >= 3.0


def test_higher_is_worse_needs_increase():
    baseline = [15.0 + (i % 3) * 0.1 for i in range(30)]
    drop = evaluate(
        baseline,
        observed=5.0,
        direction="higher",
        epsilon=0.2,
        threshold=3.0,
        ewma_prev=15.0,
        alpha=0.3,
    )
    assert not drop.is_candidate

    rise = evaluate(
        baseline,
        observed=40.0,
        direction="higher",
        epsilon=0.2,
        threshold=3.0,
        ewma_prev=15.0,
        alpha=0.3,
    )
    assert rise.is_candidate
    assert rise.ewma_value > 15.0


def test_ewma_moves_toward_observation():
    baseline = [10.0] * 25
    result = evaluate(
        baseline,
        observed=20.0,
        direction="higher",
        epsilon=0.1,
        threshold=3.0,
        ewma_prev=10.0,
        alpha=0.5,
    )
    assert result.ewma_value == 15.0
