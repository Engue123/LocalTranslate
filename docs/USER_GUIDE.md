# LocalTranslate — User Guide

LocalTranslate is a powerful, offline machine translation tool designed specifically for Ren'Py visual novels and plain text/markdown files.

---

## 1. Installation

To install and set up LocalTranslate on your machine, run the installation script:

```bash
./install.sh
```

This script will:
- Set up a Python 3.11 virtual environment (`.venv`)
- Install all necessary dependencies (`customtkinter`, `llama-cpp-python`, `fast-langdetect`, etc.)
- Configure Metal acceleration for macOS Apple Silicon GPUs.

---

## 2. GUI Walkthrough

Launch the graphical user interface by running:

```bash
python main.py
```

### Key Elements of the Interface:
1. **Source Game Directory**: Select the root folder of the Ren'Py game (containing the `game/` subdirectory or `.rpy` scripts).
2. **Output Directory**: Define where the generated translation files and reports will be saved.
3. **Project Type**: Choose between **RenPy Game** and **Plain Text / Markdown**.
4. **Target Language**: Select the target language for translation.
5. **Translation Mode**: Toggle between Mode A (Native) and Mode B (Fallback).
6. **Translation Backend**: Select your translation model backend (e.g. CTranslate2 NLLB, Llama.cpp, MarianMT, or Mock).
7. **Optional Style Hint**: Provide context (e.g., *formal*, *romantic*, *slang*) to guide the LLM's tone.
8. **Auto-Tune Batch Size**: Automatically measures hardware throughput and sets the fastest batch size.
9. **Preview**: Instantly translates the first 10 strings so you can check quality before executing a full run.
10. **Run Translation / Dry Run / Cancel**: 
    - **Run Translation** initiates the process.
    - **Dry Run** generates a statistics report `dryrun_report.md` without consuming API/model tokens.
    - **Cancel** allows you to safely interrupt a running translation.

---

## 3. CLI Usage

You can also run LocalTranslate in headless/CLI mode.

```bash
python main.py --cli --source <path_to_game> --output <output_path> --src-lang en --tgt-lang fr --backend llama --mode A
```

### CLI Arguments:
- `--source`, `-s`: Path to the game folder.
- `--output`, `-o`: Path to output directory.
- `--src-lang`: Source language (default: `en`).
- `--tgt-lang`: Target language (default: `fr`).
- `--mode`, `-m`: Translation mode: `A` (Native) or `B` (Fallback).
- `--backend`, `-b`: Backend: `nllb`, `marian`, `llama`, `mock` (default: `nllb`).
- `--style-hint`, `-t`: Translation tone style guide (e.g. *slang*).
- `--cli`: Forces running in CLI mode.

---

## 4. Translation Modes Explained (A vs B)

### Mode A (Native Translation Files)
Mode A mirrors the Ren'Py script file structure under the `game/tl/<lang>/` folder. It matches the original scripts line-by-line and generates official `.rpy` translation blocks:
```renpy
# game/script.rpy:10
translate french start_1a2b3c4d:
    # e "Hello!"
    e "Bonjour !"
```
This is the recommended mode for visual novels as it preserves individual character scripts.

### Mode B (Fallback Strings)
Mode B compiles all unique translation pairs into a single global translation dictionary file called `fallback.rpy` inside `game/tl/<lang>/`.
```renpy
translate french strings:
    old "Hello!"
    new "Bonjour !"
```
This is useful if you want a quick, global substitution or for generic UI strings.

---

## 5. Updating a Translation (Incremental Update Diff)

When the source game is updated (e.g., a new chapter/episode is released):
- LocalTranslate scans the existing target `tl/` directory and identifies previously translated lines.
- It calculates the diff, translating **only** the new or modified strings.
- Cached translations are injected directly without calling the LLM backend, saving significant time and compute.

---

## 6. Troubleshooting

- **GUI won't open / Tkinter missing**: Run `brew install python-tk@3.11` on macOS to install Tkinter support.
- **Low performance / High CPU load**: Ensure that you select the **Llama.cpp** backend and that **Metal** acceleration is enabled (indicated by GPU layers set to `-1` in settings). Run **Auto-Tune Batch Size** to optimize performance.
- **Mangled Tags/Placeholders**: If variables like `[player_name]` or formatting tags like `{b}...{/b}` are lost, verify that you are translating a Ren'Py game project. The built-in safeguard automatically masks variables to prevent LLMs from altering them. Check `quality_report.md` in the output folder for any warnings.
