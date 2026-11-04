#!/usr/bin/env bash
# In-cluster setup for the Runtm sandbox cluster, run by main.tf
# (terraform_data local-exec). Idempotent; safe to re-run.
#
#   cluster-setup.sh apply    install agent-sandbox, apply Runtm's objects,
#                             wait for the preview router's load balancer
#   cluster-setup.sh destroy  delete the router Gateway and Service (frees
#                             their load balancer before the cluster is deleted)
#
# Inputs (environment): PROJECT_ID REGION CLUSTER, and for apply ROUTER_IP
# ROUTER_MODE (edge = a LoadBalancer Service for Runtm's edge, gateway = a GKE
# Gateway for previews on your own domain) AGENT_SANDBOX_URL
# AGENT_SANDBOX_SHA256 MANIFEST HEAL_RBAC.
# Needs gcloud (authenticated), kubectl, gke-gcloud-auth-plugin, curl.
# Reaches the cluster through its DNS endpoint, so it works from anywhere
# your gcloud identity may use the cluster (the IP endpoint only admits
# Runtm). Uses a throwaway kubeconfig; yours is not touched.
set -euo pipefail

mode=${1:?usage: cluster-setup.sh apply|destroy}
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
export KUBECONFIG="$work/kubeconfig"

log() { printf 'runtm cluster-setup: %s\n' "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# wait_for <seconds> <command...>: retry every 10 s until it succeeds;
# returns 1 if it has not after <seconds>.
wait_for() {
  local limit=$1 start=$SECONDS
  shift
  until "$@" >/dev/null 2>&1; do
    ((SECONDS - start < limit)) || return 1
    sleep 10
  done
}

for tool in gcloud kubectl gke-gcloud-auth-plugin curl; do
  command -v "$tool" >/dev/null || die "$tool is not installed (Cloud Shell has it)"
done

credentials() {
  gcloud container clusters get-credentials "$CLUSTER" \
    --project "$PROJECT_ID" --location "$REGION" --dns-endpoint --quiet
}

if [[ $mode == destroy ]]; then
  credentials || { log "cluster $CLUSTER not reachable; nothing to release"; exit 0; }
  # The Gateway's forwarding rule holds its address; no Gateway API (edge-only
  # stacks on an old cluster) is not an error.
  if kubectl api-resources --api-group=gateway.networking.k8s.io -o name 2>/dev/null | grep -q '^gateways'; then
    kubectl -n agent-sandbox-system delete httproute,gateway sandbox-router \
      --ignore-not-found --wait --timeout=10m \
      || log "WARNING: could not delete Gateway agent-sandbox-system/sandbox-router; if destroy then fails on the address, delete its forwarding rule and retry"
  fi
  kubectl -n agent-sandbox-system delete service sandbox-router \
    --ignore-not-found --wait --timeout=5m \
    || log "WARNING: could not delete Service agent-sandbox-system/sandbox-router; if destroy then fails on the address, delete its forwarding rule and retry"
  exit 0
fi
[[ $mode == apply ]] || die "unknown mode $mode"
credentials

# 1. agent-sandbox controller, pinned release, checksum-verified.
curl -fsSL "$AGENT_SANDBOX_URL" -o "$work/agent-sandbox.yaml"
if command -v sha256sum >/dev/null; then
  echo "$AGENT_SANDBOX_SHA256  $work/agent-sandbox.yaml" | sha256sum -c --quiet -
else
  echo "$AGENT_SANDBOX_SHA256  $work/agent-sandbox.yaml" | shasum -a 256 -c --quiet -
fi || die "agent-sandbox manifest checksum mismatch ($AGENT_SANDBOX_URL)"
log "installing agent-sandbox"
kubectl apply -f "$work/agent-sandbox.yaml"

# 2. Pod Snapshot CRDs: GKE installs them a minute or two after the cluster
#    is RUNNING.
for crd in podsnapshotstorageconfigs podsnapshotpolicies; do
  wait_for 900 kubectl get crd "$crd.podsnapshot.gke.io" \
    || die "CRD $crd.podsnapshot.gke.io did not appear (is Pod Snapshots enabled on the cluster?)"
  kubectl wait --for condition=established --timeout=120s "crd/$crd.podsnapshot.gke.io"
done

# 3. Runtm's objects (k8s/runtm.yaml.tftpl). Switching to a Gateway: delete the
#    edge router's LoadBalancer Service first, so its external load balancer
#    goes rather than lingering behind a ClusterIP Service.
if [[ ${ROUTER_MODE:-edge} == gateway ]] &&
  [[ $(kubectl -n agent-sandbox-system get service sandbox-router -o jsonpath='{.spec.type}' 2>/dev/null) == LoadBalancer ]]; then
  log "removing the edge router's LoadBalancer Service"
  kubectl -n agent-sandbox-system delete service sandbox-router --wait --timeout=5m
fi
# Back to the edge: the Gateway (and its load balancer, on the address
# Terraform is about to delete) goes.
if [[ ${ROUTER_MODE:-edge} == edge ]] &&
  kubectl -n agent-sandbox-system get gateway sandbox-router >/dev/null 2>&1; then
  log "removing the preview Gateway"
  kubectl -n agent-sandbox-system delete httproute,gateway,healthcheckpolicy,gcpbackendpolicy sandbox-router \
    --ignore-not-found --wait --timeout=10m
  kubectl -n agent-sandbox-system delete poddisruptionbudget sandbox-router --ignore-not-found
fi
log "applying Runtm objects"
printf '%s\n' "$MANIFEST" | kubectl apply -f -

# 4. The controller reads its label allow-list at start.
kubectl -n agent-sandbox-system rollout restart deployment/agent-sandbox-controller
kubectl -n agent-sandbox-system rollout status deployment/agent-sandbox-controller --timeout=15m

# 5. Read access to the Pod Snapshot agents' logs (GKE-managed namespace).
#    Optional: without it Runtm restores paused sessions after a GKE upgrade
#    from the workspace archive instead.
wait_for 600 kubectl get namespace gke-managed-pod-snapshots || true
printf '%s\n' "$HEAL_RBAC" | kubectl apply -f - \
  || log "WARNING: could not grant Runtm read access in gke-managed-pod-snapshots (see k8s/heal-rbac.yaml.tftpl); continuing"

# 6. Preview router up, on the reserved address.
kubectl -n agent-sandbox-system rollout status deployment/sandbox-router --timeout=15m
if [[ ${ROUTER_MODE:-edge} == gateway ]]; then
  gw_ready() {
    [[ $(kubectl -n agent-sandbox-system get gateway sandbox-router \
      -o jsonpath='{.status.conditions[?(@.type=="Programmed")].status}') == True ]] &&
      [[ $(kubectl -n agent-sandbox-system get gateway sandbox-router \
        -o jsonpath='{.status.addresses[0].value}') == "$ROUTER_IP" ]]
  }
  wait_for 1200 gw_ready || die "the preview Gateway was not programmed on $ROUTER_IP (kubectl -n agent-sandbox-system describe gateway sandbox-router)"
else
  lb_ip() {
    [[ $(kubectl -n agent-sandbox-system get service sandbox-router \
      -o jsonpath='{.status.loadBalancer.ingress[0].ip}') == "$ROUTER_IP" ]]
  }
  wait_for 900 lb_ip || die "the router load balancer did not come up on $ROUTER_IP"
fi
log "done"
