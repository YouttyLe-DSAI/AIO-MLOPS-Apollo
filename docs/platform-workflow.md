# Luồng workflow của nền tảng Apollo

Từ model đã train trên máy local đến Triton trên EKS, có benchmark và monitoring. Mỗi bước đều chạy lại được và truy vết được trên MLflow, Git và GitHub Actions.

Đối chiếu với Project Handbook (đã đủ chưa, còn thiếu gì) và cách tự kiểm tra từng phần: [`handoff-checklist.md`](handoff-checklist.md).

## Tổng quan

```mermaid
flowchart LR
  subgraph LOCAL["Máy local (RTX 4090)"]
    T["Train / KD / Pruning / PTQ<br/>(Hoàng, Mạnh)"] --> MV["Thư mục Model version<br/>model.onnx + meta.yaml<br/>(+ calibration.cache cho INT8)"]
    MV --> VAL["serving validate<br/>(repo serving của Nam)"]
  end

  VAL -->|scripts/register_model_version.py| MLF[("MLflow registry<br/>resnet18-cifar10 vN")]

  subgraph TRIGGER["Kích hoạt"]
    P["Model Portal<br/>portal.3-39-32-158.sslip.io"]
    GH["GitHub → Actions<br/>Run workflow"]
  end
  MLF -->|danh sách version| P
  P -->|workflow_dispatch| WF
  GH --> WF

  subgraph WF["GitHub Actions (OIDC → AWS, không có access key)"]
    R["release.yml"]
    B["benchmark.yml"]
  end

  subgraph EKS["AWS EKS · ap-northeast-2"]
    J["Release Job trên L4<br/>fetch → trtexec → BuildKit"]
    TR["Triton 26.09<br/>(namespace serving)"]
    BJ["Benchmark Job (node loadgen)<br/>Smoke test + Perf Analyzer"]
    ARGO["Argo CD"]
    OBS["Alloy → Mimir / Loki → Grafana<br/>+ DCGM (GPU)"]
  end

  R --> J -->|push image tag = model_version_id| ECR[("ECR resnet18-triton")]
  R -->|commit tag| GITOPS[("GitOps repo<br/>envs/dev/triton/values.yaml")]
  B -->|tag + replicas 1 → 0| GITOPS
  GITOPS -->|watch| ARGO -->|deploy + smoke test PostSync| TR
  ECR -->|pull| TR
  B --> BJ -->|HTTP tải benchmark| TR
  BJ -->|results + tags benchmark.*| MLF
  TR -.metrics.-> OBS
  BJ -.GPU util / VRAM.-> OBS
```

## 1. Đăng ký Model version (Hoàng, Mạnh)

```bash
# trong repo serving: kiểm tra contract trước
uv run serving validate --model-version path/to/resnet18-baseline-fp16-v1
# trong repo Apollo: đăng ký lên MLflow
export MLFLOW_TRACKING_URI=https://mlflow.3-39-32-158.sslip.io MLFLOW_TRACKING_USERNAME=<tên> MLFLOW_TRACKING_PASSWORD=<mật khẩu>
uv run --with "mlflow-skinny==3.16.0" --with pyyaml python scripts/register_model_version.py path/to/resnet18-baseline-fp16-v1
# -> registered resnet18-cifar10 version N
```

Mỗi target precision là một Model version riêng (ADR-0001). INT8 cần thêm `calibration.cache`, build từ ảnh **train**.

## 2. Release: model → Triton (`release.yml`)

```mermaid
sequenceDiagram
  autonumber
  actor U as Người dùng (Portal / GitHub)
  participant GA as GitHub Actions
  participant K as EKS (namespace release)
  participant CA as Cluster Autoscaler
  participant E as ECR
  participant G as GitOps repo
  participant A as Argo CD
  participant T as Triton
  U->>GA: release(model_name, model_version)
  GA->>K: OIDC → kubeconfig, tạo Job
  K->>CA: pod xin nvidia.com/gpu
  CA-->>K: bật node L4 (0→1, ~4 phút)
  K->>K: fetch-model (MLflow, kiểm tra meta.yaml)
  K->>E: đã có tag? → bỏ qua build
  K->>K: trtexec TensorRT 26.09 → model.plan
  K->>E: BuildKit push image:model_version_id
  GA->>G: commit tag vào envs/dev/triton/values.yaml
  G-->>A: thay đổi Git
  A->>T: deploy image mới (Recreate)
  A->>T: PostSync smoke test (batch 1/8/32)
```

