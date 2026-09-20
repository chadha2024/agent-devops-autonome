"""
Module d'analyse des logs par LLM — point d'entrée principal.

Usage :
    python main.py --namespace monitoring
    python main.py --namespace monitoring --pod loki

Récupère les logs récents depuis Loki, les envoie à Gemini pour analyse,
et affiche le diagnostic structuré obtenu.
"""

import argparse
import json
import sys

from loki_client import fetch_logs
from llm_analyzer import analyze_logs
import config


def main():
    parser = argparse.ArgumentParser(description="Analyse des logs par LLM")
    parser.add_argument("--namespace", default="monitoring", help="Namespace Kubernetes à analyser")
    parser.add_argument("--pod", default=None, help="Filtrer sur un nom de pod précis (préfixe)")
    parser.add_argument("--minutes", type=int, default=config.LOG_LOOKBACK_MINUTES, help="Fenêtre de temps à analyser")
    parser.add_argument("--context", default="", help="Contexte additionnel (ex: alerte du module de détection)")
    args = parser.parse_args()

    print(f"Récupération des logs (namespace={args.namespace}, pod={args.pod}, {args.minutes} min)...")
    try:
        logs = fetch_logs(namespace=args.namespace, minutes=args.minutes, pod=args.pod)
    except Exception as e:
        print(f"[ERREUR] Impossible de récupérer les logs depuis Loki : {e}")
        sys.exit(1)

    print(f"{len(logs)} lignes de log récupérées.")

    if not logs:
        print("Aucun log à analyser sur cette période.")
        return

    print("Envoi à Gemini pour analyse...")
    try:
        diagnostic = analyze_logs(logs, context=args.context)
    except Exception as e:
        print(f"[ERREUR] Analyse LLM échouée : {e}")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("DIAGNOSTIC STRUCTURÉ")
    print("=" * 60)
    print(json.dumps(diagnostic.to_dict(), indent=2, ensure_ascii=False))

    # US 2.2 : sous le seuil de confiance, on le signale explicitement
    # plutôt que de laisser l'orchestrateur agir seul sur un diagnostic
    # incertain.
    if diagnostic.needs_human_review:
        print("\n[ATTENTION] Confiance insuffisante "
              f"({diagnostic.confidence_percent}%) — intervention humaine requise "
              "avant toute remédiation automatique.")


if __name__ == "__main__":
    main()