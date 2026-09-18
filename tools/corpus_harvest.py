"""Corpus harvest v1: a workspace's own merge history, turned into held-out eval cases.

Item 17. The learning loop (M5) and the impact A/B (item 18) need ground truth nobody
wrote for the occasion — commits where reality already recorded what a change touched.
This tool mines each component's git history for three case classes:

* **scoping** — a commit spreading across ≥2 files in ≥2 directories: the impact
  question ("what does this change touch?") has a non-trivial recorded answer;
* **review** — a merge commit with changes: the material a review pass would have read;
* **defect** — a non-merge commit whose message says it fixes something and that
  touches at least one non-doc file: symptom in the title, cure in the diff.

A commit may qualify for several classes and is written ONCE, carrying all of them.

## The cases are the adopter's data, and the tool is built around that line

Harvested cases quote an adopter's commit titles, paths and hashes — so they live in
the repository they came from (`<repo>/.sdlc/corpus/<sha>.json`), travelling with its
clones, and never in this repository (Rev 12; item 73 is what the alternative costs).
The default mode is `--probe`: read-only, counts per class per component against the
M5 floors, nothing written into anyone's tree. `--write` is the adopter's own act.

## Mechanical selection, recorded bounds

Selection is a rule, not a taste: the classifier reads `git log` shapes only, the
history window is a recorded bound (`--limit`, newest first), and `synthetic: false`
is stamped on every harvested case so later invented ones can never pass as history.
A component that is not a git repository, or a machine without git, degrades to a
per-component row in the report — stated, never skipped silently.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import cli  # noqa: E402

cli.use_utf8()

SCHEMA_VERSION = 1

#: M5's floors, here as CONTEXT for the probe report — v1 reports against them and
#: enforces nothing; item 51 is where they become gates.
FLOORS = {"scoping": 30, "review": 15, "defect": 10}

#: Paths the harness writes into an adopter's repository and OWNS OUTRIGHT.
#:
#: A commit touching only these is the harness's bookkeeping ABOUT the codebase,
#: never a change TO it - so nothing can rank it and scoring it measures nothing.
#: Two shapes, because they need two matches. DIRECTORIES the harness owns
#: outright, by prefix; and the individual SURFACES it writes a block into,
#: exactly - `CLAUDE.md.bak` is not `CLAUDE.md`.
#:
#: The merged surfaces are the arguable half and were MEASURED rather than
#: assumed. The worry is real: an adopter maintains `CLAUDE.md` and `AGENTS.md`
#: themselves, so excluding them could drop a case somebody's own commit earned.
#: On a real 132-case corpus it affects exactly ONE case - the harness install -
#: and no other case loses a single file. And the case it drops is one no impact
#: engine could score anyway: a commit touching only agent instructions and tool
#: manifests has no truth a graph of source code can rank, so it contributes a
#: guaranteed zero rather than a signal.
HARNESS_DIRS = (".sdlc/", ".github/workflows/sdlc-", ".claude/skills/sdlc")
HARNESS_FILES = (".mcp.json", "AGENTS.md", "CLAUDE.md",
                 ".github/copilot-instructions.md")


def own_artifact(path: str) -> bool:
    """Whether one changed path is the harness's own bookkeeping.

    `lstrip` is NOT used to drop a leading `./`: it takes a character SET, so
    `lstrip("./")` eats the leading dot of `.sdlc` and the prefix never matches
    - measured once already, in the worktree filter item 96 built, where it
    would have silently passed everything the filter existed to catch.
    """
    clean = str(path).replace("\\", "/")
    if clean.startswith("./"):
        clean = clean[2:]
    return (clean in HARNESS_FILES
            or any(clean.startswith(d) for d in HARNESS_DIRS))


def subject_files(files: list[str]) -> list[str]:
    """The files a case is ABOUT: its own, minus the harness's bookkeeping.

    Mixed commits keep their code and lose the artifacts, rather than being
    dropped whole: a change that touched real code AND recorded a run is still
    a real change, and its truth set is the code half.
    """
    return [f for f in files if not own_artifact(f)]

_FIX = re.compile(r"\b(fix(es|ed)?|bug|defect|hotfix|regression)\b", re.I)

#: Paths that do not count as code for the defect rule: a typo fixed in a README is a
#: fix, not a defect case anyone should replay a localizer against.
_DOC_SUFFIXES = (".md", ".rst", ".txt", ".adoc")


def _git(repo: Path, *args: str) -> tuple[str, str]:
    try:
        proc = subprocess.run(["git", "-C", str(repo), *args],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", f"git did not run: {exc}"
    if proc.returncode != 0:
        return "", (proc.stderr or "git failed").strip().splitlines()[0]
    return proc.stdout, ""


def classify(title: str, files: list[str], is_merge: bool) -> list[str]:
    """The v1 rules, in one place so the probe and the tests read the same sentence."""
    classes = []
    dirs = {f.rsplit("/", 1)[0] if "/" in f else "" for f in files}
    if len(files) >= 2 and len(dirs) >= 2:
        classes.append("scoping")
    if is_merge and files:
        classes.append("review")
    if (not is_merge and _FIX.search(title)
            and any(not f.lower().endswith(_DOC_SUFFIXES) for f in files)):
        classes.append("defect")
    return classes


def harvest_repo(repo: Path, alias: str,
                 limit: int) -> tuple[list[dict], str, list[dict]]:
    """Every qualifying case in this component's recent history, newest first.

    The third value carries the commits ``git log`` listed that this
    function tried and failed to turn into a case, each a
    ``{"sha", "why"}`` pair: a ``git show`` that failed, or changed files
    that were all harness bookkeeping.  Skipping these rather than
    recording them silently would lower the counts the M5 floors are
    judged against and report the lowered count as measured.  The module
    docstring keeps that rule for whole COMPONENTS; it is kept here for
    these two per-commit reasons too - stated, never skipped silently.

    It is NOT the complement of the case list, and a caller must not read
    ``len(cases) + len(dropped)`` as the number of commits ``git log``
    listed.  Two other commit shapes produce no case and are, today,
    absent from both lists just as silently: a root commit (no "before"
    to diff against) and a commit whose changed files classify to nothing
    (e.g. a docs-only fix).  Recording those against their own reasons is
    row T389, not this function yet.
    """
    fmt = "%H%x01%P%x01%aI%x01%s"
    out, err = _git(repo, "log", f"--max-count={limit}", f"--format={fmt}")
    if err:
        return [], err, []
    cases = []
    dropped: list[dict] = []
    for line in out.splitlines():
        sha, parents, date, title = (line.split("\x01") + ["", "", "", ""])[:4]
        if not sha:
            continue
        if not parents.strip():
            continue        # a root commit has no "before", so nothing can replay it
        is_merge = len(parents.split()) > 1
        # `show_err`, not `err`: reusing the outer name shadowed the component
        # error bound above, and a commit git could not show is could-not-look at
        # that commit, not one the classifier turned down - so it is counted.
        show, show_err = _git(repo, "show", "--pretty=format:", "--name-only",
                              "--diff-merges=first-parent", sha)
        if show_err:
            dropped.append({"sha": sha, "why": show_err})
            continue
        files = sorted({f for f in show.splitlines() if f.strip()})
        # **The harness's own commits are not cases.** It writes run folders,
        # metrics and generated workflows into the repository it is working in,
        # and those commits are the NEWEST - so they sort to the front of the
        # corpus and a replay picking the first N scores nothing but bookkeeping.
        # Measured on a real corpus: the first FIVE cases were all harness
        # commits, every one scoring precision@10 = 0.0 correctly and
        # meaninglessly. Third instance here of the harness contaminating what
        # it measures, after the review telemetry that dirtied its own tree and
        # the scanners that read its own worktree copy.
        files = subject_files(files)
        if not files:
            dropped.append({"sha": sha,
                            "why": "all changed files are harness bookkeeping"})
            continue
        classes = classify(title, files, is_merge)
        if not classes:
            continue
        cases.append({
            "schema_version": SCHEMA_VERSION, "repo": alias, "sha": sha,
            "parent": parents.split()[0] if parents else "", "date": date,
            "title": title, "classes": classes, "files": files,
            "synthetic": False,
        })
    return cases, "", dropped


def read_components(workspace: Path) -> list[dict]:
    """The workspace's components, through the same reader an adopter's INIT uses."""
    import importlib.util
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("sdlc_init", root / "sdlc-init.py")
    si = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("sdlc_init", si)
    spec.loader.exec_module(si)
    ws = si.read_workspace(workspace)
    found = si.discover_components(ws)["components"]
    return [{"name": c["name"], "path": str(Path(ws["root"]) / c["path"])}
            for c in found]


def probe_report(results: dict[str, tuple[list[dict], str, list[dict]]]) -> dict:
    per_class = {c: 0 for c in FLOORS}
    per_component = {}
    unharvestable = {}
    dropped_commits: dict[str, list[dict]] = {}
    for alias, (cases, err, dropped) in sorted(results.items()):
        if dropped:
            dropped_commits[alias] = dropped
        if err:
            unharvestable[alias] = err
            continue
        counts = {c: sum(1 for case in cases if c in case["classes"]) for c in FLOORS}
        per_component[alias] = counts
        for c in FLOORS:
            per_class[c] += counts[c]
    # `floor_met` is a MEASURED claim - counted, and at or below the floor. When
    # no component could be read there was no count, so the honest answer is None
    # (could-not-look), never False (which reads as measured-below).
    readable = bool(per_component)
    return {"schema_version": SCHEMA_VERSION, "classes": per_class,
            "floors_m5": FLOORS,
            "floor_met": {c: (per_class[c] >= n if readable else None)
                          for c, n in FLOORS.items()},
            "components": per_component, "unharvestable": unharvestable,
            "dropped_commits": dropped_commits}


def write_cases(repo: Path, cases: list[dict]) -> int:
    """The adopter's act: their history, into their tree, one file per commit."""
    dest = repo / ".sdlc" / "corpus"
    dest.mkdir(parents=True, exist_ok=True)
    for case in cases:
        (dest / f"{case['sha'][:12]}.json").write_text(
            json.dumps(case, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return len(cases)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", metavar="TOML", help="harvest every component the "
                                                       "workspace declares")
    ap.add_argument("--repo", metavar="PATH", help="harvest one repository")
    ap.add_argument("--limit", type=int, default=200, metavar="N",
                    help="history window per component, newest first (default 200) — "
                         "a recorded bound, not a silent one")
    ap.add_argument("--write", action="store_true",
                    help="write each component's cases into its own .sdlc/corpus/. "
                         "Default is --probe behaviour: read-only counts, because the "
                         "cases are the adopter's data and writing them is their act")
    ap.add_argument("--out", metavar="FILE", help="also write the probe report JSON here")
    args = ap.parse_args(argv)
    if bool(args.workspace) == bool(args.repo):
        print("!! exactly one of --workspace or --repo")
        return 2

    if args.repo:
        path = Path(args.repo)
        components = [{"name": path.name, "path": str(path)}]
    else:
        components = read_components(Path(args.workspace))

    results = {c["name"]: harvest_repo(Path(c["path"]), c["name"], args.limit)
               for c in components}
    report = probe_report(results)

    for c, n in report["classes"].items():
        floor = FLOORS[c]
        state = "met" if n >= floor else "below"
        print(f"  {c:<8} {n:>5} case(s)   (M5 floor {floor}: {state})")
    for alias, why in report["unharvestable"].items():
        print(f"  !! {alias}: {why}")
    for alias, drops in report["dropped_commits"].items():
        print(f"  !! {alias}: {len(drops)} commit(s) could not be scored "
              f"(e.g. {drops[0]['sha'][:12]}: {drops[0]['why']})")

    if args.write:
        total = sum(write_cases(Path(c["path"]), results[c["name"]][0])
                    for c in components if not results[c["name"]][1])
        print(f"\nwrote {total} case file(s) into the components' own .sdlc/corpus/")
    else:
        print("\nprobe only — nothing written into any component (--write is the "
              "adopter's act)")
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                                  encoding="utf-8")
        print(f"probe report: {args.out}")
    # Could-not-look, at either grain, is not a measured zero: an unreadable
    # component or a commit git could not show leaves the floor counts resting on
    # history nobody read. `3` is this repo's could-not-look code, the one
    # `corpus_replay` already uses at its door.
    if report["unharvestable"] or report["dropped_commits"]:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
