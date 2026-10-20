"""Build (or decode) the ``ChunkData`` value used by EqualizerAPO's VSTPlugin line.

The chunk is what JUCE's ``copyXmlToBinary`` produces:

    'VC2!' | uint32 little-endian XML length | XML bytes | 0x00

EqualizerAPO just hands those bytes to ``setStateInformation``, so any base64 of
that blob works. Re-encoding the defaults reproduces the stock value exactly:

    python tools/make_vst_chunk.py --threshold 0.5 --grace 20 --retro 2

Paste the printed string into config.txt:

    VSTPlugin: Library rnnoise_mono.dll ChunkData "<base64>"

Decode an existing value to see what a config actually does:

    python tools/make_vst_chunk.py --decode "<base64>"

Parameter semantics (see src/common/src/RnNoiseCommonPlugin.cpp):

    threshold  VAD probability below which a 10 ms block is muted (0..1).
               Higher = more aggressive gating.
    grace      Blocks (10 ms each) the gate stays open after the last speech
               block. 20 => 200 ms hold.
    retro      Blocks of look-back that are re-unmuted when speech starts, so
               word onsets are not clipped. JUCE clamps it to 99.
"""

import argparse
import base64
import struct


def encode(threshold: float, grace: int, retro: int) -> str:
    xml = ('<?xml version="1.0" encoding="UTF-8"?> <RNNoise>'
           '<PARAM id="vad_grace_period" value="%s"/>'
           '<PARAM id="vad_retroactive_grace_period" value="%s"/>'
           '<PARAM id="vad_threshold" value="%s"/>'
           '</RNNoise>' % (float(grace), float(retro), float(threshold))).encode("utf-8")
    blob = b"VC2!" + struct.pack("<I", len(xml)) + xml + b"\x00"
    return base64.b64encode(blob).decode("ascii")


def decode(value: str) -> None:
    blob = base64.b64decode(value)
    if blob[:4] != b"VC2!":
        raise SystemExit("not a JUCE XML chunk (magic %r)" % blob[:4])
    size = struct.unpack("<I", blob[4:8])[0]
    print("declared XML length:", size)
    print(blob[8:8 + size].decode("utf-8", "replace"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--threshold", type=float, default=0.5)
    ap.add_argument("--grace", type=int, default=20, help="10 ms blocks the gate stays open (20 = 200 ms)")
    ap.add_argument("--retro", type=int, default=2, help="10 ms blocks re-unmuted before speech onsets")
    ap.add_argument("--decode", metavar="BASE64")
    args = ap.parse_args()

    if args.decode:
        decode(args.decode)
        return

    print(encode(args.threshold, args.grace, args.retro))


if __name__ == "__main__":
    main()
