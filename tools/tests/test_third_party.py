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


# ── fetch must not write a manifest it has nothing new to record in ────────────
# MEASURED 2026-09-12 on a clean checkout: `save()` renders `json.dumps(m, indent=2)`
# through `write_text`, so on POSIX it writes LF while the committed manifest is the one
# tracked file in this repository whose INDEX copy is CRLF (`git ls-files --eol` →
# `i/crlf`, against `.gitattributes`' `* text=auto eol=lf`). Writing the UNCHANGED
# manifest there therefore produces `303 insertions(+), 303 deletions(-)` — and
# `git diff --ignore-cr-at-eol` over it is EMPTY, so not one value differs. A fetch step
# in CI would dirty the tree under test with pure line-ending churn, and this harness
# refuses a run against a tree it dirtied itself.
#
# The fix that holds whatever the line endings are is to not call the writer: a fetch
# whose download hashes to the digest already recorded has nothing new to record. The
# recording itself stays — `cmd_fetch` earns a digest against `entry["sha256"]`, which is
# why it kept `save()` when `cmd_verify` lost it.
def test_fetch_does_not_rewrite_the_manifest_when_the_digest_is_unchanged(env, monkeypatch):
    """The CI shape exactly: digest recorded, binary absent, download matches.

    Asserted on BYTES, which is the property a dirty-tree guard reads.
    """
    good = zip_with("t.exe", b"GOOD")
    env.write({"a": spec(version="1.0", url="https://dl/t-v1.0.zip", sha256=sha(good),
                         installed_sha256=sha(b"GOOD"))})
    monkeypatch.setattr(tp, "_get", lambda url, token=None: good)
    before = env.manifest.read_bytes()

    assert tp.cmd_fetch(PLAT) == 0
    assert (env.bin_dir / "t.exe").read_bytes() == b"GOOD", "it must still install"
    assert env.manifest.read_bytes() == before, "a fetch with nothing new to record must not write"


def test_fetch_does_not_call_the_writer_at_all_when_nothing_changed(env, monkeypatch):
    """The byte assertion above is only as strong as the rendering agreeing, so pin the
    behaviour directly: the writer is not reached. This is what makes the claim hold on a
    checkout whose line endings differ from what `save()` would emit."""
    good = zip_with("t.exe", b"GOOD")
    env.write({"a": spec(version="1.0", url="https://dl/t-v1.0.zip", sha256=sha(good),
                         installed_sha256=sha(b"GOOD"))})
    monkeypatch.setattr(tp, "_get", lambda url, token=None: good)

    def boom(m):
        raise AssertionError("cmd_fetch called save() with nothing new to record")
    monkeypatch.setattr(tp, "save", boom)

    assert tp.cmd_fetch(PLAT) == 0


def test_fetch_still_records_a_digest_it_did_not_have(env, monkeypatch):
    """The positive control for the two above, and the half of yesterday's ruling this
    keeps: a fetch that learns something new must still persist it."""
    good = zip_with("t.exe", b"GOOD")
    env.write({"a": spec(version="1.0", url="https://dl/t-v1.0.zip", sha256=sha(good))})
    monkeypatch.setattr(tp, "_get", lambda url, token=None: good)

    assert tp.cmd_fetch(PLAT) == 0
    saved = json.loads(env.manifest.read_text(encoding="utf-8"))
    assert saved["tools"]["a"]["assets"][PLAT]["installed_sha256"] == sha(b"GOOD")


def test_fetch_records_a_digest_that_differs_from_the_one_already_recorded(env, monkeypatch):
    """The other half of the control above: DIFFERENT, not merely absent.

    Every other test that reaches the recording path has no `installed_sha256` at all, so a
    write condition narrowed to `entry.get("installed_sha256") is None` passes all of them.
    MEASURED 2026-09-12 against that narrowing: the new binary lands on disk while the stale
    digest stays recorded, so the NEXT fetch reads `recorded != installed` with a truthy
    record and refuses -- "INSTALLED BINARY CHANGED since pin" -- wedging a run that nothing
    tampered with. This is the path a real download takes when the recorded digest is stale,
    and the write suppression must not reach it.
    """
    good = zip_with("t.exe", b"GOOD")
    env.write({"a": spec(version="1.0", url="https://dl/t-v1.0.zip", sha256=sha(good),
                         installed_sha256=sha(b"STALE"))})
    monkeypatch.setattr(tp, "_get", lambda url, token=None: good)

    assert tp.cmd_fetch(PLAT) == 0
    saved = json.loads(env.manifest.read_text(encoding="utf-8"))
    assert saved["tools"]["a"]["assets"][PLAT]["installed_sha256"] == sha(b"GOOD"), \
        "a digest that differs from the one recorded is something new to record"


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


