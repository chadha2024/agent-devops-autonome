"""
Structure du diagnostic produit par l'analyse LLM.

Le ticket demande un "diagnostic structuré" — pas juste du texte libre,
mais des champs exploitables par le futur orchestrateur pour décider
automatiquement de la remédiation à appliquer.
"""

from dataclasses import dataclass, field


@dataclass
class LogDiagnostic:
    has_error: bool                      # y a-t-il un vrai problème dans ces logs ?
    root_cause: str                      # cause probable, en une phrase
    affected_service: str                # quel service/pod est concerné
    error_category: str                  # US 2.2 : catégorie connue (taxonomie fermée, ex: config.ERROR_CATEGORIES)
    error_pattern: str                   # le motif d'erreur identifié en texte libre (ex: "OOMKilled", "connexion refusée")
    first_occurrence: str                # horodatage approximatif du premier signe du problème
    confidence_percent: int              # niveau de confiance du LLM (0-100)
    needs_human_review: bool             # US 2.2 : True si confidence_percent < seuil (voir config.CONFIDENCE_THRESHOLD_PERCENT)
    recommended_action: str              # suggestion d'action (informatif, pas exécuté automatiquement)
    raw_summary: str                     # résumé libre, pour lecture humaine
    related_log_lines: list[str] = field(default_factory=list)  # extraits pertinents

    def to_dict(self) -> dict:
        return {
            "has_error": self.has_error,
            "root_cause": self.root_cause,
            "affected_service": self.affected_service,
            "error_category": self.error_category,
            "error_pattern": self.error_pattern,
            "first_occurrence": self.first_occurrence,
            "confidence_percent": self.confidence_percent,
            "needs_human_review": self.needs_human_review,
            "recommended_action": self.recommended_action,
            "raw_summary": self.raw_summary,
            "related_log_lines": self.related_log_lines,
        }