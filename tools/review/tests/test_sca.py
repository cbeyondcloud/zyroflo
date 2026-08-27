"""Item 40 — the SCA gate, against osv-scanner 2.5.1's real output.

Every canned shape here was transcribed from the pinned binary run on
2026-08-24, not from documentation: the exit codes, the `results[]` nesting,
the fact that `max_severity` is a CVSS SCORE rather than a label, and the fact
that a `pom.xml` naming an unpublished module fails the entire scan. The
subprocess seam (`prepass.run_tool`) is replaced because CI deliberately does
not fetch vendored binaries, so the absent-binary path is a first-class
outcome with its own tests rather than a skip.

The severity half is the item's finding: rdjson has three levels and `block_on`
has five, so before this the only way a pre-pass tool could report a CVSS 9.8
was `ERROR`, which `loop.from_prepass` mapped to `high` — and a profile
blocking on `critical` alone would have let it through.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.review import loop as LOOP  # noqa: E402
from tools.review import prepass as PP  # noqa: E402
from tools.review import sca as SCA  # noqa: E402
from tools.sdlc import worktree as W  # noqa: E402

#: One `results[]` entry as osv-scanner 2.5.1 writes it (values inert but the
#: nesting, key names and the string-typed `max_severity` verbatim).
REPORT = {
    "results": [{
        "source": {"path": "/scan/api/requirements.txt", "type": "lockfile"},
        "packages": [{
            "package": {"name": "requests", "version": "2.19.1", "ecosystem": "PyPI"},
            "groups": [
                {"ids": ["PYSEC-2018-28", "GHSA-x84v-xcm2-53pg"],
                 "aliases": ["CVE-2018-18074", "GHSA-x84v-xcm2-53pg",
                             "PYSEC-2018-28"],
                 "max_severity": "7.5"},
                {"ids": ["PYSEC-2023-74"], "aliases": ["CVE-2023-32681"],
                 "max_severity": "6.1"},
            ],
        }],
    }],
}

#: The tool's own log line, which is the ONLY place it says what it read: the
#: JSON carries sources with findings and nothing else.
STDERR_SCANNED = (
    "Starting filesystem walk for root: /scan\n"
    "Scanned /scan/api/requirements.txt file and found 2 packages\n"
    "End status: 16 dirs visited, 30 inodes visited, 1 Extract calls\n")

#: Verbatim shape of the failure a private module produces. Measured: the whole
#: scan exits 127, not just that manifest.
STDERR_UNRESOLVED = (
    "Warning: enricher transitivedependency/pomxml may be risky when run on "
    "untrusted artifacts.\n"
    "Error during extraction: (extracting as transitivedependency/pomxml) failed "
    "resolution for api/pom.xml: failed resolving {Maven:example:api"
    "[Concrete:2.1.0] {}}: version Maven:example:shared[Concrete:2.1.0]: not found\n")


def stub(monkeypatch, replies):
    """Replace the subprocess seam with a queue of (exit, stdout, stderr)."""
    calls: list[list[str]] = []

    def fake(cmd, timeout=0):
        calls.append(list(cmd))
        return replies[min(len(calls) - 1, len(replies) - 1)]

    monkeypatch.setattr(PP, "run_tool", fake)
    monkeypatch.setattr(PP, "binary", lambda name: (Path("/bin/osv-scanner"), ""))
    return calls


def component(tmp_path: Path, name: str = "api") -> tuple[Path, list[dict]]:
    (tmp_path / name).mkdir(parents=True, exist_ok=True)
    return tmp_path, [{"name": name, "path": name}]


def scanned_log(root: Path, rel: str = "api/requirements.txt") -> str:
    """`STDERR_SCANNED` rooted where the run actually happens. The tool logs an
    ABSOLUTE path, so a canned one from another root is a fixture that cannot
    be relativised - which is a test bug, not a finding."""
    return STDERR_SCANNED.replace("/scan/api/requirements.txt",
                                  (root / rel).as_posix())


# ── a CVSS score is not a label ──────────────────────────────────────────────


@pytest.mark.parametrize("score,expect", [
    ("9.8", "critical"), ("9.0", "critical"), ("8.9", "high"), ("7.0", "high"),
    ("6.9", "medium"), ("4.0", "medium"), ("3.9", "low"), ("0.1", "low"),
])
def test_cvss_scores_land_in_the_standard_bands(score, expect):
    """The v3 cut points, because they are the ones the scores were assigned
    against - a different mapping would make `block_on: [critical]` mean
    something no advisory database does."""
    assert SCA.severity_of(score) == expect


@pytest.mark.parametrize("value", ["", None, "unknown", "  "])
def test_an_unscored_vulnerability_is_ungraded_not_low(value):
    """OSV records a vulnerability nobody has assessed. A gate that reads that
    as the mildest thing it knows has decided the question it was asked."""
    assert SCA.severity_of(value) == ""


# ── the parser, against the real shape ───────────────────────────────────────


def test_one_diagnostic_per_advisory_group_not_per_id():
    """A group is one vulnerability across databases. A row per id would report
    a single CVE three times and make the count measure how many databases
    carry it."""
    diags = SCA.parse(REPORT, Path("/scan"))
    assert len(diags) == 2, [d["code"]["value"] for d in diags]


def test_a_diagnostic_states_its_severity_in_block_ons_vocabulary():
    diags = SCA.parse(REPORT, Path("/scan"))
    assert [d["severity_class"] for d in diags] == ["high", "medium"]
    assert diags[0]["severity"] == "ERROR" and diags[1]["severity"] == "WARNING"


def test_the_message_names_the_package_and_the_advisory():
    diag = SCA.parse(REPORT, Path("/scan"))[0]
    assert "PyPI requests 2.19.1" in diag["message"]
    assert "CVE-2018-18074" in diag["message"] and "CVSS 7.5" in diag["message"]


def test_the_location_is_the_manifest_not_an_invented_line():
    """A vulnerability is a property of a resolved dependency, and a plausible
    line inside a lockfile is a citation nobody can check."""
    diag = SCA.parse(REPORT, Path("/scan"))[0]
    assert diag["location"]["path"] == "api/requirements.txt"
    assert diag["location"]["range"]["start"]["line"] == 1


def test_an_ungraded_vulnerability_says_so_in_the_message():
    report = json.loads(json.dumps(REPORT))
    report["results"][0]["packages"][0]["groups"] = [
        {"ids": ["OSV-1"], "aliases": [], "max_severity": ""}]
    diag = SCA.parse(report, Path("/scan"))[0]
    assert diag["severity_class"] == ""
    assert "UNGRADED" in diag["message"]


def test_the_tools_own_log_says_what_it_read():
    assert SCA.scanned_paths(STDERR_SCANNED, Path("/scan")) == ["api/requirements.txt"]
    assert SCA.scanned_paths("", Path("/scan")) == []


# ── the exits, all measured ──────────────────────────────────────────────────


def test_exit_one_with_json_is_findings_not_failure(tmp_path, monkeypatch):
    target, comps = component(tmp_path)
    stub(monkeypatch, [(1, json.dumps(REPORT), STDERR_SCANNED)])
    doc = SCA.run(target, comps)
    assert doc["tools"][0]["status"] == "ok" and doc["tools"][0]["findings"] == 2


def test_exit_zero_is_clean_and_still_says_what_it_read(tmp_path, monkeypatch):
    target, comps = component(tmp_path)
    stub(monkeypatch, [(0, '{"results":null}', scanned_log(target))])
    doc = SCA.run(target, comps)
    assert doc["tools"][0]["status"] == "ok" and doc["tools"][0]["findings"] == 0
    assert doc["tools"][0]["per_component"][0]["scanned"] == ["api/requirements.txt"]


def test_a_clean_scan_emits_null_results_and_must_not_crash(tmp_path, monkeypatch):
    """Found by the field run. osv-scanner writes `"results": null` when it
    finds nothing - the key is PRESENT, so a `.get("results", [])` default never
    fires and the commonest case in the world raised TypeError. The canned
    fixtures had all spelled it `[]`, which is the tool being described rather
    than transcribed."""
    target, comps = component(tmp_path)
    stub(monkeypatch, [(0, '{"results":null}', scanned_log(target))])
    doc = SCA.run(target, comps)
    assert doc["tools"][0]["status"] == "ok" and doc["tools"][0]["findings"] == 0
    assert SCA.parse({"results": None}, Path("/scan")) == []
    assert SCA.parse({"results": [{"source": None, "packages": None}]},
                     Path("/scan")) == []


def test_a_scan_that_read_nothing_is_not_a_pass(tmp_path, monkeypatch):
    """`results[]` holds only sources WITH findings, so a clean scan and a scan
    that read nothing produce the same empty array."""
    target, comps = component(tmp_path)
    stub(monkeypatch, [(0, '{"results":[]}', "no lockfiles here\n")])
    doc = SCA.run(target, comps)
    assert doc["tools"][0]["status"] == "nothing-scanned"
    assert "1 component(s) had no manifest" in doc["tools"][0]["detail"]
    assert "not the same as clean" in doc["tools"][0]["per_component"][0]["detail"]


def test_the_tool_level_detail_is_a_count_not_a_wall(tmp_path, monkeypatch):
    """The field run printed every component's note concatenated into one line
    that read as if all of it applied to the whole scan."""
    (tmp_path / "quiet").mkdir()
    (tmp_path / "api").mkdir(exist_ok=True)
    stub(monkeypatch, [(0, '{"results":null}', "")])
    doc = SCA.run(tmp_path, [{"name": "api", "path": "api"},
                             {"name": "quiet", "path": "quiet"}])
    detail = doc["tools"][0]["detail"]
    assert "2 component(s) had no manifest" in detail, detail
    assert "not the same as clean" not in detail, "the row's words leaked upward"
    assert all("not the same as clean" in r["detail"]
               for r in doc["tools"][0]["per_component"])


def test_a_scan_where_some_components_were_read_is_not_a_tool_failure(
        tmp_path, monkeypatch):
    """A charts or docs component legitimately has no dependencies. Exit 3 for
    every estate that owns one would make the gate permanently noisy, so the
    tool-level status is `nothing-scanned` only when NOTHING anywhere was read
    — and the per-component rows carry the rest."""
    (tmp_path / "quiet").mkdir()
    calls = []

    def fake(cmd, timeout=0):
        calls.append(cmd)
        found = "api" in cmd[-1]
        return (1 if found else 0,
                json.dumps(REPORT) if found else '{"results":null}',
                scanned_log(tmp_path) if found else "")

    monkeypatch.setattr(PP, "run_tool", fake)
    monkeypatch.setattr(PP, "binary", lambda name: (Path("/bin/osv-scanner"), ""))
    target, comps = component(tmp_path)
    doc = SCA.run(target, comps + [{"name": "quiet", "path": "quiet"}])
    assert doc["tools"][0]["status"] == "ok"
    assert [r["status"] for r in doc["tools"][0]["per_component"]] ==         ["ok", "nothing-scanned"]


def test_an_error_on_both_attempts_is_an_error(tmp_path, monkeypatch):
    target, comps = component(tmp_path)
    stub(monkeypatch, [(127, "", "failed to resolve path\n")])
    doc = SCA.run(target, comps)
    assert doc["tools"][0]["status"] == "error"
    assert "1 component(s) errored (api)" in doc["tools"][0]["detail"]
    assert "exit 127" in doc["tools"][0]["per_component"][0]["detail"]


def test_an_unpublished_module_degrades_the_scan_and_says_so(tmp_path, monkeypatch):
    """Measured: a pom.xml naming a module the public registry does not have
    fails the WHOLE scan. Retrying without that resolver keeps the gate usable
    on an estate, at a cost this states rather than swallows."""
    target, comps = component(tmp_path)
    calls = stub(monkeypatch, [(127, "", STDERR_UNRESOLVED),
                               (0, '{"results":[]}', STDERR_SCANNED)])
    doc = SCA.run(target, comps)
    row = doc["tools"][0]["per_component"][0]
    assert row["status"] == "ok"
    assert "DIRECT dependencies only" in row["degraded"]
    assert len(calls) == 2 and SCA.TRANSITIVE_PLUGIN in " ".join(calls[1])
    assert SCA.TRANSITIVE_PLUGIN not in " ".join(calls[0]), \
        "the first attempt must keep transitive coverage where it works"


# ── the gate had never scanned anything, and the harness was why ─────────────
#
# Found by the first field run. osv-scanner's walker honours git's exclusions,
# and P5 points it at a worktree the harness has deliberately told git to
# ignore - so the scan root was pruned before the walk began and the gate read
# zero manifests on every run, on every platform, since the day it shipped.
# Measured on 2.5.1: `1 dirs visited, 1 inodes visited, 0 Extract calls`
# there, against a full walk for the same module scanned in place.


def test_git_ignores_a_worktree_because_the_harness_told_it_to(tmp_path):
    """The premise, proven with real git rather than asserted.

    This is half the defect and the half nobody would look for: `worktree.py`
    writes an ignore-everything marker into the worktrees root ON PURPOSE, so
    that a run stops dirtying the repository it is about. Both modules are
    correct alone. Together they make a scanner blind.
    """
    repo = tmp_path / "repo"
    (repo / ".sdlc-worktrees" / "run-1" / "api").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / ".sdlc-worktrees" / ".gitignore").write_text(
        W.SELF_IGNORE, encoding="utf-8", newline="\n")
    manifest = repo / ".sdlc-worktrees" / "run-1" / "api" / "requirements.txt"
    manifest.write_text("requests==2.19.1\n", encoding="utf-8")

    ignored = subprocess.run(
        ["git", "check-ignore", "-q",
         str(manifest.relative_to(repo)).replace("\\", "/")],
        cwd=str(repo))
    assert ignored.returncode == 0, (
        "the premise has changed: git no longer ignores a run's worktree, so "
        "the reason for --no-ignore should be re-measured rather than kept")


def test_the_scan_does_not_honour_the_ignore_that_hides_every_worktree(
        tmp_path, monkeypatch):
    """The fix. Without this flag the walk stops at the scan root."""
    target, comps = component(tmp_path)
    calls = stub(monkeypatch, [(0, '{"results":null}', scanned_log(tmp_path))])
    SCA.run(target, comps)
    assert SCA.NO_IGNORE in calls[0], (
        "the gate would report 'nothing was scanned' for every component of "
        "every run, which is honest and useless")


def test_the_retry_is_not_where_the_flag_gets_dropped(tmp_path, monkeypatch):
    """A pruned scan exits non-zero, so it lands on the degrade path - and a
    retry that dropped the flag would scan nothing just as thoroughly, while
    now also blaming an unpublished module for it."""
    target, comps = component(tmp_path)
    calls = stub(monkeypatch, [(127, "", STDERR_UNRESOLVED),
                               (0, '{"results":[]}', scanned_log(tmp_path))])
    SCA.run(target, comps)
    assert len(calls) == 2
    assert all(SCA.NO_IGNORE in call for call in calls)


def test_an_absent_binary_is_not_run_never_clean(tmp_path, monkeypatch):
    target, comps = component(tmp_path)
    monkeypatch.setattr(PP, "binary", lambda name: (None, "not run: vendored missing"))
    doc = SCA.run(target, comps)
    assert doc["tools"][0]["status"] == "not-run"
    assert doc["summary"]["total"] == 0


def test_a_component_directory_that_is_not_there_is_an_error(tmp_path, monkeypatch):
    stub(monkeypatch, [(0, '{"results":null}', "")])
    doc = SCA.run(tmp_path, [{"name": "ghost", "path": "ghost"}])
    assert doc["tools"][0]["per_component"][0]["status"] == "error"


# ── the item's finding: a critical CVE can be critical ───────────────────────


def test_a_critical_cve_blocks_a_profile_that_blocks_on_critical():
    """Before this item the only severity a pre-pass tool could state was
    rdjson's ERROR, which `from_prepass` mapped to `high`."""
    report = json.loads(json.dumps(REPORT))
    report["results"][0]["packages"][0]["groups"] = [
        {"ids": ["CVE-9"], "aliases": ["CVE-9"], "max_severity": "9.8"}]
    doc = SCA._document(Path("/scan"), [{"name": SCA.TOOL, "status": "ok",
                                         "detail": "", "findings": 1,
                                         "components": ["api"]}],
                        [{"source": {"name": SCA.TOOL},
                          "diagnostics": SCA.parse(report, Path("/scan"))}],
                        [{"name": "api"}])
    findings = LOOP.from_prepass(doc)
    assert [f["severity"] for f in findings] == ["critical"]
    assert LOOP.assess(findings, ["critical"])["open"], \
        "a CVSS 9.8 did not block a profile blocking on critical"


