# RNNoise VST2 inside EqualizerAPO — field notes

Everything needed to rebuild, deploy, verify and debug the mono VST2 denoiser when
EqualizerAPO is the host. Written after a long debugging session; the two traps in
sections 5 and 6 cost most of the time.

Tested on: Windows 11 ARM64, Sennheiser Profile USB mic, EqualizerAPO 1.4.2,
Visual Studio 18 2026 (MSVC 14.51), vendored JUCE 7.0.1, RNNoise v0.2.

---

## 0. Working state

| Item | Value |
|---|---|
| Live plugin | `C:\Program Files\EqualizerAPO\VSTPlugins\rnnoise_mono.dll` |
| Build output | `build\bin\vst\rnnoise_mono.dll` |
| Size | 10 312 192 bytes (Release, ARM64). Every rebuild has a different SHA-256 (PE timestamp), so compare build vs live directly — see §3.2. |
| Host block | 480 frames requested by the capture stream (APO buffer 528) |
| Gate settings | threshold 0.5, grace 20 (200 ms), retroactive 2 (20 ms) |

Measured behaviour:

| Input | Result |
|---|---|
| Room tone, nobody speaking | 95 % of samples exactly 0, rms −91 dBFS |
| Speech | 0 % zeros, rms −15…−21 dBFS, peaks up to −0.0 dBFS |
| Pauses between sentences | 81–89 % zeros, fully silent after ~250 ms |
| Longest unbroken digital silence | 1.38 s |

---

## 1. The chain

`C:\Program Files\EqualizerAPO\config\config.txt`:

```
Device: Sennheiser Profile
Preamp: 12 dB
Filter: ON HP Fc 85 Hz
VSTPlugin: Library rnnoise_mono.dll ChunkData "<base64>"
Filter: ON HS Fc 3500 Hz Gain 3.5 dB
Filter: ON PK Fc 5000 Hz Gain 1.5 dB Q 1.5
Filter: ON PK Fc 10000 Hz Gain 2 dB Q 0.7
```

- `Device:` is a substring match on the endpoint's friendly name. Remove the line to
  apply the chain to every device.
- Order matters: the Preamp runs **before** the plugin, so the plugin sees +12 dB.
  Speech peaks landed at −0.0 dBFS with 12 dB; 10 dB is the safer choice for headroom.
- The VST line's library name is relative to `C:\Program Files\EqualizerAPO\VSTPlugins`.
  If the path contains spaces it **must** be quoted — EqualizerAPO splits the line on
  spaces without respecting quotes.
- `ChunkData` is the plugin state (see §3.3).

Keep a copy of `config.txt` before editing; EqualizerAPO reloads as soon as the file
is saved (no service restart needed).

---

## 2. Build

```powershell
cmake -S . -B build                      # VS generator; on this machine it targets ARM64
cmake --build build --config Release --target rnnoise_juce_plugin_mono_VST --parallel
```

Result: `build\bin\vst\rnnoise_mono.dll` (CMake copies it there via `VST_COPY_DIR`).
The full multi-format build (`--parallel` with no target) additionally produces
stereo VST2, VST3, LV2 and LADSPA. See `ARM64-WINDOWS-BUILD-NOTES.md` for the ARM64
toolchain details.

Sanity checks:

```powershell
dumpbin /headers build\bin\vst\rnnoise_mono.dll | findstr machine   # expect AA64 (ARM64)
python tools\vst_abi_probe.py build\bin\vst\rnnoise_mono.dll        # see §5
```

---

## 3. Deploy

### 3.1 Copy the DLL

```powershell
Copy-Item build\bin\vst\rnnoise_mono.dll `
          "C:\Program Files\EqualizerAPO\VSTPlugins\rnnoise_mono.dll" -Force
```

Recording software keeps the DLL mapped while a stream is open. If the copy fails
with "file in use", stop the app using the mic (or restart the audio service:
`Restart-Service audiosrv -Force`).

### 3.2 Confirm what is actually live

```powershell
$b = [IO.File]::ReadAllBytes("C:\Program Files\EqualizerAPO\VSTPlugins\rnnoise_mono.dll")
"{0} bytes  {1}" -f $b.Length, [BitConverter]::ToString(
    [Security.Cryptography.SHA256]::Create().ComputeHash($b)).Replace('-','').ToLower()
