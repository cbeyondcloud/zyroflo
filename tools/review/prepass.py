"""Deterministic review pre-pass: what a machine finds before an agent reads code.

Item 25. Two vendored, pinned tools run over the change and their findings land in
one rdjson-style document BEFORE agent review starts: `gitleaks` (the mandatory
secret gate — a leak is a finding no reviewer may miss because they were reading
logic) and `ast-grep` (rule matches: the harness's small universal set plus the
repository's own rules under `.sdlc/review-rules/`). The agent review then starts
from machine facts, and the two never blur: a pre-pass diagnostic cites tool, rule
and location; an agent finding cites reasoning.

Three rules, each load-bearing:

* **A tool that could not run is a stated status and exit 3, never a silent
  clean.** The vendored-binary doctrine: an empty result from a tool that did not
  execute reads exactly like "no findings", and for a SECRET gate that misreading
  is the worst one available. Exit 3 outranks exit 1 — findings from the tool that
  ran do not make the change reviewed while the other gate never fired.
* **Secrets are redacted at the boundary.** gitleaks reports carry the matched
  secret verbatim (measured on the real binary); this document travels into review
  artifacts and PR comments, so diagnostics keep rule, file:line and fingerprint
  and NEVER the matched text — a leak report that re-publishes the leak is a
  second incident, not a finding.
* **Scope is the change.** With a diff, the touched files' current content is
  copied into a scratch tree and scanned there, plus the diff text itself as
  `_diff.patch` — a secret visible in the hunks but already edited out of the
  file must still block, because the diff is what gets pushed.

Line numbers: ast-grep's JSON is zero-based (measured, 0.45.1); rdjson and every
graph artifact in this repository are one-based, so the converter adds one.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import cli  # noqa: E402
from tools.sdlc import stage as _contract  # noqa: E402  (after the path bootstrap)

# CLAUDE.md: "Reconfigure stdout to UTF-8". This module had no such
# call, and its crash handler prints an arbitrary exception message.
cli.use_utf8()

SCHEMA_VERSION = 1

#: The harness's default rule set — deliberately tiny and universally defensible;
#: anything opinionated belongs in the repository's own .sdlc/review-rules/.
RULES_DIR = Path(__file__).resolve().parent / "rules"
REPO_RULES = Path(".sdlc") / "review-rules"

#: gitleaks exit code requested for "leaks found", distinct from its error exit 1.
LEAK_EXIT = 9

#: The suffix that marks a source as CONTEXT (pre-existing findings), not gate
#: material. Spelled ONCE: the own-tree de-dup recovers a source's tool by
#: stripping it, and the severity summary skips it, so a second copy is exactly
#: how T113 lost findings when the source name and the tool row name drifted.
PRE_EXISTING_SUFFIX = " (pre-existing)"

# ── the document's own vocabulary ────────────────────────────────────────────
#
# This module WRITES the `tools[]` document, so it owns the words in it. Three
# readers had each restated them instead: `exit_code()` below, `sca.py` (which
# emits the same shape for item 40's gate), and `provider_smoke.verify()`,
# which hard-coded the pair of gate names AND the ok status. Restating a
# vocabulary is how two copies of one rule end up disagreeing, and both of
# these did - measurably, not hypothetically:
#
#   * `sca.py` asked the question as a DENY-LIST of three named bad statuses
#     while its own comment claimed to be "the rule prepass.py states". On a
#     status neither list anticipated, the allow-list said 3 and the deny-list
#     said 0. A deny-list is silent about every status added after it.
#   * `verify()` checked exactly ("gitleaks", "ast-grep"), so a THIRD gate that
#     never fired was invisible to it: `exit_code()` returned 3 for that
#     document while the smoke recorded the run GREEN.
GATES = ("gitleaks", "ast-grep")
STATUS_OK = "ok"

#: What this pass's documents are CALLED, and the one place that decides it.
#: `provider_smoke` opens `prepass.json` by name, and until this constant
#: existed the filename was a bare literal at three sites here and four there -
#: the same latent drift that had already broken the SCA gate, where the writer
#: said `sca.json` and the phase looked for `50-sca.json` and every test passed
#: because the fixture spelled the reader's name. Not drifted yet; the point is
#: that nothing was stopping it.
#: Asked of the phase contract rather than spelled again. `stage.py` is the
#: only module every writer can import - it is embedded in the
#: dependency-free ledger check, so it can import almost nothing itself.
#: One home per name; the import graph decides which module gets to be it.
RESULT_NAME, REPORT_NAME = (_contract.PREPASS_RESULT,
                            _contract.PREPASS_REPORT)


def tool_rows(result: dict) -> list[dict] | None:
    """The `tools[]` rows, or None when the document has no readable list
    there at all.

    None is NOT `[]`, and the difference is the whole point: an empty list is
    a document that ran nothing, a missing or malformed one is a document
    nobody can conclude from, and folding them together is how empty comes to
    read as clean. Item 40 met the shape in the field - a clean osv-scanner
    scan emits `"results": null`, and a present null is not a missing key, so
    a `.get("tools", [])` default never fires for it.

    A row that is not a mapping becomes an unreadable row rather than
    disappearing: it then holds the gate shut, which is the safe direction.

    `result` itself is checked too. The first cut guarded the field and the
    row and left the top-level object, so a `prepass.json` holding `[]` — which
    any live runner can write — raised AttributeError out of every caller."""
    if not isinstance(result, dict):
        return None
    tools = result.get("tools")
    if not isinstance(tools, list):
        return None
    return [row if isinstance(row, dict) else {"name": None, "status": "unreadable"}
            for row in tools]


def unfired(result: dict) -> list[dict]:
    """The tool rows in a `tools[]` document that did NOT fire.

    One rule, and deliberately an ALLOW-LIST: a status that is not `ok` did not
    fire, whatever it is called. Nothing has to be edited when a status is
    added, which is the whole difference from the deny-list this replaced.

    Reads any document of this shape, not only the one this module writes -
    `sca.py` emits the same `tools[]` and asks the same question of it.
    """
    return [row for row in (tool_rows(result) or [])
            if row.get("status") != STATUS_OK]


def missing_gates(result: dict,
                  expected: tuple[str, ...] | None = None) -> list[str]:
    """Gates that are not in the document at all.

    Absent and failed are different findings, and a reader that folds them
    together cannot say which happened. Only a caller that knows which gates
    OUGHT to be there asks this; `exit_code()` does not, because a document
    this module just wrote contains exactly what it ran.

    `expected` defaults to None rather than to GATES because a default
    argument is evaluated ONCE, at definition: written `expected=GATES`, every
    caller got a SNAPSHOT of the tuple taken at import, which is the same copy
    this whole change removes, one level down. Its own test caught it.
    """
    expected = GATES if expected is None else expected
    present = {row.get("name") for row in (tool_rows(result) or [])}
    return [gate for gate in expected if gate not in present]


def unreviewed(result: dict, expected: tuple[str, ...] = ()) -> list[str]:
    """Every reason this document does not evidence a review, in ONE call.

    The three rules below were previously spread across `exit_code()` and
    `provider_smoke.verify()`, and `verify()` adopted two of them: it asked
    about missing gates and unfired gates and never about
    `scope.empty_after_exclude`. A change whose every file was folded out by
    `review_exclude` therefore recorded a GREEN smoke while `exit_code()`
    returned 3 for the identical document. A rule split across callers is a
    rule some caller will adopt in halves, so there is now one function and
    the callers have nothing left to choose between.

    `expected` is the gates the CALLER requires to be present. It is empty by
    default because the answer differs by caller and no default is right for
    all of them: this module's own `exit_code` requires none (a report it just
    wrote contains exactly what it ran), the smoke requires `GATES`, and
    `sca.py` requires its own scanner.

    Returns reasons, not a bool, so a caller can say WHICH rule refused.
    """
    rows = tool_rows(result)
    if rows is None:
        return ["the report has no readable tools[] list, so nothing in it "
                "can be called clean"]
    out = [f"gate {gate!r} is not in the report at all - it cannot say "
           f"whether it fired" for gate in missing_gates(result, expected)]
    out += [f"gate {row.get('name')!r} did not fire (status: "
            f"{row.get('status')!r})" for row in unfired(result)]
    scope = result.get("scope")
    if isinstance(scope, dict) and scope.get("empty_after_exclude"):
        out.append("every changed file was folded out by review_exclude - "
                   "nothing was reviewed")
    return out


def run_tool(cmd: list[str], timeout: int = 300,
             env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Run one vendored tool. The seam tests replace; the program is absolute.

    `env` is MERGED onto the caller's environment rather than replacing it: a
    scanner needs PATH, HOME and the platform's own variables to find the
    toolchains it shells out to, and handing it a bare dict would change what it
    can see as well as how it behaves.

    **A tool the CLOCK ended keeps what it printed.** This was a bare
    `subprocess.run(..., timeout=...)`, so `TimeoutExpired` escaped `prepass()`
    — whose docstring says it never raises for tool trouble — carrying the
    scanner's partial output away with it, taking the SIBLING gate's row and
    the document itself. `cli.run_bounded` is the same helper the four
    phases outside `review/` adopted; the exit code is None when no exit
    status exists, and the reason leads stderr so a reader taking a leading
    slice still sees why the output stops where it does.
    """
    merged = None if env is None else {**os.environ, **env}
    code, out, err, note = cli.run_bounded(
        cmd, env=merged, timeout=timeout,
        timed_out="not run to completion: the tool did not finish "
                  "within {timeout}s",
        could_not_start="not run: the tool could not be started: {error}")
    return code, out, cli.noted(note, err)


