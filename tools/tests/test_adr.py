"""Item 64 — the ADR trigger fires on evidence, or it says it could not look.

Item 36 shipped `30-adr-*.md` as a `conditional` artifact and pinned, in a test
named for it, that it *is asked for and never required*. The obligation was
real, the wording was right, and the only thing behind it was the runner's own
opinion of its own design. So these tests are mostly about the two ways that
fails silently: a trigger that fires and nobody notices, and a checker that
never looked reporting clean.

The graph here is written through `emit.write_jsonl` from real `Node` and
`Edge` objects rather than hand-rolled JSON. A fixture that spells the format
itself would pass while the real one changed shape underneath it — the same
seam item 29 found between two test fakes that modelled incompatible versions
of one file.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.graph import rules as traversal  # noqa: E402
from tools.graph.core.lib import emit  # noqa: E402
from tools.graph.core.model import Edge, ExtractResult, Node  # noqa: E402
from tools.sdlc import adr as A  # noqa: E402
from tools.sdlc import approve as AP  # noqa: E402
from tools.sdlc import stage as ST  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.tests import _runs as R  # noqa: E402

WHO = "lead@example.invalid"

#: One component per trigger kind, plus one that fires none — the negative
#: control, because a detector that fires on everything is not a detector.
FILES = {
    "api": {"pom.xml": "<project/>\n",
            "src/OrderController.java": "class OrderController {}\n",
            "src/Formatter.java": "class Formatter {}\n"},
    "worker": {"db/V1__create.sql": "create table order_events (id int);\n"},
}


def _git(where: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(where), *args], check=True, timeout=120,
                   capture_output=True)


def _graph(root: Path) -> None:
    """A built graph over the fixture, in the real on-disk format."""
    result = ExtractResult()
    result.nodes += [
        Node("svc:orders@api", "svc", {"component": "api"}),
        Node("svc:worker@worker", "svc", {"component": "worker"}),
        Node("repo:api", "repo"), Node("repo:worker", "repo"),
        Node("mvn:shared", "mod"), Node("ep:/orders/v1/list", "ep"),
        Node("table:order_events", "table"),
        Node("cfg:api/logging", "cfg", {"component": "api"}),
    ]
    result.edges += [
        Edge("svc:orders@api", "DG_DEPENDS_ON", "mvn:shared", "pom.xml", 6, "mvn"),
        Edge("svc:orders@api", "DG_EXPOSES", "ep:/orders/v1/list",
             "src/OrderController.java", 9, "spring"),
        Edge("svc:orders@api", "DG_CALLS", "ep:/orders/v1/list",
             "src/OrderController.java", 12, "spring"),
        Edge("svc:worker@worker", "DG_OWNS_TABLE", "table:order_events",
             "db/V1__create.sql", 2, "flyway"),
        # Ruled, and not a trigger: the negative control's only fact.
        Edge("svc:orders@api", "DG_CONFIGURES", "cfg:api/logging",
             "src/Formatter.java", 3, "spring"),
    ]
    emit.write_jsonl(root / ".sdlc" / "graph" / "graph.jsonl", result,
                     extractor="fixture")


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    """A two-component workspace with a built graph, and a run at P3."""
    root = tmp_path / "estate"
    (root / ".sdlc").mkdir(parents=True)
    lines = ["scope: estate", "gate_profile: team",
             "phases: [intake, requirements, design, implementation, test, "
             "review, triage, closeout]",
             'test_command: ""', "review:", "  block_on: [critical, high]",
             "components:"]
    for name, files in FILES.items():
        where = root / name
        for rel, body in files.items():
            (where / rel).parent.mkdir(parents=True, exist_ok=True)
            (where / rel).write_text(body, encoding="utf-8")
        _git(where, "init", "-q", "-b", "main")
        _git(where, "add", "-A")
        _git(where, "-c", "user.email=d@x.invalid", "-c", "user.name=D",
             "commit", "-qm", "base")
        lines += [f'  - name: "{name}"', f'    path: "{name}"',
                  "    role: authoritative"]
    (root / ".sdlc" / "profile.yml").write_text("\n".join(lines) + "\n",
                                                encoding="utf-8")
    _graph(root)
    return root


def design(ws: Path, body: str, declared: bool = True) -> str:
    """A run parked at P3 whose design says `body`. Returns the run id.

    **`body` is written as a DECLARED change set** — item 123. The design used
    to be mined whole, so any backticked token anywhere was a file the change
    would touch, and a document citing its own sibling artifacts named them as
    files. The reader now takes the list entries under a `Change set`
    declaration, which is what the P3 SOP asks for, so the fixture writes one.

    Every caller keeps its meaning: each line of `body` becomes one entry, so
    "Change `api/pom.xml`." still declares `api/pom.xml`. Pass `declared=False`
    for the tests that are ABOUT the undeclared case.
    """
    st = L.create(ws, "requirement", "a change")
    rid = st["run_id"]
    folder = L.run_dir(ws, rid)
    if declared:
        entries = [f"- {line.strip()}" for line in body.splitlines() if line.strip()]
        body = "## Change set" + chr(10) * 2 + chr(10).join(entries) + chr(10)
    (folder / "30-design.md").write_text(body, encoding="utf-8")
    L.record_artifact(ws, rid, "30-design.md")
    L.advance(ws, rid, phase="P3", status="awaiting_approval")
    return rid


def adr(ws: Path, rid: str, slug: str, answers: str = "", body: str = "") -> Path:
    path = L.run_dir(ws, rid) / f"30-adr-{slug}.md"
    text = f"# {slug}\n\n"
    if answers:
        text += f"{A.ANSWERS_MARK} `{answers}`\n"
    text += body or f"{A.REJECTED_MARK} doing nothing — the caller still breaks\n"
    path.write_text(text, encoding="utf-8")
    return path


# ── the ruling map is complete, and it is a ruling ────────────────────────────


def test_every_edge_type_the_graph_mints_has_a_ruling():
    """The prophylactic for this item's own failure mode. A coupling type can
    enter the graph and silently trigger nothing; this fails until someone
    decides whether it is worth a record."""
    unruled = sorted(set(traversal.DEFAULT_TABLE) - set(A.EDGE_RULING))
    assert not unruled, (
        f"{unruled} can be traversed but nobody has ruled whether changing a "
        f"file that declares one is worth an ADR — add it to EDGE_RULING with "
        f"a kind, or with '' meaning considered-and-no")


def test_a_ruling_is_a_known_kind_or_a_deliberate_no():
    for edge_type, kind in A.EDGE_RULING.items():
        assert kind in ("",) + A.KINDS, f"{edge_type} is ruled {kind!r}"
    assert set(A.KINDS) == set(A.WHY), "a kind with no explanation of why it fires"


def test_all_three_kinds_are_reachable_from_the_ruling_map():
    """The task line names three triggers. A kind no edge type maps to is a
    trigger that cannot fire."""
    assert set(A.EDGE_RULING.values()) - {""} == set(A.KINDS)


# ── it fires on what the file declares ───────────────────────────────────────


def test_each_kind_fires_on_its_own_evidence(ws):
    rid = design(ws, "Change `api/pom.xml`, `api/src/OrderController.java` and "
                     "`worker/db/V1__create.sql`.\n")
    fired, unruled = A.triggers(ws, rid)
    assert {t.kind for t in fired} == set(A.KINDS), \
        f"expected all three kinds, got {[(t.kind, t.id) for t in fired]}"
    assert unruled == []
    by_id = {t.id: t for t in fired}
    assert "dependency:api/pom.xml" in by_id
    assert "data-model:worker/db/V1__create.sql" in by_id
    boundary = by_id["boundary:api/src/OrderController.java"]
    assert len(boundary.evidence) == 2, "two declarations, one trigger"
    assert any(":9 declares DG_EXPOSES" in e for e in boundary.evidence), \
        f"the provenance line is the evidence: {boundary.evidence}"


def test_evidence_is_ordered_by_line_number_not_by_its_text(ws):
    """Sorting the rendered string put line 12 above line 2, which reads as a
    provenance bug to anyone checking a citation against the file."""
    result = ExtractResult()
    result.nodes += [Node("svc:orders@api", "svc", {"component": "api"}),
                     Node("ep:/a/b", "ep"), Node("ep:/c/d", "ep")]
    result.edges += [
        Edge("svc:orders@api", "DG_EXPOSES", "ep:/a/b",
             "src/OrderController.java", 12, "spring"),
        Edge("svc:orders@api", "DG_EXPOSES", "ep:/c/d",
             "src/OrderController.java", 2, "spring"),
    ]
    emit.write_jsonl(ws / ".sdlc" / "graph" / "graph.jsonl", result, extractor="ordering")
    rid = design(ws, "Change `api/src/OrderController.java`.\n")
    fired, _ = A.triggers(ws, rid)
    assert [e.split(" ")[0] for e in fired[0].evidence] == \
        ["src/OrderController.java:2", "src/OrderController.java:12"]


def test_the_gate_refusal_is_short_and_the_check_carries_the_why(ws):
    """`approve.py` joins every obligation with '; '. Five triggers each
    repeating the reasoning and the remedy is a refusal nobody reads."""
    rid = design(ws, "Change `api/pom.xml`.\n")
    owed = A.obligations(ws, rid)[0]
    assert len(owed) < 220, f"{len(owed)} chars in one obligation: {owed}"
    assert "adr.py --check" in owed
    assert A.WHY["dependency"] not in owed, "the why belongs in --check"
    assert A.WHY["dependency"] in A._render(A.report(ws, rid))


def test_a_file_that_declares_nothing_interesting_fires_nothing(ws):
    """The negative control. `Formatter.java` declares a fact — it is just not
    one anybody else can see."""
    rid = design(ws, "Change `api/src/Formatter.java` only.\n")
    fired, unruled = A.triggers(ws, rid)
    assert fired == [] and unruled == []
    assert A.obligations(ws, rid) == []


def test_an_edge_type_nobody_ruled_on_is_reported_not_ignored(ws, monkeypatch):
    monkeypatch.delitem(A.EDGE_RULING, "DG_CONFIGURES")
    rid = design(ws, "Change `api/src/Formatter.java`.\n")
    fired, unruled = A.triggers(ws, rid)
    assert fired == [] and unruled == ["DG_CONFIGURES"]
    assert "DG_CONFIGURES" in A._render(A.report(ws, rid))


def test_evidence_beyond_the_cap_is_counted_out_loud(ws, monkeypatch):
    monkeypatch.setattr(A, "EVIDENCE_SHOWN", 1)
    rid = design(ws, "Change `api/src/OrderController.java`.\n")
    fired, _ = A.triggers(ws, rid)
    assert len(fired[0].evidence) == 1 and fired[0].elided == 1
    assert "1 further declaration" in A._render(A.report(ws, rid))


# ── reading the design ───────────────────────────────────────────────────────


def declared(*entries: str) -> str:
    """A design that DECLARES these lines as its change set - item 123.

    The four tests below are about how a TOKEN is read: backticks, separators,
    dotfiles, extension-less names. None of them is about the declaration, so
    they say it once here rather than each carrying the scaffolding.
    """
    return "## Change set" + chr(10) * 2 + chr(10).join(
        e if e.lstrip().startswith("- ") else "- " + e for e in entries) + chr(10)


def test_a_candidate_is_a_backticked_token_and_the_tree_decides():
    """Backticks are the requirement, and resolution is the test. A token with
    whitespace is a command and a token starting with `-` is a flag; anything
    else is offered to the filesystem, INCLUDING an extension-less name -
    `README`, `Makefile` and `Dockerfile` are files, and a shape filter that
    ruled them out could not see a whole class of repository."""
    found = A.named_paths(declared(
        "Edit `api/pom.xml` and the pom.xml beside it; run "
        "`pytest -q`; see `--check` and `README`."))
    assert found == ["api/pom.xml", "README"], found
    assert A.path_shaped("api/pom.xml") and not A.path_shaped("README"), \
        "only a path-SHAPED token that resolves to nothing is worth reporting"


def test_a_windows_spelling_and_a_leading_dot_slash_are_the_same_file():
    assert A.named_paths(declared(r"`api\pom.xml` `./api/pom.xml`")) == ["api/pom.xml"]


def test_a_dotfile_directory_and_a_dunder_file_survive_normalisation():
    """Both were quietly renamed into something that resolves to nothing,
    which reads as 'the design named a file we do not have'."""
    found = A.named_paths(declared("`.github/workflows/ci.yml` and `pkg/__init__.py`"))
    assert found == [".github/workflows/ci.yml", "pkg/__init__.py"], found


def test_an_extension_less_file_at_a_component_root_is_found(ws):
    """`Makefile`, `Dockerfile`, `go` tool wrappers - a shape filter that
    omits them is a detector that cannot see a whole class of repository."""
    (ws / "api" / "Makefile").write_text("build:\n\ttrue\n", encoding="utf-8")
    assert A.locate(ws, "api/Makefile") == [("api", "Makefile")]
    assert "api/Makefile" in A.named_paths(declared("Change `api/Makefile`."))


def test_a_command_that_is_not_a_path_stays_out_of_the_report(ws):
    """Item 141 STRENGTHENED this rather than moving it: this test's own name
    is the claim, and `stage.py` is a command in the sentence it reads - it
    was reported as a file this workspace does not have, which is the defect.
    Both commands stay out now."""
    rid = design(ws, "Change `api/pom.xml`, then run `pytest` and `stage.py`.\n")
    changes = A.change_set(ws, rid)
    assert changes.unknown == (), changes.unknown
    assert changes.resolved == (("api", "pom.xml"),)


def test_a_component_relative_name_resolves_when_it_is_unambiguous(ws):
    assert A.locate(ws, "db/V1__create.sql") == [("worker", "db/V1__create.sql")]


def test_a_qualified_name_beats_a_bare_one(ws):
    assert A.locate(ws, "api/pom.xml") == [("api", "pom.xml")]


def test_a_name_two_components_share_is_reported_never_picked(ws):
    (ws / "worker" / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    rid = design(ws, "Change `pom.xml`.\n")
    changes = A.change_set(ws, rid)
    assert changes.resolved == () and changes.ambiguous[0][1] == ("api", "worker")
    assert any("2 components" in p for p in A.obligations(ws, rid))


def test_a_file_the_design_invents_is_not_a_finding(ws):
    """A design names files it is about to CREATE. A file that does not exist
    declares nothing, which is not the same as a mistake."""
    rid = design(ws, "Add `api/src/NewThing.java`, changing `api/pom.xml`.\n")
    changes = A.change_set(ws, rid)
    assert changes.unknown == ("api/src/NewThing.java",)
    assert changes.resolved == (("api", "pom.xml"),)


# ── the obligation, and what closes it ───────────────────────────────────────


def test_a_fired_trigger_nothing_answers_is_owed(ws):
    rid = design(ws, "Change `api/pom.xml`.\n")
    owed = A.obligations(ws, rid)
    assert len(owed) == 1 and "dependency:api/pom.xml" in owed[0]
    assert "adr.py --check" in owed[0], "the obligation sends the reader somewhere"
    shown = A._render(A.report(ws, rid))
    assert "--dismiss" in shown and A.ANSWERS_MARK in shown, \
        "both remedies are stated where the reader has just been sent"


def test_an_adr_citing_the_trigger_closes_it(ws):
    rid = design(ws, "Change `api/pom.xml`.\n")
    adr(ws, rid, "shared-lib", "dependency:api/pom.xml")
    assert A.obligations(ws, rid) == []


def test_one_adr_may_answer_several_triggers(ws):
    rid = design(ws, "Change `api/pom.xml` and `api/src/OrderController.java`.\n")
    path = L.run_dir(ws, rid) / "30-adr-one.md"
    path.write_text(
        f"# one\n\n{A.ANSWERS_MARK} `dependency:api/pom.xml`, "
        f"`boundary:api/src/OrderController.java`\n{A.REJECTED_MARK} two records\n",
        encoding="utf-8")
    assert A.obligations(ws, rid) == []


def test_an_adr_that_cites_a_trigger_that_did_not_fire_is_a_finding(ws):
    """A citation typo leaves the real trigger open while looking closed."""
    rid = design(ws, "Change `api/pom.xml`.\n")
    adr(ws, rid, "typo", "dependency:api/pom.xm")
    owed = A.obligations(ws, rid)
    assert any("did not fire" in p for p in owed)
    assert any("dependency:api/pom.xml fired" in p for p in owed)


def test_a_trigger_id_mentioned_in_prose_does_not_close_it(ws):
    """Named by the mutation pass: reading citations off every line rather
    than off the `**Answers**` line reddened nothing. An ADR that DISCUSSES a
    trigger has not answered it, and the difference is the whole point of
    having a line that means "this closes that"."""
    rid = design(ws, "Change `api/pom.xml`.\n")
    (L.run_dir(ws, rid) / "30-adr-prose.md").write_text(
        "# a record about something else\n\n"
        "We looked at `dependency:api/pom.xml` and moved on.\n"
        f"{A.REJECTED_MARK} nothing\n", encoding="utf-8")
    owed = A.obligations(ws, rid)
    assert any("nothing answers it" in o for o in owed), owed
    assert A.report(ws, rid)["answered"] == {}


def test_an_empty_adr_is_a_finding(ws):
    rid = design(ws, "Change `api/pom.xml`.\n")
    (L.run_dir(ws, rid) / "30-adr-blank.md").write_text("", encoding="utf-8")
    assert any("is empty" in p for p in A.obligations(ws, rid))


def test_an_adr_with_no_rejected_alternative_is_a_finding(ws):
    rid = design(ws, "Change `api/pom.xml`.\n")
    adr(ws, rid, "thin", "dependency:api/pom.xml", body="We added it.\n")
    owed = A.obligations(ws, rid)
    assert any(A.REJECTED_MARK in p for p in owed)
    assert not any("nothing answers it" in p for p in owed), \
        "the trigger IS answered - the record is thin, which is a different sentence"


def test_an_adr_with_no_answers_line_closes_nothing(ws):
    rid = design(ws, "Change `api/pom.xml`.\n")
    adr(ws, rid, "loose")
    owed = A.obligations(ws, rid)
    assert any(A.ANSWERS_MARK in p for p in owed)
    assert any("nothing answers it" in p for p in owed)


# ── the escape hatch is a record ─────────────────────────────────────────────


def test_a_dismissal_closes_the_trigger_and_says_who_and_why(ws):
    rid = design(ws, "Change `api/pom.xml`.\n")
    row = A.dismiss(ws, rid, "dependency:api/pom.xml", "a patch bump inside the "
                    "range the ADR already ruled on", WHO)
    assert row["by"] == WHO and row["at"].endswith("Z")
    assert A.obligations(ws, rid) == []
    assert A.report(ws, rid)["dismissed"]["dependency:api/pom.xml"].startswith("a patch")


def test_a_dismissal_with_no_reason_is_refused(ws):
    rid = design(ws, "Change `api/pom.xml`.\n")
    with pytest.raises(A.AdrError, match="needs a reason"):
        A.dismiss(ws, rid, "dependency:api/pom.xml", "   ", WHO)
    assert A.obligations(ws, rid), "a refused dismissal must not close anything"


def test_a_dismissal_without_a_graph_is_refused_not_a_traceback(ws, capsys):
    """Found by the field run. `obligations` and `report` ask `graph_ready`
    first; `dismiss` did not, and died as a FileNotFoundError the CLI could
    not catch because an OSError is not a RuntimeError."""
    rid = design(ws, "Change `api/pom.xml`.\n")
    (ws / ".sdlc" / "graph" / "graph.jsonl").unlink()
    with pytest.raises(A.AdrError, match="no built graph"):
        A.dismiss(ws, rid, "dependency:api/pom.xml", "a reason", WHO)
    assert A.main(["--target", str(ws), "--run", rid, "--dismiss",
                   "dependency:api/pom.xml", "--because", "a reason",
                   "--by", WHO]) == 2
    assert "no built graph" in capsys.readouterr().err


def test_a_dismissal_attributed_to_nobody_is_refused(ws, monkeypatch):
    monkeypatch.setattr(AP, "who", lambda target, given: "")
    rid = design(ws, "Change `api/pom.xml`.\n")
    with pytest.raises(A.AdrError, match="needs a name against it"):
        A.dismiss(ws, rid, "dependency:api/pom.xml", "a good reason")
    assert A.obligations(ws, rid), "a refused dismissal must not close anything"


def test_a_dismissal_of_a_trigger_that_never_fired_is_refused(ws):
    rid = design(ws, "Change `api/pom.xml`.\n")
    with pytest.raises(A.AdrError, match="not a trigger this design fired"):
        A.dismiss(ws, rid, "boundary:api/src/OrderController.java", "no", WHO)


def test_a_dismissal_is_pinned_like_any_other_artifact(ws):
    """An override that can be rewritten after the yes is one nobody approved."""
    rid = design(ws, "Change `api/pom.xml`.\n")
    A.dismiss(ws, rid, "dependency:api/pom.xml", "ruled on last quarter", WHO)
    recorded = L.load(ws, rid)["artifacts"]
    assert A.DISMISSED in recorded
    assert L.drift(ws, rid, recorded) == []
    (L.run_dir(ws, rid) / A.DISMISSED).write_text("", encoding="utf-8")
    assert L.drift(ws, rid, recorded), "the pin did not notice the rewrite"


def test_a_later_dismissal_supersedes_an_earlier_one(ws):
    rid = design(ws, "Change `api/pom.xml`.\n")
    A.dismiss(ws, rid, "dependency:api/pom.xml", "first reason", WHO)
    A.dismiss(ws, rid, "dependency:api/pom.xml", "the better reason", WHO)
    assert A.dismissals(ws, rid)["dependency:api/pom.xml"]["because"] == "the better reason"


def test_an_unreadable_dismissal_file_is_refused_by_line(ws):
    rid = design(ws, "Change `api/pom.xml`.\n")
    (L.run_dir(ws, rid) / A.DISMISSED).write_text("{not json\n", encoding="utf-8")
    with pytest.raises(A.AdrError, match=r":1 is not readable JSON"):
        A.dismissals(ws, rid)


def test_a_reason_typed_in_another_encoding_is_refused_by_line_not_a_traceback(ws):
    """The dismissal ledger holds reasons a HUMAN typed, and G2 is refused or
    released on what this returns. The decode sat in the `for` statement,
    outside the guard, so one byte from a cp1252 editor raised
    UnicodeDecodeError - which is a ValueError, and this CLI's handler
    catches RuntimeError and OSError. It would have exited 1, and 1 here
    means "there are obligations": a crash forging a real answer."""
    rid = design(ws, "Change `api/pom.xml`.\n")
    A.dismiss(ws, rid, "dependency:api/pom.xml", "a stated reason", WHO)
    path = L.run_dir(ws, rid) / A.DISMISSED
    path.write_bytes(path.read_bytes() + b"\xff\xfe from a cp1252 editor\n")
    with pytest.raises(A.AdrError, match=r":2 is not readable JSON"):
        A.dismissals(ws, rid)


# ── a checker that could not look must not report clean ──────────────────────


def test_without_a_built_graph_nothing_is_owed_and_it_says_so(ws):
    rid = design(ws, "Change `api/pom.xml`.\n")
    (ws / ".sdlc" / "graph" / "graph.jsonl").unlink()
    assert A.obligations(ws, rid) == []
    data = A.report(ws, rid)
    assert data["evaluated"] is False and "no built graph" in data["why"]
    assert "NOT EVALUATED" in A._render(data)


def test_an_empty_graph_directory_is_not_a_graph(ws):
    """`stage.capable` accepts the directory because scoping activates on
    intent. Evidence is a different question and this asks that one."""
    (ws / ".sdlc" / "graph" / "graph.jsonl").write_text("", encoding="utf-8")
    assert ST.capable(ws, "graph") is True
    assert A.graph_ready(ws) is False


def test_a_design_that_names_nothing_is_the_finding_not_a_clean_bill(ws):
    rid = design(ws, "We will change the api and then the worker.\n")
    owed = A.obligations(ws, rid)
    assert len(owed) == 1 and "looked at nothing" in owed[0]
    assert "backticks" in owed[0], "the finding states the fix"


# ── the teeth: this is what G2 stands on ─────────────────────────────────────


def test_the_stage_contract_reports_an_unanswered_trigger(ws):
    rid = design(ws, "Change `api/pom.xml`.\n")
    owed = ST.verify(ws, rid, "P3")
    assert any("dependency:api/pom.xml" in p for p in owed), owed


def test_g2_is_refused_while_a_trigger_is_unanswered(ws):
    rid = design(ws, "Change `api/pom.xml`.\n")
    with pytest.raises(RuntimeError, match="dependency:api/pom.xml"):
        AP.record(ws, rid, "G2", "approve", WHO)
    adr(ws, rid, "shared-lib", "dependency:api/pom.xml")
    AP.record(ws, rid, "G2", "approve", WHO)
    ok, why = AP.gate_state(ws, rid, "G2")
    assert ok, why


def test_a_run_cannot_leave_p3_with_a_trigger_open(ws):
    rid = design(ws, "Change `api/pom.xml`.\n")
    with pytest.raises(ST.StageError, match="dependency:api/pom.xml"):
        ST.advance(ws, rid, "P4")


def test_a_phase_with_no_conditional_artifact_is_untouched(ws):
    """The obligation is keyed to the stage contract, not bolted onto verify:
    a phase that owes no conditional artifact must not pay for this item.
    Which phases DO declare one is item 38's question, not this one's."""
    assert "P1" not in ST.CONDITIONAL_JUDGE and not ST.by_phase("P1").conditional
    rid = design(ws, "Change `api/pom.xml`.\n")
    R.deliver(ws, rid, "P1")
    assert ST.verify(ws, rid, "P1") == []


# ── the prompt states the requirement ────────────────────────────────────────


def test_the_p3_prompt_states_the_check_and_the_backtick_rule(ws):
    rid = design(ws, "x\n")
    text = ST.render_prompt(ws, rid, "P3")
    assert "adr.py" in text and "--check" in text
    assert "backticks" in text and "empty one" in text
    assert "dismissed with a stated reason" in text


def test_the_sop_states_both_load_bearing_lines():
    text = (ROOT / "sdlc" / "stages" / "P3-design.md").read_text(encoding="utf-8")
    assert A.ANSWERS_MARK in text and A.REJECTED_MARK in text
    assert "--dismiss" in text, "the escape hatch is only usable if it is written down"


# ── the two spellings nobody required to agree ───────────────────────────────


def test_a_graph_that_names_different_components_is_a_finding_not_a_silence(ws):
    """Resolution finds the file through the profile's PATHS and the lookup
    asks the graph under its NAMES. Where those disagree every lookup misses
    and the answer is zero triggers, which reads exactly like a clean design."""
    rid = design(ws, "Change `api/pom.xml`.\n")
    profile = ws / ".sdlc" / "profile.yml"
    profile.write_text(profile.read_text(encoding="utf-8")
                       .replace('name: "api"', 'name: "svc-api"')
                       .replace('name: "worker"', 'name: "svc-worker"'),
                       encoding="utf-8")
    owed = A.obligations(ws, rid)
    assert any("not one name is shared" in o for o in owed), owed
    assert any("svc-api" in o for o in owed), \
        "the finding names both sides or it cannot be acted on"


def test_agreeing_names_produce_no_such_finding(ws):
    assert A.naming_problem(ws) == ""


def test_a_component_the_extractors_cannot_read_is_not_punished(ws):
    """Most stacks have no extractor yet (item 80). A component contributing
    nothing to the graph must not read as a broken build."""
    result = ExtractResult()
    result.nodes += [Node("svc:orders@api", "svc", {"component": "api"})]
    result.edges += [Edge("svc:orders@api", "DG_DEPENDS_ON", "mvn:shared",
                          "pom.xml", 6, "mvn")]
    emit.write_jsonl(ws / ".sdlc" / "graph" / "graph.jsonl", result, extractor="partial")
    assert A.naming_problem(ws) == "", "worker contributes nothing, which is not a mismatch"


# ── the graph is parsed once, and a rebuild is still seen ────────────────────


def test_the_parsed_graph_is_reused_within_one_build(ws, monkeypatch):
    """Counting PARSES, not the size of the dict holding them - the mutation
    pass caught the first version asserting something that stayed true when
    every call re-read the file."""
    rid = design(ws, "Change `api/pom.xml`.\n")
    A._PARSED.clear()
    calls = []
    real = A.impact.load_graph
    monkeypatch.setattr(A.impact, "load_graph",
                        lambda out: (calls.append(out), real(out))[1])
    A.triggers(ws, rid)
    A.triggers(ws, rid)
    A.report(ws, rid)
    assert len(calls) == 1, f"the graph was parsed {len(calls)} times for one build"


def test_a_rebuilt_graph_is_not_served_from_the_cache(ws):
    rid = design(ws, "Change `api/pom.xml`.\n")
    assert [t.kind for t in A.triggers(ws, rid)[0]] == ["dependency"]
    result = ExtractResult()
    result.nodes += [Node("svc:orders@api", "svc", {"component": "api"}),
                     Node("repo:worker", "repo"), Node("table:orders", "table")]
    result.edges += [Edge("svc:orders@api", "DG_OWNS_TABLE", "table:orders",
                          "pom.xml", 6, "flyway")]
    emit.write_jsonl(ws / ".sdlc" / "graph" / "graph.jsonl", result, extractor="second")
    assert [t.kind for t in A.triggers(ws, rid)[0]] == ["data-model"], \
        "the rebuild was served from the cache"


# ── a str target is a target (item 37's defect class) ────────────────────────


def test_a_string_target_is_read_the_same_as_a_path(ws):
    """Item 37's field run found a `str` target raising TypeError inside a
    blanket except, so a gate silently fell back to a hardcoded default."""
    rid = design(ws, "Change `api/pom.xml`.\n")
    assert A.obligations(str(ws), rid) == A.obligations(ws, rid)
    assert A.report(str(ws), rid)["evaluated"] is True


def test_a_missing_design_is_reported_once_and_by_the_artifact_check(ws):
    st = L.create(ws, "requirement", "a change")
    rid = st["run_id"]
    L.advance(ws, rid, phase="P3", status="awaiting_approval")
    assert A.obligations(ws, rid) == []
    owed = ST.verify(ws, rid, "P3")
    assert len(owed) == 1 and "30-design.md" in owed[0]


# ── the CLI ──────────────────────────────────────────────────────────────────


def test_cli_exits_zero_clean_one_owed_and_three_unevaluated(ws, capsys):
    rid = design(ws, "Change `api/pom.xml`.\n")
    assert A.main(["--target", str(ws), "--run", rid, "--check"]) == 1
    assert "UNANSWERED" in capsys.readouterr().out
    adr(ws, rid, "shared-lib", "dependency:api/pom.xml")
    assert A.main(["--target", str(ws), "--run", rid, "--check"]) == 0
    assert "answered by 30-adr-shared-lib.md" in capsys.readouterr().out
    (ws / ".sdlc" / "graph" / "graph.jsonl").unlink()
    assert A.main(["--target", str(ws), "--run", rid, "--check"]) == 3


def test_cli_json_carries_the_triggers_and_their_provenance(ws, capsys):
    rid = design(ws, "Change `worker/db/V1__create.sql`.\n")
    assert A.main(["--target", str(ws), "--run", rid, "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["triggers"][0]["kind"] == "data-model"
    assert "db/V1__create.sql:2" in data["triggers"][0]["evidence"][0]


def test_cli_dismiss_records_and_then_the_check_is_clean(ws, capsys):
    rid = design(ws, "Change `api/pom.xml`.\n")
    assert A.main(["--target", str(ws), "--run", rid, "--dismiss",
                   "dependency:api/pom.xml", "--because", "ruled last quarter",
                   "--by", WHO]) == 0
    assert A.main(["--target", str(ws), "--run", rid, "--check"]) == 0


def test_cli_refuses_a_dismissal_without_a_reason(ws, capsys):
    rid = design(ws, "Change `api/pom.xml`.\n")
    assert A.main(["--target", str(ws), "--run", rid, "--dismiss",
                   "dependency:api/pom.xml", "--by", WHO]) == 2
    assert "needs a reason" in capsys.readouterr().err


# ── item 123: a change set is DECLARED, and prose is prose ──────────────────
#
# `named_paths` read every backticked token in the design, so a document citing
# its own sibling artifacts in the ordinary house style named them as files the
# change would touch. Item 100 fixed exactly this in `defect.py` and recorded
# what it cost there: the only way past was to strip backticks off every
# legitimate cross-reference, so the check made documents WORSE.
#
# It could not copy `defect.py` outright: that bounds a declaration at ONE
# following line, which is right for a short list of test names and wrong for a
# per-component change set. A change set is a SECTION.

DESIGN = chr(10).join([
    "# Design",
    "",
    "The requirement is in `10-requirements.md` and the scope in `20-scope.md`.",
    "A reproduction is owed; see `40-repro.md`.",
    "",
    "## Change set - api",
    "",
    "- `api/src/OrderController.java` - stop logging inbound headers",
    "- `api/pom.xml` - pin the library",
    "",
    "The order matters because `20-scope.md` records the boundary.",
    "",
    "## Test plan",
    "",
    "- a test that `api/src/Formatter.java` is untouched",
    "",
    "## Notes",
    "Nothing else changes.",
])


def test_the_design_declares_two_files_and_cites_three_documents():
    """**The item, as the walk found it.** `20-scope.md`, `40-repro.md` and
    `10-requirements.md` are the run's OWN artifacts, cited in the house style
    this project writes everything in. They are not the change set."""
    assert A.named_paths(DESIGN) == ["api/src/OrderController.java",
                                     "api/pom.xml"]


def test_prose_inside_the_declared_section_is_still_prose(ws):
    """The sentence after the list cites `20-scope.md` again. Being inside the
    section does not make it a member of it - only list entries are."""
    rid = design(ws, DESIGN, declared=False)
    changes = A.change_set(ws, rid)
    assert changes.unknown == (), changes.unknown


def test_a_section_ends_at_the_next_heading():
    """`api/src/Formatter.java` is a list entry under **Test plan**, which is a
    different declaration. Without a bound, one change set would swallow every
    list in the document."""
    assert "api/src/Formatter.java" not in A.named_paths(DESIGN)


def test_prose_inside_the_section_is_SKIPPED_rather_than_ending_it():
    """**The judgement call, stated where a reader meets it.**

    A paragraph between two list entries does not close the change set. Erring
    toward reading MORE is the safe direction for this check specifically,
    because it REPORTS and never obliges - item 64 made the trigger a lookup and
    an unresolved name a finding. Under-reading is the silent direction, and
    silence here is an ADR nobody was asked for.
    """
    text = chr(10).join([
        "## Change set",
        "- `api/pom.xml`",
        "Some prose in the middle of the section.",
        "- `api/src/OrderController.java`",
    ])
    assert A.named_paths(text) == ["api/pom.xml", "api/src/OrderController.java"]


def test_a_label_declaration_carries_its_own_names():
    """`**Change set:** `a`, `b`` on one line is how people really write a small
    one, and `defect.py` accepts the same shape for the same reason."""
    assert A.named_paths("**Change set:** `api/pom.xml`, `api/Makefile`") == [
        "api/pom.xml", "api/Makefile"]
    assert A.named_paths("- Change set: `api/pom.xml`") == ["api/pom.xml"]


def test_a_design_that_declares_nothing_names_nothing(ws):
    """And that is not a silent pass. The empty case was ALREADY a stated
    finding before this item, which is what makes a stricter reader safe: the
    failure direction is toward being told, never toward a quiet green."""
    assert A.named_paths("Change `api/pom.xml` and `api/Makefile`.") == []
    rid = design(ws, "Change `api/pom.xml`.", declared=False)
    owed = A.obligations(ws, rid)
    assert any("names no file this workspace contains" in p for p in owed), owed
    assert any("looked at nothing" in p for p in owed), owed


def test_a_stray_fence_marker_that_swallows_a_change_set_is_LOUD(ws):
    """**Item 149's reversal rests on this, so it is pinned and not assumed.**
    An unclosed fence runs to the end of the document, which means one stray
    backtick can swallow a real change set. That is only the safe direction
    while the swallow is LOUD - if this reader ever answered an empty change
    set quietly, the reversal would have traded a plausible wrong answer for a
    silent one, which is worse. The document below declares a real change set
    AFTER a stray marker; the reader must name nothing and the run must be told
    so, in item 64's own words."""
    swallowed = (
        "# Design" + chr(10) * 2
        + "The P3 SOP asks for:" + chr(10) * 2
        + "```markdown" + chr(10)
        + "## Change set" + chr(10) * 2
        + "- `api/pom.xml`" + chr(10) * 2
        + "## Change set" + chr(10) * 2
        + "- `api/src/OrderController.java`" + chr(10))
    assert "```" in swallowed and swallowed.count("```") == 1, (
        "the fixture must carry exactly one stray marker")
    assert A.named_paths(swallowed) == [], "the stray marker swallowed the rest"
    rid = design(ws, swallowed, declared=False)
    owed = A.obligations(ws, rid)
    assert any("names no file this workspace contains" in p for p in owed), owed
    assert any("looked at nothing" in p for p in owed), owed


