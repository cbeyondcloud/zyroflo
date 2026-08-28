"""The field corpus — item 96: the gates, run against repositories the harness
did not develop against.

The suite is thousands of tests deep and every fixture in it was written by
this project, so it measures what the harness does to its own idea of a
repository. One afternoon against one real repository found four gate-level
defects and **three of them walked straight past that suite**: a supply-chain
gate that had never scanned anything on any run on any platform, a baseline
that reported green off an exit code while its own report counted zero tests,
and a gate that wrote one filename beside a reader that opened another.

None of those needed an agent to find. They needed a real repository and
somebody reading what the gates actually said, which is the whole of this
module: walk the MECHANICAL phases — the baseline P0 records, the scoping P2
asks for, the supply-chain gate and the review pre-pass P5 and P6 own — and
write down, per gate, whether it fired and **what it measured**.

**The gates are spawned as their own command lines, never imported and
called.** Every one of the three defects above lived at or below the process
boundary: in an argument, in a walker's exclusions, in the name of a file one
side wrote and the other opened. A driver that imported the functions would
have reproduced all three faithfully and reported them all green. So the real
CLI runs, and the census is read back out of the documents it leaves, by the
constants their writers own.

**What is measured is a count, and its absence is not a zero.** `None` means
the gate could not look; `0` means it looked and found nothing. Conflating
them is how a gate that has stopped working reads as a clean repository, and
`baseline green` for a suite that ran nothing is that conflation with a name.
A gate's measure keys never change with its status, so a key that DISAPPEARS
is the harness having changed rather than the repository — which is the signal
item 97 reads.

Four statuses, because "did not run" is three different sentences:

* `ok`      — it ran and its document says so
* `skipped` — nobody asked it to (an operator's `--no-commands`), so this
              census makes no claim either way
* `not-run` — it was asked, and could not: no binary, no graph, no command
* `error`   — it was asked, and broke

The census is adopter-resident, in the repository it describes, beside the
metrics `policy.yml` already protects. It goes there rather than into this
repository for the reason the corpus manifest is gitignored: a harness that
must work on any repository cannot carry the name of one.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import cli  # noqa: E402
from tools.review import prepass as review  # noqa: E402
from tools.review import sca as scanner  # noqa: E402

cli.use_utf8()

SCHEMA_VERSION = 1

HARNESS = Path(__file__).resolve().parents[1]

#: Written and read by this module alone, so it needs no crossing test: a
#: module cannot drift from itself. It lives beside `reviews.jsonl` and
#: `provider-smoke.jsonl` because `policy.yml` protects that directory as one
#: glob, and a history a run can rewrite is not a history.
CENSUS_NAME = "field-census.jsonl"
METRICS = Path(".sdlc") / "metrics"

#: The file INIT writes; its absence is what "not installed here" means.
PROFILE = Path(".sdlc") / "profile.yml"

#: Where the chained build puts a repository's own facts.
GRAPH_DIR = Path(".sdlc") / "graph"

#: Every gate this walk asks, and every counter it reports. The key set is
#: FIXED per gate and independent of the outcome: a row for a gate that could
#: not look carries the same keys holding `None`. That is what lets a missing
#: key mean "this harness stopped reporting it" rather than "this repository
#: had none", which is the difference between a defect and a fact.
MEASURES: dict[str, tuple[str, ...]] = {
    "baseline": ("components", "with_command", "executed", "tests"),
    "scoping": ("nodes", "edges", "ranked"),
    "sca": ("manifests", "findings", "graded"),
    "prepass": ("gates_ok", "rules", "findings"),
}
GATES = tuple(MEASURES)

STATUSES = ("ok", "skipped", "not-run", "error")

#: A component's suite is the long pole and a real one can take an hour.
TIMEOUTS = {"baseline": 7200, "scoping": 600, "sca": 900, "prepass": 900}

#: What a severity class looks like when the grader could not grade it. The
#: string is `sca.py`'s, and it is asked for rather than spelled.
UNGRADED = "(ungraded)"


class CorpusError(RuntimeError):
    """The walk could not start, and starting anyway would record a fiction."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _git(where: Path, *args: str) -> str | None:
    """Ask git, or `None` when it cannot be asked.

    `None` rather than `""`: a directory that is not a repository and a
    repository with no commits are different answers, and neither is a sha.
    """
    try:
        proc = subprocess.run(["git", "-C", str(where), *args],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _spawn(cmd: list[str], timeout: int) -> tuple[int | None, str, str]:
    """A gate's own command line, run the way an operator runs it.

    A `None` exit code is a command that did not finish — a missing
    interpreter, a timeout — and is never folded into a number, for the
    reason every other absence here is kept apart from one.
    """
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout, cwd=str(HARNESS))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, "", f"{type(exc).__name__}: {exc}"
    return proc.returncode, proc.stdout, proc.stderr