#: How much of a hung tool's own output a row carries. Enough to see what it
#: was doing when the clock ran out; not so much that one row becomes the
#: document. The tail is the part worth keeping - a scanner prints its progress
#: - and the note is prepended AFTERWARDS, so slicing can never cut it off.
HUNG_TAIL = 400


def hung_row(name: str, out: str, err: str, **extra) -> dict:
    """The tool row for a scan the CLOCK ended: `not-run`, and what it printed.

    `not-run` rather than `error`, because nothing came back to judge: this is
    the COULD-NOT-LOOK answer, and `exit_code` already refuses to call a
    document holding one a pass. What the scanner managed to say before it was
    killed is the only evidence anybody has about why, so it is kept here
    rather than thrown away with the exception that used to carry it.
    """
    return {"name": name, "status": "not-run", "findings": 0,
            "detail": cli.noted(err.strip()[:HUNG_TAIL],
                                out.strip()[-HUNG_TAIL:]), **extra}


def binary(name: str) -> tuple[Path | None, str]:
    """The vendored binary's absolute path, or (None, how-to-get-it)."""
    from tools import third_party as tp
    plat = tp.current_platform()
    exe = tp.bin_dir(plat) / (f"{name}.exe" if plat.startswith("windows") else name)
    if not exe.exists():
        # T544: named where it sits in the harness, not by this machine's path. The
        # detail lands in the committed prepass.json, and an absolute path carries the
        # operator's home directory into another repository - `scope.root` below
        # strips the target to its name for the same reason (class 6).
        harness = Path(__file__).resolve().parents[2]
        try:
            where = exe.resolve().relative_to(harness).as_posix()
        except ValueError:
            where = exe.name
        return None, (f"not run: vendored {name} missing at {where} "
                      f"(fetch: python tools/third_party.py --fetch)")
    return exe, ""


