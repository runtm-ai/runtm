# ---------------------------------------------------------------------------
# In-cluster setup: agent-sandbox controller, label allow-list, namespace,
# Pod Snapshot storage config and policy, Runtm's RBAC, preview router.
#
# Applied by the kubectl provider (server-side apply) through the cluster's
# DNS endpoint with the same Google credentials as the google provider: the
# machine running Terraform needs nothing but Terraform (no gcloud, kubectl
# or auth plugin). The DNS endpoint is Google IAM-authorized and serves a
# publicly trusted certificate, so no cluster CA is needed; the IP endpoint
# only admits Runtm.
#
# One apply creates the cluster and everything in it: lazy_load defers
# connecting until the first object is applied (plans never contact the
# cluster for a create), and apply_retry_count rides out what appears late
# (GKE installs the Pod Snapshot CRDs a few minutes after the cluster is
# RUNNING; a failed apply is retried for up to ~20 minutes).
# ---------------------------------------------------------------------------

data "google_client_config" "default" {}

provider "kubectl" {
  host              = "https://${google_container_cluster.sandboxes.control_plane_endpoints_config[0].dns_endpoint_config[0].endpoint}"
  token             = data.google_client_config.default.access_token
  load_config_file  = false
  lazy_load         = true
  apply_retry_count = 40
}

locals {
  # agent-sandbox controller release, vendored verbatim (kubernetes-sigs/agent-sandbox v1.0.9,
  # sandbox-with-extensions.yaml, sha256 4bf9ee5558e9a663f91cfa5963e96f2591bfaa1dcf79f03bfab65bc5d65cc699).
  agent_sandbox_docs = {
    for doc in split("\n---\n", file("${path.module}/k8s/agent-sandbox-v1.0.9.yaml")) :
    "${yamldecode(doc).kind}/${yamldecode(doc).metadata.name}" => doc
    if trimspace(doc) != ""
  }
  # Cluster-scoped objects (Namespace, CRDs, ClusterRoles) go first.
  agent_sandbox_cluster_kinds = ["Namespace", "CustomResourceDefinition", "ClusterRole", "ClusterRoleBinding"]

  # agent-sandbox v1.0.9 refuses SandboxClaim labels outside its allow-list
  # (default sandbox.users.io). Runtm labels paused pods runtm.com/* and pins
  # restores with the podsnapshot.gke.io/ps-name annotation. The controller
  # reads it at start: its pod template carries the list's hash, so a change
  # rolls it.
  agent_sandbox_allowed_label_domains = "runtm.com,podsnapshot.gke.io"

  agent_sandbox_controller = yamldecode(local.agent_sandbox_docs["Deployment/agent-sandbox-controller"])
}

resource "kubectl_manifest" "agent_sandbox_cluster" {
  for_each = { for k, v in local.agent_sandbox_docs : k => v if contains(local.agent_sandbox_cluster_kinds, split("/", k)[0]) }

  yaml_body         = each.value
  server_side_apply = true
  force_conflicts   = true

  lifecycle {
    replace_triggered_by = [google_container_cluster.sandboxes.id]
  }
}

resource "kubectl_manifest" "agent_sandbox" {
  for_each = {
    for k, v in local.agent_sandbox_docs : k => v
    if !contains(local.agent_sandbox_cluster_kinds, split("/", k)[0]) && k != "Deployment/agent-sandbox-controller"
  }

  yaml_body         = each.value
  server_side_apply = true
  force_conflicts   = true

  depends_on = [kubectl_manifest.agent_sandbox_cluster]

  lifecycle {
    replace_triggered_by = [google_container_cluster.sandboxes.id]
  }
}

resource "kubectl_manifest" "agent_sandbox_config" {
  yaml_body = yamlencode({
    apiVersion = "v1"
    kind       = "ConfigMap"
    metadata   = { name = "agent-sandbox-config", namespace = "agent-sandbox-system" }
    data       = { "allowed-label-domains" = local.agent_sandbox_allowed_label_domains }
  })
  server_side_apply = true
  force_conflicts   = true

  depends_on = [kubectl_manifest.agent_sandbox_cluster]

  lifecycle {
    replace_triggered_by = [google_container_cluster.sandboxes.id]
  }
}

