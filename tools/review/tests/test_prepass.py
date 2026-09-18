"""Item 25 — the deterministic review pre-pass, seam-tested against real transcriptions.

The canned tool outputs here were transcribed from the pinned binaries themselves
(gitleaks 8.30.1, ast-grep 0.45.1, run on 2026-08-21) — the parsers are held to what
the tools actually emit, not to documentation. Most tests replace the subprocess seam
(`run_tool`) with canned results, so the PLUMBING is exercised deterministically and
the absent-binary path stays a first-class outcome, not a skip. The two
`..._fires_on_a_runtime_built_...` tests at the end instead spawn the REAL gitleaks
and ast-grep through the gate on a runtime-built positive, so the PREMISE — that the
gate detects a real finding, not only that the code parses a transcription of one —
is covered where the pinned binaries are present (CI's fetch step installs them).
Canned secrets are placeholder-shaped on purpose, and the runtime-built credential is
assembled from code points so no token literal ever sits in this tracked file: a
realistic token in test source would be flagged by the harness's own CI secret scan —
correctly.
"""
from __future__ import annotations

import json
import os
import string
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import tools.review.prepass as PP  # noqa: E402

#: One finding row as gitleaks 8.30.1 writes it (fields verbatim; values inert).
GITLEAKS_ROW = {
    "RuleID": "github-pat",
    "Description": "Uncovered a GitHub Personal Access Token, potentially leading "
                   "to unauthorized repository access and sensitive content exposure.",
    "StartLine": 2, "EndLine": 2, "StartColumn": 11, "EndColumn": 50,
    "Match": "placeholder-matched-text", "Secret": "placeholder-secret-value",
    "File": "/scan/src/config.py", "SymlinkFile": "", "Commit": "",
    "Entropy": 4.97, "Author": "", "Email": "", "Date": "", "Message": "",
    "Tags": [], "Fingerprint": "/scan/src/config.py:github-pat:2",
}

#: One match row as ast-grep 0.45.1 --json=compact emits it. Lines are ZERO-based.
ASTGREP_ROW = {
    "text": "e.printStackTrace()",
    "range": {"byteOffset": {"start": 32, "end": 51},
              "start": {"line": 0, "column": 32}, "end": {"line": 0, "column": 51}},
    "file": "/scan/A.java", "lines": "class A { }", "language": "Java",
    "charCount": {"leading": 32, "trailing": 5},
    "metaVariables": {"single": {}, "multi": {}, "transformed": {}},
    "ruleId": "java-swallowed-stacktrace", "severity": "warning",
    "message": "swallowed stack trace - route it to the logger", "note": None,
    "labels": None,
}


# ── the parsers, against the real shapes ──────────────────────────────────────


def test_gitleaks_findings_are_redacted_at_the_boundary():
    """The measured hazard: the report carries the secret verbatim, and this
    document travels into review artifacts and PR comments. Rule, location and
    fingerprint identify the leak without containing it."""
    (d,) = PP.parse_gitleaks([GITLEAKS_ROW], Path("/scan"))
    blob = json.dumps(d)
    assert "placeholder-matched-text" not in blob
    assert "placeholder-secret-value" not in blob
    assert d["code"]["value"] == "github-pat"
    assert d["location"] == {"path": "src/config.py",
                             "range": {"start": {"line": 2, "column": 11}}}
    assert d["severity"] == "ERROR"
    assert d["fingerprint"] == "src/config.py:github-pat:2"


def test_astgrep_lines_arrive_zero_based_and_leave_one_based():
    """Measured on 0.45.1: `"line": 0` is the file's first line. Every artifact
    in this repository is one-based; a converter that forgot would misplace
    every finding by exactly one line, plausibly."""
    (d,) = PP.parse_astgrep([ASTGREP_ROW], Path("/scan"))
    assert d["location"]["path"] == "A.java"
    assert d["location"]["range"]["start"] == {"line": 1, "column": 33}
    assert d["severity"] == "WARNING"
    assert d["code"]["value"] == "java-swallowed-stacktrace"
    assert "stack trace" in d["message"]


# ── T397: a present-null ast-grep severity must not become the string 'NONE' ──
#
# MEASURED against the real pinned ast-grep 0.45.1 (sg.exe) for this row: a
# rule that OMITS `severity` makes the tool supply its OWN default, "hint" -
# never a null, never an absent key. A rule that states `severity: null`
# explicitly fails the tool's own rule-config parse (exit 8, "invalid type:
# unit value, expected enum Severity") before any row is ever produced. So no
# real, spawnable ast-grep 0.45.1 path reaches `parse_astgrep` with a null
# severity - this differs from `note`/`labels` (ASTGREP_ROW above), which
# really are nullable in the tool's own output and are the row's cited
# evidence that ast-grep CAN null a field. The fix below is nonetheless
# required unconditionally by the row: `.get(key, default)` must not sail a
# PRESENT null past its default for any producer, today's or a future one.

FAKE_ASTGREP_CLI = Path(__file__).resolve().parent / "_t397_fake_astgrep_main.py"


def test_null_astgrep_severity_becomes_the_default_not_the_string_none():
    """A JSON null in the ast-grep `severity` field must fall back to the
    parser's intended default ("warning"), not `str(None).upper()` ==
    "NONE" - a severity no rule ever stated, and the exact string T394's
    loop.py guard was filed to stop a reader from seeing."""
    row = dict(ASTGREP_ROW)
    row["severity"] = None
    (d,) = PP.parse_astgrep([row], Path("/scan"))
    assert d["severity"] == "WARNING", d
    assert d["severity"] != "NONE", d


def test_a_genuinely_stated_astgrep_severity_still_round_trips():
    """CONTROL: a real, stated severity is untouched by the null handling -
    the default must apply ONLY to a present null, never override a real
    value.

    NOT SUFFICIENT ON ITS OWN (review-W428.md, T397 round 2): ASTGREP_ROW
    states "warning", which is ALSO this function's own default, so a
    mutant that discards the parsed value entirely and substitutes the
    default verbatim would pass this assertion vacuously. See the
    NON-default control immediately below, which is the one that actually
    proves the parsed value round-trips."""
    (d,) = PP.parse_astgrep([ASTGREP_ROW], Path("/scan"))
    assert d["severity"] == "WARNING"


def test_a_non_default_stated_astgrep_severity_still_round_trips():
    """CONTROL, strengthened (review-W428.md, T397 round 2): "warning" is
    both a genuinely-stated value AND the function's own fallback default,
    so the control above cannot tell "the real value survived" from "the
    default was substituted regardless of input" - and a mutant that does
    exactly that (`"severity": str("warning").upper()`, ignoring `r` and
    `severity` entirely) passed the whole file, 77/77, silently discarding
    every stated severity.

    A NON-default severity closes that gap. "error" is not a placeholder:
    it is what the shipped rules actually state (rules/python.yml:3,
    rules/javascript.yml:3, rules/typescript.yml:3 all carry
    `severity: error`), and ast-grep is not in loop.py's `TOOL_SEVERITY`
    map, so this exact string is what sets the grade at loop.py:260 - a
    parser that quietly downgraded every real "error" to "warning" would
    still say '77 passed' without this test."""
    row = dict(ASTGREP_ROW)
    row["severity"] = "error"
    (d,) = PP.parse_astgrep([row], Path("/scan"))
    assert d["severity"] == "ERROR", d


def test_null_astgrep_message_becomes_the_default_not_none():
    """Sibling of the severity fix: `message` has the identical present-null
    shape (`r.get("message", "")` past a present null returns None, not "").
    MEASURED against real ast-grep 0.45.1: a rule that omits `message`
    likewise gets the tool's own default (`""`), never null - the same
    shape as severity, so fixed the same way rather than merely named."""
    row = dict(ASTGREP_ROW)
    row["message"] = None
    (d,) = PP.parse_astgrep([row], Path("/scan"))
    assert d["message"] == "", d


