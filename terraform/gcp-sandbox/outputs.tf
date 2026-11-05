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
  description = "Address of the preview router: the edge load balancer (reachable only from runtm_ingress_cidrs), or with `preview` the Gateway's (internal or external)."
  value       = local.router_address
}

output "preview_url_pattern" {
  description = "With `preview`: how session previews are addressed."
  value       = local.preview_enabled ? "https://<port>-<sandbox>.${local.preview_domain}" : null
}

output "preview_dns_records" {
  description = "With `preview`: the DNS records previews need. Created by the module when preview.dns_zone is set; otherwise create them yourself. The certificate record, when present, must be resolvable on the public internet."
  value = local.preview_enabled ? concat(
    [{ name = "*.${local.preview_domain}.", type = "A", data = google_compute_address.preview[0].address }],
    local.preview_dns_auth ? [{
      name = google_certificate_manager_dns_authorization.preview[0].dns_resource_record[0].name
      type = google_certificate_manager_dns_authorization.preview[0].dns_resource_record[0].type
      data = google_certificate_manager_dns_authorization.preview[0].dns_resource_record[0].data
    }] : [],
  ) : null
}

output "dns_endpoint" {
  description = "The cluster's DNS endpoint (Google IAM-authorized). The only endpoint with control_plane_access = \"dns\"."
  value       = google_container_cluster.sandboxes.control_plane_endpoints_config[0].dns_endpoint_config[0].endpoint
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
    network              = local.create_network ? google_compute_network.sandboxes[0].name : local.network_id
    subnet               = local.create_subnet ? google_compute_subnetwork.sandboxes[0].name : local.subnetwork_id
    nat_router           = var.nat == "create" ? google_compute_router.nat[0].name : null
    router_ip_name       = local.preview_enabled ? google_compute_address.preview[0].name : google_compute_address.router[0].name
    node_service_account = google_service_account.node.email
    bucket               = google_storage_bucket.sandboxes.name
    ar_repo              = "${google_artifact_registry_repository.images.location}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
  }
}
