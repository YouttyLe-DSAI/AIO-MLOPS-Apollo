"""Create the baseline evidence report from a completed training/export run."""
import argparse
import csv
import json
import shutil
import subprocess
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import yaml


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--run", default="artifacts/resnet18_cifar10_seed42")
    a = p.parse_args()
    run = Path(a.run)
    result = Path("results/baseline")
    result.mkdir(parents=True, exist_ok=True)
    Path("docs").mkdir(exist_ok=True)
    read = lambda f: json.loads((run / f).read_text())
    summary, test, onnx = (read(f) for f in ("summary.json", "test_metrics.json", "onnx_metrics.json"))
    environment, metadata, manifest = (read(f) for f in ("environment.json", "model_metadata.json", "dataset_manifest.json"))
    c = yaml.safe_load((run / "config.yaml").read_text())
    with (run / "history.csv").open() as f:
        history = list(csv.DictReader(f))
    if len(history) != c["epochs"] or [int(r["epoch"]) for r in history] != list(range(1, c["epochs"] + 1)):
        raise ValueError("History must contain every completed epoch exactly once")
    assert summary["best_epoch"] == int(max(history, key=lambda r: float(r["val_accuracy"]))["epoch"])
    assert test["samples"] == onnx["test_samples"] == 10000
    epochs = [int(r["epoch"]) for r in history]
    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), constrained_layout=True)
    for field, label, color in [("train_accuracy", "Training", "#1d4ed8"), ("val_accuracy", "Validation", "#d97706")]:
        axes[0].plot(epochs, [100 * float(r[field]) for r in history], label=label, color=color)
    axes[0].axvline(summary["best_epoch"], color="#64748b", ls="--", lw=1, label="Selected epoch")
    axes[0].set(xlabel="Epoch", ylabel="Top-1 accuracy (%)", title="CIFAR-10 · ResNet-18 · seed 42")
    axes[0].legend(frameon=False)
    for field, label, color in [("train_loss", "Training", "#1d4ed8"), ("val_loss", "Validation", "#d97706")]:
        axes[1].plot(epochs, [float(r[field]) for r in history], label=label, color=color)
    axes[1].set(xlabel="Epoch", ylabel="Cross-entropy", title="Loss during training")
    axes[1].legend(frameon=False)
    fig.savefig(result / "training-curves.png", dpi=180)
    plt.close(fig)
    cm = np.asarray(test["confusion_matrix"])
    fig, ax = plt.subplots(figsize=(8, 7), constrained_layout=True)
    im = ax.imshow(cm, cmap="Blues")
    for i in range(10):
        for j in range(10):
            ax.text(j, i, str(cm[i, j]), ha="center", va="center", fontsize=8,
                    color="white" if cm[i, j] > cm.max() * 0.6 else "#0f172a")
    ax.set(xticks=range(10), yticks=range(10), xticklabels=manifest["class_order"],
           yticklabels=manifest["class_order"], xlabel="Predicted class", ylabel="True class", title="Official CIFAR-10 test set · 10,000 images")
    plt.setp(ax.get_xticklabels(), rotation=45, ha="right")
    fig.colorbar(im, ax=ax, label="Images")
    fig.savefig(result / "confusion-matrix.png", dpi=180)
    plt.close(fig)
    for name in ("summary.json", "config.yaml", "environment.json", "requirements.lock.txt", "history.csv", "test_metrics.json", "test_predictions.csv", "onnx_metrics.json", "model_metadata.json", "dataset_manifest.json", "mlflow_run.json"):
        shutil.copy2(run / name, result / name)
    provenance = Path("data/download_provenance.json")
    if provenance.exists():
        shutil.copy2(provenance, result / "download_provenance.json")
    for filename in ("train.log", "export.log", "tests.log"):
        if Path(filename).exists():
            shutil.copy2(filename, result / filename)
    snapshot = run / "training_source.zip"
    if snapshot.exists():
        shutil.copy2(snapshot, result / "training_source.zip")
    if shutil.which("nvidia-smi"):
        (result / "nvidia-smi.csv").write_text(subprocess.check_output([
            "nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv"], text=True))
    report = f'''# Baseline training — ResNet-18 / CIFAR-10

## Kết quả thực chạy

Baseline train từ đầu đủ **{c['epochs']} epochs**, seed **{c['seed']}**, FP32 trên **{environment['gpu']}**, server Brev `zalo-ai`. Chọn checkpoint theo validation, sau đó đánh giá một lần trên official test set. Không đặt ngưỡng accuracy từ tài liệu vì nguồn chưa quy định ngưỡng nghiệm thu.

| Chỉ số | Kết quả |
|---|---:|
| Validation top-1 tốt nhất | {summary['best_val_accuracy'] * 100:.2f}% |
| Epoch được chọn | {summary['best_epoch']} |
| PyTorch test top-1 (10.000 ảnh) | {summary['test_accuracy'] * 100:.2f}% |
| ONNX Runtime test top-1 (10.000 ảnh) | {onnx['test_accuracy'] * 100:.2f}% |
| Test cross-entropy | {summary['test_loss']:.5f} |
| Số tham số | {summary['parameters']:,} |
| ONNX size | {metadata['onnx_bytes'] / 1024 ** 2:.2f} MiB |
| Training + test evaluation | {summary['training_seconds_this_invocation'] / 60:.2f} phút |
| Peak PyTorch allocated VRAM | {summary['peak_cuda_memory_bytes'] / 1024 ** 3:.2f} GiB |

VRAM ở đây là peak bộ nhớ PyTorch cấp phát trong training/evaluation, không phải GPU memory khi serving và không bao gồm mọi vùng nhớ driver. Training time không gồm cài môi trường, tải dataset và export ONNX. Chưa đo latency/throughput/Triton serving.

![Training curves](../results/baseline/training-curves.png)

## Data và phương pháp

- Dataset: official CIFAR-10, archive MD5 `{manifest['archive_md5']}`. Bản chạy dùng mirror Paddle vì nguồn Toronto tải chậm; checksum khớp archive chính thức. Tham khảo [mã dataset Paddle](https://github.com/PaddlePaddle/Paddle/blob/develop/python/paddle/dataset/cifar.py).
- Stratified split cố định: 45.000 train, 5.000 validation (500/class), 10.000 official test. Indices đầy đủ và SHA-256 từng file ở `results/baseline/dataset_manifest.json`.
- Calibration: 1.000 ảnh (100/class) lấy từ train; không giao validation/test. Dữ liệu này chỉ được đóng gói cho bước INT8 về sau, chưa chạy quantization.
- Augmentation chỉ train: RandomCrop(32, padding=4), RandomHorizontalFlip. Eval/calibration: tensor RGB /255, normalize mean `[0.4914,0.4822,0.4465]`, std `[0.2470,0.2435,0.2616]`.
- ResNet-18 CIFAR stem: conv 3×3 stride 1, bỏ maxpool, 10 classes; không pretrained ImageNet. API gốc: [torchvision ResNet-18](https://docs.pytorch.org/vision/2.0/models/generated/torchvision.models.resnet18.html).
- SGD lr {c['lr']}, momentum {c['momentum']}, weight decay {c['weight_decay']}; cosine schedule {c['epochs']} epochs, batch {c['batch_size']}. FP32, TF32 tắt, deterministic algorithms. Cấu hình đầy đủ đi kèm.
- Chọn best validation top-1, giữ checkpoint xuất hiện sớm hơn khi bằng điểm. Test không tham gia chọn checkpoint/hyperparameters.

## Kiểm chứng và truy vết

- 5 tests đạt: stratified split/leakage, invalid split, normalization, input/output model, resume tương đương chạy liên tục. Test resume dùng mô hình nhỏ và dữ liệu tổng hợp để kiểm tra optimizer/scheduler restart, không phải thí nghiệm accuracy CIFAR-10 bổ sung.
- `pip check`: không có dependency hỏng. Có 2 cảnh báo deprecation từ MLflow/Pydantic; không làm thất bại tests.
- ONNX checker đạt, dynamic batch 1/8/31 đạt `allclose(rtol=1e-3, atol=1e-4)` với PyTorch. Max absolute logit error **{onnx['max_absolute_logit_error']:.8f}**. So sánh này dùng input tổng hợp để kiểm tra export, không thay cho đánh giá dataset.
- Đánh giá ONNX trên đủ official test set bằng CPUExecutionProvider; accuracy lệch **{onnx['absolute_accuracy_delta'] * 100:.4f} điểm phần trăm** so với PyTorch GPU.
- MLflow run gốc: `{summary['mlflow_run_id']}`, experiment `cifar10-baseline`. Tracking file store trên server: `/home/ubuntu/mlops-baseline/mlruns`; [MLflow tracking](https://www.mlflow.org/docs/latest/tracking/).
- Checkpoint SHA-256: `{summary['checkpoint_sha256']}`.
- ONNX SHA-256: `{metadata['onnx_sha256']}`.
- Python {environment['python']}; torch {environment['torch']}; torchvision {environment['torchvision']}; CUDA {environment['cuda']}; cuDNN {environment['cudnn']}. `requirements.lock.txt` giữ phiên bản dependency thực tế; `environment.json` giữ hashes mã nguồn lúc bắt đầu run.
- `training_source.zip` giữ đúng mã nguồn đã thực hiện run, kiểm tra được với hashes trong `environment.json`. Sau khi khởi chạy, mã bàn giao được bổ sung bảo vệ metadata khi nhập sai config resume và xử lý history khi gián đoạn checkpoint; công thức training, model, dữ liệu và hyperparameters giữ nguyên.

![Confusion matrix](../results/baseline/confusion-matrix.png)

## Bàn giao và giới hạn

Mã chạy: `src/baseline/`, config: `configs/baseline.yaml`, hướng dẫn train/evaluate/resume/export ở docs/baseline-training.md. Checkpoint: `artifacts/resnet18_cifar10_seed42/best.pt` và `last.pt`. ONNX: `triton_models/resnet18_cifar10_fp32/1/model.onnx`; input FP32 NCHW `[N,3,32,32]` đã normalize, output `logits` `[N,10]`.

`calibration.npy` có 1.000 tensors đã normalize; `inference_examples.npz` có 8 test inputs/labels cho smoke test. Các file lớn được giữ riêng khỏi Git. Gói portable có danh sách SHA-256 và có thể nhập lại vào MLflow bằng `scripts/import_mlflow.py`.

Đây là baseline một seed trên H100, khác RTX 4090 trong sơ đồ. Chưa train ResNet-50 teacher, chưa thực hiện PTQ/KD/pruning, chưa build TensorRT, chưa kiểm tra Triton live, chưa đo serving benchmark. Dockerfile và GitHub Actions đã chuẩn bị nhưng chưa được build/chạy trên hệ thống CI. Cần reviewer nhóm xác nhận protocol và chất lượng baseline trước khi đánh dấu task Done trên bảng chung.

Nguồn yêu cầu: [Google Docs](https://docs.google.com/document/d/1ir1vsAyo6xA5cESlyBSUOfrEZFARnBPQK_E3PCPbjMQ/edit), [Google Sheets](https://docs.google.com/spreadsheets/d/1UKM-6IyY2Y1gE1NbCutgXKU2Jz_GQ6HfzXx88_HXRFg/edit?gid=315438118), hai sơ đồ đính kèm. Scope thực hiện theo yêu cầu trực tiếp chuẩn bị baseline training; nội dung AWS/GitOps và ví dụ khác trong tài liệu không được coi là lệnh triển khai.
'''
    profile_path = Path("results/rtx4090-profile/gpu-fit.json")
    if profile_path.exists():
        profile = json.loads(profile_path.read_text())
        report += f'''
## Cập nhật cấu hình mục tiêu RTX 4090 — 07/11/2026

`configs/baseline.yaml` hiện dùng train/eval batch 128/128, workers/threads 4/4, FP32 và ngân sách CUDA allocator 16 GiB cố định. Output mới: `artifacts/resnet18_cifar10_rtx4090_seed42/`. `results/baseline/config.yaml` giữ nguyên cấu hình run H100 100 epochs ở trên.

Kiểm tra ngắn 3 bước train + 1 batch validation trên **{profile['execution_gpu']}** đạt: peak allocated **{profile['peak_cuda_allocated_bytes'] / 1024 ** 3:.2f} GiB**, peak reserved **{profile['peak_cuda_reserved_bytes'] / 1024 ** 3:.2f} GiB**. Bộ 8 tests sau thay đổi đạt. Xem `results/rtx4090-profile/gpu-fit.json` và [ảnh demo](results-demo.md).

Giới hạn áp dụng cho caching allocator của PyTorch, không gồm toàn bộ CUDA/driver. Đây là kiểm tra tài nguyên với profile hướng tới 4090 trên H100; chưa chạy RTX 4090 thật và chưa train lại 100 epochs bằng profile mới. Kết quả 94,85% và thời gian 23,68 phút ở trên tiếp tục thuộc run H100 gốc.
'''
    Path("docs/baseline-report.md").write_text(report)
    print("Created docs/baseline-report.md and results/baseline/")