def test_null_astgrep_severity_becomes_the_default_through_cli(repo, tmp_path):
    """T397 through the spawned door: `main()` is run as a FRESH process via
    `sys.executable`, not called in-process - a refusal and a traceback both
    just raise/return in-process and pytest cannot tell them apart, which is
    this repo's standing CLI-testing rule.

    The real ast-grep binary cannot itself be made to emit a null severity
    (module docstring above) and does not even resolve under this name on
    this machine (T385, a separate unfixed row), so the external-tool seam
    is faked at the process boundary by `_t397_fake_astgrep_main.py`
    (committed alongside this test) - everything else (argparse, prepass(),
    parse_astgrep, report assembly, the exit code, stdout) is real and
    unmodified.
    """
    out = tmp_path / "out"
    done = subprocess.run(
        [sys.executable, str(FAKE_ASTGREP_CLI), "--target", str(repo),
         "--out", str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300)
    assert "Traceback" not in done.stderr, done.stderr
    # The exit code IS part of what a person sees from a spawned CLI (the
    # docstring above and this commit's message both say so) - MEASURED
    # (review-W428.md, T397 round 2): one gitleaks gate clean + one ast-grep
    # finding is exit_code()'s findings exit, 1, never a crash and never the
    # could-not-look 3.
    assert done.returncode == 1, \
        f"expected exit 1 (a findings exit, not a crash): {done.returncode}, " \
        f"stdout={done.stdout!r} stderr={done.stderr!r}"
    # NOT stdout (review-W428.md, T397 round 2, correcting round 1): stdout
    # only ever carries the one-line-per-gate summary
    # ("prepass: ast-grep: ok, 1 finding(s)"), never a diagnostic's severity
    # text, so "NONE" not in done.stdout was true by construction regardless
    # of this fix. The severity text actually appears in the written
    # markdown report (`render_md`, "[{severity}] ...") as well as the JSON,
    # so both are checked.
    md = (out / "prepass.md").read_text(encoding="utf-8")
    assert "NONE" not in md, md
    data = json.loads((out / "prepass.json").read_text(encoding="utf-8"))
    diags = next(s["diagnostics"] for s in data["sources"]
                 if s["source"]["name"] == "ast-grep")
    assert diags[0]["severity"] == "WARNING", diags[0]
    assert "NONE" not in json.dumps(data), data


def test_paths_relativize_across_separators(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    inside = str(root / "src" / "a.py").replace("/", "\\")
    assert PP.rel_to(inside, root) == "src/a.py"
    outside = tmp_path / "elsewhere.py"
    assert PP.rel_to(str(outside), root).endswith("elsewhere.py")


# ── the rule set ──────────────────────────────────────────────────────────────


def test_the_default_rules_ship_and_concatenate(tmp_path):
    text, sources = PP.collect_rules(tmp_path, [])
    assert {"java.yml", "javascript.yml", "python.yml", "typescript.yml"} <= set(sources)
    assert text.count("\n---\n") == len(sources) - 1
    assert "printStackTrace" in text and "breakpoint" in text


def test_the_repositorys_own_rules_join_the_defaults(tmp_path):
    own = tmp_path / ".sdlc" / "review-rules"
    own.mkdir(parents=True)
    (own / "house.yml").write_text(
        "id: own-rule\nlanguage: python\nseverity: warning\nmessage: m\n"
        "rule:\n  pattern: eval($X)\n", encoding="utf-8")
    text, sources = PP.collect_rules(tmp_path, [])
    assert "own-rule" in text
    assert any(s.endswith("house.yml") for s in sources)


# ── the runner wiring, through the seam ───────────────────────────────────────


class FakeRun:
    """Canned per-binary results; records every command. The gitleaks fake
    writes the report file its command names, as the real one does."""

    def __init__(self, gitleaks=(0, "", ""), astgrep=(0, "[]", ""),
                 report_rows=None):
        self.gitleaks, self.astgrep = gitleaks, astgrep
        self.report_rows = report_rows or []
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], timeout: int = 300):
        self.calls.append(list(cmd))
        name = Path(cmd[0]).stem.lower()
        if name == "gitleaks":
            if "--report-path" in cmd:
                Path(cmd[cmd.index("--report-path") + 1]).write_text(
                    json.dumps(self.report_rows), encoding="utf-8")
            return self.gitleaks
        return self.astgrep


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def fake_binaries(monkeypatch):
    monkeypatch.setattr(PP, "binary",
                        lambda name: (Path(f"/fake/{name}.exe"), ""))


def test_a_clean_run_is_exit_zero_with_both_tools_ok(repo, monkeypatch):
    fake_binaries(monkeypatch)
    monkeypatch.setattr(PP, "run_tool", FakeRun())
    result = PP.prepass(repo)
    assert [t["status"] for t in result["tools"]] == ["ok", "ok"]
    assert result["summary"]["total"] == 0
    assert PP.exit_code(result) == 0


def test_the_leak_exit_is_findings_and_they_arrive_redacted(repo, monkeypatch):
    fake_binaries(monkeypatch)
    fake = FakeRun(gitleaks=(PP.LEAK_EXIT, "", ""), report_rows=[GITLEAKS_ROW])
    monkeypatch.setattr(PP, "run_tool", fake)
    result = PP.prepass(repo)
    # Exact equality on purpose - the row's KEY SET is the pin, so a field
    # added to it has to be stated here rather than quietly tolerated.
    # `own_trees` is the count of findings dropped for sitting in the harness's
    # own worktree copies of the repository; zero, here, because this fixture
    # has none.
    assert result["tools"][0] == {"name": "gitleaks", "status": "ok",
                                  "detail": "", "findings": 1, "own_trees": 0}
    assert "placeholder-secret-value" not in json.dumps(result)
    assert PP.exit_code(result) == 1
    gl_cmd = fake.calls[0]
    assert gl_cmd[1] == "dir" and "--no-banner" in gl_cmd
    assert gl_cmd[cmd_i(gl_cmd, "--exit-code") + 1] == str(PP.LEAK_EXIT)


def cmd_i(cmd: list[str], flag: str) -> int:
    return cmd.index(flag)


def test_astgreps_findings_exit_is_a_findings_exit_not_a_failure(repo, monkeypatch):
    """Measured on 0.45.1: error-severity matches exit 1 with the JSON still on
    stdout ("Scan succeeded and found error level diagnostics"). The
    discriminator is the parseable stdout, and this pin is why."""
    fake_binaries(monkeypatch)
    monkeypatch.setattr(PP, "run_tool",
                        FakeRun(astgrep=(1, json.dumps([ASTGREP_ROW]), "")))
    result = PP.prepass(repo)
    ag = next(t for t in result["tools"] if t["name"] == "ast-grep")
    assert ag["status"] == "ok" and ag["findings"] == 1
    assert PP.exit_code(result) == 1


def test_a_crash_exit_with_garbage_stdout_is_an_error_status(repo, monkeypatch):
    fake_binaries(monkeypatch)
    monkeypatch.setattr(PP, "run_tool",
                        FakeRun(astgrep=(1, "thread panicked", "boom")))
    result = PP.prepass(repo)
    ag = next(t for t in result["tools"] if t["name"] == "ast-grep")
    assert ag["status"] == "error" and "exit 1" in ag["detail"]
    assert PP.exit_code(result) == 3