def rel_to(path_text: str, root: Path) -> str:
    """A tool-reported absolute path as a forward-slash path relative to root."""
    p = Path(path_text.replace("\\", "/"))
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def parse_gitleaks(rows: list[dict], root: Path) -> list[dict]:
    """gitleaks report rows → redacted rdjson diagnostics.

    `Match`/`Secret` are dropped on purpose — see the module docstring. The
    fingerprint (file:rule:line) survives: it identifies the leak without
    containing it, and it is what a suppression would reference.
    """
    out = []
    for r in rows:
        out.append({
            "message": r.get("Description", r.get("RuleID", "secret detected")),
            "location": {"path": rel_to(r.get("File", ""), root),
                         "range": {"start": {"line": int(r.get("StartLine", 0)),
                                             "column": int(r.get("StartColumn", 0))}}},
            "severity": "ERROR",
            "code": {"value": r.get("RuleID", "")},
            "fingerprint": rel_to(r.get("Fingerprint", ""), root),
        })
    return out


def parse_astgrep(rows: list[dict], root: Path) -> list[dict]:
    """ast-grep --json rows → rdjson diagnostics. Lines arrive zero-based.

    T397: `.get(key, default)` only applies its default for an ABSENT key — a
    PRESENT null sails past it. `str(r.get("severity", "warning")).upper()`
    used to do exactly that: a present-null severity became `str(None)` ==
    "None" -> "NONE", a class no rule ever stated, which the reader's T394
    guard cannot catch because by then it is a genuine string. `severity` and
    `message` are fixed the same way — a present null takes the SAME default
    an absent key gets. MEASURED for T397 against the real pinned ast-grep
    0.45.1: today's tool never actually sends either as null (an omitted rule
    field gets the TOOL's own default, "hint"/""; an explicit `severity: null`
    fails the tool's rule-config parse before any row exists) — but the
    parser must not depend on that staying true.

    `range`/`start`/`line`/`column` are NOT hardened the same way and are
    named safe rather than fixed: they are the match's own location, which
    ast-grep always populates for a produced match (there is no matchless
    match), so a present null there would be a schema violation the tool
    itself does not produce; if it ever did, `int(None)` raises loudly
    (TypeError/AttributeError) rather than silently manufacturing a
    plausible-looking wrong value — a different, louder failure class than
    the one this row addresses.
    """
    out = []
    for r in rows:
        start = r.get("range", {}).get("start", {})
        severity = r.get("severity")
        message = r.get("message")
        out.append({
            "message": message if message is not None else "",
            "location": {"path": rel_to(r.get("file", ""), root),
                         "range": {"start": {"line": int(start.get("line", 0)) + 1,
                                             "column": int(start.get("column", 0)) + 1}}},
            "severity": str(severity if severity is not None else "warning").upper(),
            "code": {"value": r.get("ruleId", "")},
        })
    return out


