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
