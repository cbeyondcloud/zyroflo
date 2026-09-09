"""Vendored-binary pin/fetch/verify for third_party/ — per platform (manifest schema v2).

The manifest is the contract: a pinned version + URL + sha256 per tool **per platform**.
Checksums are recorded once (--pin, trust-on-first-use against the upstream release asset)
and verified on every run thereafter, so a swapped binary is caught before it executes.

    python tools/third_party.py --pin rg fd     # pin for THIS platform
    python tools/third_party.py --pin rg --platform linux-x86_64    # pin for another one
    python tools/third_party.py --fetch         # fetch every tool pinned for this platform
    python tools/third_party.py --verify        # re-hash what's on disk vs the manifest

## Why this is per platform (item 74)

Schema v1 had one `asset_pattern` per tool and one `bin/` directory, both Windows-only,
and `repo_walk` looked for `{name}.exe`. The harness therefore bootstrapped on exactly one
operating system — and the second half of that is worse than the first: even a correctly
downloaded Linux binary would never have been *found*.

**An unpinned platform fails closed**, with the platform named. The alternative is a tool
that resolves to nothing, a scan that returns no results, and an answer that is confidently
empty — the single highest-value failure class in this project, and the one an "empty means
clean" reading turns into a wrong answer rather than an error.

That sentence was true of a platform this manifest never DECLARED and false of a tool it
declared and never pinned: `--verify` printed `— d2: unpinned (needed by M2)` and returned
0, months after M2 closed. The rule was written down here and implemented one case short.
A tool that cannot be installed now fails closed too, unless it carries `optional` with the
reason its absence is survivable — per tool, and a reason rather than a flag, because a
bare `true` is what the next entry gets waved through on.

**Pinning is cheap across platforms**: it downloads and hashes an asset without executing
it, so one machine can pin all of them. Only `installed_sha256` is produced by extracting,
and that is still per platform rather than per machine — the same asset extracts to the
same bytes everywhere.

Run from the repo root.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform as _platform
import shutil
import stat
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import cli  # noqa: E402

cli.use_utf8()

ROOT = Path(__file__).resolve().parents[1]
THIRD_PARTY = ROOT / "third_party"
MANIFEST = THIRD_PARTY / "manifest.json"
BIN_ROOT = THIRD_PARTY / "bin"
UA = {"User-Agent": "sdlc-harness-third-party-fetcher"}

SCHEMA_VERSION = 2

#: Platform keys, `<os>-<arch>`. Not an arbitrary vocabulary — every asset block in the
#: manifest is keyed by one of these, and `current_platform()` must produce one.
PLATFORMS = ("windows-x86_64", "linux-x86_64", "linux-aarch64",
             "darwin-x86_64", "darwin-aarch64")

#: `platform.machine()` is not normalised across operating systems: the same 64-bit Intel
#: chip reports `AMD64` on Windows and `x86_64` on Linux, and Apple silicon reports `arm64`
#: while Linux reports `aarch64`. Mapping them is the difference between finding a pinned
#: asset and reporting the platform unsupported on hardware that is fully supported.
_ARCH_ALIASES = {
    "amd64": "x86_64", "x86_64": "x86_64", "x64": "x86_64",
    "arm64": "aarch64", "aarch64": "aarch64",
}


class UnsupportedPlatform(Exception):
    """This machine has no pinned assets. Always fatal — never a fallback to 'no tools'."""


def current_platform(system: str | None = None, machine: str | None = None) -> str:
    """`<os>-<arch>` for this machine, e.g. `linux-x86_64`.

    Raises rather than guessing. A guess here means downloading the wrong binary, and a
    silently-absent tool is indistinguishable from a tool that legitimately found nothing.
    """
    osname = (system or _platform.system()).lower()
    arch = _ARCH_ALIASES.get((machine or _platform.machine()).lower(), "")
    if osname not in ("windows", "linux", "darwin") or not arch:
        raise UnsupportedPlatform(
            f"unrecognised platform: {system or _platform.system()!r} / "
            f"{machine or _platform.machine()!r}\n"
            f"  supported: {', '.join(PLATFORMS)}\n"
            f"  The vendored tools all have pure-Python fallbacks, so the harness still "
            f"runs — but it must say so rather than silently find no tools."
        )
    return f"{osname}-{arch}"


def bin_dir(plat: str) -> Path:
    """One directory per platform. A shared `bin/` would let a Windows `.exe` and a Linux
    ELF of the same tool overwrite each other on a shared checkout."""
    return BIN_ROOT / plat


def binary_name(spec: dict, plat: str) -> str:
    """The on-disk filename. `binary` in the manifest is the BASE name; Windows adds `.exe`.

    Storing `rg.exe` in the manifest, as schema v1 did, is what forced `repo_walk` to
    hardcode the same suffix and made every lookup Windows-only.
    """
    return spec["binary"] + (".exe" if plat.startswith("windows") else "")


def asset_for(spec: dict, plat: str, name: str = "?") -> dict:
    """The asset block for a platform, or a fatal error naming what is missing."""
    assets = spec.get("assets") or {}
    if plat not in assets:
        raise UnsupportedPlatform(
            f"{name}: no asset declared for {plat}.\n"
            f"  declared: {', '.join(sorted(assets)) or 'none'}\n"
            f"  Add an `asset_pattern` for {plat} to third_party/manifest.json, then "
            f"`--pin {name} --platform {plat}`."
        )
    return assets[plat]


def load() -> dict:
    """Read and shape-check the manifest.

    Validated here, once, rather than guarded at each use: the manifest is hand-edited to
    add a tool or a platform pattern, and a missing key should name itself instead of
    surfacing as a KeyError from somewhere three calls down.
    """
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if m.get("schema_version") != SCHEMA_VERSION:
        raise SystemExit(
            f"third_party/manifest.json is schema_version {m.get('schema_version')}, "
            f"this tool reads {SCHEMA_VERSION}. v1 was single-platform; each tool now "
            f"carries an `assets` map keyed by {'/'.join(PLATFORMS[:2])}/…"
        )
    for name, spec in m.get("tools", {}).items():
        for key in ("repo", "binary", "assets"):
            if key not in spec:
                raise SystemExit(f"third_party/manifest.json: tool '{name}' has no '{key}'")
        if spec["binary"].endswith(".exe"):
            raise SystemExit(
                f"third_party/manifest.json: tool '{name}' has binary "
                f"'{spec['binary']}' — `binary` is the BASE name and the platform adds the "
                f"suffix, or the Linux lookup goes looking for a .exe"
            )
    return m


def save(m: dict) -> None:
    MANIFEST.write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8")


def _get(url: str, token: str | None = None) -> bytes:
    headers = dict(UA)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers), timeout=120) as r:
        return r.read()


def resolve_latest(repo: str) -> tuple[str, list[dict]]:
    """Return (version_without_v, assets) for a repo's latest GitHub release."""
    data = json.loads(_get(f"https://api.github.com/repos/{repo}/releases/latest"))
    return data["tag_name"].lstrip("v"), data["assets"]


