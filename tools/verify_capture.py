"""Measure a capture the way the gate is supposed to behave.

Feed it a WAV recorded with tools/apo-capture (WASAPI shared mode) - *not* with
ffmpeg's DirectShow input, which bypasses APOs on Windows and will happily record
raw, unprocessed microphone audio.

    python tools/verify_capture.py take.wav
    python tools/verify_capture.py take.wav --open -45 --closed 0.5

What "good" looks like:
    - silence / room tone: >90 % of samples exactly 0, rms well below -80 dBFS
    - speech: 0 % zeros, rms around -20 dBFS, no clipped samples
    - after speech: gate closes ~200 ms later (the VAD grace period)
"""

import argparse
import wave

import numpy as np


def read_wav(path):
    with wave.open(path, "rb") as w:
        channels = w.getnchannels()
        width = w.getsampwidth()
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())

    if width == 4:
        # EqualizerAPO/WASAPI captures are 32-bit float in practice; fall back to
        # int32 if the values are clearly not normalised floats.
        floats = np.frombuffer(raw, dtype="<f4")
        if np.isfinite(floats).all() and np.abs(floats).max() <= 1.5:
            data = floats.astype(np.float64)
        else:
            data = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2**31
    elif width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 2**15
    elif width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = (b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16))
        v = np.where(v & 0x800000, v - 0x1000000, v)
        data = v.astype(np.float64) / 2**23
    else:
        raise SystemExit("unsupported sample width %d" % width)

    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return rate, data


def db(v):
    return 20 * np.log10(max(float(v), 1e-12))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("wav")
    ap.add_argument("--open", type=float, default=-45.0, help="dBFS above which a frame counts as open")
    ap.add_argument("--window", type=float, default=0.05, help="analysis window in seconds")
    args = ap.parse_args()

    fs, x = read_wav(args.wav)
    n = len(x)
    dur = n / fs
    zeros = float((x == 0).mean())

    print("file      : %s" % args.wav)
    print("format    : %.0f Hz mono, %.2f s" % (fs, dur))
    print("zeros     : %.1f %% of samples are exactly 0" % (100 * zeros))
    print("rms/peak  : %.1f / %.1f dBFS" % (db(np.sqrt((x ** 2).mean())), db(np.abs(x).max())))

    clipped = int((np.abs(x) >= 0.999).sum())
    print("clipped   : %d samples%s" % (clipped, "  <-- lower Preamp" if clipped else ""))

    w = max(1, int(args.window * fs))
    frames = np.array([np.sqrt((x[i:i + w] ** 2).mean()) for i in range(0, n - w + 1, w)])
    loud = frames > 10 ** (args.open / 20.0)

    print("\n%.0f ms envelope ('#' open, '.' gated):" % (args.window * 1000))
    line = "".join("#" if v else "." for v in loud)
    for i in range(0, len(line), 60):
        print("  %5.1fs %s" % (i * args.window, line[i:i + 60]))

    runs, cur, start = [], loud[0] if len(loud) else False, 0
    for i, v in enumerate(loud[1:], 1):
        if v != cur:
            runs.append((cur, start, i))
            cur, start = v, i
    runs.append((cur, start, len(loud)))

    print("\nsegments (>=200 ms):")
    for is_open, a, b in runs:
        if (b - a) * args.window < 0.2:
            continue
        seg = x[a * w:b * w]
        if not len(seg):
            continue
        print("  %5.2f-%5.2f s  %-6s rms %6.1f dBFS  peak %6.1f dBFS  zeros %5.1f %%" %
              (a * args.window, b * args.window, "open" if is_open else "gated",
               db(np.sqrt((seg ** 2).mean())), db(np.abs(seg).max()), 100 * (seg == 0).mean()))

    longest, cur = 0, 0
    for v in (x == 0):
        cur = cur + 1 if v else 0
        longest = max(longest, cur)
    print("\nlongest digital silence: %.2f s" % (longest / fs))

    verdict = []
    if zeros > 0.9:
        verdict.append("room tone is muted")
    if clipped == 0:
        verdict.append("no clipping")
    print("verdict   : " + (", ".join(verdict) if verdict else "check the numbers above"))


if __name__ == "__main__":
    main()
