"""Deployment packages: which logical service each declares, and the host map they publish.

Two facts live here that exist nowhere else in a workspace.

**The logical name of a service.** A directory is called one thing, an image another, and the
name other services actually reach it by is a third. The deployment package is where those
three are written down together, which makes it the only place the mapping can be read.

**That two packages can claim one service.** A legacy tree and a current one routinely
declare the same logical name. Detection can SEE that collision; it cannot know which variant
configuration and UI links should attach to, because that is a statement about intent rather
than about content — the two trees are equally real. So this extractor records both claims,
identically and without preference, and a later pass resolves them from the workspace's own
`[[canonical]]` declaration. **Guessing here would attach real edges to the wrong service
while looking entirely correct**, which is the failure mode that makes a graph confidently
useless rather than visibly broken.

## Why the node type is `deploy` and not the tool's name

A chart tree, a compose file and a set of manifests answer the same question. Naming the node
after one of them would make every adopter using a different one look like an exception, and
core would have to learn a vocabulary that belongs here.
"""
from __future__ import annotations

import os
import posixpath
import re
from collections.abc import Collection, Iterable
from pathlib import Path
from typing import Any

from tools.graph.core.lib import ids, props, repo_walk
from tools.graph.core.model import Edge, ExtractResult, Node, Provenance
from tools.graph.core.spi import ExtractContext, RepoRef
from tools.graph.extractors import _canonical, _yaml

#: Where the resolved host map lands for a later pass to join on.
CACHE_KEY = "hostmap"

#: Paths within the values document that name the logical service, in precedence order.
#: An explicit name beats an image path, because the image is a publishing detail and the
#: name is what other services address.
SERVICE_NAME_PATHS = ("service.name", "nameOverride", "fullnameOverride", "app.name")
IMAGE_PATHS = ("image.repository", "image.name")

#: Documents whose data block is a map of configuration keys.
CONFIG_KINDS = {"ConfigMap"}

#: A document declaring itself one of `CONFIG_KINDS`, matched as TEXT rather than as parsed
#: YAML. Used in exactly one place — deciding whether a file that FAILED to parse was one
#: this extractor would have read — and the justification is that by then there is nothing
#: else left to ask. See `_unreadable` for why the alternative is worse.
_DECLARES_CONFIG_KIND = re.compile(
    r"(?m)^\s*kind:\s*[\"']?(" + "|".join(sorted(CONFIG_KINDS)) + r")[\"']?\s*(?:#.*)?$")


def _rel(repo: RepoRef, path: Path) -> str:
    try:
        return path.relative_to(repo.path).as_posix()
    except ValueError:
        return path.as_posix()


def _flat(doc: Any) -> dict[str, Any]:
    return {k: v for k, v in props.walk_yaml(doc)} if isinstance(doc, (dict, list)) else {}


def logical_service(flat: dict[str, Any]) -> tuple[str, str]:
    """The service this package deploys, and which field said so.

    Falls back to the last segment of an image path, which is the convention when nothing
    declares a name outright. Returns ("", "") when neither is present — an unnamed package
    is a real state and inventing a name from the directory would fabricate identity.
    """
    for key in SERVICE_NAME_PATHS:
        value = flat.get(key)
        if isinstance(value, str) and value.strip() and _yaml.TOKEN not in value:
            return value.strip(), key
    for key in IMAGE_PATHS:
        value = flat.get(key)
        if isinstance(value, str) and value.strip() and _yaml.TOKEN not in value:
            return value.strip().rsplit("/", 1)[-1].split(":", 1)[0], key
    return "", ""



#: What each `kind` this module emits promises its reader beyond `UNRESOLVED_COMMON`.
#: Declared HERE rather than in core: a central table would make core learn every
#: extractor's vocabulary, so adding a stack would force a core edit. The pipeline
#: checks these after every run and fails the build when a row does not carry them.
#: The parsers this module's readers can lack (batch 16, the owner's ruling 30): a shard
#: records whether each was installed when it was built.
PARSERS = ("yaml",)

UNRESOLVED_FIELDS = {
    "unreadable-file": (),
    "grammar-unavailable": ('language',),
    "off-case-claim": ('claim',),
    "superseded-declaration": ('key', 'value'),
    "templated-value": ('raw',),
    "unnamed-deployment": (),
    "unparsable": ('error',),
    "yaml-typed-value": ('key', 'value', 'readings'),
}


