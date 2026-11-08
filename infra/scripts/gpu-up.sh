#!/usr/bin/env bash
# Start the single g6.xlarge (NVIDIA L4, ~$0.99/h on-demand in Seoul) and wait until the GPU is schedulable.
# Terraform ignores the node group's desired size, so this does not cause drift.
# If AWS cannot launch the instance (e.g. InsufficientInstanceCapacity) or the node does not join in time,
# the node group is scaled back to 0 so it cannot start later unnoticed.
# Run gpu-down.sh when finished.
set -euo pipefail
CLUSTER=${CLUSTER:-apollo-mlops}; REGION=${REGION:-ap-northeast-2}; NG=${NG:-gpu-l4}
JOIN_TIMEOUT_S=${JOIN_TIMEOUT_S:-600}
HERE=$(cd "$(dirname "$0")" && pwd)

abort() { echo "ERROR: $1" >&2; "$HERE/gpu-down.sh" >&2; exit 1; }

ASG=$(aws eks describe-nodegroup --region "$REGION" --cluster-name "$CLUSTER" --nodegroup-name "$NG" \
      --query 'nodegroup.resources.autoScalingGroups[0].name' --output text)
started=$(date -u +%Y-%m-%dT%H:%M:%S)

aws eks update-nodegroup-config --region "$REGION" --cluster-name "$CLUSTER" --nodegroup-name "$NG" \
  --scaling-config minSize=0,maxSize=1,desiredSize=1 --query 'update.status' --output text

echo "Waiting for the GPU node to join (timeout ${JOIN_TIMEOUT_S}s)..."
deadline=$(( $(date +%s) + JOIN_TIMEOUT_S ))
until kubectl get nodes -l role=gpu -o name 2>/dev/null | grep -q node; do
  # Only launch failures from this run count (older ones may still be listed).
  # (JMESPath cannot compare strings with >=, so the time filter is done in awk on UTC ISO timestamps.)
  failed=$(aws autoscaling describe-scaling-activities --region "$REGION" --auto-scaling-group-name "$ASG" --max-items 5 \
           --query "Activities[?StatusCode=='Failed'].[StartTime,StatusMessage]" --output text 2>/dev/null \
           | awk -F'\t' -v s="$started" '$1 >= s { print $2; exit }' || true)
  if [ -n "$failed" ]; then abort "AWS could not launch the GPU instance: ${failed:0:300}"; fi
  [ "$(date +%s)" -lt "$deadline" ] || abort "GPU node did not join within ${JOIN_TIMEOUT_S}s"
  sleep 15
done

kubectl wait --for=condition=Ready node -l role=gpu --timeout=10m
echo "Waiting for the NVIDIA device plugin to advertise nvidia.com/gpu..."
until [ "$(kubectl get nodes -l role=gpu -o jsonpath='{.items[0].status.allocatable.nvidia\.com/gpu}')" = "1" ]; do
  [ "$(date +%s)" -lt "$(( deadline + 600 ))" ] || abort "nvidia.com/gpu never became allocatable"
  sleep 10
done
kubectl get nodes -l role=gpu -L node.kubernetes.io/instance-type,topology.kubernetes.io/zone
echo "GPU node up at $(date '+%F %T') — billing until gpu-down.sh"
