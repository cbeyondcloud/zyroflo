"""Key/value configuration files: parse, merge, interpolate, and publish a resolved view.

This extractor emits very few edges of its own. Its real product is the **resolved property
view** it leaves in `ctx.cache`, which several later extractors read: a value referenced in
source code, a host paired with a path, a topic name written in one file and consumed in
another all have to agree on what a key resolves to. Resolving it once is not an
optimisation — two extractors resolving the same key independently is two chances to
resolve it differently, and the graph would then contain both answers with no way to tell
which is wrong.

## Three things a naive reader of these files gets wrong

**One setting, several spellings.** `orders.publish.topic`, `ORDERS_PUBLISH_TOPIC` and
`orders-publish-topic` are one key under relaxed binding. Treated as three, a workspace
grows phantom entities beside the real one — and they look exactly like real ones.

**A placeholder is not a value.** `${ORDER_TOPIC:order-events-v1}` resolves to its default.
Taken literally, the entity downstream ends up named after the placeholder text.

**A placeholder with no default cannot be resolved at build time, and saying so is the whole
job.** `${AUDIT_TOPIC}` is recorded as unresolved and **kept out of the resolved view**, so a
consumer that looks the key up simply does not find it. Absence is the safe failure; a view
that returned the literal `${AUDIT_TOPIC}` would hand every consumer a plausible string.

## The cache contract

`ctx.cache["properties"][<component alias>]` is::

    {
      "values": {
        "<relaxed key>": {
          "value":     "order-events-v1",   # fully interpolated
          "file":      "src/main/resources/application.properties",
          "line":      5,
          "raw":       "${ORDER_TOPIC:order-events-v1}",
          "spellings": {"orders.publish.topic": {"file": ..., "line": 5, "raw": ...}, ...},
        },
      },
      "unresolved": {
        "<relaxed key>": {"raw": "${AUDIT_TOPIC}", "missing": ["AUDIT_TOPIC"],
                          "file": ..., "line": 14},
      },
    }

**Resolution rule for consumers, and it matters for provenance:** look the key up by its
relaxed form, then — if `spellings` holds the exact spelling that was asked for — cite that
spelling's `file`/`line`. A consumer reading `${orders.publish.topic}` should be pointed at
the line declaring *that* spelling, not at whichever declaration happened to win the value.
Provenance that points at the wrong line is wrong in a way nothing notices unless someone
checks the number.

**A consumer must tolerate the cache being absent.** This extractor activates on files, so a
component with no configuration of this kind produces no entry, and a build run with
`--only` may not include it at all. The correct response is an `unresolved` row naming what
could not be looked up — never a crash, and never a guess.

## Deliberately not here

Layering across components — a chart's values or a deployment configmap overriding what the
component declares — is a link pass, not an extractor: it joins facts from two components
and neither one can see the other. Within a component, several files merge in sorted path
order with later declarations winning, which is deterministic and needs no precedence table
nobody has asked for yet.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tools.graph.core.lib import ids, props
from tools.graph.core.model import Edge, ExtractResult, Node
from tools.graph.core.spi import ExtractContext, RepoRef

#: Where the resolved view lands in the shared cache.
CACHE_KEY = "properties"

#: `cfg` node kind for this format, from the generic kind vocabulary.
CONFIG_KIND = "properties"


def lookup(view: dict, key: str) -> dict | None:
    """Resolve one key against a component's view, preferring the requested spelling.

    Returns `{"value", "file", "line", "raw"}` or None. None means *not resolvable*, which
    covers both "never declared" and "declared but its placeholder has no default" — the
    caller should record an unresolved row either way, and can consult `view["unresolved"]`
    to say which.
    """
    entry = view.get("values", {}).get(ids.relaxed_key(key))
    if entry is None:
        return None
    spelling = entry.get("spellings", {}).get(key)
    if spelling is not None:
        return {"value": entry["value"], "file": spelling["file"],
                "line": spelling["line"], "raw": spelling["raw"]}
    return {"value": entry["value"], "file": entry["file"],
            "line": entry["line"], "raw": entry["raw"]}


def view_for(cache: dict, alias: str) -> dict | None:
    """This component's resolved view, or None when nothing published one.

    None is a real answer, not an error: this extractor activates on files, so a component
    with no configuration of this kind has no entry, and `--only` can exclude it entirely.
    A consumer that gets None records an unresolved row naming what it could not look up.
    """
    return cache.get(CACHE_KEY, {}).get(alias)


#: A reference that is ENTIRELY one placeholder — the form a framework binds to a field or
#: an annotation argument. A value with text around the placeholder is handled by
#: `props.interpolate` instead; this is about telling "a key" from "a literal".
_WHOLE_PLACEHOLDER = re.compile(r"^\$\{([A-Za-z0-9_.\-]+)(?::-?([^}]*))?\}$")


def resolve_reference(view: dict | None, text: str) -> dict | None:
    """Resolve a configured reference — either a literal value or a whole `${key}`.

    Returns `{"value", "key", "file", "line"}`, or None when the text names a key that
    cannot be resolved. `key` is empty for a literal, and `file`/`line` are empty for one
    too: a literal's provenance is wherever it was written, which only the caller knows.
    """
    stripped = text.strip()
    m = _WHOLE_PLACEHOLDER.match(stripped)
    if m is None:
        return {"value": stripped, "key": "", "file": "", "line": 0}

    key, default = m.group(1), m.group(2)
    hit = lookup(view, key) if view is not None else None
    if hit is not None:
        return {"value": hit["value"], "key": key, "file": hit["file"], "line": hit["line"]}
    if default is not None:
        return {"value": default, "key": key, "file": "", "line": 0}
    return None


def _declarations(repo: RepoRef, files: list[Path]) -> list[dict]:
    """Every declaration in every file, in a deterministic order.

    Sorted by path so a build is reproducible regardless of how the filesystem enumerates a
    directory, and preserving in-file order so "last declaration wins" means what it says.
    """
    out: list[dict] = []
    for path in sorted(files):
        rel = _rel(repo, path)
        text = props.read_text(path)
        for key, value, line in props.parse_properties(text):
            out.append({"key": key, "relaxed": ids.relaxed_key(key), "raw": value,
                        "file": rel, "line": line})
    return out


def _rel(repo: RepoRef, path: Path) -> str:
    try:
        return path.relative_to(repo.path).as_posix()
    except ValueError:
        return path.as_posix()


def resolve(declarations: list[dict]) -> dict:
    """Turn raw declarations into the resolved view described in the module docstring.

    Two passes, because a value may reference another key: the first builds a raw map so
    interpolation has something to look keys up in, the second expands. `interpolate` handles
    a chain by re-expanding its own output, so `a=${b}` where `b=${c:x}` still lands on `x`.
    """
    by_key: dict[str, list[dict]] = {}
    for d in declarations:
        by_key.setdefault(d["relaxed"], []).append(d)

    # Pass 1: the raw map interpolation resolves against. The LAST declaration wins the
    # value, which is what these files mean by a repeated key.
    raw_map = {k: decls[-1]["raw"] for k, decls in by_key.items()}

    values: dict[str, dict] = {}
    unresolved: dict[str, dict] = {}
    for key, decls in by_key.items():
        winner, first = decls[-1], decls[0]
        expanded, missing = props.interpolate(winner["raw"], raw_map)
        if missing:
            unresolved[key] = {"raw": winner["raw"], "missing": sorted(set(missing)),
                               "file": winner["file"], "line": winner["line"]}
            continue
        values[key] = {
            "value": expanded,
            # Provenance defaults to the FIRST declaration: it is where the setting is
            # introduced, and a later duplicate is usually an override rather than the
            # definition. A consumer that asked for a specific spelling gets that spelling's
            # line instead — see `lookup`.
            "file": first["file"], "line": first["line"], "raw": first["raw"],
            "spellings": {d["key"]: {"file": d["file"], "line": d["line"], "raw": d["raw"]}
                          for d in decls},
        }
    return {"values": values, "unresolved": unresolved}


def conflicts(view: dict, declarations: list[dict]) -> list[dict]:
    """Keys whose several spellings resolve to DIFFERENT values.

    Not an error and not dropped: one of them is what the running system uses, and which one
    is a question about deployment that no file here answers. It is surfaced so a human sees
    the disagreement rather than a graph that silently picked a side.
    """
    by_key: dict[str, list[dict]] = {}
    for d in declarations:
        by_key.setdefault(d["relaxed"], []).append(d)

    raw_map = {k: decls[-1]["raw"] for k, decls in by_key.items()}
    out = []
    for key, decls in by_key.items():
        if len(decls) < 2 or key not in view["values"]:
            continue
        seen = {props.interpolate(d["raw"], raw_map)[0] for d in decls}
        if len(seen) > 1:
            out.append({"key": key, "values": sorted(seen),
                        "declarations": [{"key": d["key"], "file": d["file"], "line": d["line"]}
                                         for d in decls]})
    return out



#: What each `kind` this module emits promises its reader beyond `UNRESOLVED_COMMON`.
#: Declared HERE rather than in core: a central table would make core learn every
#: extractor's vocabulary, so adding a stack would force a core edit. The pipeline
#: checks these after every run and fails the build when a row does not carry them.
UNRESOLVED_FIELDS = {
    "key-conflict": ('key', 'values'),
    "placeholder": ('key', 'raw', 'missing'),
}

class PropertiesExtractor:
    """Reads key/value configuration files and publishes the resolved view."""

    name = "properties"
    INPUT_GLOBS = ("*.properties",)

    def extract(self, ctx: ExtractContext) -> ExtractResult:
        res = ExtractResult()
        cache: dict[str, Any] = ctx.cache.setdefault(CACHE_KEY, {})

        for repo in ctx.repos:
            files = ctx.inputs(repo)
            decls = _declarations(repo, files)
            view = resolve(decls)
            cache[repo.alias] = view

            for path in files:
                rel = _rel(repo, path)
                node_id = ids.config_id(repo.alias, rel)
                res.nodes.append(Node(id=node_id, type="cfg",
                                      props={"kind": CONFIG_KIND, "path": rel,
                                             "component": repo.alias}))
                res.edges.append(Edge(src=node_id, type="DG_IN_REPO",
                                      dst=ids.repo_id(repo.alias), source_file=rel, line=0,
                                      extractor=self.name))

            for key, info in sorted(view["unresolved"].items()):
                res.unresolved.append({
                    "extractor": self.name, "component": repo.alias, "kind": "placeholder",
                    "key": key, "raw": info["raw"], "missing": info["missing"],
                    "source_file": info["file"], "line": info["line"],
                    "why": (f"{', '.join(info['missing'])} has no default and nothing in this "
                            f"component declares it, so the value cannot be known from the "
                            f"repository alone"),
                })
            for c in conflicts(view, decls):
                res.unresolved.append({
                    "extractor": self.name, "component": repo.alias, "kind": "key-conflict",
                    "key": c["key"], "values": c["values"],
                    "source_file": c["declarations"][0]["file"],
                    "line": c["declarations"][0]["line"],
                    "why": (f"{len(c['declarations'])} spellings of one key resolve to "
                            f"different values ({', '.join(c['values'])}); which one applies "
                            f"is a deployment fact this repository does not contain"),
                })
        return res


EXTRACTORS = (PropertiesExtractor(),)
