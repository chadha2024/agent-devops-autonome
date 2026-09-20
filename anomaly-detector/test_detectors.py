"""
Tests unitaires pour detectors.py — détection statistique (z-score) et
dégradation progressive (régression linéaire).
"""

import pytest
from detectors import (
    compute_zscore,
    is_statistical_anomaly,
    moving_average,
    detect_progressive_degradation,
)


class TestComputeZscore:

    def test_not_enough_history_returns_none(self):
        assert compute_zscore([20], 25) is None

    def test_stable_history_returns_zero_for_constant_value(self):
        history = [20, 20, 20, 20]
        assert compute_zscore(history, 20) == 0.0

    def test_clear_outlier_gives_high_zscore(self):
        history = [20, 22, 21, 19, 20, 21, 20, 22]
        z = compute_zscore(history, 95)
        assert z > 3.0


class TestIsStatisticalAnomaly:

    def test_high_outlier_is_flagged_as_anomaly(self):
        history = [20, 22, 21, 19, 20, 21, 20, 22]
        anomaly, z = is_statistical_anomaly(history, 95)
        assert anomaly is True
        assert z is not None

    def test_normal_value_is_not_flagged(self):
        history = [20, 22, 21, 19, 20, 21, 20, 22]
        anomaly, z = is_statistical_anomaly(history, 21)
        assert anomaly is False

    def test_insufficient_history_returns_false_and_none(self):
        anomaly, z = is_statistical_anomaly([20], 90)
        assert anomaly is False
        assert z is None


class TestMovingAverage:

    def test_window_larger_than_data_returns_data_unchanged(self):
        values = [1, 2, 3]
        assert moving_average(values, window=5) == values

    def test_smooths_values_correctly(self):
        values = [10, 20, 30, 40]
        result = moving_average(values, window=2)
        assert result == [15.0, 25.0, 35.0]


class TestDetectProgressiveDegradation:

    def test_too_few_points_returns_false(self):
        is_degrading, slope = detect_progressive_degradation([1, 2, 3])
        assert is_degrading is False
        assert slope == 0.0

    def test_flat_values_are_not_degrading(self):
        values = [50.0] * 10
        is_degrading, slope = detect_progressive_degradation(values)
        assert is_degrading is False

    def test_clear_upward_trend_is_flagged_as_degrading(self):
        # Simule une fuite mémoire : hausse régulière et marquée
        values = [40 + i * 2 for i in range(10)]
        is_degrading, slope = detect_progressive_degradation(values)
        assert is_degrading is True
        assert slope > 0