# Runtm Cloud - Google Cloud sandboxes integration (customer side).
#
# The GCP twin of runtm/terraform/aws-sandbox and runtm/terraform/azure-sandbox.
# It creates, in YOUR project: a GKE Autopilot cluster that runs each sandbox
# as a gVisor pod (agent-sandbox controller, Pod Snapshots on), its network
# with Cloud NAT (or joins yours), a bucket for workspace archives and
# snapshots, an Artifact Registry repository for the sandbox image, a preview
# router (reachable only from Runtm, or on your own domain behind a GKE
# Gateway), and the grants your organization's Runtm service account needs.
# Runtm gets no project-wide role: it acts through Kubernetes RBAC in the
# cluster and through grants on the bucket and the repository.

locals {
  # The Runtm organization is the tenant. Every name below carries a slug
  # DERIVED from the org id (the first 12 hex chars of sha256, the slug the
  # organization has on AWS and Azure), so several organizations, and several
  # stacks of one organization told apart by `suffix`, share one project
  # without a name collision. cloud-api computes the same names from the
  # cluster resource name the customer pastes
  # (backend/app/services/gcp_sandbox_connections.py: gcp_resource_names).
  slug  = substr(sha256(var.runtm_organization_id), 0, 12)
  label = var.suffix != "" ? "${var.suffix}-${local.slug}" : local.slug

  # The service account id (30 chars) cannot carry the label and the region,
  # and the bucket name is global and outlives the stack (force_destroy =
  # false): both carry a 9-hex discriminator of (org, suffix, region, project)
  # instead, Azure's discriminator plus the project.
  disc = substr(sha256("${var.runtm_organization_id}|${var.suffix}|${var.region}|${var.project_id}"), 0, 9)

  # Identity Runtm presents: one Google service account per organization, its
  # email DERIVED from the org id with the formula cloud-api uses
  # (backend/app/services/aws_org_identity.py: "runtm-aws-" + sha256(org)[:20],
  # 30 chars = GCP's account-id maximum). Same account the AWS module trusts.
  runtm_sa_email  = "runtm-aws-${substr(sha256(var.runtm_organization_id), 0, 20)}@${var.runtm_google_project}.iam.gserviceaccount.com"
  runtm_sa_member = "serviceAccount:${local.runtm_sa_email}"

  # Name formulas and worst-case lengths (suffix 10, slug 12, region 23
  # "northamerica-northeast1"); limits in the README table. The region is in a
  # name only where the name's scope spans regions (the network, the node
  # service account, the bucket); regional resources and the cluster (named
  # within its location) do not need it.
  cluster_name   = "runtm-sandbox-${local.label}"               # 14+23 = 37 <= 40 (GKE)
  network_name   = "runtm-sandbox-${local.label}-${var.region}" # 14+23+1+23 = 61 <= 63
  subnet_name    = "runtm-sandbox-${local.label}"               # 37 <= 63
  nat_name       = "runtm-sandbox-nat-${local.label}"           # 18+23 = 41 <= 63
  router_ip_name = "runtm-sandbox-preview-${local.label}"       # 22+23 = 45 <= 63
  ar_repo_id     = "runtm-sandbox-${local.label}"               # 37 <= 63
  node_sa_id     = "rtm-node-${local.slug}${local.disc}"        # 9+12+9 = 30 <= 30 (binding)
  bucket_name    = "runtm-sandbox-${local.label}-${local.disc}" # 14+23+1+9 = 47 <= 63
  namespace      = "sandboxes"

  labels = merge(var.labels, {
    "runtm-slug"  = local.slug
    "runtm-label" = local.label
  })
}

# ---------------------------------------------------------------------------
# Which network: created (default), the module's subnet in your VPC, or your
# subnet. Names, ids and self links are all accepted for `network` and
# `subnetwork`; the regexes split them into project / region / name.
# ---------------------------------------------------------------------------

