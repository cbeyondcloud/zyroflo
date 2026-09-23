# Loading the exports into a warehouse

The harness writes the audit trail out as **files**. Your organisation's own job loads
them. This page is the recipe for that job: a worked Postgres example, a generic NDJSON
mapping for any other warehouse, and a zero-infrastructure DuckDB check.

The column names are a contract, documented once in
[`docs/metrics-columns.md`](metrics-columns.md). This page does not restate what each
column means. It shows how to land them. `tools/tests/test_warehouse_doc.py` holds every
table below equal to `metrics.export_columns(<event type>)` by name, so a renamed column
fails the suite before it can break your loader.

## The principle: files to a path, your job loads them

- **The harness holds no connection string.** No DSN, host, user or password is read,
  stored or needed by any harness tool. It writes files; nothing in it connects to a
  warehouse.
- **Your loader job owns the credentials**, in its own secret store (a CI secret, a vault,
  a scheduler's connection). Never in the repository, and never in the harness's `.env`.
  A `.env`-style shape for the job, with placeholders only:

  ```sh
  # the LOADER job's environment, filled from its own secret store
  PGHOST=warehouse.example.invalid
  PGDATABASE=sdlc_audit
  PGUSER=sdlc_loader
  PGPASSWORD=__from_the_loader_jobs_secret_store__
  ```

- **The files are derived and disposable.** The record is each run's committed
  `events.jsonl`. An export can always be regenerated from it, so a failed load is re-run,
  never repaired by hand.

## What the harness writes

```sh
python tools/sdlc/metrics.py --target <repo> --export csv    [--out DIR]
python tools/sdlc/metrics.py --target <repo> --export ndjson [--out DIR]
python tools/sdlc/metrics.py --target <repo> --export sqlite [--out DIR]
```

- **CSV**: one set of rows per event type (`run_opened`, `step_started`, `step_finished`,
  `gate_waiting`, `gate_decided`, `loop_back`, `run_closed`). The header is exactly
  `metrics.export_columns(<type>)`: the common fields, then the type's own, with the nested
  `tokens` and `cost` objects expanded to fixed `tokens_*` / `cost_*` sub-columns and
  `artifacts` written as a JSON string. **An unmeasured value is an empty cell, never
  `0`.** Booleans are written `True` / `False`.
- **NDJSON**: the same events, one JSON object per line, with `tokens`, `cost` and
  `artifacts` still nested. See [the NDJSON mapping](#generic-ndjson-mapping).
- **SQLite**: `audit.db`, with tables `runs`, `events`, `steps`, `gates`, `loop_backs`,
  `artifacts` and the view `models`, every row keyed by its event's chain `hash` and written
  `INSERT OR IGNORE`. Its columns are the ones in
  [`docs/metrics-columns.md`](metrics-columns.md). Use it when your ETL reads SQLite more
  easily than CSV. The keys and the idempotency rule below are the same.

**Where the files land (backlog row T454).** An export writes
`<out>/<repo>/<event_type>/<YYYY-MM-DD>/<run-id>.csv` (or `.ndjson`) - the date is the day
the run opened, and `<repo>` is the repository's pre-INIT project name - plus one
`<out>/<repo>/manifest.json` listing the format, each run's event count and chain head, the
rows per event type and every file written. `<out>` is, first match wins: `--out`, the
machine's `SDLC_METRICS_OUT`, the repository's `policy.yml: metrics_out` (repo-relative
only), then `.sdlc/metrics/export/`. The recipe below assumes only this much: **every
`*.csv` (or `*.ndjson`) file under `<out>/<repo>/<event_type>/` holds that event type's rows
with the same header**. Check the manifest's `rows` against what you loaded.

## Worked example: Postgres

### Tables

One table per event type, named `sdlc_<event type>`, with the chain `hash` as primary key.
The column list and order match the CSV header exactly, so `COPY` needs no column list.
`from` and `to` are reserved words in SQL and have to be quoted.

```sql
CREATE TABLE IF NOT EXISTS sdlc_run_opened (
    schema_version INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    at TIMESTAMPTZ NOT NULL,
    event TEXT NOT NULL,
    step TEXT,
    step_name TEXT,
    prev TEXT,
    hash TEXT PRIMARY KEY,
    kind TEXT,
    project TEXT,
    owner TEXT,
    criticality TEXT
);

CREATE TABLE IF NOT EXISTS sdlc_step_started (
    schema_version INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    at TIMESTAMPTZ NOT NULL,
    event TEXT NOT NULL,
    step TEXT,
    step_name TEXT,
    prev TEXT,
    hash TEXT PRIMARY KEY,
    attempt INTEGER,
    actor TEXT
);

CREATE TABLE IF NOT EXISTS sdlc_step_finished (
    schema_version INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    at TIMESTAMPTZ NOT NULL,
    event TEXT NOT NULL,
    step TEXT,
    step_name TEXT,
    prev TEXT,
    hash TEXT PRIMARY KEY,
    attempt INTEGER,
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    duration_ms BIGINT,
    actor TEXT,
    runner TEXT,
    provider TEXT,
    model TEXT,
    protocol TEXT,
    tokens_source TEXT,
    tokens_reason TEXT,
    tokens_calls INTEGER,
    tokens_input BIGINT,
    tokens_output BIGINT,
    tokens_cached BIGINT,
    tokens_total BIGINT,
    cost_source TEXT,
    cost_reason TEXT,
    cost_amount NUMERIC,
    cost_currency TEXT,
    exit_code INTEGER,
    outcome TEXT,
    artifacts JSONB,
    forced BOOLEAN
);

CREATE TABLE IF NOT EXISTS sdlc_gate_waiting (
    schema_version INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    at TIMESTAMPTZ NOT NULL,
    event TEXT NOT NULL,
    step TEXT,
    step_name TEXT,
    prev TEXT,
    hash TEXT PRIMARY KEY,
    gate TEXT,
    hard BOOLEAN
);

CREATE TABLE IF NOT EXISTS sdlc_gate_decided (
    schema_version INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    at TIMESTAMPTZ NOT NULL,
    event TEXT NOT NULL,
    step TEXT,
    step_name TEXT,
    prev TEXT,
    hash TEXT PRIMARY KEY,
    gate TEXT,
    decision TEXT,
    approver TEXT,
    ledger_hash TEXT,
    waited_ms BIGINT,
    forced BOOLEAN
);

CREATE TABLE IF NOT EXISTS sdlc_loop_back (
    schema_version INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    at TIMESTAMPTZ NOT NULL,
    event TEXT NOT NULL,
    step TEXT,
    step_name TEXT,
    prev TEXT,
    hash TEXT PRIMARY KEY,
    "from" TEXT,
    "to" TEXT
);

CREATE TABLE IF NOT EXISTS sdlc_run_closed (
    schema_version INTEGER NOT NULL,
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    at TIMESTAMPTZ NOT NULL,
    event TEXT NOT NULL,
    step TEXT,
    step_name TEXT,
    prev TEXT,
    hash TEXT PRIMARY KEY,
    status TEXT
);
```

Things the types rely on, all visible in a real export:

- **Empty cells load as NULL.** `COPY ... (FORMAT csv)` reads an unquoted empty field as
  NULL, and the export writes every missing or unmeasured value that way. An unmeasured
  `tokens_input` arrives as NULL, never `0`. `prev` is empty (NULL) on a run's first event.
- `at`, `started_at` and `ended_at` are UTC ISO-8601 with milliseconds and an explicit
  offset, e.g. `2026-09-23T14:01:22.193+00:00`, which `TIMESTAMPTZ` accepts as-is.
- Booleans are written `True` / `False`. Postgres's boolean input ignores case.
- `artifacts` is a JSON object (artifact path to digest) with sorted keys, and `{}` when a
  step pinned none.

### Loading, idempotently

Load each event type through a staging table, then insert with
`ON CONFLICT (hash) DO NOTHING`. Loading the same files twice, or two overlapping exports,
or many repositories' exports into one table, then never double-counts.

```sql
-- once, next to the tables: a key-less staging table with the same columns
CREATE UNLOGGED TABLE IF NOT EXISTS stage_step_finished (LIKE sdlc_step_finished);
```

```sh
# the loader job: every step_finished file of one repository's export, in one transaction.
# psql takes PGHOST / PGUSER / PGPASSWORD from the job's environment; this script sets none of them.
EXPORT=path/to/export       # what metrics.py --export csv --out wrote
REPO=billing                # the <repo> directory T454 writes under it
{
  echo "TRUNCATE stage_step_finished;"
  for f in "$EXPORT/$REPO"/step_finished/*/*.csv; do
    echo "\\copy stage_step_finished FROM '$f' WITH (FORMAT csv, HEADER true)"
  done
  echo "INSERT INTO sdlc_step_finished SELECT * FROM stage_step_finished ON CONFLICT (hash) DO NOTHING;"
} | psql -v ON_ERROR_STOP=1 --single-transaction -f -
```

Repeat for the other event types, with `stage_<type>` and `sdlc_<type>`. Notes:

- `\copy` is psql's client-side form: it reads the file on the loader's machine. A plain
  `COPY sdlc_... FROM '/path'` reads a file on the database **server** and needs a
  server-side role. Both take the same `WITH (FORMAT csv, HEADER true)` options.
- `HEADER true` skips the header line without reading it. On Postgres 15 and later,
  `HEADER MATCH` also checks that the header names match the table's columns, so a column
  renamed upstream fails the load instead of shifting values into the wrong columns.
- If the glob matches no file, the shell passes the pattern through unexpanded, `\copy`
  fails on it, and `ON_ERROR_STOP` aborts the transaction. A missing export stops the load
  loudly instead of loading as "no events".
- `SELECT *` relies on the staging table having the same column order as the target.
  `LIKE` guarantees that.
- `DO NOTHING` is correct, not a shortcut. An event is immutable once written, since
  changing it would change its hash. The same hash therefore always means the same row, and
  there is nothing to update.

## Generic NDJSON mapping

For BigQuery, Snowflake, DuckDB, Spark or anything else that loads newline-delimited JSON,
load every `*.ndjson` under `<out>/<repo>/<event_type>/` into a table for that event type.
Each line is one event. The nested objects map to the flat CSV columns like this:

| NDJSON field | Flat column(s) | Notes |
|---|---|---|
| common fields (`schema_version` … `hash`) | same names | on every event |
| `tokens.source`, `.reason`, `.calls`, `.input`, `.output`, `.cached`, `.total` | `tokens_source` … `tokens_total` | `step_finished` only |
| `cost.source`, `.reason`, `.amount`, `.currency` | `cost_source` … `cost_currency` | `step_finished` only |
| `artifacts` (object: path → digest) | `artifacts` (JSON string) | keep as JSON/VARIANT, or explode into one row per path (the SQLite store's `artifacts` table does exactly that) |
| `from`, `to` | `from`, `to` | `loop_back`; quote them, or rename them `from_step` / `to_step` as the SQLite store does |
| any other field | same name | |

Two rules make the mapping lossless:

- **An absent key is NULL.** NDJSON omits what was not recorded. For example, an
  `unavailable` `tokens` object carries only `source` and `reason`, and `forced` appears
  only when it was recorded. Map a missing key to NULL, never to `0` or `false`.
- **Declare the schema rather than inferring it from a sample.** A sample of steps that
  all reported no tokens infers no `tokens.input` column at all. Take the column list from
  `metrics.export_columns(<type>)`, which is the header of the CSV export, and the types
  from the Postgres tables above, mapped to your warehouse's equivalents. For example,
  `TIMESTAMPTZ` becomes `TIMESTAMP` in BigQuery and `TIMESTAMP_TZ` in Snowflake, and
  `JSONB` becomes `JSON` / `VARIANT`.

The full field list per type is in `tools/sdlc/events.py` (`FIELDS`, `FIELD_TYPES`) and the
JSON Schema `sdlc_templates/schemas/events.schema.json`.

## Idempotency and incrementality

- **The chain `hash` is the natural key** of every exported row. It is a SHA-256 over the
  whole event, including its `run_id` and the previous event's hash, so it is unique across
  runs and across repositories. Key on it everywhere, and make every load an upsert that
  ignores a hash it already has. `audit.db` does the same with `INSERT OR IGNORE`.
- **Incremental loads.** `--since YYYY-MM-DD` exports only runs touched on or after that
  day, so a nightly job moves only what is new. A run that grew since its last export
  changes its file, and the export refuses to overwrite it unless you pass `--force` - the
  key above is what makes re-loading that file safe. An unchanged file is left alone.
- **An org roll-up without a warehouse.** `metrics.py --merge <audit.db>... --into
  <org.db>` folds many repositories' SQLite stores into one with a `repo` column (T452),
  and `sdlc_templates/collector/nightly-sdlc-metrics.yml` is a sample nightly job doing
  exactly that. Set `identity: hash` in a repository's `policy.yml` to have every export
  name approvers by a stable pseudonym instead of their email.
- To roll many repositories up, load each repository's export into the same tables. The
  hash keeps rows distinct, and `sdlc_run_opened.project` / `owner` (the pre-INIT project
  metadata) name the repository a run belongs to. Join other tables to it on `run_id`.

## A zero-infrastructure check with DuckDB

Before standing up a warehouse, DuckDB can query the export in place, with no server and
no credentials:

```sql
-- tokens per step, reported vs not measurable; run from the directory --out wrote
SELECT step_name,
       count(*) AS steps,
       sum(tokens_input) FILTER (WHERE tokens_source IN ('reported', 'self-reported')) AS tokens_input,
       count(*) FILTER (WHERE tokens_source = 'unavailable') AS steps_not_measurable
FROM read_csv('**/*.csv', header = true, union_by_name = true,
              types = {'tokens_input': 'BIGINT'})
WHERE event = 'step_finished'
GROUP BY step_name
ORDER BY step_name;
```

`union_by_name` lets one glob read every event type's files together, and the
`WHERE event = ...` filter picks one type out, so it works whatever the file layout is.
`types` pins `tokens_input` as a number, because a sample in which no step reported tokens
would otherwise infer text. Report the `steps_not_measurable` count next to the sum rather than folding it into it:
an unmeasured step is not a zero.
