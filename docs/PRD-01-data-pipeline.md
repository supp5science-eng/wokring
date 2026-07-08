# PRD-01 — Baza podataka i PubMed→AI pipeline

Verzija 0.1 · Faza 0 · Vlasnik: supp5science

## 1. Zašto (problem)

DoseCheck korisniku treba da odgovori: *"da li mi ovaj supplement i ova doza
zaista pomažu za moj cilj?"* — na osnovu nauke, a ne marketinga. Da bismo to
mogli, treba nam **baza kurirana iz naučnih studija**: efektivne doze, forme,
nivoi dokaza, upozorenja i preklapanja između suplemenata.

Ručno čitati stotine studija po suplementu je presporo. Zato gradimo pipeline:
PubMed daje studije, Claude ih destiluje u strukturirane podatke, vlasnik
pregleda i odobri. Ovo je Faza 0 — temelj nad kojim se gradi web app (Faza 1).

## 2. Ključne odluke

- **Izvor:** javni PubMed E-utilities API (besplatan). **Examine se ne skrejpuje**
  (ToS/autorska prava) — umesto toga reprodukujemo njihovu metodologiju kuracije:
  samo meta-analize, sistematski pregledi i RCT na ljudima.
- **AI unapred destiluje** (ne čita uživo pri svakoj proveri). Rezultat je
  strukturiran i čuva se → korisniku je skor brz, jeftin i uvek isti (poverenje).
- **Draft → review → live:** pipeline upisuje `status='draft'`; vlasnik pregleda
  `*-review.md`; tek onda podaci postaju `live` i vidljivi u app-u.
- **Determinizam:** skor korisniku se računa iz baze pravila (`supplement_goals`),
  a AI piše samo kratak personalizovan komentar (Faza 1).

## 3. Tok podataka

```
  --supplement "probiotics"
        │
        ▼
  ┌───────────────┐   esearch + efetch (filteri: meta/SR/RCT, humans, ≥2010)
  │  pubmed.py    │──────────────────────────────────────────────► ~60–200 studija
  └───────────────┘
        │
        ▼
  ┌───────────────┐   tip dokaza × skorašnjost × veličina uzorka (bez AI)
  │  rank.py      │──────────────────────────────────────────────► top ~40
  └───────────────┘
        │
        ▼
  ┌───────────────┐   Claude Sonnet, structured JSON prema schema.sql
  │  distill.py   │──────────────────────────────────────────────► doze, ciljevi, forme,
  └───────────────┘                                                 upozorenja, PMID izvori
        │
        ▼
  ┌───────────────┐   output/{slug}.json  +  output/{slug}-review.md
  │  ingest.py    │──────────────────────────────────────────────► DRAFT
  └───────────────┘
        │  (vlasnik pregleda review, odobri)
        ▼
     Supabase  status='live'   →   web app (Faza 1)
```

## 4. Koje informacije AI izvlači

Po svakom (supplement, cilj) paru:

| Polje | Primer (probiotici, cilj „creva") |
|---|---|
| evidence_level | `strong` |
| dose_min–dose_max + unit | 10–20 `billion_CFU` |
| dose_per_kg | `null` (ne skalira se po kilaži) |
| preferred_form | soj/forma sa najviše dokaza |
| timing | `with_meal` |
| onset_weeks | 4 |
| notes_sr | kratko objašnjenje na srpskom |
| supporting_pmids | \["33057475", …\] — izvori za "pun izveštaj" |

Plus, po suplementu: **forme** (apsorpcija), **upozorenja** (trudnoća, štitna…,
mapira se na profil), **interakcije** (`overlap` = rade isto → stack analiza).

Stroga pravila u promptu: izvlači samo iz apstrakata, nikad ne izmišljaj doze,
svaki nalaz mora imati PMID izvore, protivrečne studije snižavaju nivo dokaza.

## 5. Model baze

Detaljno u `db/schema.sql`. Osam tabela: `supplements`, `supplement_forms`,
`goals`, `supplement_goals` (srce — skor se računa odavde), `studies`,
`supplement_goal_studies`, `cautions`, `interactions`. Sav tekst dvojezično
(`_sr`/`_en`), enumeri za integritet, `status` za draft/live tok.

## 6. Prvih 10 suplemenata

Probiotici (prvi test), magnezijum, vitamin D3, ašvaganda, omega-3, kreatin,
melatonin, cink, vitamin C, whey protein. Redosled/izbor se potvrđuje kroz
review prvih par ingest-a.

## 7. Trošak

- PubMed: besplatno.
- Destilacija: ~40 apstrakata ≈ $0.15–0.30 po suplementu (Sonnet), jednokratno.
  Prvih 10 ≈ ~$2–3 ukupno.

## 8. Granice faze

**U Fazi 0:** šema, pipeline, review fajlovi, test na probioticima.
**Nije ovde:** web app, auth, profil, UI, skor-računica, AI komentar (Faza 1);
brendovi/cene, email reportovi (Faza 2); interakcije sa lekovima, slikanje
etikete/voice (Faza 3).

## 9. Otvorena pitanja

- Egress ka `eutils.ncbi.nlm.nih.gov` mora biti dozvoljen u okruženju gde se
  pipeline pokreće (u trenutnoj sesiji je blokiran politikom).
- Odgovori na upitnik 18–30 (utiču na Fazu 1, ne na pipeline).
- Kad Supabase projekat postoji: dodati `upload` korak u `ingest.py` (INSERT
  draft redova) i `--approve` (draft → live).
