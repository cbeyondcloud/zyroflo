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

import re
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
UNRESOLVED_FIELDS = {
    "unreadable-file": (),
    "grammar-unavailable": ('language',),
    "superseded-declaration": ('key', 'value'),
    "templated-value": ('raw',),
    "unnamed-deployment": (),
    "unparsable": ('error',),
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


def _no_yaml(extractor: str, repo: RepoRef, n_files: int) -> dict:
    """One row per component when PyYAML is absent — the zero-dependency degradation.

    Shared by both extractors here: each reports its own loss under its own name, because
    a chart tree whose values files were skipped and one whose templates were skipped are
    different absences and the reader deserves to know which.
    """
    return {
        "extractor": extractor, "component": repo.alias, "kind": "grammar-unavailable",
        "source_file": "", "line": 0, "files": n_files, "language": "yaml",
        "why": (f"no parser is installed for yaml (PyYAML is absent), so {n_files} "
                f"file(s) were not read. Every deployment and address they declare is "
                f"missing from the graph, and this row is the only record of that"),
    }

class DeploymentsExtractor:
    """Deployment packages and the service each claims."""

    name = "helm-deployments"
    INPUT_GLOBS = ("values.yaml", "values.yml", "Chart.yaml", "Chart.yml")

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        if not props.yaml_available():
            for repo in ctx.repos:
                res.unresolved.append(_no_yaml(self.name, repo, len(ctx.inputs(repo))))
            return res
        for repo in ctx.repos:
            for path in ctx.inputs(repo):
                if path.name.lower().startswith("chart."):
                    continue        # metadata, not values; read only for a name it may carry
                self._file(res, repo, path)
        return res

    def _file(self, res: ExtractResult, repo: RepoRef, path: Path) -> None:
        rel = _rel(repo, path)
        _, read_err = repo_walk.read_text_reporting(path)
        if read_err:
            res.unresolved.append({
                "extractor": self.name, "component": repo.alias, "kind": "unreadable-file",
                "source_file": rel, "line": 0,
                "why": ("this file could not be read, so whether it is a deployment resource "
                        "is unknown; if it was, the service it declares is missing from the "
                        "graph rather than merely unnamed")})
            return
        # REPORTING, not the plain spelling. This reader emitted an `unparsable` row that
        # said a file could not be read and threw away the parser's account of WHY — the
        # asymmetry item 82 recorded next door, on the same kind, in the same package.
        doc, error = props.load_yaml_reporting(path)
        if doc is None and error:
            text = props.read_text(path)
            if "{{" in text:
                doc, error = _parse_templated(text, all_docs=False)
        if doc is None:
            res.unresolved.append({
                "extractor": self.name, "component": repo.alias, "kind": "unparsable",
                "source_file": rel, "line": 0, "error": error,
                "why": ("this deployment package could not be parsed, so the service it "
                        "declares is missing from the graph rather than merely unnamed"),
            })
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

        res.nodes.append(Node(id=node_id, type="deploy", props={
            "path": rel, "component": repo.alias, "tree": rel.rsplit("/", 1)[0] if "/" in rel else "",
            "service": name, "declared_by": source_field,
            "image": str(flat.get("image.repository", "")), "tag": str(flat.get("image.tag", "")),
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
        res.nodes.append(Node(id=target, type="svc", props={"component": name}))
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
    INPUT_GLOBS = ("templates/*.yaml", "templates/*.yml")

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        if not props.yaml_available():
            for repo in ctx.repos:
                res.unresolved.append(_no_yaml(self.name, repo, len(ctx.inputs(repo))))
            return res
        host_map: dict[str, dict] = ctx.cache.setdefault(CACHE_KEY, {})
        #: Per component, the keys the canonical chart tree has already claimed. Local to
        #: this run rather than stored in the map, which later passes read.
        owned: dict[str, set[str]] = {}
        for repo in ctx.repos:
            entries = host_map.setdefault(repo.alias, {})
            for path in sorted(ctx.inputs(repo)):
                self._file(res, ctx, repo, path, entries,
                           owned.setdefault(repo.alias, set()))
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
              entries: dict, owned: set[str]) -> None:
        rel = _rel(repo, path)
        # Whether this chart tree is the one the workspace named as the owner. A claim names
        # `values.yaml`, and the host map lives in `templates/` beside it, so `path` covers
        # its subtree — see `_canonical`. Without this the map is plain last-wins, and a
        # superseded tree sorting later supplies live addresses: the same defect the second
        # fixture found in compose, where a file marked `legacy` won and a component was
        # recorded as publishing to a topic one version number off.
        canonical = _canonical.is_owner(ctx.config, repo.alias, rel)
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
        docs, error = props.load_yaml_all_reporting(path)
        if error and "{{" in text:
            docs, error = _parse_templated(text, all_docs=True)
            docs = docs or []
        if error:
            self._unreadable(res, repo, rel, text, error)
            return
        # Advances across the WHOLE file, documents included. One `---` file holding two
        # configmaps that declare the same key is ordinary, and a cursor reset per document
        # gives both of them the first one's line — which is the very defect being fixed,
        # reintroduced one loop out. Caught by its own test rather than by reading.
        cursor = 0
        # A single file can hold several config documents separated by `---`. One id per FILE
        # gave them all the same id, so the second collided with the first and one was lost on
        # dedup. The first config document keeps the bare per-file id — a file with one
        # configmap is unchanged — and the rest are distinguished by their position among the
        # config documents this file declares.
        cfg_index = 0
        for doc in docs:
            if not isinstance(doc, dict) or doc.get("kind") not in CONFIG_KINDS:
                continue
            data = doc.get("data") or {}
            if not isinstance(data, dict):
                if _yaml.TOKEN in str(data):
                    # The whole `data:` block is a Go-template span, de-templated to a
                    # scalar — every key and address it would publish is unknowable here.
                    # A bare `continue` emitted nothing; `templated-value` covers a value,
                    # and a templated block is that value one level up, so it is the same
                    # row for the same reason rather than a silent drop.
                    dline, cursor = _yaml.key_line(text, "data", cursor)
                    raw = text.splitlines()[dline - 1].strip() if dline else "data:"
                    res.unresolved.append({
                        "extractor": self.name, "kind": "templated-value",
                        "component": repo.alias, "source_file": rel, "line": dline,
                        "raw": raw,
                        "why": ("the entire `data:` block is written as a Go template a static "
                                "reader cannot render, so every key and address it would "
                                "publish is unknowable here, and this row is the only record "
                                "of that")})
                continue
            cfg = ids.config_id(repo.alias, rel, "" if cfg_index == 0 else str(cfg_index))
            cfg_index += 1
            name = str((doc.get("metadata") or {}).get("name", ""))
            res.nodes.append(Node(id=cfg, type="cfg", props={
                "kind": "configmap", "path": rel, "component": repo.alias,
                # A templated name is not a name — "" is the honest unnamed state.
                "name": "" if _yaml.TOKEN in name else name}))
            # Scanned forward, key by key, for the same two reasons the compose reader is:
            # `props.find_line` is a substring search and matches a key's name inside an
            # EARLIER value, and restarting from the top would give two configmaps declaring
            # one key the first one's line for both.
            for key, value in data.items():
                line, cursor = _yaml.key_line(text, str(key), cursor)
                if _yaml.TOKEN in str(value):
                    # The span the de-templating pass erased was THIS value. Publishing
                    # the token as an address would be believed; a row is the record.
                    raw = (text.splitlines()[line - 1].strip() if line else str(value))
                    res.unresolved.append({
                        "extractor": self.name, "kind": "templated-value",
                        "component": repo.alias, "source_file": rel, "line": line,
                        "raw": raw,
                        "why": (f"`{key}`'s value is written as a Go template, which a "
                                f"static reader cannot render — the key is real, its "
                                f"address is not knowable here, and this row is the "
                                f"only record of that")})
                    continue
                node = ids.env_key_id(str(key))
                res.nodes.append(Node(id=node, type="envkey",
                                      props={"key": ids.relaxed_key(str(key))}))
                res.edges.append(Edge(src=cfg, type="DG_CONFIGURES", dst=node,
                                      source_file=rel, line=line, extractor=self.name,
                                      confidence=1.0,
                                      props={"declared_as": str(key), "value": str(value)}))
                relaxed = ids.relaxed_key(str(key))
                entry = {"value": str(value), "file": rel, "line": line,
                         "declared_as": str(key)}
                held = entries.get(relaxed)
                if held is not None and relaxed in owned and not canonical:
                    # The owner has already published this address. Keep it, and report the
                    # loser rather than discarding it — a stale chart tree still supplying
                    # what looks like a live host is a finding, not noise.
                    if held["value"] != entry["value"]:
                        res.unresolved.append({
                            "extractor": self.name, "kind": "superseded-declaration",
                            "component": repo.alias, "key": relaxed, "source_file": rel,
                            "line": line, "value": entry["value"],
                            "why": (f"`{key}` is {entry['value']!r} here and {held['value']!r} "
                                    f"in {held['file']}, which the workspace names as the "
                                    f"owner. The owner's value is what the graph uses; this "
                                    f"one is reported because a stale deployment nobody has "
                                    f"noticed is a finding, not noise")})
                    continue
                entries[relaxed] = entry
                if canonical:
                    owned.add(relaxed)


EXTRACTORS = (DeploymentsExtractor(), ConfigMapsExtractor())