def _parse_templated(text: str, *, all_docs: bool):
    """A Go-templated file, parsed by de-templating rather than rendering (item 87).

    139 of a real workspace's 143 unparsable rows were exactly this class: YAML
    skeletons under `templates/` whose `{{ ... }}` spans break the parse while the
    mapping around them — the host map, the literal majority of `data:` — is ordinary
    YAML. `_yaml.detemplate` is line-preserving, so every citation against the ORIGINAL
    text stays true; values a span passed through carry `_yaml.TOKEN` and are refused
    as literals by everything downstream. Returns (docs|doc, error)."""
    detext = _yaml.detemplate(text)
    try:
        if all_docs:
            return [d for d in props.yaml.safe_load_all(detext)], ""
        return props.yaml.safe_load(detext), ""
    except props.yaml.YAMLError as exc:
        return None, " ".join(str(exc).split())


#: What each helm reader loses when YAML cannot be parsed (batch 15b, D15b-61): the
#: deployment reader's sentence is `main`'s, word for word; the host-map reader also reads
#: values and `Chart` files to know which directories are charts.
DEPLOYMENTS_LOST = "Every deployment and address they declare is missing from the graph"
HOST_MAPS_LOST = ("Every address they publish, and which charts they make, are missing from "
                  "the graph")


def _no_yaml(extractor: str, repo: RepoRef, n_files: int, lost: str) -> dict:
    """One row per component when PyYAML is absent — the zero-dependency degradation.

    Shared by both extractors here: each reports its own loss under its own name and in its
    own words (`lost`), because a chart tree whose values files were skipped and one whose
    templates were skipped are different absences and the reader deserves to know which.
    """
    return {
        "extractor": extractor, "component": repo.alias, "kind": "grammar-unavailable",
        "source_file": "", "line": 0, "files": n_files, "language": "yaml",
        "why": (f"no parser is installed for yaml (PyYAML is absent), so {n_files} "
                f"file(s) were not read. {lost}, and this row is the only record of that"),
    }


#: Where a chart keeps its subcharts. One release deploys an umbrella chart and every chart
#: vendored under its `charts/`, so a claim on the umbrella is a claim on each of them.
SUBCHART_DIR = "charts"

#: A chart's own files in its root that a `[[canonical]]` claim may name it by, and the chart
#: rule itself: moved to `_canonical` (batch 15b), so the link pass reads a block by the
#: rule this reader ranks by, and imported back - both layers apply one function.
_CHART_FILE = _canonical.CHART_FILE
chart_root = _canonical.chart_root
claimed_chart = _canonical.claimed_chart
#: The same names, case folded: what a claim names a chart's file by only in another case.
_CHART_FILE_FOLDED = re.compile(_CHART_FILE.pattern, re.IGNORECASE)


def chart_roots(rel: str, packages: Collection[str] | None = None) -> list[str]:
    """Every chart a template ships in, its own first: the chart it belongs to, then each
    umbrella that vendors that chart under its `charts/`, outward. A subchart is deployed by
    its umbrella's release, so a claim naming the umbrella's `values.yaml` covers it too -
    a claim that stopped at the nearest chart let a superseded tree supply the subchart's
    live address in silence. A chart nested anywhere else is its own (T529).

    Given the `packages` the reader found (`package_dirs`), a directory counts as an
    umbrella only when it is itself a package, and the walk STOPS at the first that is not
    (batch 15b, B8-10): a root holding neither a `Chart.yaml` nor a values file naming a
    service is no chart, and a claim on a root file that is not there no longer reaches
    every chart under `charts/` (D15b-12, D15b-35). Omitted, the walk is `main`'s."""
    roots = [chart_root(rel)]
    while posixpath.basename(posixpath.dirname(roots[-1])) == SUBCHART_DIR:
        umbrella = posixpath.dirname(posixpath.dirname(roots[-1]))
        if packages is not None and umbrella not in packages:
            break
        roots.append(umbrella)
    return roots


#: The globs a chart's files answer to, by role (batch 15b): a file's role to a reader is
#: decided by which of ITS patterns match it, never by where it sits (D15b-110).
VALUES_GLOBS = _canonical.VALUES_FILES
CHART_GLOBS = _canonical.CHART_FILES
TEMPLATE_GLOBS = ("templates/*.yaml", "templates/*.yml")


def _matches(rel: str, globs: tuple[str, ...]) -> bool:
    return any(repo_walk.glob_match(rel, posixpath.basename(rel), g) for g in globs)


