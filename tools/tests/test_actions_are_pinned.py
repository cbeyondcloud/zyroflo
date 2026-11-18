"""Every GitHub Actions step this repository runs or ships names an immutable ref.

A tag such as ``astral-sh/setup-uv@v5`` can be moved by whoever controls the action's
repository, so a workflow that names one runs whatever that tag points at on the day it
runs. A full 40-hex commit sha cannot move. This covers the repository's own workflows and
the workflow templates the installer copies into other repositories, which run there with
that repository's token.

Allowed: ``owner/repo[/path]@<40-hex sha>`` (with the version in a trailing comment, by
convention), a local action (``./...``), and ``docker://image@sha256:<64 hex>``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCANNED_ROOTS = (ROOT / "sdlc_templates", ROOT / ".github" / "workflows")

# A `uses:` key at the start of a line, optionally as a list item. Anchored so a prose
# comment containing "causes:" or "uses:" mid-sentence is not read as a step.
USES_LINE = re.compile(r"""^\s*(?:-\s+)?uses:\s*["']?([^\s"'#]+)""")
PINNED_ACTION = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")
PINNED_DOCKER = re.compile(r"^docker://[^@\s]+@sha256:[0-9a-f]{64}$")


def is_pinned(ref: str) -> bool:
    if ref.startswith("./"):
        return True
    if ref.startswith("docker://"):
        return bool(PINNED_DOCKER.match(ref))
    return bool(PINNED_ACTION.match(ref))


def workflow_files() -> list[Path]:
    files: list[Path] = []
    for root in SCANNED_ROOTS:
        files += [p for p in root.rglob("*") if p.suffix in (".yml", ".yaml") and p.is_file()]
    return sorted(files)


def uses_refs() -> list[tuple[Path, int, str]]:
    found = []
    for path in workflow_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = USES_LINE.match(line)
            if match:
                found.append((path, number, match.group(1)))
    return found


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("astral-sh/setup-uv@d4b2f3b6ecc6e67c4457f6d3e41ec42d3d0fcb86", True),
        ("github/codeql-action/init@d4b2f3b6ecc6e67c4457f6d3e41ec42d3d0fcb86", True),
        ("./.github/actions/local", True),
        ("docker://alpine@sha256:" + "a" * 64, True),
        ("astral-sh/setup-uv@v5", False),
        ("astral-sh/setup-uv@v5.4.2", False),
        ("actions/checkout@main", False),
        ("actions/checkout@11d5960", False),
        ("actions/checkout@" + "A" * 40, False),
        ("actions/checkout", False),
        ("docker://alpine:3.20", False),
        ("docker://alpine@sha256:abc", False),
    ],
)
def test_the_pin_rule(ref: str, expected: bool) -> None:
    assert is_pinned(ref) is expected


def test_the_scan_finds_the_steps_it_is_meant_to_check() -> None:
    # An empty scan would pass the test below having checked nothing.
    refs = uses_refs()
    scanned = {path for path, _, _ in refs}
    assert ROOT / ".github" / "workflows" / "ci.yml" in scanned
    assert any(p.is_relative_to(ROOT / "sdlc_templates") for p in scanned)
    assert len(refs) >= 10, refs


def test_every_uses_is_pinned_to_a_full_commit_sha() -> None:
    unpinned = [
        f"{path.relative_to(ROOT).as_posix()}:{number}: {ref}"
        for path, number, ref in uses_refs()
        if not is_pinned(ref)
    ]
    assert not unpinned, (
        "pin each action by its full 40-hex commit sha, version in a trailing comment "
        "(`uses: owner/repo@<sha> # vX.Y.Z`):\n  " + "\n  ".join(unpinned)
    )
