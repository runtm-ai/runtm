# ---------------------------------------------------------------------------
# Identity: how Runtm proves it is YOUR organization when it acts in your
# project.
#
# Runtm operates one Google service account per organization, its email
# DERIVED from the organization id with the same formula the AWS module trusts
# (runtm-aws-<sha256(org id)[:20]>@<runtm_google_project>). This module grants
# that account access to the resources below; Runtm impersonates it for
# short-lived access tokens. No key, no secret, nothing to paste in.
# ---------------------------------------------------------------------------

variable "runtm_organization_id" {
  description = "Your Runtm organization id (Integrations -> Google Cloud Sandboxes). Derives the per-organization slug and Runtm's service account email."
  type        = string

  validation {
    condition     = length(var.runtm_organization_id) > 0 && length(var.runtm_organization_id) <= 64
    error_message = "runtm_organization_id must be 1-64 characters."
  }
}

variable "runtm_google_project" {
  description = "GCP project that hosts Runtm's per-organization service accounts (the part after '@' in the granted email). Defaults to Runtm production; override only for Runtm staging."
  type        = string
  default     = "kuimjyxqkrbfvalw-orgids"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.runtm_google_project))
    error_message = "runtm_google_project must be a GCP project id."
  }
}

# ---------------------------------------------------------------------------
# Placement / naming
# ---------------------------------------------------------------------------

variable "project_id" {
  description = "Your GCP project to run sandboxes in. A dedicated project is recommended."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.project_id))
    error_message = "project_id must be a GCP project id."
  }
}

variable "region" {
  description = "GCP region for every resource, e.g. us-west1. One module instance per region; several in one project are told apart by suffix (or are different organizations)."
  type        = string

  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]$", var.region))
    error_message = "region must be a GCP region name, e.g. us-west1."
  }
}

variable "suffix" {
  description = "Optional human-readable label put in front of the per-organization slug in every resource name, e.g. \"pci\" or \"ops\": cluster runtm-sandbox-pci-<slug>. 1-10 lowercase letters, digits or hyphens, starting and ending with a letter or digit, the same rule the Runtm dialog and the AWS and Azure modules enforce, so one value works everywhere (10 keeps the cluster name at 37 of GKE's 40 chars). Enter the same value in the Runtm dialog so its pre-filled names match."
  type        = string
  default     = ""

  validation {
    condition     = var.suffix == "" || can(regex("^[a-z0-9]([a-z0-9-]{0,8}[a-z0-9])?$", var.suffix))
    error_message = "suffix must be 1-10 lowercase letters, digits or hyphens, starting and ending with a letter or digit (the Runtm dialog's rule)."
  }
}

# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

variable "runtm_ingress_cidrs" {
  description = "Runtm's egress addresses. The only sources admitted to the cluster's Kubernetes API (authorized networks) and to the preview router's load balancer. Defaults to Runtm production; override only for Runtm staging."
  type        = list(string)
  default     = ["8.235.110.45/32", "136.70.35.90/32"]

  validation {
    condition = length(var.runtm_ingress_cidrs) > 0 && alltrue([
      for c in var.runtm_ingress_cidrs : can(cidrhost(c, 0)) && !endswith(c, "/0")
    ])
    error_message = "runtm_ingress_cidrs must be one or more CIDRs, none of them /0."
  }
}

# Bring your own network. Leave everything unset and the module creates a
# dedicated VPC, subnet and Cloud NAT (the default, unchanged). Set `network`
# to put the module's subnet in a VPC you already run, or `subnetwork` to use a
# subnet you already have (its secondary ranges named below). Shared VPC: set
# `network_project_id` to the host project.

variable "network" {
  description = "Existing VPC to create the sandbox subnet in: a name, id or self link. Ignored when `subnetwork` is set (the subnet's own VPC is used). null = create a dedicated VPC."
  type        = string
  default     = null
}

variable "subnetwork" {
  description = "Existing subnet for the cluster: a name, id or self link, in `region`. It must have Private Google Access on and the two secondary ranges named by `pods_range_name` and `services_range_name` (sized about /17 and /22). null = create the subnet."
  type        = string
  default     = null
}

variable "network_project_id" {
  description = "Project that owns `network` / `subnetwork` (the Shared VPC host project). null = `project_id`. The host-side grants GKE needs are a prerequisite; see the README."
  type        = string
  default     = null

  validation {
    condition     = var.network_project_id == null || can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.network_project_id))
    error_message = "network_project_id must be a GCP project id."
  }
}

variable "pods_range_name" {
  description = "Secondary range of the subnet for pod IPs. With an existing `subnetwork`, the name of its range; otherwise the name the module gives it."
  type        = string
  default     = "pods"
}

variable "services_range_name" {
  description = "Secondary range of the subnet for Service IPs. With an existing `subnetwork`, the name of its range; otherwise the name the module gives it."
  type        = string
  default     = "services"
}

