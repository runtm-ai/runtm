# Runtm Cloud - Google Cloud sandboxes integration (customer side).
#
# The GCP twin of runtm/terraform/aws-sandbox and runtm/terraform/azure-sandbox.
# It creates, in YOUR project: a GKE Autopilot cluster that runs each sandbox
# as a gVisor pod (agent-sandbox controller, Pod Snapshots on), its network
# with Cloud NAT, a bucket for workspace archives and snapshots, an Artifact
# Registry repository for the sandbox image, a preview router reachable only
# from Runtm, and the grants your organization's Runtm service account needs.
# Runtm gets no project-wide role: it acts through Kubernetes RBAC in the
# cluster and through grants on the bucket and the repository.

locals {
  # The shared sha256 slug (the same the organization has on AWS and Azure).
  # cloud-api computes the same names
  # (backend/app/services/sandbox_connections.py: resource_slug).
  slug = substr(sha256(var.runtm_organization_id), 0, 12)

  # Identity Runtm presents: one Google service account per organization, its
  # email DERIVED from the org id with the formula cloud-api uses
  # (backend/app/services/aws_org_identity.py: "runtm-aws-" + sha256(org)[:20],
  # 30 chars = GCP's account-id maximum). Same account the AWS module trusts.
  runtm_sa_email  = "runtm-aws-${substr(sha256(var.runtm_organization_id), 0, 20)}@${var.runtm_google_project}.iam.gserviceaccount.com"
  runtm_sa_member = "serviceAccount:${local.runtm_sa_email}"

  # Fixed names (cloud-api derives the bucket from the slug and the project
  # id and reads everything else from runtm/cluster.json; nothing to paste
  # back but the project id). Bucket names are global: slug + project keeps
  # them unique (10 + 12 + 1 + 30 = 53 <= 63).
  bucket_name    = substr("runtm-sbx-${local.slug}-${var.project_id}", 0, 63)
  ar_repo_id     = "runtm-sandbox"
  network_name   = "runtm-sandbox"
  subnet_name    = "runtm-sandbox-${var.region}"
  cluster_name   = "runtm-sandbox"
  node_sa_id     = "runtm-sandbox-gke-node"
  router_ip_name = "runtm-sandbox-preview"
  namespace      = "sandboxes"

  # agent-sandbox controller release, pinned by URL and checksum.
  agent_sandbox_url    = "https://github.com/kubernetes-sigs/agent-sandbox/releases/download/v1.0.9/sandbox-with-extensions.yaml"
  agent_sandbox_sha256 = "4bf9ee5558e9a663f91cfa5963e96f2591bfaa1dcf79f03bfab65bc5d65cc699"

  router_image     = "caddy:2.11.7"
  router_caddyfile = file("${path.module}/router/Caddyfile")

  labels = merge(var.labels, {
    "runtm-slug" = local.slug
  })
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
  project                 = var.project_id
  name                    = local.network_name
  auto_create_subnetworks = false

  depends_on = [google_project_service.this]
}

resource "google_compute_subnetwork" "sandboxes" {
  project                  = var.project_id
  name                     = local.subnet_name
  region                   = var.region
  network                  = google_compute_network.sandboxes.id
  ip_cidr_range            = var.subnet_cidr
  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = var.pods_cidr
  }

  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = var.services_cidr
  }
}

resource "google_compute_router" "nat" {
  project = var.project_id
  name    = "runtm-sandbox-nat"
  region  = var.region
  network = google_compute_network.sandboxes.id
}

