"""Exact-match parsing for `.properties` and YAML, plus placeholder interpolation.

Regex is not used to *find* keys here — these formats have unambiguous line/mapping
structure, so parsing them exactly is both simpler and more correct than pattern matching.
Line numbers are preserved because every downstream edge must cite `source_file:line`.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

try:  # pragma: no cover - import guard exercised only where PyYAML is absent
    # Soft on purpose, matching `srcfacts`' tree-sitter guard: the drop-in promise (item
    # 62) is that a zero-dependency environment still completes a build, degraded and
    # SAYING SO — a hard import here died at extractor discovery instead, before any
    # extractor could report what it was about to lose.
    import yaml
except ImportError:
    yaml = None

from .ids import relaxed_key

#: `${VAR}` / `${VAR:default}` / `${VAR:-default}`
PLACEHOLDER = re.compile(r"\$\{([A-Za-z0-9_.\-]+)(?::-?([^}]*))?\}")


def read_text(path: Path) -> str:
    """Read source text tolerantly — estate files are mixed-encoding in practice."""
    return path.read_text(encoding="utf-8", errors="replace")


def parse_properties(text: str) -> list[tuple[str, str, int]]:
    """Parse a `.properties` body into (key, value, 1-based line).

    Handles `=` and `:` separators, `#`/`!` comments, and backslash line continuations.
    """
    out: list[tuple[str, str, int]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        start = i
        raw = lines[i]
        stripped = raw.strip()
        if not stripped or stripped[0] in "#!":
            i += 1
            continue
        # Join continuations (line ends with an odd number of backslashes).
        buf = raw
        while buf.rstrip().endswith("\\") and (len(buf.rstrip()) - len(buf.rstrip().rstrip("\\"))) % 2 == 1:
            buf = buf.rstrip()[:-1]
            i += 1
            if i >= len(lines):
                break
            buf += lines[i]
        m = re.match(r"\s*([^=:\s]+)\s*[=:]\s*(.*)$", buf)
        if m:
            out.append((m.group(1), m.group(2).strip(), start + 1))
        i += 1
    return out


def yaml_available() -> bool:
    """Whether YAML can be parsed here — the sibling of `srcfacts.available()`.

    Extractors that read YAML check this FIRST and record one unresolved row per component
    when it is False, exactly as the source extractors do for a missing grammar: an
    extractor that simply found nothing would be indistinguishable from a workspace with
    no such files, and a build that died on the import would fail every repository over a
    library only some of its files need.
    """
    return yaml is not None


def _require_yaml() -> None:
    if yaml is None:
        raise RuntimeError(
            "PyYAML is not installed. Callers gate on props.yaml_available() and report "
            "the loss as an unresolved row — reaching this raise means a caller skipped "
            "the gate.")


def load_yaml(path: Path) -> Any:
    """Parse YAML, returning None on malformed input rather than raising.

    A malformed config file in one repo must not abort a whole-estate build; the caller
    records it as unresolved so it surfaces in the validation report instead.
    """
    _require_yaml()
    try:
        return yaml.safe_load(read_text(path))
    except yaml.YAMLError:
        return None


def load_yaml_reporting(path: Path) -> tuple[Any, str]:
    """Parse YAML, returning (document, error). Exactly one of the two is meaningful.

    `load_yaml` collapses every failure to None, which is enough to keep a build alive and
    not enough to say what went wrong. A caller recording a malformed file as unresolved
    needs the parser's own message: "this file was skipped" is a log line, while "this file
    was skipped because line 5 has a mapping value where none is allowed" is something a
    human can act on without reproducing the build.
    """
    _require_yaml()
    try:
        return yaml.safe_load(read_text(path)), ""
    except yaml.YAMLError as exc:
        return None, " ".join(str(exc).split())


def load_yaml_all_reporting(path: Path) -> tuple[list[Any], str]:
    """Parse a multi-document file, returning (documents, error). Prefer this spelling.

    The multi-document counterpart of `load_yaml_reporting`, and it exists because the two
    failure modes are not equally survivable. `load_yaml` collapses a failure to `None`,
    which a caller can still SEE and report. `load_yaml_all` collapses it to `[]` — **the
    same value a well-formed file with no documents returns** — so a caller looping over the
    result does nothing, reports nothing, and cannot tell the two apart even in principle.

    That is not a hypothetical, and it cost a whole extractor. A deployment chart's
    `templates/` are frequently not YAML at all: a templating language interpolates into
    keys and values, so every document fails to parse. The ConfigMap reader looped over `[]`
    and emitted zero edges **and zero unresolved rows** against a tree full of them — a
    silence indistinguishable from a workspace that publishes no addresses (item 76).
    """
    _require_yaml()
    try:
        return [d for d in yaml.safe_load_all(read_text(path)) if d is not None], ""
    except yaml.YAMLError as exc:
        return [], " ".join(str(exc).split())


def load_yaml_all(path: Path) -> list[Any]:
    """Parse a multi-document file, discarding any parse error.

    **This spelling cannot distinguish "unreadable" from "empty"** — both are `[]`. A caller
    that needs to report the loss must use `load_yaml_all_reporting`; this one is kept for
    callers that genuinely do not care, which read better without the tuple. One
    implementation underneath, so the two can never disagree about what parses.
    """
    return load_yaml_all_reporting(path)[0]


def walk_yaml(node: Any, prefix: str = "") -> Iterator[tuple[str, Any]]:
    """Yield (dotted.path, scalar) for every leaf. List indices appear as `[i]`."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from walk_yaml(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk_yaml(v, f"{prefix}[{i}]")
    else:
        yield prefix, node


def find_line(text: str, needle: str, start: int = 0) -> int:
    """1-based line of the first occurrence of `needle`, or 0 when absent.

    YAML parsing loses positions, so a key's line is recovered by searching the raw text.
    Returning 0 (rather than guessing 1) keeps 'unknown' visibly distinct in provenance.
    """
    if not needle:
        return 0
    idx = text.find(needle, start)
    return text.count("\n", 0, idx) + 1 if idx != -1 else 0


def interpolate(value: str, resolved: dict[str, str], depth: int = 0) -> tuple[str, list[str]]:
    """Expand `${...}` placeholders against a resolved key cache.

    Returns (expanded, unresolved_keys). Lookup is relaxed-binding aware, so a value written
    as `${ORDERS_DB_HOST}` resolves against a property named `orders.db.host`.
    """
    if depth > 5 or not isinstance(value, str) or "${" not in value:
        return value, []
    missing: list[str] = []

    def sub(m: re.Match[str]) -> str:
        key, default = m.group(1), m.group(2)
        hit = resolved.get(relaxed_key(key))
        if hit is not None:
            return hit
        if default is not None:
            return default
        missing.append(key)
        return m.group(0)

    expanded = PLACEHOLDER.sub(sub, value)
    if expanded != value and "${" in expanded:
        deeper, more = interpolate(expanded, resolved, depth + 1)
        return deeper, missing + more
    return expanded, missing


def build_property_cache(pairs: list[tuple[str, str, int]]) -> dict[str, str]:
    """Relaxed-binding key -> value map for interpolation."""
    return {relaxed_key(k): v for k, v, _ in pairs}
