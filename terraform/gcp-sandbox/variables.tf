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
# Placement
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
  description = "GCP region for every resource (one module instance per project), e.g. us-west1."
  type        = string

  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]$", var.region))
    error_message = "region must be a GCP region name, e.g. us-west1."
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