# ── a check must not write the answer it reports (the T243 class) ─────────────
#
# `cmd_verify` used to do trust-on-first-use: no recorded digest meant it wrote
# whatever was on disk into the manifest and printed `ok <tool> (recorded
# installed digest)`. Both halves are wrong and the second is worse than the
# first. A file compared against NOTHING reported in the `ok` column, so a
# planted binary was certified by the gate whose whole job is to refuse it --
# and adopted as the pin, which means the SECOND run agreed. Measured on a
# sandbox copy of the shipped manifest: six digests dropped, six planted files,
# six `ok ... (recorded installed digest)` lines, and the manifest's own sha
# moved. `sdlc-init --estate` spawns this during an adopter's install, so the
# write also edited a committed harness file as a side effect of looking.
#
# Recording a digest stays a deliberate act, and it already has two homes that
# both earn it against something: `cmd_pin` records what it just downloaded and
# checksummed, `cmd_fetch` records what it just re-fetched and checksummed.
# `--verify` reads.
# Inverted below, not deleted: test_verify_records_the_installed_digest_on_first_sight.


def test_verify_never_writes_the_manifest_it_audits(env):
    """The headline: --verify is a reader. Byte-identical, every path."""
    env.write({"a": spec(version="1.0", url="https://dl/x.zip", sha256="ab")})
    env.bin_dir.mkdir(parents=True)
    (env.bin_dir / "t.exe").write_bytes(b"FIRST")
    before = env.manifest.read_bytes()

    tp.cmd_verify(PLAT)
    assert env.manifest.read_bytes() == before, \
        "--verify must not rewrite the manifest it is auditing"


def test_a_binary_with_no_recorded_digest_is_could_not_look_never_ok(env, capsys):
    """ABSENT is not VERIFIED. Nothing here has been compared with anything."""
    env.write({"a": spec(version="1.0", url="https://dl/x.zip", sha256="ab")})
    env.bin_dir.mkdir(parents=True)
    (env.bin_dir / "t.exe").write_bytes(b"FIRST")

    code = tp.cmd_verify(PLAT)
    out = capsys.readouterr().out
    assert code != 0, "an unvouched binary must fail the check, not pass it"
    assert "NEVER VERIFIED" in out
    assert not [ln for ln in out.splitlines() if ln.startswith("ok a")], \
        "a file checked against nothing must never report in the ok column"


def test_a_present_but_empty_recorded_digest_is_also_could_not_look(env, capsys):
    """`""` is the same absence wearing a different type, and it used to land in
    the `rec != installed` arm and be reported as TAMPERING -- a true statement
    for the wrong reason, which sends someone hunting a change that never
    happened."""
    env.write({"a": spec(version="1.0", url="https://dl/x.zip", sha256="ab",
                         installed_sha256="")})
    env.bin_dir.mkdir(parents=True)
    (env.bin_dir / "t.exe").write_bytes(b"FIRST")

    assert tp.cmd_verify(PLAT) != 0
    out = capsys.readouterr().out
    assert "NEVER VERIFIED" in out
    assert "CHANGED" not in out, "absent is not changed"


def test_verify_does_not_adopt_a_planted_binary_as_the_pin(env):
    """The supply-chain case, stated as the manifest sees it: after --verify,
    the planted file's digest must not have become the recorded pin."""
    env.write({"a": spec(version="1.0", url="https://dl/x.zip", sha256="ab")})
    env.bin_dir.mkdir(parents=True)
    (env.bin_dir / "t.exe").write_bytes(b"PLANTED")

    tp.cmd_verify(PLAT)
    saved = json.loads(env.manifest.read_text(encoding="utf-8"))
    assert saved["tools"]["a"]["assets"][PLAT].get("installed_sha256") != sha(b"PLANTED"), \
        "the audited file must never become the answer the audit compares against"


def test_verify_still_writes_nothing_when_everything_is_clean(env):
    """The positive control of the write ban: a fully verified, exit-0 run is
    also byte-identical, so `no write` is not an accident of the failure path."""
    env.write({"a": spec(version="1.0", url="https://dl/x.zip", sha256="ab",
                         installed_sha256=sha(b"SAME"))})
    env.bin_dir.mkdir(parents=True)
    (env.bin_dir / "t.exe").write_bytes(b"SAME")
    before = env.manifest.read_bytes()

    assert tp.cmd_verify(PLAT) == 0, "the premise: a clean tree verifies clean"
    assert env.manifest.read_bytes() == before


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


def test_an_unavailable_asset_reads_differently_from_an_unpinned_one(env, capsys):
    """Two different statements, and the difference matters: "not yet pinned" is a task
    someone will do, "not published" is a fact about upstream that no amount of pinning will
    change. Collapsing them sends people hunting for a build that never existed.

    Found for real: `fd` ships no Intel-Mac build at all.
    """
    s = spec()
    s["assets"][PLAT] = {"asset_pattern": "x", "version": None, "url": None, "sha256": None,
                         "unavailable": "upstream ships no build for this platform"}
    env.write({"a": s})
    assert tp.cmd_fetch(PLAT) == 0
    out = capsys.readouterr().out
    assert "NOT PUBLISHED" in out
    assert "not yet pinned" not in out, "an upstream absence is not a missing task"


def test_verify_does_not_fail_on_an_unavailable_asset(env, capsys):
    """It is reported, never a failure. The tools have Python fallbacks, so an upstream gap
    degrades performance rather than correctness — failing init over it would block a
    platform for a reason nobody can fix."""
    s = spec()
    s["assets"][PLAT] = {"asset_pattern": "x", "version": None, "url": None, "sha256": None,
                         "unavailable": "no build published"}
    env.write({"a": s})
    assert tp.cmd_verify(PLAT) == 0
    assert "not published" in capsys.readouterr().out