def pick_asset(assets: list[dict], pattern: str, version: str) -> dict:
    want = pattern.format(version=version)
    for a in assets:
        if a["name"] == want:
            return a
    # List EVERY asset, not a prefix-filtered subset. A prefix filter shows the names most
    # like the one that failed — which are exactly the ones for the wrong platform. When
    # `fd-v10.4.2-x86_64-apple-darwin.tar.gz` was missing, the filtered list showed four
    # x86_64 builds and hid the only macOS asset there was, which is the one fact needed to
    # tell "my pattern is wrong" from "upstream does not build this".
    names = sorted(a["name"] for a in assets)
    shown = names[:25] + ([f"… and {len(names) - 25} more"] if len(names) > 25 else [])
    raise SystemExit(
        f"asset '{want}' not found in this release.\n  available:\n    "
        + "\n    ".join(shown)
        + "\n  Either the asset_pattern is wrong, or upstream does not build this "
          "platform — if the latter, set `unavailable` on the asset entry with the reason."
    )


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


#: Archive suffixes handled natively. The Linux and macOS builds of rg, fd, gitleaks, d2 and
#: difftastic all ship tarballs, so `.zip`-only support was half the reason the harness was
#: Windows-only.
_TARBALL_SUFFIXES = (".tar.gz", ".tgz", ".tar.xz", ".tar.bz2")


