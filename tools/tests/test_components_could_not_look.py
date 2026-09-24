"""T128: a profile nothing could read is refused, never read as a one-component workspace.

`policy.components` synthesised ONE component, named for the target, whenever it
found no `- name:` entry - including for a profile that is a top-level list, one
that is not YAML at all, and one with no key in it (measured on main 2026-09-24).
So `worktree.overlap()`, `deploy`, `release` and `suite` answered about a single
repository for an estate nothing could read: could-not-look, folded into an
answer. The owner's ruling: synthesise only when the reader read at least one
top-level key and none of them is `components`; otherwise refuse by name. A
PyYAML failure alone stays a printed limit (the 2026-09-01 ruling), and a
repository with NO profile is still the one repository it always was.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import policy as P  # noqa: E402


def _profile(tmp_path: Path, text: str) -> Path:
    root = tmp_path / "estate"
    (root / ".sdlc").mkdir(parents=True)
    (root / ".sdlc" / P.PROFILE_NAME).write_text(text, encoding="utf-8")
    return root


@pytest.mark.parametrize("text", ["- one\n- two\n", "{{{ not yaml\n",
                                  "just words\nmore words\n", ""],
                         ids=["top-level-list", "not-yaml", "no-key", "empty"])
def test_a_profile_with_no_readable_key_is_refused_by_name(tmp_path, text):
    root = _profile(tmp_path, text)
    with pytest.raises(P.ProfileError) as caught:
        P.components(root)
    assert P.PROFILE_NAME in str(caught.value), caught.value


def test_a_components_block_the_reader_cannot_read_is_refused(tmp_path):
    """Declared, and nothing in it the reader recognises as a component: an estate
    whose components could not be read is not a single repository either."""
    root = _profile(tmp_path, "stack: java\ncomponents:\n  - path: services/a\n")
    with pytest.raises(P.ProfileError) as caught:
        P.components(root)
    assert "components" in str(caught.value), caught.value


def test_a_readable_profile_that_declares_no_components_is_still_one(tmp_path):
    """The control: the degenerate single-repository case keeps its one component."""
    (only,) = P.components(_profile(tmp_path, "stack: python\ngate_profile: solo\n"))
    assert only["name"] == "estate" and only.get("synthesised") is True


def test_no_profile_at_all_is_still_the_one_repository(tmp_path):
    """Absent is not unreadable: a repository nobody has installed into is the
    single repository it always was, and every caller's default depends on that."""
    (only,) = P.components(tmp_path)
    assert only.get("synthesised") is True