def test_a_missing_asset_error_lists_every_name_not_a_filtered_subset():
    """A prefix filter shows the names most like the one that failed — which are exactly the
    ones for the WRONG platform. When fd's Intel-Mac asset was missing, the filtered list
    showed four x86_64 builds and hid the only macOS asset there was: the one fact that
    distinguishes "my pattern is wrong" from "upstream does not build this"."""
    assets = [{"name": "t-v1.0-x86_64-linux.tar.gz"}, {"name": "t-v1.0-aarch64-apple.tar.gz"}]
    with pytest.raises(SystemExit) as e:
        tp.pick_asset(assets, "t-v{version}-x86_64-apple.tar.gz", "1.0")
    msg = str(e.value)
    assert "aarch64-apple" in msg, "the other-platform asset is the useful signal"
    assert "x86_64-linux" in msg


def test_the_committed_manifest_records_the_m0_tools_for_every_platform():
    """rg and fd are the M0 pair. Every platform must be either pinned or explicitly marked
    unavailable — `null` everywhere would mean nobody has checked."""
    m = json.loads((Path(tp.__file__).resolve().parents[1] / "third_party" / "manifest.json")
                   .read_text(encoding="utf-8"))
    for name in ("rg", "fd"):
        for plat, entry in m["tools"][name]["assets"].items():
            assert entry.get("url") or entry.get("unavailable"), \
                f"{name}/{plat} is neither pinned nor marked unavailable"


def test_the_committed_manifest_declares_every_platform_for_every_tool():
    """A tool missing a platform is not a smaller feature set — it is a tool that vanishes
    on that OS, and `--fetch` can only name it if the manifest admits it exists."""
    m = json.loads((Path(tp.__file__).resolve().parents[1] / "third_party" / "manifest.json")
                   .read_text(encoding="utf-8"))
    for name, s in m["tools"].items():
        missing = set(tp.PLATFORMS) - set(s["assets"])
        assert not missing, f"{name} has no asset_pattern for {sorted(missing)}"


# ── an unpinned tool used to report its own defect and then pass ─────────────
#
# `cmd_verify` printed `—  d2: unpinned (needed by M2)` and returned 0, for a
# milestone that closed on 2026-08-21 — and `sdlc-init` spawns this command and
# reprints its output, so an adopter's install said the same thing and carried
# on. `needed_by` had exactly one reader in the whole tree and it was that
# print statement.
#
# Item 40 is the cost: `osv-scanner` sat declared at `needed_by: M4` with
# correct asset patterns and no pins until a human happened to look. Nothing
# would have said so.


def _manifest(tmp_path: Path, **tool) -> Path:
    """A one-tool manifest on disk, with the tool's fields overridden."""
    spec = {"repo": "o/r", "binary": "t", "needed_by": "M0", "purpose": "p",
            "assets": {p: {"asset_pattern": "t-{version}.tar.gz", "version": "",
                           "url": None, "sha256": None}
                       for p in tp.PLATFORMS}}
    spec.update(tool)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"schema_version": tp.SCHEMA_VERSION,
                                "tools": {"t": spec}}), encoding="utf-8")
    return path


def _verify(monkeypatch, manifest: Path, plat: str = "linux-x86_64") -> int:
    monkeypatch.setattr(tp, "MANIFEST", manifest)
    return tp.cmd_verify(plat)


def test_a_tool_the_manifest_says_is_needed_and_cannot_install_is_a_problem(
        tmp_path, monkeypatch, capsys):
    """The defect itself: unpinned, not optional, and `needed_by` names a
    milestone. Nothing can install it and the check said nothing was wrong."""
    code = _verify(monkeypatch, _manifest(tmp_path, needed_by="M2"))
    out = capsys.readouterr().out
    assert code == 1, out
    assert "needed by M2" in out and "nothing can install it" in out


def test_an_optional_tool_may_be_unpinned_and_still_says_so(
        tmp_path, monkeypatch, capsys):
    """The negative control, and the reason this is a REASON rather than a
    flag: `d2` genuinely improves an output that is canonical without it, and
    a bare `true` would let the next entry be waved through without anybody
    saying why. It is still PRINTED - a designed absence and a silent one look
    identical from here unless one of them speaks."""
    code = _verify(monkeypatch, _manifest(
        tmp_path, optional="the text pack is canonical without it"))
    out = capsys.readouterr().out
    assert code == 0, out
    assert "OPTIONAL" in out and "the text pack is canonical without it" in out


def test_an_unavailable_platform_is_still_a_stated_absence_not_a_problem(
        tmp_path, monkeypatch, capsys):
    """The OTHER negative control, and the one that would break first if the
    two ideas were folded together. `unavailable` is per-PLATFORM and means
    upstream does not publish it — `fd` on darwin-x86_64 carries exactly that,
    with the version it was true of. `optional` is per-TOOL. A tool can be
    required everywhere and unavailable on one platform, which is `fd`."""
    spec = {"assets": {p: {"asset_pattern": "t", "version": "", "url": None,
                           "sha256": None,
                           "unavailable": "upstream ships no such build"}
                       for p in tp.PLATFORMS}}
    code = _verify(monkeypatch, _manifest(tmp_path, **spec))
    out = capsys.readouterr().out
    assert code == 0, out
    assert "Python fallback" in out