def _make_executable(dest: Path) -> None:
    """Restore the exec bit on POSIX.

    Neither `zipfile` nor a raw byte write preserves it, and `tarfile.extractfile` returns a
    stream with no mode attached. Without this the binary downloads correctly, verifies
    correctly, and then fails with Permission denied at the first call — which reads as a
    broken tool rather than a missing chmod.
    """
    if os.name != "nt":
        dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def extract_binary(blob: bytes, asset_name: str, binary: str, dest: Path) -> None:
    """Write `binary` out of `blob` to `dest`, whatever the archive format.

    Members are matched on BASE NAME, case-insensitively, so the same call finds `rg.exe`
    inside a Windows zip and `rg` inside a Linux tarball without the caller knowing which.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    lower = asset_name.lower()

    if lower.endswith(".zip"):
        with tempfile.TemporaryDirectory() as td:
            zp = Path(td) / "asset.zip"
            zp.write_bytes(blob)
            with zipfile.ZipFile(zp) as z:
                member = next(
                    (n for n in z.namelist() if Path(n).name.lower() == binary.lower()), None
                )
                if member is None:
                    raise SystemExit(f"{binary} not inside {asset_name}: {z.namelist()[:15]}")
                with z.open(member) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)

    elif lower.endswith(_TARBALL_SUFFIXES):
        with tempfile.TemporaryDirectory() as td:
            tp = Path(td) / "asset.tar"
            tp.write_bytes(blob)
            with tarfile.open(tp, "r:*") as t:
                member = next(
                    (mi for mi in t.getmembers()
                     if mi.isfile() and Path(mi.name).name.lower() == binary.lower()), None
                )
                if member is None:
                    names = [mi.name for mi in t.getmembers()][:15]
                    raise SystemExit(f"{binary} not inside {asset_name}: {names}")
                src = t.extractfile(member)
                if src is None:
                    raise SystemExit(f"{binary} in {asset_name} is not a regular file")
                with src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)

    elif lower.endswith(".exe") or "." not in Path(asset_name).name:
        # A bare, extensionless asset IS the binary — how osv-scanner and d2 ship on POSIX.
        dest.write_bytes(blob)

    else:
        raise SystemExit(
            f"unsupported asset type for {asset_name} — extract {binary} by hand into {dest}"
        )
    _make_executable(dest)


def cmd_pin(names: list[str], plat: str) -> int:
    """Pin tools for one platform. `plat` need not be this machine's.

    Pinning downloads and hashes; it never executes. That is what makes one machine able to
    pin every platform, and it is why `installed_sha256` — which does require extracting —
    is still recorded here rather than deferred: extraction is not execution.
    """
    m = load()
    for name in names:
        spec = m["tools"].get(name)
        if spec is None:
            print(f"!! unknown tool '{name}'  (known: {', '.join(sorted(m['tools']))})")
            return 2
        entry = asset_for(spec, plat, name)
        version, assets = resolve_latest(spec["repo"])
        asset = pick_asset(assets, entry["asset_pattern"], version)
        blob = _get(asset["browser_download_url"])
        digest = sha256(blob)
        member = binary_name(spec, plat)
        dest = bin_dir(plat) / member
        extract_binary(blob, asset["name"], member, dest)
        entry.update(version=version, url=asset["browser_download_url"], sha256=digest,
                     installed_sha256=sha256(dest.read_bytes()))
        save(m)  # persist per tool: a later failure must not discard earlier pins
        print(f"pinned {name} {version} for {plat}  sha256={digest[:16]}…  -> "
              f"third_party/bin/{plat}/{member}")
    return 0


def cmd_fetch(plat: str) -> int:
    """Install every tool pinned for `plat`, verifying anything already on disk.

    A bare `if dest.exists(): continue` is the whole hole: a binary planted at
    `third_party/bin/<platform>/rg` before the first fetch would be adopted and executed
    without ever meeting a checksum, which is precisely what the pin exists to prevent.
    Present files are matched against the recorded installed digest — mismatch fails closed,
    and no recorded digest means unvouched, so it is re-fetched and verified.
    """
    m = load()
    missing_pins, undeclared, unavailable = [], [], []
    for name, spec in m["tools"].items():
        try:
            entry = asset_for(spec, plat, name)
        except UnsupportedPlatform:
            undeclared.append(name)
            continue
        if entry.get("unavailable"):
            unavailable.append(f"{name} ({entry['unavailable']})")
            continue
        if not entry.get("url"):
            missing_pins.append(name)
            continue
        member = binary_name(spec, plat)
        dest = bin_dir(plat) / member
        if dest.exists():
            recorded = entry.get("installed_sha256")
            installed = sha256(dest.read_bytes())
            if recorded == installed:
                _make_executable(dest)   # a fresh clone loses the mode bit
                continue
            if recorded:
                print(f"!! {name}: INSTALLED BINARY CHANGED since pin — refusing to trust {dest}."
                      f"\n   Delete it deliberately, then re-run --fetch.")
                return 1
            print(f"·  {name}: present but never verified — re-fetching to check it")
        blob = _get(entry["url"])
        if sha256(blob) != entry["sha256"]:
            print(f"!! {name}: CHECKSUM MISMATCH — refusing to install")
            return 1
        extract_binary(blob, Path(entry["url"]).name, member, dest)
        entry["installed_sha256"] = sha256(dest.read_bytes())
        save(m)  # per tool, for the same reason cmd_pin saves per tool
        print(f"fetched {name} {entry['version']} for {plat}")
    if missing_pins:
        print(f"not yet pinned for {plat} (run --pin when their milestone arrives): "
              f"{', '.join(missing_pins)}")
    if unavailable:
        # A different statement from "not yet pinned", and the difference matters: one is a
        # task someone will do, the other is a fact about upstream that no amount of pinning
        # will change. Collapsing them into one message sends people looking for a build
        # that was never published.
        print(f"NOT PUBLISHED by upstream for {plat}: {', '.join(unavailable)}\n"
              f"   The pure-Python fallback covers these; they are absent by upstream's "
              f"choice, not by omission here.")
    if undeclared:
        # Named, never silent. A tool that is simply absent looks to every caller exactly
        # like a tool that ran and found nothing.
        print(f"!! NO ASSET DECLARED for {plat}: {', '.join(undeclared)}\n"
              f"   These tools will fall back to their pure-Python paths where one exists, "
              f"and be unavailable where one does not.")
    return 0


HARNESS = Path(__file__).resolve().parents[1]

#: Tools whose absence of a consumer is DECLARED rather than a defect, with the
#: reason. Spelled out so a new one is a decision somebody writes down - the
#: `CONTRACT_ADAPTERS` idiom - and empty on purpose today: every pinned tool
#: currently earns its place, and `difft` was DROPPED rather than exempted when
#: this question was asked by hand on 2026-08-26.
UNCONSUMED_BY_DESIGN: dict[str, str] = {}


def _literals(path: Path) -> set[str]:
    """Every string constant in one shipped file, read as CODE.

    **Comments are excluded for free, and that is the case that matters**:
    `difft` sat pinned with its ONLY mention in a comment about tarball
    formats, and a rule that read the file as text would have called that a
    consumer. An AST never yields a comment, so parsing is the whole defence.

    Docstrings are NOT stripped, deliberately. A mutation pass asked whether
    they should be and the answer was measured: a docstring only false-matches
    when it is EXACTLY a tool name, since membership here is exact rather than
    substring - so stripping them was eight lines guarding a case nobody can
    write by accident. Prose describing a tool is not code reaching for it, and
    it does not match either way.
    """
    import ast

    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):        # pragma: no cover - unreadable file
        return set()
    return {n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def _shipped() -> list[Path]:
    """Harness source an adopter runs - tests excluded, because a tool used
    only by a test is a tool nothing ships."""
    found = [q for q in (HARNESS / "tools").rglob("*.py")
             if "tests" not in q.parts and q.name != "third_party.py"]
    init = HARNESS / "sdlc-init.py"
    return found + ([init] if init.is_file() else [])


def consumers(name: str, spec: dict | None = None) -> list[str]:
    """Shipped files that NAME this tool. The manifest itself does not count.

    A tool reaches shipped source either through `third_party`'s own lookup
    (`prepass.binary`, `pack`'s d2 render) or as its own constant
    (`release.TOOL = "gh"`), so the question asked is the one both answer:
    does any shipped module name it, in code rather than in prose?
    """
    wanted = {name, (spec or {}).get("binary") or name}
    out = []
    for q in _shipped():
        if wanted & _literals(q):
            out.append(q.relative_to(HARNESS).as_posix())
    return sorted(out)


def unearned(m: dict | None = None) -> list[tuple[str, str]]:
    """Every manifest entry that neither names an item nor has a consumer.

    **The gate `9cdcd9a` said was obvious and did not build.** `difft` sat
    pinned with no consumer anywhere until a human happened to look, and `gh`
    was in that state the day it was pinned - its `purpose` naming item 47 is a
    convention nothing enforced. This is the same rule `test_dependencies.py`
    applies to Python packages, one layer down among the binaries, where the
    declared-package check could never see it.

    Naming an ITEM is enough on its own: a tool pinned ahead of the work that
    will use it is a deliberate act, and `DECLARED_AHEAD_OF_THEIR_ITEM` is the
    idiom this repository already uses for exactly that. What is refused is an
    entry that says neither.
    """
    import re

    out = []
    for name, spec in (m or load())["tools"].items():
        if name in UNCONSUMED_BY_DESIGN:
            continue
        if re.search(r"\bitem \d+", str(spec.get("purpose", "")), re.I):
            continue
        if not consumers(name, spec):
            out.append((name, str(spec.get("purpose", "")).strip()))
    return out


def cmd_verify(plat: str) -> int:
    """Re-hashing the *asset* is not possible offline, so verify the extracted binary is
    present and record its own digest — the pin protects the download, this protects
    the installed file against later tampering.

    **An unpinned tool used to print its own defect and then pass.** The line read
    `—  d2: unpinned (needed by M2)` and this function returned 0, for a milestone
    that closed on 2026-08-21 — and `sdlc-init` spawns this and reprints it, so an
    adopter's install said the same thing and carried on. `needed_by` was a claim
    with exactly one reader in the tree, and that reader was this print statement.

    Three outcomes are now kept apart, which is what the exit code is for:

    * `unavailable` — upstream does not publish this platform, with the reason on
      the asset entry. A stated absence, not a problem.
    * `optional` — the tool improves an output that is canonical without it, with
      the reason on the tool. Unpinned is fine and is still SAID, because a silent
      absence and a designed one look identical from here.
    * neither — a tool this manifest says is needed, that nothing can install.
      That is a problem, and item 40 is why: `osv-scanner` sat declared at
      `needed_by: M4` and unpinned until a human happened to look.
    """
    m = load()
    bad = 0
    print(f"platform: {plat}")
    for name, purpose in unearned(m):
        print(f"!! {name}: nothing in shipped source names it and its purpose "
              f"names no item - a pinned binary nobody uses is the `difft` shape, "
              f"which sat here until a human happened to look. Name the item it "
              f"is ahead of in `purpose`, give it a consumer, or drop it. "
              f"(purpose: {purpose or 'none'})")
        bad += 1
    for name, spec in m["tools"].items():
        try:
            entry = asset_for(spec, plat, name)
        except UnsupportedPlatform:
            print(f"!! {name}: no asset declared for {plat} — unavailable here")
            bad += 1
            continue
        dest = bin_dir(plat) / binary_name(spec, plat)
        if entry.get("unavailable"):
            print(f"—  {name}: not published for {plat} ({entry['unavailable']}) — "
                  f"Python fallback in use")
        elif not entry.get("url"):
            why = spec.get("optional")
            if why:
                print(f"—  {name}: unpinned and OPTIONAL — {why}")
            else:
                print(f"!! {name}: unpinned, and the manifest says it is needed by "
                      f"{spec['needed_by']} — nothing can install it. Pin it "
                      f"(`--pin {name}`), or declare `optional` on the tool with "
                      f"the reason its absence is survivable.")
                bad += 1
        elif not dest.exists():
            print(f"!! {name}: pinned but MISSING at {dest}")
            bad += 1
        else:
            installed = sha256(dest.read_bytes())
            rec = entry.get("installed_sha256")
            if rec is None:
                entry["installed_sha256"] = installed
                print(f"ok {name} {entry['version']} (recorded installed digest)")
            elif rec != installed:
                print(f"!! {name}: INSTALLED BINARY CHANGED since pin — {dest}")
                bad += 1
            else:
                print(f"ok {name} {entry['version']}")
    save(m)
    if bad:
        print(f"\n{bad} problem(s) — init must fail closed here.")
    return 1 if bad else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pin", nargs="+", metavar="TOOL")
    p.add_argument("--fetch", action="store_true")
    p.add_argument("--verify", action="store_true")
    p.add_argument("--platform", choices=PLATFORMS, default=None,
                   help="target platform (default: this machine). Pinning downloads without "
                        "executing, so one machine can pin them all")
    a = p.parse_args(argv)
    try:
        plat = a.platform or current_platform()
    except UnsupportedPlatform as exc:
        print(f"!! {exc}")
        return 2
    if a.pin:
        return cmd_pin(a.pin, plat)
    if a.fetch:
        return cmd_fetch(plat)
    if a.verify:
        return cmd_verify(plat)
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
