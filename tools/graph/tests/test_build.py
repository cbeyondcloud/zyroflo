"""The build pipeline: registration, activation, the four empties, and isolation.

No extractor exists yet, which is what makes these tests worth writing now: every one of
them is about the *pipeline's* behaviour, and pipeline behaviour is what decides whether a
missing extractor shows up as a warning or as a graph that merely looks small.
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph import build as B  # noqa: E402
from tools.graph.core.model import Edge, ExtractResult, Node  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────


def make_extractor_pkg(root: Path, name: str, body: str) -> Path:
    """Write one extractor subpackage. `body` is the whole __init__.py."""
    pkg = root / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(textwrap.dedent(body), encoding="utf-8")
    return pkg


#: A minimal well-formed extractor, parameterised by name, globs and what it emits.
EXTRACTOR_TEMPLATE = '''
    from tools.graph.core.model import Edge, ExtractResult, Node

    class _E:
        name = {name!r}
        INPUT_GLOBS = {globs!r}

        def extract(self, ctx):
            res = ExtractResult()
            for repo in ctx.repos:
                res.nodes.append(Node(id=f"svc:{{repo.alias}}", type="svc"))
            {body}
            return res

    EXTRACTORS = (_E(),)
'''


def simple_extractor(root: Path, pkg: str, name: str, globs: tuple[str, ...],
                     body: str = "pass") -> None:
    make_extractor_pkg(root, pkg, EXTRACTOR_TEMPLATE.format(name=name, globs=globs, body=body))


EDGE_BODY = '''
            if len(ctx.repos) >= 1:
                res.edges.append(Edge(src=f"svc:{ctx.repos[0].alias}", type="DG_CALLS",
                                      dst="ep:/x", source_file="a.txt", line=1,
                                      extractor="e"))
'''


def make_workspace(tmp_path: Path, components: dict[str, list[str]],
                   name: str = "") -> Path:
    """Create a workspace of declared components, each holding the given relative files.

    Components are DECLARED with an explicit `path` rather than given a `.git`, which is the
    same route the committed fixture takes: git refuses to track a nested `.git`, silently.
    """
    root = tmp_path / "ws"
    root.mkdir(parents=True, exist_ok=True)
    lines = ["schema_version = 1", 'root = "."', f'name = "{name}"', ""]
    for comp, files in components.items():
        (root / comp).mkdir(parents=True, exist_ok=True)
        for rel in files:
            f = root / comp / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x\n", encoding="utf-8")
        lines += ["[[component]]", f'name = "{comp}"', f'path = "{comp}"', ""]
    cfg = root / "workspace.toml"
    cfg.write_text("\n".join(lines), encoding="utf-8")
    return cfg


def run_build(cfg: Path, out: Path, ext_root: Path | None, **kw) -> dict:
    return B.build(cfg, out, run_id=kw.pop("run_id", "testrun0001"),
                   extractor_root=ext_root, **kw)


# ── registration: every violation is fatal, and says what to do ───────────────


def test_a_missing_extractor_root_registers_nothing(tmp_path: Path):
    assert B.discover_extractors(tmp_path / "nope") == []


def test_a_well_formed_package_registers(tmp_path: Path):
    simple_extractor(tmp_path, "alpha", "alpha", ("*.txt",))
    regs = B.discover_extractors(tmp_path)
    assert [r.name for r in regs] == ["alpha"]
    assert regs[0].globs == ("*.txt",)
    assert regs[0].layer == "stack"
    assert regs[0].module.endswith("/alpha")


def test_an_extractor_root_outside_the_repo_loads_even_when_it_is_the_default(
        tmp_path: Path, monkeypatch):
    """`EXTRACTOR_ROOT` is the DEFAULT root, not proof that a dotted import would resolve.

    Choosing how to load a package by comparing against that global makes every extractor
    outside the repo tree fail to import the moment anything points the default at one.
    """
    ext = tmp_path / "ext"
    simple_extractor(ext, "alpha", "alpha", ("*.txt",))
    monkeypatch.setattr(B, "EXTRACTOR_ROOT", ext)
    assert [r.name for r in B.discover_extractors()] == ["alpha"]


def test_the_repos_own_extractors_load_under_their_real_dotted_name():
    """One module object per extractor. Spec-loading them under a synthetic name would hand a
    second copy to anything that later imports them normally — two classes, two module-level
    constants, and a shared-cache key that compares equal while the code disagrees about who
    wrote it."""
    import tools.graph.extractors.properties as direct

    regs = {r.name: r for r in B.discover_extractors()}
    assert type(regs["properties"].extractor) is direct.PropertiesExtractor


def test_private_and_cache_directories_are_skipped(tmp_path: Path):
    simple_extractor(tmp_path, "alpha", "alpha", ("*.txt",))
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / ".hidden").mkdir()
    assert [r.name for r in B.discover_extractors(tmp_path)] == ["alpha"]


def test_a_directory_without_an_init_is_a_hard_error(tmp_path: Path):
    simple_extractor(tmp_path, "alpha", "alpha", ("*.txt",))
    (tmp_path / "beta").mkdir()
    with pytest.raises(B.BuildError, match="no __init__.py"):
        B.discover_extractors(tmp_path)


def test_an_import_error_names_the_package_that_failed(tmp_path: Path):
    make_extractor_pkg(tmp_path, "broken", "import a_module_that_does_not_exist_xyz\n")
    with pytest.raises(B.BuildError, match=r"broken.*failed to import.*ModuleNotFoundError"):
        B.discover_extractors(tmp_path)


def test_a_package_registering_nothing_is_a_hard_error(tmp_path: Path):
    """A package may export extractors, link passes, or both. Exporting neither means it is
    imported and then ignored, which reads as a capability this harness does not have."""
    make_extractor_pkg(tmp_path, "quiet", "X = 1\n")
    with pytest.raises(B.BuildError, match="neither `EXTRACTORS` nor `LINK_PASSES`"):
        B.discover_extractors(tmp_path)


def test_an_empty_EXTRACTORS_is_a_hard_error(tmp_path: Path):
    make_extractor_pkg(tmp_path, "empty", "EXTRACTORS = ()\n")
    with pytest.raises(B.BuildError, match="`EXTRACTORS` is empty"):
        B.discover_extractors(tmp_path)


def test_two_extractors_with_one_name_is_a_hard_error(tmp_path: Path):
    simple_extractor(tmp_path, "alpha", "same", ("*.txt",))
    simple_extractor(tmp_path, "beta", "same", ("*.md",))
    with pytest.raises(B.BuildError, match="already registered"):
        B.discover_extractors(tmp_path)


@pytest.mark.parametrize("attrs, expected", [
    (["name = ''", "INPUT_GLOBS = ('*.txt',)"], "no usable `name`"),
    (["INPUT_GLOBS = ('*.txt',)"], "no usable `name`"),
    (["name = 'a'", "INPUT_GLOBS = '*.txt'"], "must be a TUPLE"),
    (["name = 'a'", "INPUT_GLOBS = ()"], "missing or empty"),
    (["name = 'a'"], "missing or empty"),
    (["name = 'a'", "INPUT_GLOBS = ('',)"], "which is not a pattern"),
    (["name = 'a'", "INPUT_GLOBS = (7,)"], "which is not a pattern"),
])
def test_a_malformed_extractor_is_rejected_at_registration(tmp_path: Path, attrs, expected):
    make_extractor_pkg(tmp_path, "bad", "class _E:\n"
                       + "".join(f"    {a}\n" for a in attrs)
                       + "    def extract(self, ctx):\n        return None\n"
                       + "EXTRACTORS = (_E(),)\n")
    with pytest.raises(B.BuildError, match=expected):
        B.discover_extractors(tmp_path)


def test_an_extractor_without_extract_is_rejected(tmp_path: Path):
    make_extractor_pkg(tmp_path, "bad", """
        class _E:
            name = "a"
            INPUT_GLOBS = ("*.txt",)
        EXTRACTORS = (_E(),)
    """)
    with pytest.raises(B.BuildError, match="no callable `extract"):
        B.discover_extractors(tmp_path)


def test_the_bare_string_message_shows_the_fix(tmp_path: Path):
    """A one-pattern tuple is easy to write as a string, and a string is iterable — so
    without this check the globs would silently become one pattern per character."""
    make_extractor_pkg(tmp_path, "bad", """
        class _E:
            name = "a"
            INPUT_GLOBS = "pom.xml"
            def extract(self, ctx):
                return None
        EXTRACTORS = (_E(),)
    """)
    with pytest.raises(B.BuildError, match=r"INPUT_GLOBS = \('pom.xml',\)"):
        B.discover_extractors(tmp_path)


# ── ordering: the shared cache makes it load-bearing ──────────────────────────


def requiring(root: Path, pkg: str, name: str, requires: tuple[str, ...]) -> None:
    make_extractor_pkg(root, pkg, f"""
        from tools.graph.core.model import ExtractResult
        class _E:
            name = {name!r}
            INPUT_GLOBS = ("*.txt",)
            REQUIRES = {requires!r}
            def extract(self, ctx):
                return ExtractResult()
        EXTRACTORS = (_E(),)
    """)


def test_an_extractor_runs_after_what_it_requires(tmp_path: Path):
    """Directory-alphabetical order is arbitrary, and the shared cache is not.

    An extractor that reads a resolved view before the extractor that publishes it sees
    nothing and reports every reference as unresolved — a wrong graph from a correct build.
    """
    ext = tmp_path / "ext"
    requiring(ext, "aaa", "consumer", ("provider",))
    requiring(ext, "zzz", "provider", ())
    assert [r.name for r in B.discover_extractors(ext)] == ["provider", "consumer"]


def test_ordering_is_deterministic_among_independent_extractors(tmp_path: Path):
    ext = tmp_path / "ext"
    for pkg in ("c", "a", "b"):
        requiring(ext, pkg, pkg, ())
    assert [r.name for r in B.discover_extractors(ext)] == ["a", "b", "c"]


def test_a_chain_of_requirements_is_ordered_end_to_end(tmp_path: Path):
    ext = tmp_path / "ext"
    requiring(ext, "one", "one", ("two",))
    requiring(ext, "two", "two", ("three",))
    requiring(ext, "three", "three", ())
    assert [r.name for r in B.discover_extractors(ext)] == ["three", "two", "one"]


def test_a_cycle_is_a_hard_error_naming_its_members(tmp_path: Path):
    ext = tmp_path / "ext"
    requiring(ext, "a", "a", ("b",))
    requiring(ext, "b", "b", ("a",))
    with pytest.raises(B.BuildError, match="cycle.*a, b"):
        B.discover_extractors(ext)


def test_requires_written_as_a_bare_string_is_rejected(tmp_path: Path):
    """A string is iterable, so `REQUIRES = "properties"` would become one requirement per
    letter — none of which names a registered extractor, so the ordering silently reverts to
    alphabetical and the failure surfaces as a graph with edges missing."""
    ext = tmp_path / "ext"
    make_extractor_pkg(ext, "bad", """
        class _E:
            name = "a"
            INPUT_GLOBS = ("*.txt",)
            REQUIRES = "properties"
            def extract(self, ctx):
                return None
        EXTRACTORS = (_E(),)
    """)
    with pytest.raises(B.BuildError, match=r"REQUIRES.*TUPLE.*\('properties',\)"):
        B.discover_extractors(ext)


def test_a_requirement_that_did_not_run_is_a_warning_not_a_refusal(tmp_path: Path):
    """`--only` legitimately excludes a provider while debugging. The consumer still runs and
    reports what it could not look up — but the report has to say the graph is short."""
    cfg = make_workspace(tmp_path, {"svc": ["a.txt"]})
    ext = tmp_path / "ext"
    requiring(ext, "aaa", "consumer", ("provider",))
    requiring(ext, "zzz", "provider", ())

    report = run_build(cfg, tmp_path / "out", ext, only=["consumer"])
    assert any("needs provider, which did not run" in w for w in report["warnings"])


def test_a_requirement_naming_an_unregistered_extractor_does_not_break_ordering(tmp_path: Path):
    ext = tmp_path / "ext"
    requiring(ext, "a", "a", ("never-registered",))
    assert [r.name for r in B.discover_extractors(ext)] == ["a"]


# ── the version stamp ─────────────────────────────────────────────────────────


def test_extractor_version_changes_when_the_registered_set_changes(tmp_path: Path):
    simple_extractor(tmp_path, "alpha", "alpha", ("*.txt",))
    one = B.extractor_version(B.discover_extractors(tmp_path))
    assert one == B.extractor_version(B.discover_extractors(tmp_path))

    simple_extractor(tmp_path, "beta", "beta", ("*.md",))
    assert B.extractor_version(B.discover_extractors(tmp_path)) != one


def test_extractor_version_changes_when_only_the_globs_change(tmp_path: Path):
    simple_extractor(tmp_path, "alpha", "alpha", ("*.txt",))
    before = B.extractor_version(B.discover_extractors(tmp_path))
    simple_extractor(tmp_path, "alpha", "alpha", ("*.txt", "*.md"))
    assert B.extractor_version(B.discover_extractors(tmp_path)) != before


# ── activation is by FILES, not by the detected stack label ───────────────────


def test_activation_matches_files_not_the_stack_label(tmp_path: Path):
    """The regression that motivated the design.

    `detect_stack()` returns ONE label per component, chosen by first match. A tree holding
    only deployment charts detects as `unknown`, and a component with a build manifest
    detects as its build system regardless of what else it contains. Gating activation on
    that label would silently drop every extractor whose technology is not the label — which
    is not a smaller graph, it is a graph missing whole edge classes with nothing to say so.
    """
    cfg = make_workspace(tmp_path, {"charts-only": ["values.yaml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "charts", "charts", ("values.yaml",))

    report = run_build(cfg, tmp_path / "out", ext)
    comp = report["components"][0]
    assert comp["stack"] == "unknown", "precondition: nothing here identifies a stack"
    assert comp["claimed_by"] == ["charts"]


def test_one_component_can_activate_several_extractors(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml", "app.properties", "V1__x.sql"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",))
    simple_extractor(ext, "b", "b", ("*.properties",))
    simple_extractor(ext, "c", "c", ("V*__*.sql",))

    report = run_build(cfg, tmp_path / "out", ext)
    assert report["components"][0]["claimed_by"] == ["a", "b", "c"]


def test_an_extractor_that_matches_nothing_claims_nothing(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",))
    simple_extractor(ext, "z", "z", ("*.nope",))

    report = run_build(cfg, tmp_path / "out", ext)
    by_name = {e["name"]: e for e in report["extractors"]}
    assert by_name["z"]["components"] == []
    assert by_name["a"]["components"] == ["svc"]


def test_an_extractor_sees_only_the_components_it_claimed(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"one": ["pom.xml"], "two": ["package.json"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",))

    report = run_build(cfg, tmp_path / "out", ext)
    graph = json.loads(json.dumps(report))          # report is plain data
    assert graph["extractors"][0]["components"] == ["one"]
    # the extractor emits one svc node per repo it is handed, so the count is the proof
    assert graph["extractors"][0]["nodes"] == 1


def test_activation_walks_a_nested_path_glob(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["src/main/resources/application.properties"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "p", "p", ("src/main/resources/*.properties",))
    report = run_build(cfg, tmp_path / "out", ext)
    assert report["components"][0]["claimed_by"] == ["p"]


# ── the four empties, each with its own exit code ─────────────────────────────


def test_no_extractors_registered_is_a_refusal(tmp_path: Path, monkeypatch):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    report = run_build(cfg, tmp_path / "out", tmp_path / "no-extractors-here")
    assert report["status"] == "no-extractors"
    assert any("no extractors are registered" in w for w in report["warnings"])

    monkeypatch.setattr(B, "EXTRACTOR_ROOT", tmp_path / "no-extractors-here")
    assert B.main(["--workspace", str(cfg), "--out", str(tmp_path / "out2")]) == 2


def test_registered_but_nothing_activated_is_a_refusal(tmp_path: Path):
    """Distinct from 'no extractors', and it must be — one is an unbuilt pipeline, the other
    is globs that do not match the trees on disk, and they are fixed in different places."""
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("*.nope",))

    report = run_build(cfg, tmp_path / "out", ext)
    assert report["status"] == "no-activation"
    assert any("misconfiguration" in w for w in report["warnings"])
    assert any("1 extractor(s) are registered" in w for w in report["warnings"])


def test_activation_with_zero_edges_warns_but_succeeds(tmp_path: Path, monkeypatch):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",))          # emits a node, no edges

    report = run_build(cfg, tmp_path / "out", ext)
    assert report["status"] == "no-edges"
    assert any("ZERO edges" in w for w in report["warnings"])

    monkeypatch.setattr(B, "EXTRACTOR_ROOT", ext)
    rc = B.main(["--workspace", str(cfg), "--out", str(tmp_path / "out2")])
    assert rc == 0, "a workspace with no couplings is a legitimate answer"


def test_a_normal_build_is_ok(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)

    report = run_build(cfg, tmp_path / "out", ext)
    assert report["status"] == "ok"
    assert report["totals"]["edges"] == 1
    assert report["warnings"] == []


def test_components_claimed_by_nobody_are_named(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"], "docs": ["README.md"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)

    report = run_build(cfg, tmp_path / "out", ext)
    assert report["status"] == "ok"
    assert report["components_unclaimed"] == ["docs"]
    assert any("docs" in w and "coverage hole" in w for w in report["warnings"])


def test_an_unclaimed_component_is_not_an_error(tmp_path: Path, monkeypatch):
    """A coverage hole is a finding, not a failure — a docs-only component legitimately has
    no extractor, and exiting non-zero on one would train people to ignore the exit code."""
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"], "docs": ["README.md"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)
    monkeypatch.setattr(B, "EXTRACTOR_ROOT", ext)
    assert B.main(["--workspace", str(cfg), "--out", str(tmp_path / "out")]) == 0


# ── one broken extractor must not cost the workspace its graph ────────────────


def test_a_raising_extractor_is_recorded_and_the_build_continues(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    make_extractor_pkg(ext, "boom", """
        class _E:
            name = "boom"
            INPUT_GLOBS = ("pom.xml",)
            def extract(self, ctx):
                raise ValueError("upstream ate the file")
        EXTRACTORS = (_E(),)
    """)
    simple_extractor(ext, "good", "good", ("pom.xml",), body=EDGE_BODY)

    report = run_build(cfg, tmp_path / "out", ext)
    assert report["status"] == "ok"
    assert report["totals"]["edges"] == 1, "the working extractor still ran"
    boom = next(e for e in report["extractors"] if e["name"] == "boom")
    assert "ValueError: upstream ate the file" in boom["failed"]
    assert any("boom" in w and "emitted nothing" in w for w in report["warnings"])


def test_every_activated_extractor_failing_is_a_refusal(tmp_path: Path, monkeypatch):
    """One broken reader is a warning; all of them broken is not the same fact.

    Without its own state this lands in `no-edges` and exits 0 — a wholly broken pipeline
    reporting a workspace with no couplings, which a CI check on the exit code would pass.
    """
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    make_extractor_pkg(ext, "boom", """
        class _E:
            name = "boom"
            INPUT_GLOBS = ("pom.xml",)
            def extract(self, ctx):
                raise ValueError("nope")
        EXTRACTORS = (_E(),)
    """)
    report = run_build(cfg, tmp_path / "out", ext)
    assert report["status"] == "all-failed"
    assert any("not a statement about the workspace" in w for w in report["warnings"])

    monkeypatch.setattr(B, "EXTRACTOR_ROOT", ext)
    assert B.main(["--workspace", str(cfg), "--out", str(tmp_path / "out2")]) == 2


def test_one_failure_among_several_is_still_a_successful_build(tmp_path: Path, monkeypatch):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    make_extractor_pkg(ext, "boom", """
        class _E:
            name = "boom"
            INPUT_GLOBS = ("pom.xml",)
            def extract(self, ctx):
                raise ValueError("nope")
        EXTRACTORS = (_E(),)
    """)
    simple_extractor(ext, "good", "good", ("pom.xml",), body=EDGE_BODY)
    monkeypatch.setattr(B, "EXTRACTOR_ROOT", ext)
    assert B.main(["--workspace", str(cfg), "--out", str(tmp_path / "out")]) == 0


def test_an_extractor_returning_the_wrong_type_is_recorded(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    make_extractor_pkg(ext, "wrong", """
        class _E:
            name = "wrong"
            INPUT_GLOBS = ("pom.xml",)
            def extract(self, ctx):
                return {"nodes": [], "edges": []}
        EXTRACTORS = (_E(),)
    """)
    report = run_build(cfg, tmp_path / "out", ext)
    wrong = next(e for e in report["extractors"] if e["name"] == "wrong")
    assert "not an ExtractResult" in wrong["failed"]


# ── emission ──────────────────────────────────────────────────────────────────


def test_each_extractor_gets_its_own_shard_plus_a_merged_graph(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)
    out = tmp_path / "out"

    run_build(cfg, out, ext)
    assert (out / "extract" / "a.jsonl").is_file()
    assert (out / "extract" / "a.unresolved.jsonl").is_file()
    assert (out / "graph.jsonl").is_file()
    assert (out / "unresolved.jsonl").is_file()
    assert (out / "manifest.json").is_file()
    assert (out / "build_report.json").is_file()


def test_unresolved_rows_reach_the_merged_file(tmp_path: Path):
    """`unresolved` is an output, not a log line: an extractor that drops what it could not
    understand makes the graph look complete when it is not."""
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    make_extractor_pkg(ext, "u", """
        from tools.graph.core.model import ExtractResult
        class _E:
            name = "u"
            INPUT_GLOBS = ("pom.xml",)
            def extract(self, ctx):
                return ExtractResult(unresolved=[{"why": "placeholder has no default",
                                                  "raw": "${SOME_KEY}"}])
        EXTRACTORS = (_E(),)
    """)
    out = tmp_path / "out"
    report = run_build(cfg, out, ext)

    assert report["totals"]["unresolved"] == 1
    body = (out / "unresolved.jsonl").read_text(encoding="utf-8")
    assert "placeholder has no default" in body


def test_every_component_becomes_a_node_even_with_no_extractor(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"one": ["a.txt"], "two": ["b.txt"], "three": ["c.txt"]})
    report = run_build(cfg, tmp_path / "out", tmp_path / "none")
    assert report["totals"]["nodes"] == 3


def test_removing_an_inert_component_changes_only_its_own_node(tmp_path: Path):
    """The pipeline half of the fixture's negative control: a component contributes its own
    node and nothing else, so deleting an inert one cannot perturb anything between others."""
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)

    with_inert = make_workspace(tmp_path / "w1", {"svc": ["pom.xml"], "inert": ["notes.md"]})
    without = make_workspace(tmp_path / "w2", {"svc": ["pom.xml"]})
    r1 = run_build(with_inert, tmp_path / "o1", ext)
    r2 = run_build(without, tmp_path / "o2", ext)

    assert r1["totals"]["nodes"] == r2["totals"]["nodes"] + 1
    edges1 = [ln for ln in (tmp_path / "o1" / "graph.jsonl").read_text().splitlines()
              if '"kind": "edge"' in ln]
    edges2 = [ln for ln in (tmp_path / "o2" / "graph.jsonl").read_text().splitlines()
              if '"kind": "edge"' in ln]
    assert edges1 == edges2


# ── determinism ───────────────────────────────────────────────────────────────


def test_two_builds_of_one_workspace_are_byte_identical(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)

    run_build(cfg, tmp_path / "o1", ext)
    run_build(cfg, tmp_path / "o2", ext)
    assert (tmp_path / "o1" / "graph.jsonl").read_bytes() == \
           (tmp_path / "o2" / "graph.jsonl").read_bytes()


def test_a_different_run_id_does_not_change_the_graph(tmp_path: Path):
    """`rebuild == snapshot` is an extractor regression check, so build metadata must stay
    out of the serialized record — otherwise the check is red on every run and gets ignored."""
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)

    run_build(cfg, tmp_path / "o1", ext, run_id="aaaaaaaa")
    run_build(cfg, tmp_path / "o2", ext, run_id="bbbbbbbb")
    assert (tmp_path / "o1" / "graph.jsonl").read_bytes() == \
           (tmp_path / "o2" / "graph.jsonl").read_bytes()


def test_the_run_id_is_stamped_onto_edges_in_memory(tmp_path: Path):
    """An extractor that forgets to carry ctx.run_id would emit edges no build owns, which is
    invisible until someone tries to expire an old run's rows."""
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)

    regs = B.discover_extractors(ext)
    comps = [{"name": "svc", "path": "svc", "role": "authoritative", "stack": "java-maven"}]
    root = cfg.parent
    act = B.activate(regs, comps, root)
    results, failures = B.run_extractors(regs, act, comps, root, "zzz12345", {})
    assert failures == {}
    assert all(e.run_id == "zzz12345" for e in results["a"].edges)


