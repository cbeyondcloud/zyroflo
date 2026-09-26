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

from tools.graph.core.lib import ids  # noqa: E402
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


def test_a_parent_inherited_group_is_carried_into_the_modules_own_node_id(tmp_path: Path):
    """A manifest that spells no `<groupId>` of its own publishes under the one it inherits
    from `<parent>` — the ordinary multi-module shape. Its OWN module node must carry that
    effective group, because every depender writes the real coordinate: build the own id
    from the declared (empty) group and this manifest's `DG_BUILT_FROM` lands on
    `mod::billing-api` while `<dependency><groupId>com.example` ... points at
    `mod:com.example:billing-api`. One module, two node ids — the split `ids.py` exists to
    prevent — and the coordinates a manifest subtracts as its own already resolve through
    `effective_group`, so only the own node id is left reading the declared group."""
    pom = """<project>
  <parent><groupId>com.example</groupId><artifactId>platform-parent</artifactId></parent>
  <artifactId>billing-api</artifactId>
</project>
"""
    res = maven.ModulesExtractor().extract(ctx_for(tmp_path, {"pom.xml": pom}))
    built = edges(res, "DG_BUILT_FROM")
    assert len(built) == 1, f"one own module, one DG_BUILT_FROM edge. edges={built}"
    assert built[0].dst == "mod:com.example:billing-api", (
        "the module publishes com.example:billing-api, inherited from <parent>; its own node "
        f"id must be that, not an empty group a depender never writes. dst={built[0].dst!r}")
    node = [n for n in res.nodes if n.type == "mod" and n.id == built[0].dst]
    assert node, (
        "the DG_BUILT_FROM edge must land on a module node with the same id; a node built "
        f"under a different group is the two-spellings split. mod nodes={[n.id for n in res.nodes if n.type == 'mod']}")
    assert node[0].props["group"] == "com.example", (
        "the node's group prop must name the group its id is built from, the way a dependency "
        f"target node does; otherwise the prop misreports the identity. props={node[0].props}")


def test_a_manifests_own_group_wins_over_its_parents_in_its_node_id(tmp_path: Path):
    """A manifest that DECLARES its own groupId and also names a `<parent>` with a different
    one publishes under its OWN — the parent's is only the fallback when none is declared. Its
    own node id must be built from the declared group: reach for the inherited one and the id
    is `mod:com.example.parent:billing-api`, a well-formed coordinate for the wrong group,
    while every depender writing the real `com.example.child` points at a different node. The
    parent's group is the VALID wrong answer this pins against — asserting merely a non-empty
    group would prove a group is required, not that the effective (own-wins) one was chosen."""
    pom = """<project>
  <parent><groupId>com.example.parent</groupId><artifactId>platform-parent</artifactId></parent>
  <groupId>com.example.child</groupId>
  <artifactId>billing-api</artifactId>
</project>
"""
    res = maven.ModulesExtractor().extract(ctx_for(tmp_path, {"pom.xml": pom}))
    built = edges(res, "DG_BUILT_FROM")
    assert len(built) == 1, f"one own module, one DG_BUILT_FROM edge. edges={built}"
    assert built[0].dst == "mod:com.example.child:billing-api", (
        "the manifest declares com.example.child; its own node id must use that, not the "
        f"com.example.parent it merely inherits as a fallback. dst={built[0].dst!r}")
    node = [n for n in res.nodes if n.type == "mod" and n.id == built[0].dst]
    assert node and node[0].props["group"] == "com.example.child", (
        "the node's group prop must match its id's group, the declared one. "
        f"mod nodes={[(n.id, n.props.get('group')) for n in res.nodes if n.type == 'mod']}")


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


def test_a_module_that_inherits_its_group_from_a_parent_is_still_its_own(tmp_path: Path):
    """Ordinary maven: neither the aggregator nor the child spells a groupId, both inherit
    one from `<parent>`. The child manifest is right there and readable, so which artifact
    the module publishes is knowable — `com.example:core` — and an aggregator that builds it
    is not depending on somebody else's library. Read the child's groupId without the same
    parent fallback the manifest's own coordinates get and the module resolves to
    `("", "core")`, which subtracts nothing: the aggregator reports itself as its own
    dependency, and no `unresolved` row admits it, because nothing failed to resolve."""
    agg = """<project>
  <parent><groupId>com.example</groupId><artifactId>root</artifactId></parent>
  <artifactId>platform-parent</artifactId>
  <modules><module>core</module></modules>
  <dependencies>
    <dependency><groupId>com.example</groupId><artifactId>core</artifactId></dependency>
  </dependencies>
</project>
"""
    child = ("<project><parent><groupId>com.example</groupId>"
             "<artifactId>platform-parent</artifactId></parent>"
             "<artifactId>core</artifactId></project>\n")
    res = maven.ModulesExtractor().extract(
        ctx_for(tmp_path, {"pom.xml": agg, "core/pom.xml": child}))
    kept = [e.dst for e in edges(res, "DG_DEPENDS_ON")]
    assert kept == [], (
        "the module core/ publishes com.example:core, inherited from <parent> exactly as "
        f"the aggregator's own coordinates are; that is not a dependency. kept={kept}")
    assert res.unresolved == [], (
        "nothing was unresolved: the child manifest is present and readable, and a row "
        f"here would be a gap reported where there is none. rows={res.unresolved}")


