"""An unresolved row must carry the fields its `kind` promises.

The whole purpose of an unresolved row is to name something the graph is missing. A row
that fails to name it is not a partial answer — it is a row that costs a reader time and
tells them nothing, which is worse than the silence it was invented to replace.

Found by item 76 against a real workspace: **266 `grammar-unavailable` rows carried no
`language`**, so a validation report said something was unreadable and not what, while the
value was sitting in a local variable one line above the row that omitted it. The same run
had **206 `contested-service` rows naming no component at all** — a reader could see that
some service was contested and not by whom.

The contract is split on purpose. `UNRESOLVED_COMMON` lives in core because it is true of
every row in any workspace. The per-kind extras are declared by the extractor that emits
them, because a central table would make core learn the whole extractor vocabulary and
adding a stack would then force a core edit — the exact boundary failure the M1 kill gate
exists to catch.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.graph import build as B
from tools.graph.core.model import (
    UNRESOLVED_COMMON, ExtractResult, check_unresolved, unresolved_row)

GRAPH = Path(__file__).resolve().parents[1]
ROOT = GRAPH.parents[1]


def good(**over) -> dict:
    row = unresolved_row("ext", "comp", "some-kind", "a/b.yml", 7, "because")
    row.update(over)
    return row


# ── the checker itself ─────────────────────────────────────────────────────────
def test_a_conforming_row_is_not_reported():
    assert check_unresolved([good()], {}, "ext") == []


@pytest.mark.parametrize("field", UNRESOLVED_COMMON)
def test_every_common_field_is_required(field):
    row = good()
    del row[field]
    problems = check_unresolved([row], {}, "ext")
    assert len(problems) == 1 and field in problems[0]


def test_a_kind_declared_field_is_required_only_for_that_kind():
    declared = {"grammar-unavailable": ("language",)}
    missing = good(kind="grammar-unavailable")
    other = good(kind="unparsable")

    assert len(check_unresolved([missing], declared, "ext")) == 1
    assert check_unresolved([other], declared, "ext") == [], \
        "a field one kind promises must not be demanded of a different kind"


def test_present_but_empty_is_legal_and_absent_is_not():
    """A finding about a component rather than a place in it legitimately has no line and
    no file. Conflating "does not apply" with "was forgotten" would make the contract
    unsatisfiable for exactly the rows that are hardest to file."""
    assert check_unresolved([good(source_file="", line=0)], {}, "ext") == []
    assert check_unresolved([good(why=None)], {}, "ext") != [], \
        "None where a value was promised is a defect, not an empty value"


def test_every_missing_field_is_reported_not_just_the_first():
    """One violation must not hide the next. This is why the checker returns messages
    instead of raising at the first bad row."""
    row = good(kind="k")
    del row["extractor"], row["why"]
    problems = check_unresolved([row], {"k": ("language",)}, "ext")
    assert len(problems) == 3, problems


def test_a_row_that_is_not_a_dict_is_reported_rather_than_crashing():
    assert len(check_unresolved(["oops"], {}, "ext")) == 1


# ── the declaration reaches the pipeline ───────────────────────────────────────
def test_every_declared_kind_actually_reaches_a_registration():
    """**The test that would have caught this fix's own defect.**

    The declarations were first written at module level and read off the extractor
    instance, so every one of them resolved to `{}` — "this kind promises nothing extra",
    which is indistinguishable from no declaration at all. The build went green, the
    contract enforced only its common half, and nothing said so. That is precisely the
    failure this item exists to remove, reproduced one layer up inside its own fix; the
    validator could not catch it, because a declaration that never arrives is never
    malformed. Only running it did, and only against a fixture that had a violation.
    """
    declaring = {}
    for pkg in sorted(p for p in (GRAPH / "extractors").iterdir() if p.is_dir()):
        init = pkg / "__init__.py"
        if not init.exists() or pkg.name.startswith("_"):
            continue
        if "UNRESOLVED_FIELDS" in init.read_text(encoding="utf-8"):
            declaring[pkg.name] = init

    assert declaring, "no extractor declares UNRESOLVED_FIELDS — has the mechanism moved?"

    regs = B.discover_extractors()
    with_fields = {r.name: r.unresolved_fields for r in regs if r.unresolved_fields}
    assert with_fields, (
        "every extractor registered with an EMPTY unresolved_fields while "
        f"{len(declaring)} module(s) declare one. The declaration is not reaching "
        "registration, so the per-kind half of the contract is silently unenforced.")

    modules = {r.module.split("/")[-1] for r in regs if r.unresolved_fields}
    unreachable = sorted(set(declaring) - modules)
    assert not unreachable, (
        f"these modules declare UNRESOLVED_FIELDS that never reach a registration: "
        f"{unreachable}. A declaration nothing reads enforces nothing.")


def test_every_emitted_kind_is_declared_by_the_module_that_emits_it():
    """A kind with no declaration is checked against the common fields only. That is a
    legitimate state — some kinds genuinely promise nothing extra — but it must be a
    decision, so this lists the ones that have never been considered."""
    undeclared = {}
    for init in sorted((GRAPH / "extractors").glob("*/__init__.py")):
        if init.parent.name.startswith("_"):
            continue
        src = init.read_text(encoding="utf-8")
        emitted = set()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if (isinstance(k, ast.Constant) and k.value == "kind"
                            and isinstance(v, ast.Constant) and isinstance(v.value, str)
                            and "-" in v.value):
                        emitted.add(v.value)
        if not emitted:
            continue
        declared = set()
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Assign) and node.targets
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "UNRESOLVED_FIELDS"
                    and isinstance(node.value, ast.Dict)):
                declared = {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
        if missing := emitted - declared:
            undeclared[init.parent.name] = sorted(missing)
    assert not undeclared, (
        f"kinds emitted with no entry in their module's UNRESOLVED_FIELDS: {undeclared}. "
        f"Add one, with an empty tuple if the kind genuinely promises nothing extra.")


# ── registration refuses a declaration it cannot trust ─────────────────────────
def test_a_bare_string_declaration_is_fatal():
    """`("language",)` mistyped as `"language"` would become one required field per
    character. Iterable, plausible, and wrong — the same trap `INPUT_GLOBS` already refuses."""
    class Bad:
        name, INPUT_GLOBS = "bad", ("*.x",)
        UNRESOLVED_FIELDS = {"k": "language"}

        def extract(self, ctx):
            return ExtractResult()

    with pytest.raises(B.BuildError, match="one required field per character"):
        B._unresolved_fields(Bad(), "bad", "where")


def test_a_non_dict_declaration_is_fatal():
    class Bad:
        UNRESOLVED_FIELDS = ["k"]

    with pytest.raises(B.BuildError, match="expected a dict"):
        B._unresolved_fields(Bad(), "bad", "where")


def test_a_class_attribute_overrides_the_module_declaration():
    class Narrow:
        UNRESOLVED_FIELDS = {"k": ("only-mine",)}

    mod = type("M", (), {"UNRESOLVED_FIELDS": {"k": ("module-wide",)}})
    assert B._unresolved_fields(Narrow(), "n", "w", mod) == {"k": ("only-mine",)}
    assert B._unresolved_fields(object(), "n", "w", mod) == {"k": ("module-wide",)}


# ── end to end: the build reports and the exit code bites ──────────────────────
def _build(fixture: str, out: Path) -> tuple[int, dict]:
    proc = subprocess.run(
        [sys.executable, str(GRAPH / "build.py"), "--workspace",
         str(GRAPH / "tests" / "fixtures" / fixture / "workspace.toml"), "--out", str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
    report = json.loads((out / "build_report.json").read_text(encoding="utf-8"))
    return proc.returncode, report


@pytest.mark.parametrize("fixture", ["orderflow", "pantry"])
def test_the_committed_fixtures_emit_no_contract_violations(fixture, tmp_path):
    code, report = _build(fixture, tmp_path / "out")
    assert report["contract_violations"] == []
    assert code == 0


def test_the_report_carries_violations_and_the_exit_code_is_non_zero(monkeypatch, tmp_path):
    """A violation is a defect in the HARNESS, not a finding about the workspace, so it
    rides beside `status` rather than becoming one — the graph can be perfectly `ok` while
    a row it emitted fails to name what it promised. The exit code is what stops that being
    cosmetic: a CI check that only ever reads `status` would never learn the difference.
    """
    report = B._report(
        ws={"root": "/w", "name": ""}, components=[], regs=[], links=[],
        act=B.Activation(by_extractor={}, by_component={}, files={}),
        results={}, link_results={}, failures={}, merged=ExtractResult(), run_id="r",
        profile_dir=None, violations=["ext: unresolved[0] kind='k' is missing 'language'"])

    assert report["contract_violations"] == [
        "ext: unresolved[0] kind='k' is missing 'language'"]
    assert any("DEFECT IN THE HARNESS" in w for w in report["warnings"]), \
        "the warning must say whose bug this is, or it gets triaged as a workspace finding"


# ── the two rows the real workspace caught ─────────────────────────────────────
def test_an_unreadable_suite_names_the_language_it_could_not_read(tmp_path):
    """The reported defect, at its own emission site: 266 rows said a suite was unreadable
    and not in what language, while `language` sat in a local variable one line above."""
    from tools.graph.extractors import tests as tests_extractor

    declared = getattr(tests_extractor, "UNRESOLVED_FIELDS", {})
    assert "language" in declared.get("grammar-unavailable", ()), \
        "the kind whose entire job is naming a language must promise to carry one"


def test_a_contested_service_names_the_components_that_contest_it():
    """206 rows in a real workspace named a contested service and no claimant. `component`
    is empty by design — the finding is about a collision BETWEEN components, and filing it
    against one would imply the very decision this pass refuses to make — so `components`
    carries the answer instead."""
    from tools.graph.links import canonical

    assert "components" in canonical.UNRESOLVED_FIELDS["contested-service"]
    assert "component" not in canonical.UNRESOLVED_FIELDS["contested-service"], \
        "component is deliberately empty here; requiring it again would defeat the point"
