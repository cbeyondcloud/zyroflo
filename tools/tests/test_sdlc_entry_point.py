"""T448: `sdlc <subcommand>` is the path-based module CLI, byte for byte.

A second spelling, never a replacement: the same exit code and the same output
as `python tools/sdlc/<module>.py`, for every subcommand, spawned - a CLI's
contract is a process.
"""
from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.sdlc import __main__ as D  # noqa: E402

SUBS = D.subcommands()


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", cwd=str(ROOT), timeout=300)


def test_pyproject_declares_the_console_script():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert meta["project"]["scripts"]["sdlc"] == "tools.sdlc.__main__:main"


def test_the_subcommand_set_is_derived_and_covers_the_named_ones():
    """The plan names these; the set is derived, so it may hold more, never fewer."""
    for name in ("stage", "approve", "drive", "status", "metrics", "init"):
        assert name in SUBS, sorted(SUBS)
    assert "__main__" not in SUBS and not any(n.startswith("_") for n in SUBS)


@pytest.mark.parametrize("name", sorted(SUBS))
def test_help_is_the_path_based_forms_help(name):
    """Same exit code, same stdout: the dispatcher adds and removes nothing."""
    via = _run("-m", "tools.sdlc", name, "--help")
    direct = _run(str(SUBS[name]), "--help")
    assert via.returncode == direct.returncode, (via.stderr, direct.stderr)
    assert via.stdout == direct.stdout


def test_argv_reaches_the_module_untouched(tmp_path):
    """A real command, flags and all: `sdlc stage --target X --list`."""
    (tmp_path / ".sdlc").mkdir()
    (tmp_path / ".sdlc" / "profile.yml").write_text("", encoding="utf-8")
    via = _run("-m", "tools.sdlc", "stage", "--target", str(tmp_path), "--list")
    direct = _run(str(SUBS["stage"]), "--target", str(tmp_path), "--list")
    assert via.returncode == direct.returncode == 0, via.stdout + via.stderr
    assert via.stdout == direct.stdout


def test_a_refusal_keeps_its_exit_code(tmp_path):
    """Exit 2 from the module is exit 2 from `sdlc`, never flattened."""
    via = _run("-m", "tools.sdlc", "stage", "--target", str(tmp_path), "--check")
    direct = _run(str(SUBS["stage"]), "--target", str(tmp_path), "--check")
    assert via.returncode == direct.returncode == 2
    assert via.stdout == direct.stdout


def test_an_unknown_subcommand_is_refused_by_name_with_a_hint():
    done = _run("-m", "tools.sdlc", "stagee")
    assert done.returncode == 2
    assert "unknown subcommand 'stagee' - did you mean 'stage'?" in done.stderr


def test_no_subcommand_lists_them_all_and_exits_2():
    done = _run("-m", "tools.sdlc")
    assert done.returncode == 2
    for name in SUBS:
        assert f"  {name} " in done.stdout, name
    assert _run("-m", "tools.sdlc", "--help").returncode == 0


def test_the_listing_is_utf8_on_any_console():
    """The docstring lines carry em-dashes; a cp1252 pipe must still get them."""
    done = subprocess.run([sys.executable, "-m", "tools.sdlc", "--help"],
                          capture_output=True, cwd=str(ROOT), timeout=300)
    out = done.stdout.decode("utf-8")
    assert "\N{EM DASH}" in out and "\N{REPLACEMENT CHARACTER}" not in out
