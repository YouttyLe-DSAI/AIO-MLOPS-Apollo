"""Import a completed baseline into MLflow without rerunning training.

Authentication uses MLFLOW_TRACKING_USERNAME/PASSWORD from the environment.
A FINISHED import with matching source run/checkpoint and complete artifacts is reused.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import time
from pathlib import Path

import mlflow
import yaml
from mlflow import MlflowClient
from mlflow.entities import Metric


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def import_run(directory: Path, model_dir: Path, report: Path | None = None) -> dict:
    directory = directory.resolve()
    config = yaml.safe_load((directory / 'config.yaml').read_text())
    summary = json.loads((directory / 'summary.json').read_text())
    environment = json.loads((directory / 'environment.json').read_text())
    expected = summary['checkpoint_sha256']
    if sha256(directory / 'best.pt') != expected:
        raise ValueError('Checkpoint SHA-256 does not match the completed run')
    with (directory / 'history.csv').open() as stream:
        history = list(csv.DictReader(stream))
    if [int(row['epoch']) for row in history] != list(range(1, config['epochs'] + 1)):
        raise ValueError('History must contain every epoch exactly once')
    if int(max(history, key=lambda row: float(row['val_accuracy']))['epoch']) != summary['best_epoch']:
        raise ValueError('Best epoch disagrees with validation history')
    mlflow.set_tracking_uri(os.getenv('MLFLOW_TRACKING_URI', Path('mlruns').resolve().as_uri()))
    experiment = mlflow.set_experiment(config['experiment'])
    client = MlflowClient()
    source_id = summary['mlflow_run_id']
    found = client.search_runs([experiment.experiment_id],
        filter_string=f"tags.source_run_id = '{source_id}' AND tags.checkpoint_sha256 = '{expected}'",
        max_results=100)
    complete = [r for r in found if r.info.status == 'FINISHED' and r.data.tags.get('import_status') == 'complete']
    if complete:
        run_id = complete[0].info.run_id
        reused = True
    else:
        with mlflow.start_run(run_name=f"{config['model']}-fp32-seed{config['seed']}-import",
                tags={'source_run_id': source_id, 'checkpoint_sha256': expected,
                      'imported_handoff': 'true', 'import_status': 'uploading',
                      'execution_gpu': environment.get('gpu') or 'cpu',
                      'training_precision': 'FP32', 'training_performed_during_import': 'false'}) as run:
            run_id = run.info.run_id
            mlflow.log_params(config)
            timestamp = int(time.time() * 1000)
            records = [Metric(key, float(value), timestamp, int(row['epoch']))
                       for row in history for key, value in row.items() if key != 'epoch']
            for start in range(0, len(records), 1000):
                client.log_batch(run_id, metrics=records[start:start + 1000])
            mlflow.log_metrics({key: summary[key] for key in ('test_accuracy', 'test_loss', 'best_val_accuracy')})
            for folder, prefix in [(directory, 'baseline'), (model_dir, 'triton_model')]:
                if not folder.is_dir():
                    continue
                for artifact in sorted(folder.rglob('*')):
                    if artifact.is_file():
                        relative = artifact.relative_to(folder)
                        destination = str(Path(prefix) / relative.parent)
                        print(f'Uploading {prefix}/{relative} ({artifact.stat().st_size} bytes)', flush=True)
                        mlflow.log_artifact(str(artifact), artifact_path=destination)
            mlflow.set_tag('import_status', 'complete')
        reused = False
    result = {'run_id': run_id, 'source_run_id': source_id,
              'experiment_id': str(experiment.experiment_id), 'reused': reused,
              'tracking_uri': mlflow.get_tracking_uri(), 'checkpoint_sha256': expected,
              'test_accuracy': summary['test_accuracy'],
              'training_gpu': environment.get('gpu'), 'kind': 'historical_training_import'}
    if report:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--run', type=Path, default=Path('artifacts/resnet18_cifar10_seed42'))
    parser.add_argument('--model-dir', type=Path, default=Path('triton_models/resnet18_cifar10_fp32'))
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    import_run(args.run, args.model_dir, args.report)
