#!/usr/bin/env bash
# Read-only checks before creating any AWS resource for the Apollo platform.
# Usage: infra/scripts/preflight.sh [region ...]   (default: ap-northeast-2 ap-southeast-2)
set -uo pipefail

REGIONS=("${@:-ap-northeast-2 ap-southeast-2}")
read -r -a REGIONS <<<"${REGIONS[*]}"
GPU_TYPE=g6.xlarge          # 1x NVIDIA L4, 4 vCPU, 16 GiB
GPU_VCPU=4

hr() { printf '\n== %s ==\n' "$1"; }

hr "Identity"
aws sts get-caller-identity --output table || { echo "Not logged in. Run: aws login"; exit 1; }
ARN=$(aws sts get-caller-identity --query Arn --output text)
USER_NAME=${ARN##*/}

hr "IAM policies on $USER_NAME (may be denied for non-admins)"
aws iam list-attached-user-policies --user-name "$USER_NAME" --query 'AttachedPolicies[].PolicyName' --output text 2>&1 | sed 's/^/  attached: /'
aws iam list-groups-for-user --user-name "$USER_NAME" --query 'Groups[].GroupName' --output text 2>&1 | sed 's/^/  groups:   /'

for R in "${REGIONS[@]}"; do
  hr "Region $R"

  AZS=$(aws ec2 describe-instance-type-offerings --region "$R" --location-type availability-zone \
        --filters Name=instance-type,Values=$GPU_TYPE --query 'InstanceTypeOfferings[].Location' --output text 2>&1)
  echo "  $GPU_TYPE offered in AZs: ${AZS:-NONE}"

  # L-DB2E81BA = Running On-Demand G and VT instances (vCPU); L-3819A6DF = All G and VT Spot Instance Requests (vCPU)
  OD=$(aws service-quotas get-service-quota --region "$R" --service-code ec2 --quota-code L-DB2E81BA --query 'Quota.Value' --output text 2>&1)
  SP=$(aws service-quotas get-service-quota --region "$R" --service-code ec2 --quota-code L-3819A6DF --query 'Quota.Value' --output text 2>&1)
  echo "  G/VT on-demand vCPU quota: $OD   (1x $GPU_TYPE needs $GPU_VCPU)"
  echo "  G/VT spot vCPU quota:      $SP"
  aws service-quotas list-requested-service-quota-change-history --region "$R" --service-code ec2 \
      --query "RequestedQuotas[?QuotaCode=='L-DB2E81BA' || QuotaCode=='L-3819A6DF'].[QuotaName,DesiredValue,Status]" --output text 2>/dev/null | sed 's/^/  pending request: /'

  PRICE=$(aws ec2 describe-spot-price-history --region "$R" --instance-types $GPU_TYPE \
          --product-descriptions "Linux/UNIX" --max-items 1 --query 'SpotPriceHistory[0].SpotPrice' --output text 2>/dev/null | head -1)
  echo "  latest spot price $GPU_TYPE: ${PRICE:-n/a} USD/h"

  echo "  EKS clusters:   $(aws eks list-clusters --region "$R" --query clusters --output text 2>&1)"
  echo "  ECR repos:      $(aws ecr describe-repositories --region "$R" --query 'repositories[].repositoryName' --output text 2>&1)"
  echo "  VPCs:           $(aws ec2 describe-vpcs --region "$R" --query 'length(Vpcs)' --output text 2>&1)  | EIPs: $(aws ec2 describe-addresses --region "$R" --query 'length(Addresses)' --output text 2>&1)"
  echo "  running EC2:    $(aws ec2 describe-instances --region "$R" --filters Name=instance-state-name,Values=running --query 'Reservations[].Instances[].InstanceType' --output text 2>&1)"
done

hr "Account-wide"
echo "  S3 buckets: $(aws s3api list-buckets --query 'Buckets[].Name' --output text 2>&1)"
ACCT=$(aws sts get-caller-identity --query Account --output text)
echo "  Budgets:    $(aws budgets describe-budgets --account-id "$ACCT" --query 'Budgets[].[BudgetName,BudgetLimit.Amount]' --output text 2>&1)"
echo "  EKS versions (standard support, ${REGIONS[0]}):"
aws eks describe-cluster-versions --region "${REGIONS[0]}" --query "clusterVersions[?versionStatus=='STANDARD_SUPPORT'].clusterVersion" --output text 2>&1 | sed 's/^/    /'
