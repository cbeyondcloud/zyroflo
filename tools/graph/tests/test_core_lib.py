"""Functional tests for the core lib — the pieces every extractor depends on.

These target the failure modes that would silently corrupt a graph rather than crash it:
identity collisions, lost line numbers, dropped placeholders, and non-deterministic output.
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.graph.core.lib import ids, props, emit, manifest, repo_walk, srcfacts
from tools.graph.core.model import Edge, ExtractResult, Node, Provenance
from tools.graph.tests._listing import allow_listing, deny_listing, lists


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


def test_properties_parse_reads_every_separator_the_format_allows():
    """T538: the format ends a key at its first `=`, `:` or whitespace, and a line with no
    separator declares a key with an empty value. Only `=` and `:` were read, so
    `server.port 8080` and a bare key vanished with no row."""
    text = "a=1\nb:2\nc 3\nd   =  4\ne\nf:\ng with=equals\n"
    assert props.parse_properties(text) == [
        ("a", "1", 1), ("b", "2", 2), ("c", "3", 3), ("d", "4", 4),
        ("e", "", 5), ("f", "", 6), ("g", "with=equals", 7)]


def test_a_properties_key_keeps_the_separators_it_escapes():
    """B8-4: a key runs to the first separator NO BACKSLASH ESCAPES, and its escapes are
    undone - `java.util.Properties`' rule. Cut at the first space, T538's whitespace
    separator turned `key\\ with\\ spaces=v` into a key nobody wrote (`key\\`) holding
    the rest as its value. A line with an empty key declares nothing to look up."""
    b = chr(92)
    text = (f"key{b} with{b} spaces=v\n"      # escaped spaces are the key's own
            f"a{b}=b=c\n"                      # an escaped `=`, then the separator
            f"x{b}:y:z\n"                      # an escaped `:`, likewise
            f"u{b}u0041=1\n"                   # a code point
            f"bs{b}{b}=2\n"                    # an escaped backslash ends nothing
            "=empty\n")
    assert props.parse_properties(text) == [
        ("key with spaces", "v", 1), ("a=b", "c", 2), ("x:y", "z", 3), ("uA", "1", 4),
        (f"bs{b}", "2", 5)]


def test_a_continuation_line_loses_its_leading_whitespace():
    """T577: `java.util.Properties` drops the white space that starts each continuation
    line, and this parser kept it, so a continued list entered the graph padded (`a,    b`)
    and - since T538 reads white space as a separator - a key continued across lines split
    at the joint: `ke`, holding `y=v`."""
    text = "list=a,\\\n    b,\\\n\tc\nke\\\n   y=v\n"
    assert props.parse_properties(text) == [("list", "a,b,c", 1), ("key", "v", 4)]


def test_a_properties_line_ends_only_where_java_ends_one():
    """T607 (review B14-2): the body was split by `str.splitlines()`, which also ends a line
    at a form feed, a vertical tab, NEL and the Unicode line separator, where
    `java.util.Properties` ends one only at a line feed, a carriage return or the two
    together. So a form feed minted a key nobody wrote, every later line was cited one too
    far, and T577's continuation strip never saw the form feed it names."""
    b, ff, vt, nel, ls = chr(92), chr(12), chr(11), chr(0x85), chr(0x2028)
    text = (f"k=a{ff}b\n"                  # a form feed inside a value is content,
            f"list=x,{b}\n{ff}y\n"          # and white space where a continuation starts
            f"v=1{vt}2{nel}3{ls}4\n"        # none of these ends a line either
            "cr=5\rcrlf=6\r\nlf=7\n")       # Java's three terminators do
    assert props.parse_properties(text) == [
        ("k", f"a{ff}b", 1), ("list", "x,y", 2), ("v", f"1{vt}2{nel}3{ls}4", 4),
        ("cr", "5", 5), ("crlf", "6", 6), ("lf", "7", 7)]


def test_a_properties_line_reads_white_space_as_java_does():
    """T610: `java.util.Properties` reads three characters as white space - space, tab and
    form feed - where `\\s` read every Unicode space as one. A key was cut at a no-break
    space, VT, NEL, U+2028 or U+3000; a value lost the trailing white space Java keeps, and
    a leading no-break space Java does not skip; a line led by a no-break space read as
    blank or as a comment; and a backslash before trailing white space continued the line,
    where Java counts the backslashes a line ends with. Each case is read as Java 22's
    `Properties.load` read it (batch 18's oracle, `PropsOracle.java`)."""
    b, nbsp, vt, nel = chr(92), chr(0xA0), chr(0x0B), chr(0x85)
    ls, ideo = chr(0x2028), chr(0x3000)
    java = [
        (f"a{nbsp}b=1", [(f"a{nbsp}b", "1", 1)]),
        (f"a{vt}b=1", [(f"a{vt}b", "1", 1)]),
        (f"a{nel}b=1", [(f"a{nel}b", "1", 1)]),
        (f"a{ls}b=1", [(f"a{ls}b", "1", 1)]),
        (f"a{ideo}b=1", [(f"a{ideo}b", "1", 1)]),
        (f"k=v{nbsp}", [("k", f"v{nbsp}", 1)]),
        ("k=v ", [("k", "v ", 1)]),
        ("k=v\t", [("k", "v\t", 1)]),
        (f"k={nbsp}v", [("k", f"{nbsp}v", 1)]),
        (f"{nbsp}# x", [(f"{nbsp}#", "x", 1)]),
        (nbsp, [(nbsp, "", 1)]),
    ]
    for text, read in java:
        assert props.parse_properties(text + "\n") == read, repr(text)
    # A backslash before trailing white space continues nothing. Java then undoes the escape
    # the value ends with (`\ ` reads ` `); a value's escapes are T644's (Deferred), so here
    # the value keeps it as written - the line structure is this row's.
    for tail in (" ", nbsp):
        assert props.parse_properties(f"k=a{b}{tail}\nb=2\n") == [
            ("k", f"a{b}{tail}", 1), ("b", "2", 2)], repr(tail)


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


def test_read_text_reporting_tells_unreadable_from_empty(tmp_path: Path):
    """could-not-read must be its OWN answer, not folded into "empty file".

    Two read helpers used to disagree about the same failure: repo_walk.read_text swallowed an
    OSError into "" (an unreadable file and a blank one became one value, and nothing reached
    unresolved), while props.read_text raised and aborted the build. The honest shape is ONE
    helper with a reporting spelling, read_text_reporting(path) -> (text, error) with exactly
    one meaningful, so a caller can emit an unresolved row for the unreadable case the way
    load_yaml_all_reporting already does for a malformed one. The collapsing read_text stays for
    callers that genuinely cannot act on the distinction; one implementation underneath, so the
    two can never disagree again.
    """
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    unreadable = tmp_path / "adir"            # a directory: open() raises OSError on both OSes
    unreadable.mkdir()

    # One helper: props re-exports repo_walk's reader, it is not a second copy with its own bugs.
    assert props.read_text is repo_walk.read_text

    assert repo_walk.read_text_reporting(empty) == ("", ""), "a blank file has no error"
    text, error = repo_walk.read_text_reporting(unreadable)
    assert text == "" and error, "an unreadable path must NAME its failure, not read as blank"

    # The collapsing spelling folds BOTH to "" (props no longer raises and aborts the build); the
    # distinction lives in read_text_reporting, which is what a recording caller must reach for.
    assert props.read_text(empty) == props.read_text(unreadable) == ""


