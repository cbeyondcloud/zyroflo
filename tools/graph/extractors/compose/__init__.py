"""Compose files: what services exist, and what environment each one actually runs with.

Like the properties extractor, this emits some edges of its own but its real product is a
**resolved environment view** left in `ctx.cache` for later extractors to read.

## Why this layer matters more here than a chart layer does

In a workspace whose components carry their own `application.properties`, configuration and
the code reading it live in one directory, so an extractor confined to a component finds
both halves. Compose inverts that: a service's environment is declared in the *deployment*
component, and the code reading it lives somewhere else entirely. An extractor that only
reads a component's own files sees `os.environ["QUEUE"]`, finds no value anywhere near it,
and stops one hop short of wherever that queue goes.

That is not a Python fact or a compose fact. It is what any workspace looks like once
deployment is a separate concern, and it is the reason this view is published rather than
resolved privately.

## Which component a service belongs to

`build:` names a directory, and a directory is a component or it is not — a **declared**
link, so it is taken at full confidence. Matching by name instead (service `pantry-api` must
mean component `pantry-api`) is a coincidence that usually holds, so it is the fallback and
it is recorded as INFERRED rather than presented as the same kind of fact. A service that
maps to neither — a stock database image, say — still publishes its environment under its
own name, because a service this workspace does not build is still a service it runs.

## The cache contract

`ctx.cache["composeenv"][<component alias or service name>]` has **exactly the shape the
properties extractor publishes**, so `properties.lookup()` reads it unchanged and there is
one resolution rule rather than two that can disagree:

    {"values": {"<relaxed key>": {"value", "file", "line", "raw", "spellings"}},
     "unresolved": {"<relaxed key>": {"raw", "missing", "file", "line"}}}

It is a **separate cache key** rather than a merge into the properties view. Whether a
deployment's value overrides what a component declares for itself is the layering question,
and layering across components is a link pass; silently merging here would answer it by
accident and in whichever order the extractors happened to run.

## `${VAR:-default}`

Compose writes `:-` where a properties file writes `:`. Both already parse: `props.PLACEHOLDER`
covers the two spellings, which is worth stating because it means this extractor adds no
interpolation of its own. A second implementation is how one workspace ends up resolving a
placeholder two ways.

## Deliberately not here

`depends_on` is a real coupling and is not emitted as `DG_DEPENDS_ON`. That edge type
currently carries *build*-time module dependencies with versions, and putting a runtime
start-order edge under the same name would make a version-carrying query return rows that
have no version. It needs its own type, which needs a consumer, and neither exists yet.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.graph.core.lib import ids, props, repo_walk
from tools.graph.core.model import Edge, ExtractResult, Node, Provenance
from tools.graph.core.spi import ExtractContext, RepoRef
from tools.graph.extractors import _canonical, _yaml

#: Where the resolved environment view lands for later extractors to read.
CACHE_KEY = "composeenv"

#: `cfg` node kind for this format, from the generic kind vocabulary.
CONFIG_KIND = "compose"


def view_for(cache: dict, alias: str) -> dict | None:
    """This component's resolved compose environment, or None when nothing published one.

    None is a real answer: a workspace with no compose files has no entry, and `--only` can
    exclude this extractor entirely. A consumer that gets None records an unresolved row
    naming what it could not look up — never a crash, and never a guess.
    """
    return cache.get(CACHE_KEY, {}).get(alias)


def _rel(repo: RepoRef, path: Path) -> str:
    try:
        return path.relative_to(repo.path).as_posix()
    except ValueError:
        return path.as_posix()


#: Where `key` is declared as a MAPPING KEY, rather than wherever its name happens to appear.
#: Lived here until a second YAML reader needed the same thing; moved to `_yaml/` so there is
#: one implementation instead of two that can drift apart while both look right. Re-exported
#: under its original name because callers and tests refer to it as `compose.key_line`.
key_line = _yaml.key_line


def environment_entries(node: Any) -> list[dict]:
    """The `environment:` block as entries - `key`, `value`, `line`, `readings` - in either
    spelling compose allows, read from its YAML NODE.

    A map (`KEY: value`) and a list (`- KEY=value`) mean the same thing, and a reader that
    handles only the map form silently produces a service with no environment at all — which
    is indistinguishable from a service that genuinely has none.

    A bare `- KEY` in list form means "inherit from the host" and carries no value here. It
    yields an empty string rather than being dropped: the key is referenced, and a key this
    workspace names is a key the graph should know about. A map value YAML reads as null
    (`KEY:`) says the same.

    Read from the node, not from what `safe_load` typed (T536): a value is the text it was
    written as - `1.10`, not `1.1`; `off`, not `False` - and `readings` names what a YAML
    reader may deliver it as instead. A key's line is its own node's (T539), so a key merged
    in through an anchor (`<<: *env`) is cited where it is declared, not at the next
    service's key of the same name.
    """
    y = props.yaml
    out: list[dict] = []
    if isinstance(node, y.MappingNode):
        for key, value in _yaml.mapping_pairs(node):
            if isinstance(value, y.ScalarNode):
                text, readings = _yaml.scalar_text(value), _yaml.typed_readings(value)
            else:
                text, readings = str(_yaml.constructed(value)), ()
            out.append({"key": str(key.value), "value": text,
                        "line": _yaml.line_of(key), "readings": readings})
    elif isinstance(node, y.SequenceNode):
        for item in node.value:
            # A list item is `KEY=value` text; one YAML reads as anything but text (a null,
            # a number) names no variable, as before.
            if not isinstance(item, y.ScalarNode) or item.tag != _yaml.STR_TAG:
                continue
            key, sep, value = item.value.partition("=")
            out.append({"key": key.strip(), "value": value.strip() if sep else "",
                        "line": _yaml.line_of(item), "readings": ()})
    return out


def target_component(compose_file: Path, service: dict, repos: list[RepoRef],
                     name: str) -> tuple[str, str]:
    """Which component this service is, and how that was decided.

    Returns (alias, how). `how` is `"build"` for a declared build context, `"name"` for the
    name coincidence, and `""` when neither reaches a component — kept distinct so a
    consumer can rank a declaration above a guess instead of treating them alike.
    """
    build = service.get("build")
    context = build.get("context") if isinstance(build, dict) else build
    if isinstance(context, str) and context.strip():
        try:
            resolved = (compose_file.parent / context.strip()).resolve()
        except OSError:                       # pragma: no cover - platform-dependent
            resolved = None
        if resolved is not None:
            for repo in repos:
                if repo.path.resolve() == resolved:
                    return repo.alias, "build"
    for repo in repos:
        if repo.alias == name:
            return repo.alias, "name"
    return "", ""



#: What each `kind` this module emits promises its reader beyond `UNRESOLVED_COMMON`.
#: Declared HERE rather than in core: a central table would make core learn every
#: extractor's vocabulary, so adding a stack would force a core edit. The pipeline
#: checks these after every run and fails the build when a row does not carry them.
UNRESOLVED_FIELDS = {
    "unreadable-file": (),
    "compose-env-unresolved": ('key',),
    "compose-unparsed": ('error',),
    "grammar-unavailable": ('language',),
    "superseded-declaration": ('key', 'value'),
    "yaml-typed-value": ('key', 'value', 'readings'),
}


def _no_yaml(extractor: str, repo: RepoRef, n_files: int) -> dict:
    """One row per component when PyYAML is absent — `spring._no_grammar`'s sibling.

    YAML is a language and its parser is not installed; the row says so in the same
    vocabulary a missing source grammar uses, so item 84's grouping files both losses
    under one cause instead of inventing a second spelling for it.
    """
    return {
        "extractor": extractor, "component": repo.alias, "kind": "grammar-unavailable",
        "source_file": "", "line": 0, "files": n_files, "language": "yaml",
        "why": (f"no parser is installed for yaml (PyYAML is absent), so {n_files} "
                f"file(s) were not read. Every service they declare and every address "
                f"they publish are missing from the graph, and this row is the only "
                f"record of that"),
    }

class ComposeExtractor:
    """Services a compose file declares, and the environment it gives each one."""

    name = "compose-services"
    INPUT_GLOBS = ("docker-compose*.yml", "docker-compose*.yaml",
                   "compose*.yml", "compose*.yaml")

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        if not props.yaml_available():
            # The zero-dependency degradation (item 62), same shape as a missing source
            # grammar: one row per component saying what was not read, because an
            # extractor that simply returned nothing would be indistinguishable from a
            # workspace that runs no services.
            for repo in ctx.repos:
                res.unresolved.append(_no_yaml(self.name, repo, len(ctx.inputs(repo))))
            return res
        views: dict[str, dict] = ctx.cache.setdefault(CACHE_KEY, {})
        #: Per component, the keys the canonical source has already claimed. Local to this
        #: run rather than stored in the view, which is a shape other publishers share.
        owned: dict[str, set[str]] = {}
        #: Per component, what each value the view now holds may be read as (T536), so two
        #: files spelling one value differently are not reported as disagreeing outright
        #: (review B10-4). Local for the same reason as `owned`.
        held: dict[str, dict[str, tuple[str, ...]]] = {}
        for repo in ctx.repos:
            for path in sorted(ctx.inputs(repo)):
                self._file(res, ctx, repo, path, views, owned, held)
        return res

    def _file(self, res: ExtractResult, ctx: ExtractContext, repo: RepoRef, path: Path,
              views: dict[str, dict], owned: dict[str, set[str]],
              held: dict[str, dict[str, tuple[str, ...]]]) -> None:
        rel = _rel(repo, path)
        _, read_err = repo_walk.read_text_reporting(path)
        if read_err:
            res.unresolved.append({
                "extractor": self.name, "kind": "unreadable-file", "component": repo.alias,
                "source_file": rel, "line": 0,
                "why": ("this file could not be read, so whether it defines services is "
                        "unknown; if it did, they are missing from the graph rather than "
                        "absent")})
            return
        doc, error = props.load_yaml_reporting(path)
        if error:
            # E6. Recorded, and the build continues: one unreadable file must not abort a
            # workspace scan, and must not vanish from it either.
            res.unresolved.append({
                "extractor": self.name, "kind": "compose-unparsed",
                "component": repo.alias, "source_file": rel, "line": 0, "error": error,
                "why": "could not be parsed as YAML, so every service it declares and "
                       "every address they publish are missing from the graph"})
            return
        services = doc.get("services") if isinstance(doc, dict) else None
        if services is None:
            return
        if not isinstance(services, dict):
            # `services:` written as a LIST (or any non-mapping) is valid YAML, so no parse
            # error fires and a bare `return` reported an INVALID file as one that simply
            # declared no services. Record it: invalid must not read as absent.
            res.unresolved.append({
                "extractor": self.name, "kind": "compose-unparsed",
                "component": repo.alias, "source_file": rel, "line": 0,
                "error": f"`services:` is a {type(services).__name__}, not a mapping",
                "why": "declares `services:` as something other than a mapping, so the "
                       "services it means to define are unreadable rather than absent"})
            return

        text = props.read_text(path)
        deploy = ids.deployment_id(repo.alias, rel)
        res.nodes.append(Node(id=deploy, type="deploy", props={
            "kind": CONFIG_KIND, "path": rel, "component": repo.alias}))
        cfg = ids.config_id(repo.alias, rel)
        res.nodes.append(Node(id=cfg, type="cfg", props={
            "kind": CONFIG_KIND, "path": rel, "component": repo.alias}))

        # The services as the NODE tree holds them - each name as written, each body with
        # its values' text and its keys' lines (T536, T539). The loaded dict folds two names
        # YAML reads as equal (`on:` and `true:`) into one, and pairing the two by position
        # or by name then dropped a whole environment without a row (review B10-2).
        y = props.yaml
        services_node = _yaml.mapping_get(_yaml.document_nodes(text)[0], "services")
        # Scanned forward, service by service, so a service's name appearing earlier - in
        # another service's value - is not taken for its declaration.
        cursor = 0
        for key_node, node in _yaml.mapping_pairs(services_node):
            name = str(key_node.value)
            if isinstance(node, y.MappingNode):
                service = _yaml.constructed(node)
            elif isinstance(node, y.ScalarNode) and _yaml.scalar_text(node) == "" \
                    and node.style is None:
                service = {}          # an empty-body service (`worker:` with nothing under
                                      # it) is a declared service with no config; dropping it
                                      # loses a real svc node and its DG_DEPLOYS edge
            else:
                continue
            line, cursor = key_line(text, name, cursor)
            self._service(res, ctx, repo, rel, deploy, cfg, name, service, node,
                          views, owned, held, line)

    def _service(self, res: ExtractResult, ctx: ExtractContext, repo: RepoRef, rel: str,
                 deploy: str, cfg: str, name: str, service: dict, node: Any,
                 views: dict[str, dict], owned: dict[str, set[str]],
                 held: dict[str, dict[str, tuple[str, ...]]], line: int) -> None:
        alias, how = target_component(repo.path / rel, service, ctx.repos, name)

        # The claim on a service, in exactly the shape `mark-canonical` groups on. Two files
        # naming one service produce two of these, identically, and the workspace's own
        # [[canonical]] block decides — never this extractor, which cannot see intent.
        target = ids.service_id(name, repo.alias)
        res.nodes.append(Node(id=target, type="svc", props={"component": repo.alias}))
        res.edges.append(Edge(
            src=deploy, type="DG_DEPLOYS", dst=target, source_file=rel, line=line,
            extractor=self.name, confidence=1.0,
            props={"service": name, "component": alias, "mapped_by": how}))

        entries = environment_entries(_yaml.mapping_get(node, "environment"))
        if not entries:
            return
        declarations = [{"key": e["key"], "value": e["value"], "file": rel,
                         "line": e["line"]} for e in entries]
        view = _resolve(declarations)
        views.setdefault(alias or name, {"values": {}, "unresolved": {}})
        claim = canonical_claim(ctx.config, name)
        superseded = _merge(views[alias or name], view,
                            owned.setdefault(alias or name, set()),
                            contested=claim is not None,
                            canonical=is_canonical_source(claim, repo.alias, rel),
                            held=held.setdefault(alias or name, {}),
                            readings={ids.relaxed_key(e["key"]): e["readings"]
                                      for e in entries})
        for row in superseded:
            said = (f"`{row['key']}` is declared as {row['dropped']['value']!r} here and "
                    f"as {row['kept']['value']!r} in {row['kept']['file']}, which the "
                    f"workspace names as the owner of service `{name}`. The owner's value "
                    f"is what the graph uses; this one is reported because ")
            res.unresolved.append({
                "extractor": self.name,
                "kind": "superseded-declaration", "component": repo.alias,
                "key": row["key"], "source_file": row["dropped"]["file"],
                "line": row["dropped"]["line"], "value": row["dropped"]["value"],
                "why": said + ("the two are spelled differently and may be one value or two, "
                               "depending on the tool that reads them"
                               if row["maybe_same"] else
                               "a stale deployment nobody has noticed is a finding, not "
                               "noise")})
        for e in entries:
            if e["readings"]:
                # T536: recorded as written, and said once what else it may arrive as.
                res.unresolved.append({
                    "extractor": self.name, "kind": "yaml-typed-value",
                    "component": repo.alias, "key": e["key"], "value": e["value"],
                    "readings": list(e["readings"]), "source_file": rel, "line": e["line"],
                    "why": _yaml.typed_value_why(e["key"], e["value"], e["readings"])})

        for entry in declarations:
            envkey = ids.env_key_id(entry["key"])
            res.nodes.append(Node(id=envkey, type="envkey",
                                  props={"key": ids.relaxed_key(entry["key"])}))
            resolved = view["values"].get(ids.relaxed_key(entry["key"]))
            res.edges.append(Edge(
                src=cfg, type="DG_CONFIGURES", dst=envkey, source_file=rel,
                line=entry["line"], extractor=self.name, confidence=1.0,
                provenance=Provenance.EXTRACTED,
                props={"declared_as": entry["key"], "service": name,
                       "value": resolved["value"] if resolved else "",
                       "raw": entry["value"]}))
        for key, miss in view["unresolved"].items():
            res.unresolved.append({
                "extractor": self.name,
                "kind": "compose-env-unresolved", "component": repo.alias, "key": key,
                "source_file": rel, "line": miss["line"],
                "why": (f"`{miss['raw']}` names {', '.join(miss['missing'])}, which nothing "
                        f"in this file sets and which carries no default")})


def _resolve(declarations: list[dict]) -> dict:
    """Interpolate a service's environment against itself.

    Two passes, because compose lets one variable reference another and the declaration
    order in a file says nothing about dependency order. A placeholder that still cannot be
    resolved is kept OUT of the values map — absence is the safe failure, where returning the
    literal would hand every consumer a plausible string and name an entity after it.
    """
    literal = {ids.relaxed_key(d["key"]): d["value"] for d in declarations
               if "${" not in d["value"]}
    values: dict[str, dict] = {}
    unresolved: dict[str, dict] = {}
    for d in declarations:
        expanded, missing = props.interpolate(d["value"], literal)
        key = ids.relaxed_key(d["key"])
        if missing:
            unresolved[key] = {"raw": d["value"], "missing": missing,
                               "file": d["file"], "line": d["line"]}
            continue
        values[key] = {"value": expanded, "file": d["file"], "line": d["line"],
                       "raw": d["value"],
                       "spellings": {d["key"]: {"file": d["file"], "line": d["line"],
                                                "raw": d["value"]}}}
    return {"values": values, "unresolved": unresolved}


def canonical_claim(config: dict, service: str) -> dict | None:
    """The workspace's decision about a contested service, or None if it made none.

    Read from `ctx.config`, which the pipeline already populates from the workspace's
    `[[canonical]]` blocks — so the decision reaches the extract layer through a seam the
    SPI already had, and no core change is needed to consult it.
    """
    for claim in config.get("canonical") or []:
        if isinstance(claim, dict) and str(claim.get("service", "")) == service:
            return claim
    return None


def is_canonical_source(claim: dict | None, alias: str, rel: str) -> bool:
    """Whether this file is the one the workspace named as the owner of a service.

    Both coordinates are optional and each narrows independently: `owner` alone is enough
    when the variants live in different components, and `path` is what decides it when one
    component holds two of them — which is exactly this fixture's shape.

    Delegates to `_canonical.covers` so that "does this claim designate this file" has ONE
    implementation across every deployment reader. A compose claim names a file, and the
    match is exact: a claim on `deploy/docker-compose.yml` no longer reaches the rest of
    `deploy/`, where a superseded file in `deploy/legacy/` used to count as the owner's too
    (T529). Surrounding slashes are stripped from both sides, so `/docker-compose.yml`
    matches; that is a spelling of the same claim, and refusing it would have been a silent
    no-match rather than a complaint.
    """
    if claim is None:
        return True
    return _canonical.covers(claim, alias, rel)


def _merge(into: dict, view: dict, owned: set[str], *, contested: bool = False,
           canonical: bool = True, held: dict[str, tuple[str, ...]] | None = None,
           readings: dict[str, tuple[str, ...]] | None = None) -> list[dict]:
    """Fold one service's view into a component's, keeping every spelling seen.

    Several compose files can configure one component. Later files win the value — the same
    deterministic last-wins rule the properties extractor uses within a component — but no
    spelling is lost, because provenance must point at the declaration the consumer asked
    for rather than at whichever one happened to win.

    **Last-wins is correct for layering and catastrophic for a contested service, and those
    are different situations wearing the same shape.** An override file legitimately
    supersedes a base file; a superseded deployment of the same service does not, and the
    workspace's `[[canonical]]` block is the only thing that can tell them apart, because
    neither file contains the intent. So when a service is contested, the owner's value
    outranks every other claim's regardless of filename order — and the losing value is
    RETURNED rather than dropped, because a stale deployment about to be believed is exactly
    what a reader needs to see.

    Without the ranking, this fixture's superseded file sorted last alphabetically and won:
    a whole component published to a topic one version number off, resolved from a file
    whose own header says it is dead. Nothing about the resulting graph looked wrong.

    **`owned` is the extractor's own state and deliberately not part of the view.** Which
    keys the canonical source has claimed is a fact about this extractor's traversal, and
    the view is a SHARED shape that `properties.lookup` reads whoever published it — a field
    only one publisher understands is precisely the divergence that contract exists to stop.
    A test asserting the entry's exact key set caught the first attempt doing it.

    **`held` is the same kind of state:** what each value the view holds may be read as
    (T536), with `readings` the same for this view's values. Values are compared as written,
    so `yes` in one file and `true` in another differ - and a reader of one YAML version
    delivers them alike, another not. The row says which of those it is (`maybe_same`,
    review B10-4) rather than calling every spelling a stale deployment.
    """
    held = {} if held is None else held
    readings = readings or {}
    superseded: list[dict] = []
    for key, entry in view["values"].items():
        existing = into["values"].get(key)
        spellings = {**(existing or {}).get("spellings", {}), **entry.get("spellings", {})}
        maybe_same = existing is not None and _yaml.may_agree(
            existing["value"], held.get(key, ()), entry["value"], readings.get(key, ()))
        if existing is not None and contested and not canonical and key in owned:
            # The owner has already spoken. Keep its value and its provenance; record the
            # loser rather than discarding it.
            if existing["value"] != entry["value"]:
                superseded.append({"key": key, "kept": existing, "dropped": entry,
                                   "maybe_same": maybe_same})
            into["values"][key] = {**existing, "spellings": spellings}
            continue
        if existing is not None and contested and canonical and key not in owned \
                and existing["value"] != entry["value"]:
            superseded.append({"key": key, "kept": entry, "dropped": existing,
                               "maybe_same": maybe_same})
        into["values"][key] = {**entry, "spellings": spellings}
        held[key] = readings.get(key, ())
        if contested and canonical:
            owned.add(key)
    for key, miss in view["unresolved"].items():
        into["unresolved"].setdefault(key, miss)
    return superseded


EXTRACTORS = (ComposeExtractor(),)
