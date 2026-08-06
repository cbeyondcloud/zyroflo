"""The module graph: what each component builds, and what it builds against.

Answers the question that makes a shared library dangerous — *if I change this, who
rebuilds?* — which is the one blast-radius question no source-level call graph can reach,
because the dependency is declared in a build manifest and nowhere in the code.

## The trap this exists to avoid, and it has already been paid for once

A manifest names its OWN coordinates before it names anyone else's. Match artifact names
anywhere in the file and every component reports itself as its own dependency — which is not
a visible error but a graph where the shared-library question returns the component you
started from. A workspace scan in this project's history reported eight components
"consuming" their own submodules exactly this way.

So only `<dependency>` blocks are read, and the project's own coordinates — plus its
parent's, plus the modules of an aggregator — are subtracted from what is left.

## Structure, read with patterns rather than a parser

These manifests have one relevant shape and a namespace declaration that a strict XML reader
must be taught about while a tag pattern does not care. Line numbers matter more here than
schema fidelity: an edge whose provenance points at a file rather than at a declaration is
an edge nobody can check. If the shapes read here ever grow beyond `<dependency>`, this
should become a real parse.
"""
from __future__ import annotations

import re
from pathlib import Path

from tools.graph.core.lib import ids, repo_walk
from tools.graph.core.model import Edge, ExtractResult, Node
from tools.graph.core.spi import ExtractContext, RepoRef

_DEP_BLOCK = re.compile(r"<dependency>(.*?)</dependency>", re.S)
_PARENT_BLOCK = re.compile(r"<parent>.*?</parent>", re.S)
_MODULE = re.compile(r"<module>\s*([\w.\-/]+)\s*</module>")


def _tag(name: str, text: str) -> str:
    m = re.search(rf"<{name}>\s*([^<]*?)\s*</{name}>", text, re.S)
    return m.group(1).strip() if m else ""


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def own_coordinates(text: str) -> set[tuple[str, str]]:
    """Everything this manifest declares as its own: the project, its parent, its modules.

    An aggregator names its children before their manifests are read, so those names are
    included too — on a repository whose child manifests fall outside a scan, they are the
    only evidence that a "dependency" is really a sibling of the same build.
    """
    head = text.split("<dependencies>", 1)[0]
    parent = _PARENT_BLOCK.search(head)
    parent_coords = ((_tag("groupId", parent.group(0)), _tag("artifactId", parent.group(0)))
                     if parent else None)
    project = _PARENT_BLOCK.sub("", head)
    own = {(_tag("groupId", project) or (parent_coords[0] if parent_coords else ""),
            _tag("artifactId", project))}
    if parent_coords:
        own.add(parent_coords)
    group = _tag("groupId", project) or (parent_coords[0] if parent_coords else "")
    own |= {(group, m.rsplit("/", 1)[-1]) for m in _MODULE.findall(text)}
    return {c for c in own if c[1]}


def dependencies(text: str) -> list[dict]:
    """Every `<dependency>` block, with the line it opens on."""
    out = []
    for m in _DEP_BLOCK.finditer(text):
        body = m.group(1)
        artifact = _tag("artifactId", body)
        if not artifact:
            continue
        out.append({
            "group": _tag("groupId", body), "artifact": artifact,
            "version": _tag("version", body), "scope": _tag("scope", body),
            "optional": _tag("optional", body) == "true",
            "line": _line_of(text, m.start()),
        })
    return out


def _rel(repo: RepoRef, path: Path) -> str:
    try:
        return path.relative_to(repo.path).as_posix()
    except ValueError:
        return path.as_posix()


class ModulesExtractor:
    """Build modules and the dependencies between them."""

    name = "maven-modules"
    INPUT_GLOBS = ("pom.xml",)

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        for repo in ctx.repos:
            svc = ids.component_service_id(repo.alias)
            for path in ctx.inputs(repo):
                rel = _rel(repo, path)
                if repo_walk.looks_like_test(rel):
                    continue
                self._file(res, repo, rel, repo_walk.read_text(path), svc)
        return res

    def _file(self, res: ExtractResult, repo: RepoRef, rel: str, text: str, svc: str) -> None:
        own = own_coordinates(text)
        head = text.split("<dependencies>", 1)[0]
        own_group, own_artifact = _tag("groupId", _PARENT_BLOCK.sub("", head)), \
            _tag("artifactId", _PARENT_BLOCK.sub("", head))

        if own_artifact:
            mine = ids.module_id(own_group, own_artifact)
            res.nodes.append(Node(id=mine, type="mod",
                                  props={"group": own_group, "artifact": own_artifact,
                                         "version": _tag("version", _PARENT_BLOCK.sub("", head))}))
            res.nodes.append(Node(id=svc, type="svc", props={"component": repo.alias}))
            # What this component publishes. Without it a dependency edge points at a module
            # node that belongs to nobody, and the traversal "who rebuilds when this library
            # changes" stops one hop short of the answer.
            res.edges.append(Edge(src=svc, type="DG_BUILT_FROM", dst=mine, source_file=rel,
                                  line=_line_of(text, text.find(f"<artifactId>{own_artifact}")),
                                  extractor=self.name))

        for dep in dependencies(text):
            if (dep["group"], dep["artifact"]) in own:
                continue                    # the manifest's own coordinates, not a dependency
            target = ids.module_id(dep["group"], dep["artifact"])
            res.nodes.append(Node(id=target, type="mod",
                                  props={"group": dep["group"], "artifact": dep["artifact"]}))
            res.nodes.append(Node(id=svc, type="svc", props={"component": repo.alias}))
            res.edges.append(Edge(
                src=svc, type="DG_DEPENDS_ON", dst=target, source_file=rel, line=dep["line"],
                extractor=self.name, confidence=1.0,
                # A blank version is inherited from a parent or a managed set. Normal, not a
                # finding: reporting every one would bury the findings that matter.
                props={"version": dep["version"], "scope": dep["scope"],
                       "optional": dep["optional"],
                       "version_source": "declared" if dep["version"] else "inherited"}))


EXTRACTORS = (ModulesExtractor(),)
