"""P8 — publish the branch, open the pull request, watch the REAL CI. Item 47.

The lifecycle's own table says it plainly: *"Pushes branch, opens PR with
artifacts, watches real CI… **Clicks merge — always**"*, where the human is the
one clicking. This module is everything up to that click and nothing past it.

**The merge click is not automated, and that is enforced rather than promised.**
`gh pr merge` is never called, and a test asserts the string does not appear in
this file. A prose rule in an SOP is a rule an agent can decide to be pragmatic
about; a gate the code cannot reach is not.

**Three answers are not green, and each of them has been the shape of a real
defect in this project.**

* **red** — a check failed. The phase routes back to P4 **with the log**,
  because "CI is red" is not something P4 can act on and a failing job's output
  is.
* **pending** — a check has not finished. A run that read pending as passing
  would merge on a promise; this is the `not-run` vs `ok` distinction the review
  gates already make.
* **none** — the pull request has NO checks at all. This is the one worth the
  most care: item 40's SCA gate scanned nothing and reported nothing for
  months, and *"nothing ran"* and *"nothing failed"* produce identical output
  unless something insists they are different sentences.

**Publishing goes through the wall, not around it.** `guard.check` decides
whether this remote and this branch may be pushed to, using `policy.push_target`
— the same predicate the constitution is written in. A component whose role is
`fork` is refused there, and this module does not second-guess it.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import cli  # noqa: E402
from tools.sdlc import policy as guardrails  # noqa: E402
from tools.sdlc import stage as stages  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

SCHEMA_VERSION = 1
PHASE = "P8"

#: The CLI this phase watches CI through. Pinned in the manifest at
#: `needed_by: M5` with this item named in its purpose.
TOOL = "gh"

#: What a pull request's checks add up to. **Only one of these is a pass**, and
#: the other four are kept apart because folding any pair of them together is
#: how a gate starts reporting a green it did not earn.
GREEN, RED, PENDING, NONE, UNKNOWN = "green", "red", "pending", "none", "unknown"

#: `gh pr checks --json ... .bucket` vocabulary, mapped to ours. `skipping` is
#: deliberately NOT a pass: a skipped job ran nothing, and whether that is fine
#: is a question for a person.
BUCKETS = {"pass": GREEN, "fail": RED, "pending": PENDING,
           "skipping": PENDING, "cancel": RED}


class ReleaseError(RuntimeError):
    """The publish path could not be walked, which is never a green CI."""


def binary() -> tuple[Path | None, str]:
    from tools.review import prepass

    return prepass.binary(TOOL)


def _gh(args: list[str], cwd: Path, timeout: int = 300) -> tuple[int, str, str]:
    exe, why = binary()
    if exe is None:
        raise ReleaseError(f"{TOOL} is not available: {why} - CI cannot be "
                           f"watched, which is not the same as CI being green")
    try:
        proc = subprocess.run([str(exe), *args], cwd=str(cwd), capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseError(f"{TOOL} did not run: {exc}") from None
    return proc.returncode, proc.stdout or "", proc.stderr or ""


# ── publishing, through the wall ────────────────────────────────────────────


def branch_of(target: Path, run_id: str, component: str = "") -> str:
    """This run's branch, from the record the worktree model already keeps."""
    from tools.sdlc import worktree as trees

    try:
        homes = (trees.load_record(Path(target), run_id).get("components") or {})
    except (trees.WorktreeError, OSError):
        return ""
    entry = homes.get(component) if component else None
    if entry is None and len(homes) == 1:
        entry = next(iter(homes.values()))
    return str((entry or {}).get("branch") or "")


def push_problems(target: Path, run_id: str, branch: str,
                  remote: str = "origin") -> list[str]:
    """Why this push is refused, in the constitution's own words.

    `policy.push_target` and not a second opinion: the wall item 35 built is
    what decides where a run may publish, and a module that asked the question
    its own way would be a second answer to it.
    """
    try:
        rules = guardrails.load(Path(target))
    except (guardrails.PolicyError, OSError, ValueError) as exc:
        return [f"the policy could not be read: {exc}"]
    comp = None
    try:
        every = guardrails.components(Path(target))
        comp = every[0] if len(every) == 1 else None
    except Exception:
        comp = None
    return guardrails.push_target(rules, remote, branch, run_id,
                                  (comp or {}).get("vcs"))


def push(target: Path, run_id: str, branch: str, remote: str = "origin",
         where: Path | None = None) -> dict:
    """Publish the run branch. Refuses first, pushes second."""
    if blocked := push_problems(Path(target), run_id, branch, remote):
        raise ReleaseError("; ".join(blocked))
    # git, not gh: publishing a branch needs neither the CLI nor a token, and
    # requiring one here would refuse a push on a machine where only CI
    # WATCHING is unavailable.
    cwd = Path(where or target)
    proc = subprocess.run(["git", "-C", str(cwd), "push", "-u", remote, branch],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=600)
    if proc.returncode != 0:
        raise ReleaseError(f"git push failed: "
                           f"{(proc.stderr or '').strip().splitlines()[-1:]}")
    return {"remote": remote, "branch": branch, "pushed": True}


