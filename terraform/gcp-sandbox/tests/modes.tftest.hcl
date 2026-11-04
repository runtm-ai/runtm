# Network, control-plane and preview modes, planned against mock providers
# (nothing is created). Run: terraform init && terraform test

mock_provider "google" {
  override_during = plan

  override_resource {
    target = google_container_cluster.sandboxes
    values = {
      id       = "projects/my-project-1/locations/us-west1/clusters/c"
      endpoint = "34.0.0.1"
    }
  }
  override_resource {
    target = google_compute_address.router
    values = { address = "34.0.0.9", name = "runtm-sandbox-preview-x" }
  }
  override_resource {
    target = google_compute_address.preview
    values = { address = "10.77.0.50", name = "runtm-sandbox-gw-x" }
  }
  override_resource {
    target = google_certificate_manager_dns_authorization.preview
    values = {
      id = "projects/my-project-1/locations/us-west1/dnsAuthorizations/a"
      dns_resource_record = [{
        name = "_acme-challenge_abc.runtm.rain.xyz."
        type = "CNAME"
        data = "abc.4.authorize.certificatemanager.goog."
      }]
    }
  }
  override_data {
    target = data.google_project.this
    values = { number = "123456789012" }
  }
  override_data {
    target = data.google_compute_network.existing
    values = {
      id        = "projects/host-prj/global/networks/corp"
      self_link = "https://www.googleapis.com/compute/v1/projects/host-prj/global/networks/corp"
    }
  }
  override_data {
    target = data.google_compute_subnetwork.existing
    values = {
      id                       = "projects/host-prj/regions/us-west1/subnetworks/shared"
      network                  = "https://www.googleapis.com/compute/v1/projects/host-prj/global/networks/corp"
      region                   = "us-west1"
      private_ip_google_access = true
      secondary_ip_range = [
        { range_name = "gke-pods", ip_cidr_range = "10.200.0.0/17", reserved_internal_range = "" },
        { range_name = "gke-svc", ip_cidr_range = "10.201.0.0/22", reserved_internal_range = "" },
      ]
    }
  }
  override_data {
    target = data.google_dns_managed_zone.preview
    values = { project = "dns-prj", name = "rain", dns_name = "rain.xyz.", visibility = "public" }
  }
}

variables {
  runtm_organization_id = "org123"
  project_id            = "my-project-1"
  region                = "us-west1"
}

run "defaults_create_a_dedicated_network" {
  command = plan

  assert {
    condition     = length(google_compute_network.sandboxes) == 1 && length(google_compute_subnetwork.sandboxes) == 1
    error_message = "the default stack creates its VPC and subnet"
  }
  assert {
    condition     = google_compute_router_nat.nat[0].source_subnetwork_ip_ranges_to_nat == "ALL_SUBNETWORKS_ALL_IP_RANGES"
    error_message = "the module's own NAT covers its whole VPC (unchanged)"
  }
  assert {
    condition     = google_compute_subnetwork.sandboxes[0].secondary_ip_range[0].range_name == "pods"
    error_message = "the default secondary range names are unchanged"
  }
  assert {
    condition     = length(google_compute_address.router) == 1 && length(google_compute_address.preview) == 0
    error_message = "the default router is reached by Runtm's edge"
  }
  assert {
    condition     = length(google_container_cluster.sandboxes.master_authorized_networks_config) == 1
    error_message = "the IP endpoint stays allowlisted by default"
  }
  assert {
    condition     = length(google_container_cluster.sandboxes.gateway_api_config) == 0
    error_message = "no Gateway API config without preview"
  }
  assert {
    condition     = keys(local.cluster_json_options) == ["router_ip"]
    error_message = "cluster.json keeps its original keys"
  }
}

run "existing_subnet_and_existing_nat" {
  command = plan

  variables {
    subnetwork          = "projects/host-prj/regions/us-west1/subnetworks/shared"
    pods_range_name     = "gke-pods"
    services_range_name = "gke-svc"
    nat                 = "existing"
  }

  assert {
    condition     = length(google_compute_network.sandboxes) == 0 && length(google_compute_subnetwork.sandboxes) == 0 && length(google_compute_router_nat.nat) == 0
    error_message = "nothing network-side is created with an existing subnet and NAT"
  }
  assert {
    condition     = google_container_cluster.sandboxes.subnetwork == "projects/host-prj/regions/us-west1/subnetworks/shared"
    error_message = "the cluster uses the given subnet"
  }
  assert {
    condition     = google_container_cluster.sandboxes.network == "https://www.googleapis.com/compute/v1/projects/host-prj/global/networks/corp"
    error_message = "the cluster uses the subnet's VPC"
  }
  assert {
    condition     = google_container_cluster.sandboxes.ip_allocation_policy[0].cluster_secondary_range_name == "gke-pods"
    error_message = "the cluster uses the named pod range"
  }
  assert {
    condition     = data.google_compute_subnetwork.existing[0].project == "host-prj"
    error_message = "the subnet is looked up in the project its path names"
  }
}

