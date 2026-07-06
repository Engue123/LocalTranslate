# Changelog

All notable changes to LocalTranslate are documented here.

## [1.0.0]

First public release.

### Features
- Fully offline translation of Ren'Py games with local GGUF models (`llama-cpp-python`).
- AST-exact extraction of dialogue, menu choices and UI strings.
- Two model tiers: a fast universal machine-translation model, and a larger
  instruction-following model with gender and register control.
- Guaranteed Ren'Py markup safety (mask → translate → validate → retry → fallback).
- Automatic font adaptation when the game's font lacks the target language's glyphs.
- Grammatical gender control (declared main character, inferred cast, addressed listener).
- Deterministic French elision post-editing.
- Delta mode to complete an existing partial patch.
- In-game language switcher and an automatic post-run quality report.
- Graphical (CustomTkinter) and command-line interfaces.

### Notes
- Deeply tested from English into French on real games; other target languages are
  supported by the pipeline and welcome community feedback.
- Reference platform: macOS. Windows and Linux are compatible by design and awaiting
  community validation.