def test_read_text_reporting_reads_past_a_byte_order_mark(tmp_path: Path):
    """T532: Windows editors write one by default, and it stayed glued to the first key -
    so a context path declared on line 1 read as undeclared. Stripped here, once, for every
    reader; a mark anywhere but the start is content and stays."""
    marked = tmp_path / "a.properties"
    marked.write_bytes(bytes([0xEF, 0xBB, 0xBF]) + b"k=v\n")
    assert repo_walk.read_text_reporting(marked) == ("k=v\n", "")
    plain = tmp_path / "b.properties"
    plain.write_bytes(b"k=v\n")
    assert repo_walk.read_text_reporting(plain) == ("k=v\n", "")
    inner = tmp_path / "c.properties"
    inner.write_bytes(b"k=v\n" + bytes([0xEF, 0xBB, 0xBF]) + b"x=y\n")
    assert chr(0xFEFF) in repo_walk.read_text_reporting(inner)[0]


def test_the_reporting_yaml_loaders_name_a_read_failure_instead_of_collapsing_it(tmp_path: Path):
    """A file that could not be OPENED must reach the caller as an error, not as empty content.

    The reporting loaders read through repo_walk, which folds an OSError to "". yaml.safe_load("")
    is None and safe_load_all("") is [] -- the SAME values an empty document yields -- so a file
    that could not be read collapsed into "opened and found nothing", which is exactly the defect
    load_yaml_all_reporting's own docstring says once cost a whole extractor (item 76), one level
    up. read_text_reporting names the failure and both reporting loaders now pass it on, while an
    empty file stays a no-error result so the two remain distinguishable.
    """
    unreadable = tmp_path / "adir.yml"
    unreadable.mkdir()                       # a directory: open() raises OSError on both OSes
    empty = tmp_path / "empty.yml"
    empty.write_text("", encoding="utf-8")

    doc, error = props.load_yaml_reporting(unreadable)
    assert doc is None and error, "an unreadable file must reach the caller as an error"
    docs, error2 = props.load_yaml_all_reporting(unreadable)
    assert docs == [] and error2, "an unreadable multi-doc file must reach the caller as an error"

    assert props.load_yaml_reporting(empty) == (None, "")
    assert props.load_yaml_all_reporting(empty) == ([], ""), "empty is still no-error, not unreadable"


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


def test_read_jsonl_refuses_a_schema_version_it_does_not_know(tmp_path: Path):
    """T111 / W11 A2-4: the header's `schema_version` was written by every shard and read
    by nothing — a shard from a schema this build cannot interpret parsed anyway, one
    field at a time, until something downstream broke on a shape nobody checked for. The
    version chosen (99999) is deliberately GREATER than current, not just different, so a
    weaker comparison (e.g. `<` instead of `!=`) cannot pass this by accident."""
    p = tmp_path / "future.jsonl"
    p.write_text(
        json.dumps({"kind": "header", "schema_version": 99999, "extractor": "t"}) + "\n"
        + json.dumps({"kind": "node", "id": "svc:a", "type": "svc", "props": {}}) + "\n",
        encoding="utf-8")
    with pytest.raises(emit.ShardSchemaError) as exc:
        emit.read_jsonl(p)
    message = str(exc.value)
    assert "99999" in message, f"the refusal must name the version it found: {message}"
    assert str(emit.SCHEMA_VERSION) in message, f"and the version this build knows: {message}"


def test_read_jsonl_refuses_a_header_with_no_schema_version_key_at_all(tmp_path: Path):
    """Round 2 (leader-flagged): absent and explicit-null are NOT the same claim, although
    a plain `.get(..., None)` would read them identically. A header that never mentions
    `schema_version` has not declared a version of `None` — it has declared nothing — so
    the message must say so rather than print `schema_version=None`, which would be true
    only of the OTHER case below."""
    p = tmp_path / "no_version.jsonl"
    p.write_text(json.dumps({"kind": "header", "extractor": "t"}) + "\n", encoding="utf-8")
    with pytest.raises(emit.ShardSchemaError) as exc:
        emit.read_jsonl(p)
    message = str(exc.value)
    assert "no schema_version at all" in message, f"must say NOTHING was declared: {message}"
    assert "None" not in message, f"absent must not be worded as though None was declared: {message}"


def test_read_jsonl_refuses_a_header_with_schema_version_explicitly_null(tmp_path: Path):
    """The mirror of the test above: `"schema_version": null` DID declare a value, and it
    is `None` — so this message may name it, and must differ from the absent-key wording."""
    p = tmp_path / "null_version.jsonl"
    p.write_text(
        json.dumps({"kind": "header", "schema_version": None, "extractor": "t"}) + "\n",
        encoding="utf-8")
    with pytest.raises(emit.ShardSchemaError) as exc:
        emit.read_jsonl(p)
    message = str(exc.value)
    assert "schema_version=None" in message, f"null WAS declared, as None: {message}"
    assert "no schema_version at all" not in message, (
        f"a null value is not the same claim as no key at all: {message}")


def test_read_jsonl_refuses_a_shard_with_no_header_record_at_all(tmp_path: Path):
    """Round 2 (leader-flagged): the version check sat inside the branch that only runs
    when a header IS present, so a shard that never had one — the case whose schema is
    most obviously unknown — refused nothing and loaded its node rows anyway. Every real
    writer (`write_jsonl`) emits a header unconditionally; this shard is synthetic and
    must be refused rather than partially trusted."""
    p = tmp_path / "headerless.jsonl"
    p.write_text(json.dumps({"kind": "node", "id": "svc:a", "type": "svc", "props": {}}) + "\n",
                 encoding="utf-8")
    with pytest.raises(emit.ShardSchemaError) as exc:
        emit.read_jsonl(p)
    assert "no header" in str(exc.value)


def test_read_jsonl_refuses_a_shard_with_two_header_records(tmp_path: Path):
    """Round 3 DECISION: a duplicate header is malformed and must refuse, not silently let
    the second overwrite the first's declared extractor/schema_version. Symmetric with the
    no-header case above — `write_jsonl` writes exactly one, so a well-formed shard never
    has zero OR two."""
    p = tmp_path / "double_header.jsonl"
    good_header = json.dumps({"kind": "header", "schema_version": emit.SCHEMA_VERSION,
                               "extractor": "t"})
    p.write_text(good_header + "\n" + good_header + "\n"
                 + json.dumps({"kind": "node", "id": "svc:a", "type": "svc", "props": {}}) + "\n",
                 encoding="utf-8")
    with pytest.raises(emit.ShardSchemaError) as exc:
        emit.read_jsonl(p)
    assert "SECOND header" in str(exc.value)


