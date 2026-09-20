"""
Coeur de la détection d'anomalies : méthodes statistiques simples,
sans machine learning, comme demandé dans le ticket (z-score, moving
average, patterns de dégradation progressive).
"""

import statistics
from config import Z_SCORE_THRESHOLD, MOVING_AVERAGE_WINDOW, DEGRADATION_MIN_SLOPE_PERCENT_PER_MIN


def compute_zscore(values: list[float], current_value: float) -> float | None:
    """
    Calcule le z-score de la valeur actuelle par rapport à l'historique.
    Le z-score dit : "à combien d'écarts-types de la moyenne habituelle
    se trouve cette valeur ?". Plus il est grand, plus c'est anormal.
    """
    if len(values) < 2:
        return None  # pas assez d'historique pour calculer une moyenne fiable

    mean = statistics.mean(values)
    stdev = statistics.stdev(values)

    if stdev == 0:
        return 0.0  # valeur parfaitement stable, aucun écart possible

    return (current_value - mean) / stdev


def is_statistical_anomaly(values: list[float], current_value: float) -> tuple[bool, float | None]:
    """Retourne (True/False, z-score) selon si la valeur actuelle est anormale."""
    z = compute_zscore(values, current_value)
    if z is None:
        return False, None
    return abs(z) > Z_SCORE_THRESHOLD, z


def moving_average(values: list[float], window: int = MOVING_AVERAGE_WINDOW) -> list[float]:
    """
    Lisse une série de valeurs en calculant la moyenne glissante.
    Utile pour ignorer le bruit court terme et voir la vraie tendance.
    """
    if len(values) < window:
        return values

    smoothed = []
    for i in range(len(values) - window + 1):
        chunk = values[i : i + window]
        smoothed.append(sum(chunk) / len(chunk))
    return smoothed


def detect_progressive_degradation(values: list[float], step_seconds: int = 30) -> tuple[bool, float]:
    """
    Détecte une dégradation progressive : une métrique qui monte
    régulièrement dans le temps (ex: fuite mémoire), même si elle n'a
    pas encore atteint un seuil critique ni déclenché de z-score.

    Méthode : régression linéaire simple (pente de la droite qui
    approxime le mieux les points). Si la pente dépasse un seuil
    (% par minute), on considère que c'est une dégradation progressive.
    """
    n = len(values)
    if n < 5:
        return False, 0.0

    # Régression linéaire simple (méthode des moindres carrés), sans numpy
    # pour garder le script léger et sans dépendance supplémentaire.
    x = list(range(n))
    x_mean = sum(x) / n
    y_mean = sum(values) / n

    numerator = sum((x[i] - x_mean) * (values[i] - y_mean) for i in range(n))
    denominator = sum((x[i] - x_mean) ** 2 for i in range(n))

    if denominator == 0:
        return False, 0.0

    slope_per_point = numerator / denominator  # variation par point (step_seconds)
    slope_per_minute = slope_per_point * (60 / step_seconds)

    is_degrading = slope_per_minute > DEGRADATION_MIN_SLOPE_PERCENT_PER_MIN
    return is_degrading, slope_per_minute
