"""The declared dependencies, held to what this repository actually imports.

`in-toto-attestation` sat in the `sdlc` extra imported by NOTHING, and it was
the only package pulling a `protobuf` that `osv-scanner` reports against two
CVEs at `high` — so this repository did not pass its own SCA gate on account
of a dependency no code uses. That is what an unused declaration costs: it is
not tidiness, it is supply chain, and `uv.lock` is what this project's own
comment calls the contract.

It was not alone. When this file was written, `requests` was imported nowhere
in the tree (every hit was the English word) and `pydantic` had ZERO shipped
imports — item 35 moved it into the SUITE as the independent second statement
of the policy schema and left the declaration in `[project.dependencies]`, so
every adopter installed it, and its chain, to run code that never imports it.
Removing those two took ten packages out of the base install.

The rule here is NOT "delete every unused declaration": some are genuinely
ahead of the item that will use them, and that is a legitimate thing to do.
The rule is that the reason must be WRITTEN DOWN — so the next unused
declaration fails this test until somebody says which item needs it, rather
than accreting quietly until a scanner finds it.
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

#: Distribution name → the module it actually provides, where the two differ.
#: Anything not named here is assumed to import as its own name with hyphens
#: turned into underscores, which is true of every other package declared.
IMPORT_NAME = {
    "python-dotenv": "dotenv",
    "pyyaml": "yaml",
}

#: Declared, not imported, and correct anyway — with the reason.
NOT_IMPORTED_BY_DESIGN = {
    "pytest-cov": "a pytest PLUGIN: the runner loads it by entry point, so no "
                  "source file ever imports it by name",
}

#: Declared AHEAD of the item that will use it. Each entry names that item, so
#: an unused package is a decision somebody recorded rather than a leftover.
#: An entry here is a promise: when the item lands, the import appears and the
#: entry goes away.
DECLARED_AHEAD_OF_THEIR_ITEM = {
    "duckdb": "item 52 (metrics engine, M5) names it. Item 11 chose the standard "
              "library for the validation DASHBOARD, which is a different reader",
    "deepeval": "the `eval` extra, M5+ - no item has opened against it yet",
}


def _declared() -> dict[str, str]:
    """Every declared package → the section that declares it."""
    doc = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    out = {}
    for spec in doc["project"]["dependencies"]:
        out[_name(spec)] = "dependencies"
    for extra, specs in doc["project"]["optional-dependencies"].items():
        for spec in specs:
            out[_name(spec)] = f"extra:{extra}"
    return out


def _name(spec: str) -> str:
    return re.split(r"[<>=!~\[ ]", spec.strip(), 1)[0].lower()


def _sources(tests: bool) -> list[Path]:
    """Shipped source, or test source — the distinction the pydantic defect
    turned on: a REQUIRED dependency for a test-only need is something every
    adopter installs for nothing."""
    files = [p for p in ROOT.joinpath("tools").rglob("*.py")
             if ".venv" not in p.parts and "__pycache__" not in p.parts]
    is_test = lambda p: "tests" in p.parts or p.name.startswith("test_")
    picked = [p for p in files if is_test(p) == tests]
    if not tests:
        picked.append(ROOT / "sdlc-init.py")
    return [p for p in picked if p.is_file()]


def _imports(paths: list[Path]) -> set[str]:
    found: set[str] = set()
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        found |= set(re.findall(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)",
                                text, re.M))
    return {name.split(".", 1)[0] for name in found}


def _text(paths: list[Path]) -> str:
    return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in paths)


SHIPPED_FILES = _sources(tests=False)
SHIPPED = _imports(SHIPPED_FILES)
SHIPPED_TEXT = _text(SHIPPED_FILES)
TESTED = _imports(_sources(tests=True))
DECLARED = _declared()


def _loaded_by_name(module: str) -> bool:
    """A package a LOADER resolves at run time is never `import`ed by name, and
    the four tree-sitter grammars are exactly that: `srcfacts.py` holds a table
    mapping a language to its module, and the aggregate pack publishes no
    Windows wheels, so each grammar is pinned individually and reached through
    that table. This asks the claim rather than accepting it - the module name
    has to be WRITTEN DOWN in shipped source, which is what makes it findable
    by anyone wondering why the dependency is there."""
    return re.search(rf"""['"]{re.escape(module)}['"]""", SHIPPED_TEXT) is not None