def test_the_declaration_the_reader_wants_is_the_one_the_sop_teaches():
    """**Item 33's class, guarded rather than remembered.** A reader enforcing a
    word the procedure never states is a requirement the runner is never told -
    which this project has now paid for three times."""
    sop = (ST.HARNESS / ST.SOP_DIR / "P3-design.md").read_text(encoding="utf-8")
    assert "`Change set`" in sop
    assert "Only those list entries are read" in sop
    assert "ends at the next heading" in sop
    # The SOP's own example must be readable by the reader it teaches.
    example = chr(10).join([
        "## Change set - orders-api",
        "",
        "- `orders-api/ShortenController.java` - stop logging inbound headers",
        "- `orders-api/ShortenControllerTest.java` - the regression test",
    ])
    assert example.splitlines()[0].lstrip("# ").startswith("Change set")
    assert A.named_paths(example) == [
        "orders-api/ShortenController.java",
        "orders-api/ShortenControllerTest.java"]


def test_the_two_modules_agree_that_a_declaration_is_required():
    """`defect.py` and `adr.py` are the two readers that mine a run's prose for
    names. Item 100 fixed one; this is the other. Neither reads an undeclared
    document, and a test says so in one place so a third reader has a rule to
    follow rather than a precedent to rediscover."""
    from tools.sdlc import defect as D
    assert D.named_tests("The fix is in `test_a_thing`.") == []
    assert A.named_paths("The fix is in `api/pom.xml`.") == []
    assert D.named_tests("Regression test: `test_a_thing`") == ["test_a_thing"]
    assert A.named_paths("Change set: `api/pom.xml`") == ["api/pom.xml"]


