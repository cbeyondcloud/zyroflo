"""The canonical proposal: the tool writes the paperwork, the developer writes the answer.

Item 10. On the measured estate the contested list is 206 services in only 10
claimant-sets — a handful of deployment trees each declaring the whole platform — so the
curation is ~10 decisions wearing 206 blocks. `propose()` drafts the blocks grouped by
WHO is contesting, with every decision slot blank; nothing here decides anything, and a
blank block pasted wholesale still decides nothing. The round trip below is the item's
whole loop: report → propose → a human fills one slot → rebuild → resolved.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import tools.graph.build as B  # noqa: E402
from tools.graph.links import canonical  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"


def rows_for(service: str, components: list[str], claims: list[str]) -> dict:
    """A contested row in the shape the pass emits (D9-25, T534): each claim qualified by its
    component, and `choices`, the block that selects each package - an owner per component,
    or, where one component holds every claim, that owner with each claim's path."""
    single = len(components) == 1
    owners = [components[0] if single else components[min(i, len(components) - 1)]
              for i in range(len(claims))]
    choices = ([{"owner": components[0], "path": c.rsplit(":", 1)[0]} for c in claims]
               if single else [{"owner": c} for c in components])
    return {"extractor": "mark-canonical", "kind": "contested-service", "component": "",
            "components": components, "service": f"svc:{service}@{service}",
            "claims": [f"{o}/{c}" for o, c in zip(owners, claims)], "choices": choices,
            "source_file": claims[0].rsplit(":", 1)[0], "line": 0, "why": "test row"}


def undeclared_copy(tmp_path: Path) -> Path:
    """The fixture with its [[canonical]] block removed — the pre-curation state."""
    work = tmp_path / "ws"
    shutil.copytree(FIXTURE, work)
    toml = work / "workspace.toml"
    toml.write_text(toml.read_text(encoding="utf-8").split("[[canonical]]")[0],
                    encoding="utf-8")
    return work


def build(work: Path, out: str, *flags: str) -> int:
    return B.main(["--workspace", str(work / "workspace.toml"),
                   "--out", str(work.parent / out), *flags])


def spawn(work: Path, out: str, *flags: str) -> subprocess.CompletedProcess:
    """The same command as a PROCESS. A refusal is only a refusal if a person gets an
    exit code and a message rather than a traceback, and only a spawn can tell those
    apart — see the refusal tests below."""
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "graph" / "build.py"),
         "--workspace", str(work / "workspace.toml"),
         "--out", str(work.parent / out), *flags],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))


def contested_in(tmp_path: Path, out: str) -> list[dict]:
    rows = [json.loads(line) for line in
            (tmp_path / out / "unresolved.jsonl").read_text(encoding="utf-8").splitlines()]
    return [r for r in rows if r["kind"] == "contested-service"]


# ── the emitter: grouped, blank, deterministic, and real TOML ─────────────────


def test_blocks_are_grouped_by_claimant_set_largest_first():
    rows = [rows_for("aa", ["x"], ["a/f.yaml:1", "a/g.yaml:2"]),
            rows_for("bb", ["p", "q"], ["b/f.yaml:1", "c/f.yaml:1"]),
            rows_for("cc", ["p", "q"], ["b/h.yaml:1", "c/h.yaml:1"])]
    text = canonical.propose(rows)
    assert text.index("2 service(s) contested by exactly: p, q") < \
        text.index("1 service(s) contested by exactly: x")
    assert text.index('service = "bb"') < text.index('service = "cc"'), \
        "services sort within their group"


def test_a_cross_component_contest_drafts_owner_and_a_single_component_one_drafts_path():
    text = canonical.propose([
        rows_for("multi", ["p", "q"], ["b/f.yaml:1", "c/f.yaml:1"]),
        rows_for("solo", ["x"], ["a/f.yaml:1", "a/legacy/f.yaml:2"])])
    owner_block = text[text.index('service = "multi"'):text.index('service = "solo"')]
    assert 'owner = ""' in owner_block and "one of: p | q" in owner_block
    path_block = text[text.index('service = "solo"'):]
    assert 'owner = "x"' in path_block, \
        "a single-component collision's owner is FACT — the report restated, pre-filled"
    assert 'path = ""' in path_block, \
        "owner cannot separate two packages inside ONE component; only a path can"