def test_the_rdjson_projection_alone_would_have_said_high():
    """The gap, asserted directly: strip the stated severity and the same
    finding degrades to `high`."""
    report = json.loads(json.dumps(REPORT))
    report["results"][0]["packages"][0]["groups"] = [
        {"ids": ["CVE-9"], "aliases": ["CVE-9"], "max_severity": "9.8"}]
    diags = SCA.parse(report, Path("/scan"))
    for diag in diags:
        diag.pop(LOOP.SEVERITY_FIELD)
    doc = {"sources": [{"source": {"name": SCA.TOOL}, "diagnostics": diags}]}
    assert [f["severity"] for f in LOOP.from_prepass(doc)] == ["high"]
    assert not LOOP.assess(LOOP.from_prepass(doc), ["critical"])["open"], \
        "this is the hole the stated severity closes"


def test_an_ungraded_cve_holds_the_loop_open():
    diags = [{"message": "m", "location": {"path": "p", "range": {"start": {"line": 1}}},
              "severity": "WARNING", "severity_class": "", "code": {"value": "OSV-1"}}]
    doc = {"sources": [{"source": {"name": SCA.TOOL}, "diagnostics": diags}]}
    verdict = LOOP.assess(LOOP.from_prepass(doc), ["critical"])
    assert verdict["ungraded"] and verdict["open"]


