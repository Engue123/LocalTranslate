import sys
import os
import threading
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import filedialog
import customtkinter as ctk

from core.pipeline import TranslationPipeline
from plugins.extractors.renpy import RenPyExtractor
from plugins.generators.renpy import RenPyGenerator
from plugins.extractors.plaintext import PlainTextExtractor
from plugins.generators.plaintext import PlainTextGenerator
from core import model_registry, model_manager, profiles
from core.languages import language_note

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except Exception:
    DND_AVAILABLE = False

PROJECT_TYPES = {
    "RenPy Game": (RenPyExtractor, RenPyGenerator),
    "Plain Text / Markdown": (PlainTextExtractor, PlainTextGenerator),
}

LANGUAGES = [
    ("English", "en"),
    ("French", "fr"),
    ("Spanish", "es"),
    ("Italian", "it"),
    ("German", "de"),
    ("Portuguese", "pt"),
    ("Russian", "ru"),
    ("Japanese", "ja"),
    ("Chinese", "zh"),
    ("Korean", "ko"),
]

# UI label -> engine value for the declared MC (player character) gender.
# None = unspecified (safe default: no gender directive at all).
MC_GENDER_CHOICES = {"Unspecified": None, "Man": "m", "Woman": "f"}

ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")


class Tooltip:
    """Lightweight hover tooltip for a widget (the ⓘ info bubble)."""

    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, _event=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            self.tip, text=self.text, justify="left", wraplength=360,
            background="#1f1f1f", foreground="#eaeaea", relief="solid", borderwidth=1,
            font=("", 11), padx=10, pady=8,
        )
        label.pack()

    def _hide(self, _event=None):
        if self.tip:
            self.tip.destroy()
            self.tip = None


