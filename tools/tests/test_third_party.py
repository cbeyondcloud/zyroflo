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
import os
import tarfile
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


#: The platform these plumbing tests run against, regardless of the host. Fixed rather than
#: `current_platform()` so the suite asserts the same thing on every machine; the POSIX
#: specifics (tarballs, the exec bit, no `.exe`) get their own tests below.
PLAT = "windows-x86_64"


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    """Redirect the module's on-disk contract into tmp_path."""
    manifest = tmp_path / "manifest.json"
    bin_root = tmp_path / "bin"
    monkeypatch.setattr(tp, "MANIFEST", manifest)
    monkeypatch.setattr(tp, "BIN_ROOT", bin_root)

    def write(tools: dict) -> None:
        manifest.write_text(json.dumps({"schema_version": 2, "tools": tools}, indent=2),
                            encoding="utf-8")

    return type("Env", (), {"manifest": manifest, "bin_dir": bin_root / PLAT,
                            "bin_root": bin_root, "write": staticmethod(write)})


def spec(*, plat: str = PLAT, pattern: str = "t-v{version}.zip", **over) -> dict:
    """A schema-v2 tool entry: shared metadata, plus one asset block per platform."""
    asset = {"asset_pattern": pattern, "version": "", "url": "", "sha256": ""}
    for key in ("version", "url", "sha256", "installed_sha256"):
        if key in over:
            asset[key] = over.pop(key)
    base = {"repo": "o/r", "binary": "t", "needed_by": "M0", "purpose": "p",
            "assets": {plat: asset}}
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
        tp.cmd_pin(["a", "b"], PLAT)

    saved = json.loads(env.manifest.read_text(encoding="utf-8"))
    assert saved["tools"]["a"]["assets"][PLAT]["sha256"] == sha(blob), "the successful pin must survive"
    assert saved["tools"]["a"]["assets"][PLAT]["version"] == "1.0"
    assert saved["tools"]["b"]["assets"][PLAT]["sha256"] == "", "the failed pin must stay unrecorded"


# ── fetch: the security boundary ───────────────────────────────────────────────
def test_checksum_mismatch_refuses_to_install(env, monkeypatch):
    """A swapped binary must be rejected before it is ever written to disk."""
    good = zip_with("t.exe", b"GOOD")
    env.write({"a": spec(version="1.0", url="https://dl/t-v1.0.zip", sha256=sha(good))})
    monkeypatch.setattr(tp, "_get", lambda url, token=None: zip_with("t.exe", b"EVIL"))

    assert tp.cmd_fetch(PLAT) == 1, "must exit non-zero"
    assert not (env.bin_dir / "t.exe").exists(), "must not land on disk at all"


def test_matching_checksum_installs(env, monkeypatch):
    good = zip_with("t.exe", b"GOOD")
    env.write({"a": spec(version="1.0", url="https://dl/t-v1.0.zip", sha256=sha(good))})
    monkeypatch.setattr(tp, "_get", lambda url, token=None: good)

    assert tp.cmd_fetch(PLAT) == 0
    assert (env.bin_dir / "t.exe").read_bytes() == b"GOOD"


def test_unpinned_tools_are_reported_not_silently_skipped(env, capsys):
    env.write({"pinned_none": spec()})
    assert tp.cmd_fetch(PLAT) == 0
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
    assert tp.cmd_fetch(PLAT) == 0


def test_an_unverified_present_binary_is_refetched_and_verified(env, monkeypatch):
    """The planted-binary case. Anything sitting at the destination before the first fetch
    has never met a checksum; `if dest.exists(): continue` executed it on trust."""
    good = zip_with("t.exe", b"GOOD")
    env.write({"a": spec(version="1.0", url="https://dl/t-v1.0.zip", sha256=sha(good))})
    env.bin_dir.mkdir(parents=True)
    (env.bin_dir / "t.exe").write_bytes(b"PLANTED")
    monkeypatch.setattr(tp, "_get", lambda url, token=None: good)

    assert tp.cmd_fetch(PLAT) == 0
    assert (env.bin_dir / "t.exe").read_bytes() == b"GOOD", "the planted binary must be replaced"
    saved = json.loads(env.manifest.read_text(encoding="utf-8"))
    assert saved["tools"]["a"]["assets"][PLAT]["installed_sha256"] == sha(b"GOOD")


