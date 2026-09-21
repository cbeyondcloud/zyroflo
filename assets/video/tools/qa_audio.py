"""
Objective QA for rendered audio - for when nobody in the loop can listen.

    python qa_audio.py <file.wav> <out.png> [--plan]

--plan  pitch-checks against compose_bed.PREVIEW: every strong spectral peak in each
        chord section must be an overtone (1st-8th) of a note that section was
        written to play (a saw pad carries a strong 7th/8th
        harmonic when bright). Anything else is a wrong note, aliasing or a glitch.
"""
import sys
from pathlib import Path

import numpy as np
from scipy import signal
from scipy.io import wavfile
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

path, png = sys.argv[1], sys.argv[2]
sr, x = wavfile.read(path)
x = x.astype(np.float64) / 32768
L, R = x[:, 0], x[:, 1]
M = (L + R) / 2
db = lambda v: 20 * np.log10(v + 1e-12)

print(f"file          {Path(path).name}  {len(L) / sr:.2f} s  {sr} Hz")
tp = max(np.abs(signal.resample_poly(L, 4, 1)).max(), np.abs(signal.resample_poly(R, 4, 1)).max())
print(f"true peak     {db(tp):.2f} dBTP (4x oversampled)")
print(f"edges         first 10 ms {db(np.abs(x[:480]).max()):.1f} dBFS   last 10 ms {db(np.abs(x[-480:]).max()):.1f} dBFS")

corr = np.corrcoef(L, R)[0, 1]
loss = db(np.sqrt(np.mean(M ** 2))) - db(np.sqrt((np.mean(L ** 2) + np.mean(R ** 2)) / 2))
print(f"stereo        L/R correlation {corr:+.3f}   mono fold-down {loss:+.2f} dB")

hf = signal.sosfilt(signal.butter(4, 12000, "high", fs=sr, output="sos"), M)
d = np.abs(np.diff(M))
print(f"glitch scan   >12 kHz peak {db(np.abs(hf).max()):.1f} dBFS   "
      f"max step / p99.9 step = {d.max() / np.percentile(d, 99.9):.2f}")

f, P = signal.welch(M, sr, nperseg=8192)
area = lambda a, b: np.trapezoid(P[(f >= a) & (f < b)], f[(f >= a) & (f < b)])
tot = area(20, 20000)
bands = [("sub   <80", 20, 80), ("low   80-250", 80, 250), ("lomid 250-1k", 250, 1000),
         ("VOICE 1k-4k", 1000, 4000), ("air   4k+", 4000, 20000)]
print("energy share  " + "   ".join(f"{n.split()[0]} {100 * area(a, b) / tot:4.1f}%" for n, a, b in bands))

sections = []
if "--plan" in sys.argv:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "music"))
    import compose_bed as cb
    print("pitch check   (strong peaks 40 Hz-3 kHz, must be overtone 1-8 of a written note, +-20 cents)")
    bad_total = 0
    for chord, b0, nb in cb.PREVIEW:
        t0, t1 = b0 * cb.BAR, (b0 + nb) * cb.BAR
        sections.append((t0, chord))
        s = int((t1 - 1.05) * sr)        # last second of the section: previous chord gone,
                                           # next one not yet started
        seg = M[s:s + sr] * np.hanning(sr)
        mag = np.abs(np.fft.rfft(seg))
        fr = np.fft.rfftfreq(sr, 1 / sr)
        keep = (fr > 40) & (fr < 3000)
        pk, _ = signal.find_peaks(mag * keep, height=mag[keep].max() * 0.08, distance=3)
        notes = [cb.hz(m) for m in cb.PAD[chord] + cb.ARP[chord] + [cb.BASS[chord]]]
        unexplained = []
        lm = np.log(mag + 1e-20)
        for p in pk:
            # parabolic interpolation: a 1 Hz bin is ~16 cents at 110 Hz, so the raw
            # bin centre cannot be held to a 15-cent tolerance at the bottom end
            a_, b_, c_ = lm[p - 1], lm[p], lm[p + 1]
            fp = fr[p] + 0.5 * (a_ - c_) / (a_ - 2 * b_ + c_)
            # 20 cents: the pad voices sit up to ~9 cents off nominal by design (+-7 detune,
            # +-2 drift) and merged clusters bias the interpolation. A wrong note is 100+.
            ok = any(abs(1200 * np.log2(fp / (k * n))) < 20 for n in notes for k in range(1, 9))
            if not ok:
                unexplained.append(round(fp))
        bad_total += len(unexplained)
        print(f"   {chord:6s} {t0:5.1f}-{t1:4.1f}s  {len(pk):2d} strong peaks  "
              f"{'all explained' if not unexplained else 'UNEXPLAINED ' + str(unexplained)}")
    print(f"   -> {bad_total} unexplained peak(s)")

fig, (a1, a2) = plt.subplots(2, 1, figsize=(14, 7), sharex=True,
                             gridspec_kw={"height_ratios": [1, 2.4]})
hop = int(0.05 * sr)
env = [db(np.sqrt(np.mean(M[i:i + hop] ** 2))) for i in range(0, len(M) - hop, hop)]
a1.plot(np.arange(len(env)) * 0.05, env, lw=1)
a1.set_ylabel("RMS dBFS")
a1.set_ylim(-70, 0)
a1.grid(alpha=.3)
ff, tt, S = signal.spectrogram(M, sr, nperseg=4096, noverlap=3584)
m = ff <= 6000
a2.pcolormesh(tt, ff[m], 10 * np.log10(S[m] + 1e-14), shading="auto", cmap="magma", vmin=-130, vmax=-50)
a2.set_ylabel("Hz")
a2.set_xlabel("seconds")
a2.axhspan(1000, 4000, color="cyan", alpha=.06)
for t0, name in sections:
    for ax in (a1, a2):
        ax.axvline(t0, color="w" if ax is a2 else "k", lw=.6, alpha=.6)
    a1.text(t0 + .1, -8, name, fontsize=9)
fig.tight_layout()
fig.savefig(png, dpi=90)
print(f"plot          {png}")