# ── the manifest ──────────────────────────────────────────────────────────────


def test_the_manifest_stamps_every_component_and_counts_every_extractor(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"one": ["pom.xml"], "two": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)
    out = tmp_path / "out"

    run_build(cfg, out, ext)
    mf = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert set(mf["repos"]) == {"one", "two"}
    assert mf["counts"]["a"] > 0
    assert mf["extractor_version"]
    assert any(k.startswith("one#a/") for k in mf["files"]), \
        "the files an extractor read are recorded per (component, extractor)"


# ── selection flags fail loudly on a name that does not exist ─────────────────


def test_only_with_an_unknown_name_is_a_hard_error(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",))
    with pytest.raises(B.BuildError, match="--only names typo.*Registered: a"):
        run_build(cfg, tmp_path / "out", ext, only=["typo"])


def test_components_with_an_unknown_name_is_a_hard_error(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    with pytest.raises(B.BuildError, match="--components names nope.*Known: svc"):
        run_build(cfg, tmp_path / "out", None, components_filter=["nope"])


def test_only_restricts_what_runs(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)
    simple_extractor(ext, "b", "b", ("pom.xml",), body=EDGE_BODY)
    report = run_build(cfg, tmp_path / "out", ext, only=["a"])
    assert [e["name"] for e in report["extractors"]] == ["a"]


def test_components_restricts_what_is_built(tmp_path: Path):
    cfg = make_workspace(tmp_path, {"one": ["pom.xml"], "two": ["pom.xml"]})
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)
    report = run_build(cfg, tmp_path / "out", ext, components_filter=["one"])
    assert [c["name"] for c in report["components"]] == ["one"]


# ── the output location is a refusal, and an ALLOWLIST ────────────────────────


@pytest.mark.parametrize("bad", ["", "docs", "docs/m0", "graph/snapshot", "tools/graph"])
def test_writing_inside_the_repo_but_outside_scratch_is_refused(tmp_path: Path, bad: str):
    repo = tmp_path / "repo"
    (repo / ".tmp").mkdir(parents=True)
    assert B.check_output_dir(repo / bad if bad else repo, repo_root=repo) is not None


@pytest.mark.parametrize("ok", [".tmp", ".tmp/graph", ".tmp/anything/deep"])
def test_writing_under_the_scratch_dir_is_allowed(tmp_path: Path, ok: str):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    assert B.check_output_dir(repo / ok, repo_root=repo) is None


def test_a_path_outside_the_repo_is_not_this_tools_business(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    assert B.check_output_dir(tmp_path / "elsewhere", repo_root=repo) is None


def test_the_refusal_fires_through_main(tmp_path: Path, capsys):
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    rc = B.main(["--workspace", str(cfg), "--out", "docs/m0"])
    assert rc == 2
    assert "refusing to write" in capsys.readouterr().out


def test_the_default_output_is_inside_the_scratch_dir():
    assert B.check_output_dir(Path(".tmp/graph")) is None


# ── the workspace config is the only route in ─────────────────────────────────


def test_a_broken_workspace_config_surfaces_as_a_build_error(tmp_path: Path, capsys):
    cfg = tmp_path / "ws.toml"
    cfg.write_text('schema_version = 1\nroot = "does-not-exist"\n', encoding="utf-8")
    with pytest.raises(B.BuildError):
        run_build(cfg, tmp_path / "out", None)
    assert B.main(["--workspace", str(cfg), "--out", str(tmp_path / "out")]) == 2
    assert "!!" in capsys.readouterr().out


# ── zero-config: the repository you are standing in (item 77) ─────────────────
#
# `test_main_requires_a_workspace` used to live here and pinned `B.main([])` raising
# SystemExit. Item 77 reverses that, so the test is RETARGETED rather than deleted: the
# claim worth keeping was never "a bare invocation fails" but **"a bare invocation never
# silently guesses"** — outside a repository it says so and names the directory, and inside
# one it builds exactly that repository. Same property, different mechanism.


def make_repo(parent: Path, name: str, files: tuple[str, ...] = ("pom.xml",)) -> Path:
    repo = parent / name
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    for rel in files:
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text("x\n", encoding="utf-8")
    return repo


def test_a_bare_invocation_outside_any_repository_is_a_hard_error(tmp_path: Path, capsys):
    rc = B.main(["--target", str(tmp_path), "--out", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert rc == 2
    assert "not inside a git repository" in out and str(tmp_path.resolve()) in out


def test_a_bare_invocation_inside_a_repository_builds_that_repository(tmp_path: Path, capsys):
    repo = make_repo(tmp_path / "ws", "orders")
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)
    report = B.build(None, tmp_path / "out", target=repo, extractor_root=ext,
                     run_id="testrun0001")
    assert [c["name"] for c in report["components"]] == ["orders"]
    assert report["workspace"]["inferred_from"] == str(repo.resolve())
    assert report["workspace"]["config"] == ""


def test_the_report_says_a_workspace_was_inferred_rather_than_read(tmp_path: Path, capsys):
    """How a graph came to describe what it describes is part of the record. A build that
    inferred its workspace must not print indistinguishably from one that was told."""
    repo = make_repo(tmp_path / "ws", "orders")
    ext = tmp_path / "ext"
    simple_extractor(ext, "a", "a", ("pom.xml",), body=EDGE_BODY)
    B._print_report(B.build(None, tmp_path / "out", target=repo, extractor_root=ext))
    printed = capsys.readouterr().out
    assert "inferred" in printed and str(repo.resolve()) in printed


def test_target_and_workspace_cannot_be_combined(tmp_path: Path, capsys):
    """Refused, not ignored. Silently dropping --target means the thing the developer asked
    for did not happen and nothing says so — the failure this whole file is built around."""
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    rc = B.main(["--workspace", str(cfg), "--target", str(tmp_path), "--out",
                 str(tmp_path / "out")])
    assert rc == 2
    assert "cannot be combined" in capsys.readouterr().out


# ── where a repository's own graph may be written (Rev 12) ────────────────────


def test_a_repository_may_carry_its_own_graph(tmp_path: Path):
    """The Rev 12 rule: a repository may carry its own facts and nothing else. Its own
    `.sdlc/` is the committed home for them, so it is allowed where any other in-repo path
    would be refused."""
    repo = make_repo(tmp_path / "ws", "orders")
    assert B.check_output_dir(repo / ".sdlc" / "graph", own_repo=repo) is None


def test_writing_outside_sdlc_in_its_own_repository_is_refused(tmp_path: Path):
    repo = make_repo(tmp_path / "ws", "orders")
    refusal = B.check_output_dir(repo / "graphdump", own_repo=repo)
    assert refusal is not None and ".sdlc" in refusal


def test_a_graph_may_not_be_written_into_a_different_repository(tmp_path: Path):
    """The half the old rule could not see. It declared everything outside this repo the
    developer's own business, which stopped being true once output was written into
    repositories at all: a workspace graph committed into one component carries every
    sibling's names into a tree that may not be as private as the workspace is."""
    subject = make_repo(tmp_path / "ws", "orders")
    other = make_repo(tmp_path / "ws", "billing")
    refusal = B.check_output_dir(other / ".sdlc" / "graph", own_repo=subject)
    assert refusal is not None and "is not about" in refusal


def test_a_workspace_graph_may_not_be_written_into_any_repository(tmp_path: Path):
    """`own_repo` is None for a graph spanning several components — it is about no single
    repository, so no repository may carry it."""
    other = make_repo(tmp_path / "ws", "billing")
    assert B.check_output_dir(other / ".sdlc" / "graph") is not None


def test_the_zero_config_default_output_is_the_repositorys_own_sdlc(tmp_path: Path, capsys):
    """Rev 12's destination, and the reason item 77 needed one: a build with no config file
    still has to write somewhere, and that somewhere is the repository's own committed
    artifact directory rather than a scratch dir the developer would have to go find.

    Runs the REAL extractor set, so this also proves the whole pipeline reaches the end
    zero-config rather than only that the argument parsing does.
    """
    repo = make_repo(tmp_path / "ws", "orders")
    B.main(["--target", str(repo)])
    assert str(repo / ".sdlc" / "graph") in capsys.readouterr().out


def test_a_config_file_still_defaults_to_the_scratch_dir(tmp_path: Path, capsys):
    """Unchanged by item 77: a workspace graph is about no single repository, so it keeps
    writing to the gitignored scratch dir rather than into any component."""
    cfg = make_workspace(tmp_path, {"svc": ["pom.xml"]})
    B.main(["--workspace", str(cfg)])
    assert str(Path(B.SCRATCH_DIR) / "graph") in capsys.readouterr().out
