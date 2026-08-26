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

import ast
import re
import subprocess
import tomllib
from pathlib import Path

import pytest

# No `sys.path` bootstrap: this module imports nothing from the
# repository - it reads the tree as text and as an AST. The
# insert was copy-pasted from the sibling test files that do
# need it, and it mutated a process-global for the whole session.
ROOT = Path(__file__).resolve().parents[2]

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


def _declared() -> dict[str, set[str]]:
    """Every declared package -> the sections that declare it.

    A SET, not one label. The first cut kept a flat dict and wrote extras
    last, so a package declared in BOTH `[project.dependencies]` and an extra
    came out as `extra:<name>` - and silently left the required-dependency
    parametrisation below, which is the guard against the exact `pydantic`
    regression this file exists to prevent."""
    doc = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for spec in doc["project"]["dependencies"]:
        out.setdefault(_name(spec), set()).add("dependencies")
    for extra, specs in doc["project"]["optional-dependencies"].items():
        for spec in specs:
            out.setdefault(_name(spec), set()).add(f"extra:{extra}")
    return out


def _name(spec: str) -> str:
    """A PEP 503 normalised distribution name.

    `_`, `-` and `.` fold together, because that is what a distribution name
    means and because the two sibling helpers in this directory
    (`test_requirements_matches_the_lock._normalise` and
    `test_repo_contract`) already fold them. This one did not, so the first
    `foo_bar`-spelled requirement would have keyed the allow-lists below on a
    name the other two normalise away, and the entry would silently stop
    applying."""
    bare = re.split(r"[<>=!~\[ ]", spec.strip(), maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", bare).lower()


def _tracked_python() -> list[Path]:
    """Every tracked `.py` file, from git rather than from a glob.

    `test_no_private_coordinates` makes the argument in its own docstring and
    it applies here: the working tree carries `.venv`, `__pycache__` and -
    the case that bit this file - `.sdlc-worktrees/`, which `worktree.py`
    creates INSIDE the repository. A glob with a hand-written exclude list
    swept a live worktree's copy of the tree in as shipped source, so another
    run's checkout could vouch for a declaration nothing really imports."""
    out = subprocess.run(["git", "ls-files", "-z", "*.py"], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    return [ROOT / rel for rel in out.split(chr(0)) if rel.endswith(".py")]


def _is_test(path: Path) -> bool:
    rel = path.relative_to(ROOT).as_posix()
    return ("tests/" in rel or path.name.startswith("test_")
            or path.name.endswith("_test.py"))


def _sources(tests: bool) -> list[Path]:
    """Shipped source, or test source - the distinction the pydantic defect
    turned on: a REQUIRED dependency for a test-only need is something every
    adopter installs for nothing."""
    return [p for p in _tracked_python() if _is_test(p) == tests and p.is_file()]


def _parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return None


def _imports(paths: list[Path]) -> set[str]:
    r"""The top-level module names these files actually import.

    Read with `ast`, not a regex. The regex this replaced was
    `^\s*(?:import|from)\s+([A-Za-z_][\w.]*)` over raw text, so it matched
    PROSE: measured against this repository it found 72 names where the parse
    finds 59, and the 13 extra were ordinary English words that happened to
    begin a docstring line after `from` or `import`. A declared package whose
    module name landed in a sentence therefore passed the unused-declaration
    check - which is precisely the failure this file's own docstring describes
    for `requests`, re-introduced by the scanner written to catch it. It also
    dropped the second name in `import os, sys`."""
    found: set[str] = set()
    for path in paths:
        tree = _parse(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found |= {alias.name.split(".", 1)[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".", 1)[0])
    return found


def _docstring_nodes(tree: ast.Module) -> set[int]:
    """The id()s of the string constants that are docstrings, so the literal
    scan below can ignore them."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                out.add(id(body[0].value))
    return out


def _named_in_code(paths: list[Path]) -> set[str]:
    """Every string literal that shipped CODE spells, docstrings excluded.

    A package a LOADER resolves at run time is never `import`ed by name: the
    four tree-sitter grammars are reached through `srcfacts.py`'s
    language-to-module table, because the aggregate pack publishes no Windows
    wheels. The claim is asked rather than assumed - the module name has to be
    written down in shipped source - but the first cut asked it of the raw
    TEXT, comments and docstrings included. A reviewer proved that: replacing
    the loader table so every language went through the aggregate pack, while
    leaving a stale comment naming the four modules, kept all four pins
    passing. Only real literals count now."""
    out: set[str] = set()
    for path in paths:
        tree = _parse(path)
        if tree is None:
            continue
        skip = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and id(node) not in skip):
                out.add(node.value)
    return out


SHIPPED_FILES = _sources(tests=False)
SHIPPED = _imports(SHIPPED_FILES)
SHIPPED_LITERALS = _named_in_code(SHIPPED_FILES)
TESTED = _imports(_sources(tests=True))
DECLARED = _declared()


def _loaded_by_name(module: str) -> bool:
    """Is this module named by shipped CODE, as a string literal?

    See `_named_in_code`. The first cut asked this of raw TEXT, so a comment
    or a docstring answered yes - and a reviewer proved it: replacing the
    loader table so every language went through the aggregate pack, while
    leaving a stale comment naming the four grammar modules, kept all four
    now-unjustified pins passing."""
    return module in SHIPPED_LITERALS


def _module(package: str) -> str:
    return IMPORT_NAME.get(package, package.replace("-", "_"))


@pytest.mark.parametrize("package", sorted(DECLARED))
def test_every_declared_package_is_imported_or_has_a_recorded_reason(package):
    if package in NOT_IMPORTED_BY_DESIGN or package in DECLARED_AHEAD_OF_THEIR_ITEM:
        return
    module = _module(package)
    assert module in SHIPPED or module in TESTED or _loaded_by_name(module), (
        f"{package} is declared in pyproject.toml "
        f"({', '.join(sorted(DECLARED[package]))}) and "
        f"nothing imports {module!r}. Either the code that needs it is missing, "
        f"or the declaration is - and an unused declaration is not free: "
        f"in-toto-attestation was the only thing pulling a protobuf with two "
        f"high CVEs. If it is deliberately ahead of its item, add it to "
        f"DECLARED_AHEAD_OF_THEIR_ITEM with the item named.")


@pytest.mark.parametrize("package", sorted(
    p for p, where in DECLARED.items() if "dependencies" in where))
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


def test_the_literal_scan_reads_code_and_not_prose(tmp_path):
    """The mechanism, on a module written for the purpose.

    Asserted directly rather than sampled from the real tree, because a
    heuristic over real source cannot tell a docstring from a long error
    message - and the property that matters is exactly that distinction. The
    scan this replaced read raw text, so a comment naming a module answered
    "yes, the loader uses it" for four dependencies whose loader had been
    replaced."""
    module = tmp_path / "sample.py"
    module.write_text(
        '"""tree_sitter_from_a_docstring is what this module is about."""\n'
        "# tree_sitter_from_a_comment is named here too\n"
        'TABLE = {"java": ("tree_sitter_from_real_code", "language")}\n'
        "def f():\n"
        '    """tree_sitter_from_a_function_docstring."""\n'
        '    return "tree_sitter_from_a_return"\n',
        encoding="utf-8")
    found = _named_in_code([module])
    assert "tree_sitter_from_real_code" in found
    assert "tree_sitter_from_a_return" in found
    assert not any("docstring" in lit for lit in found)
    assert not any("comment" in lit for lit in found)


def test_the_scan_actually_reads_this_repository():
    """The guard's own guard. Every assertion above rests on these two sets,
    and a path bug that emptied them would turn the whole file green while
    checking nothing - this repository's oldest failure shape."""
    assert "neo4j" in SHIPPED and "tree_sitter" in SHIPPED
    assert "pytest" in TESTED
    assert len(DECLARED) >= 10
    assert len(SHIPPED_LITERALS) > 500
    # ...and the string scan must be able to say NO, or it says nothing.
    assert not _loaded_by_name("a_package_this_repository_never_heard_of")
