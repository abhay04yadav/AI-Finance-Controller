-- AI Finance Controller — schema. Guide §5.6.
--
-- Money is BIGINT paise. Never NUMERIC, never FLOAT, anywhere in this file.
-- run_id on everything means you can re-run without destroying history, and
-- diff two runs — which is exactly what you want when tuning.
--
-- Nothing connects to a DB before gate 8. Written at gate 0 because it is
-- specification, not logic.

CREATE TABLE records (
  id            BIGSERIAL PRIMARY KEY,
  run_id        UUID NOT NULL,
  source        TEXT NOT NULL,
  external_id   TEXT NOT NULL,
  amount_paise  BIGINT NOT NULL,          -- never NUMERIC, never FLOAT
  value_date    DATE NOT NULL,
  direction     TEXT NOT NULL,
  narration     TEXT DEFAULT '',
  raw           JSONB NOT NULL
);

-- bank rows may legitimately duplicate (DUPLICATE_UTR); others may not
CREATE UNIQUE INDEX uq_records_nonbank
  ON records (run_id, source, external_id) WHERE source <> 'bank';

CREATE TABLE matches (
  id              BIGSERIAL PRIMARY KEY,
  run_id          UUID NOT NULL,
  bank_record_id  BIGINT REFERENCES records(id),
  ledger_ids      BIGINT[] NOT NULL,
  strategy        TEXT NOT NULL,
  confidence      NUMERIC(4,3) NOT NULL,
  reason          TEXT NOT NULL,
  evidence        TEXT[] NOT NULL DEFAULT '{}'
);

CREATE TABLE journal_entries (
  id               BIGSERIAL PRIMARY KEY,
  run_id           UUID NOT NULL,
  idempotency_key  TEXT NOT NULL UNIQUE,   -- reposting is a no-op
  entry_date       DATE NOT NULL,
  narration        TEXT NOT NULL,
  lines            JSONB NOT NULL,
  posted_at        TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE exceptions (
  id           BIGSERIAL PRIMARY KEY,
  run_id       UUID NOT NULL,
  reason_code  TEXT NOT NULL,
  ref          TEXT NOT NULL,
  amount_paise BIGINT,
  what         TEXT NOT NULL,
  why          TEXT NOT NULL,
  actions      JSONB NOT NULL,
  status       TEXT NOT NULL DEFAULT 'open'
);

CREATE TABLE audit_events (
  id          BIGSERIAL PRIMARY KEY,
  run_id      UUID NOT NULL,
  at          TIMESTAMPTZ DEFAULT now(),
  actor       TEXT NOT NULL,          -- 'system:L1' | 'llm:v3' | 'user:abhay'
  event       TEXT NOT NULL,
  payload     JSONB NOT NULL
);
