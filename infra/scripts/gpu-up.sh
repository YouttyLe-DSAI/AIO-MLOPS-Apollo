#!/usr/bin/env bash
# Start the single g6.xlarge (NVIDIA L4, ~$1.05/h on-demand) and wait until the GPU is schedulable.
# Terraform ignores the node group's desired size, so this does not cause drift.
# Run gpu-down.sh when finished.
set -euo pipefail
CLUSTER=${CLUSTER:-apollo-mlops}; REGION=${REGION:-ap-southeast-2}; NG=${NG:-gpu-l4}

aws eks update-nodegroup-config --region "$REGION" --cluster-name "$CLUSTER" --nodegroup-name "$NG" \
  --scaling-config minSize=0,maxSize=1,desiredSize=1 --query 'update.status' --output text
echo "Waiting for the GPU node to join..."
until kubectl get nodes -l role=gpu -o name 2>/dev/null | grep -q node; do sleep 15; done
kubectl wait --for=condition=Ready node -l role=gpu --timeout=10m
echo "Waiting for the NVIDIA device plugin to advertise nvidia.com/gpu..."
until [ "$(kubectl get nodes -l role=gpu -o jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}')" = "1" ]; do sleep 10; done
kubectl get nodes -l role=gpu -L node.kubernetes.io/instance-type,topology.kubernetes.io/zone
echo "GPU node up at $(date '+%F %T') — billing until gpu-down.sh"
