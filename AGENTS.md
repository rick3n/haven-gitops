# AGENTS.md

This file provides guidance to AI coding assistents when working with code in this repository.

## What this repository is

HavenPlus's GitOps-FluxCD reference implementation: a Kubernetes GitOps tree reconciled by FluxCD. There is no application source code here — the repo is entirely Kubernetes manifests, Kustomize overlays, and Helm chart references. Changes are made by editing YAML, not by writing/compiling code.

## Architecture

Every component's overlays include a `local` variant (for the Kind-based dev cluster bootstrapped by `task run-local`) and a `vanilla` variant (generic/production baseline for other clusters). `clusters/local/` wires up the `local` overlays. When adding a new overlay-consuming resource, mirror whichever pattern the sibling component already uses for both variants.

For details regarding kustomize layout and flux resources and other behavior, see the `Coding Standards` chapter in @CONTRIBUTING.md.

### Renovate

`renovate.json` drives automatic version bumps: the `flux` manager scans all `.yaml` files for chart/source versions, and a custom regex manager bumps entries in `.image-versions` (the list `task run-local` uses to pre-pull/pre-load images into Kind). Patch updates are disabled. Most `chore(deps)` commits in history are Renovate-authored — follow their existing message format when bumping versions by hand.


## Commands

```bash
mise trust && mise install     # install pinned CLI tools (flux2, kind, kubectl, kubeseal, task, velero, cloud-provider-kind)
task run-local                 # bootstrap a local Kind cluster and deploy the full stack via FluxCD
task uninstall                 # tear down the local Kind cluster
./scripts/validate.sh          # validate all Flux CRs and kustomize overlays against Flux OpenAPI schemas (requires yq, kustomize, kubeconform); this is what CI runs
```

Validating a single component during development (no need to run the full `validate.sh`):
```bash
kubectl kustomize infrastructure/<component>/overlays/<cluster> --load-restrictor=LoadRestrictionsNone
# for components split into controller/config (see below):
kubectl kustomize infrastructure/<component>/controller/overlays/<cluster> --load-restrictor=LoadRestrictionsNone
```

Once a local cluster is running:
```bash
flux get all                                       # check reconciliation status of all Flux resources
flux get kustomizations                            # check Kustomization status specifically
flux reconcile source git flux-system               # force-pull the latest commit instead of waiting on the interval
task port-forward-headlamp                          # access the Headlamp UI at localhost:4466
task get-headlamp-token                              # get an admin token for Headlamp
```

Commit messages must follow Conventional Commits. See @CONTRIBUTING.md for details. 