# ── the pull request, and never its merge ───────────────────────────────────


def find_pr(target: Path, branch: str) -> dict | None:
    """The open pull request for this branch, or None."""
    code, out, err = _gh(["pr", "list", "--head", branch, "--state", "open",
                          "--json", "number,url,headRefName"], Path(target))
    if code != 0:
        raise ReleaseError(f"{TOOL} pr list failed: {err.strip()[:200]}")
    try:
        rows = json.loads(out or "[]")
    except ValueError as exc:
        raise ReleaseError(f"{TOOL} pr list returned unreadable JSON: {exc}") from None
    return rows[0] if rows else None


def open_pr(target: Path, branch: str, title: str, body: str,
            base: str = "") -> dict:
    """Open the pull request. **This is the last outward-facing act of P8** —
    the merge belongs to a human and is not reachable from this module."""
    args = ["pr", "create", "--head", branch, "--title", title, "--body", body]
    if base:
        args += ["--base", base]
    code, out, err = _gh(args, Path(target), timeout=600)
    if code != 0:
        raise ReleaseError(f"{TOOL} pr create failed: {err.strip()[:300]}")
    lines = [ln.strip() for ln in (out or "").splitlines() if ln.strip()]
    return {"url": lines[-1] if lines else ""}


# ── what CI actually says ───────────────────────────────────────────────────


def check_rows(target: Path, pr: int | str) -> list[dict]:
    """Every check on this pull request.

    `gh pr checks` first; **the REST fallback is not decoration** — the
    subcommand's JSON fields have moved between releases, and a phase that
    could only read one shape would report `none` on a repository whose CI is
    running perfectly.
    """
    code, out, err = _gh(["pr", "checks", str(pr), "--json",
                          "name,state,bucket,link"], Path(target))
    if code in (0, 8) and out.strip():          # 8 = checks exist and some failed
        try:
            return [r for r in json.loads(out) if isinstance(r, dict)]
        except ValueError:
            pass
    return rest_rows(Path(target), pr)


def rest_rows(target: Path, pr: int | str) -> list[dict]:
    """The same question through the API, for when the subcommand cannot answer."""
    code, out, err = _gh(["api", f"repos/{{owner}}/{{repo}}/commits/"
                          f"{_head_sha(target, pr)}/check-runs",
                          "--jq", ".check_runs[] | {name: .name, "
                                  "state: .status, bucket: .conclusion, "
                                  "link: .html_url}"], Path(target))
    if code != 0:
        raise ReleaseError(f"{TOOL} api check-runs failed: {err.strip()[:200]}")
    rows = []
    for line in (out or "").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _head_sha(target: Path, pr: int | str) -> str:
    code, out, err = _gh(["pr", "view", str(pr), "--json", "headRefOid",
                          "--jq", ".headRefOid"], Path(target))
    if code != 0:
        raise ReleaseError(f"{TOOL} pr view failed: {err.strip()[:200]}")
    return (out or "").strip()


def verdict(rows: list[dict]) -> dict:
    """What the checks add up to, and **four of the five answers are not green.**

    `none` is the one this project has paid for twice: a gate that scanned
    nothing reported nothing, and *"nothing ran"* read exactly like *"nothing
    failed"*. A pull request with no checks is not a passing pull request; it is
    a pull request nobody is checking.
    """
    if not rows:
        return {"state": NONE, "checks": 0, "failed": [], "pending": [],
                "why": "this pull request has no checks at all - nothing ran, "
                       "which is not the same as nothing failed"}
    failed, pending, unknown = [], [], []
    for row in rows:
        bucket = str(row.get("bucket") or "").strip().lower()
        name = str(row.get("name") or "?")
        mapped = BUCKETS.get(bucket)
        if mapped == RED:
            failed.append(name)
        elif mapped == PENDING:
            pending.append(name)
        elif mapped is None:
            # A bucket this module has never seen. Not green, and named - a
            # vocabulary gap is how item 36's rdjson severities let a leaked
            # credential out of the review loop.
            unknown.append(f"{name} ({bucket or 'blank'})")
    if failed:
        state, why = RED, f"{len(failed)} check(s) failed: {', '.join(failed)}"
    elif unknown:
        state, why = UNKNOWN, (f"check(s) reported a state this phase does not "
                               f"know: {', '.join(unknown)} - not read as a pass")
    elif pending:
        state, why = PENDING, f"{len(pending)} check(s) still running"
    else:
        state, why = GREEN, f"all {len(rows)} check(s) passed"
    return {"state": state, "checks": len(rows), "failed": failed,
            "pending": pending, "unknown": unknown, "why": why}


def logs_for(target: Path, pr: int | str, names: list[str]) -> dict[str, str]:
    """The failing jobs' output, because *"CI is red"* is not actionable and a
    log is. This is what P4 is handed when the phase routes back."""
    out: dict[str, str] = {}
    for row in check_rows(Path(target), pr):
        if str(row.get("name")) not in names:
            continue
        link = str(row.get("link") or "")
        run_id = link.rstrip("/").split("/runs/")[-1].split("/")[0] if "/runs/" in link else ""
        if not run_id:
            out[str(row.get("name"))] = f"no log link recorded ({link or 'none'})"
            continue
        code, text, err = _gh(["run", "view", run_id, "--log-failed"], Path(target),
                              timeout=600)
        out[str(row.get("name"))] = (text or err or "").strip()[:20000]
    return out