locals {
  network_project = coalesce(var.network_project_id, var.project_id)
  create_subnet   = var.subnetwork == null
  create_network  = local.create_subnet && var.network == null

  network_ref = var.network == null ? null : regex(
    "^(?:(?:https://www\\.googleapis\\.com/compute/v1/)?projects/(?P<project>[^/]+)/global/networks/)?(?P<name>[a-z](?:[-a-z0-9]*[a-z0-9])?)$",
    var.network,
  )
  subnetwork_ref = var.subnetwork == null ? null : regex(
    "^(?:(?:https://www\\.googleapis\\.com/compute/v1/)?projects/(?P<project>[^/]+)/regions/(?P<region>[^/]+)/subnetworks/)?(?P<name>[a-z](?:[-a-z0-9]*[a-z0-9])?)$",
    var.subnetwork,
  )

  # Project of the VPC (the Shared VPC host when it is not yours).
  vpc_project = (
    local.create_network ? var.project_id
    : local.create_subnet ? coalesce(local.network_ref.project, local.network_project)
    : coalesce(local.subnetwork_ref.project, local.network_project)
  )

  network_id = (
    local.create_network ? google_compute_network.sandboxes[0].id
    : local.create_subnet ? data.google_compute_network.existing[0].id
    : data.google_compute_subnetwork.existing[0].network
  )
  subnetwork_id = local.create_subnet ? google_compute_subnetwork.sandboxes[0].id : data.google_compute_subnetwork.existing[0].id
  # Pod range (the preview firewall rule's destination).
  pods_cidr = local.create_subnet ? var.pods_cidr : one([
    for r in data.google_compute_subnetwork.existing[0].secondary_ip_range : r.ip_cidr_range
    if r.range_name == var.pods_range_name
  ])
}

# No depends_on on the API enablement: it would defer these reads (and the
# subnet checks on the cluster) to apply time. A network or zone you already
# have means its API is already on.
data "google_compute_network" "existing" {
  count   = local.create_subnet && var.network != null ? 1 : 0
  project = local.vpc_project
  name    = local.network_ref.name
}

data "google_compute_subnetwork" "existing" {
  count   = local.create_subnet ? 0 : 1
  project = local.vpc_project
  region  = coalesce(local.subnetwork_ref.region, var.region)
  name    = local.subnetwork_ref.name
}

# ---------------------------------------------------------------------------
# Preview router: Runtm's edge (default) or your own domain (`preview`).
# ---------------------------------------------------------------------------

locals {
  preview_enabled   = var.preview != null
  preview_internal  = local.preview_enabled && try(var.preview.exposure, "") == "internal"
  preview_domain    = local.preview_enabled ? var.preview.domain : null
  preview_cert      = local.preview_enabled ? var.preview.certificate : null
  preview_cert_mode = local.preview_enabled ? local.preview_cert.mode : null
  # A Google-managed certificate is validated by a DNS authorization unless a private CA
  # (issuance config) issues it.
  preview_dns_auth = local.preview_cert_mode == "google_managed" && try(local.preview_cert.issuance_config, null) == null

  # Regional address names are unique per project and region; the Gateway's differs from the
  # edge router's so switching modes can create one before deleting the other.
  preview_address_name = "runtm-sandbox-gw-${local.label}"    # 17+23 = 40 <= 63
  preview_cert_name    = "runtm-sandbox-${local.label}"       # 37 <= 63
  proxy_only_name      = "runtm-sandbox-proxy-${local.label}" # 20+23 = 43 <= 63
  # Firewall rules are global: the region keeps two regional stacks apart.
  preview_fw_name = "rtm-sbx-gw-${local.label}-${var.region}" # 11+23+1+23 = 58 <= 63
  # Name the Gateway's TLS option takes: the certificate's last path segment.
  preview_cert_ref = (
    !local.preview_enabled ? null
    : local.preview_cert_mode == "certificate_manager" ? element(split("/", local.preview_cert.certificate), length(split("/", local.preview_cert.certificate)) - 1)
    : local.preview_cert_name
  )
  preview_zone_ref = local.preview_enabled && try(var.preview.dns_zone, null) != null ? regex(
    "^(?:projects/(?P<project>[^/]+)/managedZones/)?(?P<name>[a-z0-9][-a-z0-9]*)$",
    var.preview.dns_zone,
  ) : null

  # Runtm's preview proxy image, the one Runtm runs for its own sandboxes, from Runtm's
  # public repository (it holds only this image). It carries the router's config for both
  # modes (RUNTM_PROXY_MODE edge or customer, set in k8s.tf); this module carries none.
  router_image = coalesce(var.router_image, "us-west1-docker.pkg.dev/kuimjyxqkrbfvalw-public/runtm-public/sandbox-preview-proxy:latest")
  # The router's address: the edge load balancer's, or the Gateway's.
  router_address = local.preview_enabled ? google_compute_address.preview[0].address : google_compute_address.router[0].address
}

