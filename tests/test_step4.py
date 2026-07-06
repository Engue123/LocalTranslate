import pytest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import main


def test_cli_help(capsys):
    """Test CLI help output and arguments routing."""
    with patch.object(sys, 'argv', ['main.py', '--help']):
        with pytest.raises(SystemExit) as excinfo:
            main.main()
        assert excinfo.value.code == 0
        
    captured = capsys.readouterr()
    assert "RenPy AutoTranslate" in captured.out
    assert "--source" in captured.out
    assert "--output" in captured.out


def test_cli_execution(tmp_path):
    """Test full CLI pipeline integration with mock translator."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    
    # Create a small script
    game_dir = src_dir / "game"
    game_dir.mkdir()
    (game_dir / "script.rpy").write_text('label start:\n    "Hello CLI."\n', encoding="utf-8")
    
    out_dir = tmp_path / "out"
    
    args = [
        'main.py',
        '--source', str(src_dir),
        '--output', str(out_dir),
        '--backend', 'mock',
        '--cli'
    ]
    
    with patch.object(sys, 'argv', args):
        with pytest.raises(SystemExit) as excinfo:
            main.main()
        assert excinfo.value.code == 0
        
    # Verify Mode A output
    out_file = out_dir / "game" / "tl" / "fr" / "script.rpy"
    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "translate fr" in content
    assert '[FR] Hello CLI.' in content


def test_cli_mc_gender_flag_reaches_engine(tmp_path, capsys):
    """--mc-gender flows argparse -> pipeline -> engine. With the mock (pure-MT)
    backend the engine surfaces the honest 'cannot use it' note in the log."""
    src_dir = tmp_path / "src"
    game_dir = src_dir / "game"
    game_dir.mkdir(parents=True)
    (game_dir / "script.rpy").write_text('label start:\n    "Hello CLI."\n', encoding="utf-8")

    args = [
        'main.py',
        '--source', str(src_dir),
        '--output', str(tmp_path / "out"),
        '--backend', 'mock',
        '--mc-gender', 'm',
        '--cli'
    ]

    with patch.object(sys, 'argv', args):
        with pytest.raises(SystemExit) as excinfo:
            main.main()
        assert excinfo.value.code == 0

    out = capsys.readouterr().out
    assert "MC gender:   man (declared)" in out
    assert "pure-MT" in out          # honest warning: mock is not an instruct model


def test_cli_nemo_selects_instruct_mode(tmp_path, capsys):
    """--model nemo drives the translator into instruct mode with the registry's
    sampling + ChatML format, and prints the hardware note. No real model is
    loaded (constructor mocked); resolve_path patched for hermeticity."""
    src_dir = tmp_path / "src"
    game_dir = src_dir / "game"
    game_dir.mkdir(parents=True)
    (game_dir / "script.rpy").write_text('label start:\n    "Hello CLI."\n', encoding="utf-8")

    captured = []

    class _FakeTranslator:
        def __init__(self, **kwargs):
            captured.append(kwargs)
            self.glossary = {}
            self.seed = 42
            self.instruct = kwargs.get("instruct", False)

        def translate_batch(self, texts, **kw):
            return [f"[FR] {t}" for t in texts]

    args = ['main.py', '--source', str(src_dir), '--output', str(tmp_path / "out"),
            '--backend', 'llama', '--model', 'nemo', '--cli']

    with patch.object(sys, 'argv', args), \
         patch("core.translator.LlamaCppTranslator", _FakeTranslator), \
         patch("core.model_manager.resolve_path",
               lambda mid, models_dir=None: Path("/fake/nemo.gguf")), \
         patch("core.model_manager.local_path",
               lambda spec: Path("/fake/model.gguf")):
        with pytest.raises(SystemExit) as excinfo:
            main.main()
        assert excinfo.value.code == 0

    # the Quality model is set up in instruct mode with the registry's sampling
    nemo_kw = next(c for c in captured if c.get("instruct"))
    assert nemo_kw["temperature"] == 0.3 and nemo_kw["top_p"] == 0.9
    assert nemo_kw["chat_format"] == "chatml"
    # ...and a pure-MT fallback (q6, non-instruct) is loaded alongside it (Bug 4)
    assert any(not c.get("instruct") for c in captured)
    assert "Quality tier" in capsys.readouterr().out
