# DoseCheck — PRD (v1)

> ## ⚠️ NAJVAŽNIJE PRAVILO — PROČITAJ PRVO
> Ovo je **prva verzija (v1)** aplikacije. Cilj je da bude **što JEDNOSTAVNIJA** i da
> uradimo **usko, ne široko**. Bolje jedna stvar koja radi savršeno nego deset polovičnih.
> Ako se pojavi ideja koja nije u sekciji „Obim v1" dole — **ne radi je**, zapiši je u
> „Kasnije (NE v1)". Otpor prema širenju je feature, ne bug.

---

## 1. Šta je DoseCheck i zašto postoji

Web aplikacija koja korisniku otkriva **da li ga brend suplemenata obmanjuje** — najčešće
tako što **zakida na dozi** (stavi zvezdani sastojak u tragovima da bi ga naveo na etiketi).

DoseCheck NIJE zdravstveni savetnik. To je **detektor prevare na etiketi**. Korisnik unese
sastojke i doze sa svog proizvoda, a app kaže: je l' doza dovoljna, je l' forma poštena, i
vredi li sastojak uopšte za ono što ga muči.

> Disclaimer (uvek vidljiv): „DoseCheck prikazuje obradu naučnih podataka — nije medicinski savet."

---

## 2. Obim v1 — „Provera 3 prevare" (OVO i ništa više)

Za svaki sastojak koji korisnik unese, app proverava **tačno 3 stvari**:

| # | Provera | Hvata prevaru | Podatak iz baze |
|---|---|---|---|
| 1 | **DOZA** — je li uneta doza ≥ efektivne? | fairy dusting / potcenjena doza | `supplement_goals.dose_min` |
| 2 | **FORMA** — je li oblik dobro upijajuć? | jeftina forma (oksid, D2…) | `supplement_forms.bioavailability_rank`, `is_recommended` |
| 3 | **DOKAZ** — ima li dokaza baš za taj cilj? | punilo / sastojak bez veze | `supplement_goals.evidence_level` |

Rezultat = **„Poštenje etikete"** ocena (0–100 + boja) + kratka **AI rečenica** (persiranje) +
razrada po sastojku sa 3 lampice.

**Princip:** output definiše šta AI destiluje iz studija. Pošto v1 output koristi samo
efektivnu dozu, formu i nivo dokaza — AI vadi samo to (ostalo u bazi je bonus za kasnije).

---

## 3. Korisnički tok (4 ekrana)

1. **Registracija / profil (jednom):** pol, godine, kilaža, + (opciono) trudnoća/dojenje,
   bolesti (dropdown+checkbox), alergije, nivo stresa (slajder). App zapamti — ne pita opet.
2. **Korak 1 — Problem:** korisnik bira **kategoriju problema/cilja** (Stomak i varenje,
   San i stres, Mišići i snaga, Imunitet…).
3. **Korak 2 — Etiketa:** unosi **više sastojaka + dozu za svaki** (prepisuje sa kutije).
   Progresivno — ovaj korak se otvara tek kad izabere problem.
4. **Korak 3 — Presuda:** output iz sekcije 2, personalizovan podacima iz profila.

Vizuelni mokap toka (za dizajn referencu):
https://claude.ai/code/artifact/fd0fbcc2-c0c0-4e73-9e3d-0a56a70a21d3

---

## 4. Formula za skor (v1 — transparentna, kasnije se štimuje)

Za izabrani cilj:
- `relevantni` = sastojci koji IMAJU dokaz za taj cilj (weak+).
- Ako nema nijednog relevantnog → **skor ≈ 12**, presuda „Varaju te".
- Inače:
  - `bestRatio` = min(1, max(unetaDoza / efektivnaMin) među relevantnima)
    (ako sastojak ima `dose_per_kg`, efektivnaMin = dose_per_kg × kilaža iz profila).
  - `evBonus` = najbolji dokaz među relevantnima: strong 25 / moderate 18 / weak 8.
  - `fillerPenalty` = 6 × broj sastojaka bez dokaza za cilj.
  - `skor = clamp(bestRatio×70 + evBonus − fillerPenalty, 6, 97)`.
- Presuda po boji: **≥70 zeleno „Pošteno"** · **45–69 žuto „Osrednje — preplaćuješ"** ·
  **<45 crveno „Varaju te"**.

Skor je **deterministički** (uvek isti za isti unos). AI samo piše komentar, NE računa skor.

---

## 5. Šta VEĆ postoji u repou (ne praviti ponovo!)

| Fajl / folder | Šta je | Status |
|---|---|---|
| `db/schema.sql` | Postgres/Supabase šema, 8 tabela + seed ciljeva | ✅ gotovo, validirano na PG16, pušteno u Supabase |
| `pipeline/` | alat koji puni bazu iz PubMed + Claude destilacija | ✅ gotovo, PubMed deo testiran offline |
| `pipeline/db_upload.py` | idempotentan upis u Supabase (`--upload`, `--approve`) | ✅ testiran lokalno na PG16 |
| `.github/workflows/ingest.yml` | pokretanje pipeline-a iz browsera (dugme) | ✅ gotovo |
| `.github/workflows/approve.yml` | draft → live iz browsera | ✅ gotovo |
| `docs/PRD-01-data-pipeline.md` | detaljan opis pipeline-a | ✅ |

