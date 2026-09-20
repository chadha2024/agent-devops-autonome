"""
Analyse des logs via l'API Gemini (Google AI).

Envoie un lot de lignes de logs au LLM, avec des instructions précises
pour qu'il réponde UNIQUEMENT en JSON structuré (pas de texte libre autour),
ce qui permet ensuite de parser sa réponse automatiquement dans le code.
"""

import json
import logging
from google import genai
from google.genai import types

from config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    ERROR_CATEGORIES,
    CONFIDENCE_THRESHOLD_PERCENT,
)
from diagnostic import LogDiagnostic

logger = logging.getLogger(__name__)


def _build_system_prompt() -> str:
    categories_list = ", ".join(ERROR_CATEGORIES)
    return f"""Tu es un expert SRE/DevOps chargé d'analyser des logs \
applicatifs pour un agent DevOps autonome. Ton rôle : lire des logs bruts \
et produire un diagnostic structuré, sans jamais inventer d'information \
absente des logs fournis.

Réponds STRICTEMENT en JSON valide, sans aucun texte avant ou après, avec \
exactement ces champs :
{{
  "has_error": bool,
  "root_cause": "cause probable en une phrase, ou '' si aucune erreur",
  "affected_service": "nom du service/pod concerné, ou '' si inconnu",
  "error_category": "DOIT être exactement l'une de ces valeurs : {categories_list}",
  "error_pattern": "motif d'erreur en texte libre pour contexte (ex: OOMKilled, connexion refusée), ou ''",
  "first_occurrence": "horodatage approximatif du premier signe du problème, ou ''",
  "confidence_percent": 0-100,
  "recommended_action": "suggestion d'action corrective, à titre informatif uniquement",
  "raw_summary": "résumé en 1-2 phrases, lisible par un humain",
  "related_log_lines": ["ligne de log la plus pertinente 1", "ligne 2", "..."]
}}

Contrainte impérative sur "error_category" : choisis TOUJOURS une valeur \
parmi la liste fermée fournie ci-dessus. Si aucune catégorie connue ne \
correspond clairement, utilise "Unknown" plutôt que d'inventer une nouvelle \
catégorie.

Si les logs ne montrent aucun problème, mets has_error à false, \
error_category à "Unknown" et laisse les autres champs vides ou neutres. \
Ne jamais halluciner une cause qui n'est pas visible dans les logs fournis."""


SYSTEM_PROMPT = _build_system_prompt()


def analyze_logs(log_lines: list[str], context: str = "") -> LogDiagnostic:
    """
    Envoie les logs à Gemini et retourne un diagnostic structuré.

    `context` : information additionnelle optionnelle (ex: "le module de
    détection a signalé une anomalie CPU sur ce service à 14h32"), utile
    pour guider l'analyse avec ce qu'on sait déjà côté métriques.
    """
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY n'est pas configurée. "
            "Définis-la comme variable d'environnement avant de lancer le script."
        )

    if not log_lines:
        return LogDiagnostic(
            has_error=False,
            root_cause="",
            affected_service="",
            error_category="Unknown",
            error_pattern="",
            first_occurrence="",
            confidence_percent=0,
            needs_human_review=False,
            recommended_action="",
            raw_summary="Aucun log disponible pour cette période.",
            related_log_lines=[],
        )

    client = genai.Client(api_key=GEMINI_API_KEY)

    logs_text = "\n".join(log_lines)
    user_message = f"Contexte additionnel : {context}\n\n" if context else ""
    user_message += f"Voici les logs à analyser :\n\n{logs_text}"

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            # Force une sortie JSON valide directement, plus fiable que de
            # demander "réponds en JSON" dans le prompt seul.
            response_mime_type="application/json",
        ),
    )

    raw_text = response.text.strip()

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Le LLM n'a pas renvoyé du JSON valide : {e}\nRéponse brute : {raw_text[:500]}"
        )

    # US 2.2 : le LLM peut ignorer la contrainte de catégorie fermée malgré
    # le prompt (les modèles ne respectent pas toujours les instructions à
    # 100%). On valide donc la catégorie côté code plutôt que de faire
    # confiance aveuglément au LLM.
    error_category = parsed.get("error_category", "Unknown")
    if error_category not in ERROR_CATEGORIES:
        logger.warning(
            "Catégorie inattendue renvoyée par le LLM ('%s'), retombe sur 'Unknown'",
            error_category,
        )
        error_category = "Unknown"

    confidence_percent = parsed.get("confidence_percent", 0)

    # US 2.2 : la décision "intervention humaine requise" est une règle
    # métier fixe, appliquée en code (pas laissée au LLM) pour garantir
    # qu'elle est toujours respectée.
    needs_human_review = confidence_percent < CONFIDENCE_THRESHOLD_PERCENT

    return LogDiagnostic(
        has_error=parsed.get("has_error", False),
        root_cause=parsed.get("root_cause", ""),
        affected_service=parsed.get("affected_service", ""),
        error_category=error_category,
        error_pattern=parsed.get("error_pattern", ""),
        first_occurrence=parsed.get("first_occurrence", ""),
        confidence_percent=confidence_percent,
        needs_human_review=needs_human_review,
        recommended_action=parsed.get("recommended_action", ""),
        raw_summary=parsed.get("raw_summary", ""),
        related_log_lines=parsed.get("related_log_lines", []),
    )