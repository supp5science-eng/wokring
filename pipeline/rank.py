"""
Rangiranje studija BEZ AI-ja — deterministički filter kvaliteta.

Ovo je jezgro "Examine metodologije": od svih rezultata biramo najkvalitetnije
studije na osnovu tipa dokaza, skorašnjosti i veličine uzorka. Rezultat je
kratka lista koju AI (distill.py) posle čita.
"""
from __future__ import annotations

from datetime import datetime

from pubmed import Study

# Težina po tipu studije (hijerarhija dokaza).
_TYPE_WEIGHT = {
    "meta_analysis": 3.0,
    "systematic_review": 2.5,
    "rct": 1.5,
    "other": 0.5,
}

_CURRENT_YEAR = datetime.now().year


def score_study(s: Study) -> float:
    """Viši skor = kvalitetnija / relevantnija studija."""
    score = _TYPE_WEIGHT.get(s.study_type, 0.5)

    # Skorašnjost: do +1.0 za ovogodišnju, linearno pada kroz ~15 godina.
    if s.year:
        age = max(0, _CURRENT_YEAR - s.year)
        score += max(0.0, 1.0 - age / 15.0)

    # Veličina uzorka: logaritamski bonus (n=1000 vredniji od n=20, ali ne 50x).
    if s.sample_size:
        import math
        score += min(1.0, math.log10(s.sample_size) / 4.0)

    # Bez apstrakta nema šta da se destiluje — jaka kazna.
    if not s.abstract:
        score -= 5.0

    return round(score, 3)


def rank(studies: list[Study], top_n: int = 40) -> list[Study]:
    """Sortiraj po kvalitetu i vrati top N sa apstraktom."""
    scored = [(score_study(s), s) for s in studies if s.abstract]
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:top_n]]


def summarize(studies: list[Study]) -> dict:
    """Kratka statistika za log."""
    by_type: dict[str, int] = {}
    for s in studies:
        by_type[s.study_type] = by_type.get(s.study_type, 0) + 1
    return {
        "total": len(studies),
        "by_type": by_type,
        "with_sample_size": sum(1 for s in studies if s.sample_size),
    }