def blank(gate: str) -> dict[str, int | None]:
    """This gate's counters, all of them, holding nothing yet."""
    return {name: None for name in MEASURES[gate]}


def row(gate: str, status: str, detail: str = "", exit_code: int | None = None,
        **measured: int | None) -> dict:
    """One gate's census entry, with its full key set whatever happened."""
    if status not in STATUSES:
        raise CorpusError(f"{status!r} is not one of {', '.join(STATUSES)}")
    unknown = sorted(set(measured) - set(MEASURES[gate]))
    if unknown:
        raise CorpusError(f"{gate} does not report {', '.join(unknown)} - "
                          f"it reports {', '.join(MEASURES[gate])}")
    return {"gate": gate, "status": status, "detail": detail,
            "exit_code": exit_code, "measures": {**blank(gate), **measured}}


def _tail(text: str, keep: int = 200) -> str:
    """The last thing a failing command said, short enough to live in a row."""
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return (lines[-1] if lines else "")[:keep]


def _load(path: Path) -> dict | None:
    """A document a gate wrote, or `None` if it is not there or not readable."""
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) else None


# ── the gates ───────────────────────────────────────────────────────────────

def gate_baseline(target: Path, execute: bool) -> dict:
    """P0's question: what does each component's suite do before anything moves.

    `tests` is the counter that matters and the one a field run caught lying.
    It is summed over the components that reported a number, and stays `None`
    when not one of them did — a workspace where no suite emitted a report has
    not measured zero tests, it has failed to measure.
    """
    if not execute:
        return row("baseline", "skipped",
                   "the suites were not executed (--no-commands), so this "
                   "census makes no claim about them")
    code, out, err = _spawn(
        [sys.executable, str(HARNESS / "tools" / "sdlc" / "suite.py"),
         "--target", str(target), "--baseline", "--run-commands", "--json"],
        TIMEOUTS["baseline"])
    if code is None:
        return row("baseline", "error", err or "the command did not finish")
    try:
        data = json.loads(out)
    except ValueError:
        return row("baseline", "error",
                   f"the baseline printed no JSON this could read: "
                   f"{_tail(err or out)}", exit_code=code)
    components = data.get("components")
    if not isinstance(components, dict):
        return row("baseline", "error",
                   "the baseline document carries no components map",
                   exit_code=code)

    counted = [r.get("tests") for r in components.values()
               if isinstance(r, dict) and isinstance(r.get("tests"), int)]
    with_command = sum(1 for r in components.values()
                       if isinstance(r, dict) and r.get("command"))
    executed = sum(1 for r in components.values()
                   if isinstance(r, dict) and r.get("ran"))
    if not components:
        return row("baseline", "not-run",
                   "this workspace declares no components", exit_code=code,
                   components=0, with_command=0, executed=0)
    if not with_command:
        return row("baseline", "not-run",
                   "no component declares a test command, so there is nothing "
                   "for P5 to compare against", exit_code=code,
                   components=len(components), with_command=0, executed=0)
    return row("baseline", "ok", "", exit_code=code,
               components=len(components), with_command=with_command,
               executed=executed,
               tests=sum(counted) if counted else None)


