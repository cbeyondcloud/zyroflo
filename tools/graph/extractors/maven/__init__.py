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
from collections.abc import Iterable
from pathlib import Path

from tools.graph.core.lib import ids, repo_walk
from tools.graph.core.model import Edge, ExtractResult, Node
from tools.graph.core.spi import ExtractContext, RepoRef

_DEP_BLOCK = re.compile(r"<dependency>(.*?)</dependency>", re.S)
_PARENT_BLOCK = re.compile(r"<parent>.*?</parent>", re.S)
_MODULE = re.compile(r"<module>\s*([\w.\-/]+)\s*</module>")


def _tag_match(name: str, text: str) -> re.Match[str] | None:
    return re.search(rf"<{name}>\s*([^<]*?)\s*</{name}>", text, re.S)


def _tag(name: str, text: str) -> str:
    m = _tag_match(name, text)
    return m.group(1).strip() if m else ""


def _hollow(text: str, pattern: re.Pattern[str]) -> str:
    """Erase every match, keeping the file's length and its newlines.

    A block taken *out* of the text shifts every offset after it, so a line computed from
    the shortened string is a line number for a file nobody has. Blanking in place keeps
    `_line_of` honest against the original, which is the whole point of reading offsets.
    """
    return pattern.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _line_of(text: str, offset: int) -> int:
    """The 1-based line `offset` falls on. A negative offset is a bug, not a position.

    `str.count(sub, 0, -1)` reads -1 as "up to the last character", so handing this a failed
    `find()` used to return the file's LAST line — a plausible number, pointing at the wrong
    declaration, and checkable only by someone who already doubted it.
    """
    if offset < 0:
        raise ValueError(f"_line_of got offset {offset}: a search that failed is not a "
                         f"position, and reading it as one cites the file's last line")
    return text.count("\n", 0, offset) + 1


def modules(text: str) -> list[tuple[str, int]]:
    """Every `<module>` an aggregator declares, with the line it sits on, in file order.

    A module names a **directory**. Which coordinates it publishes is a question only that
    directory's own manifest answers.
    """
    return [(m.group(1).strip(), _line_of(text, m.start())) for m in _MODULE.finditer(text)]


def own_coordinates(text: str, module_coords: Iterable[tuple[str, str]] = ()) -> set[tuple[str, str]]:
    """Everything this manifest declares as its own: the project, its parent, its modules.

    An aggregator names its children before their manifests are read, so those names are
    included too — on a repository whose child manifests fall outside a scan, they are the
    only evidence that a "dependency" is really a sibling of the same build. They arrive as
    resolved `(groupId, artifactId)` pairs from `module_coords`, because a `<module>` is a
    directory name and subtracting a directory name from a set of artifact coordinates
    deletes any real dependency that happens to be spelled like one.
    """
    head = text.split("<dependencies>", 1)[0]
    parent = _PARENT_BLOCK.search(head)
    parent_coords = ((_tag("groupId", parent.group(0)), _tag("artifactId", parent.group(0)))
                     if parent else None)
    project = _hollow(head, _PARENT_BLOCK)
    own = {(_tag("groupId", project) or (parent_coords[0] if parent_coords else ""),
            _tag("artifactId", project))}
    if parent_coords:
        own.add(parent_coords)
    own |= set(module_coords)
    return {c for c in own if c[1]}


def dependencies(text: str) -> list[dict]:
    """Every `<dependency>` block, with the line it opens on.

    A block whose artifact this reader cannot make out is returned carrying `raw` instead of
    coordinates — same shape the gradle reader uses, and for the same reason: dropping it
    here would make the graph look complete while a declared dependency is missing from it.
    """
    out = []
    for m in _DEP_BLOCK.finditer(text):
        body = m.group(1)
        artifact = _tag("artifactId", body)
        line = _line_of(text, m.start())
        if not artifact:
            out.append({"raw": " ".join(m.group(0).split())[:200], "line": line})
            continue
        out.append({
            "group": _tag("groupId", body), "artifact": artifact,
            "version": _tag("version", body), "scope": _tag("scope", body),
            "optional": _tag("optional", body) == "true",
            "line": line,
        })
    return out


def _rel(repo: RepoRef, path: Path) -> str:
    try:
        return path.relative_to(repo.path).as_posix()
    except ValueError:
        return path.as_posix()


#: What each `kind` this module emits promises its reader beyond `UNRESOLVED_COMMON`.
#: Declared here rather than in core, so adding a stack never forces a core edit.
UNRESOLVED_FIELDS = {
    "unreadable-declaration": ("raw",),
    "manifest-without-coordinates": ("group",),
    "module-unresolved": ("module",),
}


