import pytest
import tempfile
import shutil
from pathlib import Path

from core.models import UnitType, TranslationUnit
from plugins.extractors.renpy import RenPyExtractor
from plugins.generators.renpy import RenPyGenerator
from core.safeguards import TagProtector, Safeguard, protect, restore, protect_batch, restore_batch


@pytest.fixture
def temp_game_dir():
    """Fixture to manage a mock source game directory."""
    with tempfile.TemporaryDirectory() as temp_src:
        src_path = Path(temp_src)
        
        # Create a mock game structure
        game_dir = src_path / "game"
        scripts_dir = game_dir / "scripts"
        scripts_dir.mkdir(parents=True)
        
        # Create an e1s1.rpy script
        script_content = """# Character definitions
define e = Character("Eileen")
define p = Character(_("Protagonist"))

label start:
    scene background_black
    
    # Narration and dialogue
    "You enter the room."
    e "Welcome to the first episode."
    
    # Menu items
    menu choice_menu:
        "Yes, it is.":
            e "Indeed."
        "No, go back." if points > 5:
            jump stay
            
    # UI Strings
    $ title = _("My Visual Novel")
    $ subtitle = _p("A beautiful story.")
    
    # Screen UI
    screen main_hud:
        text "Score: [points]"
        textbutton "Open Options" action Show("options")
        
    # Ignore colors, variables alone, and system words
    #ff00ff
    [mood]
    "self-voicing"
    "toggle console"
"""
        script_file = scripts_dir / "e1s1.rpy"
        with open(script_file, "w", encoding="utf-8") as f:
            f.write(script_content)
            
        # Create options.rpy in game root
        options_content = """define config.name = _("My Game Options")"""
        with open(game_dir / "options.rpy", "w", encoding="utf-8") as f:
            f.write(options_content)
            
        # Create a tl/ sub-folder to ensure it is ignored
        tl_dir = game_dir / "tl" / "fr"
        tl_dir.mkdir(parents=True, exist_ok=True)
        with open(tl_dir / "ignored.rpy", "w", encoding="utf-8") as f:
            f.write('e "This should be ignored."\n')
            
        # Create a renpy/common/ directory structure to make sure it is ignored
        common_dir = game_dir / "renpy" / "common"
        common_dir.mkdir(parents=True, exist_ok=True)
        with open(common_dir / "common_script.rpy", "w", encoding="utf-8") as f:
            f.write('e "This common script should be ignored."\n')
            
        yield src_path


def test_renpy_extractor_can_handle(temp_game_dir):
    """Test can_handle function on files and folders."""
    extractor = RenPyExtractor()
    
    # Project directory should be handled because it has a game folder containing .rpy
    assert extractor.can_handle(temp_game_dir)
    
    # A single .rpy file should be handled
    script_file = temp_game_dir / "game" / "scripts" / "e1s1.rpy"
    assert extractor.can_handle(script_file)
    
    # A random directory or non-rpy file should not be handled
    with tempfile.TemporaryDirectory() as empty_dir:
        assert not extractor.can_handle(Path(empty_dir))


def test_renpy_extraction(temp_game_dir):
    """Extract units and verify expectations and ignored elements."""
    extractor = RenPyExtractor()
    units = extractor.extract(temp_game_dir)
    
    assert len(units) > 0
    
    # Check that it extracted the specific required dialogue and UI title
    original_texts = [u.original_text for u in units]
    assert "Welcome to the first episode." in original_texts
    assert "My Visual Novel" in original_texts
    
    # Verify we extracted character names wrapped in _() (translatable)...
    assert "Protagonist" in original_texts
    # ...but NOT a bare proper-noun character name (vigilance rule)
    assert "Eileen" not in original_texts
    
    # Check screen texts
    assert "Score: [points]" in original_texts
    assert "Open Options" in original_texts
    
    # Check menu items
    assert "Yes, it is." in original_texts
    assert "No, go back." in original_texts
    
    # Check that we ignored non-textual lines, system strings and tl/ folder
    assert "This should be ignored." not in original_texts
    assert "This common script should be ignored." not in original_texts
    assert "#ff00ff" not in original_texts
    assert "[mood]" not in original_texts
    assert "self-voicing" not in original_texts
    assert "toggle console" not in original_texts


