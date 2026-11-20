#!/usr/bin/env bash
# Release one MLflow Model version: run the release Job on the L4 and print the Serving image.
#
#   infra/release/run_release.sh <registered_model> <version>
#
# Needs kubectl access to the `release` namespace (the GitHub Actions role has exactly that) and
# AWS credentials (only to read the account id). Called by .github/workflows/release.yml; can also
# be run by hand. Writes image=<ref> and image_tag=<tag> to $GITHUB_OUTPUT when set.
set -euo pipefail

MODEL_NAME=${1:?usage: $0 <registered_model> <version>}
MODEL_VERSION=${2:?usage: $0 <registered_model> <version>}
NAMESPACE=${RELEASE_NAMESPACE:-release}
AWS_REGION=${AWS_REGION:-ap-northeast-2}
TIMEOUT_S=${RELEASE_TIMEOUT_S:-5400}
HERE=$(cd "$(dirname "$0")" && pwd)

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ECR_REPOSITORY_URI="${ACCOUNT}.dkr.ecr.${AWS_REGION}.amazonaws.com/${ECR_REPOSITORY:-resnet18-triton}"
SLUG=$(printf '%s-v%s' "$MODEL_NAME" "$MODEL_VERSION" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9-' '-' | cut -c1-40)
JOB_NAME="release-${SLUG%-}-$(date +%s)"
export JOB_NAME NAMESPACE MODEL_NAME MODEL_VERSION ECR_REPOSITORY_URI AWS_REGION

echo "::group::Release ${MODEL_NAME} v${MODEL_VERSION} -> ${ECR_REPOSITORY_URI} (Job ${JOB_NAME})"
kubectl -n "$NAMESPACE" create configmap release-scripts \
  --from-file="$HERE/fetch_model.py" --from-file="$HERE/build_engine.py" --from-file="$HERE/serving.Dockerfile" \
  --dry-run=client -o yaml | kubectl -n "$NAMESPACE" apply -f -
python3 - "$HERE/release-job.yaml" <<'EOF' | kubectl -n "$NAMESPACE" apply -f -
import os, string, sys
keys = ("JOB_NAME", "NAMESPACE", "MODEL_NAME", "MODEL_VERSION", "ECR_REPOSITORY_URI", "AWS_REGION")
print(string.Template(open(sys.argv[1]).read()).safe_substitute({k: os.environ[k] for k in keys}))
EOF
echo "::endgroup::"

# Wait for the Job, printing progress (GPU node scale-up + image pulls take several minutes).
deadline=$(( $(date +%s) + TIMEOUT_S )); last=""
while :; do
  succeeded=$(kubectl -n "$NAMESPACE" get job "$JOB_NAME" -o jsonpath='{.status.succeeded}')
  failed=$(kubectl -n "$NAMESPACE" get job "$JOB_NAME" -o jsonpath='{.status.failed}')
  [ "${succeeded:-0}" -ge 1 ] && break
  if [ "${failed:-0}" -ge 1 ] || [ "$(date +%s)" -ge "$deadline" ]; then
    echo "::group::Job logs"; kubectl -n "$NAMESPACE" logs "job/$JOB_NAME" --all-containers --prefix --tail=200 || true; echo "::endgroup::"
    kubectl -n "$NAMESPACE" describe job "$JOB_NAME" | tail -20
    echo "::error::release Job ${JOB_NAME} failed or timed out"; exit 1
  fi
  state=$(kubectl -n "$NAMESPACE" get pods -l job-name="$JOB_NAME" -o jsonpath='{range .items[0].status.initContainerStatuses[*]}{.name}={.state}{" "}{end}' 2>/dev/null \
          | sed -E 's/\{"(running|terminated|waiting)":[^ ]*/\1/g')
  phase=$(kubectl -n "$NAMESPACE" get pods -l job-name="$JOB_NAME" -o jsonpath='{.items[0].status.phase}' 2>/dev/null || true)
  now="pod=${phase:-pending-node} ${state}"
  [ "$now" != "$last" ] && echo "$(date -u +%H:%M:%S) ${now}" && last="$now"
  sleep 20
done

echo "::group::Job logs"; kubectl -n "$NAMESPACE" logs "job/$JOB_NAME" --all-containers --prefix --tail=400; echo "::endgroup::"
IMAGE=$(kubectl -n "$NAMESPACE" logs "job/$JOB_NAME" -c build-image | sed -n 's/^RELEASED_IMAGE=//p' | tail -1)
[ -n "$IMAGE" ] || { echo "::error::no RELEASED_IMAGE line in the Job logs"; exit 1; }
echo "Serving image: ${IMAGE}"
if [ -n "${GITHUB_OUTPUT:-}" ]; then
  { echo "image=${IMAGE}"; echo "image_tag=${IMAGE##*:}"; } >> "$GITHUB_OUTPUT"
fi