def _declares_library(doc: Any) -> bool:
    """Whether a parsed `Chart.yaml` declares a library chart (`type: library`), which helm
    never installs: it deploys nothing of its own (ruling 20; review B15b-13)."""
    return isinstance(doc, dict) and str(doc.get("type") or "").strip() == "library"


def _load_values(path: Path) -> tuple[Any, str]:
    """A values file's document, read as the deployment reader reads it: a templated file is
    read with its spans replaced, and a file of only comments is an empty document (T537)."""
    doc, error = props.load_yaml_reporting(path)
    if doc is None and error:
        text = props.read_text(path)
        if "{{" in text:
            doc, error = _parse_templated(text, all_docs=False)
    if doc is None and not error:
        doc = {}
    return doc, error


def package_dirs(repo: RepoRef, inputs: Iterable[Path]) -> frozenset[str]:
    """Each directory (component-relative, "" for the root) a helm reader counts as a
    PACKAGE: one holding a `Chart.yaml`/`Chart.yml`, or a values file whose `logical_service`
    names a service (decision 4). Both kinds count - a root values file naming a service is
    a deployment the reader reads, and narrowing to `Chart.yaml` alone lost its ranking in
    silence (D15b-35). A values file that does not parse makes its directory no package; it
    has its own `unread` node and row. Both readers call this, over their own inputs."""
    out: set[str] = set()
    for path in inputs:
        rel = _rel(repo, path)
        if _matches(rel, CHART_GLOBS):
            out.add(posixpath.dirname(rel))
        elif _matches(rel, VALUES_GLOBS):
            doc, error = _load_values(path)
            if not error and logical_service(_flat(doc))[0]:
                out.add(posixpath.dirname(rel))
    return frozenset(out)


def _holds_values_file(directory: Path) -> bool:
    """Whether a chart directory holds a values file IN ANY SPELLING (ruling 20, D15b-100) -
    a regular file, never a directory or a link, whose name folds to `values.yaml` or
    `values.yml` (D15b-126) - by a case-folded listing, never a path probe:
    `Path("values.yaml").exists()` answers True beside a `Values.yaml` on Windows and False
    on Linux, so a probe would decide this on the developer's machine and not on the CI gate
    (round 11). The listing is a filesystem answer outside this reader's inputs, and its
    freshness rests on the deployment readers' case record, which holds the same file in its
    own stamp entry (D15b-105). A directory that cannot be listed is never read as holding
    none: the reader could not look."""
    try:
        with os.scandir(directory) as entries:
            return any(_canonical.names_values_file(entry.name)
                       and entry.is_file(follow_symlinks=False) for entry in entries)
    except OSError:
        return True


def _folded_chart_root(rel: str) -> str:
    """`chart_root` with the `templates/` segment matched case-folded - for an entry of the
    case record ONLY (D15b-131): asked case-sensitively, `TEMPLATES/cm.yaml` answers its own
    directory, and a chart whose one template is lost to case went silently unreported."""
    parts = rel.strip("/").split("/")
    for i in range(len(parts) - 2, -1, -1):
        if parts[i].casefold() == "templates":
            return "/".join(parts[:i])
    return posixpath.dirname(rel.strip("/"))


def _is_folded_template(rel: str) -> bool:
    parts = rel.strip("/").split("/")
    return (parts[-1].casefold().endswith((".yaml", ".yml"))
            and any(part.casefold() == "templates" for part in parts[:-1]))


def off_case_claim(path: str) -> bool:
    """Whether a claimed path names a chart's own file only once case is ignored - a
    `templates/` directory, or a values file or `Chart.yaml`, spelled in another case.

    T576 (the owner's ruling A, 2026-10-02): `claimed_chart` matches those names as the
    reader reads them, case-sensitively on every platform (T531), so such a claim names no
    chart. That stays the rule; the claim is named in a row rather than designating
    nothing in silence."""
    if claimed_chart(path) is not None:
        return False
    parts = path.strip("/").split("/")
    return (any(part.casefold() == "templates" for part in parts[:-1])
            or bool(_CHART_FILE_FOLDED.fullmatch(parts[-1])))


