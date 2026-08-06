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

from pathlib import Path
from typing import Any

from tools.graph.core.lib import ids, props, repo_walk
from tools.graph.core.model import Edge, ExtractResult, Node, Provenance
from tools.graph.core.spi import ExtractContext, RepoRef

#: Where the resolved host map lands for a later pass to join on.
CACHE_KEY = "hostmap"

#: Paths within the values document that name the logical service, in precedence order.
#: An explicit name beats an image path, because the image is a publishing detail and the
#: name is what other services address.
SERVICE_NAME_PATHS = ("service.name", "nameOverride", "fullnameOverride", "app.name")
IMAGE_PATHS = ("image.repository", "image.name")

#: Documents whose data block is a map of configuration keys.
CONFIG_KINDS = {"ConfigMap"}


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
        if isinstance(value, str) and value.strip():
            return value.strip(), key
    for key in IMAGE_PATHS:
        value = flat.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().rsplit("/", 1)[-1].split(":", 1)[0], key
    return "", ""


class DeploymentsExtractor:
    """Deployment packages and the service each claims."""

    name = "helm-deployments"
    INPUT_GLOBS = ("values.yaml", "values.yml", "Chart.yaml", "Chart.yml")

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        for repo in ctx.repos:
            for path in ctx.inputs(repo):
                if path.name.lower().startswith("chart."):
                    continue        # metadata, not values; read only for a name it may carry
                self._file(res, repo, path)
        return res

    def _file(self, res: ExtractResult, repo: RepoRef, path: Path) -> None:
        rel = _rel(repo, path)
        doc = props.load_yaml(path)
        if doc is None:
            res.unresolved.append({
                "extractor": self.name, "component": repo.alias, "kind": "unparsable",
                "source_file": rel, "line": 0,
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
        line = props.find_line(text, f"{source_field.rsplit('.', 1)[-1]}: {name}") if name else 0

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
        host_map: dict[str, dict] = ctx.cache.setdefault(CACHE_KEY, {})
        for repo in ctx.repos:
            entries = host_map.setdefault(repo.alias, {})
            for path in ctx.inputs(repo):
                self._file(res, repo, path, entries)
        return res

    def _file(self, res: ExtractResult, repo: RepoRef, path: Path, entries: dict) -> None:
        rel = _rel(repo, path)
        text = props.read_text(path)
        for doc in props.load_yaml_all(path):
            if not isinstance(doc, dict) or doc.get("kind") not in CONFIG_KINDS:
                continue
            data = doc.get("data") or {}
            if not isinstance(data, dict):
                continue
            cfg = ids.config_id(repo.alias, rel)
            res.nodes.append(Node(id=cfg, type="cfg", props={
                "kind": "configmap", "path": rel, "component": repo.alias,
                "name": str(doc.get("metadata", {}).get("name", ""))}))
            for key, value in data.items():
                line = props.find_line(text, f"{key}:")
                node = ids.env_key_id(str(key))
                res.nodes.append(Node(id=node, type="envkey",
                                      props={"key": ids.relaxed_key(str(key))}))
                res.edges.append(Edge(src=cfg, type="DG_CONFIGURES", dst=node,
                                      source_file=rel, line=line, extractor=self.name,
                                      confidence=1.0,
                                      props={"declared_as": str(key), "value": str(value)}))
                entries[ids.relaxed_key(str(key))] = {
                    "value": str(value), "file": rel, "line": line, "declared_as": str(key)}


EXTRACTORS = (DeploymentsExtractor(), ConfigMapsExtractor())