run "existing_vpc_gets_a_scoped_nat" {
  command = plan

  variables {
    network = "projects/host-prj/global/networks/corp"
  }

  assert {
    condition     = length(google_compute_network.sandboxes) == 0 && google_compute_subnetwork.sandboxes[0].project == "host-prj"
    error_message = "the subnet is created in the existing VPC's project"
  }
  assert {
    condition     = google_compute_subnetwork.sandboxes[0].network == "projects/host-prj/global/networks/corp"
    error_message = "the subnet joins the existing VPC"
  }
  assert {
    condition = (
      google_compute_router_nat.nat[0].source_subnetwork_ip_ranges_to_nat == "LIST_OF_SUBNETWORKS" &&
      length(google_compute_router_nat.nat[0].subnetwork) == 1
    )
    error_message = "in an existing VPC the NAT covers only the sandbox subnet"
  }
}

run "subnet_in_another_region_is_refused" {
  command = plan

  variables {
    # The path names another region than the stack's (us-west1).
    subnetwork          = "projects/host-prj/regions/europe-west1/subnetworks/shared"
    pods_range_name     = "gke-pods"
    services_range_name = "gke-svc"
  }

  expect_failures = [google_container_cluster.sandboxes]
}

run "missing_secondary_range_is_refused" {
  command = plan

  variables {
    subnetwork = "projects/host-prj/regions/us-west1/subnetworks/shared"
    # The subnet's ranges are gke-pods / gke-svc, not the default names.
  }

  expect_failures = [google_container_cluster.sandboxes]
}

run "dns_only_control_plane" {
  command = plan

  variables {
    control_plane_access = "dns"
  }

  assert {
    condition     = length(google_container_cluster.sandboxes.master_authorized_networks_config) == 0
    error_message = "no IP allowlist in dns mode"
  }
  assert {
    condition     = google_container_cluster.sandboxes.control_plane_endpoints_config[0].ip_endpoints_config[0].enabled == false
    error_message = "the IP endpoint is turned off in dns mode"
  }
  assert {
    condition     = keys(local.cluster_json_options) == ["control_plane_access", "dns_endpoint", "router_ip"] && local.cluster_json_options.control_plane_access == "dns"
    error_message = "cluster.json carries the DNS endpoint"
  }
}

run "internal_preview_with_managed_certificate_and_zone" {
  command = plan

  variables {
    preview = {
      domain   = "runtm.rain.xyz"
      dns_zone = "projects/dns-prj/managedZones/rain"
    }
  }

  assert {
    condition     = length(google_compute_address.router) == 0 && google_compute_address.preview[0].address_type == "INTERNAL"
    error_message = "an internal preview replaces the edge address with an internal one"
  }
  assert {
    condition     = google_compute_address.preview[0].purpose == "SHARED_LOADBALANCER_VIP"
    error_message = "the internal Gateway address is a shared load balancer VIP"
  }
  assert {
    condition     = google_compute_subnetwork.proxy_only[0].purpose == "REGIONAL_MANAGED_PROXY"
    error_message = "a proxy-only subnet is created"
  }
  assert {
    condition     = google_compute_firewall.preview[0].destination_ranges == toset(["10.77.128.0/17"])
    error_message = "the load balancer may reach the pod range only"
  }
  assert {
    condition     = google_certificate_manager_certificate.preview[0].managed[0].domains == tolist(["*.runtm.rain.xyz"])
    error_message = "a wildcard certificate is requested"
  }
  assert {
    condition     = google_dns_record_set.preview[0].name == "*.runtm.rain.xyz." && google_dns_record_set.preview[0].project == "dns-prj"
    error_message = "the wildcard record is created in the given zone"
  }
  assert {
    condition     = google_dns_record_set.preview_cert_authorization[0].type == "CNAME"
    error_message = "the certificate's DNS authorization record is created"
  }
  assert {
    condition     = google_container_cluster.sandboxes.gateway_api_config[0].channel == "CHANNEL_STANDARD"
    error_message = "the Gateway API is enabled"
  }
  assert {
    condition = (
      strcontains(local.k8s_manifest, "gatewayClassName: gke-l7-rilb") &&
      strcontains(local.k8s_manifest, "image: us-west1-docker.pkg.dev/kuimjyxqkrbfvalw-public/runtm-public/sandbox-preview-proxy:latest") &&
      strcontains(local.k8s_manifest, "imagePullPolicy: Always") &&
      !strcontains(local.k8s_manifest, "type: LoadBalancer")
    )
    error_message = "the manifest deploys the Gateway and the public router image, without the edge LoadBalancer"
  }
  assert {
    condition     = keys(local.cluster_json_options) == ["preview"] && local.cluster_json_options.preview.domain == "runtm.rain.xyz" && local.cluster_json_options.preview.address == "10.77.0.50"
    error_message = "cluster.json describes the preview domain instead of the edge router"
  }
}

