#!/usr/bin/env python3
"""
DoseCheck ingest — CLI za punjenje baze suplemenata.

Tok:
  1. PubMed pretraga (filteri kvaliteta) + povlačenje apstrakata
  2. Rangiranje bez AI-ja -> top N studija
  3. Destilacija Claude-om -> strukturirani JSON (ako ima API ključa)
  4. Upis draft JSON + čitljiv review markdown u output/
  5. (opciono, kad Supabase postoji) upload u bazu kao status='draft'

Primeri:
  python ingest.py --supplement "probiotics" --slug probiotics
  python ingest.py --supplement "ashwagandha" --slug ashwagandha --top 30
  python ingest.py --supplement "creatine" --slug creatine --no-ai   # samo studije
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

import pubmed
import rank as ranker
from pubmed import Study

# Učitaj .env ako postoji (ANTHROPIC_API_KEY, DATABASE_URL). Radi i bez paketa.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

OUTPUT_DIR = Path(__file__).parent / "output"


def build_review_md(term: str, slug: str, studies: list[Study], distilled: dict | None) -> str:
    lines = [f"# Review: {term}  (`{slug}`)", ""]
    stats = ranker.summarize(studies)
    lines.append(f"**Studija za pregled:** {stats['total']}  ·  "
                 f"po tipu: {stats['by_type']}  ·  sa uzorkom: {stats['with_sample_size']}")
    lines.append("")

    if distilled:
        s = distilled.get("supplement", {})
        lines += [f"## {s.get('name_sr', term)}", "", s.get("summary_sr", "") or "", ""]

        forms = distilled.get("forms", [])
        if forms:
            lines += ["### Forme", "", "| Forma | Apsorpcija (1-5) | Preporučeno | Napomena |",
                      "|---|---|---|---|"]
            for f in forms:
                lines.append(f"| {f.get('form_name','')} | {f.get('bioavailability_rank','')} | "
                             f"{'✅' if f.get('is_recommended') else ''} | {f.get('note_sr','') or ''} |")
            lines.append("")

        goals = distilled.get("goals", [])
        if goals:
            lines += ["### Ciljevi i doze", "",
                      "| Cilj | Dokazi | Doza | Jedinica | Timing | Efekat za (ned.) | Studije |",
                      "|---|---|---|---|---|---|---|"]
            for g in goals:
                dose = f"{g.get('dose_min','')}–{g.get('dose_max','')}" if g.get("dose_min") else "—"
                pmids = ", ".join(g.get("supporting_pmids", [])[:5])
                lines.append(f"| {g.get('goal_slug','')} | **{g.get('evidence_level','')}** | {dose} | "
                             f"{g.get('dose_unit','') or ''} | {g.get('timing','')} | "
                             f"{g.get('onset_weeks','') or '—'} | {pmids} |")
            lines.append("")
            for g in goals:
                if g.get("notes_sr"):
                    lines.append(f"- **{g.get('goal_slug')}**: {g['notes_sr']}")
            lines.append("")

        cautions = distilled.get("cautions", [])
        if cautions:
            lines += ["### Upozorenja", ""]
            for c in cautions:
                lines.append(f"- ⚠️ **{c.get('population')}** ({c.get('severity')}): {c.get('text_sr','')}")
            lines.append("")

        nsg = distilled.get("not_supported_goals", [])
        if nsg:
            lines += ["### Ciljevi bez dovoljno dokaza", ""]
            for g in nsg:
                lines.append(f"- {g.get('goal_slug')} ({g.get('evidence_level')}): {g.get('note_sr','') or ''}")
            lines.append("")
    else:
        lines += ["> _AI destilacija nije pokrenuta (nema ANTHROPIC_API_KEY ili --no-ai)._",
                  "> _Ispod su sirove rangirane studije spremne za destilaciju._", ""]

    lines += ["---", "", "## Rangirane studije (izvor)", "",
              "| # | Tip | God. | n | Naslov | PubMed |", "|---|---|---|---|---|---|"]
    for i, s in enumerate(studies, 1):
        title = (s.title[:90] + "…") if len(s.title) > 90 else s.title
        lines.append(f"| {i} | {s.study_type} | {s.year or ''} | {s.sample_size or ''} | "
                     f"{title} | [{s.pmid}](https://pubmed.ncbi.nlm.nih.gov/{s.pmid}/) |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="DoseCheck — puni bazu suplemenata sa PubMed + AI")
    ap.add_argument("--supplement", help='PubMed pojam, npr. "probiotics"')
    ap.add_argument("--slug", help="slug za bazu (default: iz --supplement)")
    ap.add_argument("--top", type=int, default=40, help="broj najkvalitetnijih studija (default 40)")
    ap.add_argument("--retmax", type=int, default=200, help="max rezultata iz pretrage")
    ap.add_argument("--since", type=int, default=2010, help="najstarija godina (default 2010)")
    ap.add_argument("--no-ai", action="store_true", help="preskoči destilaciju, samo studije")
    ap.add_argument("--upload", action="store_true",
                    help="posle destilacije upiši u Supabase kao draft (traži DATABASE_URL)")
    ap.add_argument("--approve", metavar="SLUG",
                    help="prebaci postojeći supplement iz draft u live i izađi")
    args = ap.parse_args(argv)

    # --approve je samostalna komanda: odobri i izađi.
    if args.approve:
        import db_upload
        ok = db_upload.approve(args.approve)
        print(f"{'✅ live' if ok else '⚠️  nije nađen'}: {args.approve}")
        return 0 if ok else 1

    if not args.supplement:
        ap.error("--supplement je obavezan (osim uz --approve)")

    slug = args.slug or args.supplement.lower().replace(" ", "-")
    OUTPUT_DIR.mkdir(exist_ok=True)

    print(f"[1/4] PubMed pretraga: '{args.supplement}' (filteri: meta/SR/RCT, humans, ≥{args.since})…")
    all_studies = pubmed.fetch_studies(args.supplement, retmax=args.retmax, since_year=args.since)
    print(f"       nađeno {len(all_studies)} studija sa apstraktom")
    if not all_studies:
        print("       ⚠️  Nema rezultata — proveri pojam.")
        return 1

    print(f"[2/4] Rangiranje -> top {args.top}…")
    top = ranker.rank(all_studies, top_n=args.top)
    print(f"       {ranker.summarize(top)}")

    distilled = None
    if not args.no_ai:
        print(f"[3/4] Destilacija (Claude Sonnet)…")
        try:
            import distill as distiller
            distilled = distiller.distill(args.supplement, top)
            print(f"       ✅ destilovano: {len(distilled.get('goals', []))} ciljeva, "
                  f"{len(distilled.get('forms', []))} formi, {len(distilled.get('cautions', []))} upozorenja")
        except RuntimeError as e:
            print(f"       ⏭️  {e}")
    else:
        print("[3/4] Destilacija preskočena (--no-ai)")

    print("[4/4] Upisujem draft…")
    payload = {
        "term": args.supplement,
        "slug": slug,
        "status": "draft",
        "studies": [asdict(s) for s in top],
        "distilled": distilled,
    }
    json_path = OUTPUT_DIR / f"{slug}.json"
    md_path = OUTPUT_DIR / f"{slug}-review.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(build_review_md(args.supplement, slug, top, distilled), encoding="utf-8")
    print(f"       → {json_path}")
    print(f"       → {md_path}")

    if args.upload:
        if not distilled:
            print("       ⚠️  Nema destilovanih podataka za upload (pokreni bez --no-ai i sa API ključem).")
            return 1
        import db_upload
        print("       Upisujem u Supabase (draft)…")
        stats = db_upload.upload(payload)
        print(f"       ✅ upisano: {stats}")
        print(f"\nPregledaj review, pa odobri sa:  python ingest.py --approve {slug}")
    else:
        print("\nGotovo. Pregledaj review .md, pa pokreni ponovo sa --upload da upišeš u bazu.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
