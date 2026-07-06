"""
AST-first Ren'Py translation core.

The compiled `.rpyc` file *is* the pickled AST the engine actually runs (it can
desync from the `.rpy` text). To produce translation files Ren'Py will load, we
read that AST and compute dialogue identifiers with the *exact* algorithm Ren'Py
uses at compile time:

    identifier = label + "_" + md5(say_get_code(node) + "\\r\\n").hexdigest()[:8]

(verified byte-for-byte against a real commercial game's official translation).

Modules:
  loader      — `.rpyc` (v1/v2) bytes → AST statements (via vendored unrpyc).
  identifiers — faithful identifier computation + per-file uniqueness allocator.
  walker      — walk the AST, yield DialogueUnit with correct identifiers.
"""
from core.renpy_ast.loader import load_ast, load_ast_safe, ast_classes
from core.renpy_ast.walker import (
    DialogueUnit, StringUnit, walk_dialogue, walk_strings, collect_character_names,
)
from core.renpy_ast.structure import analyze_game, StructureReport
from core.renpy_ast.gender import (
    collect_character_defs, find_mc_tags, build_speaker_contexts,
    normalize_mc_gender, infer_cast_genders,
)

__all__ = [
    "load_ast", "load_ast_safe", "ast_classes",
    "DialogueUnit", "StringUnit", "walk_dialogue", "walk_strings",
    "collect_character_names",
    "analyze_game", "StructureReport",
    "collect_character_defs", "find_mc_tags", "build_speaker_contexts",
    "normalize_mc_gender", "infer_cast_genders",
]
