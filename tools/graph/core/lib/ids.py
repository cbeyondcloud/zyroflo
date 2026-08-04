"""Identity primitives — the single source of truth for how ids are formed.

Identity bugs are the expensive kind: two spellings of one service silently become two
nodes and the blast radius is quietly wrong. So every id in the graph is built here, and
profiles compose these primitives rather than formatting their own strings.
"""
from __future__ import annotations

import re

from ..model import norm_id

# ── endpoint paths ──────────────────────────────────────────────────────────────
_PATH_VAR = re.compile(r"\{[^}/]*\}")
_MULTI_SLASH = re.compile(r"/{2,}")


def norm_path(path: str) -> str:
    """Normalize a URL path for identity: leading slash, no trailing slash, `{var}` -> `{}`.

    Path variables are erased because `/v1/user/{id}` and `/v1/user/{userId}` are the same
    endpoint declared by two teams with different taste in names.
    """
    if not path:
        return "/"
    p = path.strip().strip('"').strip("'")
    p = _PATH_VAR.sub("{}", p)
    p = _MULTI_SLASH.sub("/", p)
    if not p.startswith("/"):
        p = "/" + p
    if len(p) > 1:
        p = p.rstrip("/")
    return p


def join_path(*parts: str) -> str:
    """Join a context-path and a mapping into one normalized endpoint path."""
    joined = "/".join(p.strip().strip("/") for p in parts if p and p.strip().strip("/"))
    return norm_path("/" + joined)


# ── id constructors ─────────────────────────────────────────────────────────────
def repo_id(alias: str) -> str:
    return f"repo:{norm_id(alias)}"


def service_id(logical: str, repo_alias: str) -> str:
    """`svc:<logical>@<repo-alias>`.

    Identity is (logical name, owning repo) — not the logical name alone — because the same
    service can exist as variants in several repos. A curated canonical map decides which
    variant config and UI edges attach to; code edges always attach to their own variant.
    """
    return f"svc:{norm_id(logical)}@{norm_id(repo_alias)}"


def endpoint_id(path: str) -> str:
    return f"ep:{norm_path(path)}"


def topic_id(base: str) -> str:
    return f"topic:{norm_id(base)}"


def table_id(name: str, schema: str = "") -> str:
    return f"table:{norm_id(schema)}.{norm_id(name)}" if schema else f"table:{norm_id(name)}"


def index_id(name: str) -> str:
    return f"esindex:{norm_id(name)}"


def config_id(repo_alias: str, relpath: str) -> str:
    return f"cfg:{norm_id(repo_alias)}/{relpath.strip('/')}"


def test_id(repo_alias: str, relpath: str) -> str:
    return f"test:{norm_id(repo_alias)}/{relpath.strip('/')}"


def doc_id(repo_alias: str, relpath: str) -> str:
    return f"doc:{norm_id(repo_alias)}/{relpath.strip('/')}"


def spec_id(repo_alias: str, relpath: str) -> str:
    return f"spec:{norm_id(repo_alias)}/{relpath.strip('/')}"


def module_id(group: str, artifact: str) -> str:
    """Build-system module coordinate (Maven groupId:artifactId, npm scope/name, ...)."""
    return f"mod:{norm_id(group)}:{norm_id(artifact)}"


def ui_id(package: str, tree: str) -> str:
    return f"ui:{norm_id(package)}@{norm_id(tree)}"


def env_key_id(key: str) -> str:
    """A configuration key as an entity (env var / property), normalized per relaxed binding."""
    return f"envkey:{relaxed_key(key)}"


# ── key normalization ───────────────────────────────────────────────────────────
def relaxed_key(key: str) -> str:
    """Canonical form for a config key under relaxed binding.

    `example.registry.host`, `EXAMPLE_REGISTRY_HOST` and `example-registry-host` all name one setting; collapsing
    them is what lets a property read in code join to the value set in a chart or env file.
    """
    return re.sub(r"[._-]+", ".", key.strip()).strip(".").lower()
