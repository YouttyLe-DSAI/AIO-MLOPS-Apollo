"""Generate a derived report without modifying the raw EKS benchmark files."""
from pathlib import Path
from dataclasses import replace
import csv,json,copy,argparse
from serving.report import load_run,Report,write_report
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--root',type=Path,default=Path.cwd(),help='Apollo checkout containing results/apollo-benchmark')
args=parser.parse_args()
root=args.root.resolve()
receipt=json.loads((root/'results/apollo-benchmark/verification.json').read_text())
if not receipt['all_checks_passed']:
 raise RuntimeError('Benchmark verification is incomplete; see verification.json')
folder=root/receipt['downloaded_run_dir']
run=load_run(folder)
run=replace(run,metadata=copy.deepcopy(run.metadata))
# The EKS producer uses hardware.gpu, while the workstation reader uses hardware.gpus.
# Adapt the in-memory report view only; raw metadata.json and its checksums stay unchanged.
hardware=run.metadata.get('hardware',{})
if not hardware.get('gpus') and hardware.get('gpu',{}).get('modelName'):
 hardware['gpus']=[{'name':hardware['gpu']['modelName']}]
 hardware['cpu_count']=hardware.get('client',{}).get('cpus')
 hardware['cpu_model']=hardware.get('cpu_model') or 'not recorded'
report=Report(runs=[run],metrics=run.metrics)
out=root/'results/apollo-benchmark/report'
write_report(report,out)
points=receipt['points']
best=max(points,key=lambda p:p['images_per_s_mean'])
base=next(p for p in points if p['concurrency']==1 and p['batch_size']==1)
smoke=receipt['smoke'];gpu=receipt.get('gpu') or {}
intro=f'''# Baseline FP32 — Release và Benchmark

Model version: **resnet18-cifar10 v2**, `resnet18-baseline-fp32-v1`.

- [GitHub Actions]({receipt['workflow_url']})
- [MLflow benchmark run](https://mlflow.3-39-32-158.sslip.io/#/experiments/{receipt['mlflow_experiment_id']}/runs/{receipt['mlflow_run_id']})
- [Model Portal](https://portal.3-39-32-158.sslip.io)

## Kết quả

| Chỉ số | Kết quả |
|---|---:|
| GPU đo thực tế | {gpu.get('modelName','Xem metadata.json')} |
| Offline top-1 | {smoke['offline_top1_accuracy']*100:.2f}% |
| Triton Smoke test top-1 | {smoke['triton_top1_accuracy']*100:.2f}% trên {smoke['images']:,} ảnh |
| Chênh lệch chất lượng | {smoke['diff_pp']:.4f} điểm phần trăm |
| Throughput cao nhất (mean 3 lần đo) | {best['images_per_s_mean']:,.1f} ảnh/s tại concurrency {best['concurrency']}, batch {best['batch_size']} |
| P95 tại concurrency 1, batch 1 (mean) | {base['p95_latency_ms_mean']:.4f} ms |
| Ma trận | {len(points)}/12 điểm, 1 warm-up + 3 lần đo mỗi điểm |
| CV throughput lớn nhất | {receipt['max_throughput_cv_pct']:.3f}% |
| Metadata | `{receipt['metadata_status']}` |

## Điều kiện đo và truy vết

Đây là số đo trên EKS L4, với TensorRT backend, strict FP32 (`--noTF32`), 1 GPU instance, dynamic batching và max batch size 32. Load generator chạy trên node riêng. Ma trận concurrency 1/2/4/8/16/32 × batch 1/8, cửa sổ Perf Analyzer 5 s, ổn định 10%, tối đa 10 trials.

`metadata.json`, `benchmark_config.yaml`, `smoke_test.json`, raw CSV/stdout của warm-up và mọi lần đo được giữ nguyên trong thư mục raw đã tải từ MLflow. `raw-files-sha256.json` lưu checksum; `verification.json` kiểm tra completeness và CV độc lập. Để dùng reader workstation của repo serving, phần cứng EKS được ánh xạ từ `hardware.gpu.modelName` sang `hardware.gpus[].name` chỉ trong bộ nhớ khi tạo report, không thay metadata gốc.

![Throughput](report/throughput_vs_concurrency.png)

![Latency](report/latency_vs_concurrency.png)

Bảng từng điểm gồm mean và sample standard deviation: [report/summary.md](report/summary.md), [report/summary.csv](report/summary.csv).
'''
(root/'results/apollo-benchmark/README.md').write_text(intro)
print('Generated baseline serving benchmark report:',out)
print(json.dumps({'gpu':gpu.get('modelName'),'best_throughput':best,'c1_b1':base,'smoke':smoke,'checks':receipt['checks']}))