def test_a_numbered_change_set_is_read_like_a_bulleted_one():
    """The P3 SOP asks for the files **and in what order**, so a numbered list
    is the natural way to write one - and a reader that only understood
    bullets would silently drop every file in it, which is the quiet direction
    this item exists to avoid.

    Found by the mutation pass: narrowing the list pattern to `[-*+]` killed
    nothing, because no test named the case.
    """
    numbered = chr(10).join([
        "## Change set",
        "1. `api/pom.xml` - pin the library first",
        "2) `api/src/OrderController.java` - then the caller",
    ])
    assert A.named_paths(numbered) == ["api/pom.xml",
                                       "api/src/OrderController.java"]


def test_a_deeper_heading_is_part_of_the_change_set_not_the_end_of_it():
    """**The per-component shape the SOP asks for.**

    `## Change set` followed by `### orders-api` is one change set with a
    section per component, and ending at ANY heading cut every component's list
    off. Found by the cross-repo build test, which had been writing exactly
    that shape since before this item — its design document was already
    declared, and a rule that read *any* heading as the end silently emptied it.
    """
    text = chr(10).join([
        "## Change set",
        "### orders-shared",
        "- `shared/pom.xml`",
        "### orders-api",
        "- `api/pom.xml`",
        "## Test plan",
        "- `api/Other.java`",
    ])
    assert A.named_paths(text) == ["shared/pom.xml", "api/pom.xml"]