def test_every_claim_is_cited_and_long_lists_are_capped():
    claims = [f"deploy/f{i}.yaml:{i}" for i in range(1, 6)]
    text = canonical.propose([rows_for("s", ["x", "y"], claims)])
    assert "deploy/f1.yaml:1" in text and "deploy/f3.yaml:3" in text
    assert "2 more" in text and "deploy/f4.yaml:4" not in text


def test_the_proposal_is_deterministic_and_parses_as_toml():
    rows = [rows_for("bb", ["p", "q"], ["b/f.yaml:1", "c/f.yaml:1"]),
            rows_for("aa", ["x"], ["a/f.yaml:1", "a/g.yaml:2"])]
    text = canonical.propose(rows)
    assert text == canonical.propose(list(reversed(rows))), \
        "same rows in any order must draft identical bytes"
    doc = tomllib.loads(text)
    assert [b["service"] for b in doc["canonical"]] == ["bb", "aa"]
    assert doc["canonical"][0]["owner"] == "", "the cross-component DECISION stays blank"
    assert doc["canonical"][1]["path"] == "", "the path DECISION stays blank"


# ── the loop, end to end on the fixture's own D4 collision ────────────────────


def test_the_round_trip_report_propose_fill_rebuild_resolved(tmp_path, capsys):
    """THE deliverable. The undeclared fixture reports its D4 collision; the proposal
    drafts a path-form block (both chart trees live in ONE component, so owner cannot
    decide); filling the one blank and pasting resolves it on the next build — winner
    marked, loser damped, report row gone."""
    work = undeclared_copy(tmp_path)
    assert build(work, "before", "--propose-canonical") == 0
    assert len(contested_in(tmp_path, "before")) == 1, "D4, undeclared, must be reported"

    proposal = (tmp_path / "before" / "canonical.proposed.toml").read_text(encoding="utf-8")
    assert 'service = "orders-api"' in proposal and 'path = ""' in proposal

    filled = proposal.replace('path = ""', 'path = "charts/orderflow"', 1)
    toml = work / "workspace.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + "\n" + filled, encoding="utf-8")

    assert build(work, "after") == 0
    assert contested_in(tmp_path, "after") == [], "the filled block must resolve it"
    edges = [json.loads(line) for line in
             (tmp_path / "after" / "graph.jsonl").read_text(encoding="utf-8").splitlines()]
    deploys = [e for e in edges if e.get("type") == "DG_DEPLOYS"]
    winner = [e for e in deploys if e.get("canonical") is True]
    losers = [e for e in deploys if e.get("canonical") is False]
    assert len(winner) == 1 and winner[0]["source_file"].startswith("charts/orderflow")
    assert losers and all(e["confidence"] == 0.5 for e in losers), "damped, never dropped"


def test_a_blank_path_pasted_wholesale_stays_reported_and_marks_no_winner(tmp_path):
    """The silent half of the safety property: the D4 proposal's owner is pre-filled
    fact and its path is the blank decision — pasted unfilled, the workspace accepts it,
    the collision stays reported, and NO winner is marked."""
    work = undeclared_copy(tmp_path)
    assert build(work, "draft", "--propose-canonical") == 0
    proposal = (tmp_path / "draft" / "canonical.proposed.toml").read_text(encoding="utf-8")
    toml = work / "workspace.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + "\n" + proposal, encoding="utf-8")

    assert build(work, "blank") == 0
    assert len(contested_in(tmp_path, "blank")) == 1, "a blank path is no decision"
    edges = [json.loads(line) for line in
             (tmp_path / "blank" / "graph.jsonl").read_text(encoding="utf-8").splitlines()]
    deploys = [e for e in edges if e.get("type") == "DG_DEPLOYS"]
    # `canonical: True` on an unadjudicated edge is the default ("not known superseded");
    # ADJUDICATION is `canonical_by`, written only when the declaration decides.
    assert not [e for e in deploys if e.get("props", {}).get("canonical_by")], \
        "nothing may be adjudicated by a blank path"
    assert all(e["confidence"] == 1.0 for e in deploys), "nothing may be damped either"


