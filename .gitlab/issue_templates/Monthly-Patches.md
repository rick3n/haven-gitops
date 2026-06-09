Once per month we update HavenPlus components to new versions, based on MR's that Renovate Bot created. These components are verified against a specific Kubernetes version. 

# Prep
- Read release notes for all components for which [Renovate has openend an MR](https://gitlab.com/commonground/haven/havenplus/gitops-flux/-/merge_requests/?sort=created_date&state=opened&author_username=group_3094865_bot_11065d24d60249032aac86922bc492d6&first_page_size=20).

# Actions
## Staging Cluster
- [ ] deploy and test component updates on the Cyso staging cluster
- [ ] upgrade to new K8S version if available

## Local Cluster
- [ ] deploy and test component updates on a local cluster, including new Kind / K8S version if available

## Core
- [ ] merge renovate MR's to the `main` branch of [gitops-flux core repo](https://gitlab.com/commonground/haven/havenplus/gitops-flux)

## Demo Environment (PDV-cluster)
- [ ] merge renovate MR's to the `main` branch of the pdv/demo-cluster and verify
- [ ] upgrade to new K8S version if available

# Verification list
> remove components which are not applicable for this monthly patch round
- [ ] alloy
- [ ] cert-manager
- [ ] cloudnative-pg
- [ ] cluster-resources (gateway api)
- [ ] eck-operator
- [ ] external-dns
- [ ] external-secrets
- [ ] falco
- [ ] flux-webhook-receiver
- [ ] garage
- [ ] grafana
- [ ] headlamp
- [ ] istio
- [ ] keycloak
- [ ] kube-state-metrics
- [ ] kyverno
- [ ] loki
- [ ] metallb
- [ ] mimir
- [ ] nfs-server-provisioner
- [ ] nfs-subdir-external-provisioner
- [ ] pinniped
- [ ] sealed-secrets
- [ ] tempo
- [ ] trivy-operator
- [ ] velero