def test_the_committed_manifest_passes_its_own_rule():
    """The real file, held to the same rule the synthetic ones are. This is
    what would have failed at `839484f`, where `d2` and `difft` were unpinned,
    not optional, and named milestones that had already closed."""
    m = json.loads((Path(tp.__file__).resolve().parents[1] / "third_party"
                    / "manifest.json").read_text(encoding="utf-8"))
    owed = []
    for name, spec in m["tools"].items():
        for plat, entry in spec["assets"].items():
            if entry.get("url") or entry.get("unavailable"):
                continue
            if not spec.get("optional"):
                owed.append(f"{name}/{plat} is unpinned, not marked unavailable, "
                            f"and the manifest says it is needed by "
                            f"{spec.get('needed_by')!r}")
    assert owed == [], "\n".join(owed)


def test_an_optional_reason_has_to_say_something():
    """The escape hatch is a REASON, and a reason nobody has to write is a
    field that becomes `true` and then becomes noise. Same obligation
    `test_dependencies.py` puts on a declared-but-unimported package."""
    m = json.loads((Path(tp.__file__).resolve().parents[1] / "third_party"
                    / "manifest.json").read_text(encoding="utf-8"))
    for name, spec in m["tools"].items():
        why = spec.get("optional")
        if why is None:
            continue
        assert isinstance(why, str) and len(why) > 40, \
            f"{name}: `optional` needs the reason its absence is survivable"


def test_every_pinned_tool_is_still_verified_the_way_it_was(
        tmp_path, monkeypatch, capsys):
    """The guard's own guard: if `cmd_verify` had been made to return 1 for
    everything, every test above would pass for the wrong reason. A pinned,
    installed tool must still come back ok and exit 0.

    The recorded digest is part of the premise, not decoration: before T243 this
    entry carried none and still came back `ok`, because `cmd_verify` recorded
    whatever was on disk. A control that passes without ever comparing anything
    is the vacuity it exists to catch."""
    payload = b"a binary"
    spec = {"assets": {p: {"asset_pattern": "t", "version": "1.0",
                           "url": "https://example.invalid/t",
                           "sha256": "a" * 64,
                           "installed_sha256": sha(payload)}
                       for p in tp.PLATFORMS}}
    manifest = _manifest(tmp_path, **spec)
    monkeypatch.setattr(tp, "MANIFEST", manifest)
    monkeypatch.setattr(tp, "BIN_ROOT", tmp_path / "bin")
    dest = tp.bin_dir("linux-x86_64")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "t").write_bytes(payload)
    assert tp.cmd_verify("linux-x86_64") == 0
    assert "ok t 1.0" in capsys.readouterr().out


# ── item 113: a pinned binary must name an item or have a consumer ──────────
#
# The gate `9cdcd9a` said was obvious and did not build. `difft` sat pinned
# with no consumer anywhere until a human happened to look, and `gh` was in
# that state the day it was pinned — its `purpose` naming item 47 is a
# convention nothing enforced. Same rule `test_dependencies.py` applies to
# Python packages, one layer down among the binaries.


def test_the_shipped_manifest_earns_every_entry():
    """The real question, asked of the real manifest. Empty is the answer
    today and this is what keeps it so."""
    assert tp.unearned() == []


def test_the_gate_catches_the_difft_shape():
    """**The guard's own guard.** A rule that cannot fail is the vacuity this
    project keeps finding, so this is the historical case as data: no item in
    the purpose, no consumer in shipped source."""
    fake = {"tools": {"difft": {"purpose": "human-facing G3 diff render",
                                "needed_by": "M4"}}}
    found = tp.unearned(fake)
    assert [n for n, _ in found] == ["difft"]


def test_naming_an_item_is_enough_on_its_own():
    """A tool pinned AHEAD of the work that will use it is a deliberate act —
    `DECLARED_AHEAD_OF_THEIR_ITEM` is the idiom this repository already uses
    for exactly that, and `gh` was pinned that way for item 47."""
    ahead = {"tools": {"newtool": {"purpose": "pinned ahead of item 99",
                                   "needed_by": "M7"}}}
    assert tp.unearned(ahead) == []


def test_a_consumer_is_enough_on_its_own():
    real = {"tools": {"d2": {"purpose": "optional SVG", "needed_by": "M2"}}}
    assert tp.unearned(real) == []
    assert "tools/graph/pack.py" in tp.consumers("d2")


def test_a_comment_is_not_a_consumer():
    """**The historical case.** `difft`'s ONLY mention was a COMMENT about
    tarball formats, and a rule reading the file as text would have called that
    a consumer. An AST never yields a comment, so parsing is the whole
    defence."""
    import tempfile

    d = Path(tempfile.mkdtemp())
    (d / "prose.py").write_text(
        "# a comment mentioning difft" + chr(10) + "X = 1" + chr(10),
        encoding="utf-8")
    assert "difft" not in tp._literals(d / "prose.py")
    (d / "code.py").write_text('TOOL = "difft"' + chr(10), encoding="utf-8")
    assert "difft" in tp._literals(d / "code.py")


