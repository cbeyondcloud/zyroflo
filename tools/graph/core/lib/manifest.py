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
    files: dict[str, str] = field(default_factory=dict)   # repo-qualified path -> digest
    counts: dict[str, int] = field(default_factory=dict)  # extractor -> records emitted

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
        return cls(schema_version=SCHEMA_VERSION, repos=repos, **raw)

    # ── incremental decisions ──────────────────────────────────────────────────
    def record_files(self, key_prefix: str, paths: list[Path], root: Path) -> None:
        for p in paths:
            try:
                rel = p.relative_to(root).as_posix()
            except ValueError:
                rel = p.as_posix()
            self.files[f"{key_prefix}/{rel}"] = file_digest(p)

    def changed_since(self, previous: "Manifest") -> set[str]:
        """Repo-qualified paths whose content differs (added, removed, or modified).

        A file we could not read counts as changed on principle: we do not know its content,
        so the only safe answer is to re-extract rather than assume last build's facts hold.
        """
        changed = {k for k, v in self.files.items()
                   if v == UNREADABLE or previous.files.get(k) != v}
        changed |= set(previous.files) - set(self.files)
        return changed

    def stale_repos(self, current: dict[str, str]) -> list[str]:
        """Repos whose HEAD has moved since this manifest was built.

        `build.py --incremental` runs at S0 of every SDLC run; intake blocks until this list
        is empty, which is what keeps a scope map honest.
        """
        return sorted(
            alias for alias, head in current.items()
            if alias in self.repos and self.repos[alias].head != head
        )