def test_read_jsonl_refuses_an_edge_row_with_an_unexpected_key(tmp_path: Path):
    """Round 4 (leader/reviewer-flagged): an edge row carrying a key the model does not
    accept raises a bare `TypeError` from `Edge.__init__` itself — a DIFFERENT type
    entirely from `RowFieldError` — and used to crash this door with a 17-line traceback
    instead of refusing. Now wrapped into `ShardRowError`, the same as every other way a
    row fails to become a model object."""
    p = tmp_path / "extra_key.jsonl"
    p.write_text(
        json.dumps({"kind": "header", "schema_version": emit.SCHEMA_VERSION, "extractor": "t"}) + "\n"
        + json.dumps({"kind": "edge", "src": "a", "type": "CALLS", "dst": "b",
                      "source_file": "f", "line": 1, "extractor": "x",
                      "provenance": "EXTRACTED", "bogus_extra_key": "surprise"}) + "\n",
        encoding="utf-8")
    with pytest.raises(emit.ShardRowError) as exc:
        emit.read_jsonl(p)
    assert "bogus_extra_key" in str(exc.value), (
        f"the wrapped message must still name the bad key: {exc.value}")


def test_read_jsonl_refuses_a_node_row_with_an_unexpected_key(tmp_path: Path):
    """Round 5 (leader/reviewer-flagged surviving mutant, W455-7): both extra-key tests
    above use EDGE rows, so narrowing the NODE branch's `except (ValueError, TypeError)`
    to `ValueError` alone left the whole suite green — the `TypeError` half of that
    branch, four lines from its tested twin, was unwelded. Mirrors the edge-row test
    exactly, for a node row."""
    p = tmp_path / "extra_key_node.jsonl"
    p.write_text(
        json.dumps({"kind": "header", "schema_version": emit.SCHEMA_VERSION, "extractor": "t"}) + "\n"
        + json.dumps({"kind": "node", "id": "svc:a", "type": "svc", "props": {},
                      "bogus_extra_key": "surprise"}) + "\n",
        encoding="utf-8")
    with pytest.raises(emit.ShardRowError) as exc:
        emit.read_jsonl(p)
    assert "bogus_extra_key" in str(exc.value), (
        f"the wrapped message must still name the bad key: {exc.value}")


def test_read_jsonl_lets_an_unrelated_bug_outside_row_interpretation_traceback(
        tmp_path: Path, monkeypatch):
    """Round 4 (leader/reviewer-flagged), the block this fixes: `snapshot.load` used to
    catch bare `ValueError`, and a reviewer showed why that is dangerous rather than
    merely imprecise — one nonsense line in this reader made EVERY good, untampered shard
    in a workspace print "does not match its own recorded digest" and recommend rebuild
    and re-publish, which would have overwritten seven correct shards over one bug that
    had nothing to do with any of their content.

    A caller can only catch exactly `ShardSchemaError`/`ShardRowError` safely if a bug
    OUTSIDE the narrow row-interpretation wrap still raises as itself. Demonstrated with a
    genuine defect in the header's own version comparison (not a node/edge row at all) —
    it must surface as the plain `RuntimeError` it is, not get folded into either named
    type."""
    class _Bomb:
        def __ne__(self, other):
            raise RuntimeError("not a real shard defect — a bug in the reader itself")

    monkeypatch.setattr(emit, "SCHEMA_VERSION", _Bomb())
    p = tmp_path / "shard.jsonl"
    p.write_text(json.dumps({"kind": "header", "schema_version": 1, "extractor": "t"}) + "\n",
                 encoding="utf-8")
    with pytest.raises(RuntimeError, match="not a real shard defect"):
        emit.read_jsonl(p)


def test_read_jsonl_counts_an_unknown_record_kind_instead_of_dropping_it(tmp_path: Path):
    """The header if/elif had no `else`: a record of any kind but node/edge/header fell
    through in silence and `read_jsonl` reported 0 unresolved regardless. Zero silent
    drops: it must be counted, not merely skipped past — and a valid row elsewhere in the
    same shard must still load, so one bad record does not cost the whole file."""
    p = tmp_path / "shard.jsonl"
    p.write_text(
        json.dumps({"kind": "header", "schema_version": emit.SCHEMA_VERSION, "extractor": "t"}) + "\n"
        + json.dumps({"kind": "widget", "surprise": True}) + "\n"
        + json.dumps({"kind": "node", "id": "svc:a", "type": "svc", "props": {}}) + "\n",
        encoding="utf-8")
    result = emit.read_jsonl(p)
    assert {n.id for n in result.nodes} == {"svc:a"}, "a valid row beside the bad one must still load"
    assert len(result.unresolved) == 1, f"the unknown kind must be counted: {result.unresolved}"
    row = result.unresolved[0]
    assert row["kind"] == "unknown-record-kind"
    assert row["source_file"] == str(p), "must locate the shard the bad row came from"
    assert row["line"] == 2, "must point at the row the reader could not interpret"
    assert "widget" in row["why"], f"the refusal must name the kind it found: {row}"


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


def test_manifest_schema_version_has_one_home_in_model():
    """T111 / W11 A2-4: `core/lib/manifest.py` used to declare its own `SCHEMA_VERSION = 1`,
    independently of `core/model.py`'s constant of the same name and, today, the same
    value — free to diverge the moment either one changes alone.

    Structural, because value equality alone cannot tell 'imported from one home' from
    'two literals that still happen to agree': CPython caches small ints, so even
    `manifest.SCHEMA_VERSION is model.SCHEMA_VERSION` would pass for two independently
    declared `= 1`s and prove nothing about where either one actually lives.
    """
    lines = Path(manifest.__file__).read_text(encoding="utf-8").splitlines()
    assert not any(line.startswith("SCHEMA_VERSION") for line in lines), (
        "manifest.py must not re-declare SCHEMA_VERSION; it must import model's")
    assert any("from ..model import" in line and "SCHEMA_VERSION" in line for line in lines), (
        "manifest.py must import SCHEMA_VERSION from ..model rather than invent its own")

    from tools.graph.core import model
    assert manifest.SCHEMA_VERSION == model.SCHEMA_VERSION


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


def _paths(found, root: Path) -> list[str]:
    """Where each file is, relative to the root it was searched under - the claim
    parity is FOR. T110: both parity tests compared basenames only, so a backend that
    answered the right names in the wrong directories agreed with the fallback."""
    return sorted(p.relative_to(root).as_posix() for p in found)