```

Compare with the build output. It is very easy to spend an hour debugging a DLL that
was never replaced.

### 3.3 ChunkData

`tools/make_vst_chunk.py` builds the exact blob JUCE's `copyXmlToBinary` produces
(`"VC2!"` + LE length + XML + NUL):

```powershell
python tools\make_vst_chunk.py --threshold 0.5 --grace 20 --retro 2
```

Paste the output into the `ChunkData "…"` field. Decode an existing config with
`--decode "<base64>"`. Re-encoding the stock defaults reproduces the stock string
byte-for-byte, which is a good self-test.

Parameter semantics:

| Parameter | Meaning |
|---|---|
| `vad_threshold` | Blocks with VAD probability below this are muted. **Higher = more aggressive.** |
| `vad_grace_period` | 10 ms blocks the gate stays open after the last speech block (20 = 200 ms). |
| `vad_retroactive_grace_period` | 10 ms blocks re-unmuted before a detected onset, so word starts are not clipped. |

---

## 4. Verification — and the capture trap

### 4.1 Do not use `ffmpeg -f dshow`

On this machine DirectShow capture talks to the KS filter and **bypasses the APO**.
It records raw microphone audio while EqualizerAPO is happily processing a parallel
stream, so a perfectly working chain looks completely dead. This produced hours of
false "the plugin is not running" conclusions.

Use a WASAPI **shared-mode** capture instead — that is the path the APO lives in.

Beyond the missing DSP, dshow negotiates a different stream, and on the
Sennheiser Profile a strange one. Measured 2026-10-20:

| | dshow (`ffmpeg`) | WASAPI shared (APO) |
|---|---|---|
| rate / layout | 44 100 Hz, stereo by default | 48 000 Hz, mono |
| right channel (ch1) | direct microphone | — |
| left channel (ch0) | same mic, **delayed 30 ms** | — |

ch1 is the direct signal. ch0 correlates 0.97 with ch1 shifted by exactly
1440 samples (30.00 ms): it is a delayed monitor copy, about 22 dB down while
the input is quiet and rising to the level of ch1 during loud speech.

That is the echo. Summing the pair — what any mono downmix does — adds a
signal to its own 30 ms copy, i.e. a comb filter with notches every 33 Hz and
a **−7 dB slap at 30 ms** (measured on live speech). It is not room reverb and
it is not the APO.

`-ac 1` is not a safe workaround, because the mono path does not reliably carry
the mic: in one 20 s run `-ac 1` recorded only room tone (peak −28 dBFS) while
the APO path captured the same speech at −21 dBFS rms. `-ac 1` is also not a
downmix of the two channels, so the 30 ms copy is not the explanation for what
lands in that file.

For a raw reference, capture stereo and keep the direct channel:

```powershell
ffmpeg -f dshow -i audio="Sennheiser (Sennheiser Profile)" -af "pan=mono|c0=c1" -ar 48000 take.wav
```

For anything that must match what EqualizerAPO does, use the WASAPI recorder in
§4.2 — it sees the same stream the APO does.

### 4.2 Recorder

`tools/apo-capture` (.NET 8 + NAudio 2.2.1):

```powershell
cd tools\apo-capture
dotnet run -c Release -- list
dotnet run -c Release -- "Sennheiser" 12 take.wav
```

It prints the device mix format (32-bit float, 48 kHz, mono here) and writes a WAV.

### 4.3 Analysis

```powershell
python tools\verify_capture.py take.wav
```

Expected for a working chain:

```
file      : take.wav
format    : 48000 Hz mono, 12.00 s
zeros     : 95.0 % of samples are exactly 0
rms/peak  : -91.3 / -58.6 dBFS
clipped   : 0 samples

segments (>=200 ms):
   2.40- 3.30 s  gated  rms  -60.1 dBFS  peak  -39.0 dBFS  zeros  81.1 %
   3.30- 4.50 s  open   rms  -19.5 dBFS  peak   -6.8 dBFS  zeros   0.0 %
