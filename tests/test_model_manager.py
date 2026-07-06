"""Tests for the model registry + manager (no real network)."""
import pytest

from core import model_registry as reg
from core import model_manager as mm
from core.model_registry import ModelSpec


# ---- registry ----

def test_registry_is_two_champions():
    ids = {m.id for m in reg.all_models()}
    assert ids == {"q6", "nemo"}                    # the two champions only
    assert reg.get("q6").native is True and reg.get("q6").instruct is False   # universal MT
    assert reg.get("nemo").instruct is True                                   # quality


def test_translator_kwargs_follow_the_spec():
    """The registry drives the translator mode + sampling + chat format (shared)."""
    assert reg.get("q6").translator_kwargs() == {
        "instruct": False, "temperature": 0.7, "top_p": 0.6,
        "chat_format": None}                                   # HY-MT official
    # NemoMix has no embedded template -> ChatML pinned (measured: mistral-instruct
    # drops the system role; chatml carries it -> faithful translation, no refusal)
    assert reg.get("nemo").translator_kwargs() == {
        "instruct": True, "temperature": 0.3, "top_p": 0.9,
        "chat_format": "chatml"}


def test_spec_for_filename():
    assert reg.spec_for_filename("HY-MT1.5-1.8B.Q6_K.gguf").id == "q6"
    assert reg.spec_for_filename("NemoMix-Unleashed-12B-Q4_K_M.gguf").id == "nemo"
    assert reg.spec_for_filename("nope.gguf") is None


# ---- presence detection ----

def test_local_path_requires_exact_size(tmp_path):
    spec = reg.get("q6")
    f = tmp_path / spec.filename
    f.write_bytes(b"x" * 10)                      # wrong size
    assert mm.local_path(spec, models_dir=tmp_path) is None
    # simulate correct size with truncate instead of allocating GBs
    with open(f, "wb") as fh:
        fh.truncate(spec.size)
    assert mm.local_path(spec, models_dir=tmp_path) == f
    assert mm.is_ready(spec, models_dir=tmp_path)


def test_resolve_default_and_explicit(tmp_path):
    spec = reg.get("q6")
    with open(tmp_path / spec.filename, "wb") as fh:
        fh.truncate(spec.size)
    # the universal model present -> the auto default
    assert mm.resolve_path(None, models_dir=tmp_path).name == reg.get("q6").filename
    assert mm.resolve_path("q6", models_dir=tmp_path).name == reg.get("q6").filename
    # an absent model -> None
    assert mm.resolve_path("nemo", models_dir=tmp_path) is None


def test_resolve_never_falls_back_to_quality_tier(tmp_path):
    """NemoMix is the LARGEST file on disk but must never be the silent default
    (heavy + slow + instruct): only an explicit selection gets it."""
    for mid in ("q6", "nemo"):
        spec = reg.get(mid)
        with open(tmp_path / spec.filename, "wb") as fh:
            fh.truncate(spec.size)
    assert mm.resolve_path(None, models_dir=tmp_path).name == reg.get("q6").filename
    assert mm.resolve_path("nemo", models_dir=tmp_path).name == reg.get("nemo").filename


# ---- download (mocked opener) ----

class _FakeResponse:
    def __init__(self, data, status=200):
        self._data, self._pos, self.status = data, 0, status
        self.headers = {}

    def read(self, n):
        chunk = self._data[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk

    def close(self):
        pass


def _spec(data, **kw):
    return ModelSpec(id="t", label="t", quant="x", filename="test.gguf",
                     size=len(data), vram_hint="", blurb="", native=False,
                     url="https://example/test.gguf", **kw)


def test_download_writes_and_verifies(tmp_path):
    data = b"HELLO-MODEL-BYTES" * 100
    spec = _spec(data)
    seen = []
    out = mm.download(spec, models_dir=tmp_path,
                      progress=lambda fr, d, t: seen.append((fr, d, t)),
                      _opener=lambda req, timeout=0: _FakeResponse(data),
                      chunk_size=64)
    assert out.read_bytes() == data
    assert seen and seen[-1][0] == 1.0           # progress reached 100%
    assert not (tmp_path / "test.gguf.part").exists()  # .part promoted


def test_download_size_mismatch_raises(tmp_path):
    data = b"x" * 50
    spec = _spec(data)
    spec = ModelSpec(**{**spec.__dict__, "size": 999})   # claim wrong size
    with pytest.raises(ValueError):
        mm.download(spec, models_dir=tmp_path,
                    _opener=lambda req, timeout=0: _FakeResponse(data), chunk_size=16)


def test_download_cancel(tmp_path):
    data = b"y" * 10_000
    spec = _spec(data)

    class Cancel:
        def is_set(self):
            return True   # cancel immediately

    with pytest.raises(mm.DownloadCancelled):
        mm.download(spec, models_dir=tmp_path, cancel=Cancel(),
                    _opener=lambda req, timeout=0: _FakeResponse(data), chunk_size=16)