def test_a_tampered_binary_fails_fetch_closed(env, monkeypatch):
    """Once a digest is recorded, a changed file is tampering — not something to overwrite
    quietly, because a silent repair hides the fact that it happened at all."""
    env.write({"a": spec(version="1.0", url="https://dl/x.zip", sha256="deadbeef",
                         installed_sha256=sha(b"ORIGINAL"))})
    env.bin_dir.mkdir(parents=True)
    (env.bin_dir / "t.exe").write_bytes(b"TAMPERED")
    monkeypatch.setattr(tp, "_get", lambda url, token=None: b"x")

    assert tp.cmd_fetch(PLAT) == 1
    assert (env.bin_dir / "t.exe").read_bytes() == b"TAMPERED", "left in place as evidence"


# ── verify: tamper detection on the installed file ─────────────────────────────
def test_verify_flags_a_pinned_but_missing_binary(env):
    env.write({"a": spec(version="1.0", url="https://dl/x.zip", sha256="ab")})
    assert tp.cmd_verify(PLAT) == 1, "init must fail closed when a pinned tool is absent"


def test_verify_detects_a_binary_changed_since_pin(env):
    env.write({"a": spec(version="1.0", url="https://dl/x.zip", sha256="ab",
                         installed_sha256=sha(b"ORIGINAL"))})
    env.bin_dir.mkdir(parents=True)
    (env.bin_dir / "t.exe").write_bytes(b"TAMPERED")
    assert tp.cmd_verify(PLAT) == 1


def test_verify_passes_an_unchanged_binary(env):
    env.write({"a": spec(version="1.0", url="https://dl/x.zip", sha256="ab",
                         installed_sha256=sha(b"SAME"))})
    env.bin_dir.mkdir(parents=True)
    (env.bin_dir / "t.exe").write_bytes(b"SAME")
    assert tp.cmd_verify(PLAT) == 0


def test_verify_records_the_installed_digest_on_first_sight(env):
    """Trust-on-first-use, deliberately: the first verify has no prior digest to compare
    against, so it records what is there. That means a binary already tampered with
    BEFORE the first verify would be baptized as good -- the download-time sha256 in the
    manifest is what guards that window, and this only protects against later drift.
    """
    env.write({"a": spec(version="1.0", url="https://dl/x.zip", sha256="ab")})
    env.bin_dir.mkdir(parents=True)
    (env.bin_dir / "t.exe").write_bytes(b"FIRST")

    assert tp.cmd_verify(PLAT) == 0
    saved = json.loads(env.manifest.read_text(encoding="utf-8"))
    assert saved["tools"]["a"]["assets"][PLAT]["installed_sha256"] == sha(b"FIRST")


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
    assert tp.cmd_pin(["nope"], PLAT) == 2


# ── extraction ─────────────────────────────────────────────────────────────────
def test_extract_from_zip_finds_a_nested_binary(tmp_path: Path):
    dest = tmp_path / "bin" / "t.exe"
    tp.extract_binary(zip_with("t.exe", b"PAYLOAD"), "a.zip", "t.exe", dest)
    assert dest.read_bytes() == b"PAYLOAD"


def test_extract_bare_exe_passthrough(tmp_path: Path):
    dest = tmp_path / "bin" / "t.exe"
    tp.extract_binary(b"RAW", "tool.exe", "t.exe", dest)
    assert dest.read_bytes() == b"RAW"


@pytest.mark.parametrize("asset", ["tool.7z", "tool.msi", "tool.deb", "tool.pkg"])
def test_extract_rejects_an_unsupported_archive(tmp_path: Path, asset: str):
    """`.tar.gz` used to be the example here and is now supported, which is the point of
    this item. `.msi` stays on the list deliberately: d2 ships one for Windows, it is
    unpinned, and pinning it will hit this error rather than produce a broken binary."""
    with pytest.raises(SystemExit):
        tp.extract_binary(b"x", asset, "t.exe", tmp_path / "t.exe")


def test_extract_reports_when_the_binary_is_absent_from_the_archive(tmp_path: Path):
    with pytest.raises(SystemExit) as e:
        tp.extract_binary(zip_with("other.exe", b"x"), "a.zip", "t.exe", tmp_path / "t.exe")
    assert "not inside" in str(e.value)


# ── the committed manifest itself ──────────────────────────────────────────────
def test_committed_manifest_is_wellformed():
    """Guards the real file: every tool needs the fields fetch/verify dereference."""
    m = json.loads((Path(tp.__file__).resolve().parents[1] / "third_party" / "manifest.json")
                   .read_text(encoding="utf-8"))
    assert m["schema_version"] == tp.SCHEMA_VERSION
    for name, s in m["tools"].items():
        for key in ("repo", "binary", "needed_by", "assets"):
            assert key in s, f"{name} is missing '{key}'"
        assert not s["binary"].endswith(".exe"),             f"{name}: `binary` is the BASE name — the platform adds the suffix"
        assert set(s["assets"]) == set(tp.PLATFORMS),             f"{name} declares {sorted(s['assets'])}, not every platform in PLATFORMS"
        for plat, entry in s["assets"].items():
            for key in ("asset_pattern", "version", "url", "sha256"):
                assert key in entry, f"{name}/{plat} is missing '{key}'"
            if entry["url"]:
                assert len(entry["sha256"]) == 64, \
                    f"{name}/{plat} is pinned but has no valid sha256"


