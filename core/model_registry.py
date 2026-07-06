"""
Registry of selectable translation models — the two tiers:

- Universal tier: HY-MT 1.5 (pure machine translation). Fast and light; runs on
  almost anything. Ships with the app.
- Quality tier (opt-in): NemoMix-Unleashed 12B — an instruction model that follows
  the system prompt (speaker gender, register, faithful adult content). Heavier and
  slower; never auto-selected.

The registry drives the translator per model: `instruct` switches the prompt mode,
and `temperature`/`top_p` carry the per-model sampling.
"""
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

_HF_BASE = "https://huggingface.co/mradermacher/HY-MT1.5-1.8B-GGUF/resolve/main"


@dataclass(frozen=True)
class ModelSpec:
    id: str               # short id: "q6" (universal) | "nemo" (quality)
    label: str            # UI label
    quant: str            # "Q4_K_M"
    filename: str         # canonical on-disk filename
    size: int             # exact bytes (for the UI + download verification)
    vram_hint: str        # human-readable resource hint
    blurb: str            # one-line quality/trade-off description
    native: bool          # ships with the app (no download)
    url: Optional[str] = None          # download URL (None for native-only)
    aliases: Tuple[str, ...] = field(default_factory=tuple)  # other accepted filenames
    instruct: bool = False             # instruction model (Quality tier)
    temperature: float = 0.7           # per-model sampling
    top_p: float = 0.6
    chat_format: Optional[str] = None  # llama-cpp chat format when GGUF has none

    @property
    def size_gb(self) -> float:
        return self.size / (1024 ** 3)

    @property
    def filenames(self) -> Tuple[str, ...]:
        return (self.filename, *self.aliases)

    def translator_kwargs(self) -> dict:
        """LlamaCppTranslator kwargs for this model — the registry drives the
        prompt mode, sampling and chat format (shared by the GUI and the CLI)."""
        return {"instruct": self.instruct, "temperature": self.temperature,
                "top_p": self.top_p, "chat_format": self.chat_format}


# Two tiers: a fast universal model (HY-MT 1.5 Q6) and the Quality model
# (NemoMix-12B). The universal model ships with the app; both can be downloaded.
REGISTRY: List[ModelSpec] = [
    ModelSpec(
        id="q6", label="HY-MT 1.5 Q6 · Universal (fast)", quant="Q6_K",
        filename="HY-MT1.5-1.8B.Q6_K.gguf",
        size=1_474_786_048, vram_hint="≈ 2.5 GB RAM/VRAM",
        blurb="Fast, runs on anything. Pure machine translation — good on prose, "
              "but no gender/register control (use the Quality model for that).",
        native=True,
        url=f"{_HF_BASE}/HY-MT1.5-1.8B.Q6_K.gguf",
    ),
    ModelSpec(
        id="nemo", label="NemoMix-Unleashed 12B · Quality tier", quant="Q4_K_M",
        filename="NemoMix-Unleashed-12B-Q4_K_M.gguf",
        size=7_477_203_776,  # verified on disk
        vram_hint="≈ 18 GB unified RAM (Apple Silicon) / 10 GB VRAM",
        blurb="Recommended Quality model: faithful crude vocabulary, correct gender "
              "and tu/vous, no person-flip. Mistral-Nemo 12B, uncensored. Heaviest "
              "and slowest; opt-in.",
        native=False,
        url="https://huggingface.co/bartowski/NemoMix-Unleashed-12B-GGUF/"
            "resolve/main/NemoMix-Unleashed-12B-Q4_K_M.gguf",
        # No embedded chat template (mergekit Base). llama-cpp's "mistral-instruct"
        # drops the system role, which makes the model chat or refuse; "chatml" carries
        # the system prompt, giving faithful translation, correct gender and crude
        # register with no refusal. Low temperature (this model degrades when hot).
        instruct=True, temperature=0.3, top_p=0.9, chat_format="chatml",
    ),
]

_BY_ID = {m.id: m for m in REGISTRY}


def get(model_id: str) -> Optional[ModelSpec]:
    return _BY_ID.get(model_id)


def all_models() -> List[ModelSpec]:
    return list(REGISTRY)


def spec_for_filename(name: str) -> Optional[ModelSpec]:
    for m in REGISTRY:
        if name in m.filenames:
            return m
    return None