# The provider waits for a Deployment's rollout.
resource "kubectl_manifest" "agent_sandbox_controller" {
  yaml_body = yamlencode(merge(local.agent_sandbox_controller, {
    spec = merge(local.agent_sandbox_controller.spec, {
      template = merge(local.agent_sandbox_controller.spec.template, {
        metadata = merge(local.agent_sandbox_controller.spec.template.metadata, {
          annotations = { "runtm.com/config-sha256" = sha256(local.agent_sandbox_allowed_label_domains) }
        })
      })
    })
  }))
  server_side_apply = true
  force_conflicts   = true

  depends_on = [kubectl_manifest.agent_sandbox, kubectl_manifest.agent_sandbox_config]

  lifecycle {
    replace_triggered_by = [google_container_cluster.sandboxes.id]
  }
}

# ---------------------------------------------------------------------------
# Runtm's objects.
# ---------------------------------------------------------------------------

locals {
  runtm_subject = [{ apiGroup = "rbac.authorization.k8s.io", kind = "User", name = local.runtm_sa_email }]

  runtm_objects = merge(
    {
      # Where Pod Snapshots are written: gs://<bucket>/podsnapshots/.
      # federatedP4SA = the GKE node service agent writes with short-lived,
      # path-scoped tokens; code inside a sandbox gets no bucket access.
      snapshot_storage = yamlencode({
        apiVersion = "podsnapshot.gke.io/v1"
        kind       = "PodSnapshotStorageConfig"
        metadata   = { name = "runtm-snapshots" }
        spec = {
          snapshotStorageConfig = {
            gcs = { bucket = google_storage_bucket.sandboxes.name, path = "podsnapshots", tokenSource = "federatedP4SA" }
          }
        }
      })
      # Root-filesystem snapshots, taken only when Runtm creates a
      # PodSnapshotManualTrigger (at pause), for pods Runtm labelled for it.
      # Grouped per session so a restore only ever picks that session's
      # snapshots. The workspace archive in fs-snapshots/ is the durable copy;
      # snapshots not restored for 90 days are deleted by GKE.
      snapshot_policy = yamlencode({
        apiVersion = "podsnapshot.gke.io/v1"
        kind       = "PodSnapshotPolicy"
        metadata   = { name = "runtm-rootfs", namespace = local.namespace }
        spec = {
          selector          = { matchLabels = { "runtm.com/snapshot" = "true", "runtm.com/snapshot-manual" = "true" } }
          storageConfigName = "runtm-snapshots"
          snapshotScope     = "rootfs-only"
          triggerConfig     = { type = "manual", postCheckpoint = "resume" }
          snapshotGroupingRules = {
            groupByLabelValue = { labels = ["runtm.com/session-id"] }
          }
          retentionConfig = { lastAccessTimeout = "90d" }
        }
      })

      # What Runtm may do in this cluster: run sandboxes in the `sandboxes`
      # namespace, and read what it needs to pick and heal snapshots. Bound to
      # your organization's Runtm service account only. No cluster-admin, no
      # Secrets.
      role = yamlencode({
        apiVersion = "rbac.authorization.k8s.io/v1"
        kind       = "Role"
        metadata   = { name = "runtm-hub", namespace = local.namespace }
        rules = [
          { apiGroups = ["extensions.agents.x-k8s.io"], resources = ["sandboxclaims"], verbs = ["get", "list", "watch", "create", "delete", "patch"] },
          { apiGroups = ["extensions.agents.x-k8s.io"], resources = ["sandboxtemplates", "sandboxwarmpools"], verbs = ["get", "create", "patch"] },
          { apiGroups = ["agents.x-k8s.io"], resources = ["sandboxes"], verbs = ["get"] },
          { apiGroups = [""], resources = ["pods"], verbs = ["get", "list", "delete"] },
          { apiGroups = [""], resources = ["pods/exec"], verbs = ["create", "get"] },
          { apiGroups = [""], resources = ["events"], verbs = ["list"] },
          { apiGroups = [""], resources = ["services"], verbs = ["get"] },
          { apiGroups = ["podsnapshot.gke.io"], resources = ["podsnapshots"], verbs = ["get", "list", "delete"] },
          { apiGroups = ["podsnapshot.gke.io"], resources = ["podsnapshotmanualtriggers"], verbs = ["create", "get", "delete"] },
          { apiGroups = ["podsnapshot.gke.io"], resources = ["podsnapshottokenrequests"], verbs = ["patch", "delete"] },
        ]
      })
      role_binding = yamlencode({
        apiVersion = "rbac.authorization.k8s.io/v1"
        kind       = "RoleBinding"
        metadata   = { name = "runtm-hub", namespace = local.namespace }
        roleRef    = { apiGroup = "rbac.authorization.k8s.io", kind = "Role", name = "runtm-hub" }
        subjects   = local.runtm_subject
      })
      # Node versions (to match a snapshot to the nodes it can restore on) and
      # the snapshot storage config (to check the setup).
      cluster_role = yamlencode({
        apiVersion = "rbac.authorization.k8s.io/v1"
        kind       = "ClusterRole"
        metadata   = { name = "runtm-hub" }
        rules = [
          { apiGroups = [""], resources = ["nodes"], verbs = ["get", "list"] },
          { apiGroups = ["podsnapshot.gke.io"], resources = ["podsnapshotstorageconfigs"], verbs = ["get"] },
        ]
      })
      cluster_role_binding = yamlencode({
        apiVersion = "rbac.authorization.k8s.io/v1"
        kind       = "ClusterRoleBinding"
        metadata   = { name = "runtm-hub" }
        roleRef    = { apiGroup = "rbac.authorization.k8s.io", kind = "ClusterRole", name = "runtm-hub" }
        subjects   = local.runtm_subject
      })
      # Read the agent-sandbox label allow-list (setup check).
      config_role = yamlencode({
        apiVersion = "rbac.authorization.k8s.io/v1"
        kind       = "Role"
        metadata   = { name = "runtm-hub", namespace = "agent-sandbox-system" }
        rules      = [{ apiGroups = [""], resources = ["configmaps"], verbs = ["get"] }]
      })
      config_role_binding = yamlencode({
        apiVersion = "rbac.authorization.k8s.io/v1"
        kind       = "RoleBinding"
        metadata   = { name = "runtm-hub", namespace = "agent-sandbox-system" }
        roleRef    = { apiGroup = "rbac.authorization.k8s.io", kind = "Role", name = "runtm-hub" }
        subjects   = local.runtm_subject
      })

      # Preview router -> <sandbox>.sandboxes.svc:<port>. It lives in
      # agent-sandbox-system with label app=sandbox-router because that is the
      # one ingress source the NetworkPolicy agent-sandbox v1.0.9 manages for
      # each sandbox admits (namespace agent-sandbox-system AND that label);
      # from `sandboxes` it is refused. The provider waits for its rollout.
      router = local.preview_enabled ? yamlencode(local.router_gateway_deployment) : yamlencode(local.router_edge_deployment)
    },

    # Runtm reads each node's Pod Snapshot agent bootstrap log (gkeVersion +
    # runscVersion) to retarget a snapshot after a GKE upgrade. Without it
    # Runtm restores paused sessions after a GKE upgrade from the workspace
    # archive instead.
    var.snapshot_log_access ? {
      heal_role = yamlencode({
        apiVersion = "rbac.authorization.k8s.io/v1"
        kind       = "Role"
        metadata   = { name = "runtm-hub", namespace = "gke-managed-pod-snapshots" }
        rules = [
          { apiGroups = [""], resources = ["pods"], verbs = ["list"] },
          { apiGroups = [""], resources = ["pods/log"], verbs = ["get"] },
        ]
      })
      heal_role_binding = yamlencode({
        apiVersion = "rbac.authorization.k8s.io/v1"
        kind       = "RoleBinding"
        metadata   = { name = "runtm-hub", namespace = "gke-managed-pod-snapshots" }
        roleRef    = { apiGroup = "rbac.authorization.k8s.io", kind = "Role", name = "runtm-hub" }
        subjects   = local.runtm_subject
      })
    } : {},

    # Runtm's edge: stock Caddy with router/Caddyfile.
    local.preview_enabled ? {} : {
      router_config = yamlencode({
        apiVersion = "v1"
        kind       = "ConfigMap"
        metadata   = { name = "sandbox-router", namespace = "agent-sandbox-system" }
        data       = { Caddyfile = local.router_caddyfile }
      })
    },

    # Previews on your own domain: a regional GKE Gateway (TLS terminated with
    # a Certificate Manager certificate) -> the router.
    local.preview_enabled ? {
      router_pdb = yamlencode({
        apiVersion = "policy/v1"
        kind       = "PodDisruptionBudget"
        metadata   = { name = "sandbox-router", namespace = "agent-sandbox-system" }
        spec       = { minAvailable = 1, selector = { matchLabels = { app = "sandbox-router" } } }
      })
      router_route = yamlencode({
        apiVersion = "gateway.networking.k8s.io/v1"
        kind       = "HTTPRoute"
        metadata   = { name = "sandbox-router", namespace = "agent-sandbox-system" }
        spec = {
          parentRefs = [{ name = "sandbox-router" }]
          hostnames  = ["*.${local.preview_domain}"]
          rules      = [{ backendRefs = [{ name = "sandbox-router", port = 80 }] }]
        }
      })
      # The router answers 200 only on this path (every other request needs a token).
      router_health_check = yamlencode({
        apiVersion = "networking.gke.io/v1"
        kind       = "HealthCheckPolicy"
        metadata   = { name = "sandbox-router", namespace = "agent-sandbox-system" }
        spec = {
          default   = { config = { type = "HTTP", httpHealthCheck = { requestPath = "/__runtm/healthz" } } }
          targetRef = { group = "", kind = "Service", name = "sandbox-router" }
        }
      })
      # Long-lived dev-server connections (WebSocket HMR, SSE) outlast the 30 s default.
      router_backend_policy = yamlencode({
        apiVersion = "networking.gke.io/v1"
        kind       = "GCPBackendPolicy"
        metadata   = { name = "sandbox-router", namespace = "agent-sandbox-system" }
        spec = {
          default = merge(
            { timeoutSec = 3600 },
            try(var.preview.security_policy, null) == null ? {} : { securityPolicy = var.preview.security_policy },
          )
          targetRef = { group = "", kind = "Service", name = "sandbox-router" }
        }
      })
    } : {},

    # Private ranges sandboxes may reach (sandbox_egress_allowed_cidrs).
    # Additive: agent-sandbox's per-sandbox NetworkPolicy blocks private
    # ranges, and NetworkPolicies allow the union of their rules. Selects
    # sandbox pods only (the label agent-sandbox sets), which that policy
    # already isolates.
    length(var.sandbox_egress_allowed_cidrs) == 0 ? {} : {
      egress_allowed = yamlencode({
        apiVersion = "networking.k8s.io/v1"
        kind       = "NetworkPolicy"
        metadata   = { name = "runtm-egress-allowed", namespace = local.namespace }
        spec = {
          podSelector = { matchExpressions = [{ key = "agents.x-k8s.io/sandbox-template-ref-hash", operator = "Exists" }] }
          policyTypes = ["Egress"]
          egress      = [{ to = [for cidr in var.sandbox_egress_allowed_cidrs : { ipBlock = { cidr = cidr } }] }]
        }
      })
    },
  )

  router_edge_deployment = {
    apiVersion = "apps/v1"
    kind       = "Deployment"
    metadata   = { name = "sandbox-router", namespace = "agent-sandbox-system", labels = { app = "sandbox-router" } }
    spec = {
      replicas = 1
      selector = { matchLabels = { app = "sandbox-router" } }
      template = {
        metadata = {
          labels = { app = "sandbox-router" }
          annotations = {
            # Restart the router when its config changes.
            "runtm.com/config-sha256"                        = sha256(local.router_caddyfile)
            "cluster-autoscaler.kubernetes.io/safe-to-evict" = "false"
          }
        }
        spec = {
          containers = [{
            name  = "caddy"
            image = local.router_image
            ports = [{ name = "http", containerPort = 8080 }]
            # Space-separated; trusted as proxies (router/Caddyfile).
            env            = [{ name = "RUNTM_INGRESS_CIDRS", value = join(" ", var.runtm_ingress_cidrs) }]
            readinessProbe = { tcpSocket = { port = "http" }, periodSeconds = 10 }
            resources      = { requests = { cpu = "250m", memory = "256Mi" } }
            volumeMounts   = [{ name = "config", mountPath = "/etc/caddy" }]
          }]
          volumes = [{ name = "config", configMap = { name = "sandbox-router" } }]
        }
      }
    }
  }

  # Runtm's preview proxy image in its customer mode: it checks Runtm's signed
  # preview token itself.
  router_gateway_deployment = !local.preview_enabled ? null : {
    apiVersion = "apps/v1"
    kind       = "Deployment"
    metadata   = { name = "sandbox-router", namespace = "agent-sandbox-system", labels = { app = "sandbox-router" } }
    spec = {
      replicas = 2
      selector = { matchLabels = { app = "sandbox-router" } }
      template = {
        metadata = {
          labels      = { app = "sandbox-router" }
          annotations = { "cluster-autoscaler.kubernetes.io/safe-to-evict" = "false" }
        }
        spec = {
          topologySpreadConstraints = [{
            maxSkew           = 1
            topologyKey       = "topology.kubernetes.io/zone"
            whenUnsatisfiable = "ScheduleAnyway"
            labelSelector     = { matchLabels = { app = "sandbox-router" } }
          }]
          containers = [{
            name  = "caddy"
            image = local.router_image
            # A moving tag: every (re)start runs the current router image.
            imagePullPolicy = "Always"
            ports           = [{ name = "http", containerPort = 8080 }]
            env = [
              # The image's config for customer clusters (the default is Runtm's hosted one).
              { name = "RUNTM_PROXY_MODE", value = "customer" },
              { name = "RUNTM_PREVIEW_DOMAIN", value = local.preview_domain },
              { name = "RUNTM_PREVIEW_DOMAIN_RE", value = replace(local.preview_domain, ".", "\\.") },
              { name = "RUNTM_PREVIEW_JWKS_URL", value = coalesce(var.runtm_preview_jwks_url, "${var.runtm_app_origin}/.well-known/runtm-preview-jwks.json") },
              { name = "RUNTM_APP_ORIGIN", value = var.runtm_app_origin },
              # The load balancer's proxies (proxy-only subnet): the only trusted proxies.
              { name = "RUNTM_PROXY_CIDRS", value = var.preview.proxy_only_subnet_cidr },
            ]
            readinessProbe = { httpGet = { path = "/__runtm/healthz", port = "http" }, periodSeconds = 10 }
            resources      = { requests = { cpu = "250m", memory = "256Mi" } }
          }]
        }
      }
    }
  }
}

