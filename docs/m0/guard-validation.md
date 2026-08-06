# Leak-guard validation record

**Recorded:** 2026-08-06 · **Guard:** `tools/tests/test_no_private_coordinates.py` (item 70)
· **Validated against:** commit `d36b467`, the last state of the tree before the Rev 7
de-coupling

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

Run at `d36b467` with the 18-digest ban list, before any de-coupling:

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
