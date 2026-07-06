from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import List, Optional, Dict, Any

class UnitType(str, Enum):
    DIALOGUE = "dialogue"
    MENU = "menu"
    UI_STRING = "ui_string"

@dataclass
class TranslationUnit:
    file_path: Path
    line_number: int
    original_text: str
    translated_text: Optional[str] = None
    unit_type: UnitType = UnitType.DIALOGUE
    character: Optional[str] = None  # None for narrator
    context: Optional[str] = None    # label, screen, or block id
    needs_review: bool = False
    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Unique key for identifying this translation unit."""
        return f"{self.file_path.name}:{self.line_number}:{self.original_text[:20]}"

    @property
    def unit_id(self) -> str:
        """Alias for key to support QualityReport."""
        return self.key

    @property
    def source_text(self) -> str:
        return self.original_text

    @source_text.setter
    def source_text(self, value: str):
        self.original_text = value

@dataclass
class ParseResult:
    units: List[TranslationUnit] = field(default_factory=list)
    total_lines: int = 0
    files_parsed: List[Path] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)      # FATAL — fail the run
    warnings: List[str] = field(default_factory=list)     # non-fatal — never block finalize
    dialogue_extracted: int = 0
    menu_extracted: int = 0
    ui_extracted: int = 0
    system_strings_ignored: int = 0
    non_textual_ignored: int = 0

    def merge(self, other: "ParseResult") -> None:
        """Merge another ParseResult into this one."""
        self.units.extend(other.units)
        self.total_lines += other.total_lines
        self.files_parsed.extend(other.files_parsed)
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        self.dialogue_extracted += other.dialogue_extracted
        self.menu_extracted += other.menu_extracted
        self.ui_extracted += other.ui_extracted
        self.system_strings_ignored += other.system_strings_ignored
        self.non_textual_ignored += other.non_textual_ignored

@dataclass
class EngineResult(ParseResult):
    output_dir: Optional[Path] = None