def test_renpy_extractor_boundaries(temp_game_dir):
    """Ensure renpy/common and tl folders are not walked at all."""
    extractor = RenPyExtractor()
    units = extractor.extract(temp_game_dir)
    
    # Check that no unit file path contains tl or renpy/common
    for u in units:
        path_str = u.file_path.as_posix()
        assert "tl/" not in path_str
        assert "renpy/common" not in path_str


def test_renpy_generation_mode_a(temp_game_dir):
    """Mirror hierarchical directory structure in Mode A."""
    src_dir = temp_game_dir
    
    extractor = RenPyExtractor()
    units = extractor.extract(src_dir)
    
    # Mock translations
    for u in units:
        u.translated_text = f"[FR] {u.original_text}"
        
    with tempfile.TemporaryDirectory() as temp_out:
        out_dir = Path(temp_out)
        generator = RenPyGenerator()
        generator.generate(units, src_dir, out_dir, "fr", mode="A")
        
        # Output path must mirror hierarchical source file:
        # e.g., scripts/e1s1.rpy -> game/tl/fr/scripts/e1s1.rpy
        output_file = out_dir / "game" / "tl" / "fr" / "scripts" / "e1s1.rpy"
        assert output_file.exists()
        
        # options.rpy contained only a `_()` string (no dialogue), so it is NOT
        # mirrored as its own file — its string is consolidated (checked below).
        assert not (out_dir / "game" / "tl" / "fr" / "options.rpy").exists()

        with open(output_file, "r", encoding="utf-8") as f:
            content = f.read()

        # The mirrored file carries the DIALOGUE (translate <id>: blocks).
        assert "translate fr" in content
        assert 'e "[FR] Welcome to the first episode."' in content

        # STRING translations live in ONE consolidated, globally-deduped file —
        # Ren'Py rejects a duplicate `old` across the whole tl/<lang>/.
        strings_file = out_dir / "game" / "tl" / "fr" / "localtranslate_strings.rpy"
        assert strings_file.exists()
        scontent = strings_file.read_text(encoding="utf-8")
        assert "translate fr strings:" in scontent
        assert 'old "Protagonist"' in scontent
        assert 'new "[FR] Protagonist"' in scontent
        assert 'old "My Game Options"' in scontent      # from options.rpy, consolidated
        assert "    old " in scontent
        assert "    new " in scontent


def test_renpy_generation_mode_b(temp_game_dir):
    """Generate a single global fallback.rpy in Mode B."""
    src_dir = temp_game_dir
    
    extractor = RenPyExtractor()
    units = extractor.extract(src_dir)
    
    for u in units:
        u.translated_text = f"[FR] {u.original_text}"
        
    with tempfile.TemporaryDirectory() as temp_out:
        out_dir = Path(temp_out)
        generator = RenPyGenerator()
        generator.generate(units, src_dir, out_dir, "fr", mode="B")
        
        # Output path must be game/tl/fr/fallback.rpy
        output_file = out_dir / "game" / "tl" / "fr" / "fallback.rpy"
        assert output_file.exists()
        
        with open(output_file, "r", encoding="utf-8") as f:
            content = f.read()
            
        # Should contain translate fr strings block
        assert "translate fr strings:" in content
        assert 'old "Welcome to the first episode."' in content
        assert 'new "[FR] Welcome to the first episode."' in content


