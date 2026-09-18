"""Gradle builds: the same module graph maven answers, from a script instead of a schema.

The join is coordinates, not file format. A gradle manifest naming
`com.example.shop:shop-shared:2.1.0` must land on the SAME `mod:` node a pom naming those
coordinates produced, or "if this library changes, who rebuilds?" silently splits by build
tool — the one question this layer exists to answer, broken exactly where an estate mixes
build systems.

## Groovy is code; this reads declarations, never evaluates them

A build.gradle is a program. This extractor takes declaration lines at face value —
`group = '...'`, `include ':x'`, `implementation 'g:a:v'` — and refuses to be an
interpreter. The honest consequences, each recorded rather than guessed at:

* a dependency declared through an expression (`implementation libs.core`, a version
  catalog, an ext-block variable) names a module this reader cannot identify — rowed as
  `unreadable-declaration`, one per line, because the loss is per-module;
* a `$variable` VERSION on a readable coordinate keeps the module join (identity is
  group:artifact) and marks `version_source: "variable"` with the spelling preserved;
* a conditional or loop-built declaration reads as whatever its text says. Line numbers
  matter more than evaluation fidelity here, for the same reason maven's reader chose
  patterns over a schema-strict parse.

## Identity is positional where nothing declares it

Gradle's module name comes from `settings.gradle` (`rootProject.name`, `include ':x'`)
or, failing that, from the directory — nothing inside a subproject's own build.gradle
names it. And a manifest that declares no `group` publishes nothing another build could
name by coordinates, so its module node is SCOPED TO THE COMPONENT (the alias takes the
group's slot in the id): without that, every Android wrapper's `:app` module in every
component would merge into one node — a join nobody declared, in the layer whose whole
value is that joins are declarations.

## The subtraction, same trap as maven

A build names its own modules before anyone else's. Everything a component's builds
declare — settings includes, root names, module directories — is subtracted from its
dependencies, so a dependency on a module the component itself builds emits nothing:
that is intra-component structure, and the component already rebuilds as one service.
`project(':x')` references are same-build siblings BY DEFINITION and need no lookup.
`classpath` lines configure the build tool, not the built artifact (maven's reader
ignores `<plugin>` blocks for the same reason), and `fileTree`/`files` dependencies are
jars on disk with no module identity — there is no producer they could ever join.
"""
from __future__ import annotations

import re
from pathlib import Path

from tools.graph.core.lib import ids, repo_walk
from tools.graph.core.model import Edge, ExtractResult, Node
from tools.graph.core.spi import ExtractContext, RepoRef

#: Dependency configurations read as declarations. `classpath` is deliberately absent.
_CONFIGS = (
    "implementation", "api", "compile", "compileOnly", "compileOnlyApi", "runtimeOnly",
    "runtime", "testImplementation", "testCompile", "testCompileOnly", "testRuntimeOnly",
    "testRuntime", "androidTestImplementation", "androidTestCompile",
    "annotationProcessor", "kapt", "ksp", "providedCompile", "providedRuntime",
)
_CONFIG = rf"(?:{'|'.join(_CONFIGS)})"

_ASSIGN_GROUP = re.compile(r"^\s*group\s*[=\s]\s*['\"]([^'\"]+)['\"]", re.M)
_ASSIGN_VERSION = re.compile(r"^\s*version\s*[=\s]\s*['\"]([^'\"]+)['\"]", re.M)
_ROOT_NAME = re.compile(r"^\s*rootProject\.name\s*=\s*['\"]([^'\"]+)['\"]", re.M)
#: `include\b` cannot match `includeBuild(...)` — no word boundary splits the two.
_INCLUDE_LINE = re.compile(r"^\s*include\b(.*)$", re.M)
_QUOTED = re.compile(r"['\"]([^'\"]+)['\"]")

_DEP_LINE = re.compile(rf"^\s*({_CONFIG})\b\s*(.*)$")
#: String notation, optionally parenthesised, optionally wrapped in a BOM import:
#: `implementation 'g:a:v'`, `testImplementation(platform("g:a:v"))`.
_DEP_STRING = re.compile(
    r"^\(?\s*(?:(?:enforcedPlatform|platform)\s*\(\s*)?['\"]([^'\"]+:[^'\"]+)['\"]")
