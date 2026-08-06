"""M0 kill-gate check, layer 3: core carries no technology or language knowledge.

The plan's portability claim (UC-14) is worth exactly as much as this test. Onboarding a
workspace must mean writing a profile, never editing core — and the failure mode this guards
against is gradual: one stack-shaped helper at a time until the split exists only on paper.

**Scope, narrowed at Rev 7.** This file now polices *technology* terms only, in
`tools/graph/core/` only. Workspace coordinates — the names of particular organisations,
products and repositories — moved to `tools/tests/test_no_private_coordinates.py`, which
covers **every tracked file** rather than one package, and stores its list as digests rather
than plaintext.

That split is not tidying. Listing a client's coordinates here in plaintext, in order to ban
them, published them to every reader of this file — the guard disclosing exactly what it
guarded. And the old scope was too narrow anyway: those names were legal everywhere outside
`core/`, which is where most of them actually were.

The layering that results, and it is the whole design:

    core/                  no technology, no workspace     (this file + the whole-tree one)
    extractors/<stack>/    its OWN technology, no workspace         (whole-tree one only)
    profiles/<workspace>/  its own workspace                        (declared exemption)

So `kafka` and `maven` below are banned *here* and perfectly legal in a stack extractor —
that is the point of naming a layer. Prose in docstrings may still mention a technology to
explain *why* a design is the way it is; code may not depend on it.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

CORE = Path(__file__).resolve().parents[1] / "core"

#: Technologies core must stay ignorant of. Legal in `extractors/<stack>/`, never here:
#: the moment core knows what a chart or a topic is, every adopter without them pays for it.
TECHNOLOGY_TERMS = [
    "indexer", "flyway", "helm", "kafka", "maven", "spring", "surefire", "flutter",
    "swagger", "zuul", "tenantid",
]
#: Terms that would bind core to a single language toolchain.
LANGUAGE_TERMS = ["javasrc", "pysrc", "pom_xml", "package_json", "pubspec", "gradle"]

FORBIDDEN = TECHNOLOGY_TERMS + LANGUAGE_TERMS

#: Neutral words that legitimately appear as substrings and must not trigger a match.
ALLOWED_SUBSTRING_OWNERS = {"language", "languages", "language_pack", "_lang", "get_parser"}


def core_files() -> list[Path]:
    return sorted(p for p in CORE.rglob("*.py") if "__pycache__" not in p.parts)


def test_core_has_files():
    assert core_files(), "no core modules found — wrong path?"


@pytest.mark.parametrize("path", core_files(), ids=lambda p: p.name)
def test_no_technology_terms_in_identifiers(path: Path):
    """Names of modules, classes, functions, args and attributes must be stack-neutral."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []

    def check(name: str, where: str) -> None:
        low = name.lower()
        if low in ALLOWED_SUBSTRING_OWNERS:
            return
        for term in FORBIDDEN:
            if term in low:
                offenders.append(f"{where}: {name!r} contains {term!r}")

    check(path.stem, "module name")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            check(node.name, f"function@{node.lineno}")
            for a in [*node.args.args, *node.args.kwonlyargs, *node.args.posonlyargs]:
                check(a.arg, f"arg@{node.lineno}")
        elif isinstance(node, ast.ClassDef):
            check(node.name, f"class@{node.lineno}")
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            check(node.id, f"assign@{node.lineno}")
        elif isinstance(node, ast.Attribute):
            check(node.attr, f"attribute@{node.lineno}")

    assert not offenders, f"technology/language terms leaked into core {path.name}:\n  " + "\n  ".join(offenders)


@pytest.mark.parametrize("path", core_files(), ids=lambda p: p.name)
def test_no_technology_terms_in_string_literals(path: Path):
    """String literals are code too: a hardcoded `**/pom.xml` glob is stack knowledge, and
    it binds core just as firmly as a function name would.

    Docstrings stay exempt HERE, because a technology name is not a disclosure and the
    rationale is worth more than the purity — but note the whole-tree guard makes the
    opposite call for workspace coordinates, where a name in a docstring ships to every
    reader. Same mechanism, different stakes.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings = {id(ast.get_docstring(n, clean=False)) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}
    doc_values = {ast.get_docstring(n, clean=False) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in doc_values:
                continue
            low = node.value.lower()
            for term in TECHNOLOGY_TERMS:
                if re.search(rf"\b{term}\b", low):
                    offenders.append(f"line {node.lineno}: {node.value[:60]!r} contains {term!r}")
    assert not offenders, f"technology terms in core string literals {path.name}:\n  " + "\n  ".join(offenders)


def test_core_does_not_import_profiles():
    """Dependency direction: profiles import core, never the reverse."""
    bad = []
    for path in core_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            mod = ""
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
            elif isinstance(node, ast.Import):
                mod = ",".join(a.name for a in node.names)
            if "profiles" in mod:
                bad.append(f"{path.name}:{node.lineno} imports {mod}")
    assert not bad, "core imports a profile (dependency inversion):\n  " + "\n  ".join(bad)


def test_store_seam_hides_query_language():
    """Cypher must exist only in the Neo4j adapter, so the M6 SQLite port is mechanical."""
    leaks = []
    for path in core_files():
        if path.name == "neo4j_store.py":
            continue
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text, filename=str(path))
        doc_values = {ast.get_docstring(n, clean=False) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value in doc_values:
                    continue
                if re.search(r"\b(MATCH|MERGE|UNWIND|DETACH DELETE)\b", node.value):
                    leaks.append(f"{path.name}:{node.lineno}")
    assert not leaks, f"query language outside the store adapter: {leaks}"