def test_a_heading_at_the_declaring_level_or_shallower_ends_it():
    """The other half. Without it, one change set would own the rest of the
    document — and `## Test plan` above is exactly what must close it."""
    same = chr(10).join(["## Change set", "- `a/pom.xml`",
                         "## Test plan", "- `b/Other.java`"])
    shallower = chr(10).join(["### Change set", "- `a/pom.xml`",
                              "## Test plan", "- `b/Other.java`"])
    assert A.named_paths(same) == ["a/pom.xml"]
    assert A.named_paths(shallower) == ["a/pom.xml"]


def test_a_label_declaration_is_closed_by_any_heading():
    """A label sits at no heading level, so it owns the lines under it and
    nothing past the next section. The first cut had it backwards — depth 0
    meant no heading could ever close it."""
    text = chr(10).join(["**Change set:** `a/pom.xml`",
                         "###### Notes", "- `b/Other.java`"])
    assert A.named_paths(text) == ["a/pom.xml"]


# -- item 141: an unresolved token must carry a path separator ---------------

#: Token -> whether an UNRESOLVED spelling of it is worth reporting as a file
#: this workspace does not have. **Both answers are in one table on purpose**:
#: a sweep that only ever asserts `False` is killed by a rule that always says
#: no, and one that only asserts `True` by a rule that always says yes - item
#: 115's lesson, that a guard nobody can watch fail is a guard nobody has
#: checked.
SHAPES = {
    # a file, named the way the P3 SOP teaches - workspace-relative
    "api/src/NewThing.java": True,
    "tools/sdlc/newthing.py": True,
    "docs/adr/0001-thing.md": True,
    ".github/workflows/ci.yml": True,
    # not a file at all: a member, a method, a package, an attribute
    "Pattern.CASE_INSENSITIVE": False,
    "Matcher.find": False,
    "Math.max": False,
    "String.format": False,
    "com.example.Order": False,
    "list.append": False,
    # already dropped before this item, and still dropped - the rule this
    # makes uniform rather than the loss it introduces
    "Dockerfile": False,
    "README": False,
    # the stated limit: a brand-new ROOT-LEVEL file named bare
    "Order.java": False,
}