_DEP_MAP = re.compile(r"^\(?\s*(?=.*\bgroup\s*:)")
_MAP_PAIR = re.compile(r"\b(\w+)\s*:\s*['\"]([^'\"]+)['\"]")
_DEP_LOCAL = re.compile(r"^\(?\s*(?:project|fileTree|files)\s*\(")


def _rel(repo: RepoRef, path: Path) -> str:
    try:
        return path.relative_to(repo.path).as_posix()
    except ValueError:
        return path.as_posix()


def parse_settings(text: str) -> dict:
    """`rootProject.name` and the last segment of every `include`d module path."""
    names = []
    for m in _INCLUDE_LINE.finditer(text):
        names += [q.strip(":").split(":")[-1] for q in _QUOTED.findall(m.group(1))]
    root = _ROOT_NAME.search(text)
    return {"root_name": root.group(1) if root else "", "includes": names}


def dependencies(text: str) -> list[dict]:
    """Every declaration line a configuration keyword opens, classified.

    Yields either a coordinate (`group`/`artifact`/`version`/`scope`/`line`), a local
    reference (`local: True` — project()/fileTree()/files(), which by definition name
    nothing outside this build), or an unreadable expression (`raw`) for the row.
    """
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        m = _DEP_LINE.match(line)
        if not m:
            continue
        scope, tail = m.group(1), m.group(2)
        if s := _DEP_STRING.match(tail):
            parts = s.group(1).split("@")[0].split(":")
            out.append({"group": parts[0], "artifact": parts[1],
                        "version": parts[2] if len(parts) > 2 else "",
                        "scope": scope, "line": n})
        elif _DEP_MAP.match(tail):
            pairs = dict(_MAP_PAIR.findall(tail))
            if pairs.get("group") and pairs.get("name"):
                out.append({"group": pairs["group"], "artifact": pairs["name"],
                            "version": pairs.get("version", ""),
                            "scope": scope, "line": n})
            else:
                out.append({"raw": line.strip(), "scope": scope, "line": n})
        elif _DEP_LOCAL.match(tail):
            out.append({"local": True, "scope": scope, "line": n})
        elif not tail or tail in ("(", "{"):
            continue        # a bare `implementation(` opening a continuation is nothing yet
        else:
            out.append({"raw": line.strip(), "scope": scope, "line": n})
    return out


#: What each `kind` this module emits promises its reader beyond `UNRESOLVED_COMMON`.
#: Declared here rather than in core, so adding a stack never forces a core edit.
UNRESOLVED_FIELDS = {
    "unreadable-declaration": ("raw",),
    "unreadable-file": (),
}


