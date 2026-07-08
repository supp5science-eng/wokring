"""
Upis destilovanih podataka u Supabase (PostgreSQL).

Supabase je običan Postgres, pa se povezujemo standardnim connection string-om
(psycopg2). Alat čita DATABASE_URL iz okruženja (.env), NIKAD iz koda.

Upis je idempotentan: ponovno pokretanje za isti supplement ne pravi duplikate —
supplement se upsertuje po slug-u, a deca (forme, ciljevi, upozorenja, veze) se
prvo obrišu pa ponovo ubace. Sve u jednoj transakciji: ili prođe celo, ili ništa.

Podaci ulaze kao status='draft'. Tek `approve()` ih prebaci u 'live'.
"""
from __future__ import annotations

import os
from typing import Optional

import psycopg2
import psycopg2.extras

# Dozvoljene vrednosti (moraju se poklopiti sa ENUM-ima u db/schema.sql).
_EVIDENCE = {"strong", "moderate", "weak", "insufficient", "no_effect"}
_UNITS = {"mg", "g", "mcg", "IU", "billion_CFU", "ml"}
_TIMING = {"morning", "evening", "with_meal", "empty_stomach", "split", "any"}
_DIRECTION = {"supports", "mixed", "against"}
_SEVERITY = {"info", "caution", "strong_caution"}
_STUDY_TYPES = {"meta_analysis", "systematic_review", "rct", "other"}


def _conn(database_url: Optional[str] = None):
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL nije postavljen. Uzmi ga iz Supabase: "
            "Settings → Database → Connection string (URI), i stavi u .env"
        )
    return psycopg2.connect(url)


def _norm(value, allowed: set, default=None):
    """Normalizuj (razmak→_) i validiraj vrednost prema dozvoljenom skupu."""
    if value is None:
        return default
    v = str(value).strip().replace(" ", "_")
    return v if v in allowed else default


