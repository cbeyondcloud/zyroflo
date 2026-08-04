"""Exact-match parsing for `.properties` and YAML, plus placeholder interpolation.

Regex is not used to *find* keys here — these formats have unambiguous line/mapping
structure, so parsing them exactly is both simpler and more correct than pattern matching.
Line numbers are preserved because every downstream edge must cite `source_file:line`.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterator

import yaml

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


def load_yaml(path: Path) -> Any:
    """Parse YAML, returning None on malformed input rather than raising.

    A malformed config file in one repo must not abort a whole-estate build; the caller
    records it as unresolved so it surfaces in the validation report instead.
    """
    try:
        return yaml.safe_load(read_text(path))
    except yaml.YAMLError:
        return None


def load_yaml_all(path: Path) -> list[Any]:
    try:
        return [d for d in yaml.safe_load_all(read_text(path)) if d is not None]
    except yaml.YAMLError:
        return []


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
    as `${EXAMPLE_REGISTRY_HOST}` resolves against a property named `example.registry.host`.
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
