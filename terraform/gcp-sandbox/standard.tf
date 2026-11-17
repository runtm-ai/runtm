# ---------------------------------------------------------------------------
# cluster_mode = "standard": a GKE Standard cluster with two node pools, for
# projects where Autopilot cannot run or a fixed machine shape is required
# (e.g. constraints/compute.disableSerialPortLogging enforced, Spot capacity,
# sandboxes whose disk outgrows Autopilot's 10 GiB per Pod, warm nodes).
#
#   system  - non-sandbox pool: agent-sandbox controller, preview router, GKE
#             add-ons (GKE Sandbox needs at least one non-gVisor pool).
#   sandbox - GKE Sandbox (gVisor) pool; GKE taints it sandbox.gke.io/runtime=
#             gvisor:NoSchedule and the gvisor RuntimeClass tolerates it, so
#             sandbox pods (runtimeClassName: gvisor) land here unchanged.
#
# Every node carries serial-port-logging-enable=false (satisfies
# constraints/compute.disableSerialPortLogging) and block-project-ssh-keys.
# ---------------------------------------------------------------------------

locals {
  autopilot = var.cluster_mode == "autopilot"
  standard  = var.cluster_mode == "standard"

  # Authoritative and ForceNew on a pool: disable-legacy-endpoints is what GKE
  # sets anyway, declared so plans show no drift.
  standard_node_metadata = {
    serial-port-logging-enable = "false"
    block-project-ssh-keys     = "true"
    disable-legacy-endpoints   = "true"
  }
}

resource "google_container_node_pool" "system" {
  count = local.standard ? 1 : 0

  project  = var.project_id
  name     = "system"
  cluster  = google_container_cluster.sandboxes.name
  location = var.region

  autoscaling {
    min_node_count = 1
    max_node_count = var.standard.system_max_nodes
  }

  node_config {
    machine_type      = var.standard.system_machine_type
    disk_size_gb      = 50
    image_type        = "COS_CONTAINERD"
    service_account   = google_service_account.node.email
    oauth_scopes      = ["https://www.googleapis.com/auth/cloud-platform"]
    boot_disk_kms_key = var.standard.boot_disk_kms_key
    metadata          = local.standard_node_metadata

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    labels = merge(local.labels, { "runtm.io/pool" = "system" })
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}

resource "google_container_node_pool" "sandbox" {
  count = local.standard ? 1 : 0

  project  = var.project_id
  name     = "sandbox"
  cluster  = google_container_cluster.sandboxes.name
  location = var.region

  autoscaling {
    min_node_count = var.standard.sandbox_min_nodes
    max_node_count = var.standard.sandbox_max_nodes
  }

  node_config {
    machine_type = var.standard.sandbox_machine_type
    spot         = var.standard.sandbox_spot
    disk_size_gb = var.standard.sandbox_disk_gb
    image_type   = "COS_CONTAINERD" # GKE Sandbox needs it

    service_account   = google_service_account.node.email
    oauth_scopes      = ["https://www.googleapis.com/auth/cloud-platform"]
    boot_disk_kms_key = var.standard.boot_disk_kms_key
    metadata          = local.standard_node_metadata

    sandbox_config {
      type = "GVISOR"
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }

    labels = merge(local.labels, { "runtm.io/pool" = "sandbox" })
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

}
