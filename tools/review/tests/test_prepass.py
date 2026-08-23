"""Item 25 — the deterministic review pre-pass, seam-tested against real transcriptions.

The canned tool outputs here were transcribed from the pinned binaries themselves
(gitleaks 8.30.1, ast-grep 0.45.1, run on 2026-08-21) — the parsers are held to what
the tools actually emit, not to documentation. The subprocess seam (`run_tool`) is
replaced in tests because CI deliberately does not fetch vendored binaries; the
absent-binary path is therefore not a skip but a first-class outcome with its own
tests. Canned secrets are placeholder-shaped on purpose: a realistic token in test
source would be flagged by the harness's own CI secret scan — correctly.
"""
from __future__ import annotations

import json
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
    assert result["tools"][0] == {"name": "gitleaks", "status": "ok",
                                  "detail": "", "findings": 1}
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


def test_an_unfired_gate_outranks_findings(repo, monkeypatch):
    """Exit 3 beats exit 1: the change is not reviewed while a gate never ran —
    for the SECRET gate especially, 'the other tool found things' is not a
    substitute for the scan that did not happen."""
    monkeypatch.setattr(PP, "binary",
                        lambda name: ((None, f"not run: vendored {name} missing "
                                             f"(fetch: python tools/third_party.py "
                                             f"--fetch)")
                                      if name == "gitleaks"
                                      else (Path("/fake/sg.exe"), "")))
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