#: Named so the sweep's own breadth is a value a test can hold. A parametrise
#: over an empty or trimmed list COLLECTS NOTHING and pytest calls that a pass.
SHAPE_CASES = sorted(SHAPES)


def test_the_shape_sweep_can_be_watched_failing():
    """**Item 115's lesson, applied to the guard rather than only to the fix.**
    A sweep that looked at nothing and a sweep that found nothing hand back the
    same empty report, and a table carrying one answer is satisfied by a rule
    that always gives it. So the table's breadth and both of its answers are
    asserted here, where trimming either fails by name."""
    assert set(SHAPE_CASES) == set(SHAPES) and len(SHAPE_CASES) >= 10
    assert set(SHAPES.values()) == {True, False}


@pytest.mark.parametrize("token", SHAPE_CASES)
def test_a_dotted_identifier_is_not_a_file_this_workspace_is_missing(token):
    """**Item 141, and it is item 100's class for the fourth time.**

    A dot alone made `Pattern.CASE_INSENSITIVE` path-shaped, and a change set
    names members that way constantly, so an ordinary design was reported as
    naming a file this workspace does not have - about a constant. A bare
    `Word.word` is undecidable by shape (`Order.java` and `Math.max` are the
    same token unless something knows a table of source extensions, and this
    module names no ecosystem), so the rule is the separator the P3 SOP
    already teaches.
    """
    assert A.path_shaped(token) is SHAPES[token], token