def upload(payload: dict, database_url: Optional[str] = None) -> dict:
    """Upiši jedan ingest payload (output/{slug}.json) u bazu. Vrati statistiku."""
    d = payload.get("distilled")
    if not d:
        raise RuntimeError("payload nema 'distilled' podatke — pokreni ingest sa AI korakom.")

    slug = payload["slug"]
    supp = d["supplement"]
    stats = {"forms": 0, "goals": 0, "studies": 0, "study_links": 0, "cautions": 0, "skipped_goals": []}

    conn = _conn(database_url)
    try:
        with conn:  # transakcija: commit na izlazu, rollback na grešci
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # 1) supplement (upsert po slug)
                cur.execute(
                    """
                    insert into supplements (slug, name_sr, name_en, category, summary_sr, status, distilled_at)
                    values (%s, %s, %s, %s, %s, 'draft', now())
                    on conflict (slug) do update set
                        name_sr = excluded.name_sr, name_en = excluded.name_en,
                        category = excluded.category, summary_sr = excluded.summary_sr,
                        distilled_at = now(), updated_at = now()
                    returning id
                    """,
                    (slug, supp.get("name_sr", slug), supp.get("name_en"),
                     supp.get("category"), supp.get("summary_sr")),
                )
                supp_id = cur.fetchone()["id"]

                # očisti decu radi idempotencije
                cur.execute("delete from supplement_forms where supplement_id = %s", (supp_id,))
                cur.execute("delete from cautions where supplement_id = %s", (supp_id,))
                cur.execute(
                    "delete from supplement_goals where supplement_id = %s", (supp_id,))

                # 2) studije (upsert po pmid) — iz sirovih rangiranih studija
                for s in payload.get("studies", []):
                    st = _norm(s.get("study_type"), _STUDY_TYPES, "other")
                    cur.execute(
                        """
                        insert into studies (pmid, title, journal, year, study_type, sample_size, abstract)
                        values (%s, %s, %s, %s, %s, %s, %s)
                        on conflict (pmid) do update set
                            title = excluded.title, journal = excluded.journal,
                            year = excluded.year, study_type = excluded.study_type,
                            sample_size = excluded.sample_size, abstract = excluded.abstract
                        """,
                        (s["pmid"], s.get("title", ""), s.get("journal"), s.get("year"),
                         st, s.get("sample_size"), s.get("abstract")),
                    )
                    stats["studies"] += 1

                # 3) forme → mapa {form_name: id} za povezivanje sa ciljevima
                form_ids: dict[str, int] = {}
                for f in d.get("forms", []):
                    cur.execute(
                        """
                        insert into supplement_forms
                            (supplement_id, form_name, bioavailability_rank, is_recommended,
                             typical_label_names, note_sr)
                        values (%s, %s, %s, %s, %s, %s)
                        returning id
                        """,
                        (supp_id, f["form_name"], f.get("bioavailability_rank"),
                         bool(f.get("is_recommended")), f.get("typical_label_names"),
                         f.get("note_sr")),
                    )
                    form_ids[f["form_name"]] = cur.fetchone()["id"]
                    stats["forms"] += 1

                # učitaj goal slug→id (ciljevi su seed-ovani u šemi)
                cur.execute("select id, slug from goals")
                goal_ids = {r["slug"]: r["id"] for r in cur.fetchall()}

                # 4) ciljevi + veze ka studijama
                all_goals = list(d.get("goals", []))
                # ciljevi bez dovoljno dokaza — takođe se čuvaju (evidence insufficient/no_effect)
                for ng in d.get("not_supported_goals", []):
                    all_goals.append({
                        "goal_slug": ng.get("goal_slug"),
                        "evidence_level": ng.get("evidence_level", "insufficient"),
                        "notes_sr": ng.get("note_sr"),
                        "supporting_pmids": [],
                    })

                for g in all_goals:
                    gslug = g.get("goal_slug")
                    if gslug not in goal_ids:
                        stats["skipped_goals"].append(gslug)
                        continue
                    ev = _norm(g.get("evidence_level"), _EVIDENCE, "insufficient")
                    unit = _norm(g.get("dose_unit"), _UNITS)
                    timing = _norm(g.get("timing"), _TIMING, "any")
                    pref_form_id = form_ids.get(g.get("preferred_form_name")) if g.get("preferred_form_name") else None

                    cur.execute(
                        """
                        insert into supplement_goals
                            (supplement_id, goal_id, evidence_level, dose_min, dose_max, dose_unit,
                             dose_per_kg, preferred_form_id, timing, onset_weeks, sex_note_sr, notes_sr)
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        returning id
                        """,
                        (supp_id, goal_ids[gslug], ev, g.get("dose_min"), g.get("dose_max"), unit,
                         g.get("dose_per_kg"), pref_form_id, timing, g.get("onset_weeks"),
                         g.get("sex_note_sr"), g.get("notes_sr")),
                    )
                    sg_id = cur.fetchone()["id"]
                    stats["goals"] += 1

                    directions = g.get("direction_per_pmid", {}) or {}
                    for pmid in g.get("supporting_pmids", []) or []:
                        direction = _norm(directions.get(pmid), _DIRECTION, "supports")
                        # veza samo ako studija postoji u bazi (izbegni FK grešku)
                        cur.execute("select 1 from studies where pmid = %s", (str(pmid),))
                        if cur.fetchone():
                            cur.execute(
                                """
                                insert into supplement_goal_studies (supplement_goal_id, pmid, direction)
                                values (%s, %s, %s)
                                on conflict do nothing
                                """,
                                (sg_id, str(pmid), direction),
                            )
                            stats["study_links"] += 1

                # 5) upozorenja
                for c in d.get("cautions", []):
                    sev = _norm(c.get("severity"), _SEVERITY, "caution")
                    cur.execute(
                        """
                        insert into cautions (supplement_id, population, severity, text_sr)
                        values (%s, %s, %s, %s)
                        """,
                        (supp_id, c.get("population", "general"), sev, c.get("text_sr", "")),
                    )
                    stats["cautions"] += 1
    finally:
        conn.close()

    stats["supplement_id"] = supp_id
    return stats


def approve(slug: str, database_url: Optional[str] = None) -> bool:
    """Prebaci supplement iz draft u live."""
    conn = _conn(database_url)
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "update supplements set status = 'live', updated_at = now() where slug = %s",
                    (slug,),
                )
                return cur.rowcount > 0
    finally:
        conn.close()