# ── ast-grep: a tool that failed to run must never read as a clean pass ────────
#
# The discriminator between "clean" and "did not run" is the stdout, and it is
# NOT symmetric across exit codes: exit 0 is a clean scan whose empty output is
# legitimately no findings, while exit 1 is a findings exit ONLY when the array
# is still on stdout. `json.loads(out or "[]")` for exit 0 OR 1 erased that
# asymmetry — an exit-1 with EMPTY stdout became a clean `[]`, and the rule gate
# reported `ok, 0 findings` while never running. That is exactly what 0.45.1's
# deprecated `sg` wrapper produced on Linux (a notice, then death: exit 1, empty
# stdout), which is why vendoring `ast-grep` (T365) and this discrimination are
# one change. CLAUDE.md's headline rule, at exit 1: nothing returned is not
# evidence of absence.


def test_astgrep_exit_one_with_empty_stdout_is_an_error_not_a_silent_clean(
        repo, monkeypatch):
    """**The bug, as a test.** The deprecated-wrapper shape: exit 1, nothing on
    stdout, the reason on stderr. It must be a LOUD error carrying that reason,
    never `ok, 0 findings`. Runs red against `json.loads(out or "[]")`, which
    reported this exact shape as a clean gate on every Linux adopter."""
    fake_binaries(monkeypatch)
    monkeypatch.setattr(PP, "run_tool", FakeRun(astgrep=(
        1, "",
        "the wrapper is deprecated. Use the standalone binary instead.\n"
        'Os { code: 2, kind: NotFound, message: "No such file or directory" }')))
    result = PP.prepass(repo)
    ag = next(t for t in result["tools"] if t["name"] == "ast-grep")
    assert ag["status"] == "error", ag
    assert ag["status"] != "ok" and ag["findings"] == 0, ag
    assert "exit 1" in ag["detail"] and "NotFound" in ag["detail"], (
        "the error must carry the tool's own stderr tail, or a reader cannot "
        "tell a wrapper that died from a scanner that found nothing")
    assert PP.exit_code(result) == 3, "a gate that did not run outranks a clean"


def test_astgrep_exit_one_with_a_valid_array_is_still_findings(repo, monkeypatch):
    """The positive control for the branch above: a real findings exit (exit 1
    WITH the array on stdout) must still be read as findings, not swept into the
    error arm along with the empty-stdout case."""
    fake_binaries(monkeypatch)
    monkeypatch.setattr(PP, "run_tool",
                        FakeRun(astgrep=(1, json.dumps([ASTGREP_ROW]), "")))
    result = PP.prepass(repo)
    ag = next(t for t in result["tools"] if t["name"] == "ast-grep")
    assert ag["status"] == "ok" and ag["findings"] == 1, ag
    assert PP.exit_code(result) == 1


def test_astgrep_exit_zero_with_empty_stdout_is_a_clean_pass(repo, monkeypatch):
    """The other side of the asymmetry: exit 0 IS a clean scan, and its empty
    output is legitimately no findings — not the error the exit-1 empty case is.
    A fix that made empty output an error for every exit code would break this,
    which is the equivalence the exit-code check is there to keep."""
    fake_binaries(monkeypatch)
    monkeypatch.setattr(PP, "run_tool", FakeRun(astgrep=(0, "", "")))
    result = PP.prepass(repo)
    ag = next(t for t in result["tools"] if t["name"] == "ast-grep")
    assert ag["status"] == "ok" and ag["findings"] == 0, ag
    assert PP.exit_code(result) == 0


@pytest.mark.parametrize("stdout", ["", "not json at all",
                                    json.dumps([ASTGREP_ROW])])
def test_astgrep_an_unexpected_exit_is_an_error_whatever_is_on_stdout(
        repo, monkeypatch, stdout):
    """exit 2 is neither a clean (0) nor a findings (1) exit, so it is an error
    REGARDLESS of stdout — including when a VALID findings array happens to sit
    there (the third param), which is the `treat exit 2 as findings` mutation
    this kills. Only exits 0 and 1 carry a contract; anything else is a tool
    that did something the parser has no agreement with, and guessing findings
    from it is the silent-pass shape one exit code over."""
    fake_binaries(monkeypatch)
    monkeypatch.setattr(PP, "run_tool", FakeRun(astgrep=(2, stdout, "boom")))
    result = PP.prepass(repo)
    ag = next(t for t in result["tools"] if t["name"] == "ast-grep")
    assert ag["status"] == "error", ag
    assert "exit 2" in ag["detail"], ag
    assert PP.exit_code(result) == 3


def test_an_unfired_gate_outranks_findings(repo, monkeypatch):
    """Exit 3 beats exit 1: the change is not reviewed while a gate never ran —
    for the SECRET gate especially, 'the other tool found things' is not a
    substitute for the scan that did not happen."""
    monkeypatch.setattr(PP, "binary",
                        lambda name: ((None, f"not run: vendored {name} missing "
                                             f"(fetch: python tools/third_party.py "
                                             f"--fetch)")
                                      if name == "gitleaks"
                                      else (Path("/fake/ast-grep.exe"), "")))
    monkeypatch.setattr(PP, "run_tool",
                        FakeRun(astgrep=(1, json.dumps([ASTGREP_ROW]), "")))
    result = PP.prepass(repo)
    gl = next(t for t in result["tools"] if t["name"] == "gitleaks")
    assert gl["status"] == "not-run" and "--fetch" in gl["detail"]
    assert result["summary"]["total"] == 1, "the findings that DID arrive survive"
    assert PP.exit_code(result) == 3


def test_a_gitleaks_error_exit_is_never_read_as_clean(repo, monkeypatch):
    fake_binaries(monkeypatch)
    monkeypatch.setattr(PP, "run_tool", FakeRun(gitleaks=(1, "", "bad flag")))
    result = PP.prepass(repo)
    gl = next(t for t in result["tools"] if t["name"] == "gitleaks")
    assert gl["status"] == "error"
    assert PP.exit_code(result) == 3


# ── diff scoping ──────────────────────────────────────────────────────────────

DIFF = ("--- a/src/a.py\n+++ b/src/a.py\n@@ -1,1 +1,1 @@\n-x\n+y\n"
        "--- a/gone.py\n+++ b/gone.py\n@@ -1,1 +1,1 @@\n-a\n+b\n")


