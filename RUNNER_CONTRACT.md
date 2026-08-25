# Runner contract

A **runner** is the thing that executes a phase: it reads the SOP, does the
work, drives the harness CLIs, and leaves artifacts behind. Claude Code was the
first. Any other agent — `codex`, `goose`, `aider`, something not written yet —
participates by satisfying this document, and nothing else.

The contract exists because provider-agnosticism is not a claim you can make
once. It is a property that decays silently: a prompt learns a Claude-shaped
habit, an SOP names a tool only one provider has, and nobody notices until swap
day. So the contract is small, it is mechanical, and it is **checked by running
it** — `tools/provider_smoke.py --runner "<command>"` is the conformance test,
and it must be green at every milestone M2–M6 (item 29).

**All gate enforcement lives in the Python CLIs.** A runner cannot skip a human
gate, because advancing a phase requires a matching entry in the approval
ledger and the CLIs refuse without one. That is what makes it safe to run an
agent at full autonomy: the harness does not trust the runner, it checks the
artifacts.

---

## 1 · How a runner is invoked

```
<runner command> [<the runner's own flags> ...] "<the task prompt>"
```

* **The prompt is the FINAL argument.** Everything before it belongs to the
  runner. A runner that needs flags declares them in its own command string.
* **The working directory is the target repository root.** `AGENTS.md` is
  there; so is `.sdlc/`.
* **The environment is the shell's, plus the harness `.env`, with the shell
  winning.** Put the provider's API key in `<harness>/.env`. Do **not** put a
  credential in the runner command: the harness masks secret-named flags in its
  record, but a value it never saw in the environment cannot be scrubbed out of
  the runner's own output.
* **No state outside files.** A runner keeps nothing between invocations. Every
  input is in the repository or the prompt; every output is a file.

## 2 · What the prompt states, exactly

The task prompt is provider-neutral prose followed by a block of exact values.
Each value **runs to the end of its line**, so a path may contain spaces:

```
phase: <the phase being executed, on the lifecycle's numbering>
run: <run id>
target: <the repository root>
sop: <the procedure to follow>
artifacts-out: <the run folder every artifact is written to>
component: <alias>
diff: <path to the unified diff under review>
prepass-out: <directory the pre-pass must write to>
pack-out: <directory the context pack must write to>
hitl-target: <directory the review session must be recorded in>
rubric: <the scoring instrument; reported, never the gate>
block-on: <the severities that hold the review loop open>
max-iterations: <the hard cap on the loop>
```

These are not suggestions. The verification reads *those* paths and no others.
A runner that writes a perfectly good pack somewhere else has failed, and the
harness will say the pack is missing — because to the record, it is.

**The block is per-phase and it is generated, not written by hand.** The first
five lines above are common to every phase; the rest are what P6 in particular
needs. Each phase declares its own set in `tools/sdlc/stage.py`, and the prompt
comes out of it:

```
stage.py --target <repo> --run <id> --phase P6 --prompt --set diff=<file> --set component=<alias> ...
```

A value the phase names and nobody supplied is a **refusal**, not a blank line.
That rule was bought with the two defects in §9: a runner cannot meet a
requirement nobody states, and a prompt that omits one produces a perfectly
compliant runner that cannot finish.

## 3 · What a runner discovers for itself

Everything else comes from the repository, which is the point of the exercise:

| From | What |
|---|---|
| `AGENTS.md` at the root | the workflow to execute, and the path to its SOP |
| `AGENTS.md`'s CLI-tier line | the harness location, as `uv run --project "<harness>" python "<harness>/tools/..."` — quoted, because a clone can live under a path with a space |
| the SOP it names | the steps, their order, and each step's flags |
| `.sdlc/graph/` | the graph the review reasons over |

If any of that is missing or wrong, the discovery chain is broken and
`provider_smoke.py` reports it as a breadcrumb failure before a runner is even
started. That check is welded in CI to what the installer actually writes.

## 4 · What a runner must do

**Follow the SOP the prompt names, and leave behind what the phase owes.** Each
phase's SOP is under `sdlc/stages/`, one file per phase, and it is the
procedure; the prompt is only the values it needs. What the phase owes is
listed in the prompt and checked mechanically:

```
stage.py --target <repo> --run <id> --phase <P> --verify
```

Exit 0 is delivered, 1 is owed — and "owed" includes an artifact that exists
but was never recorded with `state.py --record`, because an unrecorded artifact
is not pinned and no gate can stand on it.

For `impact-review`, which is P6's mechanical spine:

1. **Open the review session** — `tools/review/hitl.py --target <hitl-target>
   --begin`. The clock starts here and the M4 gate reads it.
2. **Run the deterministic pre-pass** — `tools/review/prepass.py --target
   <repo> --diff <diff> --out <prepass-out>`.