class DeploymentsExtractor:
    """Deployment packages and the service each claims."""

    name = "helm-deployments"
    INPUT_GLOBS = VALUES_GLOBS + CHART_GLOBS + TEMPLATE_GLOBS
    #: Batch 15b: a deployment reader, reading its values and `Chart` files for claims, and a
    #: chart's YAML templates only to know whether the chart has any of its own (ruling 20) -
    #: so whether it has is under this reader's input digest (D15b-99).
    DEPLOYMENT_READER = True
    CLAIM_GLOBS = VALUES_GLOBS + CHART_GLOBS
    GLOB_PURPOSES = {glob: "chart-templates" for glob in TEMPLATE_GLOBS}

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        if not props.yaml_available():
            for repo in ctx.repos:
                # Only its claim files are lost deployments: a component whose one match is
                # a web page's YAML template is told nothing (D15b-117, D15b-127).
                claims = [p for p in ctx.inputs(repo)
                          if _matches(_rel(repo, p), self.CLAIM_GLOBS)]
                if claims:
                    res.unresolved.append(_no_yaml(self.name, repo, len(claims),
                                                   DEPLOYMENTS_LOST))
            return res
        # T534: a service node names a component only when one carries the name (D9-5), as
        # compose's claim on the same node does.
        aliases = ({str(c["name"]) for c in (ctx.config or {}).get("components") or []}
                   or {r.alias for r in ctx.repos})
        case_lost = (ctx.config or {}).get("deployment_off_case") or {}
        for repo in ctx.repos:
            inputs = ctx.inputs(repo)
            packages = package_dirs(repo, inputs)
            rels = [_rel(repo, p) for p in inputs]
            # The charts with YAML templates of their own: those this reader was handed, by
            # `chart_root`, and those a deployment reader missed by case (ruling 20).
            own = ({chart_root(rel) for rel in rels if _matches(rel, TEMPLATE_GLOBS)}
                   | {_folded_chart_root(rel) for rel in case_lost.get(repo.alias, [])
                      if _is_folded_template(rel)})
            # The library charts, read from each chart's own `Chart.yaml` - one of this
            # reader's inputs, so the answer is under its digest; one that does not parse
            # says nothing (review B15b-13).
            libraries = {posixpath.dirname(_rel(repo, p)) for p in inputs
                         if _matches(_rel(repo, p), CHART_GLOBS)
                         and _declares_library(props.load_yaml_reporting(p)[0])}
            for path in inputs:
                rel = _rel(repo, path)
                if _matches(rel, CHART_GLOBS):
                    self._chart_only(res, repo, path, packages, own)
                elif _matches(rel, VALUES_GLOBS):
                    self._file(res, repo, path, aliases, packages,
                               library=posixpath.dirname(rel) in libraries)
        return res

    def _unread(self, res: ExtractResult, repo: RepoRef, rel: str, kind: str,
                packages: frozenset[str]) -> None:
        """A claim file this reader was handed and could not read, parse or name (batch 15b,
        T573 decision 6): its deployment node anyway, carrying no claim, marked `unread` with
        the kind of the row that explains it, so the graph admits the gap."""
        res.nodes.append(Node(id=ids.deployment_id(repo.alias, rel), type="deploy", props={
            "path": rel, "component": repo.alias, "kind": "helm",
            "ships_in": chart_roots(rel, packages), "unread": kind}))

    def _chart_only(self, res: ExtractResult, repo: RepoRef, path: Path,
                    packages: frozenset[str], own: set[str]) -> None:
        """Ruling 20 (the owner: "a permanent solution"): a chart directory holding a
        `Chart.yaml` and no values file in ANY spelling is a deployment package whose service
        nothing the graph reads names - unless it deploys nothing of its own: a library chart
        (`type: library`), or one with no YAML templates of its own. Every other `Chart.yaml`
        is metadata beside a values file, read only where that values file is."""
        rel = _rel(repo, path)
        chart = posixpath.dirname(rel)
        if _holds_values_file(repo.path / chart if chart else repo.path) or chart not in own:
            return
        _, read_err = repo_walk.read_text_reporting(path)
        if read_err:
            res.unresolved.append({
                "extractor": self.name, "component": repo.alias, "kind": "unreadable-file",
                "source_file": rel, "line": 0,
                "why": ("this chart's `Chart.yaml` could not be read, so whether the chart is a "
                        "library is unknown; its templates deploy a service nothing the graph "
                        "reads names")})
            self._unread(res, repo, rel, "unreadable-file", packages)
            return
        doc, error = props.load_yaml_reporting(path)
        if doc is None and error:
            res.unresolved.append({
                "extractor": self.name, "component": repo.alias, "kind": "unparsable",
                "source_file": rel, "line": 0, "error": error,
                "why": ("this chart's `Chart.yaml` could not be parsed, so the deployment its "
                        "templates make is missing from the graph rather than merely unnamed")})
            self._unread(res, repo, rel, "unparsable", packages)
            return
        if _declares_library(doc):
            return
        res.unresolved.append({
            "extractor": self.name, "component": repo.alias, "kind": "unnamed-deployment",
            "source_file": rel, "line": 0,
            "why": ("this chart has no `values.yaml`, so nothing the graph reads names the "
                    "service its templates deploy; add one naming it (`fullnameOverride: "
                    "<name>`)")})
        self._unread(res, repo, rel, "unnamed-deployment", packages)

    def _file(self, res: ExtractResult, repo: RepoRef, path: Path, aliases: set[str],
              packages: frozenset[str], *, library: bool = False) -> None:
        """One values file. In a `library` chart it is no deployment package - helm never
        installs the chart - so, once read, it publishes no node, claim or row: read,
        declaring nothing (review B15b-13: its `unnamed-deployment` row's remedy published
        a claim). The rule withholds the claim, never the read: a file that cannot be read
        or parsed is lost whatever its chart, and keeps its row and `unread` node (review
        B15b-17: skipped outright, it vanished, or was named among the files read)."""
        rel = _rel(repo, path)
        _, read_err = repo_walk.read_text_reporting(path)
        if read_err:
            res.unresolved.append({
                "extractor": self.name, "component": repo.alias, "kind": "unreadable-file",
                "source_file": rel, "line": 0,
                "why": ("this file could not be read, so whether it is a deployment resource "
                        "is unknown; if it was, the service it declares is missing from the "
                        "graph rather than merely unnamed")})
            self._unread(res, repo, rel, "unreadable-file", packages)
            return
        # REPORTING, not the plain spelling. This reader emitted an `unparsable` row that
        # said a file could not be read and threw away the parser's account of WHY — the
        # asymmetry item 82 recorded next door, on the same kind, in the same package.
        # T537: YAML reads a file of only comments - a chart taking every default - as
        # nothing, with no error. That is a package that names no service, not one that
        # could not be parsed; it reaches `unnamed-deployment` below, its honest row.
        doc, error = _load_values(path)
        if error:
            res.unresolved.append({
                "extractor": self.name, "component": repo.alias, "kind": "unparsable",
                "source_file": rel, "line": 0, "error": error,
                "why": ("this deployment package could not be parsed, so the service it "
                        "declares is missing from the graph rather than merely unnamed"),
            })
            self._unread(res, repo, rel, "unparsable", packages)
            return
        if library:
            return

        flat = _flat(doc)
        name, source_field = logical_service(flat)
        node_id = ids.deployment_id(repo.alias, rel)
        text = props.read_text(path)
        # The line of the field that named the service — not of the block containing it.
        # Provenance that points at a block header is a citation nobody can check against
        # the claim, and the difference is invisible unless someone asserts the number.
        # `_yaml.key_line` anchors on the field's declaration: `props.find_line` needed the
        # exact substring `key: value`, so a name taken from an image path (`repository:
        # registry/x` -> `x`) or a quoted value (`name: "x"`) never matched and gave line 0.
        line = _yaml.key_line(text, source_field.rsplit('.', 1)[-1])[0] if name else 0

        # Batch 15b: `kind` and `ships_in` - the chart roots the file ships in, its own first
        # - are facts about this reader's own files, so the link pass can read a block by the
        # rule this reader ranks by (`_canonical.designates`); a node naming no service is
        # `unread`, so the graph says why it carries no claim (decision 6, ruling 21).
        res.nodes.append(Node(id=node_id, type="deploy", props={
            "path": rel, "component": repo.alias, "tree": rel.rsplit("/", 1)[0] if "/" in rel else "",
            "service": name, "declared_by": source_field,
            "image": str(flat.get("image.repository", "")), "tag": str(flat.get("image.tag", "")),
            "kind": "helm", "ships_in": chart_roots(rel, packages),
            **({} if name else {"unread": "unnamed-deployment"}),
        }))
        if not name:
            res.unresolved.append({
                "extractor": self.name, "component": repo.alias, "kind": "unnamed-deployment",
                "source_file": rel, "line": 0,
                "why": (f"nothing here names a service — none of {', '.join(SERVICE_NAME_PATHS)} "
                        f"nor an image path — so what this package deploys cannot be read. "
                        f"Naming it after its directory would fabricate identity"),
            })
            return

        target = ids.component_service_id(name)
        res.nodes.append(Node(id=target, type="svc",
                              props={"component": name if name in aliases else ""}))
        # Recorded as a claim, not as a decision. Two packages naming one service produce two
        # identical-shaped edges, and which is canonical is resolved later from the
        # workspace's own declaration.
        res.edges.append(Edge(src=node_id, type="DG_DEPLOYS", dst=target, source_file=rel,
                              line=line, extractor=self.name, confidence=1.0,
                              provenance=Provenance.EXTRACTED,
                              props={"service": name, "declared_by": source_field,
                                     "image": str(flat.get("image.repository", "")),
                                     "tag": str(flat.get("image.tag", ""))}))


