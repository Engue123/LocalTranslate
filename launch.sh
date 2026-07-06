#!/bin/bash
# =============================================================================
# LocalTranslate — Launcher Universel
# =============================================================================
# Une commande = TOUT :
#   1. Vérifie Python 3.11+ et Tkinter
#   2. Crée/recrée le venv si besoin
#   3. Installe toutes les dépendances
#   4. Vérifie/télécharge le modèle universel (HY-MT Q6) via le registre
#   5. Lance la suite de tests
#   6. Lance la GUI
# =============================================================================

set -e

PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "$0")" && pwd)}"
cd "$PROJECT_DIR"

echo "========================================"
echo "  LocalTranslate — Launcher"
echo "  $(date)"
echo "========================================"

# -----------------------------------------------------------------------------
# 1. Python & Tkinter
# -----------------------------------------------------------------------------
echo ""
echo "[1/6] Checking Python 3.11+ and Tkinter..."

PYTHON=""
for cmd in python3.12 python3.11 python3; do
    if command -v "$cmd" &>/dev/null; then
        ver=$($cmd --version 2>&1 | awk '{print $2}')
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -eq 3 ] && [ "$minor" -ge 11 ]; then
            PYTHON=$cmd
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "ERROR: Python 3.11+ not found. Install with: brew install python@3.11"
    exit 1
fi

echo "  Found: $PYTHON ($($PYTHON --version 2>&1))"

if ! $PYTHON -c "import tkinter; print(tkinter.Tcl().eval('info patchlevel'))" &>/dev/null; then
    echo "  Tkinter missing. Attempting fix with Homebrew..."
    if command -v brew &>/dev/null; then
        brew install python-tk@3.11 2>/dev/null || true
    fi
    if ! $PYTHON -c "import tkinter" &>/dev/null; then
        echo "ERROR: Tkinter still missing. Please install python-tk@3.11 via Homebrew."
        exit 1
    fi
fi
echo "  Tkinter OK"

# -----------------------------------------------------------------------------
# 2. Virtual Environment
# -----------------------------------------------------------------------------
echo ""
echo "[2/6] Setting up virtual environment..."

if [ ! -d ".venv" ] || [ ! -f ".venv/bin/python" ]; then
    echo "  Creating .venv with $PYTHON..."
    rm -rf .venv
    $PYTHON -m venv .venv
fi

source .venv/bin/activate
echo "  Venv activated: $(python --version)"

# -----------------------------------------------------------------------------
# 3. Dependencies
# -----------------------------------------------------------------------------
echo ""
echo "[3/6] Installing dependencies..."

pip install --upgrade pip -q
pip install -r requirements.txt pytest -q

if ! python -c "import llama_cpp" &>/dev/null; then
    echo "  Building llama-cpp-python with Metal..."
    export CMAKE_ARGS="-DGGML_METAL=on"
    pip install llama-cpp-python --no-cache-dir -q
fi

pip install tkinterdnd2 -q 2>/dev/null || echo "  tkinterdnd2 optional, skipped"
pip install unrpyc -q 2>/dev/null || echo "  unrpyc optional, skipped"

echo "  All dependencies ready"

# -----------------------------------------------------------------------------
# 4. Model Check / Download
# -----------------------------------------------------------------------------
echo ""
echo "[4/6] Checking universal model (HY-MT 1.5 Q6)..."

# Resolve via the project's own registry (single source of truth) so the launcher
# always tracks the current model set. Only the universal default is ensured here;
# the opt-in Quality model (NemoMix) is downloaded on demand from the GUI/CLI.
PYTHONPATH=. python -c "
from core import model_registry as reg, model_manager as mm
spec = reg.get('q6')
p = mm.local_path(spec)
if p:
    print(f'  Model ready: {p.name}')
else:
    import sys
    print(f'  Downloading {spec.label} (~{spec.size_gb:.1f} GB, once, resumable)...')
    def prog(fr, done, total):
        sys.stdout.write(f'\r    {fr*100:5.1f}%  {done>>20}/{total>>20} MB'); sys.stdout.flush()
    mm.download(spec, progress=prog)
    print('\n  Model downloaded.')
"

# -----------------------------------------------------------------------------
# 5. Run All Tests
# -----------------------------------------------------------------------------
echo ""
echo "[5/6] Running test suite..."

# Full fast suite (skip the one test that loads the real GGUF — slow).
PYTHONPATH=. pytest --deselect tests/test_step3.py::test_llama_translation -q

# -----------------------------------------------------------------------------
# 6. Launch GUI
# -----------------------------------------------------------------------------
echo ""
echo "[6/6] Launching LocalTranslate GUI..."
echo ""

python main.py

echo ""
echo "LocalTranslate exited."
