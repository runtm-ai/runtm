# ---------------------------------------------------------------------------
# Identity: how Runtm proves it is YOUR organization when it acts in your
# subscription.
#
# Runtm mints one Google service account per organization and presents its ID
# token to Entra, which swaps it for tokens of the broker managed identity
# created here (workload identity federation; no Runtm Entra app, no secret).
# Unlike AWS, Entra has no email-based trust condition: a federated credential
# matches issuer + subject + audience exactly, and the subject is the Google
# account's NUMERIC unique id, which cannot be derived from the organization
# id. So on Azure `runtm_google_subject` is REQUIRED (the Runtm dialog shows it).
# ---------------------------------------------------------------------------

variable "runtm_organization_id" {
  description = "Your Runtm organization id (Integrations -> Azure Sandboxes). Derives the per-organization resource slug and the resource tags."
  type        = string

  validation {
    condition     = length(var.runtm_organization_id) > 0 && length(var.runtm_organization_id) <= 64
    error_message = "runtm_organization_id must be 1-64 characters."
  }
}

variable "runtm_google_subject" {
  description = "Numeric unique id of the ONE Google service account Runtm presents to every tenant (shown in the Runtm dialog; the same for every organization — the audience is what keeps stacks apart). Entra federated credentials match the token's `sub` exactly; there is no email condition as on AWS."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{10,30}$", var.runtm_google_subject))
    error_message = "runtm_google_subject must be the 10-30 digit numeric service-account id."
  }
}

variable "runtm_google_issuer" {
  description = "Issuer of the tokens Runtm presents. Google service-account ID tokens are issued by https://accounts.google.com; do not change."
  type        = string
  default     = "https://accounts.google.com"

  validation {
    condition     = can(regex("^https://[a-z0-9.-]+(/.*)?$", var.runtm_google_issuer))
    error_message = "runtm_google_issuer must be an https URL."
  }
}

variable "runtm_audience" {
  description = "Audience of the Google token Runtm mints for THIS organization. Empty (the default) = runtm-sandbox:<runtm_organization_id>, which is what Runtm mints; the federated credential accepts only it, so a token minted for another organization is refused (AADSTS700212). Only set it if your Runtm deployment is configured with a different audience prefix."
  type        = string
  default     = ""

  validation {
    condition     = var.runtm_audience == "" || can(regex("^[A-Za-z0-9:/._-]{8,600}$", var.runtm_audience))
    error_message = "runtm_audience must be empty or 8-600 characters of [A-Za-z0-9:/._-]."
  }
}

# ---------------------------------------------------------------------------
# Placement / naming
# ---------------------------------------------------------------------------

variable "location" {
  description = "Azure region for every resource (one module instance per region). Must offer Container Apps Sandboxes, e.g. westus2, eastus, swedencentral."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{3,24}$", var.location))
    error_message = "location must be an Azure region name (lowercase, no spaces), e.g. westus2."
  }
}

variable "suffix" {
  description = "Optional human-readable label put in front of the per-organization slug in resource names, e.g. \"pci\" or \"ops\": runtm-sandbox-pci-<slug>-<region>, sandbox group rtm-pci-<slug>. 1-10 lowercase letters, digits or hyphens, starting and ending with a letter or digit — the same rule the Runtm dialog, the Deploy-to-Azure wizard and the AWS module enforce, so one value works everywhere. Enter the same value in the Runtm dialog so its pre-filled names match."
  type        = string
  default     = ""

  validation {
    condition     = var.suffix == "" || can(regex("^[a-z0-9]([a-z0-9-]{0,8}[a-z0-9])?$", var.suffix))
    error_message = "suffix must be 1-10 lowercase letters, digits or hyphens, starting and ending with a letter or digit (the Runtm dialog's rule)."
  }
}

variable "storage_account_name" {
  description = "Override the storage account name. Default rtm<slug><disc> (3 + 12 + 9 = 24 chars, the storage maximum; disc = sha256(org|suffix|region)[:9]) matches the Bicep template and what the Runtm dialog pre-fills. Only needed when that name is somehow taken; paste the output into the dialog."
  type        = string
  default     = ""

  validation {
    condition     = var.storage_account_name == "" || can(regex("^[a-z0-9]{3,24}$", var.storage_account_name))
    error_message = "storage_account_name must be 3-24 lowercase letters or digits."
  }
}

