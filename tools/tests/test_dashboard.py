"""The productivity dashboard: three views over what was measured. Item 57.

The task line asks for org / team / developer views, and **this harness has no
organisation and no team**. `team` in the profile is a gate REGIME (solo, team,
regulated), not an organisational unit, and nothing anywhere declares who works
with whom. So the views are mapped to units that exist — the workspace, one
component, one gate approver — and inventing a team model is refused, because an
organisational structure is the adopter's to declare and not this tool's to guess.

The property worth more than the rest of this file:

**The developer view reads the APPROVAL LEDGER and never `reviews.jsonl`.** Item 27
made review records carry no identity by construction; rebuilding a per-person view
out of them would overturn that silently. Who took a gate is a different fact,
recorded deliberately and hash-chained. That is asserted structurally rather than
trusted.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.review import hitl  # noqa: E402
from tools.sdlc import dashboard as D  # noqa: E402
from tools.sdlc import metrics as M  # noqa: E402
from tools.sdlc import state as ledger  # noqa: E402

PROFILE = ("stack: python\ngate_profile: solo\n"
           "phases: [intake, requirements, design, implementation, test, review, "
           "triage, closeout]\nreview:\n  block_on: [critical, high]\n")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    (r / ".sdlc").mkdir(parents=True)
    (r / ".sdlc" / "profile.yml").write_text(PROFILE, encoding="utf-8")
    return r


def approvals(repo: Path, run_id: str, rows: list[dict]) -> None:
    from tools.sdlc import approve

    folder = ledger.run_dir(repo, run_id)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / approve.APPROVALS).write_text(
        "".join(json.dumps(r, sort_keys=True) + "\n" for r in rows),
        encoding="utf-8")


# ── the property that is not negotiable ─────────────────────────────────────


def test_the_developer_view_never_reads_the_review_record():
    """**Item 27's anonymity, held structurally.**

    `reviews.jsonl` carries no identity BY CONSTRUCTION - a closed key set, pinned
    by its own tests - and a per-person view rebuilt from it would overturn a
    decision this project made on purpose. Who took a gate is a separate fact,
    recorded deliberately and signed for, which is why the developer view is built
    from approvals instead.

    Checked by AST over string literals with docstrings exempt, because this
    module's own docstring names `reviews.jsonl` while explaining the rule - the
    exact false positive a substring sweep produces, and the reason item 26's
    literal scan and item 48's no-execution check are both written this way.
    """
    source = (ROOT / "tools" / "sdlc" / "dashboard.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(doc)

    literals = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and n.value not in docstrings]
    offenders = [s for s in literals if hitl.REVIEWS in s]
    assert not offenders, f"the dashboard names the review record: {offenders}"

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[-1] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.update(a.name for a in node.names)
    assert "hitl" not in imported, "the dashboard imports the review recorder"


# ── the views map to units that exist ───────────────────────────────────────


def test_the_developer_view_is_per_gate_approver(repo):
    rid = ledger.create(repo, "requirement", "a change")["run_id"]
    approvals(repo, rid, [
        {"gate": "G1", "verdict": "approve", "approver": "ada@example.test"},
        {"gate": "G2", "verdict": "approve", "approver": "ada@example.test"},
        {"gate": "G1", "verdict": "approve", "approver": "grace@example.test",
         "forced": True},
    ])
    states, _ = M.run_states(repo)
    got = D.per_approver(repo, states)
    assert set(got) == {"ada@example.test", "grace@example.test"}
    assert got["ada@example.test"]["approvals"] == 2
    assert got["ada@example.test"]["forced"] == 0
    assert got["grace@example.test"]["forced"] == 1
    assert got["ada@example.test"]["by_gate"] == {"G1": 1, "G2": 1}


def test_a_forced_approval_is_counted_beside_the_total_not_inside_it(repo):
    """A person who approved ten gates and forced three has a different record
    from one who forced none, and a bare total hides exactly that."""
    rid = ledger.create(repo, "requirement", "a change")["run_id"]
    approvals(repo, rid, [
        {"gate": "G1", "verdict": "approve", "approver": "ada", "forced": True}])
    states, _ = M.run_states(repo)
    row = D.per_approver(repo, states)["ada"]
    assert row["approvals"] == 1 and row["forced"] == 1


def test_an_approval_with_no_approver_is_a_named_row_not_a_silence(repo):
    rid = ledger.create(repo, "requirement", "a change")["run_id"]
    approvals(repo, rid, [{"gate": "G1", "verdict": "approve", "approver": ""}])
    states, _ = M.run_states(repo)
    assert D.UNATTRIBUTED in D.per_approver(repo, states)


def test_the_team_view_is_per_component(repo):
    """`worktree.components_of` is the reader - the record when there is one, the
    PROFILE before P4 writes it, which is the distinction item 100 built because
    reading an absent record as an empty set made a whole check silent."""
    ledger.create(repo, "requirement", "a change")
    states, _ = M.run_states(repo)
    got = D.per_component(repo, states)
    assert got, "a run must land somewhere, even before it has a worktree"
    assert sum(row["runs"] for row in got.values()) >= 1


def test_a_run_that_cannot_be_placed_is_counted_not_dropped(repo, monkeypatch):
    """**A view that omits what it could not place reports a smaller, cleaner
    workspace than the one that exists.**"""
    from tools.sdlc import worktree as trees

    ledger.create(repo, "requirement", "a change")
    monkeypatch.setattr(trees, "components_of",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
    states, _ = M.run_states(repo)
    got = D.per_component(repo, states)
    assert got == {D.UNATTRIBUTED: got[D.UNATTRIBUTED]}
    assert got[D.UNATTRIBUTED]["runs"] == 1


# ── the series a time-series tool scrapes ───────────────────────────────────


def test_an_unmeasurable_metric_is_ABSENT_from_the_series_never_zero():
    """`metrics.py`'s rule, and it matters more in an export than anywhere: a
    dashboard plots what it is given, so a null rendered as 0 becomes a flat line
    somebody reads as a measurement."""
    view = {"org": {"dora": {"deployment_frequency": M.cannot("never deployed"),
                             "counted": M.value(3)}},
            "team": {}, "developer": {}}
    names = {r["metric"] for r in D.series(view)}
    assert "dora_counted" in names
    assert "dora_deployment_frequency" not in names


def test_the_series_rows_are_flat_metric_value_labels():
    view = {"org": {"runs": {"total": M.value(2),
                             "by_kind": M.value({"defect": 2})}},
            "team": {"api": {"runs": 1, "open": 1, "closed": 0}},
            "developer": {"ada": {"approvals": 3, "forced": 0}}}
    rows = D.series(view)
    assert rows and all(set(r) == {"metric", "value", "labels"} for r in rows)
    assert all(isinstance(r["value"], (int, float)) for r in rows)
    by = {(r["metric"], tuple(sorted(r["labels"].items()))): r["value"] for r in rows}
    assert by[("runs_total", (("view", "org"),))] == 2
    assert by[("runs_by_kind", (("bucket", "defect"), ("view", "org")))] == 2
    assert by[("component_runs", (("component", "api"), ("view", "team")))] == 1
    assert by[("approver_approvals", (("approver", "ada"), ("view", "developer")))] == 3


# ── the page ────────────────────────────────────────────────────────────────


def _page(view: dict, target: str = "/somewhere/my-repo") -> str:
    return D.render(view, target)


EMPTY = {"org": {}, "team": {}, "developer": {}, "unmeasured": [], "unreadable": {}}


def test_the_page_needs_no_external_asset():
    """This harness installs into repositories with no network, and a dashboard
    that needs a CDN renders blank exactly where it is most needed."""
    page = _page(EMPTY)
    for offender in ("http://", "https://", "<script", "src="):
        assert offender not in page, f"the page reaches for {offender}"


def test_the_page_shows_rows_that_could_not_be_read():
    """Without this a corrupt ledger renders as 'no gate has been taken yet' - a
    page reporting a quieter workspace than the one that exists."""
    page = _page({**EMPTY, "unreadable": {"approvals": 4}})
    assert "could not be read" in page and "4" in page
    assert "could not be read" not in _page(EMPTY)


def test_the_page_names_the_repository_and_not_the_machines_path():
    """The page is written into `.sdlc/metrics/`, which adopters COMMIT - so an
    absolute path would publish one machine's directory layout to everyone who
    clones, and the harness's own private-coordinate guard cannot see a file it
    writes into somebody else's repository."""
    page = _page(EMPTY, target="/home/someone/work/my-repo")
    assert "my-repo" in page
    assert "/home/someone" not in page


