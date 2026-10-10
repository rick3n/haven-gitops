#!/usr/bin/env bash
# Draait acm.py ad hoc in een eigen, tijdelijke pod (zelfde image en secrets als de
# Flux-Job). Niet in de Open Zaak-webpod: die zit met uwsgi al tegen zijn geheugen-
# limiet en de kernel killt dan het script (exit 137).
# Gebruik op havenpc: run-local.sh <catalogus|applicaties|demo|check|all> [snelkoop|spoor|all]
set -euo pipefail
export KUBECONFIG=${KUBECONFIG:-$HOME/nixos_eigen_hardware1/haven/talos/kubeconfig}
HERE=$(cd "$(dirname "$0")" && pwd)
POD=acm-run-$$
cleanup() { kubectl -n openzaak delete pod "$POD" --wait=false >/dev/null 2>&1 || true; }
trap cleanup EXIT
kubectl -n openzaak apply -f - >/dev/null <<EOF
apiVersion: v1
kind: Pod
metadata: { name: $POD, namespace: openzaak, labels: { app.kubernetes.io/name: acm-run } }
spec:
  restartPolicy: Never
  ${ACM_NODE:+nodeSelector: { kubernetes.io/hostname: $ACM_NODE }}
  securityContext: { fsGroup: 1000, seccompProfile: { type: RuntimeDefault } }
  containers:
    - name: acm
      image: openzaak/open-zaak:1.30.0
      command: [ sleep, "900" ]
      env:
        - { name: OPENZAAK_URL, value: http://openzaak-nginx.openzaak.svc }
        - { name: OPENZAAK_HOST, value: openzaak.haven.3n.nl }
        - { name: OPENZAAK_SECRET, valueFrom: { secretKeyRef: { name: openzaak-koppeling, key: secret_openzaak } } }
        - { name: LOKET_SECRET, valueFrom: { secretKeyRef: { name: openzaak-koppeling, key: secret_loket, optional: true } } }
        - { name: BEHANDELING_SECRET, valueFrom: { secretKeyRef: { name: openzaak-koppeling, key: secret_behandeling, optional: true } } }
        - { name: FORENSISCH_SECRET, valueFrom: { secretKeyRef: { name: openzaak-koppeling, key: secret_forensisch, optional: true } } }
      resources: { requests: { cpu: 50m, memory: 128Mi }, limits: { memory: 256Mi } }
      securityContext: { allowPrivilegeEscalation: false, capabilities: { drop: [ "ALL" ] }, runAsNonRoot: true, runAsUser: 1000, seccompProfile: { type: RuntimeDefault } }
EOF
kubectl -n openzaak wait --for=condition=Ready "pod/$POD" --timeout=120s >/dev/null
kubectl -n openzaak exec -i "$POD" -- python - "${1:-all}" "${2:-all}" < "$HERE/acm.py"
