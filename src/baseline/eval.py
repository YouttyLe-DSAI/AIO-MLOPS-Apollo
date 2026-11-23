import argparse
import json
from pathlib import Path

import torch

from .common import build_model, evaluate, loader, prepare, setup, write_json, sha256


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--checkpoint", required=True, help="Use only a checkpoint you trust")
    p.add_argument("--data-root", default="data")
    p.add_argument("--device", choices=["cpu", "cuda"], default="cuda")
    p.add_argument("--output", default="results/evaluation.json")
    a = p.parse_args()
    state = torch.load(a.checkpoint, map_location="cpu", weights_only=True)
    c = {**state["config"], "data_root": a.data_root, "device": a.device}
    setup(c)
    manifest_path, manifest = prepare(c)
    if sha256(manifest_path) != state["manifest_sha256"]:
        raise ValueError("Checkpoint and dataset manifest mismatch")
    model = build_model(c["model"]).to(c["device"])
    model.load_state_dict(state["model"])
    metrics, _ = evaluate(model, loader(c, manifest, "test"), c["device"])
    write_json(a.output, metrics)
    print(json.dumps(metrics))