locals {
  router_service_spec = {
    edge = {
      type           = "LoadBalancer"
      loadBalancerIP = local.router_address
      # Local: the router sees the real source address.
      externalTrafficPolicy    = "Local"
      loadBalancerSourceRanges = var.runtm_ingress_cidrs
      selector                 = { app = "sandbox-router" }
      ports                    = [{ name = "http", port = 80, targetPort = "http" }]
    }
    gateway = {
      type     = "ClusterIP"
      selector = { app = "sandbox-router" }
      ports    = [{ name = "http", port = 80, targetPort = "http" }]
    }
  }
}

resource "kubectl_manifest" "namespace" {
  yaml_body = yamlencode({
    apiVersion = "v1"
    kind       = "Namespace"
    metadata   = { name = local.namespace }
  })
  server_side_apply = true
  force_conflicts   = true

  lifecycle {
    replace_triggered_by = [google_container_cluster.sandboxes.id]
  }
}

resource "kubectl_manifest" "runtm" {
  for_each = local.runtm_objects

  yaml_body         = each.value
  server_side_apply = true
  force_conflicts   = true

  depends_on = [
    kubectl_manifest.namespace,
    kubectl_manifest.agent_sandbox_controller,
    google_storage_bucket_iam_member.gke_node_agent,
    google_storage_bucket_iam_member.gke_service_agent,
    google_artifact_registry_repository_iam_member.node_reader,
  ]

  lifecycle {
    replace_triggered_by = [google_container_cluster.sandboxes.id]
  }
}