# ── item 74: the machine axis ─────────────────────────────────────────────────
def tar_with(name: str, payload: bytes, mode: str = "w:gz") -> bytes:
    """A tarball — how rg/fd/gitleaks/d2/difftastic all ship on Linux and macOS."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as t:
        info = tarfile.TarInfo(f"pkg-1.0/{name}")
        info.size = len(payload)
        t.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


@pytest.mark.parametrize("system,machine,expected", [
    ("Windows", "AMD64", "windows-x86_64"),      # Windows reports AMD64...
    ("Linux", "x86_64", "linux-x86_64"),         # ...Linux says x86_64 for the same chip
    ("Darwin", "arm64", "darwin-aarch64"),       # Apple silicon says arm64...
    ("Linux", "aarch64", "linux-aarch64"),       # ...Linux says aarch64
    ("Darwin", "x86_64", "darwin-x86_64"),
])
def test_arch_aliases_are_normalised(system: str, machine: str, expected: str):
    """`platform.machine()` is not normalised across operating systems. Without the alias
    map an ordinary 64-bit Windows box reports `AMD64`, matches no asset key, and the
    harness declares a fully supported platform unsupported."""
    assert tp.current_platform(system, machine) == expected
    assert expected in tp.PLATFORMS


@pytest.mark.parametrize("system,machine", [
    ("Plan9", "x86_64"), ("Linux", "riscv64"), ("SunOS", "sparc"),
])
def test_an_unknown_platform_raises_rather_than_guessing(system: str, machine: str):
    """A guess means downloading the wrong binary. Every vendored tool has a Python
    fallback, so the harness still runs — but it must SAY so rather than find no tools."""
    with pytest.raises(tp.UnsupportedPlatform):
        tp.current_platform(system, machine)


def test_the_binary_name_carries_exe_only_on_windows():
    """`binary` is the base name. Storing `rg.exe` in the manifest is what forced
    `repo_walk` to hardcode the same suffix and made every lookup Windows-only."""
    s = {"binary": "rg"}
    assert tp.binary_name(s, "windows-x86_64") == "rg.exe"
    assert tp.binary_name(s, "linux-x86_64") == "rg"
    assert tp.binary_name(s, "darwin-aarch64") == "rg"


def test_each_platform_gets_its_own_bin_directory():
    """A shared `bin/` lets a Windows .exe and a Linux ELF of the same tool overwrite each
    other on a checkout shared over a network drive or a container mount."""
    assert tp.bin_dir("linux-x86_64") != tp.bin_dir("windows-x86_64")
    assert tp.bin_dir("linux-x86_64").name == "linux-x86_64"


def test_an_undeclared_platform_fails_closed_and_names_itself():
    """THE failure this item exists to prevent. An unpinned platform must never degrade to
    'no tools available': a tool that resolves to nothing produces a scan that returns
    nothing, which reads exactly like a genuine absence."""
    s = {"assets": {"windows-x86_64": {}}}
    with pytest.raises(tp.UnsupportedPlatform) as e:
        tp.asset_for(s, "linux-aarch64", "rg")
    assert "linux-aarch64" in str(e.value)
    assert "windows-x86_64" in str(e.value), "the error must say what IS declared"


def test_fetch_names_the_platforms_it_has_no_asset_for(env, capsys):
    """Not a silent skip. The point of failing closed is that the operator learns which
    tools they do not have."""
    env.write({"a": spec(plat="darwin-aarch64", version="1.0", url="https://dl/x",
                         sha256="0" * 64)})
    assert tp.cmd_fetch(PLAT) == 0
    out = capsys.readouterr().out
    assert "NO ASSET DECLARED" in out and PLAT in out


def test_verify_fails_closed_on_an_undeclared_platform(env):
    env.write({"a": spec(plat="darwin-aarch64", version="1.0", url="https://dl/x",
                         sha256="0" * 64)})
    assert tp.cmd_verify(PLAT) == 1, "init must fail closed, not report an empty toolset"


# ── tarballs: the other half of the single-OS lock-in ─────────────────────────
@pytest.mark.parametrize("mode,suffix", [("w:gz", ".tar.gz"), ("w:gz", ".tgz"),
                                         ("w:bz2", ".tar.bz2"), ("w:xz", ".tar.xz")])
def test_extract_from_a_tarball(tmp_path: Path, mode: str, suffix: str):
    dest = tmp_path / "bin" / "rg"
    tp.extract_binary(tar_with("rg", b"ELF", mode), f"ripgrep-1.0{suffix}", "rg", dest)
    assert dest.read_bytes() == b"ELF"


def test_a_tarball_missing_the_binary_says_what_it_did_contain(tmp_path: Path):
    with pytest.raises(SystemExit) as e:
        tp.extract_binary(tar_with("other", b"x"), "a.tar.gz", "rg", tmp_path / "rg")
    assert "not inside" in str(e.value) and "other" in str(e.value)


def test_a_bare_extensionless_asset_is_the_binary(tmp_path: Path):
    """How osv-scanner and d2 ship on POSIX: no archive, the download IS the tool."""
    dest = tmp_path / "bin" / "osv-scanner"
    tp.extract_binary(b"ELF", "osv-scanner_linux_amd64", "osv-scanner", dest)
    assert dest.read_bytes() == b"ELF"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
@pytest.mark.parametrize("asset,make", [
    ("a.zip", lambda: zip_with("t", b"ELF")),
    ("a.tar.gz", lambda: tar_with("t", b"ELF")),
    ("t", lambda: b"ELF"),
])
def test_the_exec_bit_is_restored_on_posix(tmp_path: Path, asset: str, make):
    """Neither zipfile nor a raw byte write preserves it, and `tarfile.extractfile` returns
    a stream with no mode. Without the chmod the binary downloads correctly, verifies
    correctly, and then fails with Permission denied at the first call — which reads as a
    broken tool rather than a missing mode bit."""
    dest = tmp_path / "bin" / "t"
    tp.extract_binary(make(), asset, "t", dest)
    assert os.access(dest, os.X_OK), f"{asset} extracted without the exec bit"


# ── the resolver: the half that looks like a missing tool ─────────────────────
def test_repo_walk_resolves_the_per_platform_directory(monkeypatch, tmp_path: Path):
    """`_tool` used to look only for `{name}.exe` in a flat `bin/`, so even a correctly
    downloaded Linux binary was unfindable. That is the more insidious half of the lock-in:
    it presents as a missing tool rather than as a wrong path."""
    from tools.graph.core.lib import repo_walk

    plat = repo_walk._platform_key()
    exe = ".exe" if os.name == "nt" else ""
    vendored = tmp_path / plat
    vendored.mkdir(parents=True)
    (vendored / f"rg{exe}").write_bytes(b"binary")
    monkeypatch.setattr(repo_walk, "THIRD_PARTY_BIN", tmp_path)
    repo_walk._tool.cache_clear()
    try:
        assert repo_walk._tool("rg") == str(vendored / f"rg{exe}")
    finally:
        repo_walk._tool.cache_clear()


def test_the_two_platform_key_implementations_agree():
    """`repo_walk` cannot import `third_party`: it lives in `core/`, which may not reach up
    into `tools/`, and the vendored binaries are optional by design. So the few lines are
    duplicated, and this is what keeps the duplicate honest."""
    from tools.graph.core.lib import repo_walk

    assert repo_walk._platform_key() == tp.current_platform()


@pytest.mark.parametrize("missing", ["repo", "binary", "assets"])
def test_a_manifest_missing_a_required_key_names_it(env, missing: str):
    """The manifest is hand-edited to add a tool or a platform pattern. A missing key should
    name itself, not surface as a KeyError from three calls down."""
    s = spec()
    del s[missing]
    env.write({"a": s})
    with pytest.raises(SystemExit, match=missing):
        tp.load()


def test_a_binary_with_a_dot_exe_suffix_is_refused(env):
    """The v1 spelling. Left in place it would send every POSIX lookup hunting for a `.exe`
    — which fails as 'tool not found', the failure mode this whole item is about."""
    env.write({"a": spec(binary="t.exe")})
    with pytest.raises(SystemExit, match="BASE name"):
        tp.load()


def test_the_committed_manifest_declares_every_platform_for_every_tool():
    """A tool missing a platform is not a smaller feature set — it is a tool that vanishes
    on that OS, and `--fetch` can only name it if the manifest admits it exists."""
    m = json.loads((Path(tp.__file__).resolve().parents[1] / "third_party" / "manifest.json")
                   .read_text(encoding="utf-8"))
    for name, s in m["tools"].items():
        missing = set(tp.PLATFORMS) - set(s["assets"])
        assert not missing, f"{name} has no asset_pattern for {sorted(missing)}"
