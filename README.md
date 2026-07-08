# DoseCheck

Web aplikacija koja korisniku kaže da li mu neki supplement (i njegova doza)
zaista pomaže za njegov cilj — na osnovu naučnih studija i njegovog profila
(pol, kilaža, godine, stres, alergije, stanja).

> ⚠️ DoseCheck prikazuje rezultat obrade naučnih podataka, **nije medicinski savet**.

## Status

**Faza 0 — baza podataka i data pipeline** (ovaj kod). Web app je Faza 1.

## Struktura

```
db/schema.sql                 Supabase/PostgreSQL šema (8 tabela + seed ciljeva)
docs/PRD-01-data-pipeline.md  PRD: kako baza i pipeline rade
pipeline/                     Alat koji puni bazu sa PubMed + AI destilacijom
```

## Pipeline — kako se puni baza

Za svaki supplement pokreneš jednu komandu; ona povuče najkvalitetnije studije
sa PubMed-a, Claude ih destiluje u strukturirane doze/ciljeve/upozorenja, a ti
pregledaš rezultat pre nego što uđe u bazu.

```bash
cd pipeline
pip install -r requirements.txt          # samo za AI korak; PubMed radi bez ičega
export ANTHROPIC_API_KEY=...             # za destilaciju
python ingest.py --supplement "probiotics" --slug probiotics
```

Rezultat je u `pipeline/output/`:
- `{slug}.json` — strukturirani draft (studije + destilovani podaci)
- `{slug}-review.md` — čitljiv pregled na srpskom koji pregledaš pre objave

Opcije: `--top N` (broj studija, default 40), `--since GOD` (najstarija godina),
`--no-ai` (samo povuci studije, bez destilacije).

### Filter kvaliteta

Pipeline uzima **samo meta-analize, sistematske preglede i RCT na ljudima**, sa
apstraktom, od 2010. naovamo, pa ih rangira po tipu dokaza + skorašnjosti +
veličini uzorka. Tako reprodukujemo "kuraciju kvaliteta" legalno, koristeći
isključivo javni PubMed E-utilities API.

## Baza podataka

`db/schema.sql` se čisto izvršava na PostgreSQL 16. Jezgro:

| Tabela | Uloga |
|---|---|
| `supplements` | osnovni entitet, `status` draft/live |
| `supplement_forms` | forme (glicinat vs oksid, KSM-66 vs prah) + apsorpcija |
| `goals` | šifarnik ciljeva (san, stres, creva… 15 seed vrednosti) |
| `supplement_goals` | **srce**: doza + nivo dokaza + timing po (supplement, cilj) |
| `studies` | PubMed studije (meta/SR/RCT) |
| `supplement_goal_studies` | veza nalaza ↔ studija (za "pun izveštaj") |
| `cautions` | upozorenja po populaciji (trudnoća, štitna…) |
| `interactions` | odnos dva suplementa (`overlap` = rade isto → stack analiza) |

### Lokalna provera šeme

```bash
createdb dosecheck && psql dosecheck -f db/schema.sql
```

## Napomena o mreži

PubMed poziv zahteva izlaz ka `eutils.ncbi.nlm.nih.gov`. U okruženjima sa
restriktivnom egress politikom taj host mora biti dozvoljen; inače `ingest.py`
padne na koraku pretrage (403 sa proksija).