#: Where the CI verdict lands, so `stage.verify` has something to read. A phase
#: whose evidence is only ever printed is a phase nobody can check afterwards -
#: the SCA gate writes `50-sca.json` for the same reason.
#:
#: ASKED FOR, never spelled again. The first cut declared the literal here while
#: `stage.py` declared it too - item 94's exact violation, two spellings of one
#: name - and the equality test I wrote passed because the strings matched,
#: which is what a second spelling looks like right up until one of them moves.
#: The dead-import guard is what caught it: `stages` was imported and unused
#: precisely BECAUSE the name had been duplicated instead of asked for.
RESULT = stages.CI_RESULT


def recorded(target: Path, run_id: str) -> dict | None:
    """The CI verdict this run recorded, or None if it never asked."""
    path = ledger.run_dir(Path(target), run_id) / RESULT
    if not path.is_file():
        return None
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReleaseError(f"{path} could not be read: {exc}") from None
    return found if isinstance(found, dict) else None


def obligations(target: Path, run_id: str) -> list[str]:
    """What P8 owes, for `stage.verify` through the CONDITIONAL_JUDGE seam.

    **Owed once the run is actually AT P8.** A run sitting there with no
    recorded verdict has not watched CI - which differs from watching it and
    finding no checks, and both differ from green. Earlier phases owe nothing
    here, because a run that has not reached release has nothing to watch.
    """
    try:
        found = recorded(Path(target), run_id)
    except ReleaseError as exc:
        return [str(exc)]
    if found is None:
        try:
            at = str(ledger.load(Path(target), run_id).get("phase") or "")
        except Exception:
            return []
        if at != PHASE:
            return []
        return [f"{PHASE} has recorded no CI verdict ({RESULT}) - a branch "
                f"nobody watched is not a branch whose checks passed "
                f"(release.py --checks --pr <n> --out <run folder>)"]
    return problems(Path(target), run_id, found)


def problems(target: Path, run_id: str, result: dict | None = None) -> list[str]:
    """What a recorded verdict makes P8 owe. Anything but green holds it."""
    if result is None:
        return []
    state = result.get("state")
    if state == GREEN:
        return []
    return [f"CI is {state}: {result.get('why', 'no reason recorded')} - "
            f"a change does not leave {PHASE} on anything but a green check, "
            f"and the merge click is a human's in every case"]


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", default=".", metavar="DIR")
    ap.add_argument("--run", metavar="ID")
    ap.add_argument("--branch", metavar="NAME")
    ap.add_argument("--remote", metavar="NAME", default="origin")
    ap.add_argument("--pr", metavar="NUMBER")
    ap.add_argument("--checks", action="store_true",
                    help="read what CI says about the pull request")
    ap.add_argument("--logs", action="store_true",
                    help="with --checks: fetch the failing jobs' output")
    ap.add_argument("--out", metavar="DIR",
                    help=f"write {RESULT} here, so the verdict is evidence "
                         f"rather than a line that scrolled past")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve()
    if args.checks:
        if not args.pr:
            print("refused: --checks needs --pr", file=sys.stderr)
            return 2
        rows = check_rows(target, args.pr)
        found = verdict(rows)
        if args.logs and found["failed"]:
            found["logs"] = logs_for(target, args.pr, found["failed"])
        if args.out:
            where = Path(args.out)
            where.mkdir(parents=True, exist_ok=True)
            (where / RESULT).write_text(
                json.dumps({"schema_version": SCHEMA_VERSION, **found},
                           indent=2, sort_keys=True) + "\n",
                encoding="utf-8", newline="\n")
            print(f"wrote {where / RESULT}")
        if args.json:
            print(json.dumps(found, indent=2, sort_keys=True))
        else:
            print(f"CI: {found['state']} - {found['why']}")
            for name, text in (found.get("logs") or {}).items():
                print(f"--- {name} ---\n{text}")
        return 0 if found["state"] == GREEN else 1

    if args.run and not args.branch:
        args.branch = branch_of(target, args.run)
    if not args.branch:
        print("refused: no branch - pass --branch, or --run for a run whose "
              "worktree record names one", file=sys.stderr)
        return 2
    blocked = push_problems(target, args.run or "", args.branch, args.remote)
    if args.json:
        print(json.dumps({"branch": args.branch, "remote": args.remote,
                          "refused": blocked}, indent=2, sort_keys=True))
    else:
        print(f"branch {args.branch} -> {args.remote}: "
              + ("allowed" if not blocked else "REFUSED"))
        for line in blocked:
            print(f"  !! {line}")
    return 1 if blocked else 0


def main(argv: list[str] | None = None) -> int:
    """The door. `3` is could-not-look, which must never read as a green CI."""
    cli.use_utf8()
    return cli.door(_main, argv, "the release phase", 3)


if __name__ == "__main__":
    raise SystemExit(main())