variable "registry_name" {
  description = "Override the container registry name. Default rtmacr<slug><disc> (27 chars; ACR allows 5-50 alphanumerics, no hyphens). Only needed when that name is taken."
  type        = string
  default     = ""

  validation {
    condition     = var.registry_name == "" || can(regex("^[a-zA-Z0-9]{5,50}$", var.registry_name))
    error_message = "registry_name must be 5-50 letters or digits."
  }
}

# ---------------------------------------------------------------------------
# Retention
#
# The storage container holds paused-session workspace layers (sessions/) and
# template goldens (templates/). Image build contexts have no prefix here:
# images are pushed to the container registry instead of staged in a bucket.
# ---------------------------------------------------------------------------

variable "session_artifact_retention_days" {
  description = "Days to keep paused-session workspace layers (sessions/ prefix). Default matches Runtm's paused-session retention (90 days)."
  type        = number
  default     = 90

  validation {
    condition     = var.session_artifact_retention_days >= 1
    error_message = "session_artifact_retention_days must be at least 1."
  }
}

variable "template_version_retention_days" {
  description = "Template goldens (templates/ prefix) never expire; each rebuild overwrites the blob and the container is versioned. Superseded versions are purged this many days after they were created. Azure blob lifecycle has no \"keep the newest N versions\" rule (AWS template_versions_to_keep has no twin); Runtm prunes by count on its side if needed."
  type        = number
  default     = 30

  validation {
    condition     = var.template_version_retention_days >= 1
    error_message = "template_version_retention_days must be at least 1."
  }
}

variable "storage_shared_key_access" {
  description = "Allow Shared Key (account key) authorization on the storage account. Runtm never uses keys (it signs user-delegation SAS with the broker identity), so the default disables them; every data-plane call then needs an Entra identity with a blob data role."
  type        = bool
  default     = false
}

# ---------------------------------------------------------------------------
# Sandbox group identity attachment
# ---------------------------------------------------------------------------

variable "attach_identity_by_patch" {
  description = "Also attach the broker identity to the sandbox group with a PATCH {\"identity\": ...} after creation (the sequence the 2026-10-07 spike proved against api-version 2026-07-01, whose published schema has no identity block). The group is created with the identity in the PUT envelope as well; keep this on until PUT-with-identity on 2026-07-01 is verified."
  type        = bool
  default     = true
}

# ---------------------------------------------------------------------------
# Optional VNet (the private-database leg: sandboxes reach resources on YOUR
# network). Off by default: sandboxes then egress through the service's proxy
# under the per-sandbox egress policy Runtm sets. Turn it on to get a VNet with
# one subnet delegated to Microsoft.App/environments and a vnetConnections
# child on the sandbox group pointing at it. The subnet id is immutable once
# connected; size it for peak concurrent sandboxes (a /23 = 500-ish).
# ---------------------------------------------------------------------------

variable "create_vnet" {
  description = "Create a VNet with a delegated subnet and connect the sandbox group to it."
  type        = bool
  default     = false
}

variable "vnet_cidr" {
  description = "Address space of the VNet."
  type        = string
  default     = "10.77.0.0/16"

  validation {
    condition     = can(cidrhost(var.vnet_cidr, 0))
    error_message = "vnet_cidr must be a valid CIDR."
  }
}

variable "subnet_cidr" {
  description = "Delegated subnet sandboxes attach to, inside vnet_cidr. Keep at least a /23 (Container Apps delegation needs headroom; one subnet maps to one sandbox group)."
  type        = string
  default     = "10.77.0.0/23"

  validation {
    condition     = can(cidrhost(var.subnet_cidr, 0)) && tonumber(split("/", var.subnet_cidr)[1]) <= 27
    error_message = "subnet_cidr must be a valid CIDR no smaller than /27 (a /23 is recommended)."
  }
}

variable "tags" {
  description = "Extra tags applied to every resource."
  type        = map(string)
  default     = {}
}
