terraform {
  # 1.7: `removed` blocks (moved.tf).
  required_version = ">= 1.7"

  required_providers {
    google = {
      source = "hashicorp/google"
      # 7.33 is the first release with addons_config.pod_snapshot_config,
      # which enables Pod Snapshots when the cluster is created.
      version = ">= 7.33"
    }
    kubectl = {
      source = "alekc/kubectl"
      # 3.x: lazy_load, discovery refreshed between apply retries.
      version = "~> 3.0"
    }
  }
}