run "external_preview_with_own_certificate" {
  command = plan

  variables {
    preview = {
      domain          = "previews.rain.xyz"
      exposure        = "external"
      security_policy = "rain-armor"
      certificate = {
        mode        = "certificate_manager"
        certificate = "projects/my-project-1/locations/us-west1/certificates/rain-wildcard"
      }
    }
  }

  assert {
    condition     = google_compute_address.preview[0].address_type == "EXTERNAL" && google_compute_address.preview[0].network_tier == "STANDARD"
    error_message = "an external preview uses a Standard-tier regional external address"
  }
  assert {
    condition     = length(google_certificate_manager_certificate.preview) == 0 && length(google_certificate_manager_dns_authorization.preview) == 0
    error_message = "a certificate you manage is referenced, not created"
  }
  assert {
    condition     = length(google_dns_record_set.preview) == 0
    error_message = "no DNS records without a zone"
  }
  assert {
    condition = (
      strcontains(local.k8s_manifest, "networking.gke.io/cert-manager-certs: rain-wildcard") &&
      strcontains(local.k8s_manifest, "gatewayClassName: gke-l7-regional-external-managed") &&
      strcontains(local.k8s_manifest, "securityPolicy: rain-armor")
    )
    error_message = "the Gateway uses the given certificate, the external class and the security policy"
  }
  assert {
    condition     = length(output.preview_dns_records) == 1
    error_message = "only the wildcard record is listed when no DNS authorization exists"
  }
}

run "pem_certificate_and_private_ca" {
  command = plan

  variables {
    preview = {
      domain = "runtm.rain.xyz"
      certificate = {
        mode            = "pem"
        pem_certificate = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"
        pem_private_key = "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----"
      }
    }
  }

  assert {
    condition     = length(google_certificate_manager_certificate.preview[0].self_managed) == 1 && length(google_certificate_manager_dns_authorization.preview) == 0
    error_message = "a passed-in certificate is uploaded, without a DNS authorization"
  }
}

run "private_ca_needs_no_dns_authorization" {
  command = plan

  variables {
    preview = {
      domain = "runtm.internal.rain.xyz"
      certificate = {
        issuance_config = "projects/my-project-1/locations/us-west1/certificateIssuanceConfigs/rain-ca"
      }
    }
  }

  assert {
    condition = (
      google_certificate_manager_certificate.preview[0].managed[0].issuance_config == "projects/my-project-1/locations/us-west1/certificateIssuanceConfigs/rain-ca" &&
      length(google_certificate_manager_dns_authorization.preview) == 0
    )
    error_message = "a private CA issues the certificate without a DNS authorization"
  }
}

run "existing_proxy_only_subnet_and_no_firewall" {
  command = plan

  variables {
    manage_firewall_rules = false
    preview = {
      domain                 = "runtm.rain.xyz"
      proxy_only_subnet      = "existing"
      proxy_only_subnet_cidr = "10.10.0.0/23"
    }
  }

  assert {
    condition     = length(google_compute_subnetwork.proxy_only) == 0 && length(google_compute_firewall.preview) == 0
    error_message = "neither the proxy-only subnet nor the firewall rule is created"
  }
  assert {
    condition     = strcontains(local.k8s_manifest, "value: \"10.10.0.0/23\"")
    error_message = "the router trusts the existing proxy-only subnet"
  }
}

run "egress_allow_list_renders_a_network_policy" {
  command = plan

  variables {
    sandbox_egress_allowed_cidrs = ["10.20.0.0/16"]
  }

  assert {
    condition     = strcontains(local.k8s_manifest, "name: runtm-egress-allowed") && strcontains(local.k8s_manifest, "cidr: \"10.20.0.0/16\"")
    error_message = "the allow-list becomes an additive NetworkPolicy"
  }
}

run "zone_that_does_not_hold_the_domain_is_refused" {
  command = plan

  variables {
    preview = {
      domain   = "runtm.other.dev"
      dns_zone = "projects/dns-prj/managedZones/rain"
    }
  }

  expect_failures = [google_dns_record_set.preview]
}

run "certificate_manager_mode_requires_a_certificate" {
  command = plan

  variables {
    preview = {
      domain      = "runtm.rain.xyz"
      certificate = { mode = "certificate_manager" }
    }
  }

  expect_failures = [var.preview]
}

run "security_policy_is_external_only" {
  command = plan

  variables {
    preview = {
      domain          = "runtm.rain.xyz"
      security_policy = "rain-armor"
    }
  }

  expect_failures = [var.preview]
}

run "private_zone_cannot_validate_a_google_managed_certificate" {
  command = plan

  override_data {
    target = data.google_dns_managed_zone.preview
    values = { project = "dns-prj", name = "rain-internal", dns_name = "internal.rain.xyz.", visibility = "private" }
  }

  variables {
    preview = {
      domain   = "runtm.internal.rain.xyz"
      dns_zone = "projects/dns-prj/managedZones/rain-internal"
    }
  }

  expect_failures = [check.preview_certificate_validates]
}