class ConfigMapsExtractor:
    """The host map a deployment publishes — where service names become addresses."""

    name = "helm-configmaps"
    INPUT_GLOBS = TEMPLATE_GLOBS + VALUES_GLOBS + CHART_GLOBS
    #: Batch 15b: a deployment reader - the host maps it publishes are what the accounting
    #: rows judge a chart's addresses by - reading no file for a claim. It reads the values
    #: and `Chart` files only to know which directories are packages (decision 4), so that
    #: answer is under its input digest.
    DEPLOYMENT_READER = True
    GLOB_PURPOSES = {**{glob: "host-maps" for glob in TEMPLATE_GLOBS},
                     **{glob: "package-dirs" for glob in VALUES_GLOBS + CHART_GLOBS}}

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        if not props.yaml_available():
            for repo in ctx.repos:
                res.unresolved.append(_no_yaml(self.name, repo, len(ctx.inputs(repo)),
                                               HOST_MAPS_LOST))
            return res
        host_map: dict[str, dict] = ctx.cache.setdefault(CACHE_KEY, {})
        #: Per component, the keys the canonical chart tree has already claimed. Local to
        #: this run rather than stored in the map, which later passes read.
        owned: dict[str, set[str]] = {}
        #: Per component, what each address the map holds may be read as (T536), so two
        #: chart trees spelling one value differently are not called a stale deployment
        #: outright (review B10-4). Local for the same reason as `owned`.
        spelled: dict[str, dict[str, tuple[str, ...]]] = {}
        for repo in ctx.repos:
            # Once per claimed path - two blocks claiming one path, for two services, made two
            # rows (review B14-6) - and only by a component this reader has charts in. The
            # pipeline hands an extractor one claimed component with inputs at a time (T534),
            # so that second half guards a hand-built context alone (B14-13).
            named: set[str] = set()
            for claim in (_canonical.claims_for(ctx.config or {}, repo.alias)
                          if ctx.inputs(repo) else []):
                claimed = _canonical.claimed_path(claim, repo.alias)
                if claimed and claimed not in named and off_case_claim(claimed):
                    named.add(claimed)
                    res.unresolved.append({
                        "extractor": self.name, "component": repo.alias,
                        "kind": "off-case-claim", "source_file": claimed, "line": 0,
                        "claim": claimed,
                        "why": (f"the `[[canonical]]` claim names `{claimed}`, a chart's "
                                f"file only once case is ignored. Charts are read "
                                f"case-sensitively on every platform - `templates/`, "
                                f"`values*.yaml`, `Chart.yaml` - so it designates no chart, "
                                f"and the chart it meant is ranked as if this claim did not "
                                f"exist; spell the claim, and the file, as the reader does")})
            entries = host_map.setdefault(repo.alias, {})
            packages = package_dirs(repo, ctx.inputs(repo))
            # A file's role is decided by which pattern matches it (D15b-110): a template is
            # read as a host map, `templates/values.yaml` included; a values or `Chart` file
            # in a chart's root is read only for `package_dirs`.
            for path in sorted(ctx.inputs(repo)):
                if _matches(_rel(repo, path), TEMPLATE_GLOBS):
                    self._file(res, ctx, repo, path, entries,
                               owned.setdefault(repo.alias, set()),
                               spelled.setdefault(repo.alias, {}), packages)
        return res

    def _unreadable(self, res: ExtractResult, repo: RepoRef, rel: str, text: str,
                    error: str) -> None:
        """Record a file that declared itself a config map and could not be parsed.

        **Only the ones that meant to be config maps are reported, and that is a deliberate
        narrowing rather than the silence this fixes.** A deployment package's template
        directory holds mostly documents this extractor would never read even if they parsed
        perfectly — workloads, services, ingresses, policies. Reporting every unparsable file
        would announce that a reader could not read files it does not read, at a rate of
        hundreds per chart tree, and bury the handful that cost real edges. A report nobody
        finishes protects nothing, which is the same failure as a gate nobody keeps green.

        **Matching text in a file we have just admitted we cannot parse is the honest part of
        this, not a hack around it.** The question is not "what does this document say" — that
        is exactly what is unavailable — but "did this file intend to be one of ours", and
        intent survives in the literal `kind:` line even when the surrounding template does
        not survive a parser. When that line is itself templated the file is skipped, which
        is a known and bounded blind spot: it costs a row in a report, never an edge in the
        graph, because a document whose own kind is computed could not have been read anyway.
        """
        if not _DECLARES_CONFIG_KIND.search(text):
            return
        res.unresolved.append({
            "extractor": self.name, "component": repo.alias, "kind": "unparsable",
            "source_file": rel, "line": 0, "error": error,
            "why": ("this document declares itself a configuration map and could not be "
                    "parsed, so the addresses it publishes are missing from the graph rather "
                    "than merely unset — and a service reached by an address nobody recorded "
                    "looks exactly like a service nobody calls. A deployment package's "
                    "`templates/` are frequently not YAML: a templating language interpolates "
                    "into keys and values, and no YAML parser can read the result"),
        })

    def _file(self, res: ExtractResult, ctx: ExtractContext, repo: RepoRef, path: Path,
              entries: dict, owned: set[str], spelled: dict[str, tuple[str, ...]],
              packages: frozenset[str] = frozenset()) -> None:
        rel = _rel(repo, path)
        # Whether this chart tree is the one the workspace named as the owner. A claim names
        # `values.yaml` or the chart's directory, and the host map lives in `templates/`, so
        # the question is asked of the CHARTS this template ships in - its own and every
        # umbrella above it - see `_canonical`. Asked of the file, the claim reached
        # everything under its values file's directory, a superseded chart nested there
        # included (T529). Without this the map is plain last-wins, and a superseded tree
        # sorting later supplies live addresses: the same defect the second fixture found in
        # compose, where a file marked `legacy` won and a component was recorded as
        # publishing to a topic one version number off.
        roots = chart_roots(rel, packages)
        canonical = _canonical.owns_package(ctx.config, repo.alias, rel, roots, claimed_chart)
        _, read_err = repo_walk.read_text_reporting(path)
        if read_err:
            res.unresolved.append({
                "extractor": self.name, "component": repo.alias, "kind": "unreadable-file",
                "source_file": rel, "line": 0,
                "why": ("this file could not be read, so whether it is a configuration map is "
                        "unknown; if it was, the addresses it publishes are missing from the "
                        "graph rather than absent")})
            return
        text = props.read_text(path)
        # REPORTING, not the plain spelling: `load_yaml_all` answers `[]` for a file that
        # could not be parsed AND for one with nothing in it, so looping over it directly is
        # how this reader went silent over a whole chart tree (item 76).
        _, error = props.load_yaml_all_reporting(path)
        source = text
        if error and "{{" in text:
            _, error = _parse_templated(text, all_docs=True)
            source = _yaml.detemplate(text)
        if error:
            self._unreadable(res, repo, rel, text, error)
            return
        # A single file can hold several config documents separated by `---`. One id per FILE
        # gave them all the same id, so the second collided with the first and one was lost on
        # dedup. The first config document keeps the bare per-file id — a file with one
        # configmap is unchanged — and the rest are distinguished by their position among the
        # config documents this file declares.
        cfg_index = 0
        # Read as NODES, not as the values `safe_load` types (T536): a value is the text it
        # was written as - `1.10`, not `1.1`; `off`, not `False` - and a key's line is its
        # own node's, anchors followed to the declaration (T539's class). `detemplate` keeps
        # every line where it was, so a node's line is the original file's line too.
        y = props.yaml
        for doc in _yaml.document_nodes(source):
            kind = _yaml.mapping_get(doc, "kind")
            if not isinstance(kind, y.ScalarNode) or kind.value not in CONFIG_KINDS:
                continue
            data_key = next((k for k, _ in _yaml.mapping_pairs(doc) if k.value == "data"),
                            None)
            data = _yaml.mapping_get(doc, "data")
            if not isinstance(data, y.MappingNode):
                block = _yaml.constructed(data) if data is not None else None
                if block and _yaml.TOKEN in str(block):
                    # The whole `data:` block is a Go-template span, de-templated to a
                    # scalar — every key and address it would publish is unknowable here.
                    # A bare `continue` emitted nothing; `templated-value` covers a value,
                    # and a templated block is that value one level up, so it is the same
                    # row for the same reason rather than a silent drop.
                    dline = _yaml.line_of(data_key)
                    raw = text.splitlines()[dline - 1].strip()
                    res.unresolved.append({
                        "extractor": self.name, "kind": "templated-value",
                        "component": repo.alias, "source_file": rel, "line": dline,
                        "raw": raw,
                        "why": ("the entire `data:` block is written as a Go template a static "
                                "reader cannot render, so every key and address it would "
                                "publish is unknowable here, and this row is the only record "
                                "of that")})
                if block:
                    continue          # a shape no configmap's `data:` has
            cfg = ids.config_id(repo.alias, rel, "" if cfg_index == 0 else str(cfg_index))
            cfg_index += 1
            named = _yaml.mapping_get(_yaml.mapping_get(doc, "metadata"), "name")
            name = (_yaml.scalar_text(named) if isinstance(named, y.ScalarNode)
                    else "" if named is None else str(_yaml.constructed(named)))
            res.nodes.append(Node(id=cfg, type="cfg", props={
                "kind": "configmap", "path": rel, "component": repo.alias, "ships_in": roots,
                # A templated name is not a name — "" is the honest unnamed state.
                "name": "" if _yaml.TOKEN in name else name}))
            for key_node, value_node in _yaml.mapping_pairs(data):
                key = str(key_node.value)
                line = _yaml.line_of(key_node)
                if isinstance(value_node, y.ScalarNode):
                    value = _yaml.scalar_text(value_node)
                    readings = _yaml.typed_readings(value_node)
                else:
                    value, readings = str(_yaml.constructed(value_node)), ()
                if _yaml.TOKEN in value:
                    # The span the de-templating pass erased was THIS value. Publishing
                    # the token as an address would be believed; a row is the record.
                    raw = text.splitlines()[line - 1].strip()
                    res.unresolved.append({
                        "extractor": self.name, "kind": "templated-value",
                        "component": repo.alias, "source_file": rel, "line": line,
                        "raw": raw,
                        "why": (f"`{key}`'s value is written as a Go template, which a "
                                f"static reader cannot render — the key is real, its "
                                f"address is not knowable here, and this row is the "
                                f"only record of that")})
                    continue
                node = ids.env_key_id(key)
                res.nodes.append(Node(id=node, type="envkey",
                                      props={"key": ids.relaxed_key(key)}))
                res.edges.append(Edge(src=cfg, type="DG_CONFIGURES", dst=node,
                                      source_file=rel, line=line, extractor=self.name,
                                      confidence=1.0,
                                      props={"declared_as": key, "value": value}))
                if readings:
                    # T536: recorded as written, and said once what else it may arrive as.
                    res.unresolved.append({
                        "extractor": self.name, "kind": "yaml-typed-value",
                        "component": repo.alias, "key": key, "value": value,
                        "readings": list(readings), "source_file": rel, "line": line,
                        "why": _yaml.typed_value_why(key, value, readings)})
                relaxed = ids.relaxed_key(key)
                entry = {"value": value, "file": rel, "line": line, "declared_as": key}
                held = entries.get(relaxed)
                if held is not None and relaxed in owned and not canonical:
                    # The owner has already published this address. Keep it, and report the
                    # loser rather than discarding it — a stale chart tree still supplying
                    # what looks like a live host is a finding, not noise.
                    if held["value"] != entry["value"]:
                        maybe_same = _yaml.may_agree(held["value"], spelled.get(relaxed, ()),
                                                     value, readings)
                        res.unresolved.append({
                            "extractor": self.name, "kind": "superseded-declaration",
                            "component": repo.alias, "key": relaxed, "source_file": rel,
                            "line": line, "value": entry["value"],
                            "why": (f"`{key}` is {entry['value']!r} here and {held['value']!r} "
                                    f"in {held['file']}, which the workspace names as the "
                                    f"owner. The owner's value is what the graph uses; this "
                                    f"one is reported because " + (
                                        "the two are spelled differently and may be one "
                                        "value or two, depending on the tool that reads them"
                                        if maybe_same else
                                        "a stale deployment nobody has noticed is a finding, "
                                        "not noise"))})
                    continue
                entries[relaxed] = entry
                spelled[relaxed] = readings
                if canonical:
                    owned.add(relaxed)


EXTRACTORS = (DeploymentsExtractor(), ConfigMapsExtractor())
