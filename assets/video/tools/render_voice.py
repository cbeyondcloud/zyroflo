#!/usr/bin/env python3
"""Render the deck narration to one WAV per scene, with Kokoro.

Audio first: the visuals are cut to the narration, never the reverse. This writes
`dist/voice/sNN.wav` plus `dist/voice/durations.json`, and those measured durations
become each slide's `T.dur`. Guessing scene lengths and stretching audio to fit is
what makes corporate video feel wrong.

Reads the narration from `deck/script.js` (the single source of truth) by evaluating
it in node, so there is only ever one copy of the words.

`PRONOUNCE` respellings are applied to the TTS text ONLY -- captions keep the true
spelling.

Usage:
    python tools/render_voice.py                # all scenes
    python tools/render_voice.py s02 s05        # just these
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 24_000                  # Kokoro's native rate
VOICE = "am_michael"         # American male; settled on the TokenLean build
LANG = "a"                   # American English

# Kokoro at speed 1.0 measured 1.65 w/s on this script -- about 100 wpm, which reads
# sluggish for an explainer. 1.12 lands near 120 wpm: still measured and authoritative
# (the subject is governance, not a product demo), without dragging.
SPEED = 1.12

GAP_SENTENCE = 0.22          # seconds of silence between sentences
GAP_PARAGRAPH = 0.50         # ... and between paragraphs
LEAD_IN = 0.25               # a beat before the first word
TAIL = 0.55                  # ... and after the last, so a cut never clips speech

ROOT = Path(__file__).resolve().parent.parent
DECK = ROOT / "deck"
OUT = ROOT / "dist" / "voice"


def load_scenes() -> tuple[list[dict], dict[str, str]]:
    """Evaluate deck/script.js in node and hand back SCENES + PRONOUNCE.

    Parsing JS with a regex would break the moment the narration contains a brace
    or a backtick, so the file is executed by the one interpreter that is certain
    to agree with the browser about what it means.
    """
    js = (
        "const fs=require('fs');"
        "global.window={};"
        f"eval(fs.readFileSync({json.dumps(str(DECK / 'script.js'))},'utf8'));"
        "process.stdout.write(JSON.stringify("
        "{scenes:window.SCENES,pronounce:window.PRONOUNCE||{}}));"
    )
    # NOT text=True: this box's locale is cp1252, so Python would decode node's UTF-8
    # em-dashes into "a-euro-quote" mojibake -- which Kokoro then SPEAKS (measured:
    # +1.85s of nonsense per dash). Decode explicitly.
    proc = subprocess.run(["node", "-e", js], capture_output=True)
    if proc.returncode != 0:
        raise SystemExit(f"could not read deck/script.js via node:\n{proc.stderr}")
    data = json.loads(proc.stdout.decode("utf-8"))
    return data["scenes"], data["pronounce"]


def spoken(text: str, pronounce: dict[str, str]) -> str:
    """Apply the respellings, longest key first.

    Longest-first matters: 'cbeyond.cloud/zyroflo' contains 'zyroflo', and replacing
    the short key first would leave a half-substituted string the voice reads wrong.
    """
    for key in sorted(pronounce, key=len, reverse=True):
        text = text.replace(key, pronounce[key])
    return text


def sentences(paragraph: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", paragraph.strip())
    return [p.strip() for p in parts if p.strip()]


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(SR * seconds), dtype=np.float32)


def render_scene(pipeline, scene: dict, pronounce: dict[str, str]) -> np.ndarray:
    chunks: list[np.ndarray] = [silence(LEAD_IN)]
    paragraphs = [p for p in scene["narration"].split("\n\n") if p.strip()]

    for p_i, para in enumerate(paragraphs):
        for s_i, sent in enumerate(sentences(para)):
            text = spoken(sent, pronounce)
            audio = np.concatenate(
                [
                    np.asarray(a, dtype=np.float32)
                    for _, _, a in pipeline(text, voice=VOICE, speed=SPEED)
                ]
            )
            chunks.append(audio)
            if s_i != len(sentences(para)) - 1:
                chunks.append(silence(GAP_SENTENCE))
        if p_i != len(paragraphs) - 1:
            chunks.append(silence(GAP_PARAGRAPH))

    chunks.append(silence(TAIL))
    return np.concatenate(chunks)


def main() -> int:
    only = {a for a in sys.argv[1:] if a.startswith("s")}
    scenes, pronounce = load_scenes()

    OUT.mkdir(parents=True, exist_ok=True)

    durations: dict[str, float] = {}
    existing = json.loads((OUT / "durations.json").read_text()) if (OUT / "durations.json").exists() else {}
    durations.update(existing)

    # Kokoro is loaded lazily -- a silent title/interstitial card (empty narration)
    # needs no model, so rendering only such scenes never imports or invokes it.
    pipeline = None
    for scene in scenes:
        sid = scene["id"]
        if only and sid not in only:
            continue
        print(f"  {sid} ...", end="", flush=True)
        if scene.get("narration", "").strip():
            if pipeline is None:
                try:
                    from kokoro import KPipeline
                except Exception as exc:                       # pragma: no cover
                    raise SystemExit(
                        f"kokoro is not importable ({exc}).\n"
                        "Install it with `pip install kokoro soundfile`, or fall back to Piper."
                    )
                pipeline = KPipeline(lang_code=LANG)
            audio = render_scene(pipeline, scene, pronounce)
        else:
            # A silent open/close card: hold for `hold` seconds on music alone. Its
            # length IS the duration, so the downstream timeline stays exact.
            audio = silence(float(scene.get("hold", 3.0)))
        path = OUT / f"{sid}.wav"
        sf.write(path, audio, SR)
        secs = round(len(audio) / SR, 3)
        durations[sid] = secs
        words = len(scene["narration"].split())
        kind = f"{words} words, {words / secs:.2f} w/s" if words else "silent, music only"
        print(f" {secs:6.2f}s  ({kind})  -> {path.name}")

    (OUT / "durations.json").write_text(json.dumps(durations, indent=2, sort_keys=True))

    total = sum(durations[s["id"]] for s in scenes if s["id"] in durations)
    print(f"\ntotal narration: {total:.1f}s  ({int(total // 60)}:{total % 60:04.1f})")
    print(f"durations -> {OUT / 'durations.json'}")
    print("\nNext: set each slide's T.dur (and script.js dur) from these measured values.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
