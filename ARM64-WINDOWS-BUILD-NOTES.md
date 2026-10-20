# Windows ARM64 build notes

Session notes from getting noise-suppression-for-voice to build natively on
Windows 11 ARM64. Kept for reuse; not part of the upstream project.

## Environment

| Item | Value |
|---|---|
| Host | Windows 11 Pro, `PROCESSOR_ARCHITECTURE=ARM64` |
| Compiler | Visual Studio 18 2026 Community, MSVC 14.51 (`HostArm64\arm64\cl.exe`) |
| CMake | 4.4.4 (scoop) |
| vcpkg | `C:\tools\vcpkg`, `arm64-windows` triplet on default include/lib paths |
| Dev shell | `"C:\Program Files\Microsoft Visual Studio\18\Community\VC\Auxiliary\Build\vcvarsall.bat" arm64` |

The Visual Studio generator defaults to the host platform here, so plain
`cmake -B build` already targets ARM64. Do not pass `-A x64`.

## Build

```powershell
cmake -S . -B build                       # VS generator, ARM64 by default
cmake --build build --config Release --parallel
build\src\common\Release\common_plugin_tests.exe
```

Outputs under `build\bin\`:

| Format | Path |
|---|---|
| VST2 | `vst\rnnoise_mono.dll`, `vst\rnnoise_stereo.dll` |
| VST3 | `rnnoise.vst3\Contents\arm64-win\rnnoise.vst3` |
| LV2 | `rnnoise_mono.lv2\`, `rnnoise_stereo.lv2\` |
| LADSPA | `ladspa\Release\rnnoise_ladspa.dll` |

Verify architecture with `dumpbin /headers <file> | findstr machine`
(expect `AA64 machine (ARM64)`).

## Changes made

Two commits, pushed to `git@github.com:wisemanny/noise-suppression-for-voice.git` (`master`).
Remote name in the local clone: `fork` (`origin` still points at werman's upstream).

### Commit 1: `external/JUCE: Fix build for Windows on ARM64`

The vendored JUCE 7.0.1 assumed Windows == x86. Upstream JUCE fixed this in
7.0.3+, so these patches become unnecessary if JUCE is ever updated.

| File | Change |
|---|---|
| `modules/juce_core/system/juce_TargetPlatform.h` | Define `JUCE_ARM` (not `JUCE_INTEL`) when `_M_ARM`, `_M_ARM64` or `_M_ARM64EC` is set. This also disables SSE in `juce_audio_basics` and x86 CPU detection in FLAC, which are all gated on `JUCE_INTEL`. |
| `modules/juce_core/native/juce_win32_SystemStats.cpp` | `__cpuid`/`__rdtsc` pragmas and calls guarded by `JUCE_INTEL`. On ARM, `callCPUID` returns zeros and the cycle counter uses `QueryPerformanceCounter`. |
| `modules/juce_audio_formats/codecs/oggvorbis/libvorbis-1.3.7/lib/os.h` | The "MSVC 64-bit" SSE2 `vorbis_ftoi` path now also requires `JUCE_INTEL`; ARM falls back to the generic `floor()` version. |
| `extras/Build/CMake/JUCEUtils.cmake` | `_juce_create_windows_package` chose the VST3 folder from pointer size only (`x86_64-win`). Now emits `arm64-win` / `arm-win` based on `CMAKE_VS_PLATFORM_NAME` (VS generator) or `CMAKE_SYSTEM_PROCESSOR` (Ninja/toolchain). VST3 hosts on ARM64 only look in `Contents/arm64-win`. |

### Commit 2: `ladspa: Fix build with current MSVC`

Not ARM-specific. Current MSVC rejects `src/ladspa_plugin/ladspa++.h`; this is
the internal compiler error that forced CI onto `windows-2019`.

| Problem | Fix |
|---|---|
| `error C7658: the initializer must be the address of a variable or function` on every `constexpr port_info_t` built from string literals, once used via the `port_array_t` template argument. | Port names/descriptions are now named `constexpr static char name[] = "..."` arrays in a `strings` sub-namespace (`port_info_common::strings` in `ladspa++.h`, `port_info_custom::strings` in `RnNoiseLadspaPlugin.h`). The `info_t` strings did not need this. |
| `fatal error C1001` (ICE) on the `return_value_base_type` / `return_value_access_type` partial specialisations, which do `std::enable_if<bm->is(...)>` on a `const bitmask<port_types>*` template parameter. | Those templates now take `bool` parameters (`is_control`, `is_output`) computed at the call site from `PortDesArray[PortName].descriptor.is(...)`. No pointer NTTP, no static constexpr member copies. |

Things that did NOT help, to save time next round: `/std:c++17`, `/std:c++20`,
`/permissive-`, `/Zc:externConstexpr`, copying only the descriptor into a
static constexpr member, a namespace-scope holder struct, a variable template.

## Verification done

- All targets build in Release; all six plugin binaries are `AA64 machine (ARM64)`.
- `common_plugin_tests.exe`: all tests passed (3 test cases).
- VST3 lands in `Contents\arm64-win`; stale `x86_64-win` folders from the first build were deleted by hand.

## Follow-ups / things noticed

- **rnnoise runs scalar code on this build.** `external/rnnoise/src/vec.h` selects NEON
  via `__ARM_NEON`, which MSVC does not define (it defines `_M_ARM64`). Build with
  clang-cl, or add `|| defined(_M_ARM64)` to that check, to get the NEON kernels.
- **CI** could likely move back from `windows-2019` to `windows-latest` now that the
  LADSPA ICE is fixed. Untested on x64 MSVC.
- **Release zip:** CMake has no packaging step; CI zips `build/bin` with the shell
  `zip`. Options: CPack `ZIP` generator (needs Windows/macOS `install()` rules in
  `src/juce_plugin/CMakeLists.txt`, which are currently `UNIX AND NOT APPLE` only),
  or an `add_custom_target` using `cmake -E tar cf out.zip --format=zip -- bin`.
- **`.claude/settings.local.json`** has an invalid permission rule `"bash"`; tool
  names are case-sensitive, it must be `"Bash"` (or a scoped `"Bash(cmake:*)"`).
  Not yet changed.
- `build/` is untracked; `.gitignore` does not cover it.
