output "cluster" {
  description = "Paste into Runtm (Integrations -> Google Cloud Sandboxes): the cluster's resource name, projects/<project>/locations/<region>/clusters/<name>. It names this stack's cluster among any others in the project; Runtm derives the rest of the stack's names from it."
  value       = google_container_cluster.sandboxes.id
}

output "project_id" {
  description = "The project sandboxes run in."
  value       = var.project_id
}

output "region" {
  value = var.region
}

output "bucket" {
  description = "Bucket for workspace archives (fs-snapshots/), Pod Snapshots (podsnapshots/) and runtm/cluster.json."
  value       = google_storage_bucket.sandboxes.name
}

output "ar_repo" {
  description = "Docker repository Runtm pushes the sandbox image to."
  value       = "${google_artifact_registry_repository.images.location}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "cluster_name" {
  description = "GKE Autopilot cluster the sandboxes run in (namespace sandboxes)."
  value       = google_container_cluster.sandboxes.name
}

output "router_ip" {
  description = "Address of the preview router (reachable only from runtm_ingress_cidrs)."
  value       = google_compute_address.router.address
}

output "runtm_service_account" {
  description = "Your organization's Runtm service account, granted access above."
  value       = local.runtm_sa_email
}

output "runtm_connection" {
  description = "Everything this stack names, in one object. Runtm derives all of it from the cluster resource name and reads the cluster's address from gs://<bucket>/runtm/cluster.json; this is for checking."
  value = {
    cluster              = google_container_cluster.sandboxes.id
    project_id           = var.project_id
    region               = var.region
    suffix               = var.suffix
    cluster_name         = google_container_cluster.sandboxes.name
    network              = google_compute_network.sandboxes.name
    subnet               = google_compute_subnetwork.sandboxes.name
    nat_router           = google_compute_router.nat.name
    router_ip_name       = google_compute_address.router.name
    node_service_account = google_service_account.node.email
    bucket               = google_storage_bucket.sandboxes.name
    ar_repo              = "${google_artifact_registry_repository.images.location}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
  }
}