class RenPyAutoTranslateApp(ctk.CTk):
    def __init__(self):
        global DND_AVAILABLE
        super().__init__()

        self.title("LocalTranslate")
        self.geometry("770x765")
        self.minsize(670, 620)
        self.cancel_event = None
        self.model_download_thread = None

        if DND_AVAILABLE:
            try:
                TkinterDnD._check(self)
            except Exception:
                pass

        # Grid layout configuration
        self.grid_rowconfigure(5, weight=1)  # Log console row expands
        self.grid_columnconfigure(0, weight=1)

        # --- 1. Header Frame ---
        self.header_frame = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.header_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=(15, 10))

        self.header_title = ctk.CTkLabel(
            self.header_frame,
            text="LocalTranslate",
            font=ctk.CTkFont(size=24, weight="bold")
        )
        self.header_title.pack(anchor="w")

        self.header_sub = ctk.CTkLabel(
            self.header_frame,
            text="Local, offline machine translation for visual novels.",
            font=ctk.CTkFont(size=12, slant="italic")
        )
        self.header_sub.pack(anchor="w")

        # --- 2. Directory Selection Frame ---
        self.dir_frame = ctk.CTkFrame(self)
        self.dir_frame.grid(row=1, column=0, sticky="ew", padx=20, pady=10)
        self.dir_frame.grid_columnconfigure(1, weight=1)

        self.src_label = ctk.CTkLabel(
            self.dir_frame,
            text="Source Game Directory",
            font=ctk.CTkFont(weight="bold")
        )
        self.src_label.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="w")

        self.src_entry = ctk.CTkEntry(
            self.dir_frame,
            placeholder_text="Path to game folder (must contain 'game' or scripts)"
        )
        self.src_entry.grid(row=0, column=1, padx=10, pady=(10, 5), sticky="ew")

        self.src_btn = ctk.CTkButton(
            self.dir_frame, text="Browse", width=90, command=self.browse_source
        )
        self.src_btn.grid(row=0, column=2, padx=10, pady=(10, 5))

        self.out_label = ctk.CTkLabel(
            self.dir_frame,
            text="Output Directory",
            font=ctk.CTkFont(weight="bold")
        )
        self.out_label.grid(row=1, column=0, padx=10, pady=(5, 10), sticky="w")

        self.out_entry = ctk.CTkEntry(
            self.dir_frame,
            placeholder_text="Path where translated files will be generated"
        )
        self.out_entry.grid(row=1, column=1, padx=10, pady=(5, 10), sticky="ew")

        self.out_btn = ctk.CTkButton(
            self.dir_frame, text="Browse", width=90, command=self.browse_output
        )
        self.out_btn.grid(row=1, column=2, padx=10, pady=(5, 10))

        # --- Drop Zone ---
        self.drop_zone = ctk.CTkFrame(self, border_width=2, border_color="#3498db", fg_color="transparent")
        self.drop_zone.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 10))
        self.drop_label = ctk.CTkLabel(self.drop_zone, text="Drop folder here or click Browse", font=ctk.CTkFont(size=12))
        self.drop_label.pack(padx=20, pady=10)

        # Drag & drop: bind on the root window
        if DND_AVAILABLE:
            try:
                self.drop_zone._root_window.register_drop_target("*")
                self.drop_zone._root_window.bind("<<Drop>>", self.on_drop)
            except Exception:
                DND_AVAILABLE = False
                self.drop_label.configure(text="Browse or paste path above — drag & drop unavailable")
        else:
            self.drop_label.configure(text="Browse or paste path above — drag & drop unavailable")

        # --- 3. Settings Frame ---
        self.settings_frame = ctk.CTkFrame(self)
        self.settings_frame.grid(row=3, column=0, sticky="ew", padx=20, pady=10)
        self.settings_frame.grid_columnconfigure(0, weight=1)
        self.settings_frame.grid_columnconfigure(1, weight=1)
        self.settings_frame.grid_columnconfigure(2, weight=1)

        # Project Type Selection
        self.proj_frame = ctk.CTkFrame(self.settings_frame, fg_color="transparent")
        self.proj_frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")

        self.proj_label = ctk.CTkLabel(
            self.proj_frame, text="Project Type", font=ctk.CTkFont(size=12)
        )
        self.proj_label.pack(anchor="w", padx=5)

        self.proj_var = ctk.StringVar(value="RenPy Game")
        self.proj_menu = ctk.CTkComboBox(
            self.proj_frame,
            values=list(PROJECT_TYPES.keys()),
            variable=self.proj_var
        )
        self.proj_menu.pack(fill="x", padx=5, pady=5)

        # Target Language
        self.lang_frame = ctk.CTkFrame(self.settings_frame, fg_color="transparent")
        self.lang_frame.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")

        self.tgt_lang_label = ctk.CTkLabel(
            self.lang_frame, text="Target Language", font=ctk.CTkFont(size=12)
        )
        self.tgt_lang_label.pack(anchor="w", padx=5)

        self.tgt_lang_var = ctk.StringVar(value="French")
        self.tgt_lang_menu = ctk.CTkComboBox(
            self.lang_frame,
            values=[l[0] for l in LANGUAGES],
            variable=self.tgt_lang_var,
            command=self._on_lang_change,
        )
        self.tgt_lang_menu.pack(fill="x", padx=5, pady=5)

        # Honest per-language note: fonts are ALWAYS adapted (any script displays);
        # the translation-quality expectation depends on the model (quality tiers).
        self.lang_info = ctk.CTkLabel(
            self.lang_frame, text="", anchor="w", justify="left", wraplength=232,
            font=ctk.CTkFont(size=10, slant="italic"), text_color="gray")
        self.lang_info.pack(anchor="w", padx=5, pady=(0, 2))

        # Declared MC gender (the hero is renamed at runtime, so nothing can be
        # inferred from the game text — the player knows who they play).
        mc_row = ctk.CTkFrame(self.lang_frame, fg_color="transparent")
        mc_row.pack(anchor="w", padx=5, pady=(6, 0))
        self.mc_gender_label = ctk.CTkLabel(
            mc_row, text="Main Character (hero)", font=ctk.CTkFont(size=12)
        )
        self.mc_gender_label.pack(side="left")
        mc_info = ctk.CTkLabel(mc_row, text=" ⓘ", text_color="#3498db",
                               font=ctk.CTkFont(size=13), cursor="hand2")
        mc_info.pack(side="left")
        # NB: CTkSegmentedButton does not support .bind() — hover help lives on ⓘ.
        Tooltip(mc_info,
                "Gender of the playable character — fixes grammatical agreement "
                "in the hero's lines (\"Je suis prêt\" vs \"Je suis prête\").\n"
                "Used by the Quality (instruct) model; the default fast model "
                "is a pure translator and cannot apply it.")
        self.mc_gender_var = ctk.StringVar(value="Unspecified")
        self.mc_gender_seg = ctk.CTkSegmentedButton(
            self.lang_frame,
            values=list(MC_GENDER_CHOICES),
            variable=self.mc_gender_var,
        )
        self.mc_gender_seg.pack(fill="x", padx=5, pady=5)

        # Backend Selection
        self.backend_frame = ctk.CTkFrame(self.settings_frame, fg_color="transparent")
        self.backend_frame.grid(row=0, column=2, padx=10, pady=10, sticky="nsew")

        self.backend_label = ctk.CTkLabel(
            self.backend_frame, text="Translation Backend", font=ctk.CTkFont(size=12)
        )
        self.backend_label.pack(anchor="w", padx=5)

        self.backend_var = ctk.StringVar(value="Llama.cpp (Local GGUF)")
        self.backend_menu = ctk.CTkComboBox(
            self.backend_frame,
            values=["Llama.cpp (Local GGUF)", "Mock (Testing)"],
            variable=self.backend_var
        )
        self.backend_menu.pack(fill="x", padx=5, pady=5)

        # --- 3b. Translation Model Frame ---
        self._build_model_frame(row=4)

        # --- 4. Log Console Frame ---
        self.console_frame = ctk.CTkFrame(self)
        self.console_frame.grid(row=5, column=0, sticky="nsew", padx=20, pady=5)
        self.console_frame.grid_rowconfigure(0, weight=1)
        self.console_frame.grid_columnconfigure(0, weight=1)

        self.console_text = ctk.CTkTextbox(
            self.console_frame,
            font=ctk.CTkFont(family="Courier", size=11)
        )
        self.console_text.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.console_text.configure(state="disabled")

        # --- 5. Progress Bar ---
        self.progress_bar = ctk.CTkProgressBar(self, mode="determinate")
        self.progress_bar.grid(row=6, column=0, sticky="ew", padx=20, pady=(5, 0))
        self.progress_bar.set(0.0)

        # --- 6. Action Control Frame (options row above the action buttons) ---
        self.control_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.control_frame.grid(row=7, column=0, sticky="ew", padx=20, pady=15)

        # Translation options (own row so the long labels don't crowd the buttons).
        self.options_bar = ctk.CTkFrame(self.control_frame, fg_color="transparent")
        self.options_bar.pack(fill="x", pady=(0, 8))

        # Exclude player mods (cheat/walkthrough/console). Off by default = translate them.
        self.exclude_mods_var = ctk.IntVar(value=0)
        self.exclude_mods_chk = ctk.CTkCheckBox(
            self.options_bar, text="Exclude mods (cheat/walkthrough)",
            variable=self.exclude_mods_var
        )
        self.exclude_mods_chk.pack(side="left", padx=(5, 20))

        # Full re-translate. Off by default = DELTA (complete an existing tl/<lang>
        # patch, translating only the gap). On = re-translate everything.
        self.full_var = ctk.IntVar(value=0)
        self.full_chk = ctk.CTkCheckBox(
            self.options_bar, text="Re-translate everything (ignore existing patch)",
            variable=self.full_var
        )
        self.full_chk.pack(side="left")
        Tooltip(self.full_chk,
                "By default, if the game already has a tl/<lang> translation (a "
                "3rd-party patch, or a previous run), only the MISSING lines are "
                "translated and added — completing it safely. Check this to "
                "re-translate the whole game instead.")

        # Action buttons.
        self.buttons_bar = ctk.CTkFrame(self.control_frame, fg_color="transparent")
        self.buttons_bar.pack(fill="x")

        self.run_btn = ctk.CTkButton(
            self.buttons_bar,
            text="▶  Run Translation",
            command=self.start_pipeline_thread,
            font=ctk.CTkFont(size=14, weight="bold"),
            height=40
        )
        self.run_btn.pack(side="left", padx=5)

        self.dryrun_btn = ctk.CTkButton(
            self.buttons_bar,
            text="Analyze (audit)",
            command=self.start_dryrun_thread,
            fg_color="#555555",
            height=40
        )
        self.dryrun_btn.pack(side="left", padx=5)

        self.cancel_btn = ctk.CTkButton(
            self.buttons_bar,
            text="Cancel",
            command=self.cancel_run,
            state="disabled",
            fg_color="#c0392b",
            height=40
        )
        self.cancel_btn.pack(side="left", padx=5)

        # --- Ko-fi support footer (a desktop button that opens the browser; the
        # markdown badge / JS widget only work on the web, so they live in the README) ---
        self.kofi_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.kofi_frame.grid(row=8, column=0, sticky="ew", padx=20, pady=(0, 12))
        ctk.CTkLabel(
            self.kofi_frame,
            text="If LocalTranslate saved you time, a coffee is always appreciated ;-)",
            font=ctk.CTkFont(size=11, slant="italic"), text_color="gray",
        ).pack(side="left", padx=(5, 12))
        ctk.CTkButton(
            self.kofi_frame, text="☕  Support on Ko-fi", width=175, height=32,
            fg_color="#FF5E5B", hover_color="#E24A47",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=lambda: webbrowser.open("https://ko-fi.com/G3D422PBO3"),
        ).pack(side="left")

        self._on_lang_change()          # set the initial per-language note (French)

    def _on_lang_change(self, _choice=None) -> None:
        """Refresh the honest per-language note under the selector."""
        code = next((c for name, c in LANGUAGES if name == self.tgt_lang_var.get()), "fr")
        self.lang_info.configure(text=language_note(code))

    def log(self, message: str) -> None:
        """Appends a line to the GUI console text box in a thread-safe manner."""
        def append():
            self.console_text.configure(state="normal")
            self.console_text.insert(tk.END, f"{message}\n")
            self.console_text.see(tk.END)
            self.console_text.configure(state="disabled")
        self.after(0, append)

    def browse_source(self) -> None:
        path = filedialog.askdirectory(title="Select Source Game Directory")
        if path:
            self.src_entry.delete(0, tk.END)
            self.src_entry.insert(0, path)

    def browse_output(self) -> None:
        path = filedialog.askdirectory(title="Select Output Directory")
        if path:
            self.out_entry.delete(0, tk.END)
            self.out_entry.insert(0, path)

    def on_drop(self, event) -> None:
        data = event.data.strip("{}")
        paths = data.split("} {")
        for p in paths:
            p = p.strip().strip("{}")
            if os.path.isdir(p):
                self.src_entry.delete(0, tk.END)
                self.src_entry.insert(0, p)
                break

    def start_pipeline_thread(self) -> None:
        # Read paths directly from widgets at click time (Non-negotiable invariant 14)
        source_path = self.src_entry.get().strip()
        output_path = self.out_entry.get().strip()

        if not source_path or not os.path.exists(source_path):
            self.log("❌ Error: Source directory is empty or does not exist.")
            return
        if not output_path:
            self.log("❌ Error: Output directory is not specified.")
            return

        self.run_btn.configure(state="disabled")
        self.src_btn.configure(state="disabled")
        self.out_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")

        self.cancel_event = threading.Event()

        threading.Thread(
            target=self.run_pipeline,
            args=(source_path, output_path),
            daemon=True
        ).start()

    def run_pipeline(self, source_path: str, output_path: str) -> None:
        try:
            tgt_name = self.tgt_lang_var.get()
            tgt_lang = next((l[1] for l in LANGUAGES if l[0] == tgt_name), "fr")

            backend_choice = self.backend_var.get()
            translator_type = "llama" if "Llama" in backend_choice else "mock"

            proj_choice = self.proj_var.get()
            extractor_cls, generator_cls = PROJECT_TYPES[proj_choice]
            extractor = extractor_cls()
            generator = generator_cls()
            batch_size = 5 if proj_choice == "RenPy Game" else 10

            def progress_callback(progress: float, message: str) -> None:
                self.log(f"[{int(progress*100):3d}%] {message}")
                clamped = max(0.0, min(1.0, progress))
                self.after(0, lambda p=clamped: self.progress_bar.set(p))

            self.after(0, lambda: self.progress_bar.set(0.0))
            self.log("Starting translation...")
            self.log(f"Source: {source_path}")
            self.log(f"Output: {output_path}")

            mc_gender = MC_GENDER_CHOICES.get(self.mc_gender_var.get())
            if mc_gender:
                self.log(f"MC gender: {self.mc_gender_var.get()} (declared)")

            pipeline = TranslationPipeline(
                source_dir=Path(source_path),
                output_dir=Path(output_path),
                source_lang="en",
                target_lang=tgt_lang,
                translator=None,  # Engine will init or we pass mock/llama
                mc_gender=mc_gender,
            )

            # Manually initialize translator to match pipeline interface
            if translator_type == "llama":
                spec = self._selected_spec()
                model_path = model_manager.local_path(spec)
                if model_path is None:
                    self.log(f"❌ Model '{spec.label}' isn't downloaded yet. "
                             f"Click 'Download' in the Model section.")
                    return
                self.log(f"Model: {spec.label}")
                if spec.instruct:
                    self.log(f"Quality tier: instruct mode on — needs {spec.vram_hint}; "
                             "slower than the universal tier.")
                from core.translator import LlamaCppTranslator
                pipeline.translator = LlamaCppTranslator(
                    model_path=str(model_path),
                    source_lang="en",
                    target_lang=tgt_lang,
                    n_gpu_layers=-1,
                    **spec.translator_kwargs()
                )
                # Markup-safety fallback: the Quality (instruct) model drops masked
                # tokens on markup-heavy lines; q6 (pure MT) rescues ~60% of those
                # before we keep the English original. Load it if already present.
                if spec.instruct:
                    q6 = model_registry.get("q6")
                    q6_path = model_manager.local_path(q6) if q6 else None
                    if q6_path:
                        self.log("Markup fallback: q6 (HY-MT) loaded — rescues "
                                 "markup-heavy lines NemoMix can't keep safe.")
                        pipeline.fallback_translator = LlamaCppTranslator(
                            model_path=str(q6_path), source_lang="en",
                            target_lang=tgt_lang, n_gpu_layers=-1,
                            **q6.translator_kwargs())
            else:
                from core.translator import MockTranslator
                pipeline.translator = MockTranslator()

            result = pipeline.run(
                source_path=Path(source_path),
                output_path=Path(output_path),
                extractor=extractor,
                generator=generator,
                batch_size=batch_size,
                progress_callback=progress_callback,
                cancel_event=self.cancel_event,
                exclude_mods=bool(self.exclude_mods_var.get()),
                full=bool(self.full_var.get()),
            )

            warnings = getattr(result, "warnings", [])
            if warnings:
                self.log(f"Note: {len(warnings)} non-fatal warning(s) (patch still valid):")
                for w in warnings[:20]:
                    self.log(f" • {w}")
            if not result.errors:
                self.after(0, lambda: self.progress_bar.set(1.0))
                self.log("SUCCESS: Translation completed.")
            else:
                self.log("FAILURE: Errors occurred during translation.")
                for err in result.errors:
                    self.log(f" - {err}")
        except Exception as e:
            self.log(f"❌ Critical error: {e}")
        finally:
            self.after(0, lambda: self.run_btn.configure(state="normal"))
            self.after(0, lambda: self.src_btn.configure(state="normal"))
            self.after(0, lambda: self.out_btn.configure(state="normal"))
            self.after(0, lambda: self.cancel_btn.configure(state="disabled"))

    def cancel_run(self) -> None:
        if self.cancel_event:
            self.cancel_event.set()
            self.log("Cancellation requested...")
            self.cancel_btn.configure(state="disabled")

    # ------------------------------------------------------------------ models
    def _build_model_frame(self, row: int) -> None:
        self.model_frame = ctk.CTkFrame(self)
        self.model_frame.grid(row=row, column=0, sticky="ew", padx=20, pady=(0, 5))
        self.model_frame.grid_columnconfigure(1, weight=1)

        title = ctk.CTkLabel(self.model_frame, text="Translation Model",
                             font=ctk.CTkFont(weight="bold"))
        title.grid(row=0, column=0, padx=(12, 4), pady=(10, 2), sticky="w")

        info = ctk.CTkLabel(self.model_frame, text="ⓘ", text_color="#3498db",
                            font=ctk.CTkFont(size=15), cursor="hand2")
        info.grid(row=0, column=1, padx=0, pady=(10, 2), sticky="w")
        Tooltip(info,
                "Two models — both run 100% offline:\n\n"
                "• HY-MT 1.5 Q6 (Universal) — fast, light (~1.4 GB), runs on "
                "anything. Pure machine translation: good on prose, but no gender "
                "or tu/vous control.\n\n"
                "• NemoMix-Unleashed 12B (Quality) — applies the hero's declared "
                "gender, the casual register (tu), and faithful explicit content. "
                "Much larger (~7 GB), needs ~18 GB RAM / 10 GB VRAM and is slower "
                "(~3× the time). Opt-in.\n\n"
                "Non-bundled models download once from Hugging Face.")

        self._model_labels = {m.label: m for m in model_registry.all_models()}
        saved_id = profiles.get_last_settings().get("model_id", "q6")
        default_spec = model_registry.get(saved_id) or model_registry.get("q6")
        self.model_var = ctk.StringVar(value=default_spec.label)
        self.model_menu = ctk.CTkComboBox(
            self.model_frame, values=list(self._model_labels.keys()),
            variable=self.model_var, command=self._on_model_change, width=210, state="readonly")
        self.model_menu.grid(row=1, column=0, padx=(12, 8), pady=(0, 6), sticky="w")

        self.model_status = ctk.CTkLabel(self.model_frame, text="", font=ctk.CTkFont(size=12))
        self.model_status.grid(row=1, column=1, padx=4, pady=(0, 6), sticky="w")

        self.model_dl_btn = ctk.CTkButton(self.model_frame, text="Download",
                                          width=130, command=self._start_model_download)
        self.model_dl_btn.grid(row=1, column=2, padx=8, pady=(0, 6))

        self.model_info = ctk.CTkLabel(self.model_frame, text="", anchor="w",
                                       font=ctk.CTkFont(size=11, slant="italic"),
                                       text_color="gray")
        self.model_info.grid(row=2, column=0, columnspan=3, padx=12, pady=(0, 8), sticky="w")

        self.model_dl_progress = ctk.CTkProgressBar(self.model_frame, mode="determinate")
        self.model_dl_progress.set(0.0)  # gridded only while downloading

        self._refresh_model_ui()

    def _selected_spec(self):
        return self._model_labels.get(self.model_var.get()) or model_registry.get("q6")

    def _on_model_change(self, _choice=None) -> None:
        self._refresh_model_ui()
        settings = profiles.get_last_settings()
        settings["model_id"] = self._selected_spec().id
        profiles.save_last_settings(settings)

    def _refresh_model_ui(self) -> None:
        spec = self._selected_spec()
        # The Quality tier (instruct) is heavy — make its hardware needs loud.
        warn = "⚠ " if spec.instruct else ""
        self.model_info.configure(
            text=f"{warn}{spec.quant} · {spec.size_gb:.2f} GB · {spec.vram_hint} — {spec.blurb}",
            text_color="#e67e22" if spec.instruct else "gray")
        # Present on disk -> ready (bundled or downloaded); else offer the download.
        # (A "bundled" model still falls back to a download if its file is missing.)
        if model_manager.is_ready(spec):
            self.model_status.configure(
                text="✓ Bundled with app" if spec.native else "✓ Ready (downloaded)",
                text_color="#2ecc71")
            self.model_dl_btn.grid_remove()
        elif spec.url:
            self.model_status.configure(
                text=f"⬇ Not downloaded ({spec.size_gb:.2f} GB)", text_color="#e67e22")
            self.model_dl_btn.configure(text=f"Download {spec.size_gb:.1f} GB", state="normal")
            self.model_dl_btn.grid()
        else:
            self.model_status.configure(text="⚠ Missing (no download source)",
                                        text_color="#c0392b")
            self.model_dl_btn.grid_remove()

    def _start_model_download(self) -> None:
        spec = self._selected_spec()
        if model_manager.is_ready(spec):
            self._refresh_model_ui()
            return
        self.model_dl_btn.configure(state="disabled")
        self.model_menu.configure(state="disabled")
        self.run_btn.configure(state="disabled")
        self.model_dl_progress.grid(row=3, column=0, columnspan=3, sticky="ew",
                                    padx=12, pady=(0, 10))
        self.model_dl_progress.set(0.0)
        self.model_download_thread = threading.Thread(
            target=self._run_model_download, args=(spec,), daemon=True)
        self.model_download_thread.start()

    def _run_model_download(self, spec) -> None:
        def prog(fraction, done, total):
            self.after(0, lambda f=fraction: self.model_dl_progress.set(f))
            self.after(0, lambda d=done, t=total, f=fraction: self.model_status.configure(
                text=f"⬇ {d >> 20}/{t >> 20} MB ({f*100:.0f}%)", text_color="#e67e22"))
        try:
            self.log(f"Downloading {spec.label} ({spec.size_gb:.2f} GB) — one-time, then offline.")
            model_manager.download(spec, progress=prog)
            self.log(f"✓ Model {spec.label} downloaded and ready.")
        except Exception as e:
            self.log(f"❌ Download failed: {e}")

        def finish():
            self.model_dl_progress.grid_remove()
            self.model_menu.configure(state="readonly")
            self.run_btn.configure(state="normal")
            self._refresh_model_ui()
        self.after(0, finish)

    def start_dryrun_thread(self) -> None:
        source_path = self.src_entry.get().strip()
        output_path = self.out_entry.get().strip()
        if not source_path or not os.path.exists(source_path):
            self.log("❌ Error: Source directory is empty or does not exist.")
            return
        if not output_path:
            self.log("❌ Error: Output directory is not specified.")
            return
        self.run_btn.configure(state="disabled")
        self.dryrun_btn.configure(state="disabled")
        threading.Thread(
            target=self.run_dryrun, args=(source_path, output_path), daemon=True
        ).start()

    def run_dryrun(self, source_path: str, output_path: str) -> None:
        """Read-only audit: no LLM. RenPy -> structure report; text -> unit dry-run."""
        try:
            out_dir = Path(output_path)
            out_dir.mkdir(parents=True, exist_ok=True)
            proj_choice = self.proj_var.get()

            if proj_choice == "RenPy Game":
                from core.renpy_ast import analyze_game
                self.log("Analyzing game structure (read-only, no translation)…")
                report = analyze_game(Path(source_path))
                for line in report.summary_lines():
                    self.log("  " + line)
                path = report.write(out_dir)
                self.log(f"Structure report written to {path}")
            else:
                from core.dryrun import DryRunReport
                extractor_cls, _ = PROJECT_TYPES[proj_choice]
                self.log("Dry Run: extracting translatable units (no translation)…")
                units = extractor_cls().extract(Path(source_path))
                path = DryRunReport(units).write(out_dir)
                self.log(f"Dry Run: {len(units)} unit(s) found — report written to {path}")
        except Exception as e:
            self.log(f"❌ Audit error: {e}")
        finally:
            self.after(0, lambda: self.run_btn.configure(state="normal"))
            self.after(0, lambda: self.dryrun_btn.configure(state="normal"))


if __name__ == "__main__":
    app = RenPyAutoTranslateApp()
    app.mainloop()
