"""Short training/evaluation memory check; does not produce quality metrics."""
import argparse
import json
import os

# Required before CUDA initialization for deterministic FP32 matrix operations.
os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")

import torch
from src.baseline.common import build_model, config, loader, prepare, setup, write_json


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config", default="configs/baseline.yaml")
    p.add_argument("--output", default="results/rtx4090-profile/gpu-fit.json")
    args = p.parse_args()
    c = config(args.config)
    if c["device"] != "cuda":
        raise ValueError("GPU fit check requires device: cuda")
    hardware = setup(c)
    torch.cuda.reset_peak_memory_stats()
    _, manifest = prepare(c)
    model = build_model(c["model"]).cuda()
    optimizer = torch.optim.SGD(model.parameters(), lr=c["lr"], momentum=c["momentum"], weight_decay=c["weight_decay"])
    model.train()
    batches = loader(c, manifest, "train")
    for i, (x, y) in enumerate(batches):
        optimizer.zero_grad(set_to_none=True)
        loss = torch.nn.functional.cross_entropy(model(x.cuda()), y.cuda())
        if not torch.isfinite(loss):
            raise RuntimeError("Nonfinite training loss")
        loss.backward()
        optimizer.step()
        if i == 2:
            break
    del batches
    model.eval()
    x, _ = next(iter(loader(c, manifest, "validation")))
    with torch.inference_mode():
        logits = model(x.cuda())
    assert logits.shape == (c["eval_batch_size"], 10)
    assert torch.isfinite(logits).all()
    torch.cuda.synchronize()
    result = {"check": "gpu_fit_only", "passed": True, "target_gpu": c["target_gpu"],
              "execution_gpu": torch.cuda.get_device_name(), "target_vram_gib": c["target_vram_gib"],
              "cuda_memory_budget_gib": c["cuda_memory_budget_gib"], **hardware,
              "train_steps": 3, "batch_size": c["batch_size"], "eval_batch_size": len(x),
              "workers": c["workers"], "threads": c["threads"], "precision": "FP32", "tf32": False,
              "peak_cuda_allocated_bytes": torch.cuda.max_memory_allocated(),
              "peak_cuda_reserved_bytes": torch.cuda.max_memory_reserved(),
              "torch": torch.__version__, "actual_rtx4090_tested": False,
              "note": "Allocator budget only; H100 does not emulate RTX 4090 speed or CUDA kernel memory requirements."}
    assert result["peak_cuda_reserved_bytes"] <= result["cuda_allocator_limit_bytes"]
    write_json(args.output, result)
    print(json.dumps(result), flush=True)
