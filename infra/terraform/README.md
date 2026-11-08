# Hạ tầng AWS — Terraform

Hạ tầng cloud của dự án Apollo (EKS + GPU L4 + ECR + S3/MLflow + Argo CD), quản lý hoàn toàn bằng Terraform.
Owner: Tuấn (Integration & Release).

## Cấu trúc

```
infra/
├── scripts/
│   ├── preflight.sh                 # chỉ đọc: identity, quota GPU, region, tài nguyên đang có
│   ├── gpu-up.sh / gpu-down.sh      # bật / tắt node L4
└── terraform/
    ├── backend.hcl.example          # bucket + region của remote state (copy thành backend.hcl)
    ├── bootstrap/                   # S3 bucket chứa remote state (chạy 1 lần, state local)
    ├── modules/                     # module tái sử dụng, mỗi module là một nhóm resource
    │   ├── vpc/                     # VPC, IGW, public subnets, route table
    │   ├── eks-cluster/             # IAM role cluster, aws_eks_cluster, access entries
    │   ├── eks-node-group/          # IAM role node, launch template, aws_eks_node_group
    │   ├── eks-addons/              # aws_eks_addon (+ pod identity cho add-on)
    │   ├── pod-identity/            # IAM role + policy + aws_eks_pod_identity_association
    │   ├── ecr-repository/          # aws_ecr_repository + lifecycle policy
    │   ├── s3-bucket/               # bucket private, mã hóa, versioning tùy chọn, lifecycle
    │   ├── dynamodb-lock-table/     # bảng khóa state cho S3 backend
    │   └── budget/                  # aws_budgets_budget + cảnh báo email
    └── environments/
        └── dev/
            ├── foundation/          # gọi các module: network, EKS, node groups, IAM, ECR, S3, budget
            └── platform/            # Helm: AWS LB Controller, NVIDIA device plugin, Argo CD;
                                     # GitOps wiring (deploy key, root app) + MLflow namespace/DB secret
```

Mỗi root (`bootstrap`, `environments/<env>/<stack>`) có cùng bố cục file:
`backend.tf`, `versions.tf`, `providers.tf`, `variables.tf`, `locals.tf`, `main.tf`, `outputs.tf`, `terraform.tfvars`.

- `terraform.tfvars`: cấu hình của môi trường, **được commit**.
- `secrets.auto.tfvars`: giá trị riêng tư như email, **bị git-ignore** vì repo đang public. Mẫu nằm ở `secrets.auto.tfvars.example`.
- Remote state: `s3://apollo-tfstate-<account>-ap-southeast-2/apollo/<env>/<stack>.tfstate`, khóa bằng bảng DynamoDB `apollo-tflock` (lý do xem phần Ràng buộc SCP bên dưới).
  State backend vẫn ở **Sydney** dù hạ tầng đã chuyển sang Seoul: SCP cấm xóa bucket, nên tạo bucket mới chỉ để lại thêm một bucket thừa. Region của backend không ảnh hưởng tới region của resource.

