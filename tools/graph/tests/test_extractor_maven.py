"""What the maven reader says about a declaration it cannot read, and where it says it is.

Three properties the module graph rests on and nothing pinned:

* a provenance line comes from the match that produced the value, so a manifest that
  pretty-prints its own `<artifactId>` is cited at the declaration and not at the last line
  of the file — the shape a miss used to produce, because `str.find` returns `-1` and `-1`
  is a legal `str.count` end offset;
* a `<dependency>` block whose artifact this reader cannot make out, and a manifest that
  never names its own artifact, each leave a row behind — this was the only edge-emitting
  extractor with no `unresolved` path at all;
* an aggregator's `<module>` names a DIRECTORY, and a directory is not an artifact: the
  child manifest is what says which coordinates that module publishes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.core.spi import ExtractContext, RepoRef  # noqa: E402
from tools.graph.extractors import maven  # noqa: E402


def ctx_for(tmp_path: Path, files: dict[str, str], alias: str = "svc") -> ExtractContext:
    root = tmp_path / alias
    paths = []
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        paths.append(p)
    return ExtractContext(repos=[RepoRef(alias=alias, path=root)], run_id="r",
                          files={alias: paths})


def edges(res, type_):
    return [e for e in res.edges if e.type == type_]


def rows(res, kind):
    return [r for r in res.unresolved if r["kind"] == kind]


# ── (a) the line is where the tag is ──────────────────────────────────────────

PRETTY = """<project>
  <groupId>com.example</groupId>
  <artifactId>
    billing-api
  </artifactId>
  <version>1.0</version>
  <dependencies>
    <dependency><groupId>com.example</groupId><artifactId>shared</artifactId></dependency>
  </dependencies>
</project>
"""


def test_a_pretty_printed_artifact_is_cited_at_its_own_declaration(tmp_path: Path):
    """`text.find("<artifactId>billing-api")` needs the value glued to the tag. On this
    manifest it misses, and a miss that reaches `_line_of` reads as the file's LAST line —
    a plausible number nobody would question, pointing at `</project>`."""
    res = maven.ModulesExtractor().extract(ctx_for(tmp_path, {"pom.xml": PRETTY}))
    e = edges(res, "DG_BUILT_FROM")[0]
    assert e.line == 3, "the line the project's own <artifactId> opens on"


def test_a_parent_block_does_not_shift_the_line_it_is_removed_from(tmp_path: Path):
    """The project's coordinates are read with the `<parent>` block taken out of the way.
    Take it out by deleting it and every offset after it moves; the line must still be the
    line in the file a reader opens."""
    pom = """<project>
  <parent>
    <groupId>com.example</groupId>
    <artifactId>platform-parent</artifactId>
  </parent>
  <artifactId>billing-api</artifactId>
</project>
"""
    res = maven.ModulesExtractor().extract(ctx_for(tmp_path, {"pom.xml": pom}))
    assert edges(res, "DG_BUILT_FROM")[0].line == 6


def test_a_negative_offset_is_refused_rather_than_read_as_the_last_index():
    """`str.count(sub, 0, -1)` treats -1 as "up to the last character", so a failed search
    becomes the highest line in the file instead of an error. The one number a provenance
    reader cannot check is a wrong one that looks right."""
    with pytest.raises(ValueError):
        maven._line_of("a\nb\nc\n", -1)


# ── (b) a dropped block is a row ──────────────────────────────────────────────

def test_a_dependency_whose_artifact_cannot_be_read_leaves_a_row(tmp_path: Path):
    pom = """<project>
  <groupId>com.example</groupId>
  <artifactId>billing-api</artifactId>
  <dependencies>
    <dependency>
      <groupId>com.example</groupId>
      <artifactId></artifactId>
    </dependency>
  </dependencies>
</project>
"""
    res = maven.ModulesExtractor().extract(ctx_for(tmp_path, {"pom.xml": pom}))
    assert edges(res, "DG_DEPENDS_ON") == []
    found = rows(res, "unreadable-declaration")
    assert len(found) == 1, f"expected exactly one row, got {res.unresolved}"
    assert found[0]["line"] == 5, "the line the <dependency> block opens on"
    assert found[0]["source_file"] == "pom.xml"
    assert found[0]["raw"], "the kind promises the declaration it could not read"


def test_a_manifest_that_never_names_its_artifact_leaves_a_row(tmp_path: Path):
    """No own artifactId means no module node and no DG_BUILT_FROM — every dependency this
    manifest declares then hangs off a component that publishes nothing, and the traversal
    'who rebuilds when this library changes' stops one hop short with no record of why."""
    pom = """<project>
  <groupId>com.example</groupId>
  <version>1.0</version>
</project>
"""
    res = maven.ModulesExtractor().extract(ctx_for(tmp_path, {"pom.xml": pom}))
    assert edges(res, "DG_BUILT_FROM") == []
    found = rows(res, "manifest-without-coordinates")
    assert len(found) == 1, f"expected exactly one row, got {res.unresolved}"
    assert found[0]["line"] == 1
    assert found[0]["group"] == "com.example"


# ── (c) a module is a directory, not an artifact ──────────────────────────────

def test_a_dependency_sharing_a_module_directory_name_keeps_its_edge(tmp_path: Path):
    """The aggregator builds `core/`, whose manifest publishes `core-impl`. `com.example:core`
    is somebody else's artifact that happens to be spelled like that directory. Fold the
    directory into the coordinate set and the real dependency is subtracted away — one
    declared in, none out, and nothing recorded."""
    agg = """<project>
  <groupId>com.example</groupId>
  <artifactId>platform-parent</artifactId>
  <modules><module>core</module></modules>
  <dependencies>
    <dependency><groupId>com.example</groupId><artifactId>core</artifactId></dependency>
  </dependencies>
</project>
"""
    child = ("<project><groupId>com.example</groupId>"
             "<artifactId>core-impl</artifactId></project>\n")
    res = maven.ModulesExtractor().extract(
        ctx_for(tmp_path, {"pom.xml": agg, "core/pom.xml": child}))
    kept = [e.dst for e in edges(res, "DG_DEPENDS_ON")]
    assert "mod:com.example:core" in kept, (
        "the module directory 'core' is not the artifact 'core'; the child manifest says "
        f"the module publishes core-impl. kept={kept}")


def test_a_module_with_no_readable_manifest_is_recorded_not_guessed(tmp_path: Path):
    agg = """<project>
  <groupId>com.example</groupId>
  <artifactId>platform-parent</artifactId>
  <modules>
    <module>web</module>
  </modules>
</project>
"""
    res = maven.ModulesExtractor().extract(ctx_for(tmp_path, {"pom.xml": agg}))
    found = rows(res, "module-unresolved")
    assert len(found) == 1, f"expected exactly one row, got {res.unresolved}"
    assert found[0]["module"] == "web"
    assert found[0]["line"] == 5, "the line the <module> sits on"
