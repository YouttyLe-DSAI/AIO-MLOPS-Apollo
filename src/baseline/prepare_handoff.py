"""Package an evaluated baseline for the aio-mlops-serving Model version contract.

This does not train, quantize, release or benchmark the model. Authentication is
handled separately by the MLflow client through environment variables.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import yaml


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def prepare(run: Path, onnx: Path, output: Path, training_run_id: str | None = None) -> dict:
    summary = json.loads((run / 'summary.json').read_text())
    metadata = json.loads((run / 'model_metadata.json').read_text())
    manifest = json.loads((run / 'dataset_manifest.json').read_text())
    if summary['model'] != 'resnet18' or metadata.get('precision') != 'FP32':
        raise ValueError('This handoff packager expects the ResNet-18 FP32 baseline')
    if sha256(onnx) != metadata['onnx_sha256']:
        raise ValueError('ONNX SHA-256 does not match evaluated model metadata')
    if summary['checkpoint_sha256'] != metadata['checkpoint_sha256']:
        raise ValueError('Checkpoint provenance mismatch')
    if sha256(run / 'dataset_manifest.json') != metadata['dataset_manifest_sha256']:
        raise ValueError('Dataset manifest SHA-256 mismatch')
    accuracy = float(summary['test_accuracy'])
    if not 0.0 <= accuracy <= 1.0:
        raise ValueError('Accuracy must be a fraction in [0,1]')
    meta = {
        'model_version_id': 'resnet18-baseline-fp32-v1',
        'variant': 'baseline', 'target_precision': 'FP32', 'artifact_format': 'onnx',
        'input': {'name': metadata['input_name'], 'shape': [-1, 3, 32, 32], 'dtype': 'float32'},
        'output': {'name': metadata['output_name'], 'shape': [-1, 10], 'dtype': 'float32'},
        'num_classes': 10, 'offline_top1_accuracy': accuracy,
        'preprocessing': {'color': 'RGB', 'layout': 'NCHW', 'scale': 'uint8 / 255',
                          'mean': metadata['mean'], 'std': metadata['std'],
                          'input_already_normalized': True},
        'class_order': metadata['class_order'],
        'dataset': {'name': manifest['dataset'], 'seed': manifest['seed'],
                    'counts': manifest['counts'], 'manifest_sha256': metadata['dataset_manifest_sha256'],
                    'calibration_source': 'training subset only'},
        'source': {'created_by': 'hoang', 'training_run': training_run_id or summary['mlflow_run_id'],
                   'original_training_run': summary['mlflow_run_id'], 'arch': summary['model'],
                   'checkpoint_sha256': metadata['checkpoint_sha256'], 'onnx_sha256': metadata['onnx_sha256'],
                   'best_epoch': summary['best_epoch'], 'trained_from_scratch': True},
    }
    if (output / 'model.onnx').exists() and sha256(output / 'model.onnx') != metadata['onnx_sha256']:
        raise FileExistsError('Refusing to replace a different model in an existing Model version folder')
    output.mkdir(parents=True, exist_ok=True)
    if onnx.resolve() != (output / 'model.onnx').resolve():
        shutil.copy2(onnx, output / 'model.onnx')
    (output / 'meta.yaml').write_text(yaml.safe_dump(meta, sort_keys=False, allow_unicode=True))
    print(f"Prepared {meta['model_version_id']} at {output} (offline top-1 {accuracy:.2%})", flush=True)
    return meta


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--run', type=Path, default=Path('artifacts/resnet18_cifar10_seed42'))
    parser.add_argument('--onnx', type=Path, default=Path('triton_models/resnet18_cifar10_fp32/1/model.onnx'))
    parser.add_argument('--output', type=Path, default=Path('artifacts/model-versions/resnet18-baseline-fp32-v1'))
    parser.add_argument('--training-run-id', help='Remote imported training run ID, when available')
    args = parser.parse_args()
    prepare(args.run, args.onnx, args.output, args.training_run_id)