def collect_rules(target: Path, extra_dirs: list[Path]) -> tuple[str, list[str]]:
    """Every rule document, joined with `---` for one --inline-rules invocation.

    Order: harness defaults, the repository's own `.sdlc/review-rules/`, then
    --rules-dir extras — later files can shadow by id at ast-grep's discretion,
    and the sources list in the output says exactly what ran.
    """
    docs: list[str] = []
    sources: list[str] = []
    for d in [RULES_DIR, target / REPO_RULES, *extra_dirs]:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.yml")) + sorted(d.glob("*.yaml")):
            docs.append(f.read_text(encoding="utf-8").strip())
            sources.append(f.name if f.parent == RULES_DIR
                           else rel_to(str(f), target))
    return "\n---\n".join(docs), sources


def excluded_by(target: Path) -> list[str]:
    """`review.review_exclude` from the profile — the globs a repository folds
    out of review (lockfiles, vendored trees, generated code).

    It shipped in the template at item 60 and until now NOTHING READ IT, which
    is the worse of the two failures available: a reviewer who believes the
    lockfile was folded out reads a diff they think is smaller than it is.
    """
    from tools.sdlc import policy as guardrails
    listed = guardrails.profile_list(target, "review_exclude")
    return [str(v) for v in listed] if listed else []


def touched_lines(diff_text: str) -> dict[str, list[tuple[int, int]] | None]:
    """`{path: changed new-side ranges, or None for \"the whole file\"}`.

    `impact.parse_diff` is the one diff grammar this project has (item 20), and
    it already answers this - `diff_scope` was computing it and keeping only the
    keys. None means *touched, with no line information*: a deleted file, a
    mode-only change, a path the grammar declines to parse. A rule finding in
    such a file is attributed to the change, because nothing here can say
    otherwise and guessing the other way would hide a real one.
    """
    from tools.graph.impact import parse_diff

    return parse_diff(diff_text)


def in_change(diag: dict, ranges: dict[str, list[tuple[int, int]] | None]) -> bool:
    """Whether this diagnostic sits inside a line the change touched.

    A file the diff never mentions is not the change's (the pre-pass may scan a
    whole tree). A file it touched with no line information counts as the
    change's - see `touched_lines`.
    """
    where = (diag.get("location") or {})
    rel = (where.get("path") or "").replace("\\", "/")
    if rel not in ranges:
        return False
    spans = ranges[rel]
    if spans is None:
        return True
    line = ((where.get("range") or {}).get("start") or {}).get("line")
    if not isinstance(line, int):
        # A finding with no line cannot be placed. Attributed to the change
        # rather than dropped: this file was touched, and a rule finding
        # nobody sees is worse than one somebody re-reads.
        return True
    return any(start <= line <= end for start, end in spans)


def partition(diags: list[dict],
              ranges: dict[str, list[tuple[int, int]] | None]) -> tuple[list, list]:
    """`(the change's, pre-existing)`. **Nothing is dropped**, which is the
    whole difference between this and narrowing the scan itself: a smaller
    report that says so is honest, and a smaller report that does not is item
    97's `zeroed` shape."""
    mine = [d for d in diags if in_change(d, ranges)]
    theirs = [d for d in diags if not in_change(d, ranges)]
    return mine, theirs