def test_the_manifest_module_is_not_its_own_consumer():
    """`third_party.py` manages the tools, so a tool name appearing in its own
    error messages must not count as something reaching for it. Inert today -
    measured, it names none - and kept as the guard against the day it does."""
    shipped = {q.name for q in tp._shipped()}
    assert "third_party.py" not in shipped
    assert shipped, "the sweep found no shipped source at all"


def test_an_unearned_entry_FAILS_the_check_rather_than_only_printing(monkeypatch,
                                                                     capsys):
    """**Printing without failing is the exact defect this gate was built
    after.** `--verify` used to print `d2: unpinned (needed by M2)` and return
    0, months after that milestone closed, and `sdlc-init` reprinted it while
    the install carried on.

    ISOLATED on purpose. The first cut used a fake tool with `assets: {}`,
    which `cmd_verify` already fails on for an unrelated reason - so the test
    passed for the wrong cause and a mutation dropping the increment survived
    it. An EMPTY manifest verifies clean, so the only variable here is the
    unearned entry."""
    empty = {"schema_version": 2, "tools": {}}
    monkeypatch.setattr(tp, "load", lambda: empty)
    monkeypatch.setattr(tp, "save", lambda *a, **k: None)

    monkeypatch.setattr(tp, "unearned", lambda m=None: [])
    assert tp.cmd_verify(tp.current_platform()) == 0, "the premise: clean is 0"

    monkeypatch.setattr(tp, "unearned", lambda m=None: [("difft", "a diff render")])
    assert tp.cmd_verify(tp.current_platform()) != 0,         "an unearned entry must FAIL the check, not merely print"
    assert "names no item" in capsys.readouterr().out

def test_an_exemption_must_be_declared_and_the_table_is_empty_today():
    """`UNCONSUMED_BY_DESIGN` is the `CONTRACT_ADAPTERS` idiom: a new one is a
    decision somebody writes down. It is empty because `difft` was DROPPED
    rather than exempted when this question was asked by hand."""
    assert tp.UNCONSUMED_BY_DESIGN == {}
    fake = {"tools": {"ghost": {"purpose": "nothing", "needed_by": "M9"}}}
    assert tp.unearned(fake), "the premise: it would be caught"


def _entries_declaring(m: dict, key: str) -> list[str]:
    """Every tool entry in `m` that carries `key` AT ALL - present-and-false
    counts, because a false promise written smaller is still written."""
    return sorted(name for name, spec in m["tools"].items() if key in spec)


def test_no_manifest_entry_carries_verify_attestation():
    """`verify_attestation` declared a check that cannot live where the flag
    is read. `--verify` runs under Tier 0's no-network promise, and `gh
    attestation verify` fetches from GitHub unless it is handed a vendored
    bundle AND a trusted root; the only viable shape is verify-AT-PIN, where
    the network is already open and the asset is already in hand. That is a
    different feature and no item has opened against it. Ruled dropped
    2026-09-09 - re-add only with an item for verify-at-pin.

    This pins the field OUT of the manifest, so the next person to reach for
    it meets the ruling rather than an empty printer.
    """
    assert _entries_declaring(tp.load(), "verify_attestation") == []


def test_the_walk_that_pins_verify_attestation_out_can_see_one():
    """Positive control for the test above. Blinding the walk - iterating
    nothing, or reading `.get()` truthiness instead of key presence - turns
    the pin green for the wrong reason, so the same helper is shown finding a
    declaration first. `False` is the value a truthiness read would miss.
    """
    synthetic = {"tools": {"quiet": {"purpose": "item 1"},
                           "loud": {"purpose": "item 2",
                                    "verify_attestation": False}}}
    assert _entries_declaring(synthetic, "verify_attestation") == ["loud"]


# ── what a PERSON sees: --verify spawned as a process ─────────────────────────
#
# `sdlc-init --estate` does not import this module; it spawns
# `tools/third_party.py --verify` and reprints the rows. So the contract that
# matters to an adopter is a process's exit code and stdout, and an in-process
# call cannot tell a refusal from a traceback. The sandbox is a minimal copy of
# the harness layout, because MANIFEST and BIN_ROOT are derived from __file__.


def _sandbox(tmp_path: Path, tools: dict) -> Path:
    """A runnable copy of the module's on-disk contract, rooted in tmp_path."""
    (tmp_path / "tools").mkdir(parents=True, exist_ok=True)
    src = Path(tp.__file__).resolve().parent
    for name in ("__init__.py", "cli.py", "third_party.py"):
        if (src / name).is_file():
            (tmp_path / "tools" / name).write_bytes((src / name).read_bytes())
    (tmp_path / "third_party").mkdir(parents=True, exist_ok=True)
    (tmp_path / "third_party" / "manifest.json").write_text(
        json.dumps({"schema_version": tp.SCHEMA_VERSION, "tools": tools}, indent=2),
        encoding="utf-8")
    return tmp_path


def _spawn_verify(root: Path, plat: str = PLAT):
    import subprocess
    import sys
    return subprocess.run(
        [sys.executable, str(root / "tools" / "third_party.py"),
         "--verify", "--platform", plat],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120)