# The router's Service: a LoadBalancer on the reserved address, reachable only
# from Runtm's egress addresses (Runtm's edge), or the Gateway's backend.
# Replaced rather than updated (switching modes changes its type), and deleted
# only once GKE has released its load balancer, so the address can go.
resource "kubectl_manifest" "router_service" {
  yaml_body = yamlencode({
    apiVersion = "v1"
    kind       = "Service"
    metadata   = { name = "sandbox-router", namespace = "agent-sandbox-system", labels = { app = "sandbox-router" } }
    spec       = local.router_service_spec[local.preview_enabled ? "gateway" : "edge"]
  })
  server_side_apply = true
  force_conflicts   = true
  force_new         = true
  wait              = true

  dynamic "wait_for" {
    for_each = local.preview_enabled ? [] : [1]
    content {
      field {
        key   = "status.loadBalancer.ingress.[0].ip"
        value = local.router_address
      }
    }
  }

  timeouts {
    create = "20m"
  }

  depends_on = [kubectl_manifest.runtm]

  lifecycle {
    replace_triggered_by = [google_container_cluster.sandboxes.id]
  }
}

resource "kubectl_manifest" "router_gateway" {
  count = local.preview_enabled ? 1 : 0

  yaml_body = yamlencode({
    apiVersion = "gateway.networking.k8s.io/v1"
    kind       = "Gateway"
    metadata   = { name = "sandbox-router", namespace = "agent-sandbox-system" }
    spec = {
      gatewayClassName = local.preview_internal ? "gke-l7-rilb" : "gke-l7-regional-external-managed"
      addresses        = [{ type = "NamedAddress", value = google_compute_address.preview[0].name }]
      listeners = [{
        name     = "https"
        protocol = "HTTPS"
        port     = 443
        hostname = "*.${local.preview_domain}"
        tls = {
          mode    = "Terminate"
          options = { "networking.gke.io/cert-manager-certs" = local.preview_cert_ref }
        }
        allowedRoutes = { namespaces = { from = "Same" } }
      }]
    }
  })
  server_side_apply = true
  force_conflicts   = true
  # Deleted only once GKE has released its load balancer, so the address can go.
  wait = true

  wait_for {
    condition {
      type   = "Programmed"
      status = "True"
    }
    field {
      key   = "status.addresses.[0].value"
      value = google_compute_address.preview[0].address
    }
  }

  timeouts {
    create = "25m"
    update = "25m"
  }

  depends_on = [
    kubectl_manifest.router_service,
    google_compute_subnetwork.proxy_only,
    google_compute_firewall.preview,
    google_certificate_manager_certificate.preview,
  ]

  lifecycle {
    replace_triggered_by = [google_container_cluster.sandboxes.id]
  }
}