def test_a_module_takes_its_inherited_group_from_its_own_parent(tmp_path: Path):
    """The child inherits from a different parent than the aggregator does, so the group it
    publishes under is not the aggregator's — the shape of any nested reactor. Which makes
    this the case that tells the two halves of the rule apart: read the child's own
    `<parent>` and the module is `com.example.sub:core`, the artifact the aggregator
    declares; reach for the aggregator's group instead and it is `com.example:core`, and the
    declared dependency — on the module this very build produces — survives as an edge."""
    agg = """<project>
  <parent><groupId>com.example</groupId><artifactId>root</artifactId></parent>
  <artifactId>platform-parent</artifactId>
  <modules><module>core</module></modules>
  <dependencies>
    <dependency><groupId>com.example.sub</groupId><artifactId>core</artifactId></dependency>
  </dependencies>
</project>
"""
    child = ("<project><parent><groupId>com.example.sub</groupId>"
             "<artifactId>sub-parent</artifactId></parent>"
             "<artifactId>core</artifactId></project>\n")
    res = maven.ModulesExtractor().extract(
        ctx_for(tmp_path, {"pom.xml": agg, "core/pom.xml": child}))
    kept = [e.dst for e in edges(res, "DG_DEPENDS_ON")]
    assert kept == [], (
        "the module publishes com.example.sub:core, which its own manifest says and the "
        f"aggregator's group cannot. kept={kept}")
    assert res.unresolved == [], f"nothing failed to resolve. rows={res.unresolved}"


def test_a_module_that_names_no_group_at_all_falls_back_to_the_aggregators(tmp_path: Path):
    """The child names an artifactId and no group, its own or inherited. Maven publishes no
    artifact without a group, so the group is one this manifest does not repeat, and the
    only one in scope is the reactor it is built by — the aggregator's own effective group,
    which is itself inherited from `<parent>` here. Falling back to the aggregator's
    *declared* group would find nothing; falling back to nothing at all resolves the module
    to `("", "core")` and hands the aggregator a dependency on the module it builds."""
    agg = """<project>
  <parent><groupId>com.example</groupId><artifactId>root</artifactId></parent>
  <artifactId>platform-parent</artifactId>
  <modules><module>core</module></modules>
  <dependencies>
    <dependency><groupId>com.example</groupId><artifactId>core</artifactId></dependency>
  </dependencies>
</project>
"""
    res = maven.ModulesExtractor().extract(ctx_for(tmp_path, {
        "pom.xml": agg, "core/pom.xml": "<project><artifactId>core</artifactId></project>\n"}))
    kept = [e.dst for e in edges(res, "DG_DEPENDS_ON")]
    assert kept == [], f"the module resolves under the aggregator's group. kept={kept}"
    assert res.unresolved == [], f"nothing failed to resolve. rows={res.unresolved}"


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


# ── T533: a placeholder is not a coordinate ─────────────────────────────────


PLACEHOLDERS = """<project>
  <groupId>com.example.shop</groupId>
  <artifactId>orders-api</artifactId>
  <version>1.0</version>
  <properties>
    <shop.group>com.example.shop</shop.group>
  </properties>
  <dependencies>
    <dependency><groupId>${project.groupId}</groupId><artifactId>shop-shared</artifactId></dependency>
    <dependency><groupId>${shop.group}</groupId><artifactId>shop-model</artifactId></dependency>
  </dependencies>
</project>
"""


def test_a_placeholder_coordinate_resolves_against_the_manifest(tmp_path: Path):
    """T533: `${project.groupId}` is the usual spelling of an intra-reactor dependency, and
    it was taken as written - a phantom `mod:{project.groupId}:shop-shared` node, and the
    cross-component join to the real module lost with no row. Resolved against the
    manifest's own coordinates and its `<properties>`, it names the module it means."""
    res = maven.ModulesExtractor().extract(ctx_for(tmp_path, {"pom.xml": PLACEHOLDERS}))
    assert {e.dst for e in edges(res, "DG_DEPENDS_ON")} == {
        ids.module_id("com.example.shop", "shop-shared"),
        ids.module_id("com.example.shop", "shop-model")}
    assert not any("{" in n.id for n in res.nodes), [n.id for n in res.nodes]


def test_a_placeholder_takes_the_group_the_manifest_inherits(tmp_path: Path):
    """`${project.groupId}` of a module that names no group of its own is its parent's -
    `effective_group`'s rule, the one every other coordinate here is read by."""
    pom = """<project>
  <parent>
    <groupId>com.example.platform</groupId>
    <artifactId>platform-parent</artifactId>
  </parent>
  <artifactId>billing-api</artifactId>
  <dependencies>
    <dependency><groupId>${project.groupId}</groupId><artifactId>billing-model</artifactId></dependency>
    <dependency><groupId>${project.parent.groupId}</groupId><artifactId>audit</artifactId></dependency>
  </dependencies>
</project>
"""
    res = maven.ModulesExtractor().extract(ctx_for(tmp_path, {"pom.xml": pom}))
    assert {e.dst for e in edges(res, "DG_DEPENDS_ON")} == {
        ids.module_id("com.example.platform", "billing-model"),
        ids.module_id("com.example.platform", "audit")}