def test_spawned_verify_refuses_an_unvouched_binary_and_leaves_the_manifest(tmp_path):
    """The whole T243 reproduction, as a process. Planted file, no recorded
    digest: a non-zero exit, a row that says so, no `ok`, and a manifest whose
    bytes did not move."""
    root = _sandbox(tmp_path, {"a": spec(version="1.0", url="https://dl/x.zip",
                                         sha256="ab",
                                         purpose="fixture for item 243")})
    binary = root / "third_party" / "bin" / PLAT / "t.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"PLANTED")
    manifest = root / "third_party" / "manifest.json"
    before = manifest.read_bytes()

    proc = _spawn_verify(root)
    assert proc.returncode != 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "NEVER VERIFIED" in proc.stdout, proc.stdout
    assert "recorded installed digest" not in proc.stdout
    assert "Traceback" not in proc.stderr, proc.stderr
    assert manifest.read_bytes() == before, \
        "an adopter's install must not rewrite a committed harness file"


def test_spawned_verify_says_ok_and_exits_0_for_a_vouched_binary(tmp_path):
    """The guard's own guard, spawned: the refusal above must not be this
    command answering `no` to everything."""
    root = _sandbox(tmp_path, {"a": spec(version="1.0", url="https://dl/x.zip",
                                         sha256="ab",
                                         installed_sha256=sha(b"SAME"),
                                         purpose="fixture for item 243")})
    binary = root / "third_party" / "bin" / PLAT / "t.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"SAME")
    manifest = root / "third_party" / "manifest.json"
    before = manifest.read_bytes()

    proc = _spawn_verify(root)
    assert proc.returncode == 0, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "ok a 1.0" in proc.stdout, proc.stdout
    assert manifest.read_bytes() == before


# ── --only: fetching a subset, and refusing to fetch nothing (item T309) ───────
#
# CI ran no vendored binary and had no step to fetch one, so 58 tests across 9 files passed
# there without ever invoking the tool they are about. MEASURED with a spawn tracer over
# those files: binaries present, 320 passed and 59 tests spawned a tool; binaries absent,
# 320 passed and ZERO spawned -- exit 0 both ways, the absent run 9.8s faster. The helper
# returns None when a tool is missing, so the accelerated path silently BECOMES the fallback
# and every parity assertion then compares the fallback with itself.
#
# The fix is a CI fetch of the five tools some test actually spawns. `--fetch` alone installs
# all six, including the one CI deliberately leaves out, so the fetch needed a subset
# selector -- and a
# subset selector needed to refuse rather than shrink, because a selection that quietly
# selects nothing installs nothing at exit 0, which is the same failure one layer up.


def _spawn_third_party(root: Path, *args: str, plat: str = PLAT):
    import subprocess
    import sys
    return subprocess.run(
        [sys.executable, str(root / "tools" / "third_party.py"), *args,
         "--platform", plat],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120)


def test_only_fetches_the_named_tool_and_leaves_the_rest_pinned(env, monkeypatch):
    """A subset fetch installs the named tool, skips the others, and must NOT drop
    them from the manifest it writes back.

    The second half is the dangerous one: filtering the loaded manifest and handing the
    filtered dict to `save()` would delete every unrequested tool's pin from a tracked
    file -- a one-line change that reads as narrowing a loop.
    """
    good = zip_with("t.exe", b"GOOD")
    env.write({"a": spec(version="1.0", url="https://dl/t-v1.0.zip", sha256=sha(good)),
               "b": spec(version="2.0", url="https://dl/t-v2.0.zip", sha256=sha(good))})
    fetched = []

    def fake_get(url, token=None):
        fetched.append(url)
        return good

    monkeypatch.setattr(tp, "_get", fake_get)

    assert tp.cmd_fetch(PLAT, ["a"]) == 0
    assert fetched == ["https://dl/t-v1.0.zip"], f"b must not be downloaded: {fetched}"
    saved = json.loads(env.manifest.read_text(encoding="utf-8"))
    assert sorted(saved["tools"]) == ["a", "b"], \
        "a subset fetch must not delete the unrequested tools' pins"
    assert saved["tools"]["b"]["assets"][PLAT]["sha256"] == sha(good), \
        "b's recorded pin must be byte-identical after a fetch that skipped it"


def test_only_names_what_it_skipped_rather_than_skipping_silently(env, monkeypatch, capsys):
    """A tool left out is NAMED. An absent tool is indistinguishable from a tool that
    ran and found nothing, which is this module's whole reason for existing."""
    good = zip_with("t.exe", b"GOOD")
    env.write({"a": spec(version="1.0", url="https://dl/a.zip", sha256=sha(good)),
               "b": spec(version="2.0", url="https://dl/b.zip", sha256=sha(good))})
    monkeypatch.setattr(tp, "_get", lambda url, token=None: good)

    assert tp.cmd_fetch(PLAT, ["a"]) == 0
    out = capsys.readouterr().out
    assert "not requested by --only" in out, out
    assert "b" in out, out


