import numpy as np
import pytest
import torch
from PIL import Image

from src.baseline.common import MEAN, STD, build_model, preprocessing, split_indices
from src.baseline.common import configure_cuda_memory


def test_split_is_stratified_reproducible_and_leak_free():
    labels = np.repeat(np.arange(10), 5000)
    split = split_indices(labels, 42, 500, 100)
    assert split == split_indices(labels, 42, 500, 100)
    assert split != split_indices(labels, 43, 500, 100)
    train, val, cal = (set(split[k]) for k in ("train", "validation", "calibration"))
    assert len(train) == 45000 and len(val) == 5000 and len(cal) == 1000
    assert not train & val and cal <= train and not cal & val
    assert train | val == set(range(50000))
    assert np.bincount(labels[split["validation"]]).tolist() == [500] * 10
    assert np.bincount(labels[split["calibration"]]).tolist() == [100] * 10


def test_invalid_split_fails():
    with pytest.raises(ValueError):
        split_indices(np.repeat(np.arange(10), 100), 42, 100, 1)


def test_inference_contract_normalizes_rgb_once():
    image = Image.fromarray(np.full((32, 32, 3), 255, dtype=np.uint8))
    x = preprocessing()(image)
    assert x.dtype == torch.float32 and x.shape == (3, 32, 32)
    expected = (1 - torch.tensor(MEAN)) / torch.tensor(STD)
    torch.testing.assert_close(x[:, 0, 0], expected)


def test_cifar_stem_and_classifier_support_variable_batches():
    torch.set_num_threads(2)
    m = build_model().eval()
    assert m.conv1.kernel_size == (3, 3) and m.conv1.stride == (1, 1)
    with torch.inference_mode():
        for size in (1, 3):
            assert m(torch.zeros(size, 3, 32, 32)).shape == (size, 10)


@pytest.mark.parametrize("capacity_gib", [24, 80])
def test_gpu_memory_budget_is_fixed_across_execution_gpus(monkeypatch, capacity_gib):
    from types import SimpleNamespace
    calls = []
    monkeypatch.setattr(torch.cuda, "current_device", lambda: 0)
    monkeypatch.setattr(torch.cuda, "get_device_properties", lambda _: SimpleNamespace(total_memory=capacity_gib * 1024 ** 3))
    monkeypatch.setattr(torch.cuda, "set_per_process_memory_fraction", lambda fraction, device: calls.append((fraction, device)))
    result = configure_cuda_memory({"device": "cuda", "target_vram_gib": 24, "cuda_memory_budget_gib": 16})
    assert result["cuda_allocator_limit_bytes"] == 16 * 1024 ** 3
    assert calls == [(16 / capacity_gib, 0)]


def test_gpu_memory_budget_rejects_oversized_target(monkeypatch):
    with pytest.raises(ValueError, match="Invalid CUDA memory budget"):
        monkeypatch.setattr(torch.cuda, "current_device", lambda: 0)
        from types import SimpleNamespace
        monkeypatch.setattr(torch.cuda, "get_device_properties", lambda _: SimpleNamespace(total_memory=80 * 1024 ** 3))
        configure_cuda_memory({"device": "cuda", "target_vram_gib": 24, "cuda_memory_budget_gib": 32})