def test_the_walk_imports_where_stat_has_no_windows_reparse_tags():
    """T592's own regression, which only the Linux gate saw: `stat.IO_REPARSE_TAG_*` exist on
    Windows alone (its C `_stat` module defines them), and read at import they made every
    module importing `repo_walk` fail to collect on Linux - 63 errors. In a fresh interpreter
    with them removed, as on Linux, the walk imports and still knows both tags by value."""
    code = ("import stat\n"
            "for name in ('IO_REPARSE_TAG_SYMLINK', 'IO_REPARSE_TAG_MOUNT_POINT'):\n"
            "    stat.__dict__.pop(name, None)\n"
            "from tools.graph.core.lib import repo_walk\n"
            "print(sorted(hex(tag) for tag in repo_walk._LINK_TAGS))\n")
    done = subprocess.run([sys.executable, "-c", code], cwd=str(Path(__file__).parents[3]),
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "['0xa0000003', '0xa000000c']", done.stdout
    # The fallback values are Windows' own (review B14-8): where `stat` has the names, they
    # hold exactly those values; where it lacks them, that absence is why the fallback exists.
    if os.name == "nt":
        assert sorted(hex(tag) for tag in (stat.IO_REPARSE_TAG_SYMLINK,
                                           stat.IO_REPARSE_TAG_MOUNT_POINT)) == [
            "0xa0000003", "0xa000000c"]
    else:
        assert not hasattr(stat, "IO_REPARSE_TAG_SYMLINK")


@pytest.mark.parametrize("entry, link", [
    (SimpleNamespace(st_mode=stat.S_IFLNK | 0o777), True),
    (SimpleNamespace(st_mode=stat.S_IFLNK | 0o777, st_reparse_tag=0xA000000C), True),
    (SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_reparse_tag=0xA0000003), True),
    (SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_reparse_tag=0x9000001A), False),
    (SimpleNamespace(st_mode=stat.S_IFDIR | 0o777, st_reparse_tag=0), False),
    (SimpleNamespace(st_mode=stat.S_IFREG | 0o666), False),
    (None, False),
], ids=["posix-symlink", "windows-symlink", "junction", "cloud-placeholder", "directory",
        "file", "gone"])
def test_a_link_is_known_by_its_mode_or_its_reparse_tag_on_every_platform(
        monkeypatch, entry, link):
    """Review B14-8: the walk tests below can build a junction on Windows alone, and on
    Linux `os.walk` already leaves a symlink alone, so the Linux gate could not see T592 or
    T608 lose either half of the link test. Here each entry is read as `os.lstat` reports
    it on its own platform - a cloud-files placeholder is a reparse point too, and a real
    directory - so the test answers the same on every platform, and dropping either half,
    or widening it to every reparse point, turns it red there too."""
    real = os.lstat

    def lstat(path):
        if path != "entry":
            return real(path)
        if entry is None:
            raise FileNotFoundError(path)
        return entry

    monkeypatch.setattr(repo_walk.os, "lstat", lstat)
    assert repo_walk._is_link("entry") is link


def test_the_python_walk_does_not_enter_a_directory_link(tmp_path: Path):
    """T592: `os.walk` takes a Windows junction for a directory, so a junction back at the
    component's root sent the fallback round `self/self/self/...` dozens of levels deep,
    where fd does not enter it - one workspace, two graphs, by whether fd is vendored. A
    directory that is a link is not entered. Windows refuses an unprivileged symlink, so a
    junction stands in there; POSIX has no junction, so a symlink does - which `os.walk`
    already left alone. fd's leg runs only where fd is vendored, as the Linux gate fetches
    it, and is inert elsewhere: it never stands in for the fallback's assertion."""
    comp = tmp_path / "orders"
    (comp / "src").mkdir(parents=True)
    (comp / "src" / "a.yml").write_text("x: 1\n", encoding="utf-8")
    link = comp / "self"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(comp), str(link))
    else:
        os.symlink(comp, link, target_is_directory=True)
    fd = repo_walk._tool("fd")
    try:
        found = repo_walk.find_files(comp, ["*.yml"], use_fd=False)
        by_fd = (repo_walk._find_with_fd(fd, comp, ["*.yml"], repo_walk.DEFAULT_EXCLUDES)
                 if fd else None)
    finally:
        os.rmdir(link) if os.name == "nt" else os.unlink(link)   # the link, never walked
    assert _paths(found, comp) == ["src/a.yml"]
    if fd:
        assert by_fd is not None and _paths(by_fd, comp) == ["src/a.yml"]


def test_the_python_walk_skips_a_file_that_is_a_link(tmp_path: Path):
    """T608 (review B14-3): fd, asked for `--type f`, drops a link to a file and a broken
    link, while the Python walk returned either one when its name matched - the backends
    disagreed one level below T592's directory link. A file that is a link is skipped now,
    as a match and as a near miss. Windows refuses an unprivileged symlink and a junction
    is never a file, so there the refusal is all this can see (the test above reads a
    Windows link by its tag on every platform). fd's leg runs only where fd is vendored."""
    comp = tmp_path / "orders"
    comp.mkdir()
    (comp / "a.yml").write_text("x: 1\n", encoding="utf-8")
    try:
        os.symlink(comp / "a.yml", comp / "b.yml")             # a link to a file
        os.symlink(comp / "gone.yml", comp / "broken.yml")     # a link to nothing
        os.symlink(comp / "a.yml", comp / "C.YML")             # one a near miss would name
    except OSError as refused:
        assert os.name == "nt" and getattr(refused, "winerror", None) == 1314, refused
        return
    near: list = []
    found = repo_walk.find_files(comp, ["*.yml"], use_fd=False, near_misses=near)
    assert _paths(found, comp) == ["a.yml"]
    assert near == []
    fd = repo_walk._tool("fd")
    if fd:
        fd_near: list = []
        by_fd = repo_walk._find_with_fd(fd, comp, ["*.yml"], repo_walk.DEFAULT_EXCLUDES,
                                        fd_near)
        assert by_fd is not None and _paths(by_fd, comp) == ["a.yml"] and fd_near == []


def test_a_directory_the_walk_cannot_list_is_named_never_read_as_empty(tmp_path: Path):
    """T611: a directory the walk could enter but not list lost its files from every
    extractor, and nothing said so - this module's own rule, could-not-look read as absence.
    Measured on Windows (batch 18's scoping): the Python walk ignored the listing error, and
    fd skipped the directory at exit 0 with nothing on stderr. Each directory that could not
    be listed is named now, with its cause, and both backends give one answer. Whether the
    refusal binds is measured, as the markers' test does: root lists through the mode bits,
    and the Linux gate runs unprivileged."""
    comp = tmp_path / "orders"
    (comp / "sub").mkdir(parents=True)
    (comp / "top.yml").write_text("a: 1\n", encoding="utf-8")
    (comp / "sub" / "a.yml").write_text("a: 1\n", encoding="utf-8")
    deny_listing(comp / "sub")
    try:
        binds = not lists(comp / "sub")
        answers = {}
        for use_fd in (False, True):
            unlisted: list = []
            found = repo_walk.find_files(comp, ["*.yml"], use_fd=use_fd, unlisted=unlisted)
            answers[use_fd] = (_paths(found, comp),
                               [(p.relative_to(comp).as_posix(), why) for p, why in unlisted])
    finally:
        allow_listing(comp / "sub")
    if binds:
        assert answers[False] == (["top.yml"], [("sub", "PermissionError (EACCES)")])
    else:
        assert answers[False] == (["sub/a.yml", "top.yml"], [])
    assert answers[True] == answers[False], "fd or not, one answer"