def diff_scope(target: Path, diff_text: str, scratch: Path,
               exclude: list[str] | None = None) -> tuple[list[str], list[str], list[str]]:
    """Copy the diff's touched files (current content) into `scratch`, plus the
    diff text itself as `_diff.patch`. Returns (copied, excluded, notes).

    **An exclusion narrows the review, never the secret gate.** `_diff.patch`
    carries the whole diff regardless of what was folded out, so a credential in
    an excluded file's hunks still blocks — the property this module's own
    header already promised for deleted files, holding for excluded ones too.
    Otherwise `review_exclude` would be the one config field that can hide a key.
    """
    from tools.graph.impact import parse_diff
    from tools.sdlc import policy as guardrails
    patterns = exclude or []
    copied: list[str] = []
    dropped: list[str] = []
    notes: list[str] = []
    for rel in sorted(parse_diff(diff_text)):
        if patterns and guardrails.matches(rel, patterns):
            dropped.append(rel)
            continue
        src = target / rel
        if not src.is_file():
            notes.append(f"{rel}: not in the tree (deleted by the change, or the "
                         f"diff is from another root) — its hunks are still "
                         f"scanned via _diff.patch")
            continue
        dst = scratch / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        copied.append(rel)
    if dropped:
        notes.append(f"review_exclude folded out {len(dropped)} file(s): "
                     + ", ".join(dropped)
                     + " - their hunks are still scanned via _diff.patch, because "
                       "an exclusion narrows the review and never the secret gate")
    # Verbatim (T543): the platform newline would turn a diff's own CRLF lines
    # into CR CR LF on Windows, corrupting the patch the secret gate scans.
    (scratch / "_diff.patch").write_text(diff_text, encoding="utf-8", newline="\n")
    return copied, dropped, notes


