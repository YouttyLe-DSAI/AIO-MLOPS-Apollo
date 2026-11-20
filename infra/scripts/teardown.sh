#!/usr/bin/env bash
# Tear down the whole Apollo dev environment after hand-off (see infra/TEARDOWN.md).
#
#   infra/scripts/teardown.sh backup      # 1. back up MLflow DB + inventory (safe, read-only for AWS)
#   infra/scripts/teardown.sh destroy     # 2. delete apps, volumes, platform, foundation (asks to type the cluster name)
#   infra/scripts/teardown.sh verify      # 3. list anything tagged Project=apollo that still costs money
#
# Order matters: Argo CD apps first (so PVC volumes are deleted by the EBS CSI driver while it still
# runs), then platform (Traefik before the LB controller, so the NLB is deleted), then foundation.
# Only resources of this project are touched (cluster apollo-mlops, tag Project=apollo).
set -euo pipefail

CLUSTER=apollo-mlops
REGION=ap-northeast-2
ACCOUNT=669167565740
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
TF=$ROOT/infra/terraform/environments/dev
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP_DIR=${BACKUP_DIR:-$ROOT/../apollo-backups/$STAMP}

say() { printf '\n== %s\n' "$*"; }

preflight() {
  local acct
  acct=$(aws sts get-caller-identity --query Account --output text)
  [ "$acct" = "$ACCOUNT" ] || { echo "wrong AWS account: $acct (expected $ACCOUNT)"; exit 1; }
  aws eks update-kubeconfig --name "$CLUSTER" --region "$REGION" >/dev/null
}

backup() {
  preflight
  mkdir -p "$BACKUP_DIR"
  say "MLflow Postgres (runs, registry, users) -> $BACKUP_DIR/mlflow-postgres.sql.gz"
  kubectl -n mlflow exec statefulset/mlflow-postgres -- sh -c 'pg_dumpall -U "$POSTGRES_USER"' | gzip > "$BACKUP_DIR/mlflow-postgres.sql.gz"
  [ "$(gzip -dc "$BACKUP_DIR/mlflow-postgres.sql.gz" | grep -c 'CREATE DATABASE')" -ge 1 ] || { echo "dump looks empty"; exit 1; }

  local bucket
  bucket=$(terraform -chdir="$TF/foundation" output -raw mlflow_artifact_root 2>/dev/null | sed -E 's|^s3://([^/]+).*|\1|' || true)
  if [ -n "$bucket" ]; then
    say "copy of the dump next to the artifacts: s3://$bucket/backups/$STAMP/ (the bucket survives teardown: org SCP)"
    aws s3 cp --only-show-errors "$BACKUP_DIR/mlflow-postgres.sql.gz" "s3://$bucket/backups/$STAMP/mlflow-postgres.sql.gz"
  fi

  say "inventory (what existed; images are NOT kept)"
  for repo in resnet18-triton model-portal; do
    aws ecr describe-images --region "$REGION" --repository-name "$repo" \
      --query 'imageDetails[].{tag:imageTags[0],pushed:imagePushedAt,bytes:imageSizeInBytes}' --output json \
      > "$BACKUP_DIR/ecr-$repo.json" 2>/dev/null || true
  done
  kubectl get applications -n argocd -o wide > "$BACKUP_DIR/argocd-apps.txt" 2>&1 || true
  kubectl get pvc -A > "$BACKUP_DIR/pvcs.txt" 2>&1 || true
  echo "backup done: $BACKUP_DIR"
  echo "restore later: gunzip -c mlflow-postgres.sql.gz | psql -U <user> -d postgres   (artifacts stay in S3)"
}

wait_until_empty() {  # $1 = description, $2.. = command whose output must become empty
  local what=$1; shift
  for _ in $(seq 1 60); do
    [ -z "$("$@" 2>/dev/null)" ] && return 0
    echo "  waiting: $what"; sleep 10
  done
  echo "timeout waiting for: $what"; "$@"; exit 1
}