def test_fd_reporting_an_error_hands_the_walk_to_python(tmp_path: Path, monkeypatch):
    """T611: fd skips a directory it cannot list at exit 0 - silent by default, and still
    exit 0 under `--show-errors`, which prints the error (measured, batch 18's scoping). So
    fd runs with `--show-errors`, and a line on its stderr abandons the accelerator: the
    Python walk answers, and names the directory. fd's run is stood in for here, so every
    platform sees the rule; the test above runs the real one."""
    (tmp_path / "top.yml").write_text("a: 1\n", encoding="utf-8")
    asked = []

    def fd_run(cmd, **kwargs):
        asked.append(cmd)
        return subprocess.CompletedProcess(
            cmd, 0, stdout=f"{(tmp_path / 'top.yml').resolve()}\n",
            stderr="[fd error]: sub: Access is denied. (os error 5)\n")

    monkeypatch.setattr(repo_walk.subprocess, "run", fd_run)
    assert repo_walk._find_with_fd("fd", tmp_path, ["*.yml"], repo_walk.DEFAULT_EXCLUDES) is None
    assert "--show-errors" in asked[0], asked


def test_fd_answers_a_tree_it_can_read_in_full(tmp_path: Path):
    """T611's other side: `--show-errors` must not hand a readable tree to the slower walk.
    fd's leg runs only where fd is vendored, as the Linux gate fetches it."""
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.yml").write_text("x: 1\n", encoding="utf-8")
    fd = repo_walk._tool("fd")
    if fd:
        by_fd = repo_walk._find_with_fd(fd, tmp_path, ["*.yml"], repo_walk.DEFAULT_EXCLUDES)
        assert by_fd is not None and _paths(by_fd, tmp_path) == ["a/x.yml"]


def test_the_python_walk_skips_an_entry_lstat_says_is_not_a_regular_file(tmp_path: Path,
                                                                         monkeypatch):
    """T612 (review B14-14): fd's `--type f` keeps regular files, and the Python walk
    returned any entry whose name matched - a FIFO, a socket, a device - so the backends
    disagreed, and reading a FIFO with no writer hung the build fd would finish. A candidate
    `lstat` says is not a regular file is not counted, as a match or a near miss (one it
    cannot ask about is: the tests below). Windows cannot make a FIFO, so `lstat` is stood in
    for here, on every platform; a test below makes a real one where it can."""
    comp = tmp_path / "orders"
    comp.mkdir()
    for name in ("a.yml", "pipe.yml", "Fifo.YML"):
        (comp / name).write_text("x: 1\n", encoding="utf-8")
    real = os.lstat

    def lstat(path, *args, **kwargs):
        if os.path.basename(path) in ("pipe.yml", "Fifo.YML"):
            return SimpleNamespace(st_mode=stat.S_IFIFO | 0o644, st_reparse_tag=0)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(repo_walk.os, "lstat", lstat)
    near: list = []
    found = repo_walk.find_files(comp, ["*.yml"], use_fd=False, near_misses=near)
    assert _paths(found, comp) == ["a.yml"]
    assert near == []


def test_a_candidate_the_walk_cannot_ask_about_is_counted_never_dropped(tmp_path: Path,
                                                                        monkeypatch):
    """Batch 18's review round 1 (S1): T612's `_is_regular` folded every `lstat` error to "not
    a regular file", so a candidate the walk could not ask about - an I/O error, a sharing
    violation - left every extractor in silence, where `main` counted it. Could-not-ask is
    never absence: such a candidate counts, as the walk before T612 had it, and its reader
    says whether it can be read; a file gone since the listing does not. `lstat` is stood in
    for, so every platform sees both."""
    comp = tmp_path / "orders"
    comp.mkdir()
    for name in ("asked.yml", "gone.yml"):
        (comp / name).write_text("x: 1\n", encoding="utf-8")
    real = os.lstat

    def lstat(path, *args, **kwargs):
        name = os.path.basename(str(path))
        if name == "asked.yml":
            raise PermissionError(13, "denied", str(path))
        if name == "gone.yml":
            raise FileNotFoundError(2, "gone", str(path))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(repo_walk.os, "lstat", lstat)
    assert _paths(repo_walk.find_files(comp, ["*.yml"], use_fd=False), comp) == ["asked.yml"]


def test_a_candidate_is_asked_as_spelled_before_its_long_form(monkeypatch):
    """Batch 18's review round 2 (B18-6): `_is_regular` asked by the long form first, and
    `long_path` spells a UNC path's long form as Windows refuses it - so a directory past 240
    characters under a UNC root was counted blind, where the plain ask answers. A
    candidate is asked as the walk spelled it, and by its long form only when that cannot
    answer: past MAX_PATH where long paths are off, the long form - of the ABSOLUTE path, since
    a relative spelling can resolve past the limit - reaches it; a file neither finds is gone;
    one neither can ask about still counts. `long_path` gives an absolute path a spelling of
    its own here, and `lstat` answers each, so every platform sees every cell."""
    directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_reparse_tag=0)
    regular = SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_reparse_tag=0)
    refused = OSError(22, "the long form's syntax is refused")
    too_long = FileNotFoundError(2, "the path cannot be found at this length")

    def answers(plain, longer) -> None:
        def lstat(path, *args, **kwargs):
            reply = longer if str(path).startswith("LONG:") else plain
            if isinstance(reply, OSError):
                raise reply
            return reply
        monkeypatch.setattr(repo_walk.os, "lstat", lstat)

    monkeypatch.setattr(repo_walk, "long_path",
                        lambda p: f"LONG:{p}" if os.path.isabs(str(p)) else str(p))
    here = os.path.abspath
    answers(directory, refused)
    assert repo_walk._is_regular(here("deep/dir")) is False  # asked as spelled: answered
    answers(too_long, regular)
    assert repo_walk._is_regular("deep/file.yml") is True     # a relative spelling, too long
    answers(too_long, FileNotFoundError(2, "gone"))
    assert repo_walk._is_regular(here("deep/gone.yml")) is False  # neither finds it: gone
    answers(PermissionError(13, "denied"), refused)
    assert repo_walk._is_regular(here("deep/shared.yml")) is True  # neither can ask: counted


def test_a_fifo_is_counted_by_neither_backend(tmp_path: Path):
    """T612 where a FIFO can be made (the batch-14 review measured it in the CI image).
    Windows cannot make one, which is all this can see there."""
    if not hasattr(os, "mkfifo"):
        assert os.name == "nt"
        return
    comp = tmp_path / "orders"
    comp.mkdir()
    (comp / "a.yml").write_text("x: 1\n", encoding="utf-8")
    os.mkfifo(comp / "pipe.yml")
    assert _paths(repo_walk.find_files(comp, ["*.yml"], use_fd=False), comp) == ["a.yml"]
    fd = repo_walk._tool("fd")
    if fd:
        by_fd = repo_walk._find_with_fd(fd, comp, ["*.yml"], repo_walk.DEFAULT_EXCLUDES)
        assert by_fd is not None and _paths(by_fd, comp) == ["a.yml"]


def _hits(found, root: Path) -> list[tuple[str, int]]:
    return sorted((h.path.relative_to(root).as_posix(), h.line) for h in found)