def test_a_stated_severity_outside_the_vocabulary_is_refused():
    diags = [{"message": "m", "location": {"path": "p", "range": {"start": {"line": 1}}},
              "severity": "ERROR", "severity_class": "catastrophic",
              "code": {"value": "OSV-1"}}]
    doc = {"sources": [{"source": {"name": SCA.TOOL}, "diagnostics": diags}]}
    with pytest.raises(LOOP.LoopError, match="must speak it exactly"):
        LOOP.from_prepass(doc)


def test_a_tool_that_states_nothing_keeps_the_old_mapping():
    """The change is additive: a diagnostic with no stated severity goes
    through exactly the path it always did."""
    diags = [{"message": "m", "location": {"path": "p", "range": {"start": {"line": 1}}},
              "severity": "ERROR", "code": {"value": "R1"}}]
    doc = {"sources": [{"source": {"name": "ast-grep"}, "diagnostics": diags}]}
    assert LOOP.from_prepass(doc)[0]["severity"] == "high"
    doc["sources"][0]["source"]["name"] = "gitleaks"
    assert LOOP.from_prepass(doc)[0]["severity"] == "critical", \
        "gitleaks' whole-tool severity still outranks its rdjson label"


# ── the binary is pinned, on every platform ──────────────────────────────────


def test_osv_scanner_is_pinned_for_every_platform_at_one_version():
    """An unpinned platform fails closed and is named, but a gate the M4
    milestone depends on should not be discovering that in the field."""
    manifest = json.loads((ROOT / "third_party" / "manifest.json")
                          .read_text(encoding="utf-8"))
    spec = manifest["tools"][SCA.BINARY]
    versions = set()
    for plat, asset in spec["assets"].items():
        assert asset.get("sha256"), f"{plat} is not pinned"
        assert asset.get("installed_sha256"), f"{plat} has no installed hash"
        versions.add(asset.get("version"))
    assert len(spec["assets"]) == 5, "five platforms, or one of them fails closed"
    assert len(versions) == 1, f"platforms pinned at different versions: {versions}"


