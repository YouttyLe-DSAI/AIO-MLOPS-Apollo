#!/usr/bin/env bash
# Scale the L4 node group back to 0. GPU pods (Triton, GPU runner) go Pending until gpu-up.sh.
set -euo pipefail
CLUSTER=${CLUSTER:-apollo-mlops}; REGION=${REGION:-ap-southeast-2}; NG=${NG:-gpu-l4}

aws eks update-nodegroup-config --region "$REGION" --cluster-name "$CLUSTER" --nodegroup-name "$NG" \
  --scaling-config minSize=0,maxSize=1,desiredSize=0 --query 'update.status' --output text
echo "GPU node group scaled to 0 at $(date '+%F %T'); the instance terminates within a few minutes."