def test_parity_by_path_sees_a_backend_that_loses_directories(tmp_path: Path, monkeypatch):
    """The positive control, runnable with no vendored binary: an fd that answers the
    right NAME in the wrong directory agrees with the fallback by name and disagrees
    by path - which is why the parity tests above compare paths."""
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text("on: push", encoding="utf-8")

    class LostDirectories:
        returncode = 0
        stdout = str(tmp_path / "ci.yml") + "\n"
        stderr = ""

    monkeypatch.setattr(repo_walk.subprocess, "run", lambda *a, **k: LostDirectories())
    fast = repo_walk._find_with_fd("fd", tmp_path, ["*.yml"], repo_walk.DEFAULT_EXCLUDES)
    slow = repo_walk.find_files(tmp_path, ["*.yml"], use_fd=False)
    assert {p.name for p in fast} == {p.name for p in slow}, "names alone agree"
    assert _paths(fast, tmp_path) != _paths(slow, tmp_path), "paths do not"


def test_python_fallback_matches_accelerated_path(tmp_path: Path):
    """The vendored binaries may be absent; both paths must produce the same graph."""
    (tmp_path / "a.txt").write_text("alpha\nbeta\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("beta\n", encoding="utf-8")
    # T110: the same NAME in a second directory, so agreeing on names alone cannot pass.
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "a.txt").write_text("beta\n", encoding="utf-8")
    fast = repo_walk.search(tmp_path, "beta", globs=["*.txt"])
    slow = repo_walk.search(tmp_path, "beta", globs=["*.txt"], use_rg=False)
    assert _hits(fast, tmp_path) == _hits(slow, tmp_path)
    assert ("sub/a.txt", 1) in _hits(slow, tmp_path)


def _fake_rg(monkeypatch, stream: str) -> None:
    """Stand ripgrep's `--json` stream in for the real process, so the parse is tested on every
    host - the real binary is absent from a fresh checkout (`third_party/bin` is gitignored)."""
    def run(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stream, "")
    monkeypatch.setattr(repo_walk, "subprocess",
                        SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired))


def _rg_record(path: Path, line: int, text: str) -> str:
    return json.dumps({"type": "match", "data": {"path": {"text": str(path)},
                                                 "line_number": line, "lines": {"text": text}}},
                      ensure_ascii=False)


def test_a_ripgrep_match_whose_line_holds_a_line_separator_is_read_whole(tmp_path: Path,
                                                                         monkeypatch):
    """T635: ripgrep's `--json` stream ends a record only at a newline, and a matched line
    holding U+2028, U+2029 or U+0085 arrives raw inside the record's JSON string - its encoder
    escapes only the C0 controls. `str.splitlines()` cut the record at that character, both
    halves failed to decode, and the match was dropped in silence."""
    texts = ["x\u2028NEEDLE", "NEEDLE\u2029y", "a\x85NEEDLE"]
    _fake_rg(monkeypatch, "\n".join(
        [json.dumps({"type": "begin", "data": {}}),
         *(_rg_record(tmp_path / "f.txt", n, text + "\n") for n, text in enumerate(texts, 1)),
         ""]))
    found = repo_walk._search_with_rg("rg", tmp_path, "NEEDLE", [], [], False)
    assert [(m.line, m.text) for m in found] == list(enumerate(texts, 1))


def test_a_ripgrep_match_on_a_crlf_line_drops_the_carriage_return(tmp_path: Path, monkeypatch):
    """ripgrep hands a CRLF file's matched line back with its `\\r\\n`; only the `\\n` was
    stripped, so the match text kept a `\\r` the Python search never shows (T635)."""
    _fake_rg(monkeypatch, _rg_record(tmp_path / "f.txt", 1, "NEEDLE\r\n") + "\n")
    found = repo_walk._search_with_rg("rg", tmp_path, "NEEDLE", [], [], False)
    assert [(m.line, m.text) for m in found] == [(1, "NEEDLE")]


def test_the_python_search_counts_lines_as_ripgrep_does(tmp_path: Path):
    """The fallback split a file with `str.splitlines()`, which also ends a line at U+2028, a
    form feed and the other separators ripgrep reads as text - so it cited a match one line too
    far, with half its line, where ripgrep cites the line a person's editor shows (T635)."""
    (tmp_path / "f.txt").write_bytes("x\u2028NEEDLE\nNEEDLE\x0cy\r\nlast\n".encode("utf-8"))
    found = repo_walk.search(tmp_path, "NEEDLE", globs=["*.txt"], use_rg=False)
    assert [(m.line, m.text) for m in found] == [(1, "x\u2028NEEDLE"), (2, "NEEDLE\x0cy")]


def test_the_python_search_reports_no_line_after_a_final_newline(tmp_path: Path):
    """A newline ends a line; it does not begin an empty one. ripgrep reports no line 2 in
    `a\\n`, so a pattern matching an empty line finds nothing there - and does find the empty
    line between two others."""
    (tmp_path / "f.txt").write_bytes(b"a\n")
    assert repo_walk.search(tmp_path, "^$", globs=["*.txt"], use_rg=False) == []
    (tmp_path / "g.txt").write_bytes(b"a\n\nb\n")
    found = repo_walk.search(tmp_path, "^$", globs=["g.txt"], use_rg=False)
    assert [(m.line, m.text) for m in found] == [(2, "")]


def test_iter_lines_ends_a_line_only_at_a_newline(tmp_path: Path):
    """`iter_lines` numbers lines as ripgrep and an editor do: a newline ends one, a CRLF's
    `\\r` is dropped, and U+2028 or U+0085 inside a line stays text (T635)."""
    path = tmp_path / "f.txt"
    path.write_bytes("a\u2028b\r\nc\x85d\ne".encode("utf-8"))
    assert list(repo_walk.iter_lines(path)) == [(1, "a\u2028b"), (2, "c\x85d"), (3, "e")]


def test_a_file_that_will_not_open_is_named_in_words_no_locale_changes(tmp_path: Path,
                                                                       monkeypatch):
    """T638: the reason `read_text_reporting` hands back reaches the could-not-read rows the
    extractors file, and with them the shards a repository commits. It was `str(exc)` - the
    OS's sentence in the OS's language, and the file's absolute path besides - so two machines
    wrote different bytes for one fault. It is the exception's class and its errno name now."""
    path = tmp_path / "f.yml"
    path.write_bytes(b"a: 1\n")

    def refuse(*args, **kwargs):
        raise PermissionError(13, "Zugriff verweigert", str(path))
    monkeypatch.setattr(repo_walk, "open", refuse, raising=False)
    assert repo_walk.read_text_reporting(path) == ("", "PermissionError (EACCES)")


def test_an_oserror_without_a_known_errno_is_named_by_its_class():
    assert repo_walk.os_error_word(FileNotFoundError(2, "Datei nicht gefunden")) == (
        "FileNotFoundError (ENOENT)")
    assert repo_walk.os_error_word(OSError("no errno at all")) == "OSError"
    assert repo_walk.os_error_word(OSError(987654, "an errno no table names")) == "OSError"


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
    # T110: one name in two directories. Compared by NAME, a backend that lost the
    # directories - or returned one of the two - still agreed; by path it cannot.
    (tmp_path / "deploy").mkdir()
    (tmp_path / "deploy" / "ci.yml").write_text("on: push", encoding="utf-8")

    fast = _paths(repo_walk.find_files(tmp_path, patterns), tmp_path)
    slow = _paths(repo_walk.find_files(tmp_path, patterns, use_fd=False), tmp_path)
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
        stderr = ""

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


