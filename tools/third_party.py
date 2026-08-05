"""Vendored-binary pin/fetch/verify for third_party/.

The manifest is the contract: a pinned version + URL + sha256 per tool. Checksums are
recorded once (--pin, trust-on-first-use against the upstream release asset) and verified
on every run thereafter, so a swapped binary is caught before it executes.

    python tools/third_party.py --pin rg fd     # resolve latest, download, record sha256
    python tools/third_party.py --fetch         # fetch every pinned tool not yet present
    python tools/third_party.py --verify        # re-hash what's on disk vs the manifest

Run from the repo root.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from urllib.request import Request, urlopen

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
THIRD_PARTY = ROOT / "third_party"
MANIFEST = THIRD_PARTY / "manifest.json"
BIN_DIR = THIRD_PARTY / "bin"
UA = {"User-Agent": "sdlc-harness-third-party-fetcher"}


def load() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


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
    # Fall back to a loose match so a changed naming convention degrades to a clear
    # error listing the real asset names rather than a silent miss.
    stem = want.split("{")[0][:12]
    near = [a["name"] for a in assets if stem and stem in a["name"]]
    raise SystemExit(
        f"asset '{want}' not found.\n  candidates: {near or [a['name'] for a in assets][:12]}"
    )


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def extract_binary(blob: bytes, asset_name: str, binary: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if asset_name.endswith(".zip"):
        with tempfile.TemporaryDirectory() as td:
            zp = Path(td) / asset_name
            zp.write_bytes(blob)
            with zipfile.ZipFile(zp) as z:
                member = next(
                    (n for n in z.namelist() if Path(n).name.lower() == binary.lower()), None
                )
                if member is None:
                    raise SystemExit(f"{binary} not inside {asset_name}: {z.namelist()[:15]}")
                with z.open(member) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
    elif asset_name.endswith(".exe"):
        dest.write_bytes(blob)
    else:
        raise SystemExit(f"unsupported asset type for {asset_name} — extract {binary} by hand")


def cmd_pin(names: list[str]) -> int:
    m = load()
    for name in names:
        spec = m["tools"].get(name)
        if spec is None:
            print(f"!! unknown tool '{name}'")
            return 2
        version, assets = resolve_latest(spec["repo"])
        asset = pick_asset(assets, spec["asset_pattern"], version)
        blob = _get(asset["browser_download_url"])
        digest = sha256(blob)
        dest = BIN_DIR / spec["binary"]
        extract_binary(blob, asset["name"], spec["binary"], dest)
        spec.update(version=version, url=asset["browser_download_url"], sha256=digest,
                    installed_sha256=sha256(dest.read_bytes()))
        save(m)  # persist per tool: a later failure must not discard earlier pins
        print(f"pinned {name} {version}  sha256={digest[:16]}…  -> third_party/bin/{spec['binary']}")
    return 0


def cmd_fetch() -> int:
    """Install every pinned tool, verifying anything already on disk.

    A bare `if dest.exists(): continue` is the whole hole: a binary planted at
    `third_party/bin/rg.exe` before the first fetch would be adopted and executed without
    ever meeting a checksum, which is precisely what the pin exists to prevent. Present
    files are now matched against the recorded installed digest — mismatch fails closed,
    and no recorded digest means unvouched, so it is re-fetched and verified.
    """
    m = load()
    missing_pins = []
    for name, spec in m["tools"].items():
        if not spec["url"]:
            missing_pins.append(name)
            continue
        dest = BIN_DIR / spec["binary"]
        if dest.exists():
            recorded = spec.get("installed_sha256")
            installed = sha256(dest.read_bytes())
            if recorded == installed:
                continue
            if recorded:
                print(f"!! {name}: INSTALLED BINARY CHANGED since pin — refusing to trust {dest}."
                      f"\n   Delete it deliberately, then re-run --fetch.")
                return 1
            print(f"·  {name}: present but never verified — re-fetching to check it")
        blob = _get(spec["url"])
        if sha256(blob) != spec["sha256"]:
            print(f"!! {name}: CHECKSUM MISMATCH — refusing to install")
            return 1
        extract_binary(blob, Path(spec["url"]).name, spec["binary"], dest)
        spec["installed_sha256"] = sha256(dest.read_bytes())
        save(m)  # per tool, for the same reason cmd_pin saves per tool
        print(f"fetched {name} {spec['version']}")
    if missing_pins:
        print(f"not yet pinned (run --pin when their milestone arrives): {', '.join(missing_pins)}")
    return 0


def cmd_verify() -> int:
    """Re-hash the *asset* is not possible offline, so verify the extracted binary is
    present and record its own digest — the pin protects the download, this protects
    the installed file against later tampering."""
    m = load()
    bad = 0
    for name, spec in m["tools"].items():
        dest = BIN_DIR / spec["binary"]
        if not spec["url"]:
            print(f"—  {name}: unpinned (needed by {spec['needed_by']})")
        elif not dest.exists():
            print(f"!! {name}: pinned but MISSING at {dest}")
            bad += 1
        else:
            installed = sha256(dest.read_bytes())
            rec = spec.get("installed_sha256")
            if rec is None:
                spec["installed_sha256"] = installed
                print(f"ok {name} {spec['version']} (recorded installed digest)")
            elif rec != installed:
                print(f"!! {name}: INSTALLED BINARY CHANGED since pin — {dest}")
                bad += 1
            else:
                print(f"ok {name} {spec['version']}")
    save(m)
    if bad:
        print(f"\n{bad} problem(s) — init must fail closed here.")
    return 1 if bad else 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pin", nargs="+", metavar="TOOL")
    p.add_argument("--fetch", action="store_true")
    p.add_argument("--verify", action="store_true")
    a = p.parse_args()
    if a.pin:
        return cmd_pin(a.pin)
    if a.fetch:
        return cmd_fetch()
    if a.verify:
        return cmd_verify()
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