def gate_scoping(target: Path, seed: str) -> dict:
    """P2's question: does this repository's graph still reach anything.

    Two counters come from the graph itself and the third from a real
    traversal, because a graph that loaded and a graph that can answer are
    different claims — the second is what P2 hands a reviewer.
    """
    from tools.graph import impact as scope

    graph_dir = target / GRAPH_DIR
    # Absent, damaged and empty are three answers. The existence check is the
    # caller's here as it is at every other call site, because `load_graph`
    # reads a missing file as an empty record on purpose.
    if not (graph_dir / "graph.jsonl").is_file():
        return row("scoping", "not-run",
                   f"no graph at {graph_dir} - this repository has not been "
                   f"built, or its stack has no extractor yet")
    try:
        # The module that owns the graph decides what a node is and what an
        # edge is. Counting them here by hand would be a second answer to a
        # question with one home, and a graph gains row kinds over time.
        node_rows, edge_rows = scope.load_graph(graph_dir)
    except scope.GraphError as exc:
        return row("scoping", "error", str(exc).splitlines()[0])
    except OSError as exc:
        return row("scoping", "error", f"the graph could not be read: {exc}")
    nodes, edges = len(node_rows), len(edge_rows)

    if not seed:
        return row("scoping", "not-run",
                   "the corpus entry names no seed, so nothing was traversed - "
                   "set `seed` to a COMPONENT/RELPATH this graph cites",
                   nodes=nodes, edges=edges)
    code, out, err = _spawn(
        [sys.executable, str(HARNESS / "tools" / "graph" / "impact.py"),
         "--graph-dir", str(graph_dir), "--seed", seed, "--json",
         # 0 = all. The default caps the list at 20, and a counter read off a
         # capped list reports the cap forever - a silent ceiling is exactly
         # the shape of quiet this whole module exists to notice.
         "--top", "0"],
        TIMEOUTS["scoping"])
    if code is None:
        return row("scoping", "error", err or "the command did not finish",
                   nodes=nodes, edges=edges)
    if code != 0:
        return row("scoping", "not-run",
                   f"the impact query refused: {_tail(out or err)}",
                   exit_code=code, nodes=nodes, edges=edges)
    try:
        data = json.loads(out)
    except ValueError:
        return row("scoping", "error",
                   f"the impact query printed no JSON this could read: "
                   f"{_tail(err or out)}", exit_code=code,
                   nodes=nodes, edges=edges)
    ranked = data.get("files")
    return row("scoping", "ok", "", exit_code=code, nodes=nodes, edges=edges,
               ranked=len(ranked) if isinstance(ranked, list) else None)


def gate_sca(target: Path, out_dir: Path) -> dict:
    """P5's supply-chain question, read back out of the document it writes.

    `manifests` is the counter the gate was blind on: a walker pruned the scan
    root and it reported no findings for eight months, which is the same
    output a clean repository produces. A findings count alone cannot tell
    those apart; a manifest count can.
    """
    code, out, err = _spawn(
        [sys.executable, str(HARNESS / "tools" / "review" / "sca.py"),
         "--target", str(target), "--out", str(out_dir)],
        TIMEOUTS["sca"])
    if code is None:
        return row("sca", "error", err or "the command did not finish")
    result = _load(out_dir / scanner.RESULT_NAME)
    if result is None:
        return row("sca", "error",
                   f"the gate wrote no {scanner.RESULT_NAME} this could read: "
                   f"{_tail(err or out)}", exit_code=code)

    manifests = sum(len(per.get("scanned") or [])
                    for tool in result.get("tools") or []
                    if isinstance(tool, dict)
                    for per in tool.get("per_component") or []
                    if isinstance(per, dict))
    summary = result.get("summary") or {}
    findings = summary.get("total")
    by_class = summary.get("by_severity") or {}
    graded = sum(count for name, count in by_class.items() if name != UNGRADED)
    # The rule lives in the module that writes the document. Asking it here is
    # the whole point of spawning the real gate: a status vocabulary read off a
    # local guess is how two readers of one document came to disagree before.
    if review.unreviewed(result, expected=(scanner.TOOL,)):
        return row("sca", "not-run",
                   "; ".join(review.unreviewed(result, expected=(scanner.TOOL,))),
                   exit_code=code, manifests=manifests)
    return row("sca", "ok", "", exit_code=code, manifests=manifests,
               findings=findings if isinstance(findings, int) else None,
               graded=graded)


