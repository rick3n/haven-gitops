# havenpc — Haven-lab van 3n.nl

Overlay voor het Talos-cluster op de gaming-pc (zie
`rick3n/nixos_eigen_hardware1`, `haven/`). Afgeleid van `clusters/local`
(klein gedimensioneerd) met deze verschillen:

| Onderdeel | havenpc |
|---|---|
| Certificaten | eigen CA `haven-ca` via cert-manager; wildcard `*.haven.3n.nl` op de gateway |
| Gateway | Istio, vast MetalLB-adres **10.42.0.200**; DNS `*.haven.3n.nl A 10.42.0.200` (alleen tailnet) |
| Secrets | SOPS/age via Flux `decryption` (geen statische sealing key in git) |
| Storage | StorageClass `nfs` (csi-driver-nfs op de host) — nfs-server-provisioner uit |
| Hostnames | keycloak., grafana., headlamp., sealed-secrets.haven.3n.nl |

Flux wordt niet ge-bootstrapt (publieke repo, geen schrijftoegang nodig):

    flux install
    kubectl -n flux-system create secret generic sops-age \
      --from-file=age.agekey=/root/.config/sops/age/keys.txt
    flux create source git flux-system --url=https://github.com/rick3n/haven-gitops --branch=main
    flux create kustomization flux-system --source=GitRepository/flux-system \
      --path=./clusters/havenpc --prune=true --interval=10m

Upstream bijhouden: `git fetch upstream && git merge upstream/main`.

## Geheugenkrapte (tot de RAM-uitbreiding)

Met 32 GB op de host en 27 GiB aan VM's zitten de workers op de grens: één extra
job-pod van 150 MB was op 10 oktober genoeg voor node-brede page-cache thrash
(load 100+ op de host, etcd/kubelet-timeouts). Daarom bewust uit:
Kyverno background/reports/cleanup-controllers (alleen admission draait, policies op
`failurePolicy: Ignore`), kyverno-policy-reporter, en de metallb `frr-k8s`-DaemonSet.
Terugzetten na de uitbreiding naar 64 GB: `infrastructure/kyverno/controller/overlays/havenpc`,
`infrastructure/havenpc-base/metallb/helmrelease.yaml` en `clusters/havenpc/infrastructure/kyverno.yaml`.
