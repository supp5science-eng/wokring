"""
Destilacija studija u strukturirane podatke pomoću Claude (Sonnet).

Pristup map-reduce nije neophodan za ~40 apstrakata (stanu u jedan poziv sa
velikim kontekstom), ali batch-ujemo radi urednosti i kontrole tokena. Rezultat
je JSON koji 1:1 odgovara db/schema.sql.

Zahteva ANTHROPIC_API_KEY u okruženju i `anthropic` paket (requirements.txt).
Ako ključ ne postoji, funkcija podiže RuntimeError sa jasnom porukom — pozivalac
(ingest.py) to gracefully preskoči i sačuva samo sirove studije.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pubmed import Study

MODEL = "claude-sonnet-5"
_PROMPT_PATH = Path(__file__).parent / "prompts" / "distill_prompt.md"


def _format_studies(studies: list[Study]) -> str:
    blocks = []
    for s in studies:
        blocks.append(
            f"PMID: {s.pmid}\n"
            f"Tip: {s.study_type} | Godina: {s.year} | n≈{s.sample_size or '?'}\n"
            f"Naslov: {s.title}\n"
            f"Apstrakt: {s.abstract}\n"
        )
    return "\n---\n".join(blocks)


def distill(supplement_term: str, studies: list[Study]) -> dict:
    """Pošalji studije Claude-u i vrati strukturirani dict. Podiže RuntimeError
    ako nema API ključa ili paketa."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY nije postavljen — preskačem destilaciju. "
            "Sirove studije su sačuvane; pokreni ponovo sa ključem za AI korak."
        )
    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise RuntimeError("Instaliraj zavisnosti: pip install -r requirements.txt") from e

    prompt = _PROMPT_PATH.read_text(encoding="utf-8").replace("{SUPPLEMENT}", supplement_term)
    studies_text = _format_studies(studies)

    client = Anthropic(api_key=api_key)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system=prompt,
        messages=[{
            "role": "user",
            "content": f"Evo {len(studies)} apstrakata o '{supplement_term}':\n\n{studies_text}\n\n"
                       f"Vrati JSON prema šemi iz instrukcija.",
        }],
    )
    raw = msg.content[0].text.strip()
    # Ukloni eventualne ```json ograde ako model ipak doda.
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1].lstrip("json").strip()
    return json.loads(raw)
