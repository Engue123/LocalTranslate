#!/usr/bin/env python3
"""Environment check for LocalTranslate.

Verifies the Python version, the required dependencies, the local inference
backend and whether a model is present, then prints a readable report and exits
non-zero if something essential is missing.

    python scripts/check_env.py
"""
import importlib.util
import os
import platform
import sys
from pathlib import Path

MIN_PYTHON = (3, 10)

DEPENDENCIES = {
    "llama_cpp": "llama-cpp-python  (local model inference)",
    "customtkinter": "customtkinter     (graphical interface)",
    "fontTools": "fonttools         (font glyph coverage)",
    "fast_langdetect": "fast-langdetect   (source language detection)",
}


def _mark(ok: bool) -> str:
    return "✓" if ok else "✗"


def main() -> int:
    print("LocalTranslate — environment check")
    print("=" * 44)

    # --- Python ---
    v = sys.version_info
    py_ok = (v.major, v.minor) >= MIN_PYTHON
    print(f"[{_mark(py_ok)}] Python {v.major}.{v.minor}.{v.micro}  "
          f"(need {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+)")
    print(f"    Platform: {platform.system()} {platform.machine()}")

    # --- dependencies ---
    print("\nDependencies:")
    missing = []
    for module, label in DEPENDENCIES.items():
        found = importlib.util.find_spec(module) is not None
        print(f"  [{_mark(found)}] {label}")
        if not found:
            missing.append(label)

    # --- inference backend (best effort) ---
    print("\nInference backend:")
    if importlib.util.find_spec("llama_cpp") is not None:
        backend = "installed (backend undetermined)"
        try:
            # llama.cpp logs device init to stderr (fd 2) on import — silence it
            # for a clean report, then probe whether GPU offload is available.
            with open(os.devnull, "w") as devnull:
                saved = os.dup(2)
                os.dup2(devnull.fileno(), 2)
                try:
                    import llama_cpp
                    gpu = llama_cpp.llama_supports_gpu_offload()
                finally:
                    os.dup2(saved, 2)
                    os.close(saved)
            backend = ("GPU offload available (Metal / CUDA)" if gpu
                       else "CPU only (no GPU offload)")
        except Exception:
            pass
        print(f"  [✓] llama-cpp-python present — {backend}")
    else:
        print("  [✗] llama-cpp-python not installed "
              "(see the README, installation step 2)")

    # --- models ---
    print("\nModels:")
    models_dir = Path(__file__).resolve().parent.parent / "models"
    ggufs = sorted(models_dir.glob("*.gguf")) if models_dir.is_dir() else []
    if ggufs:
        for g in ggufs:
            print(f"  [✓] {g.name}  ({g.stat().st_size / 1e9:.2f} GB)")
    else:
        print("  [i] no local model yet — one is downloaded on first run")

    # --- verdict ---
    print("\n" + "=" * 44)
    essential_missing = (not py_ok) or ("llama-cpp-python" in " ".join(missing))
    if not missing and py_ok:
        print("Ready. ✅")
        return 0
    print("Some components are missing — see the README installation section.")
    if missing:
        print("  Missing: " + ", ".join(m.split("  ")[0] for m in missing))
    return 1 if essential_missing else 0


if __name__ == "__main__":
    sys.exit(main())
