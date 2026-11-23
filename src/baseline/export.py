"""Export best checkpoint and evaluate ONNX on the complete official test set."""
import argparse
import json
from pathlib import Path

import mlflow
import numpy as np
import onnx
import onnxruntime as ort
import torch

from .common import build_model, loader, sha256, write_json, CLASSES, MEAN, STD


def export(checkpoint, repository):
    checkpoint = Path(checkpoint).resolve()
    out = checkpoint.parent
    state = torch.load(checkpoint, map_location="cpu", weights_only=True)
    c = state["config"]
    torch.set_num_threads(c["threads"])
    model = build_model(c["model"]).eval()
    model.load_state_dict(state["model"])
    name = f"{c['model']}_cifar10_fp32"
    model_dir = Path(repository) / name
    version = model_dir / "1"
    version.mkdir(parents=True, exist_ok=True)
    target = version / "model.onnx"
    torch.onnx.export(model, torch.zeros(1, 3, 32, 32), str(target), opset_version=17,
                      input_names=["input"], output_names=["logits"],
                      dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}})
    onnx.checker.check_model(onnx.load(target))
    (model_dir / "config.pbtxt").write_text(f'''name: "{name}"
platform: "onnxruntime_onnx"
max_batch_size: 128
input [ {{ name: "input" data_type: TYPE_FP32 dims: [ 3, 32, 32 ] }} ]
output [ {{ name: "logits" data_type: TYPE_FP32 dims: [ 10 ] }} ]
instance_group [ {{ kind: KIND_GPU count: 1 }} ]
dynamic_batching {{ preferred_batch_size: [ 8, 32, 128 ] max_queue_delay_microseconds: 1000 }}
''')
    manifest = json.loads((out / "dataset_manifest.json").read_text())
    if sha256(out / "dataset_manifest.json") != state["manifest_sha256"]:
        raise ValueError("Checkpoint and dataset manifest mismatch")
    options = ort.SessionOptions()
    options.intra_op_num_threads = c["threads"]
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(str(target), sess_options=options, providers=["CPUExecutionProvider"])
    errors, agreements = [], []
    rng = np.random.default_rng(42)
    for n in (1, 8, 31):
        x = rng.standard_normal((n, 3, 32, 32)).astype(np.float32)
        with torch.inference_mode():
            reference = model(torch.from_numpy(x)).numpy()
        actual = session.run(["logits"], {"input": x})[0]
        np.testing.assert_allclose(actual, reference, rtol=1e-3, atol=1e-4)
        errors.append(float(np.max(np.abs(reference - actual))))
        agreements.append(float(np.mean(reference.argmax(1) == actual.argmax(1))))
    # No stochastic augmentation during evaluation or calibration.
    cpu_config = {**c, "device": "cpu"}
    correct, samples = 0, 0
    confusion = np.zeros((10, 10), dtype=np.int64)
    for x, y in loader(cpu_config, manifest, "test"):
        pred = session.run(["logits"], {"input": x.numpy()})[0].argmax(1)
        labels = y.numpy()
        correct += int((pred == labels).sum())
        samples += len(y)
        confusion += np.bincount(labels * 10 + pred, minlength=100).reshape(10, 10)
    pytorch_accuracy = json.loads((out / "test_metrics.json").read_text())["top1_accuracy"]
    metrics = {"test_accuracy": correct / samples, "test_samples": samples,
               "pytorch_test_accuracy": pytorch_accuracy, "absolute_accuracy_delta": abs(correct / samples - pytorch_accuracy),
               "parity_batches": [1, 8, 31], "max_absolute_logit_error": max(errors),
               "prediction_agreement_on_parity_batches": agreements, "confusion_matrix": confusion.tolist(),
               "runtime": ort.__version__, "provider": "CPUExecutionProvider"}
    if metrics["absolute_accuracy_delta"] > 0.001:
        raise RuntimeError(f"ONNX test accuracy differs by more than 0.1 percentage points: {metrics}")
    write_json(out / "onnx_metrics.json", metrics)
    calibration = np.concatenate([x.numpy() for x, _ in loader(cpu_config, manifest, "calibration")])
    np.save(out / "calibration.npy", calibration)
    x, y = next(iter(loader(cpu_config, manifest, "test")))
    np.savez(out / "inference_examples.npz", input=x[:8].numpy(), labels=y[:8].numpy())
    metadata = {"model": name, "version": 1, "format": "ONNX", "precision": "FP32", "opset": 17,
                "input": manifest["input"], "input_name": "input", "output_name": "logits", "output": "10 raw class logits (no softmax)",
                "class_order": CLASSES, "mean": MEAN, "std": STD,
                "checkpoint_sha256": sha256(checkpoint), "onnx_sha256": sha256(target),
                "onnx_bytes": target.stat().st_size, "best_epoch": state["best_epoch"],
                "calibration_sha256": sha256(out / "calibration.npy"),
                "calibration_samples": len(calibration), "calibration_source": "training subset only",
                "dataset_manifest_sha256": state["manifest_sha256"],
                "triton_validation": "config prepared; live Triton server not tested by this export"}
    write_json(out / "model_metadata.json", metadata)
    write_json(model_dir / "metadata.json", metadata)
    tracking = json.loads((out / "mlflow_run.json").read_text())
    mlflow.set_tracking_uri(tracking["tracking_uri"])
    with mlflow.start_run(run_id=tracking["run_id"]):
        mlflow.log_metric("onnx_test_accuracy", metrics["test_accuracy"])
        for filename in ["onnx_metrics.json", "model_metadata.json", "calibration.npy", "inference_examples.npz"]:
            mlflow.log_artifact(str(out / filename))
        mlflow.log_artifacts(str(model_dir), artifact_path="triton_model")
    print(json.dumps({"onnx": str(target), **metrics}), flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--checkpoint", required=True, help="Use only a checkpoint you trust")
    p.add_argument("--repository", default="triton_models")
    a = p.parse_args()
    export(a.checkpoint, a.repository)