`foundation` và `platform` tách riêng để provider Helm không bao giờ phụ thuộc vào cluster đang được tạo trong cùng lần apply.
Workload ứng dụng (Triton, MLflow, Model Portal, Grafana stack, ARC runner) **không** nằm ở đây: Argo CD deploy chúng từ GitOps repo private [`AIO-MLOPS-Apollo-gitops`](https://github.com/YouttyLe-DSAI/AIO-MLOPS-Apollo-gitops).
`platform` chỉ tạo phần nền mà Git không được chứa: deploy key read-only cho Argo CD, root app `apollo-dev` (đọc `apps/dev`), namespace và Secret `mlflow-db`.

Thêm môi trường mới, ví dụ `prod`: copy `environments/dev` sang `environments/prod`, sửa `terraform.tfvars` và `key` trong `backend.tf`.

## Quyết định chính

| Quyết định | Lý do |
| --- | --- |
| Region **ap-northeast-2 (Seoul)** | `g6.xlarge` (NVIDIA L4) không có ở Singapore. Ngày 2026-11-06, Sydney hết hàng L4 on-demand ở cả 3 AZ (thử 15 phút đều `InsufficientInstanceCapacity`). Seoul còn hàng ở cả 3 AZ (đã probe bằng capacity reservation), giá rẻ hơn ($0.99/giờ) và gần Việt Nam hơn. |
| GPU node `g6.xlarge` **on-demand**, mặc định **0 node** | Benchmark không bị spot thu hồi giữa chừng. Chỉ bật GPU khi cần. |
| System node **spot**, 1 AZ (`ap-northeast-2a`) | Rẻ hơn khoảng 60%. Để 1 AZ thì PVC (EBS) luôn gắn lại được sau khi node bị thu hồi. |
| Không dùng NAT gateway, node đặt trong public subnet | Tiết kiệm khoảng $45/tháng. Inbound vẫn bị security group chặn. |
| `upgrade_policy = STANDARD` | Không tự rơi vào extended support ($0.60/h thay vì $0.10/h). |
| UI public qua HTTPS (sslip.io + Let's Encrypt), có đăng nhập | Chỉ Tuấn có quyền AWS, nên các bạn khác cần link public. Một NLB và EIP dùng chung cho mọi UI (~$20/tháng). |
| Version add-on được pin trong `terraform.tfvars` | Tránh tự nâng cấp add-on khi apply một thay đổi không liên quan. |
| Budget **$300/tháng**, lọc theo region Seoul | Account dùng chung, budget toàn account sẽ bị chi phí của team khác chi phối. |

## Ràng buộc SCP của account

Account nằm trong một AWS Organization dùng chung. SCP `p-u1ybnvit` do management account đặt **chặn tuyệt đối** (Admin cũng không vượt được) ba action:

| Action bị chặn | Hệ quả | Cách xử lý |
| --- | --- | --- |
| `s3:PutBucketVersioning` | Bucket state và MLflow không bật được versioning | `versioning_enabled = false`. Trước khi thay đổi lớn, backup state: `terraform state pull > backup.tfstate` |
| `s3:DeleteObject` | S3 native lock (`use_lockfile`) không xóa được file `.tflock`, nên state sẽ bị khóa vĩnh viễn. MLflow không xóa được artifact | Khóa state bằng DynamoDB (`dynamodb_table`). Cảnh báo *deprecated* khi init là bình thường |
| `s3:DeleteBucket` | `terraform destroy` không xóa được bucket S3 | Nhờ admin của Organization xóa khi kết thúc dự án |

Các action còn lại của EKS, EC2, IAM, ECR, ELB, DynamoDB và Budgets đã được kiểm tra bằng `aws iam simulate-principal-policy` và đều được phép.

## Service account mà chart phải dùng (đã có quyền AWS sẵn)

| Workload | Namespace / ServiceAccount | Quyền |
| --- | --- | --- |
| MLflow tracking server | `mlflow` / `mlflow` | đọc/ghi bucket artifact MLflow |
| ARC GPU runner (build TensorRT) | `arc-runners` / `arc-gpu-runner` | push ECR `resnet18-triton`, đọc artifact MLflow |
| AWS LB Controller | `kube-system` / `aws-load-balancer-controller` | policy chính thức v3.6.2 |

Đổi tên thì sửa `mlflow_service_account` / `gpu_runner_service_account` trong `environments/dev/foundation/terraform.tfvars`.

## Chuẩn bị

Cần AWS CLI v2 (≥ 2.32), Terraform ≥ 1.10, kubectl, helm.

```bash
aws login --region ap-northeast-2      # đăng nhập bằng tài khoản console qua trình duyệt, không cần access key
infra/scripts/preflight.sh             # chỉ đọc
```

## Dựng hạ tầng (lần đầu)

```bash
cd infra/terraform

# 1. State bucket
terraform -chdir=bootstrap init
terraform -chdir=bootstrap apply
terraform -chdir=bootstrap output -raw backend_hcl > backend.hcl

# 2. Foundation (~15–20 phút, phần lớn là chờ EKS)
cp environments/dev/foundation/secrets.auto.tfvars.example environments/dev/foundation/secrets.auto.tfvars   # điền email
terraform -chdir=environments/dev/foundation init -backend-config=../../../backend.hcl
terraform -chdir=environments/dev/foundation plan -out=tfplan
terraform -chdir=environments/dev/foundation apply tfplan
$(terraform -chdir=environments/dev/foundation output -raw kubeconfig_command)

# 3. Platform
terraform -chdir=environments/dev/platform init -backend-config=../../../backend.hcl
GITHUB_TOKEN=$(gh auth token) terraform -chdir=environments/dev/platform apply   # tạo deploy key trên GitOps repo
kubectl get nodes -L role,node.kubernetes.io/instance-type
```

## Vận hành hằng ngày

```bash
infra/scripts/gpu-up.sh      # trước khi build TensorRT / benchmark (~3–5 phút đến khi GPU sẵn sàng)
infra/scripts/gpu-down.sh    # NGAY sau khi xong, vì L4 tốn ~$0.99/giờ
```

Module `eks-node-group` bỏ qua `desired_size` sau khi tạo, nên hai script trên không gây drift.
Nếu AWS hết hàng GPU (`InsufficientInstanceCapacity`) hoặc node không join được trong 10 phút, `gpu-up.sh` báo lỗi và **tự tắt về 0**, để máy không tự bật lên sau đó mà không ai biết.

## Chi phí ước tính (Seoul, mỗi tháng)

| Hạng mục | Chi phí |
| --- | --- |
| EKS control plane | $73 |
| 2× system node spot (m-class large, ~$0.026–0.044/giờ) | ~$55 |
| EBS gp3 2×40 GB + 2 public IPv4 | ~$15 |
| **Cluster khi không chạy GPU** | **~$143** |
| GPU L4 on-demand ($0.99/giờ): 4 giờ/ngày × 22 ngày | ~$87 |
| NLB (khi Triton được expose) | ~$17 |
| ECR + S3 | < $5 |
| **Tổng** | **~$250**, dưới budget $300 |

## Gỡ toàn bộ

```bash
# 0. Xóa các Application trong Argo CD trước, để LB controller kịp xóa NLB của Triton.
#    Nếu gỡ controller trước thì NLB bị bỏ lại, VPC sẽ không xóa được.
kubectl get svc -A --field-selector spec.type=LoadBalancer   # phải trống
terraform -chdir=environments/dev/platform destroy
terraform -chdir=environments/dev/foundation apply -var force_destroy_data=true   # cho phép xóa ECR còn image
terraform -chdir=environments/dev/foundation state rm module.mlflow_bucket          # SCP cấm xóa bucket S3
terraform -chdir=environments/dev/foundation destroy
# Bucket MLflow, bucket state và bảng apollo-tflock giữ lại; nhờ admin Organization xóa bucket khi kết thúc dự án.
```

## Truy cập dịch vụ

Các UI được public qua **HTTPS** theo đường: NLB (Elastic IP cố định `3.39.32.158`) → Traefik → Ingress. Chứng chỉ do Let's Encrypt cấp qua cert-manager. HTTP tự chuyển sang HTTPS.

| Dịch vụ | Link | Đăng nhập |
| --- | --- | --- |
| MLflow | https://mlflow.3-39-32-158.sslip.io | Tài khoản riêng: `kubectl -n mlflow get secret mlflow-user-<tên> -o jsonpath='{.data.password}' \| base64 -d` |
| Argo CD | https://argocd.3-39-32-158.sslip.io | `admin`: `kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' \| base64 -d` |

- Hostname lấy từ [sslip.io](https://sslip.io), dựa trên EIP. Nếu sau này có domain thật, chỉ cần đổi `local.base_domain` trong `environments/dev/platform/ingress.tf` và các host trong GitOps repo.
- Thêm tài khoản MLflow: `infra/scripts/mlflow-create-user.sh <tên>`. Mật khẩu được lưu vào Secret `mlflow/mlflow-user-<tên>`, không in ra màn hình.
- Máy training: `export MLFLOW_TRACKING_URI=https://mlflow.3-39-32-158.sslip.io MLFLOW_TRACKING_USERNAME=<tên> MLFLOW_TRACKING_PASSWORD=<mật khẩu>`. Artifact được upload qua MLflow, nên máy training không cần quyền AWS.
- Người có quyền AWS vẫn đi đường nội bộ được: `kubectl -n mlflow port-forward svc/mlflow 5000:80`.