def test_served_by_names_the_route_that_served_each_language():
    """Batch 22 (T631): a parser's version is the version of what served it, so `srcfacts`
    records the route - the pinned per-language module, or the aggregate pack - and
    `served_by` reads it. Nothing asked, or nothing serving, answers None."""
    assert srcfacts.served_by("never-asked-language") is None
    assert srcfacts.available("not-a-language") is False
    assert srcfacts.served_by("not-a-language") is None
    pinned = [lang for lang, (module, _factory) in srcfacts._MODULES.items()
              if srcfacts.available(lang)]
    # Branch and assert, never skip: where a pinned grammar serves, its module is the route;
    # where none can, every pinned language must be one nothing serves or the pack serves.
    if pinned:
        lang = pinned[0]
        assert srcfacts.served_by(lang) == srcfacts._MODULES[lang][0]
    else:
        for lang in srcfacts._MODULES:
            assert srcfacts.served_by(lang) in (None, "tree_sitter_language_pack")


def test_served_by_names_the_pack_where_only_the_pack_serves(monkeypatch):
    """A language with no importable pinned module, which the aggregate pack serves, is
    recorded as the pack's - its distribution, not a guess, gives the version."""
    marker = object()
    monkeypatch.setitem(srcfacts._MODULES, "b22-pack-only", ("no_such_grammar_module_b22",
                                                             "language"))
    monkeypatch.setattr(srcfacts, "_pack_parser",
                        lambda language: marker if language == "b22-pack-only" else None)
    assert srcfacts.available("b22-pack-only") is True
    assert srcfacts.served_by("b22-pack-only") == "tree_sitter_language_pack"


@pytest.mark.skipif(not srcfacts.available("python"), reason="grammar unavailable")
def test_query_failure_is_recorded_not_silently_dropped():
    """A broken query must record the loss so callers can surface it.

    Regression: query() returned [] for an uncompilable query and promised the loss
    would appear in the validation report, but nothing was recorded -- the caller got
    [] and could not tell 'nothing matched' from 'query broken'.
    """
    sf = srcfacts.parse("python", "x = 1\n")
    broken_a = "(this_is_not_valid_syntax @@@"
    broken_b = "))) also_broken ((( !!!"
    assert sf.query(broken_a) == [], "a broken query still returns [] for graceful degradation"
    assert sf.query(broken_b) == [], "a second broken query also returns []"
    assert hasattr(sf, "query_failures"), (
        "query() must record failures on the instance so callers can tell "
        "'nothing matched' from 'query broken' -- zero silent drops"
    )
    # Two broken queries must produce two entries -- a list that loses entries is the
    # same defect this row exists to fix, one level in.
    assert len(sf.query_failures) == 2, (
        f"two broken queries must produce two recorded failures, got {len(sf.query_failures)}"
    )
    assert broken_a in sf.query_failures[0], "first recorded failure must name its query"
    assert broken_b in sf.query_failures[1], "second recorded failure must name its query"
    # The diagnosis (exception type AND message) is the actionable half -- without it
    # a caller knows WHICH query failed but not WHY, which is not enough to fix it.
    # Pin a substring of the message that the type name alone cannot satisfy.
    for rec in sf.query_failures:
        assert "Invalid" in rec, (
            f"the recorded failure must carry the diagnostic message, not just the type: {rec!r}"
        )


# ── T531: one definition of test code, the same on every platform ────────────

@pytest.mark.parametrize("rel, test_code", [
    # Production classes whose names end in a folded test suffix, and packages named like
    # test directories: dropped by every gated extractor, with no row.
    ("src/main/java/com/example/credit/Credit.java", False),
    ("src/main/java/com/example/credit/Audit.java", False),
    ("src/main/java/com/example/web/Latest.java", False),
    ("src/main/java/com/example/spec/OrderSpecs.java", False),
    ("src/main/java/com/example/it/Ops.java", False),
    ("i18n/it/messages.properties", False),            # a locale, not a test directory
    # A test file NAME counts anywhere, below `src/main/` too: a web front end kept there
    # has `*.spec.ts` suites, and reading one as production invents a call.
    ("src/main/webapp/app/orders/orders.component.spec.ts", True),
    ("src/main/frontend/src/App.test.tsx", True),
    ("src/main/java/com/example/web/ABTest.java", True),
    ("lib/test_helpers.rb", True),                     # `test_` on any extension, as before
    ("app/Test_Legacy.PY", True),                      # and case-folded, as before
    ("src/test/java/com/example/Fixtures.java", True),
    ("src/it/java/com/example/OrdersIT.java", True),
    ("tests/conftest.py", True),
    ("spec/models/order_spec.rb", True),
    # The first `src/<set>` decides, so a project fixture under a test tree stays a test.
    ("src/test/resources/projects/demo/src/main/java/App.java", True),
    # B9-1: and a project kept under a test DIRECTORY is a fixture too - read as
    # production, its endpoints entered the graph as the component's own.
    ("tests/fixtures/demo/src/main/java/com/example/DemoController.java", True),
    ("e2e/app/src/main/java/com/example/Probe.java", True),
    # B9-14: a directory named `it` is not one - here it is Italy's site module, whose
    # production code a test reading dropped with no row.
    ("sites/it/src/main/java/com/example/Home.java", False),
    ("sites/de/src/main/java/com/example/Home.java", False),
    ("src\\main\\java\\Credit.java", False),
])
def test_looks_like_test_reads_names_and_source_sets_by_their_own_rules(rel, test_code):
    """T531: the suffix check lowercased the whole name, so `Credit.java` ended in
    `it.java` and `Latest.java` in `test.java`, and every `it/` or `spec/` package was a
    test directory. Only the suffixes whose boundary is a capital letter are case-sensitive
    now; the rest keep the folded match they always had, so nothing else narrows."""
    assert repo_walk.looks_like_test(rel) is test_code


def test_glob_match_does_not_fold_case_where_the_platform_would(monkeypatch):
    """T531: `fnmatch.fnmatch` folds case through `os.path.normcase`, which lowercases on
    Windows alone - so `Credit.java` matched `*IT.java` there and became a test suite, and
    one commit built two graphs. Simulating that platform here makes the check fail before
    the fix on every platform, not only on the one that folds."""
    import os
    monkeypatch.setattr(os.path, "normcase", lambda s: s.lower())
    assert not repo_walk.glob_match("src/Credit.java", "Credit.java", "*IT.java")
    assert not repo_walk.glob_match("charts/a/values.YAML", "values.YAML", "values.yaml")
    assert repo_walk.glob_match("src/CreditIT.java", "CreditIT.java", "*IT.java")


