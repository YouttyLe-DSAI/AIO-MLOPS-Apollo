"""Register a Model version folder (model.onnx + meta.yaml [+ calibration.cache]) in MLflow.

The folder follows the serving contract of aio-mlops-serving (docs/model-version-contract.md);
each target precision is its own Model version (ADR-0001). The script:

  1. logs one MLflow run in the experiment named after `variant` (baseline, distillation, ...),
     with the meta fields as params, `offline_top1_accuracy` as a metric and the folder as
     artifacts under `model/`;
  2. registers `runs:/<run>/model` as a new version of the registered model (default
     resnet18-cifar10) and copies the meta fields onto the version as tags.

The printed version number is what the release workflow takes (GitHub Actions -> release ->
model_version).

Usage (needs MLFLOW_TRACKING_URI, MLFLOW_TRACKING_USERNAME, MLFLOW_TRACKING_PASSWORD):

    uv run --with "mlflow-skinny==3.16.0" --with pyyaml \
        python scripts/register_model_version.py path/to/resnet18-baseline-fp32-v1 [--name resnet18-cifar10]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import mlflow
import yaml
from mlflow import MlflowClient

REQUIRED = ("model_version_id", "variant", "target_precision", "input", "output", "offline_top1_accuracy")


def flatten(d: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, f"{key}."))
        else:
            out[key] = json.dumps(v) if isinstance(v, list) else str(v)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("folder", type=Path, help="Model version folder (model.onnx + meta.yaml)")
    ap.add_argument("--name", default="resnet18-cifar10", help="registered model name in MLflow")
    args = ap.parse_args()

    folder = args.folder.resolve()
    meta = yaml.safe_load((folder / "meta.yaml").read_text())
    missing = [k for k in REQUIRED if meta.get(k) is None]
    if missing or not (folder / "model.onnx").is_file():
        raise SystemExit(f"not a Model version folder: missing {missing or 'model.onnx'} (validate it first)")

    mlflow.set_experiment(str(meta["variant"]))
    with mlflow.start_run(run_name=str(meta["model_version_id"])) as run:
        mlflow.log_params({k: v for k, v in flatten(meta).items() if not k.startswith("source.")})
        mlflow.log_metric("offline_top1_accuracy", float(meta["offline_top1_accuracy"]))
        mlflow.set_tags({"model_version_id": meta["model_version_id"], "contract": "aio-mlops-serving/meta.yaml"})
        mlflow.log_artifacts(str(folder), artifact_path="model")
        run_id = run.info.run_id

    client = MlflowClient()
    try:
        client.create_registered_model(args.name, description="ResNet-18 CIFAR-10 Model versions (one per target precision)")
    except mlflow.exceptions.MlflowException as e:
        if "RESOURCE_ALREADY_EXISTS" not in str(e):
            raise
    source = f"{mlflow.get_run(run_id).info.artifact_uri}/model"
    mv = client.create_model_version(args.name, source=source, run_id=run_id, tags=flatten(meta))
    print(f"registered {args.name} version {mv.version}: {meta['model_version_id']} "
          f"({meta['target_precision']}, top-1 {float(meta['offline_top1_accuracy']):.4f}), run {run_id}")
    print(f"release it: GitHub -> Actions -> release -> model_name={args.name}, model_version={mv.version}")


if __name__ == "__main__":
    main()