def test_spawned_only_refuses_a_tool_the_manifest_does_not_pin(tmp_path):
    """A typo in the CI step's tool list must be a refusal, not a smaller fetch.

    Spawned, because the question is what a person and a CI log see: an exit code that
    says something is wrong, and an unknown name that is named. In-process this cannot
    be told from a traceback.
    """
    good = zip_with("t.exe", b"GOOD")
    root = _sandbox(tmp_path, {"a": spec(version="1.0", url="https://dl/a.zip",
                                         sha256=sha(good))})
    proc = _spawn_third_party(root, "--fetch", "--only", "a", "nosuchtool")

    assert proc.returncode == 2, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "nosuchtool" in proc.stdout, proc.stdout
    assert "a" in proc.stdout, "the refusal must list what IS pinned"
    assert not (root / "third_party" / "bin" / PLAT / "t.exe").exists(), \
        "refusing must install nothing at all, including the name that was valid"


def test_spawned_only_without_fetch_is_refused(tmp_path):
    """`--only` with no `--fetch` printed help and exited 0, which tells a CI step that
    asked for four binaries that everything is fine and none are there."""
    root = _sandbox(tmp_path, {"a": spec(version="1.0", url="https://dl/a.zip",
                                         sha256="ab")})
    proc = _spawn_third_party(root, "--only", "a")

    assert proc.returncode == 2, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    assert "--only applies to --fetch" in proc.stdout, proc.stdout


def test_an_unreachable_upstream_exits_3_and_names_the_host(env, monkeypatch, capsys):
    """Could-not-look is its own answer, distinct from the bytes being wrong.

    Exit 1 already means "CHECKSUM MISMATCH -- refusing to install". Reusing it for a DNS
    failure makes a retryable outage indistinguishable from a tampered asset, and an
    unhandled URLError would reach CI as a traceback instead of a sentence.
    """
    from urllib.error import URLError

    env.write({"a": spec(version="1.0", url="https://dl.example.invalid/t-v1.0.zip",
                         sha256="ab")})

    def boom(url, token=None):
        raise URLError("getaddrinfo failed")

    monkeypatch.setattr(tp, "_get", boom)

    assert tp.cmd_fetch(PLAT, ["a"]) == tp.EXIT_UNREACHABLE
    assert tp.EXIT_UNREACHABLE != 1, "an outage must not share an exit code with bad bytes"
    out = capsys.readouterr().out
    assert "COULD NOT REACH" in out, out
    assert "dl.example.invalid" in out, "the host is the one fact that tells you where to look"
    assert not (env.bin_dir / "t.exe").exists()


# ── the CI step itself: the list, the ordering, and the refusal to degrade ─────

CI_WORKFLOW = Path(tp.__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def _suite_steps() -> list[dict]:
    import yaml
    return yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))["jobs"]["suite"]["steps"]


def _fetch_step() -> dict:
    hits = [s for s in _suite_steps()
            if "third_party.py" in str(s.get("run", "")) and "--fetch" in str(s.get("run", ""))]
    assert len(hits) == 1, f"expected exactly one vendored-binary fetch step, got {len(hits)}"
    return hits[0]


def _only_list() -> list[str]:
    """The tool names the CI step passes to `--only`, read off the step itself."""
    run = _fetch_step()["run"]
    assert "--only" in run, run
    _, _, tail = run.partition("--only")
    named = []
    for word in tail.split():
        if word.startswith("-") or not word.replace("-", "").replace("_", "").isalnum():
            break
        named.append(word)
    assert named, f"--only was passed no tool names: {tail[:80]!r}"
    return named


def test_ci_fetches_exactly_the_tools_some_test_spawns():
    """The step's tool list equals the MEASURED spawn set, with no second spelling.

    Fetching a tool this set does not name costs bytes for nothing; omitting one it does
    restores the vacuous pass this whole item is about. `SPAWNED_BY_TESTS` is the one
    place the set is written down, so this compares the workflow against it rather than
    against a literal repeated here.
    """
    named = _only_list()
    assert sorted(named) == sorted(tp.SPAWNED_BY_TESTS), \
        f"workflow fetches {sorted(named)}, measured spawn set is {sorted(tp.SPAWNED_BY_TESTS)}"


def test_the_spawn_set_records_membership_not_unchecked_counts():
    """`SPAWNED_BY_TESTS` names the tools some test spawns; it carries NO per-tool count.

    A count here would be welded to nothing: every reader in this module uses the
    membership -- `for name in`, `set(...)`, `sorted(...)` -- and none reads a number, and
    no test re-derives one from the suite, so a wrong count could not fail. It also went
    stale the moment a spawning test was added or removed. This guard keeps the counts
    dropped: the constant is a set of manifest-key strings, not a mapping to integers and
    not a set of (name, count) pairs -- so the docstring beside it has no number the suite
    can contradict.
    """
    from collections.abc import Mapping
    assert not isinstance(tp.SPAWNED_BY_TESTS, Mapping), (
        "SPAWNED_BY_TESTS carries per-tool counts again; nothing re-derives them, so a "
        "wrong count cannot fail -- a count welded to nothing is a comment with a colon in it")
    assert tp.SPAWNED_BY_TESTS, "the spawn set is empty; the measurement found nothing"
    for member in tp.SPAWNED_BY_TESTS:
        assert isinstance(member, str), (
            f"{member!r} is not a bare tool name; the set records membership only, and a "
            f"count or (name, count) pair reintroduces the number nothing checks")


