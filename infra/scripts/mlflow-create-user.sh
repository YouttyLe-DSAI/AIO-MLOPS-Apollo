#!/usr/bin/env bash
# Create an MLflow (basic-auth) user with a random password and keep the credentials in the
# Kubernetes Secret mlflow/mlflow-user-<name>. Nothing is printed or written to disk.
#
# Usage:  infra/scripts/mlflow-create-user.sh <username> [<username> ...]
# Share:  kubectl -n mlflow get secret mlflow-user-<name> -o jsonpath='{.data.password}' | base64 -d
set -euo pipefail
[ $# -ge 1 ] || { echo "usage: $0 <username> [...]" >&2; exit 1; }

NS=mlflow
URL=${MLFLOW_URL:-$(kubectl -n "$NS" get ingress mlflow -o jsonpath='https://{.spec.rules[0].host}')}
ADMIN_USER=$(kubectl -n "$NS" get secret mlflow-admin -o jsonpath='{.data.username}' | base64 -d)
ADMIN_PASS=$(kubectl -n "$NS" get secret mlflow-admin -o jsonpath='{.data.password}' | base64 -d)

for user in "$@"; do
  if kubectl -n "$NS" get secret "mlflow-user-$user" >/dev/null 2>&1; then
    echo "$user: secret already exists, skipping"; continue
  fi
  pass=$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 20)
  code=$(curl -s -o /dev/null -w '%{http_code}' -u "$ADMIN_USER:$ADMIN_PASS" -X POST "$URL/api/2.0/mlflow/users/create" \
         -H 'Content-Type: application/json' -d "{\"username\":\"$user\",\"password\":\"$pass\"}")
  [ "$code" = "200" ] || { echo "$user: MLflow returned HTTP $code" >&2; exit 1; }
  kubectl -n "$NS" create secret generic "mlflow-user-$user" \
    --from-literal=username="$user" --from-literal=password="$pass" >/dev/null
  echo "$user: created (Secret $NS/mlflow-user-$user)"
done
