"""The image key (batch 23, T589; design pass 23, section 2): Docker's reference grammar, read once,
so two readers that name one image in two vocabularies compare equal - compose's reference as its
file writes it, helm's `image.repository` with its `registry` beside it and a template span written
as `_yaml.TOKEN` (the `image` prop is the reference as its reader publishes it; only `image_key`
compares them).

A `_` package beside `_canonical` and `_yaml`: readers and link passes share it, and nothing
registers it (D15b-18). The link passes learn no image grammar - the key, the literal tail and the
comparison of a tail with a key all live here.
"""
from __future__ import annotations

from tools.graph.extractors._yaml import TOKEN

#: Docker Hub, the registry a reference that names none means.
DOCKER_HUB = "docker.io"
#: Docker Hub's other host names, read as `docker.io`.
_HUB_HOSTS = frozenset({"docker.io", "index.docker.io", "registry-1.docker.io"})
#: What marks a segment interpolated or templated: compose interpolates `$VAR` and `${VAR}` alike
#: (its own reader's rule is any `$`), a `${VAR:-a/b}` default can carry a `/` into the next
#: segment, which a brace still marks, and helm's de-templated span is `_yaml.TOKEN` - one reading
#: for both (D23-55). Docker forbids all four in a name, so none is ever literal.
_MARKS = ("$", "{", "}", TOKEN)


def _interpolated(segment: str) -> bool:
    return any(mark in segment for mark in _MARKS)


def _segments(reference: str) -> list[str]:
    """The reference's components, split at `/`, its digest (`@...`) and its tag - from the first
    `:` after the last `/` - dropped."""
    ref = reference.strip().split("@", 1)[0]
    head, _, last = ref.rpartition("/")
    return (head.split("/") if head else []) + [last.split(":", 1)[0]]


def _is_host(segment: str) -> bool:
    """Docker's own test of a reference's first component: a host where it holds `.` or `:`, is
    `localhost`, or is not lower case - else the first segment of a Docker Hub path."""
    return "." in segment or ":" in segment or segment == "localhost" or segment != segment.lower()


def image_key(reference: str, registry: str = "") -> str:
    """The image `reference` names, as one comparable key - `<host>/<path>` - or "" where it has
    none: the digest and the tag dropped; a helm `registry` prefixed where the reference names no
    host, never over one it names; the host lower-cased, Docker Hub's other names read as
    `docker.io`, no host as `docker.io`, and a single-segment Docker Hub path as
    `docker.io/library/<name>`; the path compared exactly. A host or a path segment interpolated
    or templated has no key; an interpolated tag alone leaves it intact."""
    segments = _segments(reference)
    if not all(segments) or any(_interpolated(s) for s in segments):
        return ""
    if len(segments) > 1 and _is_host(segments[0]):
        host, path = segments[0], segments[1:]
    elif registry.strip():
        prefix = registry.strip().strip("/").split("/")
        if not all(prefix) or any(_interpolated(s) for s in prefix):
            return ""
        host, path = prefix[0], prefix[1:] + segments
    else:
        host, path = DOCKER_HUB, segments
    host = host.lower()
    if host in _HUB_HOSTS:
        host = DOCKER_HUB
        if len(path) == 1:
            path = ["library", *path]
    return f"{host}/{'/'.join(path)}"


def literal_tail(reference: str) -> str:
    """The path after the reference's last interpolated segment, its tag dropped - `${REG}/org/web:1`
    gives `org/web`, `${IMAGE}` gives "" - so a reference with no key can still be compared with
    the keys whose ends it names (`tail_of`). A reference with no interpolated segment is its whole
    path."""
    segments = _segments(reference)
    marked = [i for i, segment in enumerate(segments) if _interpolated(segment)]
    return "/".join(segments[marked[-1] + 1:] if marked else segments)


def tail_of(key: str, tail: str) -> bool:
    """Whether `key` ends with `tail` on a path-segment boundary - `docker.io/org/web` with
    `org/web` or `web`, never `myweb` with `web` - and an empty tail meets no key, since
    `str.endswith("")` is true of every key (D23-20)."""
    return bool(key) and bool(tail) and (key == tail or key.endswith("/" + tail))