def test_the_gate_names_no_ecosystem():
    """The formats are the scanner's answer. A list here would silently stop
    matching the day somebody adds a lockfile format."""
    source = (ROOT / "tools" / "review" / "sca.py").read_text(encoding="utf-8")
    body = source.split('"""', 2)[2]
    for word in ("npm", "maven", "pypi", "cargo", "gradle", "composer", "gemfile"):
        assert word not in body.lower(), f"{word} is named in the gate's code"


# ── the CLI ──────────────────────────────────────────────────────────────────


def profile(root: Path) -> None:
    (root / ".sdlc").mkdir(parents=True, exist_ok=True)
    (root / ".sdlc" / "profile.yml").write_text(
        'scope: estate\ngate_profile: team\nreview:\n  block_on: [critical, high]\n'
        'components:\n  - name: "api"\n    path: "api"\n    role: authoritative\n',
        encoding="utf-8")


def test_cli_exits_one_on_findings_and_three_when_it_could_not_look(
        tmp_path, monkeypatch, capsys):
    target, _ = component(tmp_path)
    profile(target)
    stub(monkeypatch, [(1, json.dumps(REPORT), STDERR_SCANNED)])
    assert SCA.main(["--target", str(target)]) == 1
    assert "osv-scanner" in capsys.readouterr().out
    monkeypatch.setattr(PP, "binary", lambda name: (None, "not run: missing"))
    assert SCA.main(["--target", str(target)]) == 3


