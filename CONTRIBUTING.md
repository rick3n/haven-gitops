# Contributing to the GitOps-Flux project

First and foremost, thank you for considering contributing 🎉 We truly appreciate any feedback and improvements to this project. Please read this page carefully.

[[_TOC_]]

# Reporting Bugs
Encountered a bug? Then first check if there's an existing [open issue](https://gitlab.com/commonground/haven/havenplus/gitops-flux/-/issues). If not, create a [new issue](https://gitlab.com/commonground/haven/havenplus/gitops-flux/-/issues/new). 

# Adding Features
If you'd like to add a new feature or modify existing code, please first check if a [similar request](https://gitlab.com/commonground/haven/havenplus/gitops-flux/-/issues) already exists in our open issues catalog. If not, then create a [new issue](https://gitlab.com/commonground/haven/havenplus/gitops-flux/-/issues/new).

# Forking the repository
In order to contribute, you will need to [fork this project](https://gitlab.com/commonground/haven/havenplus/gitops-flux/-/forks/new). Once forked, you can clone the repository to your local machine. For more information about forking in Gitlab, please consult the [official docs](https://docs.gitlab.com/user/project/repository/forking_workflow/). 

# Commits

## Sign your commits
We strongly encourage signing your commits with a [GPG key](https://docs.gitlab.com/ee/user/project/repository/gpg_signed_commits/) or an [SSH key](https://docs.gitlab.com/ee/user/project/repository/signed_commits/ssh.html).

## Prepend your commit message with a type
For your commit messages, please adhere to the [Conventional Commits specification](https://www.conventionalcommits.org/). Ensure that the first word of your commit message is the type. The type must be one of the following:

**build**: Changes that affect the build system or external dependencies  
**chore**: Miscellaneous commits, e.g. modifying .gitignore or updating dependency versions  
**ci**: Changes to our CI configuration files and scripts  
**docs**: Documentation only changes  
**feat**: A new feature  
**fix**: A bug fix  
**perf**: A code change that improves performance  
**refactor**: A code change that neither fixes a bug nor adds a feature  
**revert**: Changes that revert other change  
**style**: Changes that do not affect the meaning of the code (white-space, formatting, missing semicolons, etc.)  
**test**: Adding missing tests or correcting existing tests    
  
Conventional commits are **enforced** in this project's CI pipeline for all Merge Requests. In case you also want to check this locally, then install the commitlint hook:
```
npm install  @commitlint/{config-conventional,cli}
echo 'npx commitlint --edit' >> .git/hooks/commit-msg
chmod +x .git/hooks/commit-msg
```


# Coding Standards

These conventions keep the GitOps tree predictable and Renovate-friendly. CI validates manifests with `./scripts/validate.sh`; run it before opening an MR. Make sure your editor respects the repo's `.editorconfig` (LF line endings, UTF-8, final newline, 2-space indent for YAML).

## Kustomize layout
- Every component uses a `base/` + `overlays/<cluster>/` layout. The `base/` holds environment-agnostic config; overlays apply per-cluster changes.
- Do not set `metadata.namespace` in a resource. Set `namespace:` in the overlay's `kustomization.yaml` instead.
- Pin Helm chart versions in `base/` only (`spec.chart.spec.version`).
- Use built-in transformers (`images:`, `replicas:`, `labels:`, `namespace:`) before reaching for `patches:`.
- For labels use the `labels:` field with `pairs:`. By default `pairs:` only decorates `metadata.labels`; add `includeSelectors: true` when the label must also become the workload selector.
- As a rule of thumb: if fewer than 3 patches are needed from an overlay to the base, use inline JSON 6902 patches in `kustomization.yaml`; otherwise use a strategic-merge patch file under a `patches/` subdirectory next to the `kustomization.yaml` that references it.
- Use the literal string `set-in-overlay` for base fields whose real value belongs in an overlay.

## Flux resources
- Cluster-level Flux `Kustomization`s live in `clusters/<cluster>/infrastructure/<component>.yaml`, point at `./infrastructure/<component>/.../overlays/<cluster>`, and set `prune: true` and `wait: true`.
- Split a component into `controller` and `config` Kustomizations when config must reconcile after the operator/CRDs are ready; express the order with `dependsOn`.
- Reconcile against the `flux-system` `GitRepository` with `serviceAccountName: kustomize-controller`.
- Reconciliation intervals follow the repo convention: `HelmRelease` `spec.interval: 30m`, `HelmRepository`/`GitRepository` sources longer (e.g. `24h` / `1h`). Don't set short intervals to "speed things up" — push a commit or trigger the `Receiver` instead.

## Drift detection & retries
- `prune: true` with `wait: true` lets the kustomize-controller continuously detect and correct drift back to Git state:
  ```yaml
  spec:
    prune: true
    wait: true
  ```
- Set `spec.driftDetection.mode` on each `HelmRelease` in its `base/`. Use `enabled` by default; use `disabled` only when a controller legitimately mutates its own resources at runtime (e.g. cert-manager).
- Let Flux recover from transient failures on its own. The standard pattern for HelmReleases is the Flux 2.8 retry strategy in `base/`:
  ```yaml
  install:
    strategy:
      name: RetryOnFailure
      retryInterval: 2m
  upgrade:
    strategy:
      name: RetryOnFailure
      retryInterval: 2m
  ```

# Submitting a Merge Request
Once ready, create a Merge Request targeting the upstream repository. Please ensure that the MR description and/or your commit message includes a [reference](https://docs.gitlab.com/user/project/issues/crosslinking_issues/) to the relevant GitLab issue that your MR resolves.

# Local Development
1. [Fork this repository](https://gitlab.com/commonground/haven/havenplus/gitops-flux/-/forks/new)
2. Install required dependencies
```
# in the root of this project:
mise trust
mise install
```
3. Copy `.env.example` to `.env` and add the URL of the fork you've just created. In Gitlab, this should be the URL under the `Code` button which states `Clone with HTTPS`.
4. Bootstrap FluxCD on a local cluster based on your fork:
```
task run-local
```

> Be aware that by default Flux will deploy _all_ services, which can consume a lot of resources. For development purposes, you should comment out any service you don't need in _clusters/local/infrastructure/kustomization.yaml_.

5. FluxCD will automatically pick up any changes you commit in your fork. In case you want to speed up this process, you can run:
```
flux reconcile source git flux-system
flux get all # you should see resources reconciling
```
6. Once ready, create a Merge Request with the upstream repository as a target. 
