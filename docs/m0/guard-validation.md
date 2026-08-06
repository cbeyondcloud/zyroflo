# Leak-guard validation record

**Recorded:** 2026-08-06 · **Guard:** `tools/tests/test_no_private_coordinates.py` (item 70)
· **Validated against:** the state of the tree at `d36b467`, before the Rev 7 de-coupling

> **That SHA no longer exists.** The validation ran *before* item 73's history rewrite, which
> re-hashed every commit — `d36b467` is now `c103c5c`. The old identifier is kept because it
> is what the run actually cited, and silently swapping it would misrepresent when the
> measurement happened. The pre-rewrite objects are preserved in the mirror described at the
> bottom of this file, which is where the numbers below can be re-derived.

---

## Why this file exists

The strongest available evidence that a guard *catches* rather than merely *asserts* is
running it against the exact content it was built to find. That content was the pre-Rev-7
tree, and `test_the_guard_fires_on_the_pre_cleanup_blobs` did precisely that — it replayed
real file contents out of git history and required the scanner to fire on each.

**That test could not survive item 73.** The history rewrite removes those objects, after
which the test would `pytest.skip` — and a skipping test still prints green, which is worse
than no test because it looks like coverage. So the result is recorded here, dated, and the
history-dependent test is removed rather than left as a shell.

What remains permanent is `tools/tests/test_scan.py`: ~65 cases proving the matcher's
behaviour against synthetic inputs, including every false-positive class that made a naive
regex unusable. Those do not depend on history and are the guard's ongoing regression suite.
This file covers the one thing they cannot: that the guard was ever pointed at reality.

## Result

Run at `d36b467` (pre-rewrite identity; now `c103c5c`) with the 18-digest ban list, before any
de-coupling:

| File | machine paths | workspace coordinates | distinct lines |
|---|---:|---:|---:|
| `.env.example` | 1 | 2 | 1 |
| `README.md` | 1 | 1 | 1 |
| `tools/graph/estate_check.py` | 2 | 4 | 3 |
| `tools/graph/tests/test_core_genericity.py` | 0 | 5 | 2 |
| `tools/graph/core/lib/ids.py` | 0 | 6 | 1 |
| `docs/sdlc-graph-design.md` | 1 | 5 | 2 |
| `docs/m0/estate_check.md` | 0 | 35 | 17 |
| `docs/m0/estate_check.json` | 53 | 136 | 105 |
| **Total** | **58** | **194** | — |

**The guard fired on every file it was pointed at.** No sampled pre-cleanup file came back
clean.

A second, independent check was run at the same time and is reproducible today: with the
item-71 source changes stashed, the whole-tree guard produced **21 test failures**; with them
applied, **zero**. That is the contrast the item-71 commit exists to demonstrate, and it does
not depend on rewritten history.

## What this does NOT establish

- **Not completeness.** These are eight files, chosen because the audit had already flagged
  them. The guard's structural blind spots are listed in its own module docstring — a
  coordinate that is also an English word, an identifying fact carrying no name at all
  (several were found and fixed *by hand*, and the guard found none of them), and meaning.
- **Not that the tree is now clean forever.** It establishes that the detector works. Staying
  clean is what the whole-tree test does on every run.
- **Not retroactive de-identification beyond this repo.** Item 73 rewrites this repo's
  history; clones taken before it keep what they have. That residual is accepted explicitly
  in the de-scope register rather than assumed away.

## Where the pre-rewrite objects are

A full `--mirror` clone named `sdlc-harness-backup-prerewrite.git` was taken immediately
before the rewrite. It is local to the author's machine, never pushed, and its location is
deliberately not written down here — the leak guard rejected this paragraph when it did,
which is the rule doing its job on the file that documents the rule. It is the only place the
numbers above can be re-derived, and the only copy of the project's history as it was
actually committed.

Two properties of the rewrite worth recording, because a reader of the redacted history will
otherwise be misled:

1. **Every commit was re-hashed.** Old SHAs in commit messages and documents refer to
   identities that no longer resolve. The mapping is in `.git/filter-repo/commit-map`.
2. **Historical file *content* was redacted, not just files removed.** The consequence is
   honest but real — **old commits now show text that was never committed**. Redaction and
   audit fidelity are in genuine tension here, and this resolves it toward redaction,
   deliberately.

### It took four passes, and the reason is the transferable part

Each pass looked complete and was not. Recording the sequence because the mistake generalises
well beyond this repo:

| Pass | Did | Why it was insufficient |
|---|---|---|
| 1 | Removed `docs/m0/estate_check.{json,md}` by path | `.env.example` and `estate_check.py` *continued to exist* across those commits and still carried a machine path and an organisation-named env var **inside** them. Path removal deletes whole files; it cannot touch content in files that legitimately survived. |
| 2 | Replaced machine-path literals throughout history | A **differently-named earlier copy** of the same report — `estate-check.json`, hyphenated, from before the filename was standardised — was never in the path list. Found only by cloning the pushed remote and grepping it. |
| 3 | Removed both filename conventions by glob | Coordinates still sat in historical versions of test fixtures and docstrings, which pass 2's narrow literal list did not cover. |
| 4 | Redacted every coordinate the ban list names | Clean. |

**"I removed the file that leaked" answers a narrower question than "is the thing that leaked
still reachable."** Only the second one is worth asking, and answering it requires an
enumeration rather than a memory — of every path ever committed, and of every blob, not just
the ones you remember writing. Pass 2's miss was found by an *independent clone of the pushed
remote*, not by inspecting the local repo, which is the only check that tests what other
people can actually see.

### Acceptance

The redaction's stopping condition is not judgement. `tools/tests/test_history_is_clean.py`
runs the guard's own scanner over **every blob in every commit** and requires zero findings —
history is held to exactly the standard the guard enforces going forward, no stricter and no
looser. It passed at pass 4 after failing at passes 1–3, and it runs on every suite run, so a
future rewrite that reintroduces something fails loudly.
