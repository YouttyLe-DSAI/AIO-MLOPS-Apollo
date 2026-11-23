import argparse
import csv
import json
import os
import platform
import subprocess
import time
from pathlib import Path

import mlflow
import torch
import torchvision
import yaml

from .common import build_model, config, evaluate, loader, prepare, seed_all, setup, sha256, write_json


def atomic_save(value, path):
    temp = path.with_suffix(".tmp")
    torch.save(value, temp)
    temp.replace(path)


def train(c, resume=None):
    hardware = setup(c)
    if c["device"] == "cuda":
        torch.cuda.reset_peak_memory_stats()
    out = Path(c["output_dir"]).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "config.yaml").exists() and not resume:
        raise FileExistsError("Output already contains a run. Choose a new output_dir or --resume last.pt")
    manifest_path, manifest = prepare(c)
    env = {"python": platform.python_version(), "platform": platform.platform(), "torch": torch.__version__,
           "torchvision": torchvision.__version__, "cuda": torch.version.cuda,
           "cudnn": torch.backends.cudnn.version(), "gpu": torch.cuda.get_device_name() if c["device"] == "cuda" else None,
           "precision": "FP32", "tf32": False, "deterministic": c["deterministic"],
           "target_gpu": c.get("target_gpu"), "target_vram_gib": c.get("target_vram_gib"),
           "cuda_memory_budget_gib": c.get("cuda_memory_budget_gib"), **hardware,
           "source_sha256": {str(p): sha256(p) for p in sorted(Path("src").rglob("*.py"))},
           "manifest_sha256": sha256(manifest_path)}
    model = build_model(c["model"]).to(c["device"])
    optimizer = torch.optim.SGD(model.parameters(), lr=c["lr"], momentum=c["momentum"], weight_decay=c["weight_decay"])
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=c["epochs"])
    start, best, best_epoch = 0, -1.0, 0
    if resume:
        state = torch.load(resume, map_location="cpu", weights_only=True)
        # These settings define the trajectory. Data/output locations may move.
        for key in set(c) - {"data_root", "output_dir", "experiment", "target_gpu", "target_vram_gib", "cuda_memory_budget_gib"}:
            if state["config"][key] != c[key]:
                raise ValueError(f"Resume config mismatch: {key}")
        if state["manifest_sha256"] != env["manifest_sha256"]:
            raise ValueError("Resume dataset manifest mismatch")
        model.load_state_dict(state["model"])
        optimizer.load_state_dict(state["optimizer"])
        scheduler.load_state_dict(state["scheduler"])
        start, best, best_epoch = state["epoch"], state["best_accuracy"], state["best_epoch"]
        if not (out / "best.pt").exists():
            raise ValueError("Resume requires best.pt in the output directory")
        history_path = out / "history.csv"
        if not history_path.exists():
            raise ValueError("Resume requires history.csv in the output directory")
        with history_path.open() as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames
            history = [row for row in reader if int(row["epoch"]) <= start]
        if [int(row["epoch"]) for row in history] != list(range(1, start + 1)):
            raise ValueError("Resume history does not match checkpoint epochs")
        # Discard a row written just before an interrupted checkpoint commit.
        with history_path.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(history)
    # Validate resume before replacing any provenance from the original run.
    (out / "config.yaml").write_text(yaml.safe_dump(c, sort_keys=False))
    write_json(out / "dataset_manifest.json", manifest)
    write_json(out / (f"environment_resume_epoch{start}.json" if resume else "environment.json"), env)
    (out / (f"requirements_resume_epoch{start}.lock.txt" if resume else "requirements.lock.txt")).write_text(
        subprocess.check_output([os.sys.executable, "-m", "pip", "freeze"], text=True))
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI", Path("mlruns").resolve().as_uri())
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(c["experiment"])
    started = time.monotonic()
    with mlflow.start_run(run_name=f"{c['model']}-fp32-seed{c['seed']}") as run:
        write_json(out / "mlflow_run.json", {"run_id": run.info.run_id, "tracking_uri": tracking_uri, "experiment_id": run.info.experiment_id})
        mlflow.log_params(c)
        mlflow.set_tags({"precision": "fp32", "dataset": "cifar10", "weights": "random", "stem": "cifar-3x3-no-maxpool", "resume_from_epoch": start})
        if c.get("target_gpu"):
            mlflow.set_tags({"target_gpu": c["target_gpu"], "execution_gpu": env["gpu"] or "cpu"})
        history_path = out / "history.csv"
        if start and not history_path.exists():
            raise ValueError("Resume requires history.csv in the output directory")
        for epoch in range(start, c["epochs"]):
            # Epoch-local seeds also reproduce workers/augmentation after restart.
            seed_all(c["seed"] + epoch)
            tick = time.monotonic()
            model.train()
            loss_sum, correct, count = 0.0, 0, 0
            lr = optimizer.param_groups[0]["lr"]
            for x, y in loader(c, manifest, "train", epoch):
                x, y = x.to(c["device"], non_blocking=True), y.to(c["device"], non_blocking=True)
                optimizer.zero_grad(set_to_none=True)
                logits = model(x)
                loss = torch.nn.functional.cross_entropy(logits, y)
                loss.backward()
                optimizer.step()
                loss_sum += loss.item() * len(y)
                correct += (logits.argmax(1) == y).sum().item()
                count += len(y)
            val, _ = evaluate(model, loader(c, manifest, "validation"), c["device"])
            scheduler.step()
            improved = val["top1_accuracy"] > best
            if improved:
                best, best_epoch = val["top1_accuracy"], epoch + 1
            state = {"epoch": epoch + 1, "model": model.state_dict(), "optimizer": optimizer.state_dict(),
                     "scheduler": scheduler.state_dict(), "best_accuracy": best, "best_epoch": best_epoch,
                     "config": c, "manifest_sha256": env["manifest_sha256"]}
            row = {"epoch": epoch + 1, "lr": lr, "train_loss": loss_sum / count, "train_accuracy": correct / count,
                   "val_loss": val["loss"], "val_accuracy": val["top1_accuracy"], "epoch_seconds": time.monotonic() - tick}
            with history_path.open("a", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=row.keys())
                if f.tell() == 0:
                    writer.writeheader()
                writer.writerow(row)
            mlflow.log_metrics({k: v for k, v in row.items() if k != "epoch"}, step=epoch + 1)
            if improved:
                atomic_save(state, out / "best.pt")
            atomic_save(state, out / "last.pt")
            print(json.dumps(row), flush=True)
        state = torch.load(out / "best.pt", map_location="cpu", weights_only=True)
        model.load_state_dict(state["model"])
        test, predictions = evaluate(model, loader(c, manifest, "test"), c["device"])
        write_json(out / "test_metrics.json", test)
        with (out / "test_predictions.csv").open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["test_index", "label", "prediction"])
            writer.writerows((i, *p) for i, p in enumerate(predictions))
        summary = {"model": c["model"], "best_epoch": best_epoch, "best_val_accuracy": best,
                   "test_accuracy": test["top1_accuracy"], "test_loss": test["loss"],
                   "parameters": sum(p.numel() for p in model.parameters()),
                   "fp32_weights_bytes": sum(p.numel() * p.element_size() for p in model.state_dict().values()),
                   "training_seconds_this_invocation": time.monotonic() - started,
                   "peak_cuda_memory_bytes": torch.cuda.max_memory_allocated() if c["device"] == "cuda" else 0,
                   "peak_cuda_reserved_bytes": torch.cuda.max_memory_reserved() if c["device"] == "cuda" else 0,
                   "mlflow_run_id": run.info.run_id, "checkpoint_sha256": sha256(out / "best.pt")}
        write_json(out / "summary.json", summary)
        mlflow.log_metrics({"test_accuracy": summary["test_accuracy"], "test_loss": summary["test_loss"], "best_val_accuracy": best})
        for filename in ["config.yaml", "dataset_manifest.json", "environment.json", "requirements.lock.txt", "history.csv", "test_metrics.json", "test_predictions.csv", "summary.json", "best.pt"]:
            mlflow.log_artifact(str(out / filename))
        print(json.dumps(summary), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/baseline.yaml")
    parser.add_argument("--resume")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    c = config(args.config)
    if args.prepare_only:
        p, m = prepare(c)
        print(p, m["counts"])
    else:
        train(c, args.resume)


if __name__ == "__main__":
    main()
