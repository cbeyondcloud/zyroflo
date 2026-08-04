"""Functional tests for the core lib — the pieces every extractor depends on.

These target the failure modes that would silently corrupt a graph rather than crash it:
identity collisions, lost line numbers, dropped placeholders, and non-deterministic output.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.graph.core.lib import ids, props, emit, manifest, repo_walk, srcfacts
from tools.graph.core.model import Edge, ExtractResult, Node, Provenance


# ── ids ────────────────────────────────────────────────────────────────────────
def test_path_variables_normalize_to_one_identity():
    """`{id}` and `{userId}` name the same endpoint; different names must not fork it."""
    assert ids.endpoint_id("/v1/user/{id}") == ids.endpoint_id("/v1/user/{userId}")


def test_path_normalization_rules():
    assert ids.norm_path("v1//user/") == "/v1/user"
    assert ids.norm_path("") == "/"
    assert ids.norm_path("/") == "/"


def test_join_path_applies_context_prefix():
    assert ids.join_path("/svc-name", "/v2/request/_create") == "/svc-name/v2/request/_create"
    assert ids.join_path("svc", "", "x") == "/svc/x"


def test_relaxed_binding_collapses_key_spellings():
    """The join between a property read in code and a value set in a chart depends on this."""
    forms = ["some.service.host", "SOME_SERVICE_HOST", "some-service-host"]
    assert len({ids.relaxed_key(f) for f in forms}) == 1


def test_service_identity_includes_owning_repo():
    """Same logical name in two repos = two variants, not one merged node."""
    assert ids.service_id("svc-a", "repo1") != ids.service_id("svc-a", "repo2")


# ── props ──────────────────────────────────────────────────────────────────────
def test_properties_parse_preserves_line_numbers():
    text = "# comment\n\na.b=1\nc.d : two\n"
    pairs = props.parse_properties(text)
    assert pairs == [("a.b", "1", 3), ("c.d", "two", 4)]


def test_properties_line_continuation():
    pairs = props.parse_properties("key=one\\\n-two\n")
    assert pairs[0][0] == "key" and pairs[0][2] == 1
    assert "two" in pairs[0][1]


def test_interpolation_uses_default_and_reports_missing():
    resolved = {"known.key": "resolved-value"}
    val, missing = props.interpolate("${KNOWN_KEY}/x", resolved)
    assert val == "resolved-value/x" and missing == []

    val, missing = props.interpolate("${ABSENT:fallback}", resolved)
    assert val == "fallback" and missing == []

    val, missing = props.interpolate("${ABSENT}", resolved)
    assert missing == ["ABSENT"], "an unresolvable placeholder must be reported, not hidden"


def test_malformed_yaml_returns_none_not_raises(tmp_path: Path):
    """One broken config must not abort a whole-estate build."""
    bad = tmp_path / "bad.yml"
    bad.write_text("a: [unclosed\n", encoding="utf-8")
    assert props.load_yaml(bad) is None


def test_walk_yaml_flattens_to_dotted_paths():
    doc = {"a": {"b": [{"c": 1}]}}
    assert dict(props.walk_yaml(doc))["a.b[0].c"] == 1


# ── model ──────────────────────────────────────────────────────────────────────
def test_edge_requires_valid_provenance_fields():
    with pytest.raises(ValueError):
        Edge(src="a", type="CALLS", dst="b", source_file="f", line=1, extractor="x", confidence=1.5)
    with pytest.raises(ValueError):
        Edge(src="a", type="CALLS", dst="b", source_file="", line=1, extractor="x")


def test_merge_key_keeps_two_declaration_sites_distinct():
    """Two files asserting the same coupling stay two rows — humans need both sites."""
    a = Edge(src="s", type="CALLS", dst="d", source_file="f1", line=1, extractor="x")
    b = Edge(src="s", type="CALLS", dst="d", source_file="f2", line=9, extractor="x")
    assert a.merge_key != b.merge_key


# ── emit ───────────────────────────────────────────────────────────────────────
def test_jsonl_roundtrip_and_determinism(tmp_path: Path):
    result = ExtractResult(
        nodes=[Node(id="svc:b@r", type="svc"), Node(id="svc:a@r", type="svc")],
        edges=[Edge(src="svc:a@r", type="CALLS", dst="svc:b@r", source_file="f.java",
                    line=12, extractor="t", confidence=0.9, provenance=Provenance.INFERRED)],
    )
    p1, p2 = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    emit.write_jsonl(p1, result, extractor="t")
    emit.write_jsonl(p2, ExtractResult(nodes=list(reversed(result.nodes)), edges=result.edges), extractor="t")
    assert p1.read_text(encoding="utf-8") == p2.read_text(encoding="utf-8"), \
        "input order must not change output — the snapshot is diffed in PRs"

    back = emit.read_jsonl(p1)
    assert {n.id for n in back.nodes} == {"svc:a@r", "svc:b@r"}
    assert back.edges[0].provenance is Provenance.INFERRED
    assert back.edges[0].line == 12


# ── manifest ───────────────────────────────────────────────────────────────────
def test_manifest_detects_content_change(tmp_path: Path):
    f = tmp_path / "x.properties"
    f.write_text("a=1", encoding="utf-8")
    before = manifest.Manifest()
    before.record_files("repo", [f], tmp_path)

    f.write_text("a=2", encoding="utf-8")
    after = manifest.Manifest()
    after.record_files("repo", [f], tmp_path)
    assert after.changed_since(before) == {"repo/x.properties"}


def test_manifest_flags_moved_head_as_stale():
    m = manifest.Manifest(repos={"r": manifest.RepoStamp(alias="r", head="aaa")})
    assert m.stale_repos({"r": "bbb"}) == ["r"]
    assert m.stale_repos({"r": "aaa"}) == []


def test_manifest_roundtrip(tmp_path: Path):
    m = manifest.Manifest(run_id="r1", repos={"a": manifest.RepoStamp(alias="a", head="h", dirty=True)})
    p = tmp_path / "m.json"
    m.save(p)
    assert manifest.Manifest.load(p).repos["a"].dirty is True


# ── repo_walk ──────────────────────────────────────────────────────────────────
def test_find_files_matches_and_excludes(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "src" / "app.properties").write_text("k=v", encoding="utf-8")
    (tmp_path / "node_modules" / "junk.properties").write_text("k=v", encoding="utf-8")

    found = repo_walk.find_files(tmp_path, ["*.properties"])
    names = {p.name for p in found}
    assert "app.properties" in names
    assert "junk.properties" not in names, "excluded directories must not be walked"


def test_search_reports_exact_line(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("one\ntwo\nNEEDLE here\n", encoding="utf-8")
    hits = repo_walk.search(tmp_path, "NEEDLE", globs=["*.txt"])
    assert [h.line for h in hits] == [3]


def test_python_fallback_matches_accelerated_path(tmp_path: Path):
    """The vendored binaries may be absent; both paths must produce the same graph."""
    (tmp_path / "a.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("beta\n", encoding="utf-8")
    fast = repo_walk.search(tmp_path, "beta", globs=["*.txt"])
    slow = repo_walk.search(tmp_path, "beta", globs=["*.txt"], use_rg=False)
    assert {(h.path.name, h.line) for h in fast} == {(h.path.name, h.line) for h in slow}


@pytest.mark.parametrize(
    "patterns",
    [
        ["*.yml"],                       # single pattern
        ["*.yml", "*.yaml"],             # multiple: the case that silently returned nothing
        ["pom.xml", "package.json", "pubspec.yaml"],
        ["Dockerfile*"],
    ],
    ids=["single", "two-globs", "three-names", "prefix"],
)
def test_find_files_fd_and_python_agree(tmp_path: Path, patterns):
    """Accelerated and fallback discovery must return identical sets, for any pattern count.

    Regression: `fd --glob a --glob b` reads `b` as a search *path*, so multi-pattern lookups
    returned an empty list and the caller could not tell "nothing there" from "query broken".
    An estate report built on that was confidently wrong — hence parity is asserted per
    pattern shape, not just once.
    """
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("on: push", encoding="utf-8")
    (tmp_path / ".github" / "workflows" / "release.yaml").write_text("on: tag", encoding="utf-8")
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "Dockerfile").write_text("FROM x", encoding="utf-8")

    fast = {p.name for p in repo_walk.find_files(tmp_path, patterns)}
    slow = {p.name for p in repo_walk.find_files(tmp_path, patterns, use_fd=False)}
    assert fast == slow, f"fd/python disagree for {patterns}: fd={fast} python={slow}"


def test_hidden_directories_are_searched(tmp_path: Path):
    """`.github` is hidden but load-bearing — excluding `.git` must not exclude it."""
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("on: push", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config.yml").write_text("x", encoding="utf-8")

    found = {p.name for p in repo_walk.find_files(tmp_path, ["*.yml", "*.yaml"])}
    assert "ci.yml" in found, ".github must be discoverable"
    assert "config.yml" not in found, ".git internals must stay excluded"


def test_decoding_is_tolerant(tmp_path: Path):
    f = tmp_path / "cp1252.txt"
    f.write_bytes(b"caf\xe9 = value\n")
    assert "value" in repo_walk.read_text(f), "a mis-encoded file must not fail the build"


# ── srcfacts ───────────────────────────────────────────────────────────────────
@pytest.mark.skipif(not srcfacts.available("java"), reason="java grammar unavailable")
def test_parses_multiline_annotation_and_text_block():
    """The regex-miss class: a value split across lines, and a text block literal."""
    src = '''
class C {
    @Value(
        "${some.service.host}"
    )
    private String host;

    String q = """
        SELECT 1
        """;
}
'''
    sf = srcfacts.parse("java", src)
    literals = [c.text for c in sf.string_literals()]
    assert any("some.service.host" in t for t in literals)
    assert any("SELECT 1" in t for t in literals)


@pytest.mark.skipif(not srcfacts.available("java"), reason="java grammar unavailable")
def test_nested_class_members_are_visible():
    sf = srcfacts.parse("java", "class Outer { static class Inner { String u = \"/v1/x\"; } }")
    assert any("/v1/x" in c.text for c in sf.string_literals())


@pytest.mark.skipif(not srcfacts.available("python"), reason="python grammar unavailable")
def test_same_machinery_serves_a_second_language():
    """Core stays language-neutral — this is what the M1 second-estate profile relies on."""
    sf = srcfacts.parse("python", 'URL = "http://svc:8080/v1/x"\n')
    assert any("/v1/x" in c.text for c in sf.string_literals())


def test_unknown_language_degrades_explicitly():
    assert srcfacts.available("not-a-language") is False
    with pytest.raises(srcfacts.LanguageUnavailable):
        srcfacts.parse("not-a-language", "x")