def _module(package: str) -> str:
    return IMPORT_NAME.get(package, package.replace("-", "_"))


@pytest.mark.parametrize("package", sorted(DECLARED))
def test_every_declared_package_is_imported_or_has_a_recorded_reason(package):
    if package in NOT_IMPORTED_BY_DESIGN or package in DECLARED_AHEAD_OF_THEIR_ITEM:
        return
    module = _module(package)
    assert module in SHIPPED or module in TESTED or _loaded_by_name(module), (
        f"{package} is declared in pyproject.toml ({DECLARED[package]}) and "
        f"nothing imports {module!r}. Either the code that needs it is missing, "
        f"or the declaration is - and an unused declaration is not free: "
        f"in-toto-attestation was the only thing pulling a protobuf with two "
        f"high CVEs. If it is deliberately ahead of its item, add it to "
        f"DECLARED_AHEAD_OF_THEIR_ITEM with the item named.")


@pytest.mark.parametrize("package", sorted(
    p for p, where in _declared().items() if where == "dependencies"))
def test_a_required_dependency_is_needed_by_shipped_code_not_only_by_tests(package):
    """`pydantic` was required and imported only by the suite, so every adopter
    installed it - and its chain - to run code that never touches it. A test
    need belongs in the `dev` extra, where the runner already lives."""
    if package in NOT_IMPORTED_BY_DESIGN or package in DECLARED_AHEAD_OF_THEIR_ITEM:
        return
    module = _module(package)
    assert module in SHIPPED or _loaded_by_name(module), (
        f"{package} is a REQUIRED dependency but only the test suite imports "
        f"{module!r} - move it to the `dev` extra")


def test_the_reasons_are_about_packages_that_are_actually_declared():
    """A reason for a package nobody declares is a note about the past. When an
    item lands and its import appears, its entry here must go - otherwise the
    allow-list quietly becomes the thing it was written to prevent."""
    stale = [p for p in (set(NOT_IMPORTED_BY_DESIGN) | set(DECLARED_AHEAD_OF_THEIR_ITEM))
             if p not in DECLARED]
    assert stale == [], f"recorded reasons for packages nothing declares: {stale}"


@pytest.mark.parametrize("package", sorted(DECLARED_AHEAD_OF_THEIR_ITEM))
def test_a_package_declared_ahead_of_its_item_is_still_unimported(package):
    """The other direction: once the item lands, delete the entry. An
    allow-list nobody prunes stops describing anything."""
    module = _module(package)
    assert not (module in SHIPPED or module in TESTED or _loaded_by_name(module)), (
        f"{package} is used now, so it is no longer 'ahead of its item' - "
        f"remove it from DECLARED_AHEAD_OF_THEIR_ITEM")


def test_the_import_name_map_is_only_for_packages_that_need_it():
    """Every override is a hand-written fact, and a hand-written fact that is
    no longer needed is one more thing to be wrong later."""
    pointless = [p for p, m in IMPORT_NAME.items()
                 if m == p.replace("-", "_") or p not in DECLARED]
    assert pointless == [], f"IMPORT_NAME entries that earn nothing: {pointless}"


def test_a_grammar_earns_its_pin_by_being_named_where_the_loader_reads_it():
    """Pins the MECHANISM, not the outcome. The four grammars pass the checks
    above only because `srcfacts.py` names each module in its language table;
    if that table were ever replaced by the aggregate pack, these pins would
    stop being justified and this test says so first."""
    for grammar in ("tree_sitter_java", "tree_sitter_python",
                    "tree_sitter_javascript", "tree_sitter_typescript"):
        assert _loaded_by_name(grammar), (
            f"{grammar} is pinned individually and no shipped source names it - "
            f"either the loader changed or the pin is now unearned")


def test_the_scan_actually_reads_this_repository():
    """The guard's own guard. Every assertion above rests on these two sets,
    and a path bug that emptied them would turn the whole file green while
    checking nothing - this repository's oldest failure shape."""
    assert "neo4j" in SHIPPED and "tree_sitter" in SHIPPED
    assert "pytest" in TESTED
    assert len(DECLARED) >= 10
    assert len(SHIPPED_TEXT) > 100_000
    # ...and the string scan must be able to say NO, or it says nothing.
    assert not _loaded_by_name("a_package_this_repository_never_heard_of")
