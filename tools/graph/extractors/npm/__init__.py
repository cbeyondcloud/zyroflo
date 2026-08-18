"""npm manifests: the dependency layer of the JavaScript world, joined by registry name.

Third manifest format, same module graph. A pom joins by groupId:artifactId, a gradle
script by the same coordinates, and a package.json by NAME — because npm names are
registry-global, `jest` names one package everywhere, and two components depending on it
must meet at one `mod:` node. That global merge is the deliberate INVERSE of the gradle
ruling: a gradle positional name is a directory accident and must stay component-scoped,
while an npm name is a claim on a shared registry, which is exactly what a join needs.
A scoped name (`@acme/ui-kit`) splits at the slash — the scope takes the group's slot.

## What a manifest declares, this reads; what it implies, it does not

`dependencies`, `devDependencies`, `peerDependencies` and `optionalDependencies` are
read, each entry cited at its own key's line, with the section name as the edge's scope —
a devDependency is still a rebuild trigger, which is the question this layer answers.
The SPEC STRING IS KEPT RAW: `^3.6.0` declares a range, and rewriting it to a version
would be inventing precision the manifest never claimed. `file:`/`link:` specs keep
their edge too — the dependency is declared by name either way. What is NOT read:
`scripts`, `resolutions`/`overrides` (constraint patches, not declarations), and
lockfiles — the manifest is the declaration of intent, the lockfile its solution.

## The subtraction, third spelling of the same trap

Every name any of a component's manifests declares is subtracted from that component's
dependencies: a monorepo workspace package consumed by its sibling is intra-component
structure (the maven "eight components consuming their own submodules" trap, npm
spelling). A name declared in ANOTHER component survives — that surviving edge IS the
cross-component join, and on real estates it is most of the value.

A manifest with no `name` publishes nothing a registry could hold — its dependencies
still belong to the component's service, and no module node is invented for it. A
`private` package is the opposite case: private hides it from a registry, not from the
graph, and its workspace consumers are precisely who "who rebuilds?" must find.
"""
from __future__ import annotations

import json as json_lib
import re
from pathlib import Path

from tools.graph.core.lib import ids, repo_walk
from tools.graph.core.model import Edge, ExtractResult, Node
from tools.graph.core.spi import ExtractContext, RepoRef

#: Sections read as dependency declarations, in the order they are searched.
_SECTIONS = ("dependencies", "devDependencies", "peerDependencies",
             "optionalDependencies")


def _rel(repo: RepoRef, path: Path) -> str:
    try:
        return path.relative_to(repo.path).as_posix()
    except ValueError:
        return path.as_posix()


def split_name(name: str) -> tuple[str, str]:
    """`@scope/pkg` → (scope, pkg); an unscoped name keeps the scope slot empty."""
    if name.startswith("@") and "/" in name:
        scope, bare = name[1:].split("/", 1)
        return scope, bare
    return "", name


#: What each `kind` this module emits promises its reader beyond `UNRESOLVED_COMMON`.
#: Declared here rather than in core, so adding a stack never forces a core edit.
UNRESOLVED_FIELDS = {
    "unparsable": ("error",),
}


class NpmModulesExtractor:
    """Modules npm manifests build and the dependencies between them, by registry name."""

    name = "npm-modules"
    INPUT_GLOBS = ("package.json",)

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        for repo in ctx.repos:
            self._repo(res, repo, sorted(ctx.inputs(repo)))
        return res

    def _repo(self, res: ExtractResult, repo: RepoRef, paths: list[Path]) -> None:
        manifests: list[tuple[str, dict, str]] = []
        for path in paths:
            rel = _rel(repo, path)
            if repo_walk.looks_like_test(rel):
                continue
            text = repo_walk.read_text(path)
            try:
                doc = json_lib.loads(text)
            except ValueError as exc:
                res.unresolved.append({
                    "extractor": self.name, "component": repo.alias,
                    "kind": "unparsable", "source_file": rel, "line": 0,
                    "error": " ".join(str(exc).split()),
                    "why": ("this manifest could not be read, so every module it builds "
                            "or depends on is missing from the graph and this row is "
                            "the only record of that"),
                })
                continue
            if isinstance(doc, dict):
                manifests.append((rel, doc, text))

        own = {n for _, doc, _ in manifests
               if isinstance(n := doc.get("name"), str) and n.strip()}

        svc = ids.component_service_id(repo.alias)
        for rel, doc, text in manifests:
            self._emit(res, repo, svc, rel, doc, text, own)

    def _emit(self, res: ExtractResult, repo: RepoRef, svc: str, rel: str,
              doc: dict, text: str, own: set[str]) -> None:
        res.nodes.append(Node(id=svc, type="svc", props={"component": repo.alias}))
        name = doc.get("name")
        if isinstance(name, str) and name.strip():
            scope, bare = split_name(name.strip())
            mine = ids.module_id(scope, bare)
            line, _ = _key_line(text, "name", 0)
            res.nodes.append(Node(id=mine, type="mod",
                                  props={"group": scope, "artifact": bare,
                                         "version": str(doc.get("version", ""))}))
            res.edges.append(Edge(src=svc, type="DG_BUILT_FROM", dst=mine,
                                  source_file=rel, line=line, extractor=self.name))

        for section in _SECTIONS:
            deps = doc.get(section)
            if not isinstance(deps, dict):
                continue
            offset = _key_line(text, section, 0)[1]
            for dep_name, spec in deps.items():
                line, offset = _key_line(text, dep_name, offset)
                if dep_name in own:
                    continue        # a workspace sibling, not a dependency
                scope, bare = split_name(dep_name)
                target = ids.module_id(scope, bare)
                res.nodes.append(Node(id=target, type="mod",
                                      props={"group": scope, "artifact": bare}))
                res.edges.append(Edge(
                    src=svc, type="DG_DEPENDS_ON", dst=target, source_file=rel,
                    line=line, extractor=self.name, confidence=1.0,
                    props={"version": spec if isinstance(spec, str) else "",
                           "scope": section, "version_source": "declared"}))


def _key_line(text: str, key: str, start: int) -> tuple[int, int]:
    """JSON keys are quoted, so the shared anchored scan runs on the quoted spelling —
    with an unanchored fallback for inline sections (`"deps": {"a": "1"}` on one line),
    where the key is real but sits mid-line and the anchored scan would cite line 0,
    losing provenance silently. `"key":` is specific enough in JSON: a spec string
    containing a quoted-and-coloned key does not occur in the notation."""
    from tools.graph.extractors import _yaml
    line, offset = _yaml.key_line(text, f'"{key}"', start)
    if line:
        return line, offset
    m = re.search(re.escape(f'"{key}"') + r"[ \t]*:", text[start:])
    if m is None:
        return 0, start
    return text.count("\n", 0, start + m.start()) + 1, start + m.end()


EXTRACTORS = (NpmModulesExtractor(),)