def test_a_windows_spelling_is_still_a_path_because_it_is_normalised_first(ws):
    """One separator is the whole rule because `named_paths` has already
    turned the other one into it - so the reader and this judgement cannot
    disagree about a document written on a different machine."""
    rid = design(ws, r"Add `api\src\NewThing.java`." + "\n")
    assert A.change_set(ws, rid).unknown == ("api/src/NewThing.java",)


def test_a_design_naming_a_constant_is_not_a_design_naming_a_missing_file(ws):
    """**The composition, which is the arrangement that let this ship.**

    `path_shaped` had a test and `change_set` had a test and nothing ran one
    THROUGH the other, so the report a person actually reads was answered for
    by neither. Measured on the first P3 ever walked: the design named a
    constant, the check said "1 named but not in the workspace", and the
    runner wrote a paragraph explaining the tool away inside the artifact a
    gate pins.
    """
    rid = design(ws, "Change `api/pom.xml` so `Pattern.CASE_INSENSITIVE` and "
                     "`Matcher.find` are applied once.\n")
    changes = A.change_set(ws, rid)
    assert changes.unknown == (), changes.unknown
    assert changes.resolved == (("api", "pom.xml"),)
    assert not any("not in the workspace" in p for p in A.obligations(ws, rid))


def test_a_file_the_design_invents_is_still_reported(ws):
    """The control, green by design. The signal this narrows must survive it:
    a design names files it is about to CREATE, and one written the way the
    SOP teaches carries a separator, so it is reported exactly as before."""
    rid = design(ws, "Add `api/src/NewThing.java`.\n")
    assert A.change_set(ws, rid).unknown == ("api/src/NewThing.java",)