variable "nat" {
  description = <<-EOT
    Outbound internet for nodes and sandboxes:
      create   - a Cloud Router + Cloud NAT. In a VPC the module creates, it covers every subnet
                 (unchanged); in your VPC, only the sandbox subnet (all of its ranges).
      existing - you already run a Cloud NAT that covers this subnet's primary AND pods range,
                 with enough ports (dynamic port allocation, min 256 per VM is recommended).
      none     - egress goes through your own appliance or proxy; sandboxes need it to pull
                 packages and reach Runtm.
  EOT
  type        = string
  default     = "create"

  validation {
    condition     = contains(["create", "existing", "none"], var.nat)
    error_message = "nat must be create, existing or none."
  }
}

variable "control_plane_access" {
  description = "How Runtm reaches the Kubernetes API. ip = the IP endpoint, allowlisted to runtm_ingress_cidrs (default). dns = the DNS endpoint only (*.gke.goog, Google IAM-authorized); the IP endpoint is turned off and the allowlist goes away. dns needs Runtm's support for DNS endpoints."
  type        = string
  default     = "ip"

  validation {
    condition     = contains(["ip", "dns"], var.control_plane_access)
    error_message = "control_plane_access must be ip or dns."
  }
}

variable "manage_firewall_rules" {
  description = "Create the firewall rule the preview load balancer needs (proxy-only subnet and Google health checks -> the router pods). Set false when someone else owns firewall rules in the network, e.g. a Shared VPC host project; create the rule described in the README instead. Only used with `preview`."
  type        = bool
  default     = true
}

variable "subnet_cidr" {
  description = "Primary range of the sandbox subnet (cluster nodes)."
  type        = string
  default     = "10.77.0.0/20"

  validation {
    condition     = can(cidrhost(var.subnet_cidr, 0))
    error_message = "subnet_cidr must be a valid CIDR."
  }
}

variable "pods_cidr" {
  description = "Secondary range of the sandbox subnet for pod IPs."
  type        = string
  default     = "10.77.128.0/17"

  validation {
    condition     = can(cidrhost(var.pods_cidr, 0))
    error_message = "pods_cidr must be a valid CIDR."
  }
}

variable "services_cidr" {
  description = "Secondary range of the sandbox subnet for Service IPs."
  type        = string
  default     = "10.77.16.0/22"

  validation {
    condition     = can(cidrhost(var.services_cidr, 0))
    error_message = "services_cidr must be a valid CIDR."
  }
}

variable "sandbox_egress_allowed_cidrs" {
  description = "Private ranges sandboxes may reach, e.g. internal services in your VPC. Sandboxes reach only public addresses by default: the NetworkPolicy agent-sandbox manages for every sandbox blocks 10/8, 172.16/12, 192.168/16 and link-local, so joining your network does not expose your workloads. Each range here is allowed on top (all ports) by one extra NetworkPolicy."
  type        = list(string)
  default     = []

  validation {
    condition = alltrue([
      for c in var.sandbox_egress_allowed_cidrs : can(cidrhost(c, 0)) && !endswith(c, "/0")
    ])
    error_message = "sandbox_egress_allowed_cidrs must be CIDRs, none of them /0."
  }
}

variable "snapshot_log_access" {
  description = "Let Runtm read the Pod Snapshot agents' logs in the GKE-managed namespace gke-managed-pod-snapshots, to retarget a paused session's snapshot after a GKE upgrade. Set false if your cluster refuses the Role there; Runtm then restores those sessions from the workspace archive."
  type        = bool
  default     = true
}

# ---------------------------------------------------------------------------
# Previews on your own domain
#
# null (default): Runtm's preview edge reaches the router over an external
# load balancer allowlisted to runtm_ingress_cidrs, and previews are served on
# Runtm's domain.
#
# Set it to serve previews at https://<port>-<sandbox>.<domain> from a Google
# load balancer in your project (GKE Gateway). The router checks Runtm's signed
# preview token itself, so the browser talks to your load balancer directly:
# internal = only from your network (VPN / Interconnect), external = from
# anywhere. Needs Runtm's support for customer preview domains.
# ---------------------------------------------------------------------------

