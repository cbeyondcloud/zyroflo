"""T422: two installer messages said things that were not true.

1. A grown tree's refusal said a plain `--single-component` run "exits 0 but
   changes nothing". It exits 0 and WRITES `.sdlc/profile.yml.new` beside the
   profile it preserves - a file the installer's own next step (stage and commit
   everything) would then commit.
2. The estate preflight reprinted the vendored-tool verifier's own verdict -
   "init must fail closed here" - inside an install that goes on, next to the
   installer's line saying the tools fall back. Two adjacent sentences, one
   borrowed from a tool answering a different question.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools import third_party  # noqa: E402
from tools.tests import _project  # noqa: E402

INSTALLER = ROOT / "sdlc-init.py"


def _si():
    spec = importlib.util.spec_from_file_location("sdlc_init_messages", INSTALLER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


si = _si()


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _init(root: Path, *extra: str) -> subprocess.CompletedProcess:
    _project.seed(root)
    return subprocess.run([sys.executable, str(INSTALLER), "--target", str(root),
                           "--tools", "claude", *extra], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=600)


def test_the_grown_tree_refusal_says_what_a_plain_rerun_writes(tmp_path):
    """Proven by running the step the sentence describes and diffing the tree."""
    _write(tmp_path, "services/a/pom.xml", "<project/>\n")
    _write(tmp_path, "services/b/pom.xml", "<project/>\n")
    assert _init(tmp_path, "--single-component").returncode == 0
    _write(tmp_path, "services/c/pom.xml", "<project/>\n")      # 2 units -> 3

    refused = _init(tmp_path)
    assert refused.returncode == 1, refused.stdout

    profile = tmp_path / ".sdlc" / "profile.yml"
    before = profile.read_bytes()
    plain = _init(tmp_path, "--single-component")
    assert plain.returncode == 0, plain.stdout + plain.stderr
    wrote = tmp_path / ".sdlc" / "profile.yml.new"
    assert wrote.is_file() and profile.read_bytes() == before, (
        "the measured behaviour this sentence must describe: a .new beside a "
        "preserved profile")

    assert "changes nothing" not in refused.stdout, refused.stdout
    assert "profile.yml.new" in refused.stdout, refused.stdout


def test_the_estate_preflight_does_not_reprint_the_verifiers_verdict(monkeypatch, capsys):
    """Fed the verifier's REAL output for a failed check - produced by
    `third_party.cmd_verify` itself, not a copy of its sentences."""
    code = third_party.cmd_verify("no-such-platform")
    real = capsys.readouterr().out
    assert code == 1 and "fail closed" in real, "the control: the verdict is in there"

    monkeypatch.setattr(si, "run_capture", lambda cmd, cwd: (code, real))
    rows = si.estate_verify_rows(ROOT)
    text = "\n".join(rows)
    assert "fail closed" not in text, text
    assert "no asset declared" in text, "the per-tool rows are still reprinted"
    assert "fall back" in rows[-1] and "goes on" in rows[-1], rows[-1]
