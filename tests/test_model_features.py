"""Unit tests for GGUF parsing, HF download utilities, and model library."""
from pathlib import Path
from src import gguf, hf_download, model_library
from src.profiles_schema import ModelProfile


def test_hf_download_normalize_repo():
    assert hf_download.normalize_repo("unsloth/Qwen3-8B-GGUF") == "unsloth/Qwen3-8B-GGUF"
    assert hf_download.normalize_repo("https://huggingface.co/unsloth/Qwen3-8B-GGUF") == "unsloth/Qwen3-8B-GGUF"


def test_gguf_vram_estimation():
    est = gguf.VramEstimate(
        weights_mib=4000,
        mmproj_mib=500,
        kv_mib=1000,
        overhead_mib=1152,
    )
    assert est.total_mib == 6652
    assert est.complete is True


def test_fit_verdicts():
    est = gguf.VramEstimate(weights_mib=4000, mmproj_mib=0, kv_mib=1000, overhead_mib=1152)

    class MockDev:
        def __init__(self, free, total):
            self.vram_free_mib = free
            self.vram_total_mib = total

    devs = [MockDev(10000, 12000)]
    res = gguf.fit_verdict(est, devs)
    assert res.verdict == gguf.FITS
    assert res.need_mib == 6152

    devs_small = [MockDev(1000, 4000)]
    res_small = gguf.fit_verdict(est, devs_small)
    assert res_small.verdict == gguf.NO_FIT


def test_model_library_slugify_and_helpers():
    assert model_library.slugify("Qwen 2.5 7B") == "qwen-2.5-7b"
    assert model_library.is_mmproj_name("qwen-mmproj-f16.gguf") is True
    assert model_library.is_mmproj_name("model.gguf") is False
    assert model_library.is_secondary_split("model-00002-of-00003.gguf") is True
    assert model_library.is_secondary_split("model-00001-of-00003.gguf") is False


def test_dialog_signatures():
    import inspect
    from src.ui.pages.model_dialogs import ScanDialog, HfDialog, MoveDialog, ModelDialog, ChatDialog

    # Verify ScanDialog accepts both on_add and on_saved
    sig = inspect.signature(ScanDialog.__init__)
    assert 'on_add' in sig.parameters
    assert 'on_saved' in sig.parameters

    # Verify HfDialog accepts both on_add and on_saved
    sig = inspect.signature(HfDialog.__init__)
    assert 'on_add' in sig.parameters
    assert 'on_saved' in sig.parameters

    # Verify MoveDialog accepts on_done with default
    sig = inspect.signature(MoveDialog.__init__)
    assert 'on_done' in sig.parameters
    assert sig.parameters['on_done'].default is None