```

Rules of thumb:

- gated segment: `zeros` > 80 %, rms < −55 dBFS, peaks from the 200 ms release tail
- open segment: `zeros` = 0 %, rms −15…−22 dBFS
- `clipped > 0` → lower `Preamp`

### 4.4 Speech protocol

Record ~12 s and speak in bursts with real pauses: sentence → 1–2 s silence →
sentence. Then check that the `open`/`gated` segmentation matches what you did and
that the gated stretches are exact zeros, not just quiet.

---

## 5. Trap #1 — the VST2 `AEffect` layout

`external/FST/fst/fst.h` defines the struct JUCE's VST2 wrapper compiles against.
Hosts read it at **fixed byte offsets**, so the layout is not ours to rearrange.

64-bit layout that EqualizerAPO (and every other VST2 host) uses:

| Offset | Field |
|---:|---|
| 0 | `magic` = `0x56737450` (`'VstP'`) |
| 8 | `dispatcher` |
| 16 | `process` |
| 24 | `setParameter` |
| 32 | `getParameter` |
| 40 | `numPrograms` |
| 44 | `numParams` |
| 48 | `numInputs` |
| 52 | `numOutputs` |
| 56 | `flags` |
| 64 | `ptr1` |
| 72 | `ptr2` |
| 80 | `initialDelay` |
| 84 | `realQualities` |
| 88 | `offQualities` |
| 92 | `ioRatio` |
| 96 | `object` |
| 104 | `user` |
| 112 | `uniqueID` |
| 116 | `version` |
| **120** | **`processReplacing`** |
| **128** | **`processDoubleReplacing`** |
| 136 | `future[56]` (sizeof = 192) |

The FST struct spells some of these fields differently (`t_fstPtrInt flags`,
`resvd1`, `resvd2`, `_pad2`, `float1`), but on 64-bit it lands on exactly the same
offsets. **Do not "clean it up".** Removing `magic`, or shrinking the pointer-sized
fields to 32-bit ones, shifts `object`/`processReplacing`:

- `processReplacing` @104 instead of @120 → EqualizerAPO calls whatever is at 120,
  which is zero-initialised memory → null call → SEH → silent pass-through.
- Symptom: `prepareToPlay` runs, `processBlock` is **never** called, audio is
  unchanged, and the EqualizerAPO log may say "crashed during audio processing".

Check any build with:

```powershell
python tools\vst_abi_probe.py build\bin\vst\rnnoise_mono.dll
```

```
magic            @0   : 0x56737450  (expect 0x56737450 'VstP')
dispatcher       @8   : 0x7ffd...
numInputs        @48  : 2
numOutputs       @52  : 2
flags            @56  : 0x231  (CanReplacing set)
object           @96  : 0x...
processReplacing @120 : 0x...          <-- must not be NULL
processDouble    @128 : 0x...
```

`flags = 0x231` = HasEditor (1) | CanReplacing (0x10) | ProgramChunks (0x20) |
NoSoundInStop (0x200). The `CanReplacing` bit is what makes EqualizerAPO use
`processReplacing` instead of the legacy `process`.

The same script then runs 3 s of noise through the plugin with a host callback that
mimics EqualizerAPO (`audioMasterGetTime` → 1). Healthy output: >90 % zeros.

---

## 6. Trap #2 — `audioMasterGetTime` returns `1`

JUCE's `updateCallbackContextInfo()` queries transport info:

```cpp
auto result = hostCallback (&vstEffect, Vst2::audioMasterGetTime, 0, flags, nullptr, 0);
ti = reinterpret_cast<Vst2::VstTimeInfo*> (result);   // EqualizerAPO returns 0x1
...
if (ti->sampleRate <= 0) ...
```

EqualizerAPO answers with `1` instead of a pointer, and the dereference faults
inside `audiodg` (`access violation reading 0x9`) **before** `processBlock()` ever
runs. The host catches the SEH, logs "crashed during audio processing" and passes
audio through — indistinguishable from a plugin that does nothing.

Fix (kept in `external/JUCE/modules/juce_audio_plugin_client/VST/juce_VST_Wrapper.cpp`,
inside `updateCallbackContextInfo`):

```cpp
juce::ignoreUnused (result);
ti = nullptr;   // a denoiser has no use for transport information
```

A null `ti` makes JUCE fall back to a dummy `VstTimeInfo` with the correct sample
rate, so no other behaviour changes.

JUCE is vendored in this repo, so the patch survives normal builds; it would be lost
if the JUCE subtree is ever updated. If that happens, re-apply it and re-run
`tools/vst_abi_probe.py` (which answers `getTime` with `1` on purpose).

---

## 7. Debugging playbook

Work from the outside in — each step rules out a whole class of problems.

### 7.1 Is the chain running at all?

```
Device: Sennheiser Profile     ← typo/mismatch = chain disabled
```

Also check EqualizerAPO's Configurator → Troubleshooting: "Disable capture
processing" must be off. Apps that use WASAPI **exclusive** or ASIO bypass APOs
completely; the plugin can never run there.

### 7.2 Is the DLL mapped into `audiodg.exe`?

Module enumeration needs admin. A share-mode-free check while a stream is open:

```powershell
try {
    $fs = [IO.File]::Open("C:\Program Files\EqualizerAPO\VSTPlugins\rnnoise_mono.dll", 'Open', 'Read', 'None')
    $fs.Close(); "not loaded"
} catch { "LOADED" }
```

Calibrate it against a system DLL (`kernel32.dll`) if the result looks odd — loaded
modules always fail exclusive open.

### 7.3 Did the APO call `processReplacing`?

Temporary logging in `RnNoiseAudioProcessor::prepareToPlay/processBlock` and in
`JuceVSTWrapper::processReplacing` is the fastest way to separate
"not called" from "called and crashed".

- Log to `C:\ProgramData\<name>.log`; `audiodg` runs as `LOCAL SERVICE`, so grant it
  write access: `icacls C:\ProgramData\<name>.log /grant "LOCAL SERVICE:(M)"`.
- `fopen(..., "a")` + `fclose` per line. A crash between the two leaks the handle and
  locks the file until `audiodg` restarts — if the log suddenly cannot be truncated,
  that is why.
- Remove the logging once done; a per-block `fopen` has no place in a release build.

Interpretation:

| Observation | Meaning |
|---|---|
| `prepareToPlay` only, no `processBlock` | `skipProcessing = true`, or `processReplacing` is null (see §5) |
| Neither | The APO never instantiated the plugin — wrong device, chain disabled, or a non-APO audio path |
| `processBlock` + unchanged audio | You are measuring with DirectShow (see §4), or an EQ filter after the VST is masking it |

### 7.4 Read EqualizerAPO's own log

```
C:\Windows\ServiceProfiles\LocalService\AppData\Local\Temp\EqualizerAPO.log
```

Not readable without admin. Relevant messages:

- "crashed during initialization" → `initialize()` failed, `skipProcessing` set
- "crashed while preparing for processing" → SEH in `prepareForProcessing`
- "crashed during audio processing" → SEH inside the plugin's process call

### 7.5 How EqualizerAPO decides to skip

From `filters/VSTPluginFilter.cpp` (1.4.2):

- `initialize()` → `skipProcessing = true` if the first effect fails to initialise.
- `prepareForProcessing()` → `skipProcessing = true` on SEH.
- `process()` → if `skipProcessing`, plain input→output copy; otherwise
  `processDoubleReplacing` if advertised, else `processReplacing`, else legacy
  `process`, all inside SEH.
- Effect count = `ceil(hostChannels / max(numInputs, numOutputs))`; a mono endpoint
  with this 2-in/2-out JUCE plugin gives one instance, and only one channel is used.

---

## 8. Tuning

| Knob | Effect | Recommendation |
|---|---|---|
| `vad_threshold` | how eagerly blocks are muted | 0.5 is a good balance; 0.7 if room noise leaks; 0.3 if quiet speech is cut |
| `vad_grace_period` | tail after speech | 20 (200 ms) feels natural; 10 is snappier, 30+ clips more |
| `vad_retroactive_grace_period` | look-back on onsets | 2 (20 ms) is enough for most mics |
| `Preamp` | level into the plugin and to the app | 12 dB was hot (peaks at 0 dBFS); 10 dB is safer |

Latency: RNNoise works on 480-sample (10 ms) frames and the plugin buffers a couple
of frames plus the retroactive window, so expect roughly 20–30 ms of added delay,
plus the 200 ms gate hold when a phrase ends.

---

## 9. Files

| Path | Purpose |
|---|---|
| `src/juce_plugin/RnNoiseAudioProcessor.cpp` | JUCE processor; SEH guard, channel safety nets |
| `src/common/src/RnNoiseCommonPlugin.cpp` | RNNoise + VAD gate; empty-channel guard |
| `external/FST/fst/fst.h` | VST2 `AEffect` — fixed ABI, see §5 |
| `external/JUCE/.../VST/juce_VST_Wrapper.cpp` | `audioMasterGetTime` guard, see §6 |
| `tools/vst_abi_probe.py` | AEffect dump + offline processing |
| `tools/apo-capture/` | WASAPI shared-mode recorder |
| `tools/verify_capture.py` | gate / noise-floor analysis |
| `tools/make_vst_chunk.py` | build `ChunkData` for config.txt |
| `ARM64-WINDOWS-BUILD-NOTES.md` | ARM64 toolchain and JUCE patches |

---

## 10. Symptom → cause → fix

| Symptom | Likely cause | Fix |
|---|---|---|
| Plugin loads, `prepareToPlay` runs, no `processBlock` | `processReplacing` at the wrong offset (null) | Restore the FST layout, verify with `vst_abi_probe.py` |
| Same, but `AEffect` looks right | `skipProcessing` after init/prepare failure | Read `EqualizerAPO.log`; fix the reported crash |
| `audiodg` crashes, all audio drops | exception escaping the plugin | SEH guard around the DSP; `getTime` guard |
| Captured audio never shows processing | DirectShow capture bypasses APOs | record with `tools/apo-capture` (WASAPI) |
| Gate never closes | threshold too low for the noise floor | raise `vad_threshold` |
| Word starts clipped | retroactive grace too small | raise `vad_retroactive_grace_period` |
| Speech sounds chopped | grace too small | raise `vad_grace_period` |
| Peaks at 0 dBFS | too much Preamp | lower `Preamp` |
| "file in use" when copying the DLL | a stream still has it mapped | close the app / `Restart-Service audiosrv -Force` |
