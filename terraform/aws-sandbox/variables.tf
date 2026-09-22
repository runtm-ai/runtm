# ---------------------------------------------------------------------------
# Identity: how Runtm proves it is YOUR organization when it assumes the role.
#
# Runtm mints one Google service account per organization and presents its ID
# token; AWS trusts Google natively (no IAM OIDC provider). The account's email
# is derived from the org id — see locals in main.tf — so
# `runtm_organization_id` alone is enough.
# ---------------------------------------------------------------------------

variable "runtm_organization_id" {
  description = "Your Runtm organization id (Integrations -> AWS Lambda MicroVMs). Derives the Google identity Runtm presents, the token audience, and the resource tags."
  type        = string

  validation {
    condition     = length(var.runtm_organization_id) > 0 && length(var.runtm_organization_id) <= 64
    error_message = "runtm_organization_id must be 1-64 characters."
  }
}

variable "runtm_google_project" {
  description = "GCP project that hosts Runtm's per-organization identity service accounts (the part after '@' in the trusted email). Defaults to Runtm production; override only for Runtm staging or a Runtm deployment on another GCP account."
  type        = string
  default     = "kuimjyxqkrbfvalw-orgids"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{4,28}[a-z0-9]$", var.runtm_google_project))
    error_message = "runtm_google_project must be a GCP project id."
  }
}

variable "runtm_google_subject" {
  description = "Optional: the numeric unique id of your organization's Runtm service account (shown in the Runtm dialog). When set, the trust ALSO pins accounts.google.com:sub — tighter than email alone, at the cost of a re-apply if Runtm ever recreates the account."
  type        = string
  default     = ""

  validation {
    condition     = var.runtm_google_subject == "" || can(regex("^[0-9]{10,30}$", var.runtm_google_subject))
    error_message = "runtm_google_subject must be the 10-30 digit numeric service-account id."
  }
}

variable "runtm_audience" {
  description = "Optional override of the token audience Runtm requests. Defaults to runtm-sandbox:<runtm_organization_id>, which is what Runtm Cloud mints; only change it if your Runtm deployment is configured with a different audience prefix."
  type        = string
  default     = ""

  validation {
    condition     = var.runtm_audience == "" || can(regex("^[A-Za-z0-9:._-]{8,128}$", var.runtm_audience))
    error_message = "runtm_audience must be 8-128 characters of [A-Za-z0-9:._-]."
  }
}

# ---------------------------------------------------------------------------
# Naming / retention
# ---------------------------------------------------------------------------

variable "artifact_bucket_name" {
  description = "Override the artifact bucket name. Default runtm-sandbox-<account>-<region> matches the CloudFormation stack and what the Runtm dialog pre-fills; override when that name is taken (e.g. a second stack in the same account+region) and paste the output into the dialog's Artifact bucket field."
  type        = string
  default     = ""

  validation {
    condition     = var.artifact_bucket_name == "" || can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.artifact_bucket_name))
    error_message = "artifact_bucket_name must be a valid S3 bucket name."
  }
}

variable "name_suffix" {
  description = "Suffix for the three role names (RuntmSandboxAccessRole-<suffix> ...). Defaults to the provider's region, matching the CloudFormation template, so one stack per region never collides."
  type        = string
  default     = ""
}

variable "artifact_retention_days" {
  description = "Days to keep template/image build artifacts (images/ prefix) in the bucket."
  type        = number
  default     = 90

  validation {
    condition     = var.artifact_retention_days >= 7
    error_message = "artifact_retention_days must be at least 7."
  }
}

variable "session_artifact_retention_days" {
  description = "Days to keep paused-session filesystem snapshots (sessions/ prefix). Default matches Runtm's paused-session retention (90 days)."
  type        = number
  default     = 90

  validation {
    condition     = var.session_artifact_retention_days >= 1
    error_message = "session_artifact_retention_days must be at least 1."
  }
}

variable "template_versions_to_keep" {
  description = "Template goldens (templates/ prefix) never expire; each rebuild overwrites the object and the bucket is versioned. This many PREVIOUS versions are kept for rollback before older ones are purged."
  type        = number
  default     = 3

  validation {
    condition     = var.template_versions_to_keep >= 1 && var.template_versions_to_keep <= 100
    error_message = "template_versions_to_keep must be between 1 and 100."
  }
}

variable "template_version_retention_days" {
  description = "Days a superseded template golden version is kept once it is older than the template_versions_to_keep newest ones."
  type        = number
  default     = 30

  validation {
    condition     = var.template_version_retention_days >= 1
    error_message = "template_version_retention_days must be at least 1."
  }
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

variable "create_log_group" {
  description = "Create the CloudWatch log group MicroVM app logs are written to. The execution role may write to /runtm/* regardless."
  type        = bool
  default     = true
}

variable "log_group_name" {
  description = "CloudWatch log group for sandbox app logs. Must start with /runtm/ to match the execution role's policy."
  type        = string
  default     = "/runtm/sandboxes"

  validation {
    condition     = startswith(var.log_group_name, "/runtm/")
    error_message = "log_group_name must start with /runtm/ (the execution role only writes there)."
  }
}

# ---------------------------------------------------------------------------
# Optional egress networking (ingress/egress/firewall on YOUR side)
#
# Off by default: sandboxes then use the AWS-managed internet egress. Turn it
# on to route sandbox traffic through a VPC you control, with a security group
# that only allows the CIDRs you list. See README "Egress" for the connector
# step that links these subnets to Lambda MicroVMs.
# ---------------------------------------------------------------------------

variable "create_vpc_egress" {
  description = "Create a VPC (2 private subnets behind a NAT gateway, 1 public subnet) and a security group whose egress is limited to egress_allowed_cidrs."
  type        = bool
  default     = false
}

variable "vpc_cidr" {
  description = "CIDR for the egress VPC."
  type        = string
  default     = "10.77.0.0/16"
}

variable "private_subnet_cidrs" {
  description = "Two private subnet CIDRs (one per AZ) inside vpc_cidr. Sandboxes attach here."
  type        = list(string)
  default     = ["10.77.0.0/20", "10.77.16.0/20"]

  validation {
    condition     = length(var.private_subnet_cidrs) == 2
    error_message = "private_subnet_cidrs must list exactly two CIDRs."
  }
}

variable "public_subnet_cidr" {
  description = "Public subnet CIDR hosting the NAT gateway."
  type        = string
  default     = "10.77.255.0/24"
}

variable "egress_allowed_cidrs" {
  description = "Destination CIDRs sandboxes may reach (security group egress). Default = everything; tighten to your allow-list."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "egress_connector_arn" {
  description = "ARN of the Lambda MicroVMs network connector you created on the private subnets (see README). Passed through to the runtm_connection output so it lands in the connect dialog."
  type        = string
  default     = null
}

variable "tags" {
  description = "Extra tags applied to every resource."
  type        = map(string)
  default     = {}
}