def gate_prepass(target: Path, out_dir: Path) -> dict:
    """P6's deterministic half: the secret gate and the pattern gate.

    `rules` is here because an ast-grep that loads no rules still exits 0 with
    no findings, which is indistinguishable from clean code.
    """
    code, out, err = _spawn(
        [sys.executable, str(HARNESS / "tools" / "review" / "prepass.py"),
         "--target", str(target), "--out", str(out_dir)],
        TIMEOUTS["prepass"])
    if code is None:
        return row("prepass", "error", err or "the command did not finish")
    result = _load(out_dir / review.RESULT_NAME)
    if result is None:
        return row("prepass", "error",
                   f"the pass wrote no {review.RESULT_NAME} this could read: "
                   f"{_tail(err or out)}", exit_code=code)

    rows = review.tool_rows(result) or []
    gates_ok = sum(1 for t in rows
                   if isinstance(t, dict) and t.get("status") == review.STATUS_OK)
    rules = sum(len(t.get("rules") or []) for t in rows if isinstance(t, dict))
    summary = result.get("summary") or {}
    findings = summary.get("total")
    if problems := review.unreviewed(result):
        return row("prepass", "not-run", "; ".join(problems), exit_code=code,
                   gates_ok=gates_ok, rules=rules)
    return row("prepass", "ok", "", exit_code=code, gates_ok=gates_ok,
               rules=rules,
               findings=findings if isinstance(findings, int) else None)


# ── the corpus ──────────────────────────────────────────────────────────────

def read_corpus(path: Path) -> list[dict]:
    """The manifest, or a refusal naming every field that is wrong.

    Refuses rather than defaults, for the reason `policy.py` refuses a document
    it cannot read: a corpus entry filled in wrong is indistinguishable from
    one filled in right until a census is compared against it much later.
    """
    if not path.is_file():
        raise CorpusError(
            f"no corpus manifest at {path} - copy "
            f"sdlc_templates/corpus.example.toml and fill it in")
    try:
        doc = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
        raise CorpusError(f"{path} did not parse: {exc}") from exc

    entries = doc.get("repo")
    if not isinstance(entries, list) or not entries:
        raise CorpusError(f"{path} declares no [[repo]] entries")
    problems: list[str] = []
    out: list[dict] = []
    for i, entry in enumerate(entries, 1):
        if not isinstance(entry, dict):
            problems.append(f"[[repo]] #{i} is not a table")
            continue
        name = str(entry.get("name") or "").strip()
        where = str(entry.get("path") or "").strip()
        commit = str(entry.get("commit") or "").strip()
        for field, value in (("name", name), ("path", where), ("commit", commit)):
            if not value:
                problems.append(f"[[repo]] #{i} declares no `{field}`")
        # A census taken at a commit nobody wrote down cannot be compared with
        # one taken later, so the pin is required. That it MOVED is a note; that
        # it was never stated is a refusal.
        args = entry.get("install_args") or []
        if not (isinstance(args, list) and all(isinstance(a, str) for a in args)):
            problems.append(f"[[repo]] #{i}: `install_args` must be a list of "
                            f"strings")
            args = []
        out.append({"name": name, "path": where, "commit": commit,
                    "stack": str(entry.get("stack") or "").strip(),
                    "seed": str(entry.get("seed") or "").strip(),
                    "install_args": list(args)})
    seen: set[str] = set()
    for entry in out:
        if entry["name"] and entry["name"] in seen:
            problems.append(f"two entries are named {entry['name']!r} - a census "
                            f"is filed under the name, so they would merge")
        seen.add(entry["name"])
    if problems:
        raise CorpusError(f"{path} has {len(problems)} problem(s): "
                          + "; ".join(problems))
    return out


def install(target: Path, extra: list[str] | None = None) -> tuple[bool, str]:
    """Put the harness into a repository that has none. The operator's act.

    `extra` carries the entry's own `install_args`, because the installer
    refuses some real repositories by design — item 95 stops a monorepo being
    modelled as one component — and the answer to that refusal is a decision
    about THAT repository. It belongs in the manifest describing it, which is
    the operator's file, not in a default this module picks for everybody.
    """
    code, out, err = _spawn(
        [sys.executable, str(HARNESS / "sdlc-init.py"), "--target", str(target),
         *(extra or [])],
        TIMEOUTS["sca"])
    if code == 0:
        return True, ""
    return False, f"sdlc-init exited {code}: {_tail(err or out)}"