def test_the_page_says_what_team_and_developer_MEAN_here():
    """A view labelled `team` that is really a component would be read as an
    organisational claim the harness never made."""
    page = _page(EMPTY)
    assert "component" in page and "approver" in page
    assert "does not invent" in page


def test_an_unmeasured_metric_reaches_the_page_with_its_reason():
    page = _page({**EMPTY, "unmeasured": ["dora.deployment_frequency: never deployed"]})
    assert "dora.deployment_frequency" in page and "never deployed" in page


def test_the_page_escapes_what_it_renders():
    """Component names and approver identities are data from the adopter's own
    repository, and a page that pasted them raw would be one more place untrusted
    text becomes markup."""
    page = _page({**EMPTY,
                  "developer": {"<script>x</script>": {"approvals": 1, "forced": 0,
                                                       "by_gate": {}}}})
    assert "<script>x</script>" not in page
    assert "&lt;script&gt;" in page


# ── wired onto the CLI ──────────────────────────────────────────────────────


def test_nothing_is_written_without_the_flag(repo):
    assert M.main(["--target", str(repo)]) == 0
    assert not (repo / D.DASHBOARD).exists()
    assert not (repo / D.SERIES).exists()


def test_the_flag_writes_both_artifacts(repo):
    assert M.main(["--target", str(repo), "--dashboard"]) == 0
    assert (repo / D.DASHBOARD).is_file()
    body = json.loads((repo / D.SERIES).read_text(encoding="utf-8"))
    assert body["schema_version"] == 1 and isinstance(body["series"], list)


def test_the_two_document_names_have_one_home():
    """Item 94's rule. The writer owns them and every reader asks."""
    source = (ROOT / "tools" / "sdlc" / "metrics.py").read_text(encoding="utf-8")
    assert "dashboard.html" not in source and "series.json" not in source