def test_tag_protector_mask_unmask():
    """Verify single-text tag masking and unmasking works correctly."""
    original = "Hello {b}[name]{/b}! Let's check {color=#fff}this{/color}."
    
    masked, items = TagProtector.mask(original)
    assert "__TAG_" in masked
    assert "[name]" not in masked
    assert "{b}" not in masked
    
    unmasked, warnings = TagProtector.unmask(masked, items)
    assert unmasked == original
    assert len(warnings) == 0
    
    # Test with minor tag capitalization variations by model
    mangled_masked = masked.replace("__TAG_0__", "__tag_0__").replace("__TAG_1__", "__ TAG_1 __")
    unmasked_mangled, warnings_mangled = TagProtector.unmask(mangled_masked, items)
    assert unmasked_mangled == original
    assert len(warnings_mangled) == 0


def test_safeguard_batch_protection():
    """Verify Safeguard and protect/restore batch functions work correctly."""
    texts = [
        "Select {i}[hero]{/i} option.",
        "Points: [points], Speed: {w=1.0}[speed]."
    ]
    
    # Test batch protect/restore helper functions
    protected_results = protect_batch(texts)
    assert len(protected_results) == 2
    
    proto_texts = [r[0] for r in protected_results]
    mappings = [r[1] for r in protected_results]
    
    assert "[hero]" not in proto_texts[0]
    assert "[points]" not in proto_texts[1]
    
    restored_texts = restore_batch(proto_texts, mappings)
    assert restored_texts == texts
    
    # Test Safeguard class
    safeguard = Safeguard()
    text = "Look at this [var]!"
    masked, mapping = safeguard.mask(text)
    assert "[var]" not in masked
    assert safeguard.unmask(masked, mapping) == text


def test_rpa_extraction():
    import pickle
    import zlib
    from core.rpa_extractor import RPAExtractor
    
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        archive_file = temp_path / "test.rpa"
        
        # 1. Define files to package
        files_to_pack = {
            "game/script.rpy": b"label start:\n    'Hello RPA'",
            "game/chapter1.rpyc": b"MOCKED RPYC BYTECODE",
        }
        
        # 2. Build index dictionary: filename -> [(offset, length, prefix)]
        key = 0xDEADBEEF
        dummy_header = f"RPA-3.0 {0:016x} {key:08x}\n"
        header_len = len(dummy_header.encode("utf-8"))
        
        file_offsets = {}
        current_offset = header_len
        
        packed_data = bytearray()
        for filename, content in files_to_pack.items():
            file_offsets[filename] = (current_offset, len(content))
            packed_data.extend(content)
            current_offset += len(content)
            
        index_offset = current_offset
        
        # Build index data to pickle
        raw_index = {}
        for filename, (offset, length) in file_offsets.items():
            xor_offset = offset ^ key
            xor_length = length ^ key
            raw_index[filename] = [(xor_offset, xor_length, b"")]
            
        # Pickle and compress index
        pickled = pickle.dumps(raw_index, protocol=2)
        compressed_index = zlib.compress(pickled)
        
        # Write the whole RPA file
        with open(archive_file, "wb") as f:
            header_str = f"RPA-3.0 {index_offset:016x} {key:08x}\n"
            f.write(header_str.encode("utf-8"))
            f.write(packed_data)
            f.write(compressed_index)
            
        # 3. Test extractor
        with RPAExtractor(archive_file) as rpa:
            assert rpa.version == 3.0
            assert rpa.key == key
            
            filenames = rpa.list_files()
            assert "game/script.rpy" in filenames
            assert "game/chapter1.rpyc" in filenames
            
            assert rpa.read_file("game/script.rpy") == b"label start:\n    'Hello RPA'"
            assert rpa.read_file("game/chapter1.rpyc") == b"MOCKED RPYC BYTECODE"
            
            # Test extracting to disk
            out_script = temp_path / "extracted_script.rpy"
            rpa.extract_file("game/script.rpy", out_script)
            assert out_script.exists()
            assert out_script.read_bytes() == b"label start:\n    'Hello RPA'"