Baza (8 tabela): `supplements`, `supplement_forms`, `goals`, `supplement_goals` (srce —
odavde se računa skor), `studies`, `supplement_goal_studies`, `cautions`, `interactions`.
Sve 3 provere iz v1 se već hrane iz ovih tabela — **baza ne treba prepravku za v1.**

---

## 6. Kako se puni baza (pozadina, već napravljeno)

`python pipeline/ingest.py --supplement "probiotics" --slug probiotics --upload`
1. PubMed E-utilities (besplatno) povuče SAMO meta-analize / sistematske preglede / RCT na
   ljudima, ≥2010, sa apstraktom.
2. `rank.py` rangira po tipu dokaza + skorašnjosti + veličini uzorka → top ~40.
3. `distill.py` (Claude **Sonnet**, model `claude-sonnet-5`) izvuče strukturiran JSON:
   po cilju → nivo dokaza + efektivna doza (+ po kg gde treba); forme + apsorpcija;
   upozorenja. STROGO: samo iz apstrakata, nikad izmišljene doze, svaki nalaz sa PMID izvorom.
4. Upis u Supabase kao `status='draft'`.
5. Vlasnik pregleda `pipeline/output/{slug}-review.md`, pa `--approve {slug}` → `live`.

Tajne idu u GitHub Secrets (za Actions) ili `.env` (lokalno): `ANTHROPIC_API_KEY`, `DATABASE_URL`.

---

## 7. Prvih 10 suplemenata (za start)

probiotici, magnezijum, vitamin D3, ašvaganda, omega-3, kreatin, melatonin, cink, vitamin C,
whey protein. Dodaje se lako, jedan po jedan, kroz pipeline.

Kategorije problema/ciljeva za v1 (koristiti seed iz `db/schema.sql` — 15 ciljeva):
Stomak i varenje, San i stres, Mišići i snaga, Imunitet, Srce, Mozak/fokus…

---

## 8. Tehnologija

- **Frontend + backend:** Next.js (App Router, TypeScript), deploy na **Vercel**.
- **Baza + auth:** **Supabase** (Postgres + Supabase Auth). Šema je `db/schema.sql`.
- **AI komentar:** Claude API, model `claude-sonnet-5`, samo za personalizovanu rečenicu
  uz presudu (NE za računanje skora). Trošak zanemarljiv (~$0.007 po proveri).
- **Jezik:** srpski default, prekidač za engleski (svi tekstovi već imaju `_sr`/`_en` u bazi).
- **Dizajn:** „medicinski trust + ultra jednostavno". Bela/hladna podloga, klinički tirkiz
  kao akcenat, semafor boje (zelena/žuta/crvena/siva) za presudu. Svetla i tamna tema.
  Mono font za doze (kao laboratorijsko očitavanje). Referenca: mokap iz sekcije 3.

---

## 9. Redosled izgradnje v1 (predlog za Claude Code)

1. **Napuni bazu za bar 2–3 suplementa** kroz pipeline (npr. probiotici, magnezijum, đumbir),
   da app ima na čemu da radi. (Pokreće se iz browsera preko `ingest.yml` ili lokalno.)
2. **Next.js skelet + Supabase konekcija + Auth** (registracija/login, tabela profila).
   Napraviti tabelu `profiles` (vezanu na `auth.users`): pol, godine, kilaža, trudnoća,
   bolesti[], alergije[], stres. (Ovo je jedini novi deo baze — dodati kao migraciju.)
3. **Ekran profila** (popuni jednom).
4. **Korak 1** (izbor kategorije problema) → **Korak 2** (unos sastojaka+doza).
5. **Motor za skor** (sekcija 4) — čist TS modul koji čita `supplement_goals` /
   `supplement_forms` iz Supabase i vrati 3 provere + skor. Deterministički, pokriven testovima.
6. **Korak 3 — presuda** (UI iz mokapa) + **AI rečenica** (Claude Sonnet poziv sa
   strukturiranim ulazom: profil + rezultat provera → 1–2 rečenice, persiranje).
7. **Prekidač jezika**, disclaimer, dark mode.
8. Deploy na Vercel.

---

## 10. Kasnije (NE v1) — zapisati ideje ovde, ne raditi ih

- Baza brendova + cene (value-for-money, „ko te krade" sa cenama).
- Analiza celog „stack"-a (više proizvoda) i preklapanja (`interactions` tabela već postoji).
- Interakcije sa lekovima; „proprietary blend" crvena zastava; amino spiking.
- Sitnija taksonomija simptoma (mučnina/nadutost umesto samo „stomak") + sinonimi za pretragu.
- „Jačina efekta" (koliko JAKO radi, ne samo da/ne); timing modul; očekivanje (za koliko nedelja).
- Slikanje/skeniranje etikete, voice unos.
- Email reportovi (nova studija promeni skor tvog suplementa).
- Predlog alternative kad proizvod ne valja; „predloži mi najbolje" dugme.
- Nivo dokaza + linkovi ka studijama u „Pun izveštaj" (podaci već postoje: `supplement_goal_studies`).

---

## 11. Otvorena pitanja za vlasnika (ne blokiraju start)

- Finalna lista kategorija problema (v1 koristi 15 seed ciljeva iz šeme).
- Tačne vrednosti skor-formule (štimovati posle prvih pravih podataka).