def prepass(target: Path, diff_text: str | None = None,
            extra_rule_dirs: list[Path] | None = None,
            exclude: list[str] | None = None) -> dict:
    """The whole pre-pass as data. Never raises for tool trouble — statuses say."""
    tools_out: list[dict] = []
    sources_out: list[dict] = []
    # The tree's NAME, not its absolute path: prepass.json is committed, and an
    # absolute path here files one machine's layout in somebody else's checkout
    # (class 6). Nothing reads this back as a path; the report and the operator's
    # own command line carry the live location.
    scope: dict = {"mode": "tree", "root": Path(target).resolve().name}
    notes: list[str] = []

    with tempfile.TemporaryDirectory(prefix="sdlc-prepass-") as td:
        scratch = Path(td)
        if diff_text is not None:
            patterns = excluded_by(target) if exclude is None else exclude
            copied, dropped, notes = diff_scope(target, diff_text, scratch, patterns)
            ranges = touched_lines(diff_text)
            scope = {"mode": "diff", "files": copied, "excluded": dropped,
                     "exclude_patterns": patterns, "notes": notes,
                     # Every changed file folded out is not a small review, it is
                     # no review. `exit_code` reads this and refuses to call it a
                     # pass; the secret gate still ran, which is why the message
                     # says what did happen rather than only what did not.
                     "empty_after_exclude": bool(dropped and not copied)}
            scan_root, report_root = scratch, scratch
        else:
            ranges = {}
            scan_root, report_root = target, target

        # ── gitleaks: the secret gate ────────────────────────────────────────
        exe, why = binary("gitleaks")
        if exe is None:
            tools_out.append({"name": "gitleaks", "status": "not-run",
                              "detail": why, "findings": 0})
        else:
            report = scratch / "gitleaks-report.json"
            code, out, err = run_tool([str(exe), "dir", str(scan_root),
                                       "--report-format", "json",
                                       "--report-path", str(report),
                                       "--no-banner", "--exit-code",
                                       str(LEAK_EXIT)])
            if code is None:
                tools_out.append(hung_row("gitleaks", out, err))
            elif code == 0:
                tools_out.append({"name": "gitleaks", "status": "ok",
                                  "detail": "", "findings": 0})
            elif code == LEAK_EXIT:
                # The ast-grep branch below parses its tool's output inside
                # a guard because a crash does not parse. This branch read
                # an external tool's REPORT FILE with a bare json.loads, so
                # a report gitleaks did not finish writing took the whole
                # pre-pass down - and an uncaught exception exits 1, which
                # in this module's own vocabulary means "every gate ran,
                # findings". A crash reading as the benign findings exit is
                # the defect the smoke's hardening pass fixed; the secret
                # gate had it too.
                # `bad` is a FRESH name on purpose: the first cut reused
                # `why`, which this scope already binds to the binary
                # lookup's reason - so a report holding literal `null`
                # reported an error whose detail was the wrong sentence
                # instead of failing loudly. A shadowed name turns a
                # NameError into a quiet wrong answer.
                rows, bad = None, ""
                try:
                    rows = json.loads(report.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    bad = f"leaks reported, report unreadable: {exc}"
                if not isinstance(rows, list):
                    tools_out.append(
                        {"name": "gitleaks", "status": "error",
                         "detail": bad or f"leaks reported, but the report "
                         f"holds {type(rows).__name__}, not a list of "
                         f"findings", "findings": 0})
                else:
                    diags = parse_gitleaks(rows, report_root)
                    tools_out.append({"name": "gitleaks", "status": "ok",
                                      "detail": "", "findings": len(diags)})
                    sources_out.append({"source": {"name": "gitleaks"},
                                        "diagnostics": diags})
            else:
                tail = err.strip().splitlines()[-1] if err.strip() else f"exit {code}"
                tools_out.append({"name": "gitleaks", "status": "error",
                                  "detail": f"exit {code}: {tail}", "findings": 0})

        # ── ast-grep: the rule pass ──────────────────────────────────────────
        rules_text, rule_sources = collect_rules(target, extra_rule_dirs or [])
        exe, why = binary("ast-grep")
        if exe is None:
            tools_out.append({"name": "ast-grep", "status": "not-run",
                              "detail": why, "findings": 0,
                              "rules": rule_sources})
        elif not rules_text:
            tools_out.append({"name": "ast-grep", "status": "not-run",
                              "detail": "no rule files found (harness defaults "
                                        "missing?)", "findings": 0, "rules": []})
        else:
            code, out, err = run_tool([str(exe), "scan", "--inline-rules",
                                       rules_text, "--json=compact",
                                       str(scan_root)])
            # Measured on 0.45.1: exit 0 is a clean scan (or warnings only);
            # exit 1 with the findings array still on stdout means "scan
            # succeeded and found error level diagnostics" — a findings exit,
            # not a failure. The discriminator is the stdout: a findings exit
            # carries the array, and neither a crash nor a tool that never ran
            # emits one.
            #
            # **Only exit 0 may read empty output as clean**, and that is the
            # whole of this branch's doctrine. `json.loads(out or "[]")` for
            # exit 0 OR 1 laundered an exit-1 with EMPTY stdout into a clean
            # `[]` and reported `ast-grep: ok, 0 findings` — so a binary that
            # printed a notice and died (0.45.1's deprecated `sg` wrapper does
            # exactly this on Linux: exit 1, nothing on stdout) read as a silent
            # clean, and the rule gate was dead-but-green on every Linux
            # adopter. An exit 1 (or any exit) with nothing parseable is a tool
            # that did not run, which this module's own rule says is a status
            # and never a clean — it falls through to the LOUD error below,
            # carrying the stderr tail as its detail.
            rows = None
            if code == 0:
                try:
                    rows = json.loads(out or "[]")
                except ValueError:
                    rows = None
            elif code == 1 and out.strip():
                try:
                    rows = json.loads(out)
                except ValueError:
                    rows = None
            if isinstance(rows, list):
                diags = parse_astgrep(rows, report_root)
                # the diff patch is text, not code; ast-grep never matches it,
                # and the copied tree's own findings carry repo-relative paths.
                #
                # **A CODE RULE JUDGES THE CHANGE; THE SECRET GATE JUDGES THE
                # FILE.** The pass copies every touched file and scanned all of
                # it, so a smell that predated the change was reported against
                # whoever next edited that file. Right for gitleaks - a
                # credential edited back out is still in the file, which this
                # module's header states - and wrong for every rule about code
                # somebody is answerable for. The split is per TOOL because the
                # question differs per tool, not per severity.
                #
                # Pre-existing findings are COUNTED and carried, never dropped:
                # a quietly smaller report is item 97's `zeroed` shape, and the
                # rule this repository states is zero silent drops.
                pre: list[dict] = []
                if diff_text is not None:
                    diags, pre = partition(diags, ranges)
                row = {"name": "ast-grep", "status": "ok", "detail": "",
                       "findings": len(diags), "rules": rule_sources}
                if pre:
                    row["pre_existing"] = len(pre)
                    # The "reported separately" note is written LAST (after the
                    # own-tree de-dup), from the count that survives it: written
                    # here it would quote the pre-drop count (T113).
                tools_out.append(row)
                if diags:
                    sources_out.append({"source": {"name": "ast-grep"},
                                        "diagnostics": diags})
                if pre:
                    # Its own source, so a reader can tell at a glance which
                    # findings this change is answerable for. `exit_code` and
                    # the review loop read the `ast-grep` source; this one is
                    # context, and is deliberately not gate material.
                    sources_out.append(
                        {"source": {"name": f"ast-grep{PRE_EXISTING_SUFFIX}"},
                         "diagnostics": pre})
            elif code is None:
                tools_out.append(hung_row("ast-grep", out, err,
                                          rules=rule_sources))
            else:
                tail = err.strip().splitlines()[-1] if err.strip() else f"exit {code}"
                tools_out.append({"name": "ast-grep", "status": "error",
                                  "detail": f"exit {code}: {tail}", "findings": 0,
                                  "rules": rule_sources})

    # The harness's own worktrees are inside the repository - `claim_root` puts
    # them there deliberately - and they are full COPIES of it, so a finding in
    # one is a second report of a fact already reported at its real path, at an
    # address the repository does not have. Measured on a real repository: 386
    # of 772 secret findings, all of them gitleaks, which has no path-exclusion
    # flag to prevent it with. ast-grep reported ZERO, because it honours the
    # `.gitignore` that root writes - so this is not a property of "scanners",
    # it is a property of each one, and only the reader can hold the rule for
    # all of them.
    #
    # Counted, never silently dropped: the tool row says how many and why, and
    # `own_trees` is a number a reader can act on.
    from tools.sdlc import worktree as trees

    for row in tools_out:
        row["own_trees"] = 0
    # Credit an own-tree drop to its tool row by IDENTITY, not by the source's
    # display name. Pre-existing findings ride a source named
    # `<tool> (pre-existing)` that NO tool row is named after, so the old
    # by-name match emptied that source below while crediting nobody: own_trees
    # stayed 0 and pre_existing kept its pre-drop count - a silent drop, the one
    # thing this pass exists to refuse (T113).
    rows_by_name = {r["name"]: r for r in tools_out}
    for source in sources_out:
        name = source["source"]["name"]
        is_pre = name.endswith(PRE_EXISTING_SUFFIX)
        tool = name[:-len(PRE_EXISTING_SUFFIX)] if is_pre else name
        kept, mine = [], []
        for diag in source["diagnostics"]:
            (mine if trees.is_own_tree(target, diag["location"]["path"])
             else kept).append(diag)
        source["diagnostics"] = kept
        if not mine:
            continue
        row = rows_by_name.get(tool)
        if row is None:
            continue
        row["own_trees"] += len(mine)
        # A pre-existing finding is context: its drop lowers pre_existing; a
        # gate finding's drop lowers `findings`.
        if is_pre:
            if kept:
                row["pre_existing"] = len(kept)
            else:
                row.pop("pre_existing", None)
        else:
            row["findings"] = len(kept)
        row["detail"] = ((row["detail"] + " | " if row["detail"] else "")
                         + f"{len(mine)} finding(s) dropped: they are in "
                           f"this harness's own worktree copies of the "
                           f"repository, which are duplicates at paths the "
                           f"repository does not have")
    sources_out = [s for s in sources_out if s["diagnostics"]]

    # Written now, from the pre_existing count that SURVIVED own-tree de-dup.
    for row in tools_out:
        pe = row.get("pre_existing")
        if pe:
            note = (f"{pe} finding(s) outside the change's lines are reported "
                    f"separately - they were there before it")
            row["detail"] = f"{note} | {row['detail']}" if row["detail"] else note

    # ONE consistent total: `total` counts gate-material findings (what
    # `exit_code` weighs), so `by_severity` must count the SAME set. The
    # pre-existing sources are context - excluded from both, or the two summary
    # numbers disagree whenever a pre-existing finding exists (T113).
    total = sum(t["findings"] for t in tools_out)
    by_sev: dict[str, int] = {}
    for s in sources_out:
        if s["source"]["name"].endswith(PRE_EXISTING_SUFFIX):
            continue
        for d in s["diagnostics"]:
            by_sev[d["severity"]] = by_sev.get(d["severity"], 0) + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "scope": scope,
        "tools": tools_out,
        "sources": sources_out,
        "summary": {"total": total, "by_severity": dict(sorted(by_sev.items()))},
    }