def test_a_blank_owner_is_refused_loudly_never_guessed(tmp_path, capsys):
    """The loud half: a cross-component block's owner IS the decision, and the workspace
    parser refuses a blank one outright — an unreviewed paste cannot half-declare."""
    work = undeclared_copy(tmp_path)
    toml = work / "workspace.toml"
    toml.write_text(toml.read_text(encoding="utf-8") +
                    '\n[[canonical]]\nservice = "orders-api"\nowner = ""\n',
                    encoding="utf-8")
    assert build(work, "refuse-blank") == 2
    assert "needs both `service` and `owner`" in capsys.readouterr().out


def test_a_fully_declared_workspace_has_nothing_to_propose(tmp_path, capsys):
    work = tmp_path / "ws"
    shutil.copytree(FIXTURE, work)
    assert build(work, "declared", "--propose-canonical") == 0
    assert "nothing contested is undeclared" in capsys.readouterr().out
    assert not (tmp_path / "declared" / "canonical.proposed.toml").exists(), \
        "an empty proposal file would read as work to do"


@pytest.mark.parametrize("mode", ["--check-fresh", "--from-snapshot", "--check-snapshot"])
def test_the_flag_refuses_modes_that_run_no_build(tmp_path, mode):
    """Every mode in the pre-flight clause, spawned. **A refusal's contract is a
    process** (item 96): `main()` called in-process cannot tell a refusal from a
    traceback, because pytest renders both as one failing test — and item 117 found
    exactly that, a CLI handing over a traceback where a refusal was the whole point.

    `--check-snapshot` is the leg item T233 added, and `--from-snapshot` was the leg the
    clause always had and nothing ever asked for; one parametrise is the clause's whole
    truth table rather than one of its three arms."""
    work = tmp_path / "ws"
    shutil.copytree(FIXTURE, work)
    proc = spawn(work, "refused", "--propose-canonical", mode)
    assert proc.returncode == 2, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "--propose-canonical drafts from the build this command runs" in proc.stdout
    assert "Traceback" not in proc.stderr, proc.stderr
    assert not (tmp_path / "refused").exists(), "a refused run wrote an output directory"


def test_the_refusal_arrives_before_the_build_it_refuses(tmp_path):
    """The refusal is PRE-FLIGHT, and the order is the point.

    `--check-snapshot` runs the whole extraction, so this pair was first refused from
    beside the proposal itself — after every extractor, after `_print_report`, and after
    the line saying `asked only: <out> is untouched`. A person read a complete-looking
    run and then a `!!`. Moved up beside its siblings, nothing the build prints reaches
    stdout at all, which is what this asserts: the refusal is the FIRST line and the
    only one."""
    work = tmp_path / "ws"
    shutil.copytree(FIXTURE, work)
    proc = spawn(work, "order", "--propose-canonical", "--check-snapshot")
    assert proc.returncode == 2
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    assert lines and lines[0].startswith("!! --propose-canonical"), proc.stdout
    assert len(lines) == 1, f"the build printed before the refusal: {proc.stdout}"
    # Named, so a future banner that is not the build cannot be mistaken for one: each
    # of these is printed only once extraction has already happened.
    for after_the_build in ("asked only:", "workspace:", "run:       ", "component  "):
        assert after_the_build not in proc.stdout, (
            f"{after_the_build!r} means the build ran before the refusal:\n{proc.stdout}")