Mất khoảng 10–30 phút; lần đầu lâu hơn vì phải bật GPU và kéo image Triton (~9 GB).

## 3. Benchmark (`benchmark.yml`)

```mermaid
sequenceDiagram
  autonumber
  actor U as Người dùng (Portal / GitHub)
  participant GA as GitHub Actions
  participant G as GitOps repo
  participant T as Triton (L4)
  participant BJ as Benchmark Job (node loadgen)
  participant M as MLflow
  participant MI as Mimir
  U->>GA: benchmark(model_name, model_version, keep_serving)
  GA->>GA: release (idempotent: image có rồi thì bỏ qua)
  GA->>G: tag + replicas: 1
  G-->>T: Argo CD deploy, CA bật L4
  GA->>BJ: tạo Job (CA bật node loadgen)
  BJ->>T: đợi model ready (kiểm tra model_version_id)
  BJ->>T: Smoke test: 10k ảnh test, |top1 - offline| ≤ 0.5 pp
  loop 12 điểm: concurrency {1..32} × batch {1, 8}
    BJ->>T: 1 warm-up + 3 lần đo perf_analyzer
  end
  BJ->>MI: GPU util / VRAM theo từng cửa sổ đo
  BJ->>M: experiment "benchmark": benchmark.csv, summary.csv, raw, metadata.json
  BJ->>M: tag benchmark.* lên Model version (Portal hiển thị)
  GA->>G: replicas: 0 (trừ khi keep_serving) → CA tắt L4 sau 10 phút
```

Kết quả dùng layout `results/` của repo serving, nên `serving report` tổng hợp được bảng và biểu đồ cho report (DEL-04, DEL-05).

## 4. Các workflow khác

| Workflow | Khi nào chạy | Làm gì |
| --- | --- | --- |
| `ci.yml` | Mọi PR và push lên `main` | `terraform fmt/validate`, kiểm tra cú pháp script, unit test của portal. **Bắt buộc pass mới merge được** (branch protection) |
| `portal.yml` | Push lên `main` có thay đổi trong `portal/` | Chạy test, build `model-portal:<sha>` lên ECR, ghi tag vào GitOps. Argo CD tự rollout |
| `release.yml` | Bấm tay hoặc từ Portal | Mục 2 |
| `benchmark.yml` | Bấm tay hoặc từ Portal | Mục 3 |

## 5. Thay đổi hạ tầng (Tuấn)

```mermaid
flowchart LR
  BR["branch + PR<br/>(Terraform)"] --> CI["ci.yml<br/>fmt + validate"] --> PL["terraform plan<br/>đọc kỹ từng thay đổi"] --> AP["terraform apply<br/>foundation → platform"] --> MG["merge PR"]
  GO["Thay đổi app<br/>(GitOps repo)"] --> ARGO["Argo CD auto-sync<br/>+ self-heal"]
```

- `foundation`: VPC, EKS, node group (system spot, L4 0–2 node, loadgen 0–1 node cho Perf Analyzer), IAM, Pod Identity, ECR, S3, OIDC GitHub, budget.
- `platform`: LB controller, Traefik, cert-manager, Argo CD, Cluster Autoscaler, NVIDIA device plugin, các namespace và Secret.
- App (MLflow, Triton, monitoring, portal) nằm trong GitOps repo. Argo CD đồng bộ, không ai `kubectl apply` bằng tay.

## 6. Quan sát và chi phí

- **Grafana** (https://grafana.3-39-32-158.sslip.io): dashboard *Apollo · Triton serving* gồm throughput, latency, queue/compute, GPU util, VRAM, power, tensor core, nhiệt độ. Log pod xem qua Loki.
- **GPU chỉ bật khi có workload.** Cluster Autoscaler bật L4 khi có pod xin GPU và tắt sau 10 phút rảnh. Benchmark tự đưa Triton về `replicas: 0` khi chạy xong.
- **Node loadgen** (c6i.2xlarge on-demand, khoảng $0.38/giờ) cũng chỉ chạy trong lúc có Benchmark Job, cộng 10 phút rảnh trước khi CA tắt.
- **Gỡ hạ tầng sau nghiệm thu:** xem `infra/TEARDOWN.md`.
