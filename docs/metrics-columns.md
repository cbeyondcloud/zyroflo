# The audit store's columns — `audit.db`, store version 1

`python tools/sdlc/metrics.py --target . --export sqlite` writes `.sdlc/metrics/audit.db`
(or `<--out>/audit.db`) from every run's `events.jsonl`. It is **derived and disposable**:
gitignored, regenerated on demand, never the record — the committed run folders are. Every
row is keyed by its event's chain `hash` and written `INSERT OR IGNORE`, so exporting the
same runs twice, or merging many repositories' stores into one, never double-counts.

**These names are a contract.** A Grafana panel, a warehouse loader, or somebody's own SQL
depends on them; `tools/tests/test_audit_store.py` holds this page and the store equal by
name, so renaming or dropping a column fails the suite before it breaks a dashboard. The
store's version is in `meta` (`store_version`), and it changes only with a breaking shape
change.

**An unmeasurable value is NULL, never 0.** A step whose runner reported no tokens has
`tokens_source = 'unavailable'`, a reason in `tokens_reason`, and NULL counts. Sum only
what was reported (`WHERE tokens_source IN ('reported','self-reported')`), and show the
rest as "not measurable" rather than as zero.

## `runs` — one row per run

| Column | Meaning | From |
|---|---|---|
| `run_id` | The run's id; the key every other table joins on. | `run_opened` |
| `kind` | `requirement`, `defect`, … | `run_opened` |
| `project` | The repository's name (pre-INIT metadata, T442) — the roll-up key. | `run_opened` |
| `owner` | The team or person that owns the repository. | `run_opened` |
| `criticality` | `low` / `medium` / `high`. | `run_opened` |
| `opened_at` | When the run opened (UTC ISO-8601). | `run_opened.at` |
| `closed_status` | `closed` or `rejected`; NULL while the run is open. | `run_closed` |
| `closed_at` | When it closed; NULL while open. | `run_closed.at` |

## `events` — every event, as recorded

| Column | Meaning | From |
|---|---|---|
| `hash` | The event's chain hash — primary key. | every event |
| `run_id` | The run. | every event |
| `seq` | Position in the run's chain (1-based). | every event |
| `at` | When (UTC ISO-8601, milliseconds). | every event |
| `event` | `run_opened`, `step_started`, `step_finished`, `gate_waiting`, `gate_decided`, `loop_back`, `run_closed`. | every event |
| `step` | The step id (`P0` …). | every event |
| `step_name` | The step's name (`intake` …). | every event |
| `prev` | The previous event's hash — the chain link. | every event |
| `payload` | The type-specific fields, as JSON. | every event |

## `steps` — one row per step a runner or model ran, or a driving tool reported

| Column | Meaning | From |
|---|---|---|
| `hash` | The `step_finished` event's hash — primary key. | `step_finished` |
| `run_id` | The run. | `step_finished` |
| `seq` | Its position in the run's chain. | `step_finished` |
| `at` | When it finished. | `step_finished` |
| `step` | The step id. | `step_finished` |
| `step_name` | The step's name. | `step_finished` |
| `attempt` | The nth time this step ran in this run. | `step_finished` |
| `started_at` | When the spawn started. | `step_finished` |
| `ended_at` | When it ended. | `step_finished` |
| `duration_ms` | Machine time for the step. | `step_finished` |
| `actor` | `runner`, `model-endpoint`, or `driving-tool`. | `step_finished` |
| `runner` | The runner's name (`provider/model` for an endpoint step). | `step_finished` |
| `provider` | The provider, when a model was named. | `step_finished` |
| `model` | The model, when one was named. | `step_finished` |
| `protocol` | `openai-chat` / `anthropic-messages`, for an endpoint step. | `step_finished` |
| `tokens_source` | `reported`, `self-reported`, or `unavailable`. | `step_finished.tokens` |
| `tokens_reason` | Why tokens are unavailable; NULL when reported. | `step_finished.tokens` |
| `tokens_calls` | Model calls the runner reported. | `step_finished.tokens` |
| `tokens_input` | Input tokens; NULL when not reported. | `step_finished.tokens` |
| `tokens_output` | Output tokens; NULL when not reported. | `step_finished.tokens` |
| `tokens_cached` | Cached input tokens; NULL when not reported. | `step_finished.tokens` |
| `tokens_total` | Input + output; NULL when neither was reported. | `step_finished.tokens` |
| `cost_source` | `priced`, or `unpriced` with a reason. | `step_finished.cost` |
| `cost_reason` | Why a cost is unpriced. | `step_finished.cost` |
| `cost_amount` | The estimate, from the prices declared in `providers.toml`; NULL when unpriced. | `step_finished.cost` |
| `cost_currency` | The currency the catalog declared, or `unspecified`. | `step_finished.cost` |
| `exit_code` | The runner's exit code; NULL for a self-report. | `step_finished` |
| `outcome` | How the step ended: `advanced`, `gate`, `owed`, `stale`, `ran-badly`, `refused`, `could-not-spawn`, `unanswered`, `done`, `self-reported`. | `step_finished` |

## `gates` — every halt at a gate and every decision

| Column | Meaning | From |
|---|---|---|
| `hash` | The event's hash — primary key. | `gate_waiting` / `gate_decided` |
| `run_id` | The run. | both |
| `seq` | Position in the chain. | both |
| `at` | When. | both |
| `event` | `gate_waiting` or `gate_decided`. | both |
| `step` | The step the gate stands after. | both |
| `gate` | `G1`–`G5`, or `G-<step>` for an added gate. | both |
| `hard` | Whether the gate halts under this regime; NULL on a decision row. | `gate_waiting` |
| `decision` | `approve` or `reject`. | `gate_decided` |
| `approver` | Who decided (the repository's git identity). | `gate_decided` |
| `ledger_hash` | The `approvals.jsonl` entry this decision is — never a copy of it. | `gate_decided` |
| `waited_ms` | Human time from the halt to the decision; NULL when the run never halted there. | `gate_decided` |
| `forced` | Whether the approval was forced past a refusal. | `gate_decided` |

## `loop_backs` — rework

| Column | Meaning | From |
|---|---|---|
| `hash` | The event's hash — primary key. | `loop_back` |
| `run_id` | The run. | `loop_back` |
| `seq` | Position in the chain. | `loop_back` |
| `at` | When the run moved back. | `loop_back` |
| `from_step` | The step it left. | `loop_back.from` |
| `to_step` | The earlier step it returned to. | `loop_back.to` |

## `artifacts` — what each step left, pinned

| Column | Meaning | From |
|---|---|---|
| `step_hash` | The `step_finished` event that recorded it. | `step_finished.artifacts` |
| `run_id` | The run. | `step_finished` |
| `step` | The step. | `step_finished` |
| `path` | The artifact, relative to the run folder. | `step_finished.artifacts` |
| `digest` | Its pinned digest when the step finished. | `step_finished.artifacts` |

## View: `models`

`provider`, `model`, `steps`, `tokens_input`, `tokens_output`, `steps_reporting`,
`cost_amount` — tokens and cost per provider and model, over the `steps` table.
