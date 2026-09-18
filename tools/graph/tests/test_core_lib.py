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
def test_a_files_content_decides_whether_it_changed(tmp_path: Path):
    """Content decides, which is item 12's rule and the reason digests exist.

    Asserted on `file_digest` itself now that `Manifest` carries no per-file
    map: the map was read by nothing, and `build.input_digests` folds these
    digests per extractor for the freshness check that actually runs.
    """
    f = tmp_path / "x.properties"
    f.write_text("a=1", encoding="utf-8")
    first = manifest.file_digest(f)

    f.write_text("a=2", encoding="utf-8")
    assert manifest.file_digest(f) != first

    f.write_text("a=1", encoding="utf-8")
    assert manifest.file_digest(f) == first, "same content, same digest"


def test_manifest_flags_moved_head_as_stale():
    m = manifest.Manifest(repos={"r": manifest.RepoStamp(alias="r", head="aaa")})
    assert m.stale_repos({"r": "bbb"}) == ["r"]
    assert m.stale_repos({"r": "aaa"}) == []


def test_manifest_roundtrip(tmp_path: Path):
    m = manifest.Manifest(run_id="r1", repos={"a": manifest.RepoStamp(alias="a", head="h", dirty=True)})
    p = tmp_path / "m.json"
    m.save(p)
    assert manifest.Manifest.load(p).repos["a"].dirty is True


def test_a_manifest_written_before_the_run_id_was_dropped_still_loads(tmp_path: Path):
    """`.sdlc/graph/` is committed, so every adopter already HAS a manifest carrying
    `run_id`; upgrading the harness must not make reading their graph raise. Dropped on
    load exactly as `files` is — a manifest read back from disk cannot know which run
    wrote it, and refusing it would make the fix the outage."""
    p = tmp_path / "old.json"
    p.write_text(json.dumps({
        "schema_version": 1,
        "run_id": "5f5f7143069c",
        "extractor_version": "v1",
        "repos": {"a": {"alias": "a", "head": "h", "dirty": True, "branch": ""}},
        "counts": {"e": 3},
    }, indent=2), encoding="utf-8")

    m = manifest.Manifest.load(p)
    assert m.repos["a"].head == "h"
    assert m.counts == {"e": 3}
    assert m.run_id == "", "a manifest read back from disk carries no run id"


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


@pytest.mark.parametrize("root", [".", "pkg"], ids=["dot", "subdir"])
def test_a_relative_root_finds_the_same_files_as_the_fallback(tmp_path: Path, monkeypatch,
                                                              root: str):
    """REGRESSION, and the third instance of this module's own cardinal failure.

    fd is asked for `--absolute-path`, so `p.relative_to(root)` against a RELATIVE root raised
    `ValueError` for every line and a per-line `except: continue` swallowed each one — the
    accelerator returned **zero files at exit 0**, meaning "this tree has nothing", while the
    fallback returned everything.

    **Every test in this file used `tmp_path`, which is absolute, which is exactly why it
    survived.** It was found by the POSIX gate run comparing the backends deliberately, and it
    reproduces identically on Windows: it was never a POSIX defect, only a POSIX-run finding.
    """
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "a.yml").write_text("x", encoding="utf-8")
    (tmp_path / "b.yml").write_text("y", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    fast = repo_walk.find_files(Path(root), ["*.yml"])
    slow = repo_walk.find_files(Path(root), ["*.yml"], use_fd=False)
    assert fast, "the accelerator returned nothing, which is the defect this pins"
    assert sorted(map(str, fast)) == sorted(map(str, slow))


def test_both_backends_answer_in_the_callers_frame_of_reference(tmp_path: Path, monkeypatch):
    """Agreeing on WHICH files while disagreeing on how to spell them is not parity — it
    moves the discrepancy somewhere harder to see. A relative root yields relative paths."""
    (tmp_path / "a.yml").write_text("x", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert [str(p) for p in repo_walk.find_files(Path("."), ["*.yml"])] == ["a.yml"]
    assert [p.is_absolute() for p in repo_walk.find_files(tmp_path, ["*.yml"])] == [True]


def test_a_path_that_cannot_be_placed_under_the_root_abandons_the_accelerator(
        tmp_path: Path, monkeypatch):
    """An assumption breaking must hand over to the fallback, never report "nothing".

    Every line fd emits is under the search path it was given, so one that is not means
    something here is wrong — and the honest answer to a broken assumption is the same one
    the returncode guard already gives, not a silently shorter list.
    """
    (tmp_path / "a.yml").write_text("x", encoding="utf-8")

    class FakeProc:
        returncode = 0
        stdout = "/somewhere/entirely/else/b.yml\n"

    monkeypatch.setattr(repo_walk.subprocess, "run", lambda *a, **k: FakeProc())
    assert repo_walk._find_with_fd("fd", tmp_path, ["*.yml"],
                                   repo_walk.DEFAULT_EXCLUDES) is None
    assert [p.name for p in repo_walk.find_files(tmp_path, ["*.yml"])] == ["a.yml"], \
        "find_files must fall through to the Python backend and still answer correctly"


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


@pytest.mark.skipif(not srcfacts.available("python"), reason="grammar unavailable")
def test_query_failure_is_recorded_not_silently_dropped():
    """A broken query must record the loss so callers can surface it.

    Regression: query() returned [] for an uncompilable query and promised the loss
    would appear in the validation report, but nothing was recorded -- the caller got
    [] and could not tell 'nothing matched' from 'query broken'.
    """
    sf = srcfacts.parse("python", "x = 1\n")
    broken = "(this_is_not_valid_syntax @@@"
    result = sf.query(broken)
    assert result == [], "a broken query still returns [] for graceful degradation"
    assert hasattr(sf, "query_failures"), (
        "query() must record the failure on the instance so callers can tell "
        "'nothing matched' from 'query broken' -- zero silent drops"
    )
    assert len(sf.query_failures) == 1, (
        "exactly one failure expected after one broken query"
    )
    recorded = sf.query_failures[0]
    assert broken in recorded, (
        "the recorded failure must name the query that broke"
    )
    # The diagnosis (exception type and message) is the actionable half -- without it
    # a caller knows WHICH query failed but not WHY, which is not enough to fix it.
    assert "Error" in recorded or "Exception" in recorded, (
        "the recorded failure must carry the exception type so a reader can act on it"
    )
