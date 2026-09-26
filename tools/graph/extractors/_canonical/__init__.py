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

## `path` covers what it NAMES, and a package is asked about as a package

Compose names the contested file directly, so an exact match is all it ever needed. Helm
cannot work that way: a chart tree declares its service in `values.yaml` while the host map it
publishes lives in `templates/*.yaml`, so a claim naming the values file has to reach the
chart's templates or it reaches nothing at all.

That reach used to live in `covers`, as "the file it names and everything under that file's
DIRECTORY" - which T529 found covering far more than a chart: a superseded deployment kept
in a subdirectory of the live one (`deploy/legacy/` beside `deploy/docker-compose.yml`) was
canonical too, and a directory claim (`charts/orderflow`) covered every chart beside it. So
the two questions are separate now. `covers` answers for a file: the path the claim names,
or anything under it when it names a directory. `covers_package` answers for a package - a
chart - by its root directory: the claim names that root or a directory above it, or names
one of the package's own entry files (`values.yaml`) directly inside it. A package nested
below another is its own package, and a claim on one is not a claim on the other.
"""
from __future__ import annotations

import posixpath


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


def _claimed_path(claim: dict, alias: str) -> str | None:
    """The claim's path, normalised; None when the claim is another component's."""
    owner = str(claim.get("owner", ""))
    if owner and owner != alias:
        return None
    return str(claim.get("path", "")).strip().strip("/")


def covers(claim: dict, alias: str, rel: str) -> bool:
    """Whether this claim designates `rel` (a component-relative path) as the owner's: the
    path it names, or anything under it when it names a directory. Matched on whole path
    segments, so `charts/orders` is not a prefix of `charts/orders-legacy` (T530)."""
    path = _claimed_path(claim, alias)
    if path is None:
        return False
    if not path:
        return True
    rel = rel.strip("/")
    return rel == path or rel.startswith(path + "/")


def covers_package(claim: dict, alias: str, root: str,
                   entry_files: tuple[str, ...] = ()) -> bool:
    """Whether this claim designates the package whose root directory is `root` ("" is the
    component's own root): it names that root or a directory above it, or names one of
    `entry_files` directly inside it - a chart claimed by its `values.yaml`, whose host map
    lives in `templates/` below (T529)."""
    path = _claimed_path(claim, alias)
    if path is None:
        return False
    if not path:
        return True
    root = root.strip("/")
    if root and covers(claim, alias, root):
        return True
    return path in {posixpath.join(root, name) for name in entry_files}


def is_owner(config: dict, alias: str, rel: str) -> bool:
    """Whether `rel` is canonical: covered by a claim, or uncontested because none applies.

    "No claim names a path in this component" means the same as it always did — nothing is
    contested, so nothing is ranked and the reader's existing merge order stands. A workspace
    only fills in `[[canonical]]` for collisions the build actually reports.
    """
    claims = claims_for(config, alias)
    return not claims or any(covers(c, alias, rel) for c in claims)


def owns_package(config: dict, alias: str, root: str,
                 entry_files: tuple[str, ...] = ()) -> bool:
    """`is_owner`, asked of a whole package by its root directory (see `covers_package`)."""
    claims = claims_for(config, alias)
    return not claims or any(covers_package(c, alias, root, entry_files) for c in claims)
