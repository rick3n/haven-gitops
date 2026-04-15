# ODC-Noord

Note: https://fluxcd.io/flux/installation/configuration/openshift/

```
kubectl apply -k .
```

## Specifics

ODC-Noord provides Kubernetes clusters based on [Red Hat OpenShift](https://www.redhat.com/en/technologies/cloud-computing/openshift).
OpenShift provides a set of components out-of-the-box that may overlap with the Haven+ components.

### ingress

OpenShift provides an ingress controller with IngressClass `openshift-default`. Therefore, we don't install Istio Gateway.

### keycloak

OpenShift provides Keycloak out-of-the-box.

### pinniped

Pinniped is not necessary because OpenShift provides Kube API access with Keycloak.
