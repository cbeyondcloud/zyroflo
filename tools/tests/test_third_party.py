"""Tests for the vendored-binary pin/fetch/verify contract.

This module decides whether a downloaded executable is allowed to run, and it had zero
coverage. It also already shipped one real bug: `save()` sat outside the pin loop, so a
failure on the second tool discarded the first tool's successful pin -- silent data loss
found by hand, not by test. That case is `test_a_later_failure_does_not_discard_an_earlier_pin`.

No network: `_get` is patched, and the manifest/bin paths are redirected into tmp_path.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from tools import third_party as tp


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def zip_with(name: str, payload: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"pkg-1.0/{name}", payload)
    return buf.getvalue()


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    """Redirect the module's on-disk contract into tmp_path."""
    manifest = tmp_path / "manifest.json"
    bin_dir = tmp_path / "bin"
    monkeypatch.setattr(tp, "MANIFEST", manifest)
    monkeypatch.setattr(tp, "BIN_DIR", bin_dir)

    def write(tools: dict) -> None:
        manifest.write_text(json.dumps({"schema_version": 1, "tools": tools}, indent=2),
                            encoding="utf-8")

    return type("Env", (), {"manifest": manifest, "bin_dir": bin_dir, "write": staticmethod(write)})


def spec(**over) -> dict:
    base = {"repo": "o/r", "asset_pattern": "t-v{version}.zip", "binary": "t.exe",
            "needed_by": "M0", "purpose": "p", "version": "", "url": "", "sha256": ""}
    base.update(over)
    return base


# ── the regression that shipped ────────────────────────────────────────────────
def test_a_later_failure_does_not_discard_an_earlier_pin(env, monkeypatch):
    """Pin A succeeds, pin B explodes. A must still be recorded.

    Before the fix the manifest was written once after the loop, so B's failure threw
    away A's checksum and the next run silently re-downloaded an unpinned binary.
    """
    env.write({"a": spec(), "b": spec()})
    blob = zip_with("t.exe", b"AAA")

    def fake_get(url, token=None):
        if "api.github.com" in url:
            repo_ok = True  # both resolve; the failure is in the download
            return json.dumps({"tag_name": "v1.0", "assets": [
                {"name": "t-v1.0.zip", "browser_download_url": "https://dl/a"}]}).encode()
        if url.endswith("/a") and fake_get.calls == 0:
            fake_get.calls += 1
            return blob
        raise OSError("network died mid-pin")
    fake_get.calls = 0
    monkeypatch.setattr(tp, "_get", fake_get)

    with pytest.raises(OSError):
        tp.cmd_pin(["a", "b"])

    saved = json.loads(env.manifest.read_text(encoding="utf-8"))
    assert saved["tools"]["a"]["sha256"] == sha(blob), "the successful pin must survive"
    assert saved["tools"]["a"]["version"] == "1.0"
    assert saved["tools"]["b"]["sha256"] == "", "the failed pin must stay unrecorded"


# ── fetch: the security boundary ───────────────────────────────────────────────
def test_checksum_mismatch_refuses_to_install(env, monkeypatch):
    """A swapped binary must be rejected before it is ever written to disk."""
    good = zip_with("t.exe", b"GOOD")
    env.write({"a": spec(version="1.0", url="https://dl/t-v1.0.zip", sha256=sha(good))})
    monkeypatch.setattr(tp, "_get", lambda url, token=None: zip_with("t.exe", b"EVIL"))

    assert tp.cmd_fetch() == 1, "must exit non-zero"
    assert not (env.bin_dir / "t.exe").exists(), "must not land on disk at all"


def test_matching_checksum_installs(env, monkeypatch):
    good = zip_with("t.exe", b"GOOD")
    env.write({"a": spec(version="1.0", url="https://dl/t-v1.0.zip", sha256=sha(good))})
    monkeypatch.setattr(tp, "_get", lambda url, token=None: good)

    assert tp.cmd_fetch() == 0
    assert (env.bin_dir / "t.exe").read_bytes() == b"GOOD"


def test_unpinned_tools_are_reported_not_silently_skipped(env, capsys):
    env.write({"pinned_none": spec()})
    assert tp.cmd_fetch() == 0
    assert "not yet pinned" in capsys.readouterr().out


def test_a_verified_binary_is_not_refetched(env, monkeypatch):
    """Skipping the download is only correct once the file on disk has been vouched for."""
    env.write({"a": spec(version="1.0", url="https://dl/x.zip", sha256="deadbeef",
                         installed_sha256=sha(b"EXISTING"))})
    env.bin_dir.mkdir(parents=True)
    (env.bin_dir / "t.exe").write_bytes(b"EXISTING")

    def boom(url, token=None):
        raise AssertionError("must not re-download an already-verified binary")
    monkeypatch.setattr(tp, "_get", boom)
    assert tp.cmd_fetch() == 0


def test_an_unverified_present_binary_is_refetched_and_verified(env, monkeypatch):
    """The planted-binary case. Anything sitting at the destination before the first fetch
    has never met a checksum; `if dest.exists(): continue` executed it on trust."""
    good = zip_with("t.exe", b"GOOD")
    env.write({"a": spec(version="1.0", url="https://dl/t-v1.0.zip", sha256=sha(good))})
    env.bin_dir.mkdir(parents=True)
    (env.bin_dir / "t.exe").write_bytes(b"PLANTED")
    monkeypatch.setattr(tp, "_get", lambda url, token=None: good)

    assert tp.cmd_fetch() == 0
    assert (env.bin_dir / "t.exe").read_bytes() == b"GOOD", "the planted binary must be replaced"
    saved = json.loads(env.manifest.read_text(encoding="utf-8"))
    assert saved["tools"]["a"]["installed_sha256"] == sha(b"GOOD")


