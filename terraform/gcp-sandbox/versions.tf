terraform {
  required_version = ">= 1.5"

  required_providers {
    google = {
      source = "hashicorp/google"
      # 7.33 is the first release with addons_config.pod_snapshot_config,
      # which enables Pod Snapshots when the cluster is created.
      version = ">= 7.33"
    }
  }
}