@pytest.mark.parametrize("use_fd", [True, False], ids=["fd", "python"])
def test_a_file_missing_its_pattern_only_by_case_is_reported_not_dropped(
        tmp_path: Path, monkeypatch, use_fd: bool):
    """T531: with matching case-sensitive, a file spelled `dockerfile` or a migration named
    `v3__x.sql` is no longer read on Windows either. The same walk names it, with EVERY
    pattern it missed only by case (B9-3: `values.YAML` misses `*.yaml` and `values.yaml`,
    and naming the first in sort order blamed the wrong reader), identically from both
    backends and in one order on every platform, and never for a file a pattern matches.

    The fd half drives the real `_find_with_fd` through an fd-shaped walk (B9-10): the
    vendored binary is untracked, and without it `use_fd=True` quietly ran the fallback."""
    (tmp_path / "db").mkdir()
    rels = ("dockerfile", "Dockerfile.prod", "db/v3__x.sql", "db/V4__y.sql", "values.YAML")
    for rel in rels:
        (tmp_path / rel).write_text("x", encoding="utf-8")
    if use_fd:
        class Walked:
            returncode = 0
            stdout = "".join(str((tmp_path / rel).resolve()) + "\n" for rel in rels)
            stderr = ""
        monkeypatch.setattr(repo_walk, "_tool", lambda name: "fd")
        monkeypatch.setattr(repo_walk.subprocess, "run", lambda *a, **k: Walked())
    misses: list = []
    found = repo_walk.find_files(tmp_path, ["*.yaml", "Dockerfile*", "V*__*.sql",
                                            "values.yaml"], use_fd=use_fd, near_misses=misses)
    assert _paths(found, tmp_path) == ["Dockerfile.prod", "db/V4__y.sql"]
    assert [(p.relative_to(tmp_path).as_posix(), pats) for p, pats in misses] == [
        ("db/v3__x.sql", ("V*__*.sql",)), ("dockerfile", ("Dockerfile*",)),
        ("values.YAML", ("*.yaml", "values.yaml"))]


def test_near_misses_are_only_worked_out_when_asked_for(tmp_path: Path, monkeypatch):
    """B9-12: the predicate ran for every unmatched file on the fd path even with no
    `near_misses` to fill, and the answer was thrown away."""
    (tmp_path / "dockerfile").write_text("x", encoding="utf-8")

    class Walked:
        returncode = 0
        stdout = str((tmp_path / "dockerfile").resolve()) + "\n"
        stderr = ""
    monkeypatch.setattr(repo_walk, "_tool", lambda name: "fd")
    monkeypatch.setattr(repo_walk.subprocess, "run", lambda *a, **k: Walked())
    calls: list = []
    monkeypatch.setattr(repo_walk, "_near_miss", lambda *a: calls.append(a) or ())
    repo_walk.find_files(tmp_path, ["Dockerfile*"])
    assert calls == []


# ── batch 16: a line the reader cannot decode is refused; a row it decodes can be written ──

def _b16_shard(tmp_path: Path, line: str) -> Path:
    p = tmp_path / "shard.jsonl"
    p.write_text(json.dumps({"kind": "header", "schema_version": emit.SCHEMA_VERSION,
                             "extractor": "t"}) + "\n" + line + "\n", encoding="utf-8")
    return p


@pytest.mark.parametrize("line", ["not json", "[" * 4000 + "]" * 4000],
                         ids=["not-json", "nested-4000-deep"])
def test_a_line_the_decoder_cannot_read_is_a_row_refusal(tmp_path: Path, line: str):
    """Batch 16 (D16-27, D16-36): `json.loads` raised a raw `JSONDecodeError`, or a
    `RecursionError` for a line nested past the decoder, and the load and `--incremental`
    handed a person a traceback. Both are now `ShardRowError`, naming the line, from a catch
    no wider than the decode."""
    with pytest.raises(emit.ShardRowError, match=r"shard\.jsonl:2: not JSON"):
        emit.read_jsonl(_b16_shard(tmp_path, line))


@pytest.mark.parametrize("line", ['["a list, not an object"]', '"plain string"', "null"],
                         ids=["list", "string", "null"])
def test_a_line_that_is_not_an_object_is_a_row_refusal(tmp_path: Path, line: str):
    """Batch 16 (D16-27): a valid-JSON line that is not an object raised a bare `TypeError`
    or `AttributeError` from `rec.pop`, outside every catch."""
    with pytest.raises(emit.ShardRowError, match=r"shard\.jsonl:2: not a JSON object"):
        emit.read_jsonl(_b16_shard(tmp_path, line))


def _nested(depth: int) -> object:
    value: object = "leaf"
    for _ in range(depth):
        value = {"x": value}
    return value


def test_a_row_nested_deeper_than_asdict_recurses_writes_and_reads_back(tmp_path: Path):
    """Batch 16 (D16-46): `to_jsonl` copied `props` through `dataclasses.asdict`, which
    recurses in Python, so a row `json.loads` accepts (it recurses in C, far deeper) crashed
    the write - 497 to 2997 deep, measured. It now serializes its own fields, so any row the
    reader decodes can be written."""
    node = Node(id="svc:deep", type="svc", props={"deep": _nested(600)})
    out = tmp_path / "out.jsonl"
    emit.write_jsonl(out, ExtractResult(nodes=[node]), extractor="t")
    assert emit.read_jsonl(out).nodes == [node]


def test_to_jsonl_writes_the_bytes_asdict_wrote():
    """The serialization's bytes are unchanged - a dataclass nested in `props` included, which
    `asdict` turned into a dict - so no committed shard moves."""
    from dataclasses import asdict, dataclass

    @dataclass(frozen=True)
    class Pin:
        line: int
        file: str

    node = Node(id="svc:a", type="svc", props={"pin": Pin(3, "a.py"), "tags": ("x", "y"),
                                               "n": None, "f": 1.5, "u": "\u00e9"})
    assert node.to_jsonl() == json.dumps({"kind": "node", **asdict(node)}, sort_keys=True,
                                         ensure_ascii=False)
    edge = Edge(src="svc:a", type="DG_CALLS", dst="ep:/x", source_file="a.py", line=2,
                extractor="t", props={"pin": Pin(1, "b.py")})
    old = asdict(edge)
    old["provenance"] = edge.provenance.value
    old.pop("run_id", None)
    assert edge.to_jsonl() == json.dumps({"kind": "edge", **old}, sort_keys=True,
                                         ensure_ascii=False)


@pytest.mark.parametrize("separator", [0x2028, 0x2029, 0x85], ids=["LS", "PS", "NEL"])
def test_a_fact_holding_a_unicode_line_separator_reads_back(tmp_path: Path, separator: int):
    """T634: `write_jsonl` emits these three raw (`ensure_ascii=False`; JSON escapes only the
    C0 controls) and `str.splitlines()` ends a line at each, so a fact holding one read back
    as an unterminated string. The reader splits on the newline the writer joins with."""
    node = Node(id="svc:a", type="svc", props={"note": f"x{chr(separator)}y"})
    out = tmp_path / "out.jsonl"
    emit.write_jsonl(out, ExtractResult(nodes=[node]), extractor="t")
    assert chr(separator).encode("utf-8") in out.read_bytes(), "written raw"
    assert emit.read_jsonl(out).nodes == [node]