def render_md(result: dict) -> str:
    lines = ["# Deterministic review pre-pass", ""]
    scope = result["scope"]
    if scope["mode"] == "diff":
        folded = (f", with {len(scope['excluded'])} folded out by review_exclude"
                  if scope.get("excluded") else "")
        lines.append(f"Scope: the diff's {len(scope['files'])} touched file(s){folded}, "
                     f"plus the diff text itself.")
        if scope.get("empty_after_exclude"):
            lines.append("- **review_exclude removed every changed file.** The secret "
                         "gate still read the diff, but nothing was reviewed.")
        lines += [f"- note: {n}" for n in scope.get("notes", [])]
    else:
        lines.append(f"Scope: the whole tree at {scope['root']}.")
    lines.append("")
    for t in result["tools"]:
        status = t["status"] if t["status"] != "ok" else \
            f"ok — {t['findings']} finding(s)"
        lines.append(f"- **{t['name']}**: {status}"
                     + (f" ({t['detail']})" if t["detail"] else ""))
        if t.get("rules") is not None:
            lines.append(f"    rules: {', '.join(t['rules']) or '(none)'}")
    lines.append("")
    for s in result["sources"]:
        lines.append(f"## {s['source']['name']}")
        lines.append("")
        for d in s["diagnostics"]:
            loc = d["location"]
            lines.append(f"- `{loc['path']}:{loc['range']['start']['line']}` "
                         f"[{d['severity']}] {d['code']['value']}: {d['message']}")
        lines.append("")
    if not result["sources"]:
        lines.append("No findings from the tools that ran.")
        lines.append("")
    return "\n".join(lines)


