"""The npm extractor: the dependency layer of the JavaScript world, joined by name.

Item 9c. npm names are registry-global, so unscoped modules merge across components by
design — the deliberate inverse of the gradle ruling, pinned here from both directions:
the global join that must happen, and the component-scoped one that must not. The
fixture's answer key claimed every edge by line before this extractor existed
(`56032ee`), including the line that must produce nothing: a workspace sibling consumed
by the component that declares it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph import build as B  # noqa: E402
from tools.graph.core.lib import ids  # noqa: E402
from tools.graph.core.spi import ExtractContext, RepoRef  # noqa: E402
from tools.graph.extractors import npm  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"


def ctx_for(tmp_path: Path, by_alias: dict[str, dict[str, str]]) -> ExtractContext:
    repos, files = [], {}
    for alias, tree in by_alias.items():
        root = tmp_path / alias
        paths = []
        for rel, body in tree.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
            paths.append(p)
        repos.append(RepoRef(alias=alias, path=root))
        files[alias] = paths
    return ExtractContext(repos=repos, run_id="r", files=files)


def run(tmp_path: Path, files: dict[str, str], alias: str = "svc"):
    return npm.NpmModulesExtractor().extract(ctx_for(tmp_path, {alias: files}))


def deps(res):
    return [e for e in res.edges if e.type == "DG_DEPENDS_ON"]


def built(res):
    return [e for e in res.edges if e.type == "DG_BUILT_FROM"]


def fixture_files() -> dict[str, str]:
    comp = FIXTURE / "web-console"
    return {p.relative_to(comp).as_posix(): p.read_text(encoding="utf-8")
            for p in comp.rglob("package.json")}


# ── the fixture chain, at the answer key's own lines ──────────────────────────


def test_the_fixture_chain_resolves_at_the_cited_lines(tmp_path):
    res = run(tmp_path, fixture_files(), alias="web-console")
    svc = ids.component_service_id("web-console")

    by_dst = {e.dst: e for e in built(res)}
    app = by_dst[ids.module_id("", "web-console")]
    assert app.source_file == "package.json" and app.line == 2
    kit = by_dst[ids.module_id("orderflow", "ui-kit")]
    assert kit.source_file == "packages/ui-kit/package.json" and kit.line == 2

    by_dep = {e.dst: e for e in deps(res)}
    jest = by_dep[ids.module_id("", "jest")]
    assert jest.src == svc
    assert jest.line == 14, "EXPECTED.md cites package.json:14"
    assert jest.props == {"version": "29.7.0", "scope": "devDependencies",
                          "version_source": "declared"}
    fns = by_dep[ids.module_id("", "date-fns")]
    assert fns.source_file == "packages/ui-kit/package.json" and fns.line == 5
    assert fns.props["version"] == "^3.6.0", "the range is kept raw, not invented into a version"


def test_the_workspace_sibling_is_never_a_dependency(tmp_path):
    """package.json:11 consumes `@orderflow/ui-kit`, a name this same component
    declares — the maven/gradle trap in npm spelling. Nothing may come of it."""
    res = run(tmp_path, fixture_files(), alias="web-console")
    assert ids.module_id("orderflow", "ui-kit") not in {e.dst for e in deps(res)}
    assert len(deps(res)) == 2
    assert res.unresolved == []


# ── identity: registry-global by design, the inverse of gradle's ruling ───────


def test_two_components_naming_one_package_meet_at_one_node(tmp_path):
    """THE join. `jest` names one package everywhere, so two components depending on it
    share a module node — and a third component DECLARING that name is its builder,
    reached by the same id. This global merge is what a registry name means."""
    res = npm.NpmModulesExtractor().extract(ctx_for(tmp_path, {
        "console-a": {"package.json": '{\n  "dependencies": {"jest": "^29.0.0"}\n}\n'},
        "console-b": {"package.json": '{\n  "dependencies": {"jest": "29.7.0"}\n}\n'},
        "kit-owner": {"package.json": '{\n  "name": "jest"\n}\n'},
    }))
    target = ids.module_id("", "jest")
    assert {e.dst for e in deps(res)} == {target}
    assert len(deps(res)) == 2, "each consumer keeps its own edge and its own spec"
    assert target in {e.dst for e in built(res)}, \
        "the declaring component is the builder of the SAME node"


def test_a_scoped_name_splits_at_the_slash(tmp_path):
    res = run(tmp_path, {"package.json":
                         '{\n  "dependencies": {"@acme/ui-kit": "~1.2.0"}\n}\n'})
    (e,) = deps(res)
    assert e.dst == ids.module_id("acme", "ui-kit")
    assert e.props["version"] == "~1.2.0"


def test_an_anonymous_manifest_builds_nothing_but_its_dependencies_still_count(tmp_path):
    """An app root without a `name` publishes nothing a registry could hold — no module
    node is invented for it, and its dependencies belong to the component's service."""
    res = run(tmp_path, {"package.json":
                         '{\n  "private": true,\n  "dependencies": {"react": "^18.2.0"}\n}\n'})
    assert built(res) == []
    (e,) = deps(res)
    assert e.src == ids.component_service_id("svc")