def census_path(target: Path) -> Path:
    return target / METRICS / CENSUS_NAME


def walk(entry: dict, execute: bool = True, out_dir: Path | None = None,
         may_install: bool = False, verbose: bool = True) -> dict:
    """One repository, every mechanical gate, one census row.

    Writes nothing into the repository but the census itself: the gates'
    documents go to a scratch directory unless the operator asks to keep them.
    A recurring check that accumulated run folders in somebody's checkout would
    be turned off, and a check that is off measures nothing.
    """
    target = Path(entry["path"]).expanduser()
    if not target.is_dir():
        raise CorpusError(f"{entry['name']}: no directory at {target}")
    target = target.resolve()

    def say(text: str) -> None:
        if verbose:
            print(text)

    if not (target / PROFILE).is_file():
        if not may_install:
            raise CorpusError(
                f"{entry['name']}: the harness is not installed at {target} "
                f"(no {PROFILE}). Pass --install to install it, or run "
                f"sdlc-init.py against it yourself")
        say(f"  installing the harness into {entry['name']}")
        ok, why = install(target, entry.get("install_args"))
        if not ok:
            raise CorpusError(f"{entry['name']}: {why}")

    head = _git(target, "rev-parse", "HEAD")
    at_pin = bool(head and entry["commit"] and head.startswith(entry["commit"]))
    if head is None:
        say(f"  NOTE: {target} is not a git repository, so no commit was recorded")
    elif not entry["commit"]:
        say(f"  NOTE: {entry['name']} declares no pin, so this census records "
            f"{head[:12]} and can be compared only against another census of "
            f"the same commit")
    elif not at_pin:
        say(f"  NOTE: {entry['name']} is at {head[:12]}, not the pinned "
            f"{entry['commit'][:12]} - this census is comparable only if that "
            f"move was a decision")

    with tempfile.TemporaryDirectory(prefix="sdlc-field-") as scratch:
        where = Path(out_dir) if out_dir else Path(scratch)
        where.mkdir(parents=True, exist_ok=True)
        gates = [gate_baseline(target, execute),
                 gate_scoping(target, entry["seed"]),
                 gate_sca(target, where),
                 gate_prepass(target, where)]
    for gate in gates:
        measured = ", ".join(f"{k}={'?' if v is None else v}"
                             for k, v in gate["measures"].items())
        say(f"  {gate['gate']:<9} {gate['status']:<8} {measured}"
            + (f"  - {gate['detail']}" if gate["detail"] else ""))

    return {
        "schema_version": SCHEMA_VERSION,
        "at": _now(),
        "repo": entry["name"],
        "stack": entry["stack"],
        "commit": head,
        "pinned": entry["commit"] or None,
        "at_pin": at_pin,
        "harness": _git(HARNESS, "rev-parse", "HEAD"),
        "gates": {gate["gate"]: {k: v for k, v in gate.items() if k != "gate"}
                  for gate in gates},
    }


def append(target: Path, census: dict) -> Path:
    """Add one row to the repository's own history. Append only, ever."""
    path = census_path(Path(target))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(census, sort_keys=True) + "\n")
    return path


def history(target: Path, repo: str = "") -> list[dict]:
    """Every census this repository has recorded, oldest first.

    An unreadable line is COUNTED, not skipped and not fatal: item 97 reads
    this to decide whether a gate went quiet, and a history that silently
    shortened itself would answer that question wrong in the safe-looking
    direction.
    """
    out: list[dict] = []
    for line in cli.jsonl_rows(census_path(Path(target))):
        if line.problem:
            out.append({"_unreadable": line.problem, "at": "", "repo": repo})
            continue
        record = line.row
        if repo and record.get("repo") != repo:
            continue
        out.append(record)
    return out


def problems(census: dict) -> list[str]:
    """Why this WALK is not a clean one. Empty is the only clean answer.

    Only `error`. A gate that could not look is a fact about the repository as
    often as it is a defect — no graph built, no manifests to read, no suite
    declared — and reddening a recurring check for those is how a recurring
    check gets switched off, which item 97 is explicit about. Whether a
    `not-run` MATTERS is a question about history, and history is what item
    97's check reads; a single census has nothing to compare it against.
    """
    return [f"the {name} gate broke: {gate['detail']}"
            for name, gate in sorted(census["gates"].items())
            if gate["status"] == "error"]


