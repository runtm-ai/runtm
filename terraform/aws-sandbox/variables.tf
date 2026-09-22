# ---------------------------------------------------------------------------
# Identity: how Runtm proves it is YOUR organization when it assumes the role.
#
# Preferred: Runtm mints one Google service account per organization and
# presents its ID token; AWS trusts Google natively (no IAM OIDC provider), so
# the trust policy names an identity that only your organization can produce.
# Legacy: a single Runtm hub role + a per-organization external id.
# Exactly one of the two pairs must be set (checked in main.tf).
# ---------------------------------------------------------------------------

variable "runtm_organization_id" {
  description = "Your Runtm organization id. Copied from the Runtm connect dialog; used for tagging and to derive the default audience."
  type        = string

  validation {
    condition     = length(var.runtm_organization_id) > 0 && length(var.runtm_organization_id) <= 64
    error_message = "runtm_organization_id must be 1-64 characters."
  }
}

variable "runtm_google_subject" {
  description = "Numeric unique id of your organization's Runtm Google service account (pre-filled in the Runtm connect dialog). Google-identity mode."
  type        = string
  default     = ""

  validation {
    condition     = var.runtm_google_subject == "" || can(regex("^[0-9]{10,30}$", var.runtm_google_subject))
    error_message = "runtm_google_subject must be the 10-30 digit numeric service-account id."
  }
}

variable "runtm_audience" {
  description = "Audience Runtm requests when minting the Google token for this organization (pre-filled, runtm-sandbox:<org>). Google-identity mode."
  type        = string
  default     = ""

  validation {
    condition     = var.runtm_audience == "" || can(regex("^[A-Za-z0-9:._-]{8,128}$", var.runtm_audience))
    error_message = "runtm_audience must be 8-128 characters of [A-Za-z0-9:._-]."
  }
}

variable "runtm_hub_role_arn" {
  description = "LEGACY (v1) mode: the single Runtm hub role allowed to AssumeRole. Leave empty when using runtm_google_subject."
  type        = string
  default     = ""

  validation {
    condition     = var.runtm_hub_role_arn == "" || can(regex("^arn:aws:iam::[0-9]{12}:role/.+$", var.runtm_hub_role_arn))
    error_message = "runtm_hub_role_arn must be an IAM role ARN."
  }
}

variable "runtm_external_id" {
  description = "LEGACY (v1) mode: per-organization external id minted by Runtm. Leave empty when using runtm_google_subject."
  type        = string
  default     = ""

  validation {
    condition     = var.runtm_external_id == "" || can(regex("^[A-Za-z0-9+=,.@:/_-]{16,128}$", var.runtm_external_id))
    error_message = "runtm_external_id must be 16-128 characters of [A-Za-z0-9+=,.@:/_-]."
  }
}

# ---------------------------------------------------------------------------
# Naming / retention
# ---------------------------------------------------------------------------

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
  description = "Days to keep paused-session filesystem snapshots (sessions/ prefix) in the bucket."
  type        = number
  default     = 30

  validation {
    condition     = var.session_artifact_retention_days >= 1
    error_message = "session_artifact_retention_days must be at least 1."
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