def test_a_tampered_binary_fails_fetch_closed(env, monkeypatch):
    """Once a digest is recorded, a changed file is tampering — not something to overwrite
    quietly, because a silent repair hides the fact that it happened at all."""
    env.write({"a": spec(version="1.0", url="https://dl/x.zip", sha256="deadbeef",
                         installed_sha256=sha(b"ORIGINAL"))})
    env.bin_dir.mkdir(parents=True)
    (env.bin_dir / "t.exe").write_bytes(b"TAMPERED")
    monkeypatch.setattr(tp, "_get", lambda url, token=None: b"x")

    assert tp.cmd_fetch() == 1
    assert (env.bin_dir / "t.exe").read_bytes() == b"TAMPERED", "left in place as evidence"


# ── verify: tamper detection on the installed file ─────────────────────────────
def test_verify_flags_a_pinned_but_missing_binary(env):
    env.write({"a": spec(version="1.0", url="https://dl/x.zip", sha256="ab")})
    assert tp.cmd_verify() == 1, "init must fail closed when a pinned tool is absent"


def test_verify_detects_a_binary_changed_since_pin(env):
    env.write({"a": spec(version="1.0", url="https://dl/x.zip", sha256="ab",
                         installed_sha256=sha(b"ORIGINAL"))})
    env.bin_dir.mkdir(parents=True)
    (env.bin_dir / "t.exe").write_bytes(b"TAMPERED")
    assert tp.cmd_verify() == 1


def test_verify_passes_an_unchanged_binary(env):
    env.write({"a": spec(version="1.0", url="https://dl/x.zip", sha256="ab",
                         installed_sha256=sha(b"SAME"))})
    env.bin_dir.mkdir(parents=True)
    (env.bin_dir / "t.exe").write_bytes(b"SAME")
    assert tp.cmd_verify() == 0


def test_verify_records_the_installed_digest_on_first_sight(env):
    """Trust-on-first-use, deliberately: the first verify has no prior digest to compare
    against, so it records what is there. That means a binary already tampered with
    BEFORE the first verify would be baptized as good -- the download-time sha256 in the
    manifest is what guards that window, and this only protects against later drift.
    """
    env.write({"a": spec(version="1.0", url="https://dl/x.zip", sha256="ab")})
    env.bin_dir.mkdir(parents=True)
    (env.bin_dir / "t.exe").write_bytes(b"FIRST")

    assert tp.cmd_verify() == 0
    saved = json.loads(env.manifest.read_text(encoding="utf-8"))
    assert saved["tools"]["a"]["installed_sha256"] == sha(b"FIRST")


# ── asset selection: a changed naming convention must be loud ──────────────────
def test_pick_asset_exact_match():
    assets = [{"name": "t-v1.0.zip"}, {"name": "other.zip"}]
    assert tp.pick_asset(assets, "t-v{version}.zip", "1.0")["name"] == "t-v1.0.zip"


def test_pick_asset_miss_lists_real_candidates(capsys):
    """The fd pin failed exactly here: the pattern said `fd-{version}` and the real asset
    was `fd-v{version}`. The error must show what upstream actually publishes."""
    assets = [{"name": "fd-v10.4.2-x86_64-pc-windows-msvc.zip"}]
    with pytest.raises(SystemExit) as e:
        tp.pick_asset(assets, "fd-{version}-x86_64-pc-windows-msvc.zip", "10.4.2")
    assert "fd-v10.4.2-x86_64-pc-windows-msvc.zip" in str(e.value)


def test_unknown_tool_name_exits_nonzero(env):
    env.write({"a": spec()})
    assert tp.cmd_pin(["nope"]) == 2


# ── extraction ─────────────────────────────────────────────────────────────────
def test_extract_from_zip_finds_a_nested_binary(tmp_path: Path):
    dest = tmp_path / "bin" / "t.exe"
    tp.extract_binary(zip_with("t.exe", b"PAYLOAD"), "a.zip", "t.exe", dest)
    assert dest.read_bytes() == b"PAYLOAD"


def test_extract_bare_exe_passthrough(tmp_path: Path):
    dest = tmp_path / "bin" / "t.exe"
    tp.extract_binary(b"RAW", "tool.exe", "t.exe", dest)
    assert dest.read_bytes() == b"RAW"


def test_extract_rejects_an_unsupported_archive(tmp_path: Path):
    with pytest.raises(SystemExit):
        tp.extract_binary(b"x", "tool.tar.gz", "t.exe", tmp_path / "t.exe")


def test_extract_reports_when_the_binary_is_absent_from_the_archive(tmp_path: Path):
    with pytest.raises(SystemExit) as e:
        tp.extract_binary(zip_with("other.exe", b"x"), "a.zip", "t.exe", tmp_path / "t.exe")
    assert "not inside" in str(e.value)


# ── the committed manifest itself ──────────────────────────────────────────────
def test_committed_manifest_is_wellformed():
    """Guards the real file: every tool needs the fields fetch/verify dereference."""
    m = json.loads((Path(tp.__file__).resolve().parents[1] / "third_party" / "manifest.json")
                   .read_text(encoding="utf-8"))
    assert m["schema_version"] == 1
    for name, s in m["tools"].items():
        for key in ("repo", "asset_pattern", "binary", "needed_by", "url", "sha256"):
            assert key in s, f"{name} is missing '{key}'"
        if s["url"]:
            assert len(s["sha256"]) == 64, f"{name} is pinned but has no valid sha256"
