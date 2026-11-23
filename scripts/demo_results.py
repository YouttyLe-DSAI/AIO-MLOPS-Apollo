"""Render real test predictions and class metrics from the completed baseline."""
import argparse
import csv
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import onnxruntime as ort
from torchvision.datasets import CIFAR10

from src.baseline.common import CLASSES, preprocessing, sha256, write_json


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--run", default="artifacts/resnet18_cifar10_seed42")
    p.add_argument("--model", default="triton_models/resnet18_cifar10_fp32/1/model.onnx")
    p.add_argument("--data-root", default="data")
    p.add_argument("--output", default="results/baseline")
    a = p.parse_args()
    run, out = Path(a.run), Path(a.output)
    out.mkdir(parents=True, exist_ok=True)
    metrics = json.loads((run / "test_metrics.json").read_text())
    metadata = json.loads((run / "model_metadata.json").read_text())
    summary = json.loads((run / "summary.json").read_text())
    environment = json.loads((run / "environment.json").read_text())
    actual_gpu = environment.get("gpu") or "CPU"
    gpu_label = "H100" if "H100" in actual_gpu else "RTX 4090" if "4090" in actual_gpu else actual_gpu
    if sha256(a.model) != metadata["onnx_sha256"]:
        raise ValueError("ONNX does not match completed run")
    with (run / "test_predictions.csv").open() as f:
        recorded = list(csv.DictReader(f))
    ds = CIFAR10(a.data_root, train=False, download=False, transform=preprocessing())
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(a.model, sess_options=options, providers=["CPUExecutionProvider"])
    selected = np.random.default_rng(42).choice(len(ds), size=24, replace=False).tolist()
    errors = [int(row["test_index"]) for row in recorded if row["label"] != row["prediction"]][:8]
    evidence = []

    def gallery(indices, filename, title, cols):
        x = np.stack([ds[i][0].numpy() for i in indices])
        logits = session.run(["logits"], {"input": x})[0]
        probability = np.exp(logits - logits.max(axis=1, keepdims=True))
        probability /= probability.sum(axis=1, keepdims=True)
        predicted = probability.argmax(axis=1)
        rows = int(np.ceil(len(indices) / cols))
        fig, axes = plt.subplots(rows, cols, figsize=(12, rows * 3.2), squeeze=False)
        for ax in axes.flat:
            ax.axis("off")
        for n, (i, pred) in enumerate(zip(indices, predicted)):
            ax = axes.flat[n]
            label = ds.targets[i]
            assert int(recorded[i]["label"]) == label
            correct = int(pred) == label
            confidence = float(probability[n, pred])
            ax.imshow(ds.data[i], interpolation="nearest")
            ax.set_title(f"Test #{i}  |  True: {CLASSES[label]}\nPred: {CLASSES[pred]}\nSoftmax: {confidence:.1%}",
                         fontsize=9, color="#166534" if correct else "#b91c1c", pad=5)
            evidence.append({"gallery": filename, "test_index": i, "true_class": CLASSES[label],
                             "predicted_class": CLASSES[pred], "softmax": confidence, "correct": correct})
        fig.suptitle(title, fontsize=16, fontweight="bold", y=0.99)
        fig.text(0.5, 0.015, f"ResNet-18 FP32 | checkpoint epoch {summary['best_epoch']} | trained on {gpu_label} | green: correct, red: incorrect", ha="center", fontsize=10)
        fig.subplots_adjust(left=0.02, right=0.98, bottom=0.07,
                            top=0.87 if rows <= 2 else 0.91, wspace=0.18, hspace=0.7)
        fig.savefig(out / filename, dpi=170, facecolor="white")
        plt.close(fig)

    gallery(selected, "prediction-demo.png", "CIFAR-10 predictions | 24 fixed random test images (seed 42)", 6)
    if errors:
        gallery(errors, "error-demo.png", "Error analysis | first 8 misclassified official test images", 4)
    cm = np.asarray(metrics["confusion_matrix"])
    accuracy = 100 * cm.diagonal() / cm.sum(axis=1)
    fig, ax = plt.subplots(figsize=(10, 6), constrained_layout=True)
    ax.barh(CLASSES, accuracy, color=["#d97706" if v < 93 else "#2563eb" for v in accuracy])
    for i, value in enumerate(accuracy):
        ax.text(value + 0.7, i, f"{value:.1f}%", va="center", fontsize=11)
    ax.invert_yaxis()
    ax.set(xlim=(0, 108), xlabel="Accuracy per class (%) · 1,000 test images per class",
           title=f"CIFAR-10 baseline | overall test accuracy {metrics['top1_accuracy']:.2%}\nResNet-18 · {gpu_label} training run · epoch {summary['best_epoch']}")
    ax.spines[["top", "right"]].set_visible(False)
    fig.savefig(out / "per-class-accuracy.png", dpi=180, facecolor="white")
    plt.close(fig)
    write_json(out / "demo-predictions.json", {"onnx_sha256": metadata["onnx_sha256"],
        "checkpoint_sha256": metadata["checkpoint_sha256"], "execution_provider": "CPUExecutionProvider",
        "training_gpu": environment["gpu"], "selection": "24 random official test indices with seed 42; first 8 recorded errors",
        "full_test_accuracy": metrics["top1_accuracy"], "predictions": evidence})
    print("Rendered prediction-demo.png, error-demo.png, per-class-accuracy.png")