def quiet(census: dict) -> list[str]:
    """Gates that measured nothing here. Reported always, fatal never."""
    return [f"the {name} gate did not run: {gate['detail']}"
            for name, gate in sorted(census["gates"].items())
            if gate["status"] in ("not-run", "skipped")]


def _render(census: dict) -> str:
    head = (census["commit"] or "(not a git repository)")[:12]
    pin = ("at its pin" if census["at_pin"] else
           "no pin declared" if not census["pinned"] else "NOT at its pin")
    alive = sum(1 for g in census["gates"].values() if g["status"] == "ok")
    return (f"{census['repo']}: {head} {pin}, {alive} of "
            f"{len(census['gates'])} gate(s) measured something")


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", metavar="TOML",
                    help="the field-corpus manifest (see "
                         "sdlc_templates/corpus.example.toml)")
    ap.add_argument("--repo", metavar="NAME",
                    help="walk only this entry of the manifest")
    ap.add_argument("--path", metavar="DIR",
                    help="walk one repository with no manifest; needs --name")
    ap.add_argument("--name", metavar="LABEL", help="its name in the census")
    ap.add_argument("--seed", default="", metavar="COMPONENT/RELPATH",
                    help="with --path: the file the scoping gate traverses from")
    ap.add_argument("--install", action="store_true",
                    help="install the harness into a repository that has none")
    ap.add_argument("--no-commands", action="store_true",
                    help="do not execute the suites; the baseline gate then "
                         "makes no claim rather than a false one")
    ap.add_argument("--out", metavar="DIR",
                    help="keep the gates' own documents here (default: a "
                         "scratch directory, removed afterwards)")
    ap.add_argument("--dry-run", action="store_true",
                    help="walk and report, writing no census row")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        if args.corpus and args.path:
            raise CorpusError("--corpus and --path name the same thing two ways; "
                              "pass one")
        if args.path:
            if not args.name:
                raise CorpusError("--path needs --name: a census is filed under "
                                  "the name, and an unnamed row cannot be "
                                  "compared with anything")
            entries = [{"name": args.name, "path": args.path, "commit": "",
                        "stack": "", "seed": args.seed, "install_args": []}]
        elif args.corpus:
            entries = read_corpus(Path(args.corpus))
            if args.repo:
                entries = [e for e in entries if e["name"] == args.repo]
                if not entries:
                    raise CorpusError(f"no entry named {args.repo!r} in "
                                      f"{args.corpus}")
        else:
            raise CorpusError("pass --corpus <manifest> or --path <dir> --name "
                              "<label>")
    except CorpusError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2

    walked: list[dict] = []
    refused: list[str] = []
    for entry in entries:
        if not args.json:
            print(f"{entry['name']}:")
        try:
            census = walk(entry, execute=not args.no_commands,
                          out_dir=Path(args.out) if args.out else None,
                          may_install=args.install, verbose=not args.json)
        except CorpusError as exc:
            refused.append(str(exc))
            if not args.json:
                print(f"  refused: {exc}")
            continue
        if not args.dry_run:
            path = append(Path(entry["path"]).expanduser().resolve(), census)
            if not args.json:
                print(f"  recorded in {path}")
        walked.append(census)

    if args.json:
        print(json.dumps({"walked": walked, "refused": refused,
                          "problems": {c["repo"]: problems(c) for c in walked},
                          "quiet": {c["repo"]: quiet(c) for c in walked}},
                         indent=2, sort_keys=True))
    else:
        for census in walked:
            print(_render(census))
            for note in quiet(census):
                print(f"  -- {note}")
            for problem in problems(census):
                print(f"  !! {problem}")
    if refused:
        return 2
    return 1 if any(problems(c) for c in walked) else 0


def main(argv: list[str] | None = None) -> int:
    """The door. One implementation, in `tools/cli.py`. `3` is written here
    literally because *could not look* is this module's own vocabulary, and a
    field run that crashed must not read as a field run that found nothing."""
    return cli.door(_main, argv, "the field run", 3)


if __name__ == "__main__":
    raise SystemExit(main())