def test_a_placeholder_nothing_here_defines_is_a_row_not_a_node(tmp_path: Path):
    """What cannot be resolved from this manifest - a property defined in a parent file,
    or only in a build profile - is recorded, never minted into a module that looks exactly
    like a third party's."""
    pom = """<project>
  <groupId>com.example</groupId>
  <artifactId>svc</artifactId>
  <dependencies>
    <dependency><groupId>${external.group}</groupId><artifactId>lib</artifactId></dependency>
  </dependencies>
</project>
"""
    res = maven.ModulesExtractor().extract(ctx_for(tmp_path, {"pom.xml": pom}))
    assert edges(res, "DG_DEPENDS_ON") == []
    found = rows(res, "unreadable-declaration")
    assert len(found) == 1 and "${external.group}" in found[0]["raw"], res.unresolved
    assert found[0]["line"] == 5


def test_a_commented_out_properties_block_does_not_supply_a_coordinate(tmp_path: Path):
    """B8-3: the first `<properties>` in the file was read wherever it stood, so a block a
    comment had retired resolved `${shared.group}` to a group nothing defines - a module id
    exactly as believable as a real one, and no row."""
    pom = """<project>
  <groupId>com.example</groupId>
  <artifactId>svc</artifactId>
  <!-- <properties><shared.group>com.dead.old</shared.group></properties> -->
  <properties>
    <shared.group>com.example.live</shared.group>
  </properties>
  <dependencies>
    <dependency><groupId>${shared.group}</groupId><artifactId>shared</artifactId></dependency>
  </dependencies>
</project>
"""
    res = maven.ModulesExtractor().extract(ctx_for(tmp_path, {"pom.xml": pom}))
    assert {e.dst for e in edges(res, "DG_DEPENDS_ON")} == {
        ids.module_id("com.example.live", "shared")}


def test_a_plugins_configuration_properties_are_not_the_projects(tmp_path: Path):
    """B8-3: a plugin's `<configuration>` may hold a `<properties>` of its own. It is no
    property of this project's, so a coordinate naming one stays unresolved - a row."""
    pom = """<project>
  <groupId>com.example</groupId>
  <artifactId>svc</artifactId>
  <build><plugins><plugin>
    <artifactId>maven-surefire-plugin</artifactId>
    <configuration><properties><shared.group>com.wrong</shared.group></properties></configuration>
  </plugin></plugins></build>
  <dependencies>
    <dependency><groupId>${shared.group}</groupId><artifactId>shared</artifactId></dependency>
  </dependencies>
</project>
"""
    res = maven.ModulesExtractor().extract(ctx_for(tmp_path, {"pom.xml": pom}))
    assert edges(res, "DG_DEPENDS_ON") == []
    found = rows(res, "unreadable-declaration")
    assert len(found) == 1 and "${shared.group}" in found[0]["raw"], res.unresolved


def test_a_commented_out_dependency_is_not_an_edge(tmp_path: Path):
    """T572: every tag here is read by a pattern over the raw text, and a pattern cannot
    tell a live `<dependency>` from one a comment retired - so a dependency the build had
    dropped was still a real edge, cited at a real line."""
    pom = """<project>
  <groupId>com.example</groupId>
  <artifactId>svc</artifactId>
  <dependencies>
    <!--
    <dependency><groupId>com.dead</groupId><artifactId>gone</artifactId></dependency>
    -->
    <dependency><groupId>com.example</groupId><artifactId>kept</artifactId></dependency>
  </dependencies>
</project>
"""
    res = maven.ModulesExtractor().extract(ctx_for(tmp_path, {"pom.xml": pom}))
    found = edges(res, "DG_DEPENDS_ON")
    assert [e.dst for e in found] == [ids.module_id("com.example", "kept")]
    assert found[0].line == 8, "hollowed, not removed: the line is still the file's own"


def test_a_commented_out_module_is_not_built(tmp_path: Path):
    """T572, the aggregator side: a `<module>` a comment retired named a directory the
    reader then went looking for, and reported it missing as though the build still
    built it."""
    pom = """<project>
  <groupId>com.example</groupId>
  <artifactId>parent</artifactId>
  <modules>
    <!-- <module>retired</module> -->
    <module>core</module>
  </modules>
</project>
"""
    child = """<project>
  <parent><groupId>com.example</groupId><artifactId>parent</artifactId></parent>
  <artifactId>core</artifactId>
</project>
"""
    res = maven.ModulesExtractor().extract(ctx_for(tmp_path, {"pom.xml": pom,
                                                              "core/pom.xml": child}))
    assert rows(res, "module-unresolved") == [], res.unresolved
