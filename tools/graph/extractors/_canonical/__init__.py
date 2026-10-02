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
or anything under it when it names a directory. `covers_package` answers for a file of a
package - a chart - by the package roots the reader hands it: the claim names one of them or
a directory above it, or names a FILE of one of them (a chart's `values.yaml`, its overlay
`values-prod.yaml`, one of its templates). A package nested below another is its own
package, and a claim on one is not a claim on the other - unless the reader says the outer
one deploys it: a Helm subchart, vendored under its umbrella's `charts/`, ships in the
umbrella's release, so the reader lists the umbrella's root among the subchart's. Which
roots a file has, and which package a claimed file belongs to, is the reader's vocabulary;
this module only holds the rule. `claimed_path` is public for a reader that names a claim
in a row, as helm's `off-case-claim` does (T576).
"""
from __future__ import annotations

import posixpath
from collections.abc import Callable, Sequence


def workspace_layout(config: dict) -> dict[str, str]:
    """Each component's workspace-relative POSIX path, by name, from `config["components"]`.

    Normalised as T575 normalises a claim's path - a separator, a leading `./`, a doubled or
    trailing `/` spell the same path - so `./platform` and `platform` are one place (D534-42).
    The ONE reading of the component list that placing a build context, keying a package and
    naming its most specific reader all share; two readings of one config are how two of
    them come to disagree while each looks right.
    """
    out: dict[str, str] = {}
    for component in (config or {}).get("components") or []:
        text = str(component.get("path") or ".").replace("\\", "/").strip()
        path = posixpath.normpath(text).strip("/") if text else "."
        out[str(component["name"])] = path or "."
    return out


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


def claimed_path(claim: dict, alias: str) -> str | None:
    """The claim's path in the graph's own spelling; None when the claim is another
    component's. A Windows separator, a leading `./` and a doubled or trailing `/` spell the
    same path, not another one that matches nothing (T575), and `.` names the component's
    root, as `/` does."""
    owner = str(claim.get("owner", ""))
    if owner and owner != alias:
        return None
    path = str(claim.get("path", "")).strip().replace("\\", "/")
    path = posixpath.normpath(path).strip("/") if path else ""
    return "" if path == "." else path


def covers(claim: dict, alias: str, rel: str) -> bool:
    """Whether this claim designates `rel` (a component-relative path) as the owner's: the
    path it names, or anything under it when it names a directory. Matched on whole path
    segments, so `charts/orders` is not a prefix of `charts/orders-legacy` (T530)."""
    path = claimed_path(claim, alias)
    if path is None:
        return False
    if not path:
        return True
    rel = rel.strip("/")
    return rel == path or rel.startswith(path + "/")


def covers_package(claim: dict, alias: str, rel: str, roots: Sequence[str],
                   root_of: Callable[[str], str | None]) -> bool:
    """Whether this claim designates `rel`, a file of a package. `roots` are the root
    directories of the packages `rel` ships in, its own first ("" is the component's root);
    `root_of(path)` is the root of the package a claimed path is a file of, None when it
    names no package's file. The claim designates `rel` when it covers `rel` itself, names
    one of `roots` or a directory above one, or names a file of one of them - a chart
    claimed by its `values.yaml`, whose host map lives in `templates/` below (T529)."""
    path = claimed_path(claim, alias)
    if path is None:
        return False
    if not path or covers(claim, alias, rel):
        return True
    if any(covers(claim, alias, root) for root in roots if root):
        return True
    return root_of(path) in roots


def owns_package(config: dict, alias: str, rel: str, roots: Sequence[str],
                 root_of: Callable[[str], str | None]) -> bool:
    """Whether `rel` is canonical: designated by a claim (see `covers_package`), or
    uncontested because none applies.

    "No claim names a path in this component" means the same as it always did — nothing is
    contested, so nothing is ranked and the reader's existing merge order stands. A workspace
    only fills in `[[canonical]]` for collisions the build actually reports.
    """
    claims = claims_for(config, alias)
    return not claims or any(covers_package(c, alias, rel, roots, root_of) for c in claims)
