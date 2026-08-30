# M6 · C2 — goose executes `impact-review` E2E from AGENTS.md + CLIs alone

*The demonstration record. Five attempts, three harness fixes, one green — and
every red is on the recurring smoke's own ledger beside it.*

## The condition, verbatim

> goose executes `impact-review` E2E from `AGENTS.md` + CLIs alone

From the pre-registered M6 kill gate. The judge is `provider_smoke.py`, the
recurring gate item 29 built: it hands the runner one provider-neutral prompt,
then verifies ARTIFACTS — a prepass.json, a pack.md, and a completed review
session — never the runner's own claim of success.

## The setup

goose **v1.48.0**, native Windows build, installed from the block/goose
release; provider `openai`, model `gpt-4o`, the key in the harness `.env` and
nowhere else. Target: a scratch repository with the harness freshly installed
(`sdlc-init.py --tools auto`), a built graph, and a 20-line real diff. The
smoke's harness path was proven FIRST, free, with the reference runner —
contract tier green — so every subsequent red could only be about goose.

## The arc: five attempts, and what each one taught

| # | Outcome | The reason, measured from goose's own transcript |
|---|---|---|
| 1 | red | Read AGENTS.md, **never opened the workflow document it points at**; improvised from the block's one example command, invented an `--output` flag and an `hitl record` spelling, exited clean in 15s |
| 2 | red | Prompt amended (*open and FOLLOW the document*) — it now reads the SOP, then runs `tools/review/hitl.py` **relative to the repository**, where the file is not: the SOP taught the harness prefix with a graph example and step 1 spelled a bare path |
| 3 | red | SOP amended (every step path lives under `<harness>`) — this attempt died EARLIER, at the same cmd.exe `type`-with-forward-slashes stumble attempt 2 had recovered from. Variance, not a new defect |
| 4 | red | Runner command gains a `--system` shell hint — **the whole workflow now executes**: prepass ✅ pack ✅ begin ✅ end approved ✅. But the SOP's literal `--target <repo>` beat the prompt's `hitl-target`, so the row landed in the repository's own metrics — which `verify()` rightly refuses to read, because item 27's median rule is WHY smoke reviews are redirected to scratch |
| 5 | **green** | Prompt states the substitution (*where the workflow writes `<repo>` in a hitl.py command, use hitl-target*) — full E2E, all artifacts verified, session recorded in scratch |

**Gate question answered from the record, not from this prose:**

```
provider_smoke.py --target <repo> --status --milestone M6-C2 --require agent
OK - M6-C2: green at the agent runner tier, and agent was required   (exit 0)
```

## The three harness fixes, and the class they share

All three are **item 33's recorded class — a requirement the runner is never
told** — found live, each one layer deeper than the last:

1. **"Execute the workflow it names" can be satisfied in an agent's head.**
   The prompt now says open and FOLLOW the document, step by step, and do not
   improvise commands it does not state. Pinned as the sentence.
2. **The SOP implied, never stated, that step paths live under `<harness>`.**
   The preamble's prefix example named `tools/graph/`; step 1 then spelled a
   bare `tools/review/hitl.py`, and the literal reading runs it in the repo.
   One paragraph in `workflows/impact-review.md` (edited on the owner's
   explicit ask — that file is theirs) states the rule with hitl as the worked
   example. Pinned in the currency guard.
3. **`hitl-target:` never said WHICH flag it replaces.** The reference runner
   knows because it is coded; a model must guess, and under fix 1's
   follow-the-document pressure it correctly chose the SOP's literal
   `<repo>`. The prompt now states the substitution. Pinned.

The reference runner was re-run free after every fix and stayed green — the
fixes tightened instructions without moving the mechanism.

**The Windows shell hint lives in the RUNNER DECLARATION, not the harness.**
goose's shell on this machine is cmd.exe and gpt-4o writes POSIX-flavoured
commands at it; the hint rides `goose run --system "..."` inside the runner
command — which is exactly where item 105's architecture says machine truth
belongs. The harness prompt stays provider-neutral and names no shell.

## What is claimed, and what is not

**Claimed:** a non-Claude agent (goose + gpt-4o), discovering everything from
`AGENTS.md` and the CLIs alone, executed the impact-review workflow end to end
— artifacts verified by the gate, the review session recorded where the smoke
requires, the instance on the recurring record at the `agent` tier.

**Not claimed:** that it passes every time (attempt 3 versus attempt 2 is the
variance measured); that the review's CONTENT was deep (0 findings on a
20-line scratch diff — C2 tests the lifecycle's executability by a second
agent, not review quality, which is items 25–28's subject); or that any of
this transfers below gpt-4o's capability line (C1's record carries that
finding).

**Cost, measured:** five goose sessions, ≈ 25,000 tokens total, ≈ $0.15.