def test_the_diff_scope_copies_touched_files_and_the_diff_itself(repo, tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    copied, dropped, notes = PP.diff_scope(repo, DIFF, scratch)
    assert copied == ["src/a.py"]
    assert (scratch / "src" / "a.py").read_text(encoding="utf-8") == "x = 1\n"
    assert (scratch / "_diff.patch").read_text(encoding="utf-8") == DIFF
    (note,) = notes
    assert "gone.py" in note and "_diff.patch" in note, \
        "a file the diff names but the tree lacks is noted, and its hunks " \
        "still reach the secret scan through the patch copy"


def test_a_diff_run_records_its_scope(repo, monkeypatch):
    fake_binaries(monkeypatch)
    fake = FakeRun()
    monkeypatch.setattr(PP, "run_tool", fake)
    result = PP.prepass(repo, DIFF)
    assert result["scope"]["mode"] == "diff"
    assert result["scope"]["files"] == ["src/a.py"]
    scanned = fake.calls[0][2]
    assert scanned != str(repo), "the scan runs over the scratch copy, not the tree"


def test_the_tree_scope_root_is_a_name_not_an_absolute_path(repo, monkeypatch):
    # class 6: prepass.json is committed, so the tree scope must NAME the tree,
    # never spell this machine's absolute layout into somebody else's checkout.
    fake_binaries(monkeypatch)
    monkeypatch.setattr(PP, "run_tool", FakeRun())
    result = PP.prepass(repo)
    assert result["scope"]["mode"] == "tree"
    root = result["scope"]["root"]
    assert not Path(root).is_absolute(), root
    assert "/" not in root and "\\" not in root and ":" not in root, root
    assert root == repo.name


# ── rendering and the CLI ─────────────────────────────────────────────────────


def test_the_md_names_statuses_rules_and_findings(repo, monkeypatch):
    fake_binaries(monkeypatch)
    row = {**GITLEAKS_ROW, "File": str(repo / "src" / "config.py"),
           "Fingerprint": f"{repo / 'src' / 'config.py'}:github-pat:2"}
    monkeypatch.setattr(PP, "run_tool",
                        FakeRun(gitleaks=(PP.LEAK_EXIT, "", ""),
                                report_rows=[row]))
    md = PP.render_md(PP.prepass(repo))
    assert "**gitleaks**: ok — 1 finding(s)" in md
    assert "rules: java.yml" in md
    assert "`src/config.py:2` [ERROR] github-pat" in md
    assert "placeholder-secret-value" not in md


def test_the_cli_writes_both_artifacts_and_states_absent_tools(repo, tmp_path,
                                                               monkeypatch,
                                                               capsys):
    monkeypatch.setattr(PP, "binary",
                        lambda name: (None, f"not run: vendored {name} missing "
                                            f"(fetch: python tools/third_party.py "
                                            f"--fetch)"))
    out = tmp_path / "out"
    assert PP.main(["--target", str(repo), "--out", str(out)]) == 3
    text = capsys.readouterr().out
    assert "not-run" in text and "--fetch" in text
    data = json.loads((out / "prepass.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == PP.SCHEMA_VERSION
    assert all(t["status"] == "not-run" for t in data["tools"])
    assert (out / "prepass.md").exists()


def test_the_cli_refuses_bad_inputs(tmp_path, capsys):
    assert PP.main(["--target", str(tmp_path / "nope"),
                    "--out", str(tmp_path / "o")]) == 2
    assert "not a directory" in capsys.readouterr().out
    (tmp_path / "r").mkdir()
    assert PP.main(["--target", str(tmp_path / "r"), "--diff",
                    str(tmp_path / "missing.diff"), "--out",
                    str(tmp_path / "o")]) == 2
    assert "does not exist" in capsys.readouterr().out
    bad = tmp_path / "bad.diff"
    bad.write_text("just words\n", encoding="utf-8")
    assert PP.main(["--target", str(tmp_path / "r"), "--diff", str(bad),
                    "--out", str(tmp_path / "o")]) == 2
    assert "unified diff" in capsys.readouterr().out


def test_extra_rule_dirs_reach_the_scan(repo, tmp_path, monkeypatch):
    extra = tmp_path / "more-rules"
    extra.mkdir()
    (extra / "extra.yml").write_text(
        "id: extra-rule\nlanguage: python\nseverity: error\nmessage: m\n"
        "rule:\n  pattern: exec($X)\n", encoding="utf-8")
    fake_binaries(monkeypatch)
    fake = FakeRun()
    monkeypatch.setattr(PP, "run_tool", fake)
    PP.prepass(repo, None, [extra])
    sg_cmd = next(c for c in fake.calls if "scan" in c)
    rules_text = sg_cmd[cmd_i(sg_cmd, "--inline-rules") + 1]
    assert "extra-rule" in rules_text and "printStackTrace" in rules_text


# ── review_exclude (item 36): folded out of the review, never out of the gate ──

EXCL_DIFF = ("--- a/src/a.py\n+++ b/src/a.py\n@@ -1,1 +1,1 @@\n-x\n+y\n"
             "--- a/uv.lock\n+++ b/uv.lock\n@@ -1,1 +1,1 @@\n-a\n+b\n")


def excluding(repo: Path, globs: str) -> None:
    (repo / ".sdlc").mkdir(parents=True, exist_ok=True)
    (repo / ".sdlc" / "profile.yml").write_text(
        f"stack: python\nreview:\n  review_exclude: [{globs}]\n", encoding="utf-8")


def test_the_profile_field_that_nothing_read_is_now_read(repo):
    """It shipped in the template and no code opened it. A reviewer who
    believes the lockfile was folded out reads a diff they think is smaller."""
    assert PP.excluded_by(repo) == []
    excluding(repo, "uv.lock")
    assert PP.excluded_by(repo) == ["uv.lock"]


def test_an_excluded_file_is_not_scanned_and_is_named_in_the_notes(repo, tmp_path):
    (repo / "uv.lock").write_text("locked\n", encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    copied, dropped, notes = PP.diff_scope(repo, EXCL_DIFF, scratch, ["uv.lock"])
    assert copied == ["src/a.py"] and dropped == ["uv.lock"]
    assert not (scratch / "uv.lock").exists()
    assert any("uv.lock" in n for n in notes), "an exclusion nobody is told about"


def test_an_exclusion_narrows_the_review_and_never_the_secret_gate(repo, tmp_path):
    """The one field that could otherwise hide a key. The whole diff still
    reaches `_diff.patch`, which is what gitleaks reads."""
    (repo / "uv.lock").write_text("locked\n", encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    PP.diff_scope(repo, EXCL_DIFF, scratch, ["uv.lock"])
    patch = (scratch / "_diff.patch").read_text(encoding="utf-8")
    assert patch == EXCL_DIFF and "uv.lock" in patch


def test_a_glob_excludes_a_whole_tree(repo, tmp_path):
    diff = ("--- a/src/a.py\n+++ b/src/a.py\n@@ -1,1 +1,1 @@\n-x\n+y\n"
            "--- a/vendor/x.py\n+++ b/vendor/x.py\n@@ -1,1 +1,1 @@\n-a\n+b\n")
    (repo / "vendor").mkdir()
    (repo / "vendor" / "x.py").write_text("v = 1\n", encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    copied, dropped, _ = PP.diff_scope(repo, diff, scratch, ["vendor/**"])
    assert copied == ["src/a.py"] and dropped == ["vendor/x.py"]


def test_excluding_every_changed_file_is_a_three_and_not_a_pass(repo, monkeypatch):
    """Nothing ran on anything. Reporting that as clean is the failure this
    repository keeps finding in its own tools."""
    excluding(repo, "src/**")
    fake_binaries(monkeypatch)
    monkeypatch.setattr(PP, "run_tool", lambda cmd, timeout=300: (0, "[]", ""))
    result = PP.prepass(repo, "--- a/src/a.py\n+++ b/src/a.py\n@@ -1,1 +1,1 @@\n-x\n+y\n")
    assert result["scope"]["empty_after_exclude"] is True
    assert PP.exit_code(result) == 3
    assert "removed every changed file" in PP.render_md(result)


def test_a_normal_exclusion_is_still_a_zero_when_nothing_was_found(repo, monkeypatch):
    excluding(repo, "uv.lock")
    (repo / "uv.lock").write_text("locked\n", encoding="utf-8")
    fake_binaries(monkeypatch)
    monkeypatch.setattr(PP, "run_tool", lambda cmd, timeout=300: (0, "[]", ""))
    result = PP.prepass(repo, EXCL_DIFF)
    assert result["scope"]["empty_after_exclude"] is False
    assert PP.exit_code(result) == 0
    assert "1 folded out by review_exclude" in PP.render_md(result)


def test_the_report_records_the_patterns_that_were_applied(repo, monkeypatch):
    """An auditor reading the report a month later needs to know what the
    review did not look at, not only what it did."""
    excluding(repo, "uv.lock")
    (repo / "uv.lock").write_text("locked\n", encoding="utf-8")
    fake_binaries(monkeypatch)
    monkeypatch.setattr(PP, "run_tool", lambda cmd, timeout=300: (0, "[]", ""))
    result = PP.prepass(repo, EXCL_DIFF)
    assert result["scope"]["exclude_patterns"] == ["uv.lock"]
    assert result["scope"]["excluded"] == ["uv.lock"]


def test_an_explicit_exclude_argument_beats_the_profile(repo, monkeypatch):
    """The CLI and the MCP tier read the profile; a caller that already knows
    the patterns passes them, and an empty list means 'exclude nothing'."""
    excluding(repo, "src/**")
    fake_binaries(monkeypatch)
    monkeypatch.setattr(PP, "run_tool", lambda cmd, timeout=300: (0, "[]", ""))
    result = PP.prepass(repo, EXCL_DIFF, exclude=[])
    assert result["scope"]["excluded"] == [] and PP.exit_code(result) == 0


# ── the document's vocabulary belongs to the module that writes it ───────────
#
# `tools[].name` / `tools[].status` is this module's schema, and three readers
# had each restated it rather than importing it: `exit_code()` here, `sca.py`
# (which emits the same shape for item 40's gate), and
# `provider_smoke.verify()`, which hard-coded both the pair of gate names and
# the ok status. Two of those copies were measurably WRONG, not merely
# duplicated - each test below pins one of them.


def _doc(tools, total=0):
    return {"schema_version": 1, "scope": {}, "tools": tools,
            "summary": {"total": total}}


def test_a_status_nobody_anticipated_means_the_gate_did_not_fire():
    """The allow-list, and the reason it is one. `sca.py` asked this same
    question as a DENY-LIST of three named bad statuses, beside a comment
    claiming to be "the rule prepass.py states" - so on a status neither list
    anticipated the two gave opposite answers. A deny-list is silent about
    every status added after it was written, and `nothing-scanned` was itself
    added that way."""
    doc = _doc([{"name": "some-gate", "status": "a-status-added-later",
                 "findings": 0, "detail": ""}])
    assert [t["name"] for t in PP.unfired(doc)] == ["some-gate"]
    assert PP.exit_code(doc) == 3


def test_a_gate_that_fired_with_findings_still_fired():
    """`ok` means the gate RAN, findings or not - the distinction the exit
    3-over-1 rule rests on."""
    doc = _doc([{"name": "gitleaks", "status": PP.STATUS_OK,
                 "findings": 4, "detail": ""}], total=4)
    assert PP.unfired(doc) == []
    assert PP.exit_code(doc) == 1


def test_absent_and_failed_are_two_different_findings():
    """A reader that folds them together cannot say which happened: "the gate
    errored" and "no one ever asked this gate" are different repairs."""
    doc = _doc([{"name": "gitleaks", "status": "error", "findings": 0,
                 "detail": ""}])
    assert [t["name"] for t in PP.unfired(doc)] == ["gitleaks"]
    assert PP.missing_gates(doc) == ["ast-grep"]


def test_missing_gates_is_empty_when_every_gate_is_present():
    doc = _doc([{"name": g, "status": PP.STATUS_OK, "findings": 0, "detail": ""}
                for g in PP.GATES])
    assert PP.missing_gates(doc) == [] and PP.unfired(doc) == []


def test_the_rule_reads_a_document_this_module_did_not_write():
    """`sca.py` emits the same `tools[]` and asks the same question of it, so
    the rule cannot be about this module's own gate names."""
    doc = _doc([{"name": "osv-scanner", "status": "nothing-scanned",
                 "findings": 0, "detail": ""}])
    assert [t["name"] for t in PP.unfired(doc)] == ["osv-scanner"]


def test_a_document_with_no_tools_at_all_fires_nothing_and_hides_nothing():
    """Empty must not read as clean: `unfired` is honestly empty, and the
    caller that knows which gates were expected is the one that notices."""
    assert PP.unfired(_doc([])) == []
    assert PP.missing_gates(_doc([])) == list(PP.GATES)


def test_the_gate_names_are_the_gates_the_prepass_actually_runs(tmp_path,
                                                                monkeypatch):
    """The currency guard. `GATES` is a hand-written tuple, and a hand-written
    list of what a program does is only true until the program changes - so it
    is checked against a real run rather than trusted."""
    monkeypatch.setattr(PP, "binary", lambda name: (None, "absent for this test"))
    result = PP.prepass(tmp_path)
    assert tuple(t["name"] for t in result["tools"]) == PP.GATES


# ── review of this arc ───────────────────────────────────────────────────────


@pytest.mark.parametrize("tools", [None, "not a list", 7])
def test_a_tools_field_that_is_not_a_list_is_not_a_traceback(tools):
    """These functions are documented as reading any document of this shape,
    "not only this module's" - so they read documents this module did not
    write, and `.get("tools", [])` returns the null rather than the default
    when the key is PRESENT and null. Item 40 recorded this exact shape: a
    clean osv-scanner scan emits `"results": null` and crashed the field
    run."""
    doc = {"schema_version": 1, "scope": {}, "tools": tools,
           "summary": {"total": 0}}
    assert PP.unfired(doc) == []
    assert PP.missing_gates(doc) == list(PP.GATES)
    assert PP.exit_code(doc) == 3


def test_a_tool_row_that_is_not_a_mapping_does_not_take_the_rule_down():
    """The row level of the same question."""
    doc = _doc(["not a row", {"name": "gitleaks", "status": PP.STATUS_OK,
                              "findings": 0, "detail": ""}])
    assert [t.get("name") for t in PP.unfired(doc)] == [None]
    assert "ast-grep" in PP.missing_gates(doc)


# ── the class sweep, inside this very file ───────────────────────────────────
#
# The ast-grep branch parses its tool's output inside a try, with a comment
# explaining that a crash does not parse. The gitleaks branch, one `elif`
# away, parsed an EXTERNAL tool's report file with a bare `json.loads`. A
# report gitleaks did not finish writing therefore took the whole pre-pass
# down - and an uncaught exception exits 1, which in this module's own
# vocabulary means "every gate ran, findings". A crash reading as the benign
# findings exit is the defect the `fe75d6d` pass fixed for the smoke.


def _gitleaks_writing(report_text: bytes):
    """A gitleaks run that reports leaks and leaves `report_text` behind."""
    def fake(cmd, timeout=300):
        for i, arg in enumerate(cmd):
            if arg == "--report-path":
                Path(cmd[i + 1]).write_bytes(report_text)
        return PP.LEAK_EXIT, "", ""
    return fake


@pytest.mark.parametrize("report", [
    b'[{"RuleID": "x", trunca',          # killed mid-write
    b"",                                  # created and never written
    b'\xff\xfe not utf 8',                # unreadable bytes
    b'{"not": "an array"}',               # the wrong shape entirely
    b"null",                              # parses fine, and is not findings
])
def test_a_gitleaks_report_it_cannot_read_is_an_error_not_a_traceback(
        tmp_path, monkeypatch, report):
    monkeypatch.setattr(PP, "run_tool", _gitleaks_writing(report))
    monkeypatch.setattr(PP, "binary",
                        lambda name: (Path("gitleaks"), "") if name == "gitleaks"
                        else (None, "absent for this test"))
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    result = PP.prepass(tmp_path)
    row = next(t for t in result["tools"] if t["name"] == "gitleaks")
    assert row["status"] == "error" and row["detail"]
    # ...and an unfired gate outranks findings, which is the whole point.
    assert PP.exit_code(result) == 3


def test_a_gitleaks_report_it_can_read_still_reports_the_leak(tmp_path,
                                                              monkeypatch):
    """The negative control: guarding the parse must not swallow a real one."""
    monkeypatch.setattr(PP, "run_tool",
                        _gitleaks_writing(json.dumps([GITLEAKS_ROW]).encode()))
    monkeypatch.setattr(PP, "binary",
                        lambda name: (Path("gitleaks"), "") if name == "gitleaks"
                        else (None, "absent for this test"))
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    result = PP.prepass(tmp_path)
    row = next(t for t in result["tools"] if t["name"] == "gitleaks")
    assert row["status"] == PP.STATUS_OK and row["findings"] == 1


# ── the door, not the branch ─────────────────────────────────────────────────
#
# The gitleaks-report fix above closed ONE way this gate could crash. The class
# is the door: `main()` had no top-level guard, and an uncaught exception makes
# Python exit 1 - which in this module's own contract means "every gate ran,
# findings, NOT a failure". So any unexpected failure forged the benign answer.
# The module already owns the right word for it: 3, could-not-look, which its
# own rule says outranks 1.


def test_an_unexpected_failure_is_could_not_look_not_findings(tmp_path, capsys,
                                                              monkeypatch):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")

    def boom(name):
        raise RuntimeError("a disk error, say")

    monkeypatch.setattr(PP, "binary", boom)
    code = PP.main(["--target", str(tmp_path), "--out", str(tmp_path / "o")])
    assert code == 3
    # ONE readouterr(): the second call returns an empty capture, which
    # would have made this assertion pass over an empty message.
    printed = capsys.readouterr()
    out = printed.out + printed.err
    assert "a disk error" in out


def test_the_failure_is_named_rather_than_swallowed(tmp_path, capsys,
                                                    monkeypatch):
    """A blanket except that HIDES is this project's item-37 defect. This one
    prints the exception type and message: the point is a distinguishable exit
    code plus a reason, never a quiet 3."""
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(PP, "binary",
                        lambda name: (_ for _ in ()).throw(KeyError("a-key")))
    PP.main(["--target", str(tmp_path), "--out", str(tmp_path / "o")])
    printed = capsys.readouterr()
    assert "KeyError" in (printed.out + printed.err)


def test_argparse_still_owns_the_usage_exit(tmp_path):
    """The door catches `Exception`, not `BaseException`, and the
    difference is load-bearing: argparse refuses an unknown flag by
    raising SystemExit(2), and a door that swallowed it would answer a
    typo with "the gate could not look"."""
    with pytest.raises(SystemExit) as exit_info:
        PP.main(["--target", str(tmp_path), "--no-such-flag"])
    assert exit_info.value.code == 2


def test_a_usage_refusal_is_still_a_usage_refusal(tmp_path, capsys):
    """The negative control: the door must not turn every non-zero exit into
    3. A missing target is still 2."""
    assert PP.main(["--target", str(tmp_path / "nope"),
                    "--out", str(tmp_path / "o")]) == 2


# ── one rule, whole ──────────────────────────────────────────────────────────


def test_unreviewed_applies_all_three_rules_in_one_call():
    """`exit_code` and `provider_smoke.verify()` each decided which of these
    to apply, and verify() took two of three: it never asked about
    `scope.empty_after_exclude`, so a change whose every file was folded out
    of review recorded a GREEN smoke while exit_code returned 3 for the same
    document. There is one function now and nothing left to choose between."""
    fired = [{"name": g, "status": PP.STATUS_OK, "findings": 0, "detail": ""}
             for g in PP.GATES]

    excluded = {"schema_version": 1, "tools": fired,
                "scope": {"empty_after_exclude": True}, "summary": {"total": 0}}
    assert any("folded out" in why for why in PP.unreviewed(excluded))
    assert PP.exit_code(excluded) == 3

    absent = {"schema_version": 1, "tools": fired[:1], "scope": {},
              "summary": {"total": 0}}
    assert any("not in the report" in why
               for why in PP.unreviewed(absent, expected=PP.GATES))

    unfired_doc = {"schema_version": 1, "scope": {}, "summary": {"total": 0},
                   "tools": [{"name": "gitleaks", "status": "not-run",
                              "findings": 0, "detail": ""}]}
    assert any("did not fire" in why for why in PP.unreviewed(unfired_doc))


def test_a_clean_report_is_reviewed():
    """The negative control: the one rule must still let a real review pass."""
    doc = {"schema_version": 1, "scope": {"empty_after_exclude": False},
           "tools": [{"name": g, "status": PP.STATUS_OK, "findings": 0,
                      "detail": ""} for g in PP.GATES],
           "summary": {"total": 0}}
    assert PP.unreviewed(doc, expected=PP.GATES) == []
    assert PP.exit_code(doc) == 0


@pytest.mark.parametrize("document", [[], "a report", 7, None])
def test_a_report_that_is_not_an_object_is_not_a_traceback(document):
    """The first cut guarded the `tools` field and the rows and left the
    top-level object, so a prepass.json holding `[]` — which any live runner
    can write — raised AttributeError out of every caller."""
    assert PP.tool_rows(document) is None
    assert PP.unfired(document) == []
    assert PP.missing_gates(document) == list(PP.GATES)
    assert PP.unreviewed(document) != []
    assert PP.exit_code(document) == 3


def test_exit_code_does_not_subscript_a_document_it_did_not_write():
    """`result["scope"]` and `result["summary"]["total"]` were bare, in a
    function documented as reading other modules' documents."""
    doc = {"tools": [{"name": "g", "status": PP.STATUS_OK, "findings": 0}]}
    assert PP.exit_code(doc) == 0
    assert PP.exit_code({**doc, "summary": None}) == 0


# ── the pre-pass must not report on the harness's own copy of the repository ─
#
# Measured on a real repository: 386 of 772 secret findings sat under
# `.sdlc-worktrees/`, and ALL of them were gitleaks - ast-grep reported zero,
# because it honours the `.gitignore` that root writes. So this is not a
# property of "scanners", it is a property of each one, and only the reader can
# hold one rule for all of them. gitleaks has no path-exclusion flag to prevent
# it with, which is why the rule lives here rather than in an argument.


def test_findings_in_the_harness_own_trees_are_dropped_and_counted(tmp_path,
                                                                   monkeypatch):
    root = tmp_path / "repo"
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "policy.yml").write_text(
        "worktrees_root: .sdlc-worktrees\n", encoding="utf-8")
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")

    real = {"RuleID": "generic", "File": str(root / "app.py"), "StartLine": 1,
            "Secret": "s", "Description": "d", "Match": "m"}
    mine = {"RuleID": "generic",
            "File": str(root / ".sdlc-worktrees" / "r1" / "c" / "app.py"),
            "StartLine": 1, "Secret": "s", "Description": "d", "Match": "m"}

    monkeypatch.setattr(PP, "binary", lambda name: (Path(name), ""))

    def fake_run_tool(cmd, timeout=300, env=None):
        if "gitleaks" in str(cmd[0]):
            out = Path(cmd[cmd.index("--report-path") + 1])
            out.write_text(json.dumps([real, mine]), encoding="utf-8")
            # The gate's own "leaks found" code, not a generic failure - any
            # other non-zero is a crash to it, and this fake must speak the
            # vocabulary the real tool does.
            return PP.LEAK_EXIT, "", ""
        return 0, "[]", ""

    monkeypatch.setattr(PP, "run_tool", fake_run_tool)
    got = PP.prepass(root)

    leaks = [t for t in got["tools"] if t["name"] == "gitleaks"][0]
    assert leaks["own_trees"] == 1, leaks
    assert leaks["findings"] == 1
    assert "worktree" in leaks["detail"]
    assert got["summary"]["total"] == 1
    for source in got["sources"]:
        for diag in source["diagnostics"]:
            assert ".sdlc-worktrees" not in diag["location"]["path"]


# ── item 109: a code rule judges the change, not the file ───────────────────
#
# The pass copies every touched file and scanned all of it, so a smell that
# predated the change was reported against whoever next edited that file —
# right for the secret gate (a credential edited back out is still in the
# file) and wrong for every rule about code somebody is answerable for.

HUNK_DIFF = """\
diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -40,3 +40,4 @@
 context
+added line
 more
 tail
"""


def diag(path: str, line: int | None) -> dict:
    """One rdjson-shaped diagnostic, the shape `parse_astgrep` emits."""
    where: dict = {"path": path}
    if line is not None:
        where["range"] = {"start": {"line": line, "column": 1}}
    return {"message": "m", "location": where, "severity": "ERROR"}


def test_touched_lines_reads_the_hunks_the_one_grammar_computes():
    """`impact.parse_diff` is the project's single diff grammar (item 20), and
    `diff_scope` was already calling it and keeping only the keys."""
    assert PP.touched_lines(HUNK_DIFF) == {"app.py": [(40, 43)]}


def test_a_finding_inside_the_change_is_the_changes():
    ranges = PP.touched_lines(HUNK_DIFF)
    assert PP.in_change(diag("app.py", 40), ranges) is True
    assert PP.in_change(diag("app.py", 43), ranges) is True


def test_a_finding_the_change_did_not_touch_is_not_attributed_to_it():
    """**The defect, stated as a test.** Line 5 of a file whose change starts at
    41 was reported against the author of that change."""
    ranges = PP.touched_lines(HUNK_DIFF)
    assert PP.in_change(diag("app.py", 5), ranges) is False
    assert PP.in_change(diag("app.py", 44), ranges) is False


def test_a_file_the_diff_never_mentions_is_not_the_changes():
    assert PP.in_change(diag("other.py", 40), PP.touched_lines(HUNK_DIFF)) is False


@pytest.mark.parametrize("spans", [None])
def test_a_touched_file_with_no_line_information_counts_as_the_changes(spans):
    """A deleted file, a mode-only change, a path the grammar declines: nothing
    here can place a finding, and guessing the other way would HIDE one."""
    assert PP.in_change(diag("x.py", 9), {"x.py": spans}) is True


def test_a_finding_with_no_line_is_attributed_rather_than_dropped():
    """It cannot be placed, and the file was touched. A rule finding nobody
    sees is worse than one somebody re-reads."""
    assert PP.in_change(diag("app.py", None), PP.touched_lines(HUNK_DIFF)) is True


def test_partition_keeps_every_finding():
    """**Zero silent drops.** A quietly smaller report is item 97's `zeroed`
    shape; a smaller report that says so is honest."""
    ranges = PP.touched_lines(HUNK_DIFF)
    diags = [diag("app.py", 40), diag("app.py", 5), diag("other.py", 1)]
    mine, theirs = PP.partition(diags, ranges)
    assert len(mine) == 1 and len(theirs) == 2
    assert len(mine) + len(theirs) == len(diags), "a finding went missing"


def test_the_report_separates_them_and_counts_what_it_set_aside(repo, monkeypatch):
    """**Pre-existing findings are CARRIED, never dropped** - the zero-silent-
    drops rule, and the whole difference between this and narrowing the scan.

    The first cut of this test monkeypatched `binary` to None, so ast-grep
    never ran and it asserted nothing about the partition at all - a mutation
    that DROPPED the pre-existing findings survived it. It runs the real path
    through the suite's own fake now."""
    (repo / "app.py").write_text("x = 1" + chr(10), encoding="utf-8")
    rows = [
        {"file": "app.py", "range": {"start": {"line": 40, "column": 0}},
         "ruleId": "inside", "message": "in the change", "severity": "error"},
        {"file": "app.py", "range": {"start": {"line": 4, "column": 0}},
         "ruleId": "outside", "message": "predates it", "severity": "error"},
    ]
    fake_binaries(monkeypatch)
    monkeypatch.setattr(PP, "run_tool", FakeRun(astgrep=(0, json.dumps(rows), "")))
    found = PP.prepass(repo, diff_text=HUNK_DIFF)

    by_name = {t["name"]: t for t in found["tools"]}
    assert by_name["ast-grep"]["findings"] == 1, "only the change's finding counts"
    assert by_name["ast-grep"]["pre_existing"] == 1, "and the rest are COUNTED"
    assert "reported separately" in by_name["ast-grep"]["detail"]

    sources = {s["source"]["name"]: s["diagnostics"] for s in found["sources"]}
    assert [d["message"] for d in sources["ast-grep"]] == ["in the change"]
    assert [d["message"] for d in sources["ast-grep (pre-existing)"]] == ["predates it"]


def test_a_pre_existing_finding_in_an_own_tree_is_counted_and_totals_stay_consistent(
        tmp_path, monkeypatch):
    """**T113: a pre-existing finding that ALSO sits in the harness's own worktree
    copy.** It rides a source named `ast-grep (pre-existing)`, which no tool ROW
    is named — so the by-NAME own-tree de-dup emptied it while crediting nobody:
    `own_trees` stayed 0, `pre_existing` still claimed the pre-drop count (lost
    evidence), and `by_severity` (which counted the surviving pre-existing
    source) disagreed with `summary.total` (which did not). De-dup by identity,
    count what was set aside, one consistent total."""
    root = tmp_path / "repo"
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / "policy.yml").write_text(
        "worktrees_root: .sdlc-worktrees\n", encoding="utf-8")
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")

    rows = [
        {"file": "app.py", "range": {"start": {"line": 40, "column": 0}},
         "ruleId": "inside", "message": "in the change", "severity": "error"},
        {"file": "app.py", "range": {"start": {"line": 4, "column": 0}},
         "ruleId": "outside", "message": "predates it", "severity": "error"},
        # pre-existing AND in an own-tree copy: a duplicate at a path the
        # repository does not have.
        {"file": ".sdlc-worktrees/r1/c/app.py",
         "range": {"start": {"line": 4, "column": 0}},
         "ruleId": "outside", "message": "a duplicate in an own tree",
         "severity": "error"},
    ]
    fake_binaries(monkeypatch)
    monkeypatch.setattr(PP, "run_tool", FakeRun(astgrep=(0, json.dumps(rows), "")))
    found = PP.prepass(root, diff_text=HUNK_DIFF)

    ast = {t["name"]: t for t in found["tools"]}["ast-grep"]
    assert ast["own_trees"] == 1, ast                 # counted, not silently dropped
    assert ast["pre_existing"] == 1, ast              # reflects what is REPORTED
    sources = {s["source"]["name"]: s["diagnostics"] for s in found["sources"]}
    for diags in sources.values():
        for d in diags:
            assert ".sdlc-worktrees" not in d["location"]["path"], d
    assert [d["message"] for d in sources["ast-grep (pre-existing)"]] == ["predates it"]
    # ONE consistent total: total (gate material) equals by_severity's sum.
    summary = found["summary"]
    assert summary["total"] == 1, summary
    assert sum(summary["by_severity"].values()) == summary["total"], summary


def test_the_secret_gate_is_not_narrowed_by_any_of_this():
    """gitleaks keeps judging whole files, and the split is per TOOL because
    the question differs per tool. A credential edited back out of a file is
    still in the file — this module's own header says so, and item 109 must not
    quietly overturn it."""
    import inspect

    src = inspect.getsource(PP.prepass)
    astgrep = src[src.index("ast-grep: the rule pass"):]
    assert "partition(diags, ranges)" in astgrep, "the rule pass must partition"
    secret = src[src.index("gitleaks: the secret gate"):src.index("ast-grep: the rule pass")]
    assert "partition" not in secret, "the secret gate must NOT be narrowed"


# ── the PREMISE, spawned: the real binaries through the real gate ─────────────
#
# Everything above replaces `run_tool`, so it holds the code to a TRANSCRIPTION
# of what the pinned tools emit (`GITLEAKS_ROW`, `ASTGREP_ROW`). That tests the
# plumbing and never the premise these transcriptions rest on — that the pinned
# gitleaks still recognises a secret and the pinned ast-grep still matches a
# shipped rule, each writing the shape the parser reads. Before these two tests,
# the only place the real binaries reached the gate was
# `test_artifact_crossings.py::test_the_pre_pass_writes_where_the_smoke_looks`,
# over a CLEAN tree asserting only that a `tools` key exists — a producer/reader
# weld, not a positive. So no test anywhere watched either gate FIRE on real
# output, which is why a leader could not answer "does the secret gate work?"
# from this suite and had to send a worker with a probe (T343 -> T345).
#
# T345 was reverted once (d1f1ba9): the ast-grep half asserted the shipped
# breakpoint rule fires, and on Linux the vendored ast-grep was the deprecated
# `sg` wrapper, which dies on every invocation with nothing on stdout — so the
# rule matched zero and the gate had been dead-but-green there. T365 vendors the
# standalone `ast-grep` binary instead, and these re-land on top of it.


def _real_gate_binary(fetch_name: str, binary_name: str):
    """Resolve a vendored gate binary, following the item-189 presence contract.

    `fetch_name` is what CI installs and what `third_party.SPAWNED_BY_TESTS`
    records; `binary_name` is the on-disk name the pre-pass itself resolves. The
    two coincide for every gate today — ast-grep is vendored as `ast-grep` now
    that the deprecated `sg` wrapper is retired (T365) — but they are kept
    separate because a manifest key and the on-disk binary need not agree, and
    `binary_name(spec, plat)` is the only thing that should ever derive one from
    the other. Spawn when it resolves. When it does not: a bare skip is
    invisible locally and reddens CI under `SDLC_FORBID_SKIPS`, so this
    early-returns instead — a pass that ran nothing, not a skip. Under
    `SDLC_REQUIRE_VENDORED_BINARIES` (set on CI, which fetches these) a missing
    binary is a FAILURE here, so CI cannot fall back to the empty pass while
    believing it spawned. The `SPAWNED_BY_TESTS` assertion is the weld that makes
    the local early-return safe: `test_third_party.py` fails CI if a tool in that
    set does not resolve under the flag, so absence here can only ever be local.
    """
    from tools import third_party as tp

    assert fetch_name in tp.SPAWNED_BY_TESTS, (
        f"{fetch_name} is spawned here but is not in SPAWNED_BY_TESTS, so CI "
        f"would not fetch it and this real-binary test would silently run "
        f"nothing on the gate of record")
    exe, why = PP.binary(binary_name)
    if exe is None:
        if os.environ.get("SDLC_REQUIRE_VENDORED_BINARIES") == "1":
            pytest.fail(
                f"{fetch_name} did not resolve under "
                f"SDLC_REQUIRE_VENDORED_BINARIES=1: {why}. CI fetches it, so a "
                f"real-binary premise test that quietly ran nothing is exactly "
                f"the silent fallback that flag exists to forbid")
        assert why.strip(), (
            f"{binary_name} did not resolve and gave no reason; an unexplained "
            f"absence reads exactly like a tool that ran and found nothing")
        return None
    assert Path(exe).exists(), f"{binary_name} resolved to {exe}, which is absent"
    return exe


def test_the_real_secret_scanner_fires_on_a_runtime_built_credential(tmp_path):
    """The premise, not the plumbing: the REAL gitleaks, spawned by `PP.prepass`
    over a real tree, DETECTS a real credential and the boundary REDACTS it.

    Every other gitleaks test here replaces `run_tool`, so it asserts what the
    code does with a canned report row (`GITLEAKS_ROW`); none asks whether the
    pinned binary still recognises a secret, still honours `--exit-code 9`, and
    still writes the report shape `parse_gitleaks` reads. This one does — so a
    `gitleaks` upgrade that changed any of those becomes a named red here rather
    than a false `gitleaks: ok, 0` at every push that cites this gate.

    The credential is a real provider shape with 36 DISTINCT body characters
    (rule 31: shape alone does not fire and entropy alone does not fire; the
    pinned tool needs both), assembled at runtime from code points so no token
    literal ever lands in this tracked file, and unlinked in a `finally`.
    """
    exe = _real_gate_binary("gitleaks", "gitleaks")
    if exe is None:
        return
    prefix = "".join(chr(c) for c in (0x67, 0x68, 0x70, 0x5F))   # a real provider prefix
    alphabet = string.ascii_letters + "0123456789"              # 62 distinct code points
    token = prefix + "".join(alphabet[(i * 7 + 3) % len(alphabet)] for i in range(36))
    target = tmp_path / "repo"
    target.mkdir()
    planted = target / "config.py"
    planted.write_text(f'SECRET = "{token}"\n', encoding="utf-8")
    try:
        result = PP.prepass(target)
    finally:
        planted.unlink()

    gl = next(t for t in result["tools"] if t["name"] == "gitleaks")
    assert gl["status"] == "ok", gl              # it RAN
    assert gl["findings"] >= 1, gl               # and it FIRED — the premise
    assert token not in json.dumps(result), (
        "the real matched secret reached the document; redaction at the boundary "
        "is the one promise a leak report must keep, and it is untested until the "
        "matched text is a real secret rather than a placeholder")
    diags = next(s["diagnostics"] for s in result["sources"]
                 if s["source"]["name"] == "gitleaks")
    hit = next(d for d in diags if d["location"]["path"] == "config.py")
    assert hit["severity"] == "ERROR", hit
    assert hit["code"]["value"], hit             # a real RuleID round-tripped through parse_gitleaks
    assert hit["fingerprint"].startswith("config.py:"), hit
    assert hit["location"]["range"]["start"]["line"] == 1, hit


def test_the_real_rule_matcher_fires_on_a_runtime_built_violation(tmp_path):
    """The premise for the OTHER gate: the REAL ast-grep, spawned by `PP.prepass`,
    matches a SHIPPED rule against real source, and the zero-based line it emits
    lands one-based on the offending line.

    The fakes assert the `+ 1` conversion against a canned row whose line is a
    literal 0; nothing asks whether the pinned ast-grep still emits zero-based
    lines, still puts the JSON on stdout for an error-severity match, or still
    names the rule the shipped file declares. A `breakpoint()` is planted at a
    line THIS test controls, so the conversion is checked against a moving line
    rather than a constant, and the fired rule id is welded to the shipped rules.

    This is the half T345 was reverted for: on Linux the vendored ast-grep was
    the deprecated `sg` wrapper, which never ran, so the rule matched zero. It
    resolves `ast-grep` now (T365), so the premise holds on the gate of record.
    """
    exe = _real_gate_binary("ast-grep", "ast-grep")
    if exe is None:
        return
    source = "def f():\n    x = 1\n    breakpoint()\n    return x\n"
    target = tmp_path / "repo"
    target.mkdir()
    (target / "dbg.py").write_text(source, encoding="utf-8")
    expected_line = source.splitlines().index("    breakpoint()") + 1

    result = PP.prepass(target)

    ag = next(t for t in result["tools"] if t["name"] == "ast-grep")
    assert ag["status"] == "ok", ag              # it RAN (a findings exit, not a crash)
    assert ag["findings"] >= 1, ag               # and it FIRED — the premise
    diags = next(s["diagnostics"] for s in result["sources"]
                 if s["source"]["name"] == "ast-grep")
    hit = next(d for d in diags if d["location"]["path"] == "dbg.py")
    assert hit["severity"] == "ERROR", hit
    assert hit["location"]["range"]["start"]["line"] == expected_line, hit
    rules_text, _ = PP.collect_rules(target, [])
    assert hit["code"]["value"] and hit["code"]["value"] in rules_text, (
        f"the fired rule id {hit['code']['value']!r} is not one the shipped rule "
        f"set declares; the real match and the shipped rules have drifted apart")