class GradleModulesExtractor:
    """Build modules and the dependencies between them, read from Groovy declarations."""

    name = "gradle-modules"
    INPUT_GLOBS = ("build.gradle", "settings.gradle")

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        for repo in ctx.repos:
            self._repo(res, repo, sorted(ctx.inputs(repo)))
        return res

    def _repo(self, res: ExtractResult, repo: RepoRef, paths: list[Path]) -> None:
        settings: dict[str, dict] = {}
        builds: list[tuple[str, str, str]] = []
        for path in paths:
            rel = _rel(repo, path)
            if repo_walk.looks_like_test(rel):
                continue
            text, read_err = repo_walk.read_text_reporting(path)
            if read_err:
                res.unresolved.append({
                    "extractor": self.name, "kind": "unreadable-file", "component": repo.alias,
                    "source_file": rel, "line": 0,
                    "why": "this build file could not be read, so any module or dependency it "
                           "declares is missing from the graph rather than absent from the code"})
                continue
            dir_rel = rel.rsplit("/", 1)[0] if "/" in rel else ""
            if path.name == "settings.gradle":
                settings[dir_rel] = parse_settings(text)
            else:
                builds.append((rel, dir_rel, text))
        if not builds:
            return

        modules = [self._identity(repo, rel, dir_rel, text, settings, builds)
                   for rel, dir_rel, text in builds]

        # Everything this component's builds declare as their own. A dependency on any
        # of it is intra-component structure wearing a dependency's clothes.
        own = {(m["group"], m["name"]) for m in modules}
        for dir_rel, st in settings.items():
            root_group = next((_ASSIGN_GROUP.search(t).group(1) for r, d, t in builds
                               if d == dir_rel and _ASSIGN_GROUP.search(t)), "")
            own |= {(root_group, n) for n in st["includes"]}
            if st["root_name"]:
                own.add((root_group, st["root_name"]))

        svc = ids.component_service_id(repo.alias)
        for m in modules:
            self._emit(res, repo, svc, m, own)

    def _identity(self, repo: RepoRef, rel: str, dir_rel: str, text: str,
                  settings: dict[str, dict], builds: list) -> dict:
        """Who this manifest builds: name positionally, group and version by inheritance.

        Gradle's own defaulting rules, taken at declaration level: a subproject inherits
        `group`/`version` from its build root (the manifest beside the governing
        settings.gradle), and its name is its directory unless `rootProject.name` says
        otherwise — nothing INSIDE the file names it.
        """
        parts = dir_rel.split("/") if dir_rel else []
        build_root = next(("/".join(parts[:i]) for i in range(len(parts), -1, -1)
                           if "/".join(parts[:i]) in settings), None)

        g = _ASSIGN_GROUP.search(text)
        group, group_line = (g.group(1), text.count("\n", 0, g.start()) + 1) if g else ("", 0)
        v = _ASSIGN_VERSION.search(text)
        version = v.group(1) if v else ""
        if build_root is not None and dir_rel != build_root:
            root_text = next((t for r, d, t in builds if d == build_root), "")
            if not group and (rg := _ASSIGN_GROUP.search(root_text)):
                group = rg.group(1)
            if not version and (rv := _ASSIGN_VERSION.search(root_text)):
                version = rv.group(1)

        name = ""
        if dir_rel == build_root:
            name = settings[build_root]["root_name"]
        name = name or (dir_rel.rsplit("/", 1)[-1] if dir_rel else repo.alias)
        return {"rel": rel, "name": name, "group": group, "group_line": group_line,
                "version": version, "text": text}

    def _emit(self, res: ExtractResult, repo: RepoRef, svc: str, m: dict,
              own: set[tuple[str, str]]) -> None:
        # A group-less module is unpublishable by coordinates — nothing outside this
        # component can legitimately depend on it — so the component's alias takes the
        # group's slot and the node stays scoped instead of merging across components.
        mine = ids.module_id(m["group"] or repo.alias, m["name"])
        res.nodes.append(Node(id=mine, type="mod",
                              props={"group": m["group"], "artifact": m["name"],
                                     "version": m["version"]}))
        res.nodes.append(Node(id=svc, type="svc", props={"component": repo.alias}))
        res.edges.append(Edge(src=svc, type="DG_BUILT_FROM", dst=mine,
                              source_file=m["rel"], line=m["group_line"],
                              extractor=self.name))

        for dep in dependencies(m["text"]):
            if dep.get("local"):
                continue
            if "raw" in dep:
                res.unresolved.append({
                    "extractor": self.name, "component": repo.alias,
                    "kind": "unreadable-declaration", "source_file": m["rel"],
                    "line": dep["line"], "raw": dep["raw"],
                    "why": ("this dependency is declared through an expression (a "
                            "version catalog, a variable, an ext block) that a "
                            "declaration reader cannot evaluate, so the module it names "
                            "is missing from the graph and this row is the only record "
                            "of that"),
                })
                continue
            if (dep["group"], dep["artifact"]) in own:
                continue        # the build's own module, not a dependency
            target = ids.module_id(dep["group"], dep["artifact"])
            res.nodes.append(Node(id=target, type="mod",
                                  props={"group": dep["group"],
                                         "artifact": dep["artifact"]}))
            res.edges.append(Edge(
                src=svc, type="DG_DEPENDS_ON", dst=target, source_file=m["rel"],
                line=dep["line"], extractor=self.name, confidence=1.0,
                props={"version": dep["version"], "scope": dep["scope"],
                       "version_source": ("variable" if "$" in dep["version"]
                                          else "declared" if dep["version"]
                                          else "inherited")}))


EXTRACTORS = (GradleModulesExtractor(),)