def test_cli_writes_the_document_and_the_summary(tmp_path, monkeypatch, capsys):
    target, _ = component(tmp_path)
    profile(target)
    stub(monkeypatch, [(1, json.dumps(REPORT), STDERR_SCANNED)])
    out = tmp_path / "artifacts"
    assert SCA.main(["--target", str(target), "--out", str(out)]) == 1
    doc = json.loads((out / SCA.RESULT_NAME).read_text(encoding="utf-8"))
    assert doc["summary"]["by_severity"] == {"high": 1, "medium": 1}
    assert "high" in (out / SCA.REPORT_NAME).read_text(encoding="utf-8")


def test_the_gate_writes_the_document_the_phase_actually_reads(tmp_path, monkeypatch):
    """The real writer composed with the real reader, which nothing did.

    `sca.py` wrote `sca.json` and `suite.sca_result` looked for `50-sca.json`,
    so the SCA gate's findings could never reach the phase that owes them - and
    P5's own remedy line named the command that produces the wrong name. Every
    test passed because the fixture wrote the READER's name; producer and
    consumer were each tested and never put together.
    """
    from tools.sdlc import state as ledger
    from tools.sdlc import suite as engine

    target, _ = component(tmp_path)
    profile(target)
    run_id = ledger.create(target, "requirement", "a change")["run_id"]
    stub(monkeypatch, [(1, json.dumps(REPORT), STDERR_SCANNED)])

    assert SCA.main(["--target", str(target),
                     "--out", str(ledger.run_dir(target, run_id))]) == 1
    doc = engine.sca_result(target, run_id)
    assert doc, "P5 cannot see the document the gate just wrote"
    assert doc["summary"]["by_severity"] == {"high": 1, "medium": 1}


