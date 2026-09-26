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
_PLACEHOLDER = re.compile(r"\$\{([^}]*)\}")
_PROFILES = re.compile(r"<profiles>.*?</profiles>", re.S)
_CONFIGURATION = re.compile(r"<configuration>.*?</configuration>", re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_PROPERTIES = re.compile(r"<properties>(.*?)</properties>", re.S)
_PROPERTY = re.compile(r"<([\w.\-]+)>\s*([^<]*?)\s*</\1>")


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


def uncommented(text: str) -> str:
    """The manifest with its XML comments hollowed: what every reader here scans (T572).

    Every tag in this module is read by a pattern over the raw text, and a pattern cannot
    tell a live `<dependency>` from one commented out beside it - so a dependency a build
    had retired was a real edge, and a commented-out `<properties>` block above the live one
    supplied a coordinate's group. Hollowed rather than removed, as `_hollow` explains, so
    every line cited from the result is still the file's own.
    """
    return _hollow(text, _COMMENT)


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


def effective_group(text: str) -> str:
    """The groupId this manifest publishes under: its own, or the one it inherits.

    Maven's rule, and the one place it is written down here. Most manifests in a multi-module
    build never spell a groupId at all — they take their parent's — so a reader that only
    looks for `<groupId>` sees the empty string for the ordinary case. Two callers need this
    and must agree: the coordinates a manifest subtracts as its own, and the coordinates an
    aggregator's `<module>` directory publishes. Answer it differently in those two places
    and the same artifact is `("com.example", "core")` in one and `("", "core")` in the
    other, so the aggregator never recognises the module it builds.
    """
    head = text.split("<dependencies>", 1)[0]
    parent = _PARENT_BLOCK.search(head)
    return (_tag("groupId", _hollow(head, _PARENT_BLOCK))
            or (_tag("groupId", parent.group(0)) if parent else ""))


def own_coordinates(text: str, module_coords: Iterable[tuple[str, str]] = ()) -> set[tuple[str, str]]:
    """Everything this manifest declares as its own: the project, its parent, its modules.

    An aggregator's modules count as its own, because a build that depends on a module it
    also builds depends on itself. They arrive as resolved `(groupId, artifactId)` pairs from
    `module_coords` and never as directory names: a `<module>` is a directory, and
    subtracting a directory name from a set of artifact coordinates deletes any real
    dependency that happens to be spelled like one. A module whose manifest could not be read
    contributes nothing here and is recorded as unresolved instead.
    """
    head = text.split("<dependencies>", 1)[0]
    parent = _PARENT_BLOCK.search(head)
    parent_coords = ((_tag("groupId", parent.group(0)), _tag("artifactId", parent.group(0)))
                     if parent else None)
    project = _hollow(head, _PARENT_BLOCK)
    own = {(effective_group(text), _tag("artifactId", project))}
    if parent_coords:
        own.add(parent_coords)
    own |= set(module_coords)
    return {c for c in own if c[1]}


def dependencies(text: str) -> list[dict]:
    """Every `<dependency>` block, with the line it opens on.

    A block whose artifact this reader cannot make out is returned carrying `raw` instead of
    coordinates — same shape the gradle reader uses, and for the same reason: dropping it
    here would make the graph look complete while a declared dependency is missing from it.
    Every block also carries `decl`, its text, for a caller that records it after all.
    """
    out = []
    for m in _DEP_BLOCK.finditer(text):
        body = m.group(1)
        artifact = _tag("artifactId", body)
        line = _line_of(text, m.start())
        decl = " ".join(m.group(0).split())[:200]
        if not artifact:
            out.append({"raw": decl, "line": line})
            continue
        out.append({
            "group": _tag("groupId", body), "artifact": artifact,
            "version": _tag("version", body), "scope": _tag("scope", body),
            "optional": _tag("optional", body) == "true",
            "line": line, "decl": decl,
        })
    return out


def placeholder_values(text: str) -> dict[str, str]:
    """What a coordinate's `${...}` can name from this manifest alone (T533): its own
    coordinates under the names maven gives them (`project.groupId`, the older
    `pom.groupId`, ...), its parent's (`project.parent.groupId`), and the `<properties>` it
    declares. The own group is the EFFECTIVE one - its own, or the one it inherits - the rule
    every other coordinate here is read by. A build profile's properties apply only when
    that profile is active, and a property only an ancestor manifest defines is not in this
    file, so neither is read: a placeholder naming one stays unresolved. Nor is a plugin's
    `<configuration>`, which may hold a `<properties>` of its own that is no property of
    this project's, or a comment: read as the project's, either resolved a coordinate to a
    group nothing defines, and that id looks exactly like a real one.
    """
    text = uncommented(text)
    head = text.split("<dependencies>", 1)[0]
    parent = _PARENT_BLOCK.search(head)
    project = _hollow(head, _PARENT_BLOCK)
    values: dict[str, str] = {}
    block = _PROPERTIES.search(_hollow(_hollow(text, _PROFILES), _CONFIGURATION))
    if block:
        values.update((m.group(1), m.group(2)) for m in _PROPERTY.finditer(block.group(1)))
    own = {"groupId": effective_group(text), "artifactId": _tag("artifactId", project),
           "version": _tag("version", project)}
    inherited = ({name: _tag(name, parent.group(0))
                  for name in ("groupId", "artifactId", "version")} if parent else {})
    for prefix in ("project.", "pom."):
        values.update((prefix + k, v) for k, v in own.items() if v)
        values.update((f"{prefix}parent.{k}", v) for k, v in inherited.items() if v)
    return values


def resolve_placeholders(value: str, values: dict[str, str]) -> str:
    """`value` with every `${name}` that `values` defines substituted. A property may name
    another, so this repeats, to a bound; what nothing here defines is left as written, for
    the caller to see."""
    for _ in range(8):
        expanded = _PLACEHOLDER.sub(lambda m: values.get(m.group(1), m.group(0)), value)
        if expanded == value:
            break
        value = expanded
    return value


def _rel(repo: RepoRef, path: Path) -> str:
    try:
        return path.relative_to(repo.path).as_posix()
    except ValueError:
        return path.as_posix()


#: What each `kind` this module emits promises its reader beyond `UNRESOLVED_COMMON`.
#: Declared here rather than in core, so adding a stack never forces a core edit.
UNRESOLVED_FIELDS = {
    "unreadable-declaration": ("raw",),
    "unreadable-file": (),
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
                text, read_err = repo_walk.read_text_reporting(path)
                text = uncommented(text)
                if read_err:
                    res.unresolved.append({
                        "extractor": self.name, "kind": "unreadable-file",
                        "component": repo.alias, "source_file": rel, "line": 0,
                        "why": "this manifest could not be read, so every module it builds or "
                               "depends on is missing from the graph rather than absent from "
                               "the code"})
                    continue
                self._file(res, repo, path, rel, text, svc)
        return res

    def _module_coords(self, res: ExtractResult, repo: RepoRef, path: Path, rel: str,
                       text: str, fallback_group: str) -> set[tuple[str, str]]:
        """The coordinates this aggregator's `<module>` directories actually publish.

        A `<module>` names a directory; the child manifest names the artifact. Reading the
        child is the only way to know which, and a child that is not there is a gap to
        record rather than a name to guess.

        The child's group is read with `effective_group` — the same rule this manifest's own
        coordinates are read with — so a module that inherits its group from `<parent>`, the
        ordinary multi-module shape, resolves the way its own manifest does. A child that
        names no group anywhere takes `fallback_group`, the aggregator's own effective group:
        maven publishes nothing without a group, and the reactor's is the only one in scope.
        """
        coords: set[tuple[str, str]] = set()
        for name, line in modules(text):
            child = path.parent / Path(name.strip("/")) / "pom.xml"
            child_text = uncommented(repo_walk.read_text(child)) if child.is_file() else ""
            artifact = _tag("artifactId", _hollow(child_text.split("<dependencies>", 1)[0],
                                                  _PARENT_BLOCK)) if child_text else ""
            if artifact:
                coords.add((effective_group(child_text) or fallback_group, artifact))
                continue
            res.unresolved.append({
                "extractor": self.name, "component": repo.alias,
                "kind": "module-unresolved", "source_file": rel, "line": line,
                "module": name,
                "why": ("this aggregator builds a module whose own manifest could not be "
                        "read here, so which artifact it publishes is unknown and any "
                        "dependency on it cannot be told from a third-party one"),
            })
            # No guess from the directory name. A dependency spelled like this directory may
            # be the module and may be a third party's, and the two are told apart by the
            # manifest that is not here. Keeping such an edge costs a phantom self-dependency
            # inside one component, which is noise; dropping it costs a real cross-component
            # dependency, which is a missed impact nobody sees. The row above is the record.
        return coords

    def _file(self, res: ExtractResult, repo: RepoRef, path: Path, rel: str, text: str,
              svc: str) -> None:
        head = text.split("<dependencies>", 1)[0]
        project = _hollow(head, _PARENT_BLOCK)
        own_group = _tag("groupId", project)
        eff_group = effective_group(text)
        own_match = _tag_match("artifactId", project)
        own_artifact = own_match.group(1).strip() if own_match else ""
        own = own_coordinates(
            text, self._module_coords(res, repo, path, rel, text, eff_group))

        if own_artifact and own_match is not None:
            # The own module publishes under its EFFECTIVE group — its own, or the one it
            # inherits from <parent> — the rule `own_coordinates` already subtracts by and
            # every depender writes. Building this id from the DECLARED group instead resolved
            # a parent-inheriting module to `mod::artifact` while its dependers pointed at
            # `mod:<group>:artifact`: one module, two nodes, the split ids.py exists to stop.
            mine = ids.module_id(eff_group, own_artifact)
            res.nodes.append(Node(id=mine, type="mod",
                                  props={"group": eff_group, "artifact": own_artifact,
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

        values = placeholder_values(text)
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
            # T533: `${project.groupId}` is the usual spelling of an intra-reactor
            # dependency, and taken as written it minted a phantom module and lost the
            # join to the real one, with no row. Resolved against this manifest first;
            # what stays a placeholder is recorded, never minted into a node.
            group = resolve_placeholders(dep["group"], values)
            artifact = resolve_placeholders(dep["artifact"], values)
            if _PLACEHOLDER.search(group) or _PLACEHOLDER.search(artifact):
                res.unresolved.append({
                    "extractor": self.name, "component": repo.alias,
                    "kind": "unreadable-declaration", "source_file": rel,
                    "line": dep["line"], "raw": dep["decl"],
                    "why": ("this dependency names its coordinates through a placeholder "
                            "this manifest does not define - a property set in a parent "
                            "manifest or a build profile - so which module it means cannot "
                            "be read here; a module named after the placeholder would look "
                            "exactly like a third party's"),
                })
                continue
            if (group, artifact) in own:
                continue                    # the manifest's own coordinates, not a dependency
            target = ids.module_id(group, artifact)
            version = resolve_placeholders(dep["version"], values)
            res.nodes.append(Node(id=target, type="mod",
                                  props={"group": group, "artifact": artifact}))
            res.nodes.append(Node(id=svc, type="svc", props={"component": repo.alias}))
            res.edges.append(Edge(
                src=svc, type="DG_DEPENDS_ON", dst=target, source_file=rel, line=dep["line"],
                extractor=self.name, confidence=1.0,
                # A blank version is inherited from a parent or a managed set. Normal, not a
                # finding: reporting every one would bury the findings that matter.
                props={"version": version, "scope": dep["scope"],
                       "optional": dep["optional"],
                       "version_source": "declared" if dep["version"] else "inherited"}))


EXTRACTORS = (ModulesExtractor(),)
