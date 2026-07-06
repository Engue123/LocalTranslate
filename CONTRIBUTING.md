# Contributing

## Setup
```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
PYTHONPATH=. pytest      # should report ~185 passed (1 deselected loads the real GGUF)
```

## Golden rules
1. **Read-only source.** Never write next to the input game. All output goes to
   `output_dir` (decompilation → `output_dir/temp_decompilation`).
2. **Llama.cpp only.** Do not add `ctranslate2`, `torch`, or `transformers`.
3. **Tests use the mock.** Inject `MockTranslator`; never load the real GGUF in tests.
4. **Every changed file gets a test.** No regressions; keep the suite green.
5. **Match surrounding style.** Small, reviewable commits with a checkpoint before
   risky changes (`git commit -m "checkpoint: ..."`).

## Adding a new **extractor**
An extractor turns a source file/folder into `TranslationUnit`s.

```python
# plugins/extractors/myformat.py
from pathlib import Path
from typing import List
from core.models import TranslationUnit, UnitType

class MyFormatExtractor:
    def extract(self, source_path: Path) -> List[TranslationUnit]:
        units = []
        # ... read source_path (read-only!), build units ...
        units.append(TranslationUnit(
            file_path=source_path,        # path the generator will mirror
            line_number=1,
            original_text="text to translate",
            unit_type=UnitType.UI_STRING,
        ))
        return units
```
- For Ren'Py-like inputs, store `file_path` **relative to the resolved `game/`**.
- Register it in `ui/app.py:PROJECT_TYPES` so the GUI can select it.

## Adding a new **generator**
A generator writes translated units back to `output_dir`.

```python
# plugins/generators/myformat.py
class MyFormatGenerator:
    def generate(self, translated_units, source_dir, output_dir, target_lang, mode="A"):
        # ... write files under output_dir, mirroring structure ...
        ...

    def finalize(self, output_dir, target_lang):   # optional, called once
        # write any turnkey extras (instructions, helper files)
        ...
```
- `generate()` is called **per file**; `finalize()` (optional) is called **once**
  after a successful run — put one-shot artifacts there, not in `generate()`.

## Running a manual end-to-end check
```bash
python3 main.py --cli -s test_game -o /tmp/out --backend mock --tgt-lang fr
```
