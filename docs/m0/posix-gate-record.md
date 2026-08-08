# M0 kill-gate condition (5) — the vendored-binary path, executed on a non-Windows platform

**Date:** 2026-08-07 · **Result: PASS** · **Repo at:** `df162d4`

## What the condition asks, and why it is a gate

The harness vendors external binaries in `third_party/`. Item 74 made the manifest
per-platform and taught the extractor tarballs, per-platform bin directories and `chmod +x` —
and then **none of it ever ran**: the three POSIX permission assertions carry
`@pytest.mark.skipif(os.name == "nt")`, so on the author's machine they printed as skips for a
whole milestone.

Four steps of that path differ from the Windows one — asset selection, `.tar.gz` extraction,
the exec bit, and `_tool()` resolving `bin/linux-x86_64/rg` instead of `bin/rg.exe`. If any
fails, the tool resolves to nothing and a scan returns nothing, which is **indistinguishable
from a genuine absence**. That is this project's named highest-value risk class, so the
condition is not "the code exists" but "the code has run".

## Environment, and why not the obvious one

Run in a disposable `python:3.11-slim` container, **with the repository copied into the
container's own filesystem** rather than read from a bind mount.

> **The obvious route was WSL, and it would have produced a meaningless pass.** The installed
> Ubuntu is 20.04 with Python 3.8 and no uv — below the project's 3.11 floor — and, more
> seriously, `/mnt/d` is DrvFs, which **ignores `chmod` by default**. Exec-bit assertions run
> there would have passed or failed for reasons having nothing to do with the code. Step 0 of
> the harness now proves the filesystem honours the exec bit before anything else runs, so
> this cannot be got wrong silently later.

Docker was chosen over installing a newer toolchain into WSL because it mutates nothing on the
host and is disposable. `python:3.11-slim` was already present locally; only `pytest` and the
pinned release assets were fetched.

## Results

| # | Check | Result |
|---|---|---|
| 0 | the filesystem honours exec bits at all | **ok** — probe script written, `chmod +x`, executed |
| 1 | `current_platform()` | `linux-x86_64` (from `x86_64`, which Windows reports as `AMD64`) |
| 2 | `--verify` before fetch | names each unpinned tool and its milestone; does not claim success |
| 3 | `--fetch` | downloaded, checksummed and extracted `rg` 15.2.0 and `fd` 10.4.2 from `.tar.gz` |
| 4 | `--verify` after fetch | `ok rg 15.2.0`, `ok fd 10.4.2`; M1+ tools reported unpinned, not missing |
| 5 | exec bit + the binaries actually run | both `-rwx`, `rg --version` and `fd --version` both answer |
| 6 | `_tool()` resolves the non-`.exe` path | `/work/third_party/bin/linux-x86_64/{rg,fd}` |
| 7 | fd accelerator vs Python fallback | **26 = 26, byte-identical**, on the path the pipeline uses |
| 8 | the three skipped assertions | **55 tests, zero skipped** — they executed |

Check 8 is the one the condition is really about. A skip prints green, and these had been
printing green since item 74.

## Finding: a relative root yields zero files, silently — and it is NOT a POSIX defect

Check 7 compares both roots deliberately. With an **absolute** root the two backends agree
exactly. With a **relative** root the accelerator returns **0 files where the fallback returns
26**, at exit 0.

`_find_with_fd` invokes fd with `--absolute-path`, then does `p.relative_to(root)` per line.
Against a relative `root` that raises `ValueError` for every line, and the per-line
`except ValueError: continue` swallows each one. The result is an empty list that means "this
tree has no matching files".

**Verified platform-independent**: the same call on Windows produces the identical
`relative → fd=0, python=26` split. This run surfaced it; POSIX did not cause it.

That matters for how it is counted. It is **not** a failure of condition (5) — the path the
pipeline actually uses passes on both platforms — but it is the **third** instance of this
project's cardinal failure, after the multi-pattern fd call that reported a workspace as
having no CI, and `--full-path` matching backslashes on Windows. And the module's own
docstring claims the two backends "agree by construction rather than by coincidence", which
holds only for absolute roots and says so nowhere.

**Not fixed in this run, deliberately.** `repo_walk` is `tools/graph/core/`, and item 19's
gate asserts core is byte-identical since the second fixture landed. Touching it is a
decision, not a cleanup — recorded here so it is made deliberately rather than absorbed.

> **Resolved 2026-08-08, and the process is the part worth keeping.** Fixed in `7ea8755`,
> which **left the gate red on purpose**; the baseline moved in `b43836d`, a separate commit
> carrying the reason. Appended rather than rewritten above, for the same reason the two
> commits are separate: what was true at the time of the run should stay legible.
>
> The fix is two changes and the second is the general one. Paths are compared against the
> **resolved** root, so like meets like — and a line that still cannot be placed under it now
> **abandons the accelerator** rather than skipping that file, because every line fd emits is
> under the search path it was given, so one that is not means an assumption here is wrong.
> That is the rule the `returncode` guard three lines above already followed; it had simply
> never been applied to the second way this function can be wrong.
>
> The gate's own design changed too. Deriving its baseline from `git log` was durable but
> **unmovable**, and core legitimately changes at M2 and M6 — a gate that can never be crossed
> is a cage rather than a tripwire, and gets deleted the first time it is inconvenient. The
> baseline now lives in `tools/graph/tests/core_baseline.md`, one appended line per approved
> change with its reason, so crossing it stays possible and stays visible.

## What this does not establish

One platform, one architecture. `linux-aarch64`, `darwin-x86_64` and `darwin-aarch64` are
pinned and remain unexecuted. Only `rg` and `fd` — the M0 tools — are pinned at all; gitleaks,
ast-grep, osv-scanner, d2 and difft are unpinned on **every** platform including Windows, and
each will need its own first `--pin` when its milestone arrives.

**A hand-run verification decays.** This record is true of `df162d4` and of nothing later.
Item 16 carries the `ubuntu-latest` job that turns it into a standing check, with the
acceptance stated there: a run reporting "N skipped" on Linux fails, because skipping is
exactly how this went unnoticed for a milestone.
