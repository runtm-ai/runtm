# Replacing the cluster or the reserved address must replace router_lb_release,
# so its destroy-time cleanup runs before they go (an `input` change alone is
# applied in place and skips it). Run: terraform init && terraform test
mock_provider "google" {
  override_during = plan
  override_resource {
    target = google_container_cluster.sandboxes
    values = { id = "projects/p/locations/us-west1/clusters/c" }
  }
  override_resource {
    target = google_compute_address.router[0]
    values = { id = "projects/p/regions/us-west1/addresses/a" }
  }
}

variables {
  runtm_organization_id = "org123"
  project_id            = "my-project-1"
  region                = "us-west1"
}

run "cleanup_tracks_cluster_and_address" {
  command = plan

  plan_options {
    target = [terraform_data.router_lb_release]
  }

  assert {
    condition = terraform_data.router_lb_release.triggers_replace == [
      "projects/p/locations/us-west1/clusters/c",
      "projects/p/regions/us-west1/addresses/a",
    ]
    error_message = "router_lb_release must be replaced with the cluster and the reserved address"
  }
}
