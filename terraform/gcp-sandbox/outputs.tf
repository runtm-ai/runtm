output "project_id" {
  description = "Paste into Runtm (Integrations -> Google Cloud Sandboxes): the project sandboxes run in."
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

output "cluster" {
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
  description = "Everything Runtm reads from this stack, in one object. Runtm derives the bucket from the project id and reads the rest from gs://<bucket>/runtm/cluster.json; this is for checking."
  value = {
    project_id = var.project_id
    region     = var.region
    bucket     = google_storage_bucket.sandboxes.name
    ar_repo    = "${google_artifact_registry_repository.images.location}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
    cluster    = google_container_cluster.sandboxes.name
  }
}
