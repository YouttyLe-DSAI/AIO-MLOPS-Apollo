"""A real optimizer/scheduler restart must match uninterrupted training."""
from pathlib import Path

import pytest
import torch

from src.baseline import train as pipeline
from src.baseline.common import sha256, write_json


def test_epoch_resume_matches_uninterrupted_training(tmp_path, monkeypatch):
    torch.set_num_threads(1)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", (tmp_path / "mlruns").as_uri())
    manifest = tmp_path / "manifest.json"
    write_json(manifest, {"synthetic_fixture": True})
    monkeypatch.setattr(pipeline, "prepare", lambda c: (manifest, {"synthetic_fixture": True}))
    monkeypatch.setattr(pipeline, "build_model", lambda _: torch.nn.Sequential(torch.nn.Flatten(), torch.nn.Linear(12, 10)))
    x = torch.arange(96, dtype=torch.float32).reshape(8, 3, 2, 2) / 96
    y = torch.arange(8)

    def batches(c, m, split, epoch=0):
        return [(x[:4], y[:4]), (x[4:], y[4:])]

    monkeypatch.setattr(pipeline, "loader", batches)
    c = dict(seed=42, model="resnet18", data_root="unused", output_dir=str(tmp_path / "full"),
             epochs=2, batch_size=4, eval_batch_size=4, workers=0, threads=1, lr=0.1,
             momentum=0.9, weight_decay=0.0005, val_per_class=500, calibration_per_class=100,
             device="cpu", deterministic=True, experiment="resume-contract")
    pipeline.train(c)

    def interrupted(c, m, split, epoch=0):
        if split == "train" and epoch == 1:
            raise RuntimeError("simulated process interruption")
        return batches(c, m, split, epoch)

    resumed = {**c, "output_dir": str(tmp_path / "resumed")}
    monkeypatch.setattr(pipeline, "loader", interrupted)
    with pytest.raises(RuntimeError, match="simulated"):
        pipeline.train(resumed)
    monkeypatch.setattr(pipeline, "loader", batches)
    checkpoint = Path(resumed["output_dir"]) / "last.pt"
    config_path = checkpoint.parent / "config.yaml"
    original_config = config_path.read_bytes()
    with pytest.raises(ValueError, match="Resume config mismatch"):
        pipeline.train({**resumed, "epochs": 3}, str(checkpoint))
    assert config_path.read_bytes() == original_config
    pipeline.train(resumed, str(checkpoint))
    full = torch.load(Path(c["output_dir"]) / "last.pt", weights_only=True)
    actual = torch.load(checkpoint, weights_only=True)
    for key, value in full["model"].items():
        torch.testing.assert_close(value, actual["model"][key], rtol=0, atol=0)
    assert actual["epoch"] == full["epoch"] == 2
    assert actual["scheduler"] == full["scheduler"]
    assert actual["manifest_sha256"] == sha256(manifest)
