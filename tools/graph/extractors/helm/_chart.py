"""A chart's `Chart.yaml` as helm's loader reads it (T624).

helm loads a chart only when its `Chart.yaml` unmarshals into `chart.Metadata` and passes
`Metadata.Validate()` - read from helm's own source at `release-3.16` (`pkg/chart/metadata.go`,
`dependency.go`, `chart.go`, `loader/load.go`), with Masterminds semver v3.3.0's `NewVersion`,
the version helm pins, for `version`, and `sigs.k8s.io/yaml` v1.4.0's conversion for scalars.
A chart helm refuses deploys nothing, so the deployments reader claims nothing from its values
file; one it loads is a library chart only where `type` reads `library` as helm reads the key -
matched to the field case-insensitively, as `encoding/json` matches one, in the order helm's
decoder meets the keys: sorted, since `sigs.k8s.io/yaml` writes the document out through
`json.Marshal` (review B21-2).

Not read here: a `requirements.yaml`, which helm unmarshals into the same metadata whatever the
`apiVersion`, so it can change `name`, `version` and `type`, and so the verdict (no reader is
handed it); a `values.yaml` helm cannot parse, which refuses the chart too (the reader's own
row says it was not parsed); and the refusal of a backslash in `name` that helm's
`filepath.Base` makes on Windows alone - a name helm refuses on one platform only is read as
accepted, as helm reads it on Linux. Of a chart's subcharts (`with_subcharts`), only those
holding a chart file are judged: an entry of `charts/` holding none, a `.tgz` subchart, and
one `.helmignore` hides from helm are not read.
"""
from __future__ import annotations

import math
import re
import struct
from typing import Any

#: `chart.Metadata`'s fields by their JSON names (helm 3.16), by the shape each takes.
_STRINGS = ("name", "home", "version", "description", "icon", "apiVersion", "condition",
            "tags", "appVersion", "kubeVersion", "type")
_STRING_LISTS = ("sources", "keywords")
_FIELDS = (*_STRINGS, *_STRING_LISTS, "deprecated", "annotations", "maintainers",
           "dependencies")
#: `chart.Maintainer`'s and `chart.Dependency`'s.
_MAINTAINER = ("name", "email", "url")
_DEPENDENCY_STRINGS = ("name", "version", "repository", "condition", "alias")
_DEPENDENCY = (*_DEPENDENCY_STRINGS, "tags", "enabled", "import-values")

#: Masterminds semver v3.3.0's `semVerRegex`, matched whole as `NewVersion` anchors it.
_SEMVER = re.compile(r"v?([0-9]+)(\.[0-9]+)?(\.[0-9]+)?"
                     r"(-([0-9A-Za-z\-]+(\.[0-9A-Za-z\-]+)*))?"
                     r"(\+([0-9A-Za-z\-]+(\.[0-9A-Za-z\-]+)*))?")
#: helm's `aliasNameFormat` (`pkg/chart/chart.go`).
_ALIAS = re.compile(r"[a-zA-Z0-9_-]+")
_UINT64 = 2 ** 64 - 1


class _Refused(Exception):
    """helm's loader refuses the chart; the message is helm's own wording where it has one."""


def verdict(doc: Any, error: str) -> tuple[str, str]:
    """(`library`, ""), (`chart`, ""), (`refused`, helm's reason) or, for a `Chart.yaml` that
    does not parse - which helm refuses too - (`unparsable`, the parser's reason): as helm's
    loader takes it."""
    if error:
        return "unparsable", f"cannot load Chart.yaml: {error}"
    try:
        metadata = _metadata(doc)
        _validate(metadata)
    except _Refused as refused:
        return "refused", str(refused)
    return ("library" if metadata["type"] == "library" else "chart"), ""


def semver_valid(version: str) -> bool:
    """Whether Masterminds semver v3.3.0's `NewVersion` parses `version`: the regex whole, each
    of major, minor and patch within 64 bits, and no numeric prerelease identifier with a
    leading zero."""
    match = _SEMVER.fullmatch(version)
    if match is None:
        return False
    for part in (match.group(1), (match.group(2) or "")[1:], (match.group(3) or "")[1:]):
        if part and int(part) > _UINT64:
            return False
    for ident in (match.group(5) or "").split(".") if match.group(5) else ():
        if ident.isdigit() and len(ident) > 1 and ident[0] == "0":
            return False
    return True


