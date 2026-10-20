"""Inspect a VST2 plugin's AEffect table and run it through a fake host.

This is the tool that settles "is the plugin even being called?" questions without
touching a live EqualizerAPO install. It loads the DLL directly, prints the AEffect
fields at their *official* offsets, and then processes 3 s of noise with a host
callback that answers ``audioMasterGetTime`` with ``1`` - exactly what EqualizerAPO
1.4.2 does and what used to crash JUCE.

Usage:
    python tools/vst_abi_probe.py path\\to\\rnnoise_mono.dll
    python tools/vst_abi_probe.py path\\to\\rnnoise_mono.dll --dump-only

Expected result for a healthy build (x64/ARM64, MSVC):
    magic            @0   : 0x56737450
    dispatcher       @8   : <non-null>
    numInputs        @48  : 1 (mono build) / 2 (stereo build)
    flags            @56  : 0x231   (HasEditor | CanReplacing | ProgramChunks | NoSoundInStop)
    object           @96  : <non-null>
    processReplacing @120 : <non-null>       <-- if this is NULL, the host will
                                                 crash or silently pass audio through
    output: >90 % exact zeros for room tone / white noise at vad_threshold 0.5
"""

import argparse
import ctypes as C

import numpy as np

# Official VST2 AEffect offsets on 64-bit, with the leading 'magic' field that
# EqualizerAPO (and JUCE) expect.  Do not "fix" fst.h: this is the layout hosts read.
OFF_MAGIC = 0
OFF_DISPATCHER = 8
OFF_NUM_PROGRAMS = 40
OFF_NUM_PARAMS = 44
OFF_NUM_INPUTS = 48
OFF_NUM_OUTPUTS = 52
OFF_FLAGS = 56
OFF_OBJECT = 96
OFF_UNIQUE_ID = 112
OFF_VERSION = 116
OFF_PROCESS_REPLACING = 120
OFF_PROCESS_DOUBLE = 128

FLAG_HAS_EDITOR = 1 << 0
FLAG_CAN_REPLACING = 1 << 4
FLAG_PROGRAM_CHUNKS = 1 << 5
FLAG_NO_SOUND_IN_STOP = 1 << 9

CALLBACK = C.CFUNCTYPE(C.c_int64, C.c_void_p, C.c_int32, C.c_int32, C.c_int64, C.c_void_p, C.c_float)
DISPATCHER = C.CFUNCTYPE(C.c_int64, C.c_void_p, C.c_int32, C.c_int32, C.c_int64, C.c_void_p, C.c_float)
PROCESS = C.CFUNCTYPE(None, C.c_void_p, C.POINTER(C.POINTER(C.c_float)),
                      C.POINTER(C.POINTER(C.c_float)), C.c_int32)

SAMPLE_RATE = 48000
BLOCK = 480


class Host:
    """Minimal VST2 host. Every opcode not listed returns 0."""

    def __init__(self):
        self.sample_rate = SAMPLE_RATE
        self.block_size = BLOCK
        self.calls = 0
        self._cb = CALLBACK(self._audio_master)

    def _audio_master(self, _effect, opcode, index, value, ptr, opt):
        self.calls += 1
        if opcode == 1:                      # audioMasterVersion
            return 2400
        if opcode == 10:                     # audioMasterGetSampleRate
            return self.sample_rate
        if opcode == 11:                     # audioMasterGetBlockSize
            return self.block_size
        if opcode == 14:                     # audioMasterGetTime
            # EqualizerAPO 1.4.2 returns 1 here, not a VstTimeInfo*. JUCE must
            # ignore it, otherwise it dereferences 0x1 and audiodg drops the stream.
            return 1
        return 0


