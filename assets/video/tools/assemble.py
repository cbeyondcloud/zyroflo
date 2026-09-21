#!/usr/bin/env python3
"""Assemble the rendered scene clips + narration + music bed into the final MP4.

Passes, each leaving an intermediate so a re-cut is cheap:
  1. voice.wav   -- every scene's narration placed on the crossfade-adjusted timeline
  2. bed.wav     -- the approved 30s bed, looped with equal-power crossfades
  3. mix.wav     -- bed ducked under the voice (sidechain), limited
  4. silent.mp4  -- the seven clips cross-dissolved together
  5. captions    -- an .ass seated in the deck's reserved bottom 200px band
  6. the MP4     -- captions burned in, audio muxed, +faststart

Caption timings come from the rendered audio itself: render_voice.py inserts a known
silence between sentences, so the gaps are found by looking rather than guessed. If the
segment count ever disagrees with the sentence count, the scene falls back to splitting
proportionally by length and says so -- a wrong-but-silent caption track is worse than
a crude one that announces itself.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
DECK, DIST = ROOT / "deck", ROOT / "dist"
VOICE, SCENES = DIST / "voice", DIST / "scenes"
BED = ROOT / "music" / "bed-preview-30s.wav"

XFADE = 0.40          # cross-dissolve at each scene join
SR = 48_000           # delivery rate
BED_GAIN = -23.0      # dB, before ducking -- the voice then sits ~14 dB clear of the
                      # bed even before the sidechain engages. This is a narration-led
                      # piece: intelligibility beats atmosphere.
DUCK_DB = 16.0        # how far the bed drops under the voice

FFMPEG = subprocess.run(
    [sys.executable, "-c", "import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())"],
    capture_output=True, text=True, check=True).stdout.strip()


def ff(args: list[str], label: str) -> None:
    print(f"  {label} ...", end="", flush=True)
    p = subprocess.run([FFMPEG, "-y", "-hide_banner", "-loglevel", "error", *args],
                       capture_output=True, text=True)
    if p.returncode != 0:
        print(" FAILED")
        raise SystemExit(p.stderr[-3000:])
    print(" ok")


def load_scenes() -> list[dict]:
    js = ("const fs=require('fs');global.window={};"
          f"eval(fs.readFileSync({json.dumps(str(DECK / 'script.js'))},'utf8'));"
          "process.stdout.write(JSON.stringify(window.SCENES));")
    # Decode explicitly -- see render_voice.py: a cp1252 locale turns node's UTF-8
    # em-dashes into mojibake, which then burns straight into the captions.
    out = subprocess.run(["node", "-e", js], capture_output=True, check=True).stdout
    return json.loads(out.decode("utf-8"))


def sentences_of(narration: str) -> list[str]:
    import re
    out: list[str] = []
    for para in [p for p in narration.split("\n\n") if p.strip()]:
        out += [s.strip() for s in re.split(r"(?<=[.!?])\s+", para.strip()) if s.strip()]
    return out


def speech_spans(path: Path, min_gap: float = 0.15, eps: float = 1e-5) -> list[tuple[float, float]]:
    """Find the spoken stretches by locating the silences render_voice.py inserted.

    Keyed on *exact* digital silence, not on energy. Kokoro puts its own pauses at
    commas and em-dashes that are easily as long as the 0.22s we insert between
    sentences, so an energy threshold splits one sentence into three. The gaps we
    inserted are runs of literal zeros, which the voice itself never produces -- so
    that, and only that, is what we look for.
    """
    audio, sr = sf.read(path)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    hop = int(sr * 0.01)
    frames = len(audio) // hop
    peak = np.array([np.max(np.abs(audio[i * hop:(i + 1) * hop])) for i in range(frames)])
    hard_silent = peak <= eps

    spans, start = [], None
    gap_frames = int(min_gap / 0.01)
    i = 0
    while i < frames:
        if not hard_silent[i]:
            if start is None:
                start = i
            i += 1
            continue
        j = i
        while j < frames and hard_silent[j]:
            j += 1
        if start is not None and (j - i) >= gap_frames:
            spans.append((start * 0.01, i * 0.01))
            start = None
        i = j
    if start is not None:
        spans.append((start * 0.01, frames * 0.01))
    return spans


def wrap(text: str, width: int = 62) -> str:
    if len(text) <= width:
        return text
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width and cur:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    lines.append(cur)
    if len(lines) > 2:                      # keep it to two lines in the band
        mid = (len(lines) + 1) // 2
        lines = [" ".join(lines[:mid]), " ".join(lines[mid:])]
    return "\\N".join(lines)


def ass_time(t: float) -> str:
    h, rem = divmod(max(0.0, t), 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def build_captions(scenes: list[dict], starts: dict[str, float], out: Path) -> int:
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Segoe UI,46,&H00F5ECE8,&H00140A05,&H96000000,0,0,1,3,0,2,180,180,68,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines, n = [], 0
    for sc in scenes:
        sid = sc["id"]
        sents = sentences_of(sc["narration"])
        spans = speech_spans(VOICE / f"{sid}.wav")
        if len(spans) != len(sents):
            print(f"    ! {sid}: found {len(spans)} spoken spans for {len(sents)} "
                  f"sentences — falling back to proportional timing")
            dur = json.loads((VOICE / "durations.json").read_text())[sid]
            total = sum(len(s) for s in sents)
            spans, acc = [], 0.25
            for s in sents:
                share = (len(s) / total) * (dur - 0.8)
                spans.append((acc, acc + share))
                acc += share
        for (a, b), text in zip(spans, sents):
            lines.append(
                f"Dialogue: 0,{ass_time(starts[sid] + a)},{ass_time(starts[sid] + b)},"
                f"Cap,,0,0,0,,{wrap(text)}")
            n += 1
    out.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return n


def build_silent(ids, D, clips):
    vin, vf, prev = [], [], "[0:v]"
    for sid in ids:
        vin += ["-i", str(SCENES / f"{sid}.mp4")]
    off = 0.0
    for k in range(1, len(ids)):
        off += D[ids[k - 1]] - XFADE
        label = f"[x{k}]"
        vf.append(f"{prev}[{k}:v]xfade=transition=fade:duration={XFADE}:offset={off:.3f}{label}")
        prev = label
    ff([*vin, "-filter_complex", ";".join(vf), "-map", prev,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-r", str(clips["fps"]), str(DIST / "silent.mp4")], "cross-dissolve")


def main() -> int:
    scenes = load_scenes()
    clips = json.loads((SCENES / "clips.json").read_text())
    D = {c["id"]: c["D"] for c in clips["clips"]}
    ids = [s["id"] for s in scenes]

    missing = [i for i in ids if not (SCENES / f"{i}.mp4").exists()]
    if missing:
        raise SystemExit(f"missing rendered clips: {missing} — run render_frames.mjs first")

    # every clip overlaps its neighbour by XFADE, so each start pulls back by one more
    starts, acc = {}, 0.0
    for k, sid in enumerate(ids):
        starts[sid] = acc
        acc += D[sid] - XFADE
    total = sum(D[i] for i in ids) - XFADE * (len(ids) - 1)
    print(f"final duration {total:.2f}s  ({int(total // 60)}:{total % 60:04.1f})\n")

    # 1 — narration on the timeline
    inputs, filters, mixes = [], [], []
    for k, sid in enumerate(ids):
        inputs += ["-i", str(VOICE / f"{sid}.wav")]
        filters.append(f"[{k}:a]aresample={SR},adelay={int(starts[sid]*1000)}|"
                       f"{int(starts[sid]*1000)}[v{k}]")
        mixes.append(f"[v{k}]")
    # Kokoro renders mono; the bed is stereo and the delivery is stereo, so the voice is
    # placed centre here rather than letting a later amix silently collapse the mix to 1ch.
    graph = ";".join(filters) + ";" + "".join(mixes) + \
        f"amix=inputs={len(ids)}:normalize=0,apad,atrim=0:{total:.3f}," \
        f"aformat=channel_layouts=stereo[out]"
    ff([*inputs, "-filter_complex", graph, "-map", "[out]",
        "-c:a", "pcm_s16le", "-ar", str(SR), str(DIST / "voice.wav")], "voice track")

    # 2 — the approved bed, looped to length
    ff(["-stream_loop", "-1", "-i", str(BED),
        "-filter_complex", f"[0:a]aresample={SR},aformat=channel_layouts=stereo,"
                           f"atrim=0:{total:.3f},"
                           f"afade=t=in:st=0:d=1.5,afade=t=out:st={total-2.5:.3f}:d=2.5[b]",
        "-map", "[b]", "-c:a", "pcm_s16le", str(DIST / "bed.wav")], "music bed")

    # 3 — duck the bed under the voice, then normalise for delivery.
    #     Kokoro's raw level lands around -27 LUFS integrated; without this the video is
    #     near-inaudible at a normal volume. loudnorm also owns the true peak, so no
    #     separate limiter is needed (one would only fight it).
    ff(["-i", str(DIST / "bed.wav"), "-i", str(DIST / "voice.wav"),
        "-filter_complex",
        f"[0:a]volume={BED_GAIN}dB[bg];"
        f"[1:a]asplit=2[vc][sc];"
        f"[bg][sc]sidechaincompress=threshold=0.015:ratio={DUCK_DB:.0f}:attack=15:release=420[duck];"
        f"[vc][duck]amix=inputs=2:normalize=0,"
        f"loudnorm=I=-15:TP=-1.5:LRA=11,"
        f"aresample={SR},aformat=channel_layouts=stereo[m]",
        "-map", "[m]", "-c:a", "pcm_s16le", str(DIST / "mix.wav")], "duck + mix + loudness")

    # 4 — cross-dissolve the clips (skippable: the video is unchanged by an audio re-cut)
    if "--reuse-video" in sys.argv and (DIST / "silent.mp4").exists():
        print("  cross-dissolve ... reusing silent.mp4")
    else:
        build_silent(ids, D, clips)

    # 5 — captions, timed off the rendered audio
    print("  captions ...")
    n = build_captions(scenes, starts, DIST / "zyroflo.ass")
    print(f"    {n} cues -> zyroflo.ass")

    # 6 — burn + mux
    ass = str(DIST / "zyroflo.ass").replace("\\", "/").replace(":", "\\:")
    ff(["-i", str(DIST / "silent.mp4"), "-i", str(DIST / "mix.wav"),
        "-filter_complex", f"[0:v]subtitles='{ass}'[v]",
        "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        str(DIST / "zyroflo-explainer-1080p.mp4")], "burn captions + mux")

    out = DIST / "zyroflo-explainer-1080p.mp4"
    print(f"\n{out}  ({out.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