def _go_float32(value: float) -> str:
    """`strconv.FormatFloat(value, 'g', -1, 32)`, as `sigs.k8s.io/yaml` writes a YAML float
    into a string field: the shortest digits that round-trip as a float32, in `%e` form where
    the exponent is under -4, or 6 and over."""
    if math.isnan(value):
        return "NaN"
    try:
        single = struct.unpack("<f", struct.pack("<f", value))[0]
    except OverflowError:
        single = math.copysign(math.inf, value)
    if math.isinf(single):
        return "+Inf" if single > 0 else "-Inf"
    if single == 0:
        return "-0" if math.copysign(1.0, single) < 0 else "0"
    text = f"{single:.8e}"
    for digits in range(1, 10):
        text = f"{single:.{digits - 1}e}"
        if struct.unpack("<f", struct.pack("<f", float(text)))[0] == single:
            break
    mantissa, exponent = text.split("e")
    exp = int(exponent)
    sign = "-" if mantissa.startswith("-") else ""
    figures = mantissa.lstrip("-").replace(".", "").rstrip("0") or "0"
    if exp < -4 or exp >= 6:
        body = figures[0] + ("." + figures[1:] if len(figures) > 1 else "")
        return f"{sign}{body}e{'-' if exp < 0 else '+'}{abs(exp):02d}"
    point = exp + 1
    if point <= 0:
        return f"{sign}0.{'0' * -point}{figures}"
    if point >= len(figures):
        return sign + figures + "0" * (point - len(figures))
    return f"{sign}{figures[:point]}.{figures[point:]}"


def _text(value: Any, key: str) -> str | None:
    """A scalar as `sigs.k8s.io/yaml` writes it into a string field - an integer, a float and
    a boolean become text - or None for a null, which leaves the field as it was; a list or a
    mapping there is refused, as `encoding/json` refuses one for a string."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _go_float32(value)
    if isinstance(value, (list, dict)):
        shape = "a list" if isinstance(value, list) else "a mapping"
        raise _Refused(f"cannot load Chart.yaml: `{key}` is {shape}, not a string")
    return str(value)


def _key_text(key: Any) -> str:
    """A mapping key as `sigs.k8s.io/yaml` writes it into JSON: a boolean as `true` or
    `false`, a number as its text."""
    return ("true" if key else "false") if isinstance(key, bool) else \
        key if isinstance(key, str) else str(key)


def _null_key(value: Any) -> bool:
    """Whether a null key sits anywhere in the document: `sigs.k8s.io/yaml`'s conversion walks
    every mapping and refuses a key of no type it knows, so helm cannot load the chart
    whatever field the key sits under (review B21-21)."""
    if isinstance(value, dict):
        return any(key is None or _null_key(inner) for key, inner in value.items())
    if isinstance(value, list):
        return any(_null_key(inner) for inner in value)
    return False


def _keyed(doc: dict, fields: tuple[str, ...]) -> list[tuple[str, Any]]:
    """A mapping's pairs matched to a struct's fields as helm's decoder meets them: in the
    keys' sorted order - `sigs.k8s.io/yaml` writes the document as JSON through
    `json.Marshal`, which sorts a map's keys - each matched to the field named exactly, else
    the first equal once case is folded, as `encoding/json` matches one; a key naming no field
    is ignored. So where two keys match one field, the one sorting last wins, whichever was
    written last (review B21-2): for an all-lowercase name, the exact key over every case
    variant. A null key anywhere is refused before this (`_null_key`)."""
    texts = [(_key_text(key), value) for key, value in doc.items()]
    out = []
    for text, value in sorted(texts, key=lambda pair: pair[0]):
        field = text if text in fields else next(
            (f for f in fields if f.casefold() == text.casefold()), None)
        if field is not None:
            out.append((field, value))
    return out


def _strings(value: Any, key: str, current: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise _Refused(f"cannot load Chart.yaml: `{key}` is not a list")
    return [text for item in value if (text := _text(item, key)) is not None]


def _boolean(value: Any, key: str, current: bool) -> bool:
    if value is None:
        return current
    if not isinstance(value, bool):
        raise _Refused(f"cannot load Chart.yaml: `{key}` is not a boolean")
    return value


def _entries(value: Any, key: str, fields: tuple[str, ...]) -> list[dict | None]:
    """A list of structs: each a mapping read field by field, or a null kept for `Validate`
    to refuse; anything else refused, as `encoding/json` refuses it."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise _Refused(f"cannot load Chart.yaml: `{key}` is not a list")
    out: list[dict | None] = []
    for item in value:
        if item is None:
            out.append(None)
            continue
        if not isinstance(item, dict):
            raise _Refused(f"cannot load Chart.yaml: an entry of `{key}` is not a mapping")
        entry: dict[str, Any] = {}
        for field, inner in _keyed(item, fields):
            if field == "tags":
                entry[field] = _strings(inner, f"{key}.tags", [])
            elif field == "enabled":
                entry[field] = _boolean(inner, f"{key}.enabled", bool(entry.get(field)))
            elif field == "import-values":
                if inner is not None and not isinstance(inner, list):
                    raise _Refused(f"cannot load Chart.yaml: `{key}.import-values` is not a "
                                   f"list")
            elif (text := _text(inner, f"{key}.{field}")) is not None:
                entry[field] = text
        out.append(entry)
    return out