def dump(effect, host):
    def i32(off):
        return C.cast(effect + off, C.POINTER(C.c_int32))[0]

    def f32(off):
        return C.cast(effect + off, C.POINTER(C.c_float))[0]

    def ptr(off):
        return C.cast(effect + off, C.POINTER(C.c_void_p))[0]

    flags = i32(OFF_FLAGS)
    print("AEffect:")
    print("  magic            @%-3d : 0x%08X  (expect 0x56737450 'VstP')" % (OFF_MAGIC, i32(OFF_MAGIC)))
    print("  dispatcher       @%-3d : %s" % (OFF_DISPATCHER, hex(ptr(OFF_DISPATCHER)) if ptr(OFF_DISPATCHER) else "NULL"))
    print("  numPrograms      @%-3d : %d" % (OFF_NUM_PROGRAMS, i32(OFF_NUM_PROGRAMS)))
    print("  numParams        @%-3d : %d" % (OFF_NUM_PARAMS, i32(OFF_NUM_PARAMS)))
    print("  numInputs        @%-3d : %d" % (OFF_NUM_INPUTS, i32(OFF_NUM_INPUTS)))
    print("  numOutputs       @%-3d : %d" % (OFF_NUM_OUTPUTS, i32(OFF_NUM_OUTPUTS)))
    print("  flags            @%-3d : 0x%X%s" % (OFF_FLAGS, flags,
          "  (CanReplacing set)" if flags & FLAG_CAN_REPLACING else "  *** CanReplacing NOT set ***"))
    print("  object           @%-3d : %s" % (OFF_OBJECT, hex(ptr(OFF_OBJECT)) if ptr(OFF_OBJECT) else "NULL"))
    print("  uniqueID/version @%-3d : %d / %d" % (OFF_UNIQUE_ID, i32(OFF_UNIQUE_ID), i32(OFF_VERSION)))
    print("  processReplacing @%-3d : %s" % (OFF_PROCESS_REPLACING,
          hex(ptr(OFF_PROCESS_REPLACING)) if ptr(OFF_PROCESS_REPLACING) else "NULL  *** HOST WOULD CALL NULL ***"))
    print("  processDouble    @%-3d : %s" % (OFF_PROCESS_DOUBLE,
          hex(ptr(OFF_PROCESS_DOUBLE)) if ptr(OFF_PROCESS_DOUBLE) else "NULL"))


def process(effect, host):
    def i32(off):
        return C.cast(effect + off, C.POINTER(C.c_int32))[0]

    def ptr(off):
        return C.cast(effect + off, C.POINTER(C.c_void_p))[0]

    dispatcher = DISPATCHER(ptr(OFF_DISPATCHER))
    dispatcher(effect, 0, 0, 0, None, 0.0)                      # effOpen
    dispatcher(effect, 10, 0, 0, None, float(host.sample_rate))  # effSetSampleRate
    dispatcher(effect, 11, 0, host.block_size, None, 0.0)        # effSetBlockSize
    dispatcher(effect, 25, 0, 1, None, 0.0)                      # effMainsChanged(1)

    proc = PROCESS(ptr(OFF_PROCESS_REPLACING))

    n = host.sample_rate * 3
    rng = np.random.default_rng(7)
    src = (rng.standard_normal(n) * 0.02).astype(np.float32)     # ~-50 dBFS "room tone"
    out = np.zeros(n, dtype=np.float32)

    for pos in range(0, n - host.block_size + 1, host.block_size):
        chunk = np.ascontiguousarray(src[pos:pos + host.block_size])
        dst = out[pos:pos + host.block_size]
        in_ptrs = (C.POINTER(C.c_float) * 1)(chunk.ctypes.data_as(C.POINTER(C.c_float)))
        out_ptrs = (C.POINTER(C.c_float) * 1)(dst.ctypes.data_as(C.POINTER(C.c_float)))
        proc(effect, in_ptrs, out_ptrs, host.block_size)

    dispatcher(effect, 26, 0, 0, None, 0.0)                      # effMainsChanged(0)
    dispatcher(effect, 1, 0, 0, None, 0.0)                       # effClose

    def db(v):
        return 20 * np.log10(max(float(v), 1e-12))

    zeros = float((out == 0).mean())
    print()
    print("input : peak %.1f dBFS  rms %.1f dBFS  zeros %.1f %%" %
          (db(np.abs(src).max()), db(np.sqrt((src.astype(np.float64) ** 2).mean())), 100 * (src == 0).mean()))
    print("output: peak %.1f dBFS  rms %.1f dBFS  zeros %.1f %%" %
          (db(np.abs(out).max()), db(np.sqrt((out.astype(np.float64) ** 2).mean())), 100 * zeros))
    print("host callbacks: %d" % host.calls)
    print("VERDICT:", "PROCESSED (gate muted the noise)" if zeros > 0.5 else "NOT PROCESSED")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dll")
    ap.add_argument("--dump-only", action="store_true")
    args = ap.parse_args()

    host = Host()
    lib = C.CDLL(args.dll)
    lib.VSTPluginMain.restype = C.c_void_p
    lib.VSTPluginMain.argtypes = [C.c_void_p]
    effect = lib.VSTPluginMain(C.cast(host._cb, C.c_void_p))
    if not effect:
        raise SystemExit("VSTPluginMain returned NULL")

    dump(effect, host)
    if not args.dump_only:
        process(effect, host)


if __name__ == "__main__":
    main()
