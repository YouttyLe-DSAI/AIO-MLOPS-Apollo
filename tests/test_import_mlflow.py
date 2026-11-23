"""A completed historical import is reused without uploading the artifacts again."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json

import yaml


def test_completed_import_is_reused(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'scripts/import_mlflow.py'
    spec = spec_from_file_location('baseline_import', path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    run = tmp_path / 'run'
    run.mkdir()
    checkpoint = b'fixture checkpoint'
    (run / 'best.pt').write_bytes(checkpoint)
    (run / 'config.yaml').write_text(yaml.safe_dump({'experiment': 'baseline-test', 'epochs': 2}))
    (run / 'summary.json').write_text(json.dumps({'checkpoint_sha256': hashlib.sha256(checkpoint).hexdigest(),
        'mlflow_run_id': 'source-run', 'best_epoch': 2, 'test_accuracy': .9485}))
    (run / 'environment.json').write_text(json.dumps({'gpu': 'recorded execution GPU'}))
    (run / 'history.csv').write_text('epoch,val_accuracy\n1,0.8\n2,0.9\n')
    existing = SimpleNamespace(info=SimpleNamespace(status='FINISHED', run_id='completed-import'),
                               data=SimpleNamespace(tags={'import_status': 'complete'}))
    class Client:
        def search_runs(self, experiments, filter_string, max_results):
            assert experiments == ['experiment-id']
            assert 'source-run' in filter_string
            return [existing]
    monkeypatch.setattr(module, 'MlflowClient', Client)
    monkeypatch.setattr(module.mlflow, 'set_tracking_uri', lambda _: None)
    monkeypatch.setattr(module.mlflow, 'get_tracking_uri', lambda: 'https://tracking.example')
    monkeypatch.setattr(module.mlflow, 'set_experiment', lambda _: SimpleNamespace(experiment_id='experiment-id'))
    def must_not_upload(*args, **kwargs):
        raise AssertionError('A completed import must not start another upload')
    monkeypatch.setattr(module.mlflow, 'start_run', must_not_upload)
    result = module.import_run(run, tmp_path / 'model')
    assert result['reused'] is True
    assert result['run_id'] == 'completed-import'
