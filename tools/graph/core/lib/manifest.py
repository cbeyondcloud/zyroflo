"""Content manifest driving incremental rebuilds and freshness checks.

Two jobs:
  * decide which extractors must re-run after a change (file digest per input glob);
  * stamp every build with per-repo commit SHAs, so a context pack can be refused when the
    graph no longer matches the code the developer actually has checked out.

That staleness stamp is load-bearing: a confident blast radius computed from a stale graph
is worse than no blast radius, because a human will act on it.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


#: Recorded when a file cannot be read. Never a valid sha1 (wrong length, non-hex), and
#: `changed_since` always treats it as changed.
UNREADABLE = "unreadable"


def file_digest(path: Path) -> str:
    """sha1 of file content (fast, and collision resistance is not a threat model here).

    An unreadable file yields UNREADABLE rather than "". Two builds that both failed to read
    the same file — a Windows lock, a permission blip — would otherwise compare equal, so
    the extractor would never re-run on it and the graph would stay stale for that file
    indefinitely, with nothing in the manifest to say so.
    """
    h = hashlib.sha1()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return UNREADABLE
    return h.hexdigest()


def git_head(repo_path: Path) -> str:
    """Current commit SHA, or '' when the path is not a git checkout."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def git_dirty(repo_path: Path) -> bool:
    """Whether the working tree has uncommitted changes.

    A dirty tree is not an error — impact must reflect the developer's actual code state —
    but it is recorded so a pack can say so instead of implying a clean commit.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), "status", "--porcelain"],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and bool(proc.stdout.strip())


@dataclass
class RepoStamp:
    alias: str
    head: str
    dirty: bool = False
    branch: str = ""


@dataclass
class Manifest:
    """Build fingerprint: what was read, from which commits, by which extractor version."""

    schema_version: int = SCHEMA_VERSION
    run_id: str = ""
    extractor_version: str = ""
    repos: dict[str, RepoStamp] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)  # extractor -> records emitted

    # DELIBERATELY NOT HERE: a `files` map of repo-qualified path -> sha1.
    #
    # It was written on every build by `record_files` and read by exactly one
    # function, `changed_since`, which nothing outside its own unit tests ever
    # called. The incremental decision that actually runs is `build.input_digests`
    # plus `build.stamp_matches`, which digest the same content per EXTRACTOR
    # rather than per file and are wired into the freshness check.
    #
    # It cost more than the dead weight suggests, and that is why it is gone
    # rather than merely unused. `.sdlc/graph/` is COMMITTED on purpose - it is
    # how the next clone inherits a baseline - so the map shipped into every
    # adopter's repository: measured at 7,502 entries and 1.34 MB on one real
    # install, rewritten on every build, and carrying `run_id`, so it churned
    # even when nothing changed. Worse, 40-hex digests read to gitleaks as
    # `generic-api-key`: an adopter's FIRST harness commit produced 269 secret
    # findings with no credential material anywhere among them. `prepass.py`'s
    # own docstring names the consequence - an unusable check gets switched off -
    # and the only ways past it were to exclude gitleaks or to exclude `.sdlc/`,
    # which is where the profile, the policy and the ledger live.

    # ── persistence ────────────────────────────────────────────────────────────
    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(self)
        payload["repos"] = {k: asdict(v) for k, v in self.repos.items()}
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        if not path.exists():
            return cls()
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        repos = {k: RepoStamp(**v) for k, v in raw.pop("repos", {}).items()}
        raw.pop("schema_version", None)
        # Every manifest COMMITTED before the map was removed still carries it,
        # and `cls(**raw)` would raise TypeError on an unexpected keyword - so
        # upgrading the harness would break reading the graph an adopter already
        # has. Dropped rather than rejected: it is a cache nothing reads, and
        # refusing a manifest for carrying it would make the fix the outage.
        raw.pop("files", None)
        return cls(schema_version=SCHEMA_VERSION, repos=repos, **raw)

    # ── incremental decisions ──────────────────────────────────────────────────
    def stale_repos(self, current: dict[str, str]) -> list[str]:
        """Repos whose HEAD has moved since this manifest was built.

        `build.py --incremental` runs at S0 of every SDLC run; intake blocks until this list
        is empty, which is what keeps a scope map honest.
        """
        return sorted(
            alias for alias, head in current.items()
            if alias in self.repos and self.repos[alias].head != head
        )
