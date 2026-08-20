"""Traversal rules: the damping table, out of the code and into the workspace's hands.

Item 21. The prototype's constants were transcribed, not examined — its own docstring
demanded this item examine them, and the examination changed two things:

* **`DG_DEPENDS_ON` along fell 0.6 → 0.2.** Depending on a library means its changes
  hit you (against 0.9 — the plan's explicit shared-library demand); it does not mean
  your changes hit the library. The unexamined 0.6 built a two-hop "sibling by shared
  utility" path (seed service → its dependencies → every other consumer of them) that
  put thousands of unrelated nodes above 0.3 on a real workspace.
* **Per-type damping cannot control per-NODE degree**, and that was the dominant noise:
  a transitively-reached hub (a service with 1,429 test files, a monolithic UI node
  with 5,540 URLs) dumped its whole world one hop later. Hence `hub_threshold`: a node
  the traversal merely REACHED spreads at strength × threshold/fan once its fan via
  one edge type exceeds the threshold. Three deliberate exemptions: **seeds** (their
  fan is the query — and the A/B gate's recorded winning mechanism is precisely the
  seed component's own facts ranking first); **`wide` types** (the integration chains:
  a topic reached transitively SHOULD hand you every consumer, a library every
  dependent — that coupling is the point); and small fans (≤ threshold). The default
  threshold is 20, the ranking's page size: a transitive hub may contribute at most
  about one page at full strength. Measured effect: hop-3 nodes above 0.3 fell
  4,785 → 346 and 726 → 113 on two real seeds, with consumer chains byte-identical.

The rules reach the tool as data: `DEFAULT_RULES` below (zero-dependency — no file,
no YAML needed), overridden per key by a `traversal_rules.yml` beside the graph
(`--rules` wins over auto-discovery; the build copies a profile's file there). A rules
file that exists but cannot be read or validated is a loud refusal, never a silent
fallback — rules silently diverging change every ranking while looking exactly right.

Per-hop Cypher is deliberately NOT built: the A/B gate validated this Python traversal
over the committed graph, and a Cypher twin would be a second implementation of the
same rules with drift risk — the platform-split lesson with a query language in it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RULES_BASENAME = "traversal_rules.yml"

#: (along, against, wide) per edge type. `along` traverses src→dst. `wide` marks the
#: integration chains exempt from hub attenuation.
DEFAULT_TABLE: dict[str, tuple[float, float, bool]] = {
    "DG_CALLS": (0.3, 0.9, False),
    "DG_PRODUCES": (1.0, 0.2, True),
    "DG_CONSUMES": (0.2, 0.9, True),
    "DG_WRITES_TABLE": (0.6, 0.8, True),
    "DG_DEPENDS_ON": (0.2, 0.9, True),
    "DG_BUILT_FROM": (0.9, 0.9, True),
    "DG_EXPOSES": (0.9, 0.9, False),
    "DG_OWNS_TABLE": (0.8, 0.8, False),
    "DG_CONFIGURES": (0.8, 0.8, False),
    "DG_TESTS": (0.8, 0.6, False),
    "DG_SPECIFIES": (0.7, 0.7, False),
    "DG_DEPLOYS": (0.6, 0.6, False),
    "DG_IN_REPO": (0.2, 0.2, False),
}


@dataclass(frozen=True)
class Rules:
    table: dict[str, tuple[float, float, bool]] = field(
        default_factory=lambda: dict(DEFAULT_TABLE))
    default: tuple[float, float] = (0.5, 0.5)
    noncanonical: float = 0.5
    hops: int = 4
    hub_threshold: int = 20

    def damping(self, edge_type: str) -> tuple[float, float]:
        row = self.table.get(edge_type)
        return (row[0], row[1]) if row else self.default

    def wide(self, edge_type: str) -> bool:
        row = self.table.get(edge_type)
        return row[2] if row else False


DEFAULT_RULES = Rules()


class RulesError(Exception):
    """A rules file that cannot be trusted. Always fatal, never a fallback."""


_TOP_KEYS = {"hops", "noncanonical", "hub_threshold", "default", "rules"}
_RULE_KEYS = {"along", "against", "wide"}


def _damp(value: Any, where: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) \
            or not 0.0 <= float(value) <= 1.0:
        raise RulesError(f"{where} must be a number in [0, 1], got {value!r}")
    return float(value)


def load_rules(path: Path) -> Rules:
    """Read and validate one rules file, merged over the defaults per edge type.

    Unknown TOP-LEVEL and per-rule keys are refused with the known set — an ignored
    key means the thing the author asked for silently did not happen. Unknown EDGE
    TYPES are accepted (a profile's own extractors mint new types) — the caller
    should surface which configured types the graph does not contain.
    """
    from tools.graph.core.lib import props

    if not props.yaml_available():
        raise RulesError(
            f"{path} exists but PyYAML is not installed, so it cannot be read — "
            f"refusing rather than silently ranking with different rules")
    data, error = props.load_yaml_reporting(path)
    if error:
        raise RulesError(f"{path} is not readable YAML: {error}")
    if not isinstance(data, dict):
        raise RulesError(f"{path} must be a mapping, got {type(data).__name__}")
    for key in data:
        if key not in _TOP_KEYS:
            raise RulesError(f"unknown key {key!r} in {path.name}."
                             f"\n  known keys: {', '.join(sorted(_TOP_KEYS))}")

    table = dict(DEFAULT_TABLE)
    rules = data.get("rules", {})
    if not isinstance(rules, dict):
        raise RulesError(f"`rules` in {path.name} must map edge types to "
                         f"{{along, against, wide}}")
    for edge_type, row in rules.items():
        if not isinstance(row, dict):
            raise RulesError(f"rules.{edge_type} must be a mapping, "
                             f"got {type(row).__name__}")
        for key in row:
            if key not in _RULE_KEYS:
                raise RulesError(f"unknown key {key!r} in rules.{edge_type}."
                                 f"\n  known keys: {', '.join(sorted(_RULE_KEYS))}")
        prev = table.get(str(edge_type), (*Rules().default, False))
        along = _damp(row["along"], f"rules.{edge_type}.along") \
            if "along" in row else prev[0]
        against = _damp(row["against"], f"rules.{edge_type}.against") \
            if "against" in row else prev[1]
        wide = row.get("wide", prev[2])
        if not isinstance(wide, bool):
            raise RulesError(f"rules.{edge_type}.wide must be true or false")
        table[str(edge_type)] = (along, against, wide)

    default = Rules().default
    if "default" in data:
        row = data["default"]
        if not isinstance(row, dict) or set(row) - {"along", "against"}:
            raise RulesError("`default` takes exactly {along, against}")
        default = (_damp(row.get("along", default[0]), "default.along"),
                   _damp(row.get("against", default[1]), "default.against"))

    hops = data.get("hops", Rules().hops)
    if not isinstance(hops, int) or isinstance(hops, bool) or hops < 1:
        raise RulesError(f"hops must be an integer >= 1, got {hops!r}")
    threshold = data.get("hub_threshold", Rules().hub_threshold)
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 1:
        raise RulesError(f"hub_threshold must be an integer >= 1, got {threshold!r}")
    noncanonical = _damp(data.get("noncanonical", Rules().noncanonical),
                         "noncanonical")

    return Rules(table=table, default=default, noncanonical=noncanonical,
                 hops=hops, hub_threshold=threshold)


def discover_rules(graph_dir: Path, explicit: Path | None = None) -> tuple[Rules, str]:
    """The rules for one graph: an explicit path, else the file the build placed
    beside the graph, else the defaults. Returns (rules, source-label)."""
    if explicit is not None:
        return load_rules(explicit), str(explicit)
    candidate = graph_dir / RULES_BASENAME
    if candidate.exists():
        return load_rules(candidate), str(candidate)
    return DEFAULT_RULES, "defaults"
