"""Baseline median and MAD tests."""

from app.detector import dispersion, mad, median


def test_median_odd():
    assert median([1.0, 3.0, 2.0]) == 2.0


def test_mad_and_dispersion_floor():
    values = [10.0, 10.0, 10.0, 10.0]
    mid, sigma = dispersion(values, epsilon=0.5)
    assert mid == 10.0
    assert sigma == 0.5
    assert mad(values, mid) == 0.0
