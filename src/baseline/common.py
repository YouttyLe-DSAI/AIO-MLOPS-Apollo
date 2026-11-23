"""Shared data/model contract for training, compression and serving."""
import hashlib
import json
import math
import random
from pathlib import Path

import numpy as np
import torch
import yaml
from torch import nn
from torch.utils.data import DataLoader, Subset
from torchvision import datasets, models, transforms

MEAN = (0.4914, 0.4822, 0.4465)
STD = (0.2470, 0.2435, 0.2616)
CLASSES = ["airplane", "automobile", "bird", "cat", "deer", "dog", "frog", "horse", "ship", "truck"]


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def config(path):
    c = yaml.safe_load(Path(path).read_text())
    if c["epochs"] < 1 or c["batch_size"] < 1 or c["workers"] < 0:
        raise ValueError("epochs/batch_size must be positive; workers must be nonnegative")
    if c["model"] not in ("resnet18", "resnet50"):
        raise ValueError("Supported models: resnet18, resnet50")
    if c["device"] not in ("cpu", "cuda"):
        raise ValueError("device must be cpu or cuda")
    budget = c.get("cuda_memory_budget_gib")
    if budget is not None:
        if not math.isfinite(budget) or budget <= 0:
            raise ValueError("cuda_memory_budget_gib must be finite and positive")
        if budget > c.get("target_vram_gib", float("inf")):
            raise ValueError("CUDA memory budget exceeds target VRAM")
    return c


def seed_all(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def configure_cuda_memory(c):
    """Use a fixed byte budget, independent of the execution GPU's capacity."""
    if c["device"] != "cuda":
        return {}
    device = torch.cuda.current_device()
    total = torch.cuda.get_device_properties(device).total_memory
    budget = c.get("cuda_memory_budget_gib")
    result = {"gpu_total_memory_bytes": total}
    if budget is not None:
        if not math.isfinite(budget) or budget <= 0 or budget > c.get("target_vram_gib", float("inf")):
            raise ValueError("Invalid CUDA memory budget for the target GPU")
        limit = int(budget * 1024 ** 3)
        if limit > total:
            raise ValueError("CUDA memory budget exceeds the execution GPU's capacity")
        fraction = limit / total
        torch.cuda.set_per_process_memory_fraction(fraction, device)
        result.update(cuda_allocator_limit_bytes=limit, cuda_allocator_fraction=fraction)
    return result


def setup(c):
    if c["device"] == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable; explicitly choose cpu for a CPU run")
    hardware = configure_cuda_memory(c)
    torch.set_num_threads(c["threads"])
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cudnn.benchmark = not c["deterministic"]
    torch.backends.cudnn.deterministic = c["deterministic"]
    torch.use_deterministic_algorithms(c["deterministic"])
    seed_all(c["seed"])
    return hardware


def build_model(name="resnet18"):
    # CIFAR stem preserves spatial information at 32x32. No ImageNet weights.
    model = getattr(models, name)(weights=None, num_classes=10)
    model.conv1 = nn.Conv2d(3, 64, kernel_size=3, stride=1, padding=1, bias=False)
    model.maxpool = nn.Identity()
    return model


def split_indices(targets, seed, val_per_class, calibration_per_class):
    rng = np.random.default_rng(seed)
    train, val, calibration = [], [], []
    targets = np.asarray(targets)
    for label in range(10):
        ids = rng.permutation(np.flatnonzero(targets == label)).tolist()
        if val_per_class < 1 or calibration_per_class < 1 or val_per_class + calibration_per_class > len(ids):
            raise ValueError("Invalid validation/calibration split sizes")
        val.extend(ids[:val_per_class])
        train.extend(ids[val_per_class:])
        calibration.extend(ids[val_per_class:val_per_class + calibration_per_class])
    return {"train": sorted(train), "validation": sorted(val), "calibration": sorted(calibration)}


def preprocessing(train=False):
    ops = [transforms.RandomCrop(32, padding=4), transforms.RandomHorizontalFlip()] if train else []
    return transforms.Compose(ops + [transforms.ToTensor(), transforms.Normalize(MEAN, STD)])


def prepare(c):
    root = Path(c["data_root"])
    raw = datasets.CIFAR10(root, train=True, download=True)
    test = datasets.CIFAR10(root, train=False, download=True)
    indices = split_indices(raw.targets, c["seed"], c["val_per_class"], c["calibration_per_class"])
    files = sorted((root / raw.base_folder).glob("*"))
    manifest = {
        "dataset": "CIFAR-10", "seed": c["seed"], "source": raw.url,
        "archive_md5": raw.tgz_md5, "files_sha256": {f.name: sha256(f) for f in files if f.is_file()},
        "indices": indices, "test_indices": list(range(len(test))),
        "counts": {**{k: len(v) for k, v in indices.items()}, "test": len(test)},
        "index_namespaces": {"train_validation_calibration": "official training set", "test": "official test set"},
        "calibration_policy": "stratified subset of training; no validation or test examples",
        "class_order": CLASSES, "mean": MEAN, "std": STD,
        "input": {"shape": ["N", 3, 32, 32], "dtype": "float32", "layout": "NCHW", "color": "RGB", "scale": "uint8 / 255 then (x - mean) / std"},
    }
    destination = root / f"manifest_seed{c['seed']}_val{c['val_per_class']}_cal{c['calibration_per_class']}.json"
    if destination.exists() and json.loads(destination.read_text()) != json.loads(json.dumps(manifest)):
        raise RuntimeError("Existing manifest differs: dataset or split has changed")
    write_json(destination, manifest)
    return destination, manifest


def seed_worker(_):
    s = torch.initial_seed() % (2 ** 32)
    random.seed(s)
    np.random.seed(s)


def loader(c, manifest, split, epoch=0):
    training = split == "train"
    ds = datasets.CIFAR10(c["data_root"], train=split != "test", transform=preprocessing(training), download=False)
    if split != "test":
        ds = Subset(ds, manifest["indices"][split])
    return DataLoader(ds, batch_size=c["batch_size"] if training else c["eval_batch_size"],
                      shuffle=training, num_workers=c["workers"], pin_memory=c["device"] == "cuda",
                      worker_init_fn=seed_worker, generator=torch.Generator().manual_seed(c["seed"] + epoch),
                      persistent_workers=False)


@torch.inference_mode()
def evaluate(model, batches, device):
    model.eval()
    total, correct, loss = 0, 0, 0.0
    confusion = torch.zeros(10, 10, dtype=torch.int64)
    predictions = []
    for x, y in batches:
        logits = model(x.to(device, non_blocking=True))
        loss += nn.functional.cross_entropy(logits, y.to(device), reduction="sum").item()
        pred = logits.argmax(1).cpu()
        total += len(y)
        correct += (pred == y).sum().item()
        confusion += torch.bincount(y * 10 + pred, minlength=100).reshape(10, 10)
        predictions.extend(zip(y.tolist(), pred.tolist()))
    return {"loss": loss / total, "top1_accuracy": correct / total, "samples": total,
            "confusion_matrix": confusion.tolist(),
            "per_class_accuracy": {name: confusion[i, i].item() / max(1, confusion[i].sum().item()) for i, name in enumerate(CLASSES)}}, predictions
