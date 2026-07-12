"""
build.py -- compile the REAL firmware sources + mock HAL into a host DLL.

The include order puts sil/mock_hal FIRST so the mock i2c.h / fdcan.h /
gpio.h shadow the CubeMX ones. No file under Core/ or Drivers/ is modified.

Usage:  python sil/build.py [--force]
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

SIL_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SIL_DIR)
BUILD_DIR = os.path.join(SIL_DIR, "build")
OBJ_DIR = os.path.join(BUILD_DIR, "obj")
DLL_PATH = os.path.join(BUILD_DIR, "minirideheight_sil.dll")

# (path relative to ROOT, language)  -- the real firmware sources, unmodified
SOURCES = [
    ("Drivers/VL53L4CD_ULD_Driver/VL53L4CD_api.c",         "c"),
    ("Drivers/VL53L4CD_ULD_Driver/VL53L4CD_calibration.c", "c"),
    ("Drivers/Platform/platform.c",                        "c"),
    ("Core/Src/BoardManager.c",                            "c"),
    ("Core/Src/gpio.c",                                    "c"),
    ("Core/Src/CANMessage.cpp",                            "cpp"),
    ("Core/Src/CANDriver.cpp",                             "cpp"),
    ("Core/Src/CAN_driver_wrapper.cpp",                    "cpp"),
    # SIL-only glue (does not touch Core/ or Drivers/):
    ("sil/mock_hal/mock_hal.c",                            "c"),
    ("sil/sil_shim.c",                                     "c"),
    ("sil/sil_probe.cpp",                                  "cpp"),
]

INCLUDE_DIRS = [
    os.path.join(SIL_DIR, "mock_hal"),   # must come first: shadows CubeMX headers
    os.path.join(ROOT, "Core", "Inc"),
    os.path.join(ROOT, "Drivers", "Platform"),
    os.path.join(ROOT, "Drivers", "VL53L4CD_ULD_Driver"),
]

CFLAGS = ["-O0", "-g", "-Wall", "-fdiagnostics-color=never"]

# Diagnostic warning set for `--warnings` mode. NOTE: AddressSanitizer /
# UBSan are NOT supported by MinGW-w64 gcc on Windows, so extended compiler
# diagnostics are the closest available memory/UB net in this toolchain.
DIAG_FLAGS = ["-Wextra", "-Wpedantic", "-Wconversion", "-Wshadow",
              "-Werror=return-type", "-Wno-unused-parameter"]

FALLBACK_COMPILER_DIRS = [
    r"C:\msys64\ucrt64\bin",
    r"C:\msys64\mingw64\bin",
]


def find_compilers() -> tuple[str, str, str]:
    """Return (gcc, g++, bin_dir). Raises SystemExit with guidance if absent."""
    gcc = os.environ.get("SIL_GCC") or shutil.which("gcc")
    gxx = os.environ.get("SIL_GXX") or shutil.which("g++")
    if not (gcc and gxx):
        for d in FALLBACK_COMPILER_DIRS:
            cand_gcc = os.path.join(d, "gcc.exe")
            cand_gxx = os.path.join(d, "g++.exe")
            if os.path.isfile(cand_gcc) and os.path.isfile(cand_gxx):
                gcc, gxx = cand_gcc, cand_gxx
                break
    if not (gcc and gxx):
        sys.exit(
            "SIL build error: no host C/C++ compiler found.\n"
            "Install MSYS2 (https://www.msys2.org) and the ucrt64 gcc toolchain,\n"
            "or set SIL_GCC / SIL_GXX to the full paths of gcc/g++."
        )
    return gcc, gxx, os.path.dirname(gcc)


def needs_rebuild() -> bool:
    if not os.path.isfile(DLL_PATH):
        return True
    dll_mtime = os.path.getmtime(DLL_PATH)
    watched = [os.path.join(ROOT, rel) for rel, _ in SOURCES]
    watched += [
        os.path.join(SIL_DIR, "mock_hal", h)
        for h in ("sil_hal.h", "stm32g4xx_hal.h")
    ]
    watched.append(os.path.abspath(__file__))
    # sil_probe.cpp exports these macros to Python.  A header-only constant
    # change must therefore invalidate the DLL even when no .c/.cpp mtime
    # changed.
    watched.append(os.path.join(ROOT, "Core", "Inc", "BoardManager.h"))
    return any(os.path.getmtime(p) > dll_mtime for p in watched if os.path.isfile(p))


def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write("SIL build failed.\nCommand: " + " ".join(cmd) + "\n")
        sys.stderr.write(proc.stdout + "\n" + proc.stderr + "\n")
        sys.exit(1)
    # surface warnings but keep going
    if proc.stderr.strip():
        sys.stderr.write(proc.stderr)


def build(force: bool = False, diagnostics: bool = False) -> str:
    gcc, gxx, bin_dir = find_compilers()
    if not force and not diagnostics and not needs_rebuild():
        return DLL_PATH

    os.makedirs(OBJ_DIR, exist_ok=True)
    includes = []
    for inc in INCLUDE_DIRS:
        includes += ["-I", inc]
    flags = CFLAGS + (DIAG_FLAGS if diagnostics else [])

    objects = []
    for rel, lang in SOURCES:
        src = os.path.join(ROOT, rel)
        obj = os.path.join(OBJ_DIR, rel.replace("/", "_").rsplit(".", 1)[0] + ".o")
        objects.append(obj)
        compiler = gxx if lang == "cpp" else gcc
        std = ["-std=gnu++14"] if lang == "cpp" else ["-std=gnu11"]
        run([compiler, "-c", src, "-o", obj] + std + flags + includes)

    # -static-* so the DLL loads without MSYS2 runtime DLLs on PATH
    run([gxx, "-shared", "-o", DLL_PATH] + objects +
        ["-static-libgcc", "-static-libstdc++"])
    return DLL_PATH


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="rebuild even if up to date")
    ap.add_argument("--warnings", action="store_true",
                    help="diagnostic build: -Wextra -Wconversion -Wshadow ... "
                         "(prints every warning the firmware sources produce; "
                         "ASan/UBSan are unavailable in MinGW-w64 gcc)")
    args = ap.parse_args()
    dll = build(force=args.force or args.warnings, diagnostics=args.warnings)
    print(f"SIL DLL ready: {dll}")


if __name__ == "__main__":
    main()
