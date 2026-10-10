#!/usr/bin/env bash
# Draait acm.py in de Open Zaak-pod met de secrets uit het cluster (ontwikkelen zonder Flux).
# Gebruik op havenpc: run-local.sh <catalogus|applicaties|demo|check|all>
set -euo pipefail
export KUBECONFIG=${KUBECONFIG:-$HOME/nixos_eigen_hardware1/haven/talos/kubeconfig}
HERE=$(cd "$(dirname "$0")" && pwd)
P=$(kubectl -n openzaak get pod -o name | grep -E 'pod/openzaak-[a-z0-9]+-[a-z0-9]+$' | head -1)
g() { kubectl -n openzaak get secret openzaak-koppeling -o jsonpath="{.data.$1}" 2>/dev/null | base64 -d || true; }
{ echo "$(g secret_openzaak)|$(g secret_loket)|$(g secret_behandeling)|$(g secret_forensisch)"; cat "$HERE/acm.py"; } | \
kubectl -n openzaak exec -i "$P" -- sh -c 'IFS="|" read -r OPENZAAK_SECRET LOKET_SECRET BEHANDELING_SECRET FORENSISCH_SECRET; export OPENZAAK_SECRET LOKET_SECRET BEHANDELING_SECRET FORENSISCH_SECRET; exec python - '"${1:-all}" "${2:-all}"
