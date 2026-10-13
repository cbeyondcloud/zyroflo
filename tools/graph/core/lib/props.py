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
# One read helper, not two. This module used to carry a SECOND `read_text` whose failure
# semantics were the opposite of repo_walk's -- it raised on an unreadable file and aborted the
# build, where repo_walk folded the same OSError to "" -- so one unreadable file either vanished
# or killed the run depending only on which extractor reached it, and neither recorded anything.
# Re-exporting repo_walk's reader collapses the two; a caller that must tell "unreadable" from
# "empty" and record it uses `repo_walk.read_text_reporting`.
from .repo_walk import read_text, read_text_reporting

#: `${VAR}` / `${VAR:default}` / `${VAR:-default}`
PLACEHOLDER = re.compile(r"\$\{([A-Za-z0-9_.\-]+)(?::-?([^}]*))?\}")


#: The format's white space: space, tab and form feed, and nothing else (T610). `\s` also
#: took a no-break space, VT, NEL and the Unicode separators, which `java.util.Properties`
#: reads as content - in a key, and at a value's either end.
_WHITE = " \t\f"

#: One declaration line, by the format's own rule: the key runs to the first `=`, `:` or
#: white space that no backslash escapes; white space after it is skipped, then one `=` or
#: `:` if present, then more white space; the rest is the value, its trailing white space
#: kept, as Java keeps it. A line that is only a key declares it empty.
_DECLARATION = re.compile(
    r"[ \t\f]*((?:\\.|[^=: \t\f\\])+)(?:[ \t\f]*[=:][ \t\f]*|[ \t\f]+|[ \t\f]*$)(.*)$")

#: A backslash in a key: `\uXXXX` is a code point, `\t` `\n` `\r` `\f` are control
#: characters, and any other character stands for itself - how a key spells its own
#: separators (`\ `, `\:`, `\=`) and a backslash (`\\`).
_KEY_ESCAPE = re.compile(r"\\(u[0-9a-fA-F]{4}|.)")
_KEY_CONTROL = {"t": "\t", "n": "\n", "r": "\r", "f": "\f"}


def _unescape_key(key: str) -> str:
    """A declaration's key as the format reads it, its escapes undone."""
    return _KEY_ESCAPE.sub(
        lambda m: (chr(int(m.group(1)[1:], 16)) if len(m.group(1)) == 5
                   else _KEY_CONTROL.get(m.group(1), m.group(1))), key)


def parse_properties(text: str) -> list[tuple[str, str, int]]:
    """Parse a `.properties` body into (key, value, 1-based line).

    Handles `=`, `:` and whitespace separators, a key with no value, `#`/`!` comments, and
    backslash line continuations. Only `=` and `:` used to be read, so `server.port 8080`
    and a bare key - both valid lines - were dropped with no row (T538). A key's escaped
    separators are part of it, so `key\\ with\\ spaces=v` declares `key with spaces`: cut at
    the first space instead, it declared a key nobody wrote, holding the rest as its value.
    A line with an empty key (`=v`) declares nothing a reader can look a value up by, and is
    not returned.
    """
    out: list[tuple[str, str, int]] = []
    # T607: the format's three line terminators, and only those. `str.splitlines()` also
    # ended a line at a form feed, a vertical tab, NEL and the Unicode separators, so a form
    # feed minted a key nobody wrote and every line after it was cited one too far.
    lines = re.split(r"\r\n|\r|\n", text)
    i = 0
    while i < len(lines):
        start = i
        raw = lines[i]
        # Blank, or a comment, by the format's white space alone (T610): a line led by a
        # no-break space declares a key, as Java reads it.
        stripped = raw.lstrip(_WHITE)
        if not stripped or stripped[0] in "#!":
            i += 1
            continue
        # Join continuations: a line that ENDS with an odd number of backslashes. Java counts
        # them at the line's very end, so a backslash before trailing white space continues
        # nothing (T610) - stripping first joined a line Java ends.
        buf = raw
        while buf.endswith("\\") and (len(buf) - len(buf.rstrip("\\"))) % 2 == 1:
            buf = buf[:-1]
            i += 1
            if i >= len(lines):
                break
            # T577: a continuation line's leading white space is not part of the value -
            # `java.util.Properties`' rule, and its white space: space, tab, form feed (one
            # reaches here since T607 stopped ending a line at it).
            buf += lines[i].lstrip(_WHITE)
        m = _DECLARATION.match(buf)
        if m:
            # The value as written: its leading white space went with the separator, and
            # Java keeps its trailing white space (T610).
            out.append((_unescape_key(m.group(1)), m.group(2), start + 1))
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

    A file that could not be READ is named the same way rather than collapsed: `read_text`
    folds an OSError to "" and `yaml.safe_load("")` is `None`, the same value an empty file
    yields, so a could-not-open would otherwise arrive as (None, "") -- indistinguishable from
    an empty document, and recorded by nobody. `read_text_reporting` supplies the reason.
    """
    _require_yaml()
    text, read_err = read_text_reporting(path)
    if read_err:
        return None, read_err
    try:
        return yaml.safe_load(text), ""
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

    The SAME collapse reaches this spelling one level down through the reader: `read_text`
    folds an OSError to "" and `safe_load_all("")` is `[]`, so a file that could not be OPENED
    returned ([], "") -- the empty-document value again -- and a caller looping over it reported
    nothing. That is item 76 wearing a could-not-read hat, and it is closed here: the read
    failure is reported through `read_text_reporting` before the parser is ever asked.
    """
    _require_yaml()
    text, read_err = read_text_reporting(path)
    if read_err:
        return [], read_err
    try:
        return [d for d in yaml.safe_load_all(text) if d is not None], ""
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