def test_the_phase_sop_names_the_document_that_is_actually_written():
    """The SOP is what a runner reads, and it states the artifact by name. That
    is a third place the name lives, and this drift already cost a working gate
    once - so the prose is held to the writer's constant rather than trusted."""
    sop = (ROOT / "sdlc" / "stages" / "P5-test.md").read_text(encoding="utf-8")
    assert SCA.RESULT_NAME in sop, (
        f"P5-test.md does not name {SCA.RESULT_NAME}, so a runner following it "
        f"would produce or look for something else")


def test_cli_refuses_an_unknown_component(tmp_path, monkeypatch, capsys):
    target, _ = component(tmp_path)
    profile(target)
    assert SCA.main(["--target", str(target), "--components", "nope"]) == 2
    assert "no component named" in capsys.readouterr().err


# ── one rule, both gates ─────────────────────────────────────────────────────
#
# This module emits the same `tools[]` document `prepass.py` writes and asks
# the same question of it - "did every gate fire?" - and it had its own answer:
# a DENY-LIST of three named bad statuses, beside a comment claiming to be
# "the rule prepass.py states". The two disagreed on any status neither list
# anticipated, and `nothing-scanned` shows how a status gets added.


def _tools_doc(status):
    """A minimal document of the shared `tools[]` shape - `sources` included
    because render_md reads it, which is how a hand-built fixture stops being
    a fixture the real renderer would refuse."""
    return {"schema_version": 1, "scope": {"components": []},
            "tools": [{"name": SCA.TOOL, "status": status, "findings": 0,
                       "detail": "", "per_component": []}],
            "sources": [],
            "summary": {"total": 0, "by_severity": {}}}