def test_ci_does_not_fetch_a_pinned_tool_the_fetch_set_leaves_out():
    """A pinned tool the fetch set does not name must not be fetched.

    `gh` is the only one, and **not because no test spawns it** -- tests spawn it wherever
    it is installed. It is out because every reply path `release.py` has authenticates, so
    the spawns only re-read a local help page. This test enforces the RULING, which is why
    it asks the fetch set rather than asserting anything about spawns.

    The positive control for the test above: it would also pass if the workflow fetched
    everything and the constant listed everything."""
    pinned = set(json.loads(
        (Path(tp.__file__).resolve().parents[1] / "third_party" / "manifest.json")
        .read_text(encoding="utf-8"))["tools"])
    assert set(tp.SPAWNED_BY_TESTS) < pinned, \
        "the spawn set must be a STRICT subset of what is pinned, or the measurement found nothing"
    fetched = set(_only_list())
    for unspawned in sorted(pinned - set(tp.SPAWNED_BY_TESTS)):
        assert unspawned not in fetched, \
            f"{unspawned} is not in the measured fetch set, so CI must not fetch it"


def test_the_fetch_list_is_keyed_by_manifest_key_not_by_the_binary_it_spawns():
    """`ast-grep` ships a binary called `sg`. A spawn tracer therefore reports `sg`, and
    `--only sg` is not a thing the manifest pins.

    Found by this test's first run: the constant was keyed by what the tracer saw, so the
    CI step would have asked for a tool that does not exist. It was caught because `--only`
    refuses an unknown name -- the same two spellings of one tool reaching a path that does
    not refuse is the failure class CLAUDE.md names as the expensive one.
    """
    manifest = json.loads(
        (Path(tp.__file__).resolve().parents[1] / "third_party" / "manifest.json")
        .read_text(encoding="utf-8"))
    for key in tp.SPAWNED_BY_TESTS:
        assert key in manifest["tools"], f"{key!r} is not a manifest key"
    differing = {k: manifest["tools"][k]["binary"] for k in tp.SPAWNED_BY_TESTS
                 if manifest["tools"][k]["binary"] != k}
    # The positive control: if no tool in the set had a differing binary name, this test
    # would pass while asserting nothing about the distinction it exists for.
    assert differing, ("no tool in the fetch set spells its binary differently from its key, "
                       "so this test cannot tell the two namespaces apart")
    for key, binary in differing.items():
        assert binary not in manifest["tools"], (
            f"{binary!r} is both a binary name and a manifest key, which makes "
            f"`--only {binary}` silently mean something")


def test_the_ci_fetch_failure_is_not_tolerated():
    """No `continue-on-error`. A fetch whose failure is tolerated leaves the suite running
    against no binaries and printing 320 passed -- the exact condition the step removes."""
    step = _fetch_step()
    assert "continue-on-error" not in step, step
    assert step.get("if") in (None, ""), \
        "a conditional fetch is a fetch that can be skipped into the old behaviour"


def test_the_ci_fetch_runs_before_the_suite():
    """Order is the whole point: binaries installed after the tests have run are binaries
    no test could spawn."""
    names = [s.get("name", "") for s in _suite_steps()]
    runs = [str(s.get("run", "")) for s in _suite_steps()]
    fetch_at = next(i for i, r in enumerate(runs)
                    if "third_party.py" in r and "--fetch" in r)
    suite_at = next(i for i, n in enumerate(names) if n == "Suite")
    assert fetch_at < suite_at, f"fetch at step {fetch_at}, suite at {suite_at}"


def test_ci_sets_the_flag_that_makes_the_suite_refuse_a_silent_fallback():
    import yaml
    job = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))["jobs"]["suite"]
    assert job["env"].get("SDLC_REQUIRE_VENDORED_BINARIES") == "1", job["env"]


def test_the_tools_ci_fetches_resolve_when_the_suite_requires_them():
    """The guard that makes the fetch step's success mean something downstream.

    Deliberately not a skip when the flag is unset: a skip reddens CI under
    SDLC_FORBID_SKIPS and is invisible locally, so the unset case asserts the part that is
    true everywhere -- that every name in the measured spawn set is a tool this module can
    be asked to resolve. With the flag set, each must actually resolve, which is what turns
    a tolerated fetch failure into a named red instead of a vacuous green.
    """
    manifest = json.loads(
        (Path(tp.__file__).resolve().parents[1] / "third_party" / "manifest.json")
        .read_text(encoding="utf-8"))
    for name in tp.SPAWNED_BY_TESTS:
        assert name in manifest["tools"], f"{name} is spawned by tests but is not pinned"
    if os.environ.get("SDLC_REQUIRE_VENDORED_BINARIES") != "1":
        return
    # Asked of the module that INSTALLS them, so the question is "is it where the fetch
    # puts it", not "is something of that name somewhere on PATH".
    plat = tp.current_platform()
    here = tp.bin_dir(plat)
    unresolved = [n for n in tp.SPAWNED_BY_TESTS
                  if not (here / tp.binary_name(manifest["tools"][n], plat)).exists()]
    assert not unresolved, (
        f"SDLC_REQUIRE_VENDORED_BINARIES=1 but these did not resolve: {unresolved}. "
        f"The fetch step did not install them, and every parity assertion downstream is "
        f"now comparing the pure-Python fallback with itself while still passing.")
