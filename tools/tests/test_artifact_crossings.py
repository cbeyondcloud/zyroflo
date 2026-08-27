"""Every document the pipeline hands from one module to another, composed.

The defect class this file exists for has now happened five times, and the
fifth one shipped: `sca.py` wrote `sca.json` while `suite.py` looked for
`50-sca.json`, so the SCA gate's findings could never reach the phase that owes
them - and 3,711 tests passed, because the fixture spelled the READER's name
and nothing had ever run the real writer into the real reader.

Two guards, and they answer different questions.

**Who owns a name.** A run-folder document is named ONCE and every other module
asks. Spelling it a second time is not duplication - it is two independent
decisions that agree today. Which module gets to be the one home is decided by
the import graph, not by a principle: `sca.py` writes `50-sca.json` and owns
it, while `50-scope-add.md` is owned by `stage.py` and asked for by the
`suite.py` that writes it, because `suite` imports `stage` and `stage` is
embedded in the dependency-free ledger check.

Docstrings are exempt - prose that names a document informs a reader and cannot
send anything to the wrong place. Everything else is not: the first cut of this
sweep looked only at path expressions, and the mutation pass killed it, because
the stage contract names artifacts as plain tuple elements.

**Whether the two ends meet.** Owning the name is necessary and not sufficient
- a writer and a reader can agree on a filename and disagree about everything
inside it. So the crossings below run the real writer as its CLI would, then
hand the result to the real reader and require it to see what was written.

What is deliberately NOT here: documents with one owner. `state.json`,
`guardrail.jsonl`, `feedback.jsonl`, `30-adr-dismissed.jsonl`,
`provider-smoke.jsonl`, `60-review-loop.json` and `reviews.jsonl` are each
written and parsed by a single module, and a module cannot drift from itself.
`reviews.jsonl` is read by `provider_smoke` too, and safely - through
`hitl.paths`, which is what asking the owner looks like.
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import provider_smoke as SMOKE  # noqa: E402
from tools.review import prepass as PP  # noqa: E402
from tools.review import sca as SCA  # noqa: E402
from tools.sdlc import intake as I  # noqa: E402
from tools.sdlc import state as L  # noqa: E402
from tools.sdlc import suite as S  # noqa: E402
from tools.tests.test_dependencies import _parse, _sources  # noqa: E402

#: A run-folder document: a machine-read record, or an artifact on the
#: lifecycle's numbering. Plain `*.md` is excluded - prose is written for people
#: and named in prose all over the tree.
DOCUMENT = re.compile(r"^(?:\d\d-[\w.*-]+\.(?:json|jsonl|md)|[\w.*-]+\.jsonl?)$")

#: Names that look like documents and are not this harness's to own: files it
#: reads from the repositories it runs against, or from other people's tools.
NOT_OURS = {
    "package.json", "package-lock.json", "pnpm-lock.yaml", "composer.json",
    "tsconfig.json", "graph.jsonl", "snapshot.json", "workspace.json",
    ".mcp.json", "launch.json", "manifest.json", "prepass-report.json",
}


def _document_constants() -> list[tuple[Path, str, str]]:
    """(file, constant, filename) for every module-level document name."""
    out: list[tuple[Path, str, str]] = []
    for path in _sources(tests=False):
        tree = _parse(path)
        if tree is None:
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            target, value = node.targets[0], node.value
            pairs: list[tuple[ast.expr, ast.expr]] = []
            if isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple):
                pairs = list(zip(target.elts, value.elts))
            else:
                pairs = [(target, value)]
            for name_node, value_node in pairs:
                if not isinstance(name_node, ast.Name):
                    continue
                if not (isinstance(value_node, ast.Constant)
                        and isinstance(value_node.value, str)):
                    continue
                text = value_node.value
                if DOCUMENT.match(text) and text not in NOT_OURS:
                    out.append((path, name_node.id, text))
    return out


def _docstrings(tree: ast.Module) -> set[int]:
    """`id()` of every docstring node. Prose is not the defect: a docstring
    naming a document informs a reader and cannot make anything look in the
    wrong place."""
    out: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)) or not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            out.add(id(first.value))
    return out


def _name_literals(tree: ast.Module) -> list[tuple[int, str]]:
    """Every string literal, except docstrings and module-level assignments.

    The first cut of this looked only at path EXPRESSIONS - `x / "name"` and
    `Path("name")` - and the mutation pass killed it: the stage contract names
    P5's artifacts as plain tuple elements, so putting the literal back there
    went unnoticed. A name does not have to be joined to a path in the same
    expression to be a second spelling of it.

    Module-level assignments are skipped because that is where the ONE spelling
    is declared. A message built with an f-string is not an exact match to a
    document name, so ordinary prose does not reach here either.
    """
    skip = _docstrings(tree)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            skip |= {id(n) for n in ast.walk(node.value)}
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in skip]


def test_the_sweep_can_see_the_documents_it_is_about():
    """The guard's own guard. A sweep that finds nothing passes silently, and
    every assertion below it becomes decoration."""
    found = _document_constants()
    names = {text for _, _, text in found}
    assert len(found) >= 10, f"only {len(found)} document constants found"
    for expected in ("50-sca.json", "prepass.json", "00-intake.json",
                     "approvals.jsonl", "50-suite.json"):
        assert expected in names, f"{expected} is no longer discoverable"


def test_no_document_is_spelled_outside_the_module_that_writes_it():
    """The rule. One name, one owner, everyone else asks.

    Red against `2d4fa4c`: `suite.py` built the intake path from a literal
    `"00-intake.json"` and the stage contract declared P0's artifact as a
    literal `"00-intake.md"`, both while `intake.py` held the constants - and
    `intake.py`'s own docstring records that pair diverging once already.
    """
    owners = {text: path for path, _, text in _document_constants()}
    offenders: list[str] = []
    for path in _sources(tests=False):
        tree = _parse(path)
        if tree is None:
            continue
        for line, text in _name_literals(tree):
            owner = owners.get(text)
            if owner is None:
                continue
            where = ("its own module, one edit away from disagreeing with it"
                     if owner == path else f"{owner.name}")
            offenders.append(
                f"{path.relative_to(ROOT).as_posix()}:{line} builds a path from "
                f"{text!r} rather than the constant in {where}")
    assert offenders == [], (
        "a run-folder document is spelled somewhere other than the one place "
        "that names it, which is two decisions that agree today: "
        + "; ".join(sorted(offenders)))


# ── the crossings: the real writer, then the real reader ─────────────────────


def test_the_pre_pass_writes_where_the_smoke_looks(tmp_path, monkeypatch):
    """`prepass.py` wrote `prepass.json` as a bare literal at three sites and
    `provider_smoke.py` opened it at three more. Never drifted; nothing was
    stopping it."""
    target = tmp_path / "repo"
    (target / ".sdlc").mkdir(parents=True)
    (target / "a.py").write_text("x = 1\n", encoding="utf-8")
    diff = tmp_path / "change.diff"
    diff.write_text("--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-x = 0\n+x = 1\n",
                    encoding="utf-8")
    out = tmp_path / "prepass-out"

    PP.main(["--target", str(target), "--diff", str(diff), "--out", str(out)])
    doc = SMOKE._read_json(out / PP.RESULT_NAME)
    assert isinstance(doc, dict) and "tools" in doc, \
        "the smoke cannot open the document the pre-pass just wrote"


def test_intake_writes_the_baseline_the_engine_reads_back(tmp_path):
    """P0 writes the baseline; P5 compares against it. `suite.py` reached for
    it by literal name while `intake.py` owned the constant."""
    target = tmp_path / "repo"
    (target / ".sdlc").mkdir(parents=True)
    (target / ".sdlc" / "profile.yml").write_text(
        'stack: python\ntest_command: "exit 0"\n', encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main", str(target)], check=True,
                   timeout=120, capture_output=True)

    rec = I.open_run(target, "A guard is missing on the configured base path, "
                             "so an unrelated path slices into the wrong node.",
                     allow_dirty=True, run_baseline=True)
    read_back = S.recorded_baselines(target, rec["run_id"])
    assert read_back, "P5 cannot see the baseline P0 just recorded"
    assert read_back[next(iter(read_back))]["exit_code"] == 0


def test_the_sca_gate_writes_where_p5_reads(tmp_path, monkeypatch):
    """The instance that shipped. Kept here beside its siblings so the table is
    the whole set rather than most of it; `test_sca.py` states the defect."""
    from tools.review import prepass as pp_mod

    target = tmp_path / "repo"
    (target / ".sdlc").mkdir(parents=True)
    (target / ".sdlc" / "profile.yml").write_text("stack: python\n", encoding="utf-8")
    monkeypatch.setattr(pp_mod, "binary", lambda name: (None, "not run: absent"))
    run_id = L.create(target, "requirement", "a change")["run_id"]

    SCA.main(["--target", str(target), "--out", str(L.run_dir(target, run_id))])
    assert S.sca_result(target, run_id), \
        "P5 cannot see the document the SCA gate just wrote"


@pytest.mark.parametrize("name", ["50-sca.json", "prepass.json", "00-intake.json"])
def test_each_crossing_names_a_document_that_exists(name):
    """The crossings above and the ownership sweep must be about the same set.
    A crossing for a document nobody declares is a test of nothing."""
    assert name in {text for _, _, text in _document_constants()}
