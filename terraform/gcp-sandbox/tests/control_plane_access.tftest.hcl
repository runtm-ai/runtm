# control_plane_access: "ip" (default) keeps today's stack; "dns" turns the IP
# endpoint off, drops the allow-list and grants Runtm connect on this cluster only.
# Run: terraform init && terraform test
mock_provider "google" {
  override_during = plan
  override_resource {
    target = google_container_cluster.sandboxes
    values = { id = "projects/my-project-1/locations/us-west1/clusters/c" }
  }
}

variables {
  runtm_organization_id = "org123"
  project_id            = "my-project-1"
  region                = "us-west1"
}

run "ip_is_the_default_and_unchanged" {
  command = plan
  plan_options {
    target = [google_container_cluster.sandboxes, google_project_iam_member.runtm_cluster_connect]
  }

  assert {
    condition     = google_container_cluster.sandboxes.control_plane_endpoints_config[0].ip_endpoints_config[0].enabled
    error_message = "ip mode keeps the IP endpoint"
  }
  assert {
    condition     = length(google_container_cluster.sandboxes.master_authorized_networks_config) == 1
    error_message = "ip mode keeps the allow-list"
  }
  assert {
    condition     = length(google_project_iam_member.runtm_cluster_connect) == 0
    error_message = "ip mode grants no cluster connect"
  }
}

run "dns_turns_the_ip_endpoint_off" {
  command = plan
  variables {
    control_plane_access = "dns"
  }
  plan_options {
    target = [google_container_cluster.sandboxes, google_project_iam_member.runtm_cluster_connect]
  }

  assert {
    condition     = !google_container_cluster.sandboxes.control_plane_endpoints_config[0].ip_endpoints_config[0].enabled
    error_message = "dns mode turns the IP endpoint off"
  }
  assert {
    condition     = length(google_container_cluster.sandboxes.master_authorized_networks_config) == 0
    error_message = "dns mode has no allow-list"
  }
  assert {
    condition     = google_project_iam_member.runtm_cluster_connect[0].role == "roles/container.clusterViewer"
    error_message = "dns mode grants clusterViewer"
  }
  assert {
    condition     = google_project_iam_member.runtm_cluster_connect[0].condition[0].expression == "resource.name == \"projects/my-project-1/locations/us-west1/clusters/c\""
    error_message = "the grant is scoped to this cluster"
  }
}

run "anything_else_is_refused" {
  command = plan
  variables {
    control_plane_access = "private"
  }
  expect_failures = [var.control_plane_access]
}