data "google_project" "this" {
  project_id = var.project_id

  depends_on = [google_project_service.this]
}

# ---------------------------------------------------------------------------
# APIs (twin of the Azure template's resource-provider registration). Never
# disabled on destroy: other workloads in the project may use them.
# ---------------------------------------------------------------------------

resource "google_project_service" "this" {
  for_each = toset([
    "container.googleapis.com",
    "compute.googleapis.com",
    "storage.googleapis.com",
    "artifactregistry.googleapis.com",
    "iam.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ])

  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

# ---------------------------------------------------------------------------
# Network: one subnet with explicit pod/service ranges (so GKE adds none that
# a later plan would try to remove), Private Google Access on (Artifact
# Registry, Cloud Storage). Nodes have no external IP; Cloud NAT gives them
# and the sandboxes outbound internet (image pulls, package installs).
# ---------------------------------------------------------------------------

resource "google_compute_network" "sandboxes" {
  count = local.create_network ? 1 : 0

  project                 = var.project_id
  name                    = local.network_name
  auto_create_subnetworks = false

  depends_on = [google_project_service.this]
}

# In a VPC the module creates, or in yours (`network`). Skipped with `subnetwork`.
resource "google_compute_subnetwork" "sandboxes" {
  count = local.create_subnet ? 1 : 0

  project                  = local.vpc_project
  name                     = local.subnet_name
  region                   = var.region
  network                  = local.network_id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true

  secondary_ip_range {
    range_name    = var.pods_range_name
    ip_cidr_range = var.pods_cidr
  }

  secondary_ip_range {
    range_name    = var.services_range_name
    ip_cidr_range = var.services_cidr
  }
}

resource "google_compute_router" "nat" {
  count = var.nat == "create" ? 1 : 0

  project = local.vpc_project
  name    = local.nat_name
  region  = var.region
  network = local.network_id
}

# Same port settings as Runtm's own cluster NAT: ports per node on demand
# (many sandboxes share a node) and a 1 h idle timeout so quiet long-lived
# connections are not cut. In your VPC it covers only the sandbox subnet, so
# it never claims subnets another NAT already serves (a subnet range belongs
# to one NAT); if yours already covers this subnet, use nat = "existing".
resource "google_compute_router_nat" "nat" {
  count = var.nat == "create" ? 1 : 0

  project                            = google_compute_router.nat[0].project
  name                               = local.nat_name
  router                             = google_compute_router.nat[0].name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = local.create_network ? "ALL_SUBNETWORKS_ALL_IP_RANGES" : "LIST_OF_SUBNETWORKS"

  dynamic "subnetwork" {
    for_each = local.create_network ? [] : [local.subnetwork_id]
    content {
      name                    = subnetwork.value
      source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
    }
  }

  enable_dynamic_port_allocation      = true
  enable_endpoint_independent_mapping = false
  min_ports_per_vm                    = 256
  max_ports_per_vm                    = 65536
  tcp_established_idle_timeout_sec    = 3600

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# Address of the preview router's load balancer when Runtm's edge reaches it
# (agent-sandbox-system/sandbox-router). With `preview`, google_compute_address.preview.
resource "google_compute_address" "router" {
  count = local.preview_enabled ? 0 : 1

  project = var.project_id
  name    = local.router_ip_name
  region  = var.region

  depends_on = [google_project_service.this]
}

# ---------------------------------------------------------------------------
# Node service account: logs/metrics (Google's minimum node role) and pulls
# from the sandbox image repository. Code in a sandbox (gVisor, Workload
# Identity on) never sees it.
# ---------------------------------------------------------------------------

resource "google_service_account" "node" {
  project      = var.project_id
  account_id   = local.node_sa_id
  display_name = "Runtm sandbox GKE nodes (${local.label}, ${var.region})"
  description  = "Node identity of the Runtm sandbox cluster."

  depends_on = [google_project_service.this]
}

resource "google_project_iam_member" "node" {
  project = var.project_id
  role    = "roles/container.defaultNodeServiceAccount"
  member  = "serviceAccount:${google_service_account.node.email}"
}

# ---------------------------------------------------------------------------
# Cluster: GKE Autopilot on the RAPID channel (Pod Snapshots need a recent
# GKE), Pod Snapshots enabled at creation, private nodes. The Kubernetes API
# answers on its IP endpoint only to Runtm's egress addresses; the DNS
# endpoint (Google IAM-authorized) is how this module's kubectl provider
# reaches it (k8s.tf).
# ---------------------------------------------------------------------------

resource "google_container_cluster" "sandboxes" {
  project  = var.project_id
  name     = local.cluster_name
  location = var.region

  enable_autopilot = true

  network    = local.network_id
  subnetwork = local.subnetwork_id

  ip_allocation_policy {
    cluster_secondary_range_name  = var.pods_range_name
    services_secondary_range_name = var.services_range_name
  }

  release_channel {
    channel = "RAPID"
  }

  addons_config {
    pod_snapshot_config {
      enabled = true
    }
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
  }

  # control_plane_access = "ip": the IP endpoint, admitting Runtm's addresses only.
  # "dns": no IP endpoint; Runtm, like this module (k8s.tf), uses the DNS one.
  dynamic "master_authorized_networks_config" {
    for_each = var.control_plane_access == "ip" ? [1] : []
    content {
      dynamic "cidr_blocks" {
        for_each = var.runtm_ingress_cidrs
        content {
          cidr_block   = cidr_blocks.value
          display_name = "runtm"
        }
      }
      gcp_public_cidrs_access_enabled = false
    }
  }

  control_plane_endpoints_config {
    dns_endpoint_config {
      allow_external_traffic = true
    }
    dynamic "ip_endpoints_config" {
      for_each = var.control_plane_access == "dns" ? [1] : []
      content {
        enabled = false
      }
    }
  }

  # Previews on your own domain are served by a GKE Gateway.
  dynamic "gateway_api_config" {
    for_each = local.preview_enabled ? [1] : []
    content {
      channel = "CHANNEL_STANDARD"
    }
  }

  cluster_autoscaling {
    auto_provisioning_defaults {
      service_account = google_service_account.node.email
      oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    }
  }

  resource_labels = local.labels

  # `terraform destroy` removes the cluster (paused sessions stay in the
  # bucket, which is never deleted while it has objects).
  deletion_protection = false

  lifecycle {
    precondition {
      condition     = local.create_subnet || data.google_compute_subnetwork.existing[0].region == var.region
      error_message = "subnetwork must be in region ${var.region}."
    }
    precondition {
      condition     = local.create_subnet || data.google_compute_subnetwork.existing[0].private_ip_google_access
      error_message = "subnetwork needs Private Google Access on: nodes pull the sandbox image from Artifact Registry and write snapshots to Cloud Storage over it."
    }
    precondition {
      condition = local.create_subnet || alltrue([
        for name in [var.pods_range_name, var.services_range_name] :
        contains([for r in data.google_compute_subnetwork.existing[0].secondary_ip_range : r.range_name], name)
      ])
      error_message = "subnetwork must have secondary ranges named ${var.pods_range_name} (pods) and ${var.services_range_name} (Services); set pods_range_name / services_range_name to yours."
    }
  }

  depends_on = [
    google_project_iam_member.node,
    google_compute_router_nat.nat,
  ]
}

# ---------------------------------------------------------------------------
# Bucket: workspace archives (fs-snapshots/), Pod Snapshots (podsnapshots/)
# and runtm/cluster.json. Pod Snapshots require hierarchical namespace (and so
# uniform access), the cluster's region, and soft delete off (their parallel
# composite uploads would otherwise bill every deleted temporary part).
# force_destroy = false is the twin of CloudFormation's Retain: a non-empty
# bucket is never deleted by destroy.
# ---------------------------------------------------------------------------

resource "google_storage_bucket" "sandboxes" {
  project  = var.project_id
  name     = local.bucket_name
  location = upper(var.region)

  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  hierarchical_namespace {
    enabled = true
  }

  soft_delete_policy {
    retention_duration_seconds = 0
  }

  # Paused-session workspace archives. Matches Runtm's paused-session
  # retention. Template archives (fs-snapshots/templates/) never expire.
  lifecycle_rule {
    condition {
      age            = var.session_artifact_retention_days
      matches_prefix = ["fs-snapshots/sessions/"]
    }
    action {
      type = "Delete"
    }
  }

  # Copies of snapshot metadata Runtm keeps before retargeting a snapshot to
  # new nodes. The snapshots themselves are deleted by GKE (policy
  # runtm-rootfs, 90 days without a restore).
  lifecycle_rule {
    condition {
      age            = 30
      matches_prefix = ["podsnapshots/backup/"]
    }
    action {
      type = "Delete"
    }
  }

  labels = merge(local.labels, { "runtm-component" = "sandbox-artifacts" })

  depends_on = [google_project_service.this]
}

# Pod Snapshots with tokenSource federatedP4SA: the GKE node service agent
# writes snapshots (it mints the short-lived per-path tokens), the GKE
# service agent deletes them (retention). Google creates both agents with
# the GKE API; the grants wait for the cluster so they surely exist.
resource "google_storage_bucket_iam_member" "gke_node_agent" {
  bucket = google_storage_bucket.sandboxes.name
  role   = "roles/storage.admin"
  member = "serviceAccount:service-${data.google_project.this.number}@gcp-sa-gkenode.iam.gserviceaccount.com"

  depends_on = [google_container_cluster.sandboxes]
}

resource "google_storage_bucket_iam_member" "gke_service_agent" {
  bucket = google_storage_bucket.sandboxes.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:service-${data.google_project.this.number}@container-engine-robot.iam.gserviceaccount.com"

  depends_on = [google_container_cluster.sandboxes]
}

# ---------------------------------------------------------------------------
# Artifact Registry: Runtm copies its sandbox image here (twin of Azure's
# ACR); cluster nodes pull it.
# ---------------------------------------------------------------------------

resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = local.ar_repo_id
  format        = "DOCKER"
  description   = "Runtm sandbox images"
  labels        = merge(local.labels, { "runtm-component" = "sandbox-images" })

  depends_on = [google_project_service.this]
}

resource "google_artifact_registry_repository_iam_member" "node_reader" {
  project    = var.project_id
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.reader"
  member     = "serviceAccount:${google_service_account.node.email}"
}

# ---------------------------------------------------------------------------
# What Runtm may do (twin of the AWS inline access policy / Azure's role
# assignments). Your organization's Runtm service account must exist before
# these grants (Google refuses a binding to an unknown account): opening the
# Runtm connect dialog creates it. In the cluster it gets the Kubernetes
# RBAC in k8s.tf; in the project, only these grants.
# ---------------------------------------------------------------------------

# Workspace archives, snapshot metadata and runtm/cluster.json (read, write,
# delete objects; Runtm signs upload/download URLs as its own account).
resource "google_storage_bucket_iam_member" "runtm_objects" {
  bucket = google_storage_bucket.sandboxes.name
  role   = "roles/storage.objectAdmin"
  member = local.runtm_sa_member
}

# Push the sandbox image (twin of the Azure registry import).
resource "google_artifact_registry_repository_iam_member" "runtm_writer" {
  project    = var.project_id
  location   = google_artifact_registry_repository.images.location
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = local.runtm_sa_member
}

# control_plane_access = "dns": the DNS endpoint checks Google IAM
# (container.clusters.connect) before Kubernetes RBAC applies. clusterViewer is
# the smallest predefined role carrying it: cluster metadata get/list plus
# connect, nothing inside the cluster (k8s.tf's RBAC still bounds that). The IP
# endpoint needs no IAM grant, so `ip` keeps none.
resource "google_project_iam_member" "runtm_cluster_connect" {
  count = var.control_plane_access == "dns" ? 1 : 0

  project = var.project_id
  role    = "roles/container.clusterViewer"
  member  = local.runtm_sa_member
}

# ---------------------------------------------------------------------------
# Previews on your own domain (`preview`): a regional GKE Gateway (internal or
# external Application Load Balancer) in front of the router, TLS terminated at
# the load balancer with a Certificate Manager certificate, and optionally the
# DNS records. The router (the preview proxy image in its customer mode) checks
# Runtm's preview token itself.
# ---------------------------------------------------------------------------

# The Envoy proxies of a regional load balancer run in this subnet; a VPC has
# at most one active per region, so in your VPC it may already exist.
resource "google_compute_subnetwork" "proxy_only" {
  count = local.preview_enabled && try(var.preview.proxy_only_subnet, "") == "create" ? 1 : 0

  project       = local.vpc_project
  name          = local.proxy_only_name
  region        = var.region
  network       = local.network_id
  ip_cidr_range = var.preview.proxy_only_subnet_cidr
  purpose       = "REGIONAL_MANAGED_PROXY"
  role          = "ACTIVE"
}

# The Gateway's address: internal (in the sandbox subnet, reachable from your
# network) or regional external (Standard tier, what GKE's regional external
# Gateway uses).
resource "google_compute_address" "preview" {
  count = local.preview_enabled ? 1 : 0

  project      = var.project_id
  name         = local.preview_address_name
  region       = var.region
  address_type = local.preview_internal ? "INTERNAL" : "EXTERNAL"
  purpose      = local.preview_internal ? "SHARED_LOADBALANCER_VIP" : null
  subnetwork   = local.preview_internal ? local.subnetwork_id : null
  network_tier = local.preview_internal ? null : "STANDARD"
  labels       = local.labels

  depends_on = [google_project_service.this]
}

# The load balancer's proxies and Google's health checkers -> the router pods
# (port 8080 on the pod range: the Gateway targets pods directly).
resource "google_compute_firewall" "preview" {
  count = local.preview_enabled && var.manage_firewall_rules ? 1 : 0

  project            = local.vpc_project
  name               = local.preview_fw_name
  network            = local.network_id
  direction          = "INGRESS"
  source_ranges      = [var.preview.proxy_only_subnet_cidr, "35.191.0.0/16", "130.211.0.0/22"]
  destination_ranges = [local.pods_cidr]

  allow {
    protocol = "tcp"
    ports    = ["8080"]
  }
}

resource "google_project_service" "preview" {
  for_each = local.preview_enabled ? toset(concat(
    local.preview_cert_mode == "certificate_manager" ? [] : ["certificatemanager.googleapis.com"],
    try(var.preview.dns_zone, null) == null ? [] : ["dns.googleapis.com"],
  )) : toset([])

  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

resource "google_certificate_manager_dns_authorization" "preview" {
  count = local.preview_dns_auth ? 1 : 0

  project     = var.project_id
  name        = local.preview_cert_name
  location    = var.region
  domain      = local.preview_domain
  type        = "PER_PROJECT_RECORD"
  description = "Runtm sandbox previews (*.${local.preview_domain})"
  labels      = local.labels

  depends_on = [google_project_service.preview]
}

# google_managed: *.<domain>, issued by Google (DNS authorization) or your
# private CA (issuance_config). pem: your certificate, uploaded.
resource "google_certificate_manager_certificate" "preview" {
  count = contains(["google_managed", "pem"], coalesce(local.preview_cert_mode, "none")) ? 1 : 0

  project     = var.project_id
  name        = local.preview_cert_name
  location    = var.region
  description = "Runtm sandbox previews (*.${local.preview_domain})"
  labels      = local.labels

  dynamic "managed" {
    for_each = local.preview_cert_mode == "google_managed" ? [1] : []
    content {
      domains            = ["*.${local.preview_domain}"]
      dns_authorizations = local.preview_dns_auth ? [google_certificate_manager_dns_authorization.preview[0].id] : null
      issuance_config    = try(local.preview_cert.issuance_config, null)
    }
  }

  dynamic "self_managed" {
    for_each = local.preview_cert_mode == "pem" ? [1] : []
    content {
      pem_certificate = local.preview_cert.pem_certificate
      pem_private_key = local.preview_cert.pem_private_key
    }
  }

  depends_on = [google_project_service.preview]
}

data "google_dns_managed_zone" "preview" {
  count = local.preview_zone_ref == null ? 0 : 1

  project = coalesce(local.preview_zone_ref.project, var.project_id)
  name    = local.preview_zone_ref.name
}

resource "google_dns_record_set" "preview" {
  count = local.preview_zone_ref == null ? 0 : 1

  project      = data.google_dns_managed_zone.preview[0].project
  managed_zone = data.google_dns_managed_zone.preview[0].name
  name         = "*.${local.preview_domain}."
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_address.preview[0].address]

  lifecycle {
    precondition {
      condition     = endswith(".${local.preview_domain}.", ".${data.google_dns_managed_zone.preview[0].dns_name}")
      error_message = "preview.dns_zone (${data.google_dns_managed_zone.preview[0].dns_name}) does not contain ${local.preview_domain}."
    }
  }
}

resource "google_dns_record_set" "preview_cert_authorization" {
  count = local.preview_zone_ref != null && local.preview_dns_auth ? 1 : 0

  project      = data.google_dns_managed_zone.preview[0].project
  managed_zone = data.google_dns_managed_zone.preview[0].name
  name         = google_certificate_manager_dns_authorization.preview[0].dns_resource_record[0].name
  type         = google_certificate_manager_dns_authorization.preview[0].dns_resource_record[0].type
  ttl          = 300
  rrdatas      = [google_certificate_manager_dns_authorization.preview[0].dns_resource_record[0].data]
}

# Certificate Manager validates the DNS authorization from the public internet:
# a record in a private zone never validates.
check "preview_certificate_validates" {
  assert {
    condition = !(
      local.preview_dns_auth && local.preview_zone_ref != null &&
      try(data.google_dns_managed_zone.preview[0].visibility, "public") == "private"
    )
    error_message = "preview.dns_zone is private, but a Google-managed certificate is validated through PUBLIC DNS. Publish the DNS authorization record (output preview_dns_records) in the public zone for this domain, or set certificate.issuance_config (private CA), or bring a certificate (mode certificate_manager or pem)."
  }
}

# The mode-dependent keys of runtm/cluster.json: router_ip (Runtm's edge, the
# default) or preview, plus the DNS endpoint with control_plane_access = "dns". Each is kept or dropped by a filtered `for`,
# which (unlike a conditional between two object shapes) type-checks in every
# mode.
locals {
  cluster_json_options = merge(
    { for k, v in { router_ip = try(google_compute_address.router[0].address, null) } : k => v if !local.preview_enabled },
    {
      for k, v in {
        preview = {
          domain   = local.preview_domain
          exposure = try(var.preview.exposure, null)
          address  = try(google_compute_address.preview[0].address, null)
        }
      } : k => v if local.preview_enabled
    },
    {
      for k, v in {
        control_plane_access = "dns"
        dns_endpoint         = try(google_container_cluster.sandboxes.control_plane_endpoints_config[0].dns_endpoint_config[0].endpoint, null)
      } : k => v if var.control_plane_access == "dns"
    }
  )
}

# Where Runtm finds the cluster and the preview router (it may read the
# bucket, not the project). Written last, so its presence means the setup
# above finished.
resource "google_storage_bucket_object" "cluster" {
  bucket       = google_storage_bucket.sandboxes.name
  name         = "runtm/cluster.json"
  content_type = "application/json"
  # An existing stack's file is unchanged: the same five keys (router_ip comes
  # from cluster_json_options in the default mode).
  content = jsonencode(merge(
    {
      endpoint  = google_container_cluster.sandboxes.endpoint
      ca        = one(google_container_cluster.sandboxes.master_auth[*].cluster_ca_certificate)
      cluster   = google_container_cluster.sandboxes.name
      namespace = local.namespace
    },
    local.cluster_json_options,
  ))

  depends_on = [
    kubectl_manifest.runtm,
    kubectl_manifest.router_service,
    kubectl_manifest.router_gateway,
  ]
}
