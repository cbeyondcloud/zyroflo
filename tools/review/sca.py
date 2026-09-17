"""The SCA gate — item 40: known vulnerabilities in what the changed components
actually depend on.

`osv-scanner` is pinned like the other vendored tools and run over the changed
components. Its findings land in the same rdjson-shaped document the review
pre-pass produces, so `loop.assess` weighs them against `block_on` and they
reach G3 and G4 through the machinery that already carries findings there.

**The formats are the tool's answer, not a list here.** `scan source -r` walks
a component and reads whatever manifests it recognises, so "whichever formats
the changed components actually use" is decided by the binary rather than by a
table that would silently stop matching the day someone adds a `pnpm-lock.yaml`.
Nothing in this file names an ecosystem.

**Five things measured on osv-scanner 2.5.1 rather than read in documentation:**

*Its walker prunes what git ignores, and the tree P5 asks it to scan is a tree
the harness has deliberately told git to ignore.* A run's worktree lives under
a `.gitignore` holding `*`, written by `worktree.claim_root` so that runs stop
dirtying the repository — so the scan root was pruned before the walk started
and this gate read **zero manifests on every run, on every platform**, from the
day it shipped. It never lied about it: "nothing was scanned" is not a pass,
which is the only reason a field run could find this at all. See `NO_IGNORE`.

*Exit 1 is findings, not failure* — the JSON is on stdout and parses. Exit 0 is
clean. Exit 127 is the tool refusing: an unreadable path, or a format it cannot
identify. The discriminator between a findings exit and a crash is whether
stdout parses, which is the rule `prepass.py` already applies to ast-grep.

*A `pom.xml` naming a module the public registry does not have FAILS THE WHOLE
SCAN.* The transitive resolver tries to fetch it and gives up with exit 127 —
so on any estate with internal, unpublished modules the gate would be
permanently broken rather than useful. The scan therefore retries with that one
resolver disabled and **records the reduction**: direct dependencies only, said
out loud, because a quietly narrowed gate reporting zero is the failure this
whole file exists to avoid.

*`results[]` carries only the sources that HAVE findings.* A clean scan and a
scan that read nothing produce the same empty array, so "nothing was scanned"
is taken from the tool's own `Scanned <path> ... found N packages` log line.
When nothing was scanned the status says so and is not a pass.

*`max_severity` is a CVSS SCORE, not a label.* rdjson has three severities and
`block_on` has five, so a 9.8 arriving as `ERROR` would become `high` and a
profile blocking on `critical` alone would let it through. Each diagnostic
therefore states its severity in `block_on`'s own vocabulary; a vulnerability
OSV has not scored is stated as UNGRADED, which holds the loop open rather than
being rounded to something convenient.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import cli  # noqa: E402
from tools.sdlc import stage as _contract  # noqa: E402
from tools.review import prepass  # noqa: E402

cli.use_utf8()

SCHEMA_VERSION = 1

#: The pinned binary's base name in `third_party/manifest.json`.
BINARY = "osv-scanner"

#: The name this tool reports itself under, in the pre-pass document's
#: vocabulary. `loop.assess` groups findings by it.
TOOL = "osv-scanner"

#: CVSS bands, tightest first. The standard v3 cut points, used because they are
#: the ones the scores were assigned against — inventing a different mapping
#: would make `block_on: [critical]` mean something no advisory database does.
CVSS_BANDS: tuple[tuple[float, str], ...] = (
    (9.0, "critical"), (7.0, "high"), (4.0, "medium"), (0.0, "low"))

#: The one plugin that turns an internal module into a failed scan. Disabled
#: only on the retry, so a workspace whose dependencies ARE published keeps its
#: transitive coverage.
TRANSITIVE_PLUGIN = "transitivedependency/pomxml"

#: osv-scanner's own log line naming each source it read. Parsed because the
#: JSON cannot answer "was anything scanned at all" — `results[]` holds only
#: sources with findings. Absence is tolerated: this supports the verdict, it
#: does not decide it.
_SCANNED = re.compile(r"^Scanned (.+?) file and found (\d+) package", re.M)

#: Defeats osv-scanner's git-ignore pruning. **Without it this gate scans
#: NOTHING on every run**, and the two halves of that are both the harness's
#: own doing: `worktree.claim_root` writes a `.gitignore` holding `*` into the
#: worktrees root — deliberately, so a run stops dirtying the repository it is
#: about — and osv-scanner's walker honours git's exclusions, so the scan root
#: P5 hands it is pruned before the walk begins. Measured on 2.5.1: the same
#: module scanned in place reads its manifest, and scanned inside a worktree
#: reports `1 dirs visited, 1 inodes visited, 0 Extract calls` and "No package
#: sources found". Isolated to one variable — a directory gained the pruning
#: the moment it was added to a `.gitignore` and lost it again with this flag.
#: `--include-git-root` was measured too and changes nothing here.
#:
#: The cost is stated rather than discovered: this also reads build output and
#: vendored trees a repository ignores on purpose, so the gate can now over-
#: report. It could only ever under-report before, and silently.
#:
#: The sibling tools were swept rather than assumed safe: `ast-grep` matched
#: inside an ignored tree and `gitleaks dir` scanned the identical byte count
#: there, so the review pre-pass is NOT affected and only this walker prunes.
NO_IGNORE = "--no-ignore"

#: What this gate's document is CALLED - asked of the phase contract, which is
#: the one place that names it. The two DID drift: this CLI wrote `sca.json`
#: while P5 looked for `50-sca.json`, so the stage contract's own remedy line
#: told an operator to run the exact command producing the name it would then
#: not find, and the gate's findings could never reach the phase that owes
#: them. Every test passed, because the fixture wrote the READER's name and
#: nothing composed the real writer with the real reader. `stage.py` holds it
#: because it is the only module every writer can import - it is embedded in
#: the dependency-free ledger check and can import almost nothing itself.
RESULT_NAME, REPORT_NAME = _contract.SCA_RESULT, _contract.SCA_REPORT

#: Environment that stops the scanner WRITING to the repository it audits.
#:
#: Measured, and it is the reason this constant exists rather than a worry:
#: `osv-scanner`'s transitive resolution SHELLS OUT to the ecosystem's own
#: toolchain, and Go before 1.16 updates `go.mod` as a side effect of resolving
#: it. A field scan of a real monorepo silently moved two dependency versions
#: and added an `indirect` line in a module the change under review never
#: touched - and a `git add -A` in the run's worktree committed the edit into
#: that change. Reproduced in isolation on a copy of the module: the file's
#: digest changes across a plain scan and does not change with `-mod=readonly`,
#: and the findings are identical either way (61 both runs), so prevention
#: costs no coverage.
#:
#: A gate that writes to the thing it is auditing is worse than one that
#: misreports: the misreport is visible in its own document, and the write is
#: not visible anywhere until somebody reads a diff carefully. `dirty_after`
#: below is the general answer, because this list can only name the toolchains
#: somebody has already been bitten by.
READ_ONLY_ENV: dict[str, str] = {
    # Go <1.16 rewrites go.mod during `go list`; readonly makes it refuse
    # instead. Harmless on newer Go, where it is already the default.
    "GOFLAGS": "-mod=readonly",
}

#: How long one component's scan may take. It queries the OSV API, so this is
#: network-bound rather than CPU-bound.
TIMEOUT = 600


def severity_of(max_severity: object) -> str:
    """A CVSS score in `block_on`'s vocabulary, or "" for ungraded.

    "" is not "low". OSV records a vulnerability with no score when nobody has
    assessed it, and a gate that reads that as the mildest thing it knows has
    decided the question it was asked to raise.
    """
    text = str(max_severity if max_severity is not None else "").strip()
    if not text:
        return ""
    try:
        score = float(text)
    except ValueError:
        return ""
    for floor, name in CVSS_BANDS:
        if score >= floor:
            return name
    return ""


#: rdjson's three levels, for readers that only speak that. The authoritative
#: value is `severity_class`; this is the lossy projection, kept so the document
#: stays a valid rdjson one.
_RDJSON = {"critical": "ERROR", "high": "ERROR", "medium": "WARNING",
           "low": "INFO", "": "WARNING"}

#: The per-finding identity `parse` attaches, and the profile key a repository
#: declares its accepted findings under. Both live here because this module
#: owns what an SCA finding IS; `suite.py` owns when one is owed.
QUARANTINE_FIELD = "sca"
QUARANTINE_KEY = "sca_known_findings"

#: A declared entry names ONE vulnerability in ONE package version, as four
#: whitespace-separated tokens, all of which must match:
#:
#:     <ecosystem> <package> <version> <advisory>
#:
#: The ecosystem is spelled however the SCANNER spells it. **No example here
#: names a real one**, and that is item 40's rule rather than shyness: the
#: formats this gate understands are the scanner's answer, and a name written
#: down here is one that silently stops matching the day the tool renames it.
#: The profile template carries worked examples, where they are documentation
#: for a human rather than a list the gate could come to rely on.
QUARANTINE_ARITY = 4


def names_of(diag: dict) -> set[str]:
    """Every advisory id this finding can honestly be called by."""
    ident = diag.get(QUARANTINE_FIELD)
    if not isinstance(ident, dict):
        return set()
    out = set()
    for key in ("ids", "aliases"):
        for value in ident.get(key) or []:
            if text := str(value).strip():
                out.add(text)
    return out


def coordinate(diag: dict) -> tuple[str, str, str] | None:
    """`(ecosystem, package, version)`, or None if this finding carries no
    identity — a document written before the identity existed, or by something
    that is not this module. **None is "cannot look", never "does not match"**:
    a caller that folded the two would quarantine findings nobody declared."""
    ident = diag.get(QUARANTINE_FIELD)
    if not isinstance(ident, dict):
        return None
    parts = tuple(str(ident.get(k, "")).strip()
                  for k in ("ecosystem", "package", "version"))
    return parts if all(parts) else None            # type: ignore[return-value]


def entry_for(diag: dict) -> str:
    """The canonical declaration for one finding, so a proposal and a
    hand-written entry are the same string. The CVE is preferred over `ids[0]`
    where one exists, because that is what a human reading an advisory has."""
    where = coordinate(diag)
    if where is None:
        return ""
    names = names_of(diag)
    cve = sorted(n for n in names if n.upper().startswith("CVE-"))
    ident = diag.get(QUARANTINE_FIELD) or {}
    first = [str(i).strip() for i in (ident.get("ids") or []) if str(i).strip()]
    advisory = cve[0] if cve else (first[0] if first else "")
    return " ".join([*where, advisory]).strip() if advisory else ""


def matches(entry: str, diag: dict) -> bool:
    """Does one declared entry name this finding?

    **All four tokens must match, and the version is one of them.** A quarantine
    that survived a version bump would be the thing this mechanism exists to
    prevent: an exception nobody re-examines, still standing over a dependency
    nobody has looked at since. Bumping the package invalidates the declaration
    and the finding blocks again, which is the moment somebody should decide.

    The advisory matches against ids AND aliases, so the entry may be spelled
    with the CVE even when OSV lists a `BIT-` or `PYSEC-` id first.
    """
    tokens = str(entry).split()
    if len(tokens) != QUARANTINE_ARITY:
        return False
    where = coordinate(diag)
    if where is None:
        return False
    *coord, advisory = tokens
    return tuple(coord) == where and advisory in names_of(diag)


def malformed(entries: list[str]) -> list[str]:
    """Declared entries that are not four tokens. Reported rather than ignored:
    an entry nobody can match is indistinguishable from one that matched, and
    the difference is whether a vulnerability is being watched."""
    return [e for e in entries if len(str(e).split()) != QUARANTINE_ARITY]


def partition(rows: list[dict], entries: list[str]) -> dict:
    """Split findings into those a declaration covers and those it does not.

    Returns `kept` (still owed), `quarantined` (declared, and reported as a
    count rather than dropped), and `stale` — declared entries that matched
    nothing. **Stale is the half this mechanism would rot without.** A
    quarantine that outlives its finding is a standing exception nobody
    re-examines, which is exactly what the declaration is supposed to replace.
    """
    usable = [e for e in entries if len(str(e).split()) == QUARANTINE_ARITY]
    kept: list[dict] = []
    quarantined: list[dict] = []
    used: set[str] = set()
    for row in rows:
        hit = next((e for e in usable if matches(e, row)), "")
        if hit:
            used.add(hit)
            quarantined.append(row)
        else:
            kept.append(row)
    return {"kept": kept, "quarantined": quarantined,
            "stale": [e for e in usable if e not in used]}


def filter_document(doc: dict, entries: list[str]) -> dict:
    """A copy of an SCA document with declared findings removed, plus what was
    removed and which declarations matched nothing.

    **This operates on the document's own diagnostics rather than on flattened
    rows, and that is the whole reason it exists.** `loop.from_prepass` builds
    each row with a fixed key set, so the identity `parse` attaches does not
    survive the flattening - a partition applied afterwards sees no identity on
    anything, matches nothing, and reports every declared finding as still
    owed. Producer and consumer each correct alone and never composed, which is
    the class this project keeps meeting; here they are composed by construction
    because the filtering happens before the flattening.
    """
    sources = []
    quarantined: list[dict] = []
    used: set[str] = set()
    usable = [e for e in entries if len(str(e).split()) == QUARANTINE_ARITY]
    for source in doc.get("sources") or []:
        if not isinstance(source, dict):
            sources.append(source)
            continue
        rows = [d for d in (source.get("diagnostics") or []) if isinstance(d, dict)]
        split = partition(rows, usable)
        quarantined += split["quarantined"]
        used |= {e for e in usable if e not in split["stale"]}
        sources.append({**source, "diagnostics": split["kept"]})
    return {"doc": {**doc, "sources": sources}, "quarantined": quarantined,
            "stale": [e for e in usable if e not in used]}



def parse(report: dict, root: Path) -> list[dict]:
    """An osv-scanner JSON report → rdjson diagnostics carrying an explicit
    severity.

    One diagnostic per (package, advisory group): a group is the set of ids
    describing ONE vulnerability across databases, so emitting a row per id
    would report a single CVE three times and make the count a measure of how
    many databases carry it.

    The location is the manifest at line 1. A vulnerability is a property of a
    resolved dependency rather than of a line, and pointing at a plausible line
    inside a lockfile would be a citation nobody can check.
    """
    out: list[dict] = []
    # `or []`, not a `.get` default: measured on 2.5.1, a scan with no findings
    # emits `"results": null` — the key is PRESENT and the default never fires.
    # The field run crashed here on the commonest case in the world, a clean
    # scan, because the canned fixture spelled the shape more optimistically
    # than the tool does.
    for result in report.get("results") or []:
        path = prepass.rel_to(str((result.get("source") or {}).get("path", "")), root)
        for entry in result.get("packages") or []:
            pkg = entry.get("package") or {}
            name = str(pkg.get("name", "?"))
            version = str(pkg.get("version", "?"))
            ecosystem = str(pkg.get("ecosystem", "?"))
            for group in entry.get("groups", []) or []:
                ids = [str(i) for i in (group.get("ids") or [])]
                aliases = [str(a) for a in (group.get("aliases") or [])]
                grade = severity_of(group.get("max_severity"))
                shown = ", ".join(dict.fromkeys(aliases or ids)) or "(unnamed)"
                out.append({
                    "message": (f"{ecosystem} {name} {version} is affected by "
                                f"{shown}" +
                                (f" (CVSS {group.get('max_severity')})" if grade
                                 else " — no CVSS score recorded, so this is "
                                      "UNGRADED and holds the loop open")),
                    "location": {"path": path,
                                 "range": {"start": {"line": 1, "column": 1}}},
                    "severity": _RDJSON[grade],
                    "severity_class": grade,
                    "code": {"value": ids[0] if ids else "OSV",
                             "url": f"https://osv.dev/vulnerability/{ids[0]}"
                                    if ids else ""},
                    # The identity a quarantine names, structured rather than
                    # left in the message. `code.value` is `ids[0]`, which is
                    # whichever database OSV happened to list first - on a real
                    # estate that is regularly a `BIT-` id while the human
                    # writing the declaration has the CVE in front of them. A
                    # declaration that could only name `ids[0]` would be a
                    # declaration nobody can spell, so every alias is carried.
                    QUARANTINE_FIELD: {"ecosystem": ecosystem, "package": name,
                                       "version": version, "ids": ids,
                                       "aliases": aliases},
                })
    return out


def scanned_paths(stderr: str, root: Path) -> list[str]:
    """Every source the tool says it read, from its own log."""
    return sorted({prepass.rel_to(m.group(1), root)
                   for m in _SCANNED.finditer(stderr or "")})


def _invoke(exe: Path, where: Path, disable: str = "") -> tuple[int, str, str]:
    cmd = [str(exe), "scan", "source", "-r", "--allow-no-lockfiles",
           NO_IGNORE, "--format", "json"]
    if disable:
        cmd += ["--experimental-disable-plugins", disable]
    cmd.append(str(where))
    return prepass.run_tool(cmd, timeout=TIMEOUT, env=READ_ONLY_ENV)


def tracked_state(where: Path) -> str | None:
    """What git says about this tree, or None when it cannot be asked.

    Used to answer one question either side of a scan: **did the scanner write
    to the repository it was auditing?** `READ_ONLY_ENV` prevents the case that
    was measured; this catches the ones that have not been, because a scanner
    that shells out to arbitrary build toolchains can be made to write in ways
    no constant here can enumerate in advance.

    None rather than "" for an unanswerable question - a target that is not a
    git repository is a different fact from a clean one, and reporting "no
    change" for a tree nobody could look at is the whole failure mode this
    harness is written against.
    """
    code, out, _, _note = cli.run_bounded(
        ["git", "-C", str(where), "status", "--porcelain"], timeout=120)
    return out if code == 0 else None


def dirty_after(where: Path, before: str | None, after: str | None) -> list[str]:
    """Paths the scan changed: what git reports now and did not report before.

    A DELTA, not a state. The tree may have been dirty when the scan started -
    a run's worktree usually is - and blaming the scanner for that would make
    this warning noise, which is how a real one gets ignored.
    """
    if before is None or after is None:
        return []
    was = set(before.splitlines())
    return sorted(line[3:].strip() for line in after.splitlines()
                  if line not in was and len(line) > 3)


def _mine_apart(rows: list, target: Path, keep, path_of=None) -> tuple[list, list]:
    """Split rows into (the repository's, the harness's own). Never drops.

    One helper for both shapes this gate carries - a list of manifest paths and
    a list of diagnostics - because two spellings of one rule is how a reader
    and a writer come to disagree about the same document.
    """
    ours, mine = [], []
    for row in rows:
        where = path_of(row) if path_of else row
        (mine if keep(target, where) else ours).append(row)
    return ours, mine


def scan_one(exe: Path, where: Path, root: Path) -> dict:
    """Scan one component. Returns its tool row and its diagnostics."""
    before = tracked_state(where)
    code, out, err = _invoke(exe, where)
    if code is None:
        # The clock ended it, so there is no exit status to read and nothing to
        # retry: the plugin retry below exists for a resolver that FAILED, and
        # running the same hung command again only spends the timeout twice.
        # What it printed is kept - see `prepass.hung_row` - because a scanner
        # that hangs on one component is diagnosed from its own last words.
        return {"row": dict(prepass.hung_row(where.name, out, err,
                                             scanned=[], degraded="",
                                             mutated=[], own_trees=0),
                            component=where.name),
                "diagnostics": []}
    degraded = ""
    if code not in (0, 1):
        # Measured: an internal module the public registry cannot resolve fails
        # the WHOLE scan. Retrying without that one resolver keeps the gate
        # usable on an estate, at a cost this row states.
        retry_code, retry_out, retry_err = _invoke(exe, where, TRANSITIVE_PLUGIN)
        if retry_code in (0, 1):
            code, out, err = retry_code, retry_out, retry_err
            degraded = (f"transitive resolution failed, so this component was "
                        f"scanned for DIRECT dependencies only - a transitively "
                        f"vulnerable package would not be found here. Publish the "
                        f"internal coordinates, or commit a resolved lockfile")
    report = None
    if code in (0, 1):
        try:
            report = json.loads(out or "{}")
        except ValueError:
            report = None
    if not isinstance(report, dict):
        tail = (err or "").strip().splitlines()
        return {"row": {"component": where.name, "status": "error",
                        "detail": f"exit {code}: {tail[-1] if tail else 'no output'}",
                        "findings": 0, "scanned": [], "degraded": degraded},
                "diagnostics": []}
    read = scanned_paths(err, root)
    diags = parse(report, root)
    # A worktree is a full COPY of the repository, so every manifest in one is
    # a second report of a fact already reported at its real path - at an
    # address that does not exist in the tree a reviewer is looking at. The
    # count moves when a run is opened or closed with nothing about the code
    # having changed, which is how 36 manifests became 72 and then 108.
    #
    # Dropped at the READER and not only at the scanner, because `--no-ignore`
    # is what lets the walker in and the exclusion flag that would keep it out
    # is marked experimental upstream. A correctness rule may not rest on a
    # flag whose name is documented as provisional.
    #
    # Counted, never silently dropped: `own_trees` says how many and why.
    from tools.sdlc import worktree as trees

    read, mine = _mine_apart(read, target=root, keep=trees.is_own_tree)
    diags, mine_diags = _mine_apart(
        diags, target=root, keep=trees.is_own_tree,
        path_of=lambda d: d["location"]["path"])
    own_trees = len(mine) + len(mine_diags)
    status = "ok" if read else "nothing-scanned"
    detail = degraded
    if own_trees:
        detail = (detail + " | " if detail else "") + (
            f"{len(mine)} manifest(s) and {len(mine_diags)} finding(s) were "
            f"dropped: they are in this harness's own worktree copies of the "
            f"repository, which are duplicates at paths the repository does "
            f"not have")
    if not read:
        detail = (detail + " | " if detail else "") + (
            "osv-scanner read no dependency manifest here - it reports only "
            "sources with findings, so this is 'nothing was scanned', which is "
            "not the same as clean")
    mutated = dirty_after(where, before, tracked_state(where))
    if mutated:
        named = ", ".join(mutated[:5])
        more = "" if len(mutated) <= 5 else f" (+{len(mutated) - 5} more)"
        detail = (detail + " | " if detail else "") + (
            f"THE SCAN WROTE TO THIS TREE: {named}{more}. A gate that edits what "
            f"it audits puts changes nobody made into whatever is committed next; "
            f"restore these before continuing and treat the result as suspect")
    return {"row": {"component": where.name, "status": status, "detail": detail,
                    "findings": len(diags), "scanned": read,
                    "degraded": degraded, "mutated": mutated,
                    "own_trees": own_trees},
            "diagnostics": diags}


def run(target: Path, components: list[dict]) -> dict:
    """The SCA gate over the named components, as a pre-pass-shaped document."""
    target = Path(target)
    exe, why = prepass.binary(BINARY)
    tools_out: list[dict] = []
    sources_out: list[dict] = []
    if exe is None:
        # The vendored-binary doctrine, and it matters most here: an empty
        # result from a scanner that never ran reads exactly like "no known
        # vulnerabilities".
        tools_out.append({"name": TOOL, "status": "not-run", "detail": why,
                          "findings": 0, "components": [c["name"] for c in components]})
        return _document(target, tools_out, sources_out, components)
    per: list[dict] = []
    diags: list[dict] = []
    for comp in components:
        where = target / (comp.get("path") or ".")
        if not where.is_dir():
            per.append({"component": comp["name"], "status": "error",
                        "detail": f"{where} is not a directory", "findings": 0,
                        "scanned": [], "degraded": ""})
            continue
        found = scan_one(exe, where, target)
        per.append(found["row"])
        diags += found["diagnostics"]
    # `not per` - an EMPTY component set is "nothing anywhere was read", and
    # the previous spelling (`per and all(...)`) fell through it to `ok`, so
    # `--components ,` produced a green supply-chain gate that ran no scanner.
    #
    # A MIXED set stays `ok` deliberately, and that is not a fold: a charts or
    # docs component legitimately has no dependencies, and exit 3 for every
    # estate that owns one is a gate people learn to ignore. Partial coverage
    # is not an unfired gate. It is carried in `per_component[]` and REPORTED
    # by `suite.sca_problems`, which is the reader that owes P5 an answer -
    # and which, until now, promised that report and did not make it.
    # A component the CLOCK ended is `not-run`, and it must not fall through to
    # `ok` beside components that answered: nobody looked at that one, which is
    # this harness's ABSENT-vs-COULD-NOT-LOOK rule and the reason the gate has
    # a failing status at all.
    worst = ("error" if any(r["status"] in ("error", "not-run") for r in per)
             else "nothing-scanned"
             if not per or all(r["status"] == "nothing-scanned" for r in per)
             else prepass.STATUS_OK)
    # A COUNT at the tool level and the specifics on the rows. Joining every
    # component's detail here produced one wall of text that read as if all of
    # it applied to the whole scan — the field run's own output argued this.
    notes = []
    if degraded := [r["component"] for r in per if r["degraded"]]:
        notes.append(f"{len(degraded)} component(s) scanned for direct "
                     f"dependencies only ({', '.join(degraded)})")
    if quiet := [r["component"] for r in per if r["status"] == "nothing-scanned"]:
        notes.append(f"{len(quiet)} component(s) had no manifest this scanner "
                     f"reads ({', '.join(quiet)})")
    if broke := [r["component"] for r in per if r["status"] == "error"]:
        notes.append(f"{len(broke)} component(s) errored ({', '.join(broke)})")
    if hung := [r["component"] for r in per if r["status"] == "not-run"]:
        notes.append(f"{len(hung)} component(s) were not scanned to completion "
                     f"({', '.join(hung)})")
    tools_out.append({"name": TOOL, "status": worst,
                      "detail": "; ".join(notes),
                      "findings": len(diags),
                      "components": [r["component"] for r in per],
                      "per_component": per})
    if diags:
        sources_out.append({"source": {"name": TOOL}, "diagnostics": diags})
    return _document(target, tools_out, sources_out, components)


def _document(target: Path, tools_out: list[dict], sources_out: list[dict],
              components: list[dict]) -> dict:
    by_class: dict[str, int] = {}
    for source in sources_out:
        for diag in source["diagnostics"]:
            key = diag.get("severity_class") or "(ungraded)"
            by_class[key] = by_class.get(key, 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        # The target tree's NAME, not its absolute path: 50-sca.json is
        # committed, so an absolute path here would carry one machine's layout
        # into another checkout (class 6). It sits beside component NAMES for the
        # same reason, and nothing reads it back as a path.
        "scope": {"mode": "components", "target": Path(target).resolve().name,
                  "components": [c["name"] for c in components]},
        "tools": tools_out,
        "sources": sources_out,
        "summary": {"total": sum(t["findings"] for t in tools_out),
                    "by_severity": dict(sorted(by_class.items()))},
    }


def propose(result: dict) -> str:
    """Draft declarations for everything this scan found, decisions left to a
    human.

    Modelled on `--propose-canonical`: the facts are pre-filled and the
    JUDGEMENT is not. Nothing is written to the profile — this prints, and a
    person pastes what they accept. A tool that quarantined its own findings
    would be a gate marking its own homework.

    Deduplicated, because the identity carries no path: one CVE in one package
    version is one decision however many manifests resolve it.
    """
    rows = [d for source in result.get("sources") or []
            for d in (source.get("diagnostics") or []) if isinstance(d, dict)]
    seen: dict[str, str] = {}
    unnameable = 0
    for row in rows:
        entry = entry_for(row)
        if not entry:
            unnameable += 1
            continue
        seen.setdefault(entry, str(row.get("severity_class") or "ungraded"))
    if not seen:
        return (f"# no finding in this scan can be named, so there is nothing "
                f"to declare ({unnameable} carried no identity)\n")
    out = [f"# {QUARANTINE_KEY}: DRAFT for {len(seen)} finding(s), from a scan "
           f"of {len(rows)} row(s).",
           "# Every line below is a decision nobody has taken yet. Delete the "
           "ones you intend",
           "# to FIX; keep only what you are accepting, and accept nothing you "
           "have not read.",
           "# A version bump invalidates its line and the finding blocks "
           "again - deliberately.",
           f"{QUARANTINE_KEY}:"]
    for entry, grade in sorted(seen.items(), key=lambda kv: (kv[1], kv[0])):
        out.append(f'  - "{entry}"    # {grade}')
    if unnameable:
        out.append(f"# {unnameable} finding(s) carried no identity and cannot "
                   f"be declared - they will keep blocking, which is correct.")
    return "\n".join(out) + "\n"


def render_md(result: dict) -> str:
    lines = ["# SCA gate", "",
             f"Components: {', '.join(result['scope']['components']) or '(none)'}", ""]
    for tool in result["tools"]:
        lines.append(f"- **{tool['name']}** — {tool['status']}, "
                     f"{tool['findings']} finding(s)"
                     + (f": {tool['detail']}" if tool["detail"] else ""))
        for row in tool.get("per_component", []):
            lines.append(f"    - {row['component']}: {row['status']}, "
                         f"{row['findings']} finding(s), "
                         f"{len(row['scanned'])} manifest(s) read")
    for source in result["sources"]:
        lines.append("")
        for diag in source["diagnostics"]:
            grade = diag.get("severity_class") or "UNGRADED"
            lines.append(f"- `[{grade}]` {diag['location']['path']} — "
                         f"{diag['message']}")
    return "\n".join(lines) + "\n"


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", default=".", metavar="DIR")
    ap.add_argument("--components", metavar="NAMES",
                    help="comma-separated component names (default: all)")
    ap.add_argument("--out", metavar="DIR",
                    help=f"write {RESULT_NAME} and {REPORT_NAME} here")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--propose-quarantine", action="store_true",
                    help=f"draft {QUARANTINE_KEY} entries for what this scan "
                         f"found, to be edited and pasted into the profile")
    args = ap.parse_args(argv)

    from tools.sdlc import policy as guardrails

    target = Path(args.target).resolve()
    every = guardrails.components(target)
    wanted = ([c.strip() for c in args.components.split(",") if c.strip()]
              if args.components else None)
    if wanted is not None:
        known = {c["name"] for c in every}
        if unknown := sorted(set(wanted) - known):
            print(f"refused: no component named {', '.join(map(repr, unknown))} - "
                  f"one of {', '.join(sorted(known))}", file=sys.stderr)
            return 2
        every = [c for c in every if c["name"] in wanted]
    result = run(target, every)
    # Exit 3 outranks exit 1, the rule `prepass.py` states - and now the rule
    # `prepass.py` OWNS. This was a deny-list of three named statuses beside a
    # comment claiming to be that rule; on a status neither anticipated the two
    # gave opposite answers, and a deny-list is silent about every status added
    # after it was written. `nothing-scanned` was itself added that way.
    # `unreviewed`, not `unfired`: the second is one of the three rules the
    # first applies, and taking it alone is how this file and prepass.py
    # disagreed before. `expected` names the scanner this gate requires to
    # be present, so a document that lost its row is refused rather than
    # read as a scan with no findings.
    #
    # Asked ONCE, ABOVE every branch, because it used to be asked below the
    # propose branch: `--propose-quarantine` on a scan that never ran exited 0
    # printing "nothing to declare" while `--json` on the same document exited
    # 3. One document, two verdicts, and the exit-0 one was the could-not-look
    # / measured-zero fold. `propose()` cannot make this distinction on its own
    # - it reads `sources[]` and never the tool row's status - so the refusal
    # has to sit where no exit path can get past it.
    refused = prepass.unreviewed(result, expected=(TOOL,))
    if args.propose_quarantine:
        if refused:
            # To stderr, and named: stdout for this flag is a draft somebody
            # pastes into a profile, and a refusal that lands there becomes a
            # comment in a document that says the scan was clean.
            print("refused: " + "; ".join(refused), file=sys.stderr)
            return 3
        print(propose(result), end="")
        return 1 if result["summary"]["total"] else 0
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / RESULT_NAME).write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8", newline="\n")
        (out / REPORT_NAME).write_text(render_md(result), encoding="utf-8",
                                       newline="\n")
        print(f"sca: wrote {out / RESULT_NAME} and {REPORT_NAME}")
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(render_md(result), end="")
    if refused:
        return 3
    return 1 if result["summary"]["total"] else 0


def main(argv: list[str] | None = None) -> int:
    """The door. One implementation, in `tools/cli.py` - see it for what is
    caught and why. `3` is written here, literally, because the code that
    means *could not look* is this module's vocabulary, not the door's."""
    return cli.door(_main, argv, "the SCA gate", 3)


if __name__ == "__main__":
    raise SystemExit(main())