def exit_code(result: dict) -> int:
    """3 beats 1 beats 0: an unfired gate outranks findings from the one that
    fired — the change is not reviewed while a gate never ran. A `review_exclude`
    that folded out every changed file lands here too: nothing ran on anything,
    which is a 3 wearing a 0's clothes."""
    if unreviewed(result):
        return 3
    # `.get` chains, not subscripts: this function is documented as reading
    # documents this module did not write, and a report missing `summary`
    # used to raise KeyError past every guard above it.
    summary = result.get("summary")
    total = summary.get("total") if isinstance(summary, dict) else 0
    return 1 if total else 0


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--target", required=True, metavar="DIR",
                    help="the repository root the change belongs to")
    ap.add_argument("--diff", metavar="FILE",
                    help="a unified diff; scope narrows to its touched files "
                         "plus the diff text itself (default: the whole tree)")
    ap.add_argument("--out", required=True, metavar="DIR",
                    help=f"where {RESULT_NAME} + {REPORT_NAME} are written")
    ap.add_argument("--rules-dir", action="append", default=[], metavar="DIR",
                    help="extra ast-grep rule directories (repeatable), on top "
                         "of the harness defaults and <target>/.sdlc/review-rules")
    args = ap.parse_args(argv)

    target = Path(args.target)
    if not target.is_dir():
        print(f"!! --target is not a directory: {target}")
        return 2
    diff_text = None
    if args.diff:
        diff_path = Path(args.diff)
        if not diff_path.is_file():
            print(f"!! {diff_path} does not exist")
            return 2
        diff_text = diff_path.read_text(encoding="utf-8", errors="replace")
        from tools.graph.impact import parse_diff
        if not parse_diff(diff_text):
            print(f"!! {diff_path} contains no file headers a unified diff would have")
            return 2

    result = prepass(target, diff_text, [Path(d) for d in args.rules_dir])
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    # LF on every platform (T543), as `sca.py` writes its twin: these are committed,
    # and a Windows run in CRLF made the next Linux run rewrite every line.
    (out / RESULT_NAME).write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        newline="\n")
    (out / REPORT_NAME).write_text(render_md(result), encoding="utf-8", newline="\n")

    for t in result["tools"]:
        line = t["status"] if t["status"] != "ok" else f"ok, {t['findings']} finding(s)"
        print(f"prepass: {t['name']}: {line}" + (f" - {t['detail']}" if t['detail'] else ""))
    print(f"prepass: wrote {out / RESULT_NAME} and {REPORT_NAME}")
    return exit_code(result)


def main(argv: list[str] | None = None) -> int:
    """The door. One implementation, in `tools/cli.py` - see it for what is
    caught and why. `3` is written here, literally, because the code that
    means *could not look* is this module's vocabulary, not the door's."""
    return cli.door(_main, argv, "the pre-pass", 3)


if __name__ == "__main__":
    raise SystemExit(main())