variable "preview" {
  description = "Serve previews on your own domain. See the README (Previews on your own domain)."
  type = object({
    # Sessions are served at <port>-<sandbox>.<domain>, e.g. runtm.rain.xyz.
    domain = string
    # internal: a regional internal load balancer in the sandbox subnet. external: a regional
    # external one.
    exposure = optional(string, "internal")
    # Cloud DNS managed zone for `domain` (name, or projects/<p>/managedZones/<zone>): the module
    # creates *.<domain> (and the certificate's DNS authorization record). null = create the
    # records yourself from the `preview_dns_records` output.
    dns_zone = optional(string)
    certificate = optional(object({
      # google_managed      - Certificate Manager issues *.<domain>. Validated by a DNS
      #                       authorization record, which must be resolvable on the PUBLIC
      #                       internet, unless issuance_config names a private CA.
      # certificate_manager - an existing regional Certificate Manager certificate you manage.
      # pem                 - certificate and key passed in. The key is stored in Terraform
      #                       state; prefer certificate_manager for keys you guard.
      mode            = optional(string, "google_managed")
      issuance_config = optional(string) # google_managed: a Certificate Manager issuance config (private CA)
      certificate     = optional(string) # certificate_manager: name or id, in project_id / region
      pem_certificate = optional(string) # pem: full chain
      pem_private_key = optional(string) # pem
    }), {})
    # Regional Cloud Armor security policy attached to the router backend (external exposure).
    security_policy = optional(string)
    # Proxy-only subnet the regional load balancer needs (one per region per VPC):
    # create = the module creates it, existing = one already exists in the region.
    proxy_only_subnet      = optional(string, "create")
    proxy_only_subnet_cidr = optional(string, "10.77.24.0/23")
  })
  default = null

  validation {
    condition     = var.preview == null || can(regex("^([a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?\\.)+[a-z][-a-z0-9]{0,61}[a-z0-9]$", var.preview.domain))
    error_message = "preview.domain must be a lowercase DNS name such as runtm.example.com."
  }
  validation {
    condition     = var.preview == null || contains(["internal", "external"], try(var.preview.exposure, ""))
    error_message = "preview.exposure must be internal or external."
  }
  validation {
    condition     = var.preview == null || contains(["google_managed", "certificate_manager", "pem"], try(var.preview.certificate.mode, ""))
    error_message = "preview.certificate.mode must be google_managed, certificate_manager or pem."
  }
  validation {
    condition = var.preview == null || (
      try(var.preview.certificate.mode, "") != "certificate_manager" || try(var.preview.certificate.certificate, null) != null
    )
    error_message = "preview.certificate.certificate is required when mode = certificate_manager."
  }
  validation {
    condition = var.preview == null || (
      try(var.preview.certificate.mode, "") != "pem" || (
        try(var.preview.certificate.pem_certificate, null) != null && try(var.preview.certificate.pem_private_key, null) != null
      )
    )
    error_message = "preview.certificate.pem_certificate and pem_private_key are required when mode = pem."
  }
  validation {
    condition     = var.preview == null || contains(["create", "existing"], try(var.preview.proxy_only_subnet, ""))
    error_message = "preview.proxy_only_subnet must be create or existing."
  }
  validation {
    condition     = var.preview == null || can(cidrhost(try(var.preview.proxy_only_subnet_cidr, ""), 0))
    error_message = "preview.proxy_only_subnet_cidr must be a valid CIDR."
  }
  validation {
    condition     = var.preview == null || try(var.preview.security_policy, null) == null || try(var.preview.exposure, "") == "external"
    error_message = "preview.security_policy applies to external exposure only."
  }
}

variable "router_image" {
  description = "Preview router image. null = caddy:2.11.7 for Runtm's edge, and with `preview` Runtm's preview proxy from its public repository (us-west1-docker.pkg.dev/kuimjyxqkrbfvalw-public/runtm-public/sandbox-preview-proxy:latest, pulled on every start; reachable over Private Google Access, no NAT needed). Set it to a copy in your own registry if your policy requires one."
  type        = string
  default     = null
}

variable "runtm_app_origin" {
  description = "Runtm web app origin. With `preview`, the router sends a visitor without a valid preview token there to sign in, and only that origin may embed previews. Defaults to Runtm production; override only for Runtm staging."
  type        = string
  default     = "https://app.runtm.com"

  validation {
    condition     = can(regex("^https://[a-z0-9.-]+(:[0-9]+)?$", var.runtm_app_origin))
    error_message = "runtm_app_origin must be an https origin without a path."
  }
}

variable "runtm_preview_jwks_url" {
  description = "Where Runtm publishes the public keys that sign preview tokens (JWKS). With `preview`, the router verifies tokens against it. null = <runtm_app_origin>/.well-known/runtm-preview-jwks.json."
  type        = string
  default     = null

  validation {
    condition     = var.runtm_preview_jwks_url == null || can(regex("^https://", var.runtm_preview_jwks_url))
    error_message = "runtm_preview_jwks_url must be an https URL."
  }
}

# ---------------------------------------------------------------------------
# Retention (same default as aws-sandbox)
# ---------------------------------------------------------------------------

variable "session_artifact_retention_days" {
  description = "Days to keep paused-session workspace archives (fs-snapshots/sessions/). Default matches Runtm's paused-session retention (90 days)."
  type        = number
  default     = 90

  validation {
    condition     = var.session_artifact_retention_days >= 1
    error_message = "session_artifact_retention_days must be at least 1."
  }
}

variable "labels" {
  description = "Extra labels applied to every resource that takes labels."
  type        = map(string)
  default     = {}
}
