-- ============================================================================
-- DoseCheck — Šema baze podataka (Supabase / PostgreSQL)
-- Faza 0: jezgro baze suplemenata (bez app tabela profiles/checks — one dolaze u Fazi 1)
--
-- Konvencije:
--   * sav tekst ima _sr i _en varijantu (srpski default, engleski opcion)
--   * enumeri su Postgres ENUM tipovi radi integriteta
--   * "status" (draft|live) omogućava da pipeline upiše draft, vlasnik odobri -> live
-- ============================================================================

-- ---------- ENUM tipovi ----------------------------------------------------
create type content_status  as enum ('draft', 'live', 'archived');
create type evidence_level   as enum ('strong', 'moderate', 'weak', 'insufficient', 'no_effect');
create type dose_unit        as enum ('mg', 'g', 'mcg', 'IU', 'billion_CFU', 'ml');
create type timing_kind      as enum ('morning', 'evening', 'with_meal', 'empty_stomach', 'split', 'any');
create type study_type       as enum ('meta_analysis', 'systematic_review', 'rct', 'other');
create type study_direction  as enum ('supports', 'mixed', 'against');
create type caution_severity as enum ('info', 'caution', 'strong_caution');
create type interaction_kind as enum ('overlap', 'synergy', 'caution');

-- ---------- 1. supplements -------------------------------------------------
-- Osnovni entitet: jedan supplement (npr. "Ašvaganda", "Probiotici").
create table supplements (
    id           bigint generated always as identity primary key,
    slug         text not null unique,              -- 'ashwagandha', 'probiotics'
    name_sr      text not null,
    name_en      text not null,
    category     text,                              -- 'adaptogen', 'mineral', 'vitamin', ...
    summary_sr   text,
    summary_en   text,
    status       content_status not null default 'draft',
    distilled_at timestamptz,                       -- kad je pipeline poslednji put obradio
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

-- ---------- 2. supplement_forms -------------------------------------------
-- Hemijske forme istog suplementa se razlikuju po apsorpciji.
-- Npr. Mg: oksid (loš) vs glicinat (dobar); Ašvaganda: KSM-66 vs sirovi prah.
create table supplement_forms (
    id                  bigint generated always as identity primary key,
    supplement_id       bigint not null references supplements(id) on delete cascade,
    form_name           text not null,             -- 'magnesium glycinate', 'KSM-66'
    bioavailability_rank smallint check (bioavailability_rank between 1 and 5),
    is_recommended      boolean not null default false,
    typical_label_names text[],                    -- kako piše na etiketama (za prepoznavanje brenda)
    note_sr             text,
    note_en             text,
    unique (supplement_id, form_name)
);

-- ---------- 3. goals -------------------------------------------------------
-- Šifarnik ciljeva. Popunjen seed-om na dnu fajla.
create table goals (
    id       bigint generated always as identity primary key,
    slug     text not null unique,                 -- 'sleep', 'stress', 'gut'
    name_sr  text not null,
    name_en  text not null,
    icon     text                                  -- naziv ikonice za UI (kasnije)
);

-- ---------- 4. supplement_goals -------------------------------------------
-- SRCE SISTEMA. Iz ovoga se računa deterministički skor za korisnika.
-- Jedan red = "koliko i kako supplement X pomaže za cilj Y".
create table supplement_goals (
    id               bigint generated always as identity primary key,
    supplement_id    bigint not null references supplements(id) on delete cascade,
    goal_id          bigint not null references goals(id) on delete cascade,
    evidence_level   evidence_level not null,
    dose_min         numeric,                      -- efektivni opseg doze
    dose_max         numeric,
    dose_unit        dose_unit,
    dose_per_kg      numeric,                       -- popunjeno SAMO gde studije skaliraju po kg (npr. kreatin)
    preferred_form_id bigint references supplement_forms(id) on delete set null,
    timing           timing_kind not null default 'any',
    onset_weeks      smallint,                      -- za koliko nedelja efekat postaje vidljiv
    sex_note_sr      text,                          -- ako se doza/efekat razlikuje po polu
    sex_note_en      text,
    notes_sr         text,
    notes_en         text,
    unique (supplement_id, goal_id)
);

-- ---------- 5. studies -----------------------------------------------------
-- Naučne studije povučene sa PubMed-a (samo meta/SR/RCT na ljudima).
create table studies (
    pmid        text primary key,                  -- PubMed ID
    title       text not null,
    journal     text,
    year        smallint,
    study_type  study_type not null default 'other',
    sample_size integer,
    abstract    text,
    pubmed_url  text generated always as ('https://pubmed.ncbi.nlm.nih.gov/' || pmid || '/') stored
);

-- ---------- 6. supplement_goal_studies ------------------------------------
-- Veza: koja studija podupire koji (supplement, cilj) nalaz. Puni "View full report".
create table supplement_goal_studies (
    supplement_goal_id bigint not null references supplement_goals(id) on delete cascade,
    pmid               text   not null references studies(pmid) on delete cascade,
    direction          study_direction not null default 'supports',
    primary key (supplement_goal_id, pmid)
);

-- ---------- 7. cautions ----------------------------------------------------
-- Upozorenja po populaciji. Mapira se na checkbox trudnoće i dropdown bolesti iz profila.
create table cautions (
    id            bigint generated always as identity primary key,
    supplement_id bigint not null references supplements(id) on delete cascade,
    population    text not null,                    -- 'pregnancy', 'thyroid', 'blood_pressure_meds', ...
    severity      caution_severity not null default 'caution',
    text_sr       text not null,
    text_en       text
);

-- ---------- 8. interactions -----------------------------------------------
-- Odnos između dva suplementa. 'overlap' = rade istu stvar (za stack analizu:
-- "koristiš 2 koja rade isto, uzmi bolji").
create table interactions (
    id               bigint generated always as identity primary key,
    supplement_a_id  bigint not null references supplements(id) on delete cascade,
    supplement_b_id  bigint not null references supplements(id) on delete cascade,
    type             interaction_kind not null,
    text_sr          text not null,
    text_en          text,
    check (supplement_a_id < supplement_b_id)       -- bez duplikata (A,B)=(B,A)
);

-- ---------- Indeksi za česte upite ----------------------------------------
create index idx_supplement_goals_supplement on supplement_goals(supplement_id);
create index idx_supplement_goals_goal       on supplement_goals(goal_id);
create index idx_forms_supplement            on supplement_forms(supplement_id);
create index idx_cautions_supplement         on cautions(supplement_id);
create index idx_sgs_pmid                    on supplement_goal_studies(pmid);

-- ============================================================================
-- SEED: ciljevi (goals). Prošireno prema "što je više moguće, vezano za suplemente".
-- ============================================================================
insert into goals (slug, name_sr, name_en) values
    ('sleep',      'San',                 'Sleep'),
    ('stress',     'Stres i anksioznost', 'Stress & anxiety'),
    ('energy',     'Energija',            'Energy'),
    ('focus',      'Fokus i pamćenje',    'Focus & memory'),
    ('muscle',     'Mišićna masa i snaga','Muscle & strength'),
    ('recovery',   'Oporavak',            'Recovery'),
    ('immunity',   'Imunitet',            'Immunity'),
    ('gut',        'Varenje i creva',     'Gut health'),
    ('heart',      'Zdravlje srca',       'Heart health'),
    ('joints',     'Zglobovi',            'Joints'),
    ('mood',       'Raspoloženje',        'Mood'),
    ('hormones',   'Hormoni',             'Hormones'),
    ('skin_hair',  'Koža i kosa',         'Skin & hair'),
    ('bone',       'Kosti',               'Bone health'),
    ('libido',     'Libido',              'Libido');
