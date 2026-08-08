"""Which file a workspace's `[[canonical]]` declaration covers.

Underscore-prefixed so the registry skips it, same as `_java/` and `_yaml/`. One rule, read
by every deployment reader, because two readers deciding "is this the owner's file" their own
way is two chances to decide it differently — and the answer changes which values the graph
believes.

## Why the extract layer consults this at all

The canonical link pass resolves the contested service *node*, and that is not enough. A
resolved-value view is built during **extraction**, before any link pass runs, so a value read
from a contested service comes from whichever file the reader happened to merge last. The
second fixture made that concrete: a compose file whose own header says it is superseded sorted
after the live one, won the merge, and a whole component was recorded as publishing to a topic
one version number off. Everything about the resulting graph looked correct.

`ExtractContext.config` already carries the `[[canonical]]` blocks, so consulting them here
needs no core change — the seam existed before anything used it.

## `path` covers a FILE and its SUBTREE, and the second half was decided rather than assumed

Compose names the contested file directly, so an exact match is all it ever needed. Helm
cannot work that way: a chart tree declares its service in `values.yaml` while the host map it
publishes lives in `templates/*.yaml`, so a claim naming the values file has to reach files
beside it or it reaches nothing at all.

So a claim covers the file it names **and** everything under that file's directory. Note the
asymmetry that keeps compose intact: when the named path has no directory part
(`docker-compose.yml`), the subtree rule is **skipped entirely** rather than treated as an
empty prefix — an empty prefix matches every path, which would make a sibling `legacy/` file
canonical too and silently invert the very decision being recorded.
"""
from __future__ import annotations


def claims_for(config: dict, alias: str) -> list[dict]:
    """Every `[[canonical]]` block that names a path and applies to this component.

    A claim with no `owner` applies everywhere: `owner` narrows, it does not qualify. A claim
    with no `path` decides nothing about files and is skipped here — it still decides the
    service node, which is the link pass's business rather than this one's.
    """
    out = []
    for claim in config.get("canonical") or []:
        if not isinstance(claim, dict) or not str(claim.get("path", "")).strip():
            continue
        owner = str(claim.get("owner", ""))
        if not owner or owner == alias:
            out.append(claim)
    return out


def covers(claim: dict, alias: str, rel: str) -> bool:
    """Whether this claim designates `rel` (a component-relative path) as the owner's."""
    owner = str(claim.get("owner", ""))
    if owner and owner != alias:
        return False
    path = str(claim.get("path", "")).strip().strip("/")
    if not path:
        return True
    rel = rel.strip("/")
    if rel == path:
        return True
    # Only when the claim names something inside a directory. A bare filename has no subtree,
    # and treating its empty parent as a prefix would match every path in the component.
    parent = path.rsplit("/", 1)[0] if "/" in path else ""
    return bool(parent) and (rel == parent or rel.startswith(parent + "/"))


def is_owner(config: dict, alias: str, rel: str) -> bool:
    """Whether `rel` is canonical: covered by a claim, or uncontested because none applies.

    "No claim names a path in this component" means the same as it always did — nothing is
    contested, so nothing is ranked and the reader's existing merge order stands. A workspace
    only fills in `[[canonical]]` for collisions the build actually reports.
    """
    claims = claims_for(config, alias)
    return not claims or any(covers(c, alias, rel) for c in claims)