# Same port settings as Runtm's own cluster NAT: ports per node on demand
# (many sandboxes share a node) and a 1 h idle timeout so quiet long-lived
# connections are not cut.
resource "google_compute_router_nat" "nat" {
  project                            = var.project_id
  name                               = "runtm-sandbox-nat"
  router                             = google_compute_router.nat.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

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

# Address of the preview router's load balancer
# (agent-sandbox-system/sandbox-router).
resource "google_compute_address" "router" {
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
  display_name = "Runtm sandbox GKE nodes"
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
# endpoint (Google IAM-authorized) is how scripts/cluster-setup.sh reaches it
# from Cloud Shell or your machine.
# ---------------------------------------------------------------------------

resource "google_container_cluster" "sandboxes" {
  project  = var.project_id
  name     = local.cluster_name
  location = var.region

  enable_autopilot = true

  network    = google_compute_network.sandboxes.id
  subnetwork = google_compute_subnetwork.sandboxes.id

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
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

  master_authorized_networks_config {
    dynamic "cidr_blocks" {
      for_each = var.runtm_ingress_cidrs
      content {
        cidr_block   = cidr_blocks.value
        display_name = "runtm"
      }
    }
    gcp_public_cidrs_access_enabled = false
  }

  control_plane_endpoints_config {
    dns_endpoint_config {
      allow_external_traffic = true
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
# RBAC in k8s/runtm.yaml.tftpl; in the project, only these two grants.
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

# ---------------------------------------------------------------------------
# In-cluster setup: agent-sandbox controller, label allow-list, namespace,
# Pod Snapshot storage config and policy, Runtm's RBAC, preview router.
# kubectl through the cluster's DNS endpoint with your gcloud credentials
# (Cloud Shell has gcloud, kubectl and gke-gcloud-auth-plugin). Re-runs when
# any input changes; every step is idempotent.
# ---------------------------------------------------------------------------

locals {
  k8s_manifest = templatefile("${path.module}/k8s/runtm.yaml.tftpl", {
    bucket               = google_storage_bucket.sandboxes.name
    runtm_sa_email       = local.runtm_sa_email
    router_ip            = google_compute_address.router.address
    ingress_cidrs        = var.runtm_ingress_cidrs
    router_image         = local.router_image
    router_caddyfile     = local.router_caddyfile
    router_config_sha256 = sha256(local.router_caddyfile)
  })
  k8s_heal_rbac = templatefile("${path.module}/k8s/heal-rbac.yaml.tftpl", {
    runtm_sa_email = local.runtm_sa_email
  })
}

resource "terraform_data" "cluster_setup" {
  triggers_replace = [
    google_container_cluster.sandboxes.id,
    sha256(local.k8s_manifest),
    sha256(local.k8s_heal_rbac),
    filesha256("${path.module}/scripts/cluster-setup.sh"),
    local.agent_sandbox_sha256,
  ]

  provisioner "local-exec" {
    interpreter = ["bash", "-c"]
    command     = "bash '${path.module}/scripts/cluster-setup.sh' apply"
    environment = {
      PROJECT_ID           = var.project_id
      REGION               = var.region
      CLUSTER              = google_container_cluster.sandboxes.name
      ROUTER_IP            = google_compute_address.router.address
      AGENT_SANDBOX_URL    = local.agent_sandbox_url
      AGENT_SANDBOX_SHA256 = local.agent_sandbox_sha256
      MANIFEST             = local.k8s_manifest
      HEAL_RBAC            = local.k8s_heal_rbac
    }
  }

  depends_on = [
    google_storage_bucket_iam_member.gke_node_agent,
    google_storage_bucket_iam_member.gke_service_agent,
    google_artifact_registry_repository_iam_member.node_reader,
  ]
}

# On `terraform destroy` only: delete the router Service so GKE releases its
# load balancer before the cluster and the reserved address go (GKE does not
# always clean it up with the cluster). Kept apart from cluster_setup, whose
# replacement on every config change must not take the router down.
resource "terraform_data" "router_lb_release" {
  input = {
    project_id = var.project_id
    region     = var.region
    cluster    = google_container_cluster.sandboxes.name
    address    = google_compute_address.router.id
  }

  provisioner "local-exec" {
    when        = destroy
    interpreter = ["bash", "-c"]
    command     = "bash '${path.module}/scripts/cluster-setup.sh' destroy"
    environment = {
      PROJECT_ID = self.input.project_id
      REGION     = self.input.region
      CLUSTER    = self.input.cluster
    }
  }
}

# Where Runtm finds the cluster and the preview router (it may read the
# bucket, not the project). Written last, so its presence means the setup
# above finished.
resource "google_storage_bucket_object" "cluster" {
  bucket       = google_storage_bucket.sandboxes.name
  name         = "runtm/cluster.json"
  content_type = "application/json"
  content = jsonencode({
    endpoint  = google_container_cluster.sandboxes.endpoint
    ca        = google_container_cluster.sandboxes.master_auth[0].cluster_ca_certificate
    cluster   = google_container_cluster.sandboxes.name
    namespace = local.namespace
    router_ip = google_compute_address.router.address
  })

  depends_on = [terraform_data.cluster_setup]
}
