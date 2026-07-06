import sys
import argparse
from pathlib import Path

def run_gui():
    """Starts the CustomTkinter GUI."""
    try:
        import customtkinter as ctk
        from ui.app import RenPyAutoTranslateApp
    except ImportError as e:
        print(f"Error: Required GUI dependency missing: {e}")
        print("Please run: pip install -r requirements.txt")
        sys.exit(1)
        
    app = RenPyAutoTranslateApp()
    app.mainloop()

def _resolve_cli_model(model_id):
    """Resolve the GGUF path for --model, downloading the quant if needed."""
    from core import model_manager as mm, model_registry as reg
    path = mm.resolve_path(model_id)
    if path:
        return str(path)
    if model_id:
        spec = reg.get(model_id)
        if spec and spec.url:
            print(f"Model '{spec.label}' not present — downloading {spec.size_gb:.2f} GB ...")

            def _p(fr, done, total):
                print(f"\r  {fr*100:5.1f}%  ({done >> 20}/{total >> 20} MB)", end="", flush=True)

            mm.download(spec, progress=_p)
            print()
            return str(mm.local_path(spec))
    return None  # let LlamaCppTranslator pick the best available GGUF


def run_cli(args):
    """Executes the translation pipeline in headless CLI mode."""
    from core.pipeline import TranslationPipeline
    from plugins.extractors.renpy import RenPyExtractor
    from plugins.generators.renpy import RenPyGenerator
    from plugins.extractors.plaintext import PlainTextExtractor
    from plugins.generators.plaintext import PlainTextGenerator
    
    source = Path(args.source)
    output = Path(args.output)
    
    if not source.exists():
        print(f"Error: Source directory '{source}' does not exist.")
        sys.exit(1)
        
    def cli_callback(progress: float, message: str) -> None:
        print(f"[{int(progress * 100):3d}%] {message}")
        
    print(f"Starting CLI Translation:")
    print(f"  Source:      {source.absolute()}")
    print(f"  Output:      {output.absolute()}")
    print(f"  Languages:   {args.src_lang} -> {args.tgt_lang}")
    print(f"  Mode:        Mode {args.mode}")
    print(f"  Backend:     {args.backend}")
    if args.style_hint:
        print(f"  Style Hint:  {args.style_hint}")
    if getattr(args, "mc_gender", None):
        print(f"  MC gender:   {'man' if args.mc_gender == 'm' else 'woman'} (declared)")
    print("-" * 50)

    pipeline = TranslationPipeline(
        source_dir=source,
        output_dir=output,
        source_lang=args.src_lang,
        target_lang=args.tgt_lang,
        mode=args.mode,
        mc_gender=getattr(args, "mc_gender", None)
    )
    
    if args.backend == "llama":
        from core.translator import LlamaCppTranslator
        from core import model_registry as reg
        model_path = _resolve_cli_model(getattr(args, "model", None))
        spec = reg.get(getattr(args, "model", None) or "")
        if spec and spec.instruct:
            print(f"  NOTE: {spec.label} is the Quality tier — needs "
                  f"{spec.vram_hint}; slower than the universal tier.")
        pipeline.translator = LlamaCppTranslator(
            model_path=model_path,
            source_lang=args.src_lang,
            target_lang=args.tgt_lang,
            n_gpu_layers=-1,
            **(spec.translator_kwargs() if spec else {})
        )
        # Markup-safety fallback: an instruct model (Quality tier) drops masked tokens
        # on markup-heavy lines; q6 (pure MT) rescues ~60% of those before we keep the
        # English original. Load it only if already present (no surprise download).
        if spec and spec.instruct:
            from core import model_manager as mm
            q6 = reg.get("q6")
            q6_path = mm.local_path(q6) if q6 else None
            if q6_path:
                print("  Markup fallback: q6 (HY-MT) loaded — rescues markup-heavy lines.")
                pipeline.fallback_translator = LlamaCppTranslator(
                    model_path=str(q6_path), source_lang=args.src_lang,
                    target_lang=args.tgt_lang, n_gpu_layers=-1,
                    **q6.translator_kwargs())
    else:
        from core.translator import MockTranslator
        pipeline.translator = MockTranslator()
        
    if any(source.rglob("*.rpy")) or any(source.rglob("*.rpyc")):
        extractor = RenPyExtractor()
        generator = RenPyGenerator()
        batch_size = 5
    else:
        extractor = PlainTextExtractor()
        generator = PlainTextGenerator()
        batch_size = 10
        
    result = pipeline.run(
        source_path=source,
        output_path=output,
        extractor=extractor,
        generator=generator,
        batch_size=batch_size,
        exclude_mods=getattr(args, "exclude_mods", False),
        full=getattr(args, "full", False)
    )
    
    print("-" * 50)
    warnings = getattr(result, "warnings", [])
    if warnings:
        print(f"Note: {len(warnings)} non-fatal warning(s):")
        for w in warnings[:20]:
            print(f" • {w}")
    if not result.errors:
        print(f"SUCCESS: Translation finished. Assets saved to: {output.absolute()}")
        sys.exit(0)
    else:
        print("FAILURE: Translation failed with errors:")
        for err in result.errors:
            print(f" - {err}")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(
        description="RenPy AutoTranslate - Automated Local Offline translation for Ren'Py games."
    )
    parser.add_argument(
        "--source", "-s",
        help="Path to the Ren'Py game folder (must contain game/ or scripts)"
    )
    parser.add_argument(
        "--output", "-o",
        help="Path to output directory for translated files"
    )
    parser.add_argument(
        "--src-lang",
        default="en",
        help="Source language code (e.g. en, es, fr)"
    )
    parser.add_argument(
        "--tgt-lang",
        default="fr",
        help="Target language code (e.g. fr, es, it)"
    )
    parser.add_argument(
        "--mode", "-m",
        choices=["A", "B"],
        default="A",
        help="Translation mode: A (Native tl/ files) or B (Fallback strings)"
    )
    parser.add_argument(
        "--backend", "-b",
        choices=["llama", "mock"],
        default="llama",
        help="Translation engine backend: llama (Llama.cpp GGUF), mock (test prefix)"
    )
    parser.add_argument(
        "--model",
        choices=["q6", "nemo"],
        default=None,
        help="Translation model: q6 (HY-MT 1.5 — fast universal MT, runs on anything) "
             "or nemo (NemoMix-Unleashed 12B — Quality tier: applies --mc-gender, "
             "register and faithful explicit content; ~18 GB RAM / 10 GB VRAM, slower)"
    )
    parser.add_argument(
        "--style-hint", "-t",
        default=None,
        help="Optional translation style hint (e.g. romantic, formal, slang)"
    )
    parser.add_argument(
        "--mc-gender",
        choices=["m", "f"],
        default=None,
        help="Declared gender of the player character (m/f). Applied by the "
             "Quality-tier instruct model (speaker agreement); a pure-MT model "
             "cannot use it"
    )
    parser.add_argument(
        "--exclude-mods",
        action="store_true",
        help="Skip player mods (cheat / walkthrough / console / gallery unlock)"
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Re-translate everything. By default, if the game already has a "
             "tl/<lang> patch (a 3rd-party one, or a previous run), only the "
             "missing lines are translated (DELTA mode, completes it safely)"
    )
    parser.add_argument(
        "--cli",
        action="store_true",
        help="Force run in headless CLI mode instead of GUI"
    )

    # If args were passed or --cli is set, run in CLI mode
    # Otherwise run the GUI
    if len(sys.argv) > 1 and (sys.argv[1] in ("-h", "--help") or any(arg in sys.argv for arg in ("-s", "--source", "--cli"))):
        args = parser.parse_args()
        if not args.source or not args.output:
            parser.error("Headless CLI mode requires both --source and --output arguments.")
        run_cli(args)
    else:
        run_gui()

if __name__ == "__main__":
    main()