@pytest.mark.parametrize("status", ["not-run", "error", "nothing-scanned",
                                    "a-status-added-later"])
def test_every_status_that_is_not_ok_holds_the_sca_gate_shut(status):
    assert [t["name"] for t in PP.unfired(_tools_doc(status))] == [SCA.TOOL]


def test_the_two_gates_answer_the_same_question_the_same_way():
    """The deny-list said 0 where the allow-list said 3 - measured, not
    hypothetical. Both now come from one function."""
    doc = _tools_doc("a-status-added-later")
    deny_list_answer = 3 if any(
        t["status"] in ("not-run", "error", "nothing-scanned")
        for t in doc["tools"]) else 0
    assert deny_list_answer == 0            # what shipped
    assert PP.exit_code(doc) == 3           # what it claimed to be


def test_the_cli_holds_the_gate_shut_on_a_status_the_deny_list_never_named(
        tmp_path, monkeypatch, capsys):
    """`main()` had its OWN copy of the rule, and the existing exit-3 test used
    `not-run` - a status BOTH the deny-list and the allow-list catch, so it
    could never have discriminated the two. Found by asking what a mutation
    restoring the deny-list would redden: nothing."""
    target, _ = component(tmp_path)
    profile(target)
    monkeypatch.setattr(SCA, "run", lambda *a, **k: _tools_doc("a-status-added-later"))
    assert SCA.main(["--target", str(target)]) == 3


def test_a_scan_that_ran_and_found_nothing_is_clean():
    """The negative control: sharing the rule must not have made a clean scan
    hold the gate shut."""
    doc = _tools_doc(PP.STATUS_OK)
    assert PP.unfired(doc) == [] and PP.exit_code(doc) == 0


# ── the door ─────────────────────────────────────────────────────────────────
#
# Same shape as the pre-pass: `main()` had no top-level guard, and exit 1 here
# means "the scan ran, findings". An unexpected failure therefore said a supply
# chain had been scanned when nothing had scanned it.


def test_an_unexpected_failure_is_could_not_look_not_findings(tmp_path, capsys,
                                                              monkeypatch):
    target, _ = component(tmp_path)
    profile(target)
    monkeypatch.setattr(PP, "binary",
                        lambda name: (_ for _ in ()).throw(
                            RuntimeError("a disk error, say")))
    code = SCA.main(["--target", str(target)])
    assert code == 3
    printed = capsys.readouterr()
    assert "a disk error" in (printed.out + printed.err)


def test_a_clean_scan_is_still_clean(tmp_path, monkeypatch, capsys):
    """The negative control: the door must not make every run a 3."""
    target, _ = component(tmp_path)
    profile(target)
    stub(monkeypatch, [(0, json.dumps({"results": None}), STDERR_SCANNED)])
    assert SCA.main(["--target", str(target)]) == 0


# ── the review's fixes ───────────────────────────────────────────────────────


def test_a_scan_of_no_components_is_not_a_clean_scan(tmp_path, monkeypatch):
    """`per and all(...)` fell through an EMPTY component set to `ok`, so a
    supply-chain gate that ran no scanner at all reported clean and exited 0.
    Reachable from the CLI: `--components ,` makes `wanted` empty, and the
    unknown-name refusal then passes vacuously."""
    monkeypatch.setattr(PP, "binary", lambda name: (Path("osv-scanner"), ""))
    doc = SCA.run(tmp_path, [])
    assert doc["tools"][0]["status"] == "nothing-scanned"
    assert PP.unreviewed(doc, expected=(SCA.TOOL,)) != []
    assert PP.exit_code(doc) == 3


def test_the_sca_gate_takes_the_whole_rule_not_one_third_of_it(tmp_path,
                                                               monkeypatch):
    """`_main` adopted `unfired()` alone, so a document whose `tools` list
    cannot be read exited clean here while `prepass.exit_code` returned 3 for
    the same bytes — the disagreement this was all meant to end."""
    target, _ = component(tmp_path)
    profile(target)
    monkeypatch.setattr(SCA, "run", lambda *a, **k: {
        "schema_version": 1, "scope": {"components": []}, "tools": None,
        "sources": [], "summary": {"total": 0, "by_severity": {}}})
    assert SCA.main(["--target", str(target)]) == 3
