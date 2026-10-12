"""The fixture workspace's own profile: a write-mapping format nobody else has.

Committed because the fixture must build by exactly the route an adopter's build takes — if
the golden test reached these edges by any other path, it would be testing something no
adopter ever runs. It is also the worked example of what a profile is *for*: one file format
someone invented, plus the check that only makes sense for it.

A mapping file says "records on this topic land in this table". Nothing generic can read
that — the shape is this workspace's invention — and without it the chain has a hole exactly
where a message becomes a row.

## Keyed on the filename and the topic set, never on a service-name field

A verified trap, carried over from a real workspace: mapping files often carry a free-text
field naming the service they belong to, and it is prose. It gets copied between files, it
disagrees with the directory it sits in, and it collides across components. Keying identity
on it produces mappings attributed to the wrong service — which looks like a real
architectural finding rather than like a parsing bug.

## Two failures this must handle differently

**A malformed file** is recorded with the parser's own message and the build carries on.
Aborting fails a whole workspace on one bad file in one component; skipping silently drops
real edges with no trace. Neither is acceptable and they are not the same mistake.

**A mapping for a topic nothing produces** is not a parse failure at all — the file is
perfectly well-formed and describes a topic that does not exist here. It can only be seen
once every component has been read, so it is a link pass rather than an extractor, and it is
reported rather than dropped: "zero silent drops" is the claim, and an orphan that vanishes
quietly is exactly the drop that claim forbids.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.graph.core.lib import ids, props, repo_walk
from tools.graph.core.model import Edge, ExtractResult, Node, Provenance
from tools.graph.core.spi import ExtractContext, RepoRef

#: The document key holding the list of mappings, and the two fields each entry needs.
MAPPINGS_KEY = "mappings"
TOPIC_FIELD = "topic"
TABLE_FIELD = "table"


def _rel(repo: RepoRef, path: Path) -> str:
    try:
        return path.relative_to(repo.path).as_posix()
    except ValueError:
        return path.as_posix()



#: What each `kind` this module emits promises its reader beyond `UNRESOLVED_COMMON`.
#: Declared HERE rather than in core: a central table would make core learn every
#: extractor's vocabulary, so adding a stack would force a core edit. The pipeline
#: checks these after every run and fails the build when a row does not carry them.
UNRESOLVED_FIELDS = {
    "incomplete-mapping": (),
    "malformed-mappings": (),
    "unreadable-file": (),
    "unparsable": ('error',),
    "unproduced-topic": ('topic', 'table'),
}

class WriteMappingsExtractor:
    """Topic-to-table mappings: where a message becomes a row."""

    name = "orderflow-write-mappings"
    INPUT_GLOBS = ("*-mapping.yml", "*-mapping.yaml", "*-mappings.yml", "*-mappings.yaml")
    #: It reads YAML (batch 16): its shard records whether PyYAML was installed. Its loss
    #: is a failure, which the publish refuses, so no `grammar-unavailable` forces this.
    PARSERS = ("yaml",)

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        for repo in ctx.repos:
            for path in ctx.inputs(repo):
                self._file(res, repo, _rel(repo, path), path)
        return res

    def _file(self, res: ExtractResult, repo: RepoRef, rel: str, path: Path) -> None:
        _, read_err = repo_walk.read_text_reporting(path)
        if read_err:
            res.unresolved.append({
                "extractor": self.name, "component": repo.alias, "kind": "unreadable-file",
                "source_file": rel, "line": 0,
                "why": ("this mapping file could not be read, so the topics and tables it "
                        "connects are missing from the graph rather than absent from the "
                        "configuration")})
            return
        doc, error = props.load_yaml_reporting(path)
        if error:
            res.unresolved.append({
                "extractor": self.name, "component": repo.alias, "kind": "unparsable",
                "source_file": rel, "line": 0, "error": error,
                "why": (f"this mapping file could not be parsed ({error}), so the topics and "
                        f"tables it connects are missing from the graph. The build continues "
                        f"— one bad file in one component must not cost a workspace its whole "
                        f"graph — and this row is the only record that anything was lost"),
            })
            return
        if not isinstance(doc, dict):
            return

        entries = doc.get(MAPPINGS_KEY) or []
        if not isinstance(entries, list):
            res.unresolved.append({
                "extractor": self.name, "component": repo.alias, "kind": "malformed-mappings",
                "source_file": rel, "line": 0,
                "why": f"`{MAPPINGS_KEY}` is not a list, so no mapping could be read from it",
            })
            return

        text = props.read_text(path)
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            topic, table = entry.get(TOPIC_FIELD), entry.get(TABLE_FIELD)
            if not topic or not table:
                res.unresolved.append({
                    "extractor": self.name, "component": repo.alias,
                    "kind": "incomplete-mapping", "source_file": rel,
                    "line": props.find_line(text, f"{TOPIC_FIELD}: {topic}") if topic else 0,
                    "why": (f"a mapping entry names {'no table' if topic else 'no topic'}, so "
                            f"it connects nothing — recorded rather than skipped, because a "
                            f"half-written mapping is a real finding about the file"),
                })
                continue

            topic_id, table_id = ids.topic_id(str(topic)), ids.table_id(str(table))
            res.nodes.append(Node(id=topic_id, type="topic"))
            res.nodes.append(Node(id=table_id, type="table", props={"name": str(table)}))
            res.edges.append(Edge(
                src=topic_id, type="DG_WRITES_TABLE", dst=table_id, source_file=rel,
                line=props.find_line(text, f"{TOPIC_FIELD}: {topic}"),
                extractor=self.name, confidence=1.0, provenance=Provenance.EXTRACTED,
                props={"table_line": props.find_line(text, f"{TABLE_FIELD}: {table}"),
                       "mode": str(entry.get("mode", "")), "component": repo.alias}))


class UnproducedTopicsPass:
    """Mappings whose topic nothing in the workspace publishes."""

    name = "orderflow-unproduced-topics"
    REQUIRES = ("orderflow-write-mappings",)

    def link(self, ctx: Any, nodes: list[Node], edges: list[Edge]) -> ExtractResult:
        res = ExtractResult()
        produced = {e.dst for e in edges if e.type == "DG_PRODUCES"}
        for edge in edges:
            if edge.type != "DG_WRITES_TABLE" or edge.src in produced:
                continue
            # The nodes stay. What must never happen is the mapping vanishing without a
            # record — a well-formed file describing a topic nobody publishes is either a
            # dangling reference or a producer this build cannot see, and both are findings.
            res.unresolved.append({
                "extractor": self.name, "component": edge.props.get("component", ""),
                "kind": "unproduced-topic", "topic": edge.src, "table": edge.dst,
                "source_file": edge.source_file, "line": edge.line,
                "why": (f"this mapping writes {edge.src} into {edge.dst}, and no component in "
                        f"this workspace publishes that topic. The mapping is kept — dropping "
                        f"it would be the silent loss this report exists to prevent — but the "
                        f"table it fills has no visible source"),
            })
        return res


EXTRACTORS = (WriteMappingsExtractor(),)
LINK_PASSES = (UnproducedTopicsPass(),)
