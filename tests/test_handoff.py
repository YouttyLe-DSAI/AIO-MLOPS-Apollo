"""Handoff rejects changed evidence before it can replace a Model version."""
import hashlib
import json
from pathlib import Path

import pytest

from src.baseline.prepare_handoff import prepare


def fixture_run(root):
    root.mkdir()
    onnx = root / 'export.onnx'
    onnx.write_bytes(b'fixture ONNX bytes; graph validation belongs to serving validate')
    manifest = {'dataset': 'CIFAR-10', 'seed': 42,
                'counts': {'train': 45000, 'validation': 5000, 'test': 10000, 'calibration': 1000}}
    (root / 'dataset_manifest.json').write_text(json.dumps(manifest))
    summary = {'checkpoint_sha256': 'checkpoint-hash', 'test_accuracy': .9485,
               'mlflow_run_id': 'original-run', 'model': 'resnet18', 'best_epoch': 96}
    metadata = {'precision': 'FP32', 'onnx_sha256': hashlib.sha256(onnx.read_bytes()).hexdigest(),
                'checkpoint_sha256': 'checkpoint-hash',
                'dataset_manifest_sha256': hashlib.sha256((root / 'dataset_manifest.json').read_bytes()).hexdigest(),
                'input_name': 'input', 'output_name': 'logits',
                'mean': [.4914, .4822, .4465], 'std': [.2470, .2435, .2616],
                'class_order': ['airplane', 'automobile', 'bird', 'cat', 'deer', 'dog', 'frog', 'horse', 'ship', 'truck']}
    for name, value in [('summary.json', summary), ('model_metadata.json', metadata)]:
        (root / name).write_text(json.dumps(value))
    return onnx


def test_handoff_retains_accuracy_contract_and_remote_provenance(tmp_path):
    run = tmp_path / 'run'
    onnx = fixture_run(run)
    target = tmp_path / 'version'
    meta = prepare(run, onnx, target, 'remote-training-run')
    assert meta['offline_top1_accuracy'] == .9485
    assert meta['input'] == {'name': 'input', 'shape': [-1, 3, 32, 32], 'dtype': 'float32'}
    assert meta['source']['training_run'] == 'remote-training-run'
    assert meta['source']['original_training_run'] == 'original-run'
    assert (target / 'model.onnx').read_bytes() == onnx.read_bytes()


def test_changed_export_cannot_replace_existing_handoff(tmp_path):
    run = tmp_path / 'run'
    onnx = fixture_run(run)
    target = tmp_path / 'version'
    prepare(run, onnx, target)
    before = (target / 'meta.yaml').read_bytes()
    onnx.write_bytes(b'changed model')
    with pytest.raises(ValueError, match='ONNX SHA-256'):
        prepare(run, onnx, target)
    assert (target / 'meta.yaml').read_bytes() == before
    assert (target / 'model.onnx').read_bytes() != onnx.read_bytes()


def test_changed_split_manifest_is_rejected(tmp_path):
    run = tmp_path / 'run'
    onnx = fixture_run(run)
    (run / 'dataset_manifest.json').write_text('{}')
    with pytest.raises(ValueError, match='manifest SHA-256'):
        prepare(run, onnx, tmp_path / 'version')
    assert not (tmp_path / 'version').exists()


def test_other_precision_cannot_be_mislabeled_as_fp32(tmp_path):
    run = tmp_path / 'run'
    onnx = fixture_run(run)
    path = run / 'model_metadata.json'
    metadata = json.loads(path.read_text())
    metadata['precision'] = 'FP16'
    path.write_text(json.dumps(metadata))
    with pytest.raises(ValueError, match='ResNet-18 FP32'):
        prepare(run, onnx, tmp_path / 'version')