def test_a_file_spec_keeps_its_edge(tmp_path):
    """`file:` points at a path, but the dependency is declared BY NAME — and when that
    name is not one this component declares, the claim is on a module elsewhere."""
    res = run(tmp_path, {"package.json":
                         '{\n  "dependencies": {"shared-theme": "file:../theme"}\n}\n'})
    (e,) = deps(res)
    assert e.dst == ids.module_id("", "shared-theme")
    assert e.props["version"] == "file:../theme"


def test_peer_and_optional_sections_carry_their_own_scope(tmp_path):
    res = run(tmp_path, {"package.json":
                         '{\n'
                         '  "peerDependencies": {"react": "^18.0.0"},\n'
                         '  "optionalDependencies": {"fsevents": "^2.3.0"}\n'
                         '}\n'})
    scopes = {e.dst: e.props["scope"] for e in deps(res)}
    assert scopes[ids.module_id("", "react")] == "peerDependencies"
    assert scopes[ids.module_id("", "fsevents")] == "optionalDependencies"


def test_a_dependency_repeated_across_sections_cites_each_own_line(tmp_path):
    body = ('{\n'
            '  "devDependencies": {"probe": "^1.0.0"},\n'
            '  "dependencies": {"probe": "^1.0.0"}\n'
            '}\n')
    res = run(tmp_path, {"package.json": body})
    lines = sorted((e.props["scope"], e.line) for e in deps(res))
    assert lines == [("dependencies", 3), ("devDependencies", 2)], \
        "each section's entry resolves to its own occurrence, not the first for both"


# ── fail-loud and the boundaries ──────────────────────────────────────────────


def test_an_unparsable_manifest_is_a_row_and_the_readable_one_still_emits(tmp_path):
    res = run(tmp_path, {"package.json": '{"name": "app", "dependencies": {}',
                         "packages/kit/package.json": '{\n  "name": "@acme/kit"\n}\n'})
    (row,) = res.unresolved
    assert row["kind"] == "unparsable" and row["error"]
    assert row["source_file"] == "package.json"
    assert built(res), "the readable manifest must still produce its module"


def test_a_manifest_under_a_test_tree_is_not_read(tmp_path):
    res = run(tmp_path, {"src/test/fixtures/package.json":
                         '{\n  "dependencies": {"decoy": "1.0.0"}\n}\n'})
    assert res.edges == [] and res.nodes == []


def test_the_package_registers_through_the_real_pipeline():
    regs = {r.name: r for r in B.discover_extractors()}
    assert "npm-modules" in regs
    assert regs["npm-modules"].layer == "stack"
    assert regs["npm-modules"].globs == ("package.json",)