destroy() {
  preflight
  [ -f "$(ls -d "$ROOT"/../apollo-backups/*/mlflow-postgres.sql.gz 2>/dev/null | tail -1)" ] \
    || { echo "no MLflow backup found in $ROOT/../apollo-backups: run '$0 backup' first"; exit 1; }
  read -r -p "This deletes cluster $CLUSTER and everything in it. Type the cluster name to continue: " answer
  [ "$answer" = "$CLUSTER" ] || { echo "aborted"; exit 1; }

  say "1/5 Argo CD: delete the root app (cascades to every app and its resources)"
  kubectl -n argocd patch application apollo-dev --type merge -p '{"spec":{"syncPolicy":null}}' 2>/dev/null || true
  kubectl -n argocd delete application apollo-dev --wait=false 2>/dev/null || true
  wait_until_empty "Argo CD applications" kubectl -n argocd get applications -o name

  say "2/5 volumes: delete leftover PVCs (StatefulSet volumes survive app deletion) and wait for the EBS volumes"
  kubectl delete pvc --all -n mlflow --wait=false 2>/dev/null || true
  kubectl delete pvc --all -n monitoring --wait=false 2>/dev/null || true
  kubectl delete pvc --all -n release --wait=false 2>/dev/null || true
  wait_until_empty "PersistentVolumes" kubectl get pv -o name
  wait_until_empty "LoadBalancer Services other than Traefik" \
    sh -c "kubectl get svc -A --field-selector spec.type=LoadBalancer -o name | grep -v traefik"

  say "3/5 platform (Helm releases; Traefik before the LB controller, so its NLB is deleted)"
  GITHUB_TOKEN=${GITHUB_TOKEN:-$(gh auth token 2>/dev/null || true)} \
    terraform -chdir="$TF/platform" destroy -input=false -auto-approve

  say "4/5 foundation: allow deleting ECR repos that still hold images, keep the S3 bucket out of state"
  terraform -chdir="$TF/foundation" apply -input=false -auto-approve -var force_destroy_data=true \
    -target='module.ecr["resnet18-triton"]' -target='module.ecr["model-portal"]'
  terraform -chdir="$TF/foundation" state list | grep '^module.mlflow_bucket' | while read -r addr; do
    terraform -chdir="$TF/foundation" state rm "$addr"
  done

  say "5/5 foundation destroy (EKS, node groups, VPC, IAM, ECR, EIP, budget)"
  terraform -chdir="$TF/foundation" destroy -input=false -auto-approve -var force_destroy_data=true
  verify
}

verify() {
  say "still tagged Project=apollo in $REGION (expected: only the MLflow S3 bucket)"
  aws resourcegroupstaggingapi get-resources --region "$REGION" --tag-filters Key=Project,Values=apollo \
    --query 'ResourceTagMappingList[].ResourceARN' --output text | tr '\t' '\n'
  say "EBS volumes left by the cluster (should be none)"
  aws ec2 describe-volumes --region "$REGION" --filters "Name=tag-key,Values=kubernetes.io/cluster/$CLUSTER" \
    --query 'Volumes[].[VolumeId,Size,State]' --output text
  say "load balancers left by the cluster (should be none)"
  aws elbv2 describe-load-balancers --region "$REGION" --query "LoadBalancers[?contains(LoadBalancerName, 'k8s') || contains(LoadBalancerName, 'apollo')].[LoadBalancerName,State.Code]" --output text
  cat <<'EOF'

Kept on purpose (cannot or need not be deleted by us):
  - MLflow artifact bucket (Seoul) and state bucket (Sydney): org SCP denies s3:DeleteBucket -> ask the Organization admin.
  - DynamoDB table apollo-tflock (on-demand, ~$0 when idle): terraform -chdir=infra/terraform/bootstrap destroy removes it.
GitHub (manual, see infra/TEARDOWN.md): repo variable AWS_RELEASE_ROLE_ARN, secret GITOPS_DEPLOY_KEY,
deploy keys on the GitOps repo.
EOF
}

case "${1:-}" in
  backup) backup ;;
  destroy) destroy ;;
  verify) preflight; verify ;;
  *) sed -n '2,10p' "$0"; exit 1 ;;
esac