3. **Build the context pack** — `tools/graph/pack.py --graph-dir .sdlc/graph
   --diff <diff> --component <component> --out <pack-out> --no-svg`.
4. **Do the actual review**, using the pack, and record each finding through
   `hitl.py --finding ... --source ... --disposition ...`.
5. **Close the session** — `--end <verdict>`, or `--abandon` if the run did not
   finish. Either way it closes: an open session blocks the next run.

## 5 · Exit codes, and what they are not

**A runner's exit code is not the verdict.** The harness judges artifacts,
never the runner's own claim of success, because a runner that exits 0 having
done nothing is precisely the failure this gate exists to catch. Report your
exit code honestly anyway — a runner that lies about its own outcome is one
nobody can debug.

Codes the runner must interpret correctly when it calls the CLIs:

| CLI | Code | Means |
|---|---|---|
| `prepass.py` | 0 | every gate ran, no findings |
| `prepass.py` | 1 | every gate ran, findings — **not a failure** |
| `prepass.py` | 3 | a gate could not run — outranks findings; the change is not reviewed |
| `pack.py`, `hitl.py` | non-zero | a failure, always |

## 6 · What a runner must never do

* Leave the review session open.
* Write outside the stated output paths.
* Invent findings. A fabricated finding puts a synthetic acceptance row into
  the statistics the M4 gate is measured on.
* Depend on MCP, or on provider-specific skill or rules files. The CLI tier is
  the only tier every provider has; MCP is an accelerator, never a requirement.
* Carry a credential in its own command line.
* Assume it can skip a human gate. It cannot; the CLIs refuse.

## 7 · Conformance

```bash
uv run python tools/provider_smoke.py --target <repo> --diff <patch> --component <alias> --runner "<your command>" --milestone <label> --agent
```

Green means: the discovery chain is walkable, the session was opened and
closed, every pre-pass gate fired, the pack names every changed file, and every
artifact is from *this* run. The result appends one row to
`<repo>/.sdlc/metrics/provider-smoke.jsonl`, **naming the runner** — so a green
row can never be misread as a green from a different one.

`--milestone` says which milestone the instance is evidence *for*, and
`--agent` is your attestation that the command has a **model in it**. The smoke
cannot tell: your adapter and the reference runner are both an argv it spawns,
so a `--runner` on its own records the `contract` tier — the weakest honest
claim — and only this flag raises it to `agent`. Omitting `--milestone` records
the instance unattributed, which satisfies nothing and says so on the way out.

Read the record back — this is the question the recurring gate actually asks,
and until it existed the file was written and never read:

```bash
uv run python tools/provider_smoke.py --target <repo> --status --milestone <label> --require agent
```

Exit 1 means that milestone has no green instance at that tier or stronger.
Without `--require` it asks about any tier; without `--milestone` it is a
report and asks nothing.

## 8 · Known adapters

| Adapter | Command | Exercised here |
|---|---|---|
| **null provider** | built in (`--runner` omitted) | yes — proves CLI-tier closure with zero Claude surfaces; not an agent |
| **reference runner** | `python <harness>/tools/sdlc/reference_runner.py` | yes — **green**; proves this contract is executable by an outside process; has no model in it, so it proves nothing about agents |
| **codex** | `codex exec` (AGENTS.md-native, headless) | **no** — not installed on this machine and no LLM credential is present; the run is a machine act, not a code change |
| **aider** | `aider --message` | no |
| **goose** | `goose run` | no — scheduled at M4 (item 55) |

The honesty split matters and is repeated in the record: **a green from the
null provider or the reference runner is a green CONTRACT, never a green
agent.** Neither decides anything. What a live agent adds is proof that the
steps can be *discovered* rather than merely executed, and that is what the
codex row is for.

That split used to live only in this paragraph. It is now a **field** — `tier`,
one of `null` < `contract` < `agent` — so `--status --require agent` can refuse
a milestone whose only evidence is a contract green. Prose two documents away
from the record could not do that, and the reference runner is the case that
proves it: it is a `--runner`, so anything inferring the tier from the presence
of one would have filed a runner with no model in it as agent evidence.

## 9 · Two traps, found by walking this path

Both were found by making the reference runner actually run, and both were
defects in the harness rather than in the runner. They are recorded here
because the next adapter author would otherwise meet them the same way:

* **Values embedded in prose get parsed with their punctuation.** The first
  prompt said "…with `--target <dir>`, then close the session", and the first
  thing a mechanical runner did was create a directory whose name ended in a
  comma. Hence the exact-value block in §2, one value per line.
* **A requirement nobody states cannot be met.** `pack.py` refuses `--diff`
  without `--component`, and the prompt did not say which component — so a
  fully compliant runner could not finish. The alias is now stated. If you
  find another of these, it is a bug in the prompt, not in your adapter.