def _metadata(doc: Any) -> dict[str, Any]:
    """`chart.Metadata` as `yaml.Unmarshal` fills it, or `_Refused` where it cannot."""
    if doc is None:
        doc = {}
    if not isinstance(doc, dict):
        raise _Refused("cannot load Chart.yaml: it is not a mapping")
    if _null_key(doc):
        raise _Refused("cannot load Chart.yaml: a key is null")
    metadata: dict[str, Any] = {**{f: "" for f in _STRINGS}, **{f: [] for f in _STRING_LISTS},
                                "deprecated": False, "annotations": {}, "maintainers": [],
                                "dependencies": []}
    for field, value in _keyed(doc, _FIELDS):
        if field in _STRINGS:
            if (text := _text(value, field)) is not None:
                metadata[field] = text
        elif field in _STRING_LISTS:
            metadata[field] = _strings(value, field, metadata[field])
        elif field == "deprecated":
            metadata[field] = _boolean(value, field, metadata[field])
        elif field == "annotations":
            if value is not None and not isinstance(value, dict):
                raise _Refused("cannot load Chart.yaml: `annotations` is not a mapping")
            metadata[field] = {_key_text(k): _text(v, f"annotations.{_key_text(k)}")
                               for k, v in (value or {}).items()}
        elif field == "maintainers":
            metadata[field] = _entries(value, field, _MAINTAINER)
        else:
            metadata[field] = _entries(value, field, _DEPENDENCY)
    return metadata


#: Go's `unicode.IsSpace`: Python's `str.isspace` also counts U+001C to U+001F, which Go
#: drops as unprintable.
_GO_SPACE = frozenset("\t\n\v\f\r \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005"
                      "\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000")


def _sanitized(text: str) -> str:
    """helm's `sanitizeString`: white space as Go's `unicode.IsSpace` reads it becomes a
    space, and what does not print is dropped."""
    return "".join(" " if ch in _GO_SPACE else ch if ch.isprintable() else "" for ch in text)


def _base(path: str) -> str:
    """Go's `filepath.Base` on Linux."""
    if not path:
        return "."
    stripped = path.rstrip("/")
    return stripped.rsplit("/", 1)[-1] if stripped else "/"


def _validate(metadata: dict[str, Any]) -> None:
    """`Metadata.Validate()`, in its order. A missing `apiVersion` is never refused: the
    loader reads it as `v1` first."""
    name = _sanitized(metadata["name"])
    if not name:
        raise _Refused("chart.metadata.name is required")
    if name != _base(name):
        raise _Refused(f'chart.metadata.name "{name}" is invalid')
    version = metadata["version"]
    if not version:
        raise _Refused("chart.metadata.version is required")
    if not semver_valid(version):
        raise _Refused(f'chart.metadata.version "{version}" is invalid')
    if metadata["type"] not in ("", "application", "library"):
        raise _Refused("chart.metadata.type must be application or library")
    if any(m is None for m in metadata["maintainers"]):
        raise _Refused("maintainers must not contain empty or null nodes")
    seen: set[str] = set()
    for dependency in metadata["dependencies"]:
        if dependency is None:
            raise _Refused("dependencies must not contain empty or null nodes")
        named = _sanitized(dependency.get("name", ""))
        alias = dependency.get("alias", "")
        if alias and not _ALIAS.fullmatch(alias):
            raise _Refused(f'dependency "{named}" has disallowed characters in the alias')
        key = alias or named
        if key in seen:
            raise _Refused(f'more than one dependency with name or alias "{key}"')
        seen.add(key)
