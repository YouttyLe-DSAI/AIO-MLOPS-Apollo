# Kế hoạch gỡ hạ tầng (sau bàn giao 23/11/2026)

Owner: Tuấn. Script: `infra/scripts/teardown.sh` (`backup` → `destroy` → `verify`).

## Lịch

| Mốc | Việc | Chi phí trong giai đoạn |
| --- | --- | --- |
| Đến 23/11 (bàn giao) | Chạy đủ release + benchmark cho mọi Model version (FP32, FP16, INT8, KD, pruning). Ngoài giờ benchmark để Triton `replicas: 0`. | ~$5/ngày khi không bật GPU, cộng $0.99/giờ cho mỗi L4 đang bật |
| 23/11 → nghiệm thu (Ad. Vinh) | Giữ cluster để demo. Chỉ bật Triton lúc demo (Model Portal → Benchmark, hoặc `replicas: 1`). | ~$5/ngày |
| Trong vòng 48 giờ sau nghiệm thu | Chạy checklist bên dưới rồi gỡ. | 0 sau khi gỡ, trừ S3 (vài cent/tháng) |

Nếu đến **30/11/2026** vẫn chưa nghiệm thu, vẫn gỡ: mọi số liệu đã nằm trong MLflow backup và report, và dựng lại được bằng Terraform trong khoảng 30 phút (README → Dựng hạ tầng).

## Checklist trước khi gỡ

- [ ] Số liệu benchmark của mọi variant đã vào report và slides (DEL-04, DEL-05). Raw nằm trong MLflow, experiment `benchmark`. Tải về bằng:
      `mlflow artifacts download -r <run_id> -d results/` hoặc dùng `serving report` của repo serving.
- [ ] Mọi PR đã merge, GitOps repo không còn thay đổi local.
- [ ] Đã báo team trước ít nhất 24 giờ (MLflow, Grafana, Portal sẽ ngừng hoạt động).
- [ ] `infra/scripts/teardown.sh backup` chạy thành công. Bản dump nằm ở `../apollo-backups/<thời điểm>/`, có thêm một bản trên S3 tại `backups/`.

## Các bước

```bash
aws login                                   # IAM user AIO_MLOps
infra/scripts/teardown.sh backup            # 1. dump Postgres của MLflow (runs, registry, users) + inventory
infra/scripts/teardown.sh destroy           # 2. hỏi nhập "apollo-mlops" rồi mới xóa
infra/scripts/teardown.sh verify            # 3. chỉ còn bucket S3 MLflow
```

`destroy` chạy theo thứ tự:
1. Xóa root app `apollo-dev` của Argo CD. Mọi app và tài nguyên con bị xóa theo, LB controller xóa NLB của chúng.
2. Xóa PVC (Postgres, Mimir, Loki) và đợi volume EBS bị xóa hẳn. EBS CSI driver phải còn chạy lúc này, nếu không volume sẽ bị bỏ lại và vẫn tính tiền.
3. `terraform destroy` platform. Traefik bị xóa trước LB controller, nên NLB và Elastic IP được trả lại.
4. Foundation: bật `force_destroy_data` để xóa được ECR còn image, gỡ bucket MLflow khỏi state (SCP cấm xóa bucket), rồi `destroy` (EKS, node group, VPC, IAM, OIDC provider, ECR, EIP, budget).

## Dọn GitHub (làm tay)

```bash
gh variable delete AWS_RELEASE_ROLE_ARN -R YouttyLe-DSAI/AIO-MLOPS-Apollo
gh secret delete GITOPS_DEPLOY_KEY -R YouttyLe-DSAI/AIO-MLOPS-Apollo
gh repo deploy-key list -R YouttyLe-DSAI/AIO-MLOPS-Apollo-gitops     # xóa github-actions-release và argocd-apollo-mlops-dev:
gh repo deploy-key delete <id> -R YouttyLe-DSAI/AIO-MLOPS-Apollo-gitops
```

Token GitHub fine-grained của Model Portal: vào GitHub → Settings → Developer settings → Fine-grained tokens → Revoke.

## Những gì còn lại

| Còn lại | Lý do | Cách xử lý |
| --- | --- | --- |
| Bucket S3 artifact MLflow (Seoul) và bucket state (Sydney) | SCP của Organization cấm `s3:DeleteBucket` / `DeleteObject` | Nhờ admin Organization xóa. Chi phí vài cent/tháng |
| Bảng DynamoDB `apollo-tflock` | Khóa state, on-demand, ~$0 khi không dùng | `terraform -chdir=infra/terraform/bootstrap destroy` nếu muốn xóa hẳn |
| Bản dump MLflow (local + S3 `backups/`) | Để khôi phục registry và runs | Xem phần dưới |

## Khôi phục (nếu cần demo lại)

1. Dựng lại theo `infra/terraform/README.md` → Dựng hạ tầng (bootstrap đã có, chỉ cần foundation + platform). Argo CD tự deploy MLflow, monitoring và portal từ GitOps repo.
2. Khôi phục MLflow: `gunzip -c mlflow-postgres.sql.gz | kubectl -n mlflow exec -i statefulset/mlflow-postgres -- psql -U <user> -d postgres`. Artifact vẫn còn nguyên trong S3, nên run và Model version mở lại được ngay.
3. Image ECR đã bị xóa. Chạy lại workflow `release` cho từng version cần serve (khoảng 10 phút mỗi version).