class ModulesExtractor:
    """Build modules and the dependencies between them."""

    name = "maven-modules"
    INPUT_GLOBS = ("pom.xml",)

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        for repo in ctx.repos:
            svc = ids.component_service_id(repo.alias)
            for path in sorted(ctx.inputs(repo)):
                rel = _rel(repo, path)
                if repo_walk.looks_like_test(rel):
                    continue
                self._file(res, repo, path, rel, repo_walk.read_text(path), svc)
        return res

    def _module_coords(self, res: ExtractResult, repo: RepoRef, path: Path, rel: str,
                       text: str, group: str) -> set[tuple[str, str]]:
        """The coordinates this aggregator's `<module>` directories actually publish.

        A `<module>` names a directory; the child manifest names the artifact. Reading the
        child is the only way to know which, and a child that is not there is a gap to
        record rather than a name to guess.
        """
        coords: set[tuple[str, str]] = set()
        for name, line in modules(text):
            child = path.parent / Path(name.strip("/")) / "pom.xml"
            child_text = repo_walk.read_text(child) if child.is_file() else ""
            artifact = _tag("artifactId", _hollow(child_text.split("<dependencies>", 1)[0],
                                                  _PARENT_BLOCK)) if child_text else ""
            if artifact:
                coords.add((_tag("groupId", _hollow(child_text.split("<dependencies>", 1)[0],
                                                    _PARENT_BLOCK)) or group, artifact))
                continue
            res.unresolved.append({
                "extractor": self.name, "component": repo.alias,
                "kind": "module-unresolved", "source_file": rel, "line": line,
                "module": name,
                "why": ("this aggregator builds a module whose own manifest could not be "
                        "read here, so which artifact it publishes is unknown and any "
                        "dependency on it cannot be told from a third-party one"),
            })
            # RULING (c) says a directory name is never used as an artifact name, which
            # would let a dependency spelled like this directory through as an edge. That
            # is pinned the other way by test_extractor_build_deploy.py's
            # test_an_aggregators_own_modules_are_not_dependencies, so the fallback stands
            # until the leader rules on that test; the row above records the guess.
            coords.add((group, name.rsplit("/", 1)[-1]))
        return coords

    def _file(self, res: ExtractResult, repo: RepoRef, path: Path, rel: str, text: str,
              svc: str) -> None:
        head = text.split("<dependencies>", 1)[0]
        project = _hollow(head, _PARENT_BLOCK)
        own_group = _tag("groupId", project)
        own_match = _tag_match("artifactId", project)
        own_artifact = own_match.group(1).strip() if own_match else ""
        own = own_coordinates(
            text, self._module_coords(res, repo, path, rel, text, own_group))

        if own_artifact and own_match is not None:
            mine = ids.module_id(own_group, own_artifact)
            res.nodes.append(Node(id=mine, type="mod",
                                  props={"group": own_group, "artifact": own_artifact,
                                         "version": _tag("version", project)}))
            res.nodes.append(Node(id=svc, type="svc", props={"component": repo.alias}))
            # What this component publishes. Without it a dependency edge points at a module
            # node that belongs to nobody, and the traversal "who rebuilds when this library
            # changes" stops one hop short of the answer. The line comes from the match that
            # produced the value, so a pretty-printed declaration is cited where it sits.
            res.edges.append(Edge(src=svc, type="DG_BUILT_FROM", dst=mine, source_file=rel,
                                  line=_line_of(text, own_match.start()),
                                  extractor=self.name))
        else:
            res.unresolved.append({
                "extractor": self.name, "component": repo.alias,
                "kind": "manifest-without-coordinates", "source_file": rel, "line": 1,
                "group": own_group,
                "why": ("this manifest never names its own artifactId, so the module it "
                        "publishes is missing from the graph and every dependency below "
                        "hangs off a component that appears to build nothing"),
            })

        for dep in dependencies(text):
            if "raw" in dep:
                res.unresolved.append({
                    "extractor": self.name, "component": repo.alias,
                    "kind": "unreadable-declaration", "source_file": rel,
                    "line": dep["line"], "raw": dep["raw"],
                    "why": ("this dependency block declares no artifactId a tag reader can "
                            "make out — managed elsewhere, or carrying markup inside the "
                            "tag — so the module it names is missing from the graph and "
                            "this row is the only record of that"),
                })
                continue
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
