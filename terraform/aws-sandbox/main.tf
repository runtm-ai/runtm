# Runtm Cloud - AWS Lambda MicroVMs sandbox integration (customer side).
#
# Terraform twin of the CloudFormation quick-create stack Runtm hands out. It
# creates, in YOUR account: the access role Runtm assumes, the MicroVM build
# and execution roles, an artifact bucket, and optionally an egress VPC.
# Nothing here grants Runtm access outside Lambda MicroVMs and that bucket.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  # aws >= 6 renamed .name to .region and deprecates the old attribute; support both.
  region = try(data.aws_region.current.region, data.aws_region.current.name)
  suffix = var.name_suffix != "" ? var.name_suffix : local.region

  google_mode = var.runtm_google_subject != "" && var.runtm_audience != ""
  legacy_mode = var.runtm_hub_role_arn != "" && var.runtm_external_id != ""

  bucket_name = "runtm-sandbox-${local.account_id}-${local.region}"

  tags = merge(var.tags, {
    "runtm:organization" = var.runtm_organization_id
  })
}

# Exactly one identity mode. Terraform variable validation cannot see other
# variables, so this is a data-source precondition instead: it fails the plan
# before any resource is touched.
data "aws_iam_policy_document" "identity_mode_guard" {
  statement {
    sid     = "Placeholder"
    effect  = "Deny"
    actions = ["sts:GetCallerIdentity"]
  }

  lifecycle {
    precondition {
      condition     = local.google_mode != local.legacy_mode
      error_message = "Set exactly one identity: runtm_google_subject + runtm_audience (preferred) OR runtm_hub_role_arn + runtm_external_id (legacy)."
    }
    precondition {
      condition     = !(var.runtm_google_subject != "" && var.runtm_audience == "") && !(var.runtm_google_subject == "" && var.runtm_audience != "")
      error_message = "runtm_google_subject and runtm_audience must be set together."
    }
    precondition {
      condition     = !(var.runtm_hub_role_arn != "" && var.runtm_external_id == "") && !(var.runtm_hub_role_arn == "" && var.runtm_external_id != "")
      error_message = "runtm_hub_role_arn and runtm_external_id must be set together."
    }
  }
}

# ---------------------------------------------------------------------------
# Artifact bucket: image build contexts (images/) + paused-session snapshots
# (sessions/). Encrypted, private, lifecycle-expired.
# ---------------------------------------------------------------------------

resource "aws_s3_bucket" "artifacts" {
  bucket = local.bucket_name
  tags   = merge(local.tags, { "runtm:component" = "sandbox-artifacts" })

  depends_on = [data.aws_iam_policy_document.identity_mode_guard]
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id

  rule {
    id     = "expire-image-artifacts"
    status = "Enabled"
    filter {
      prefix = "images/"
    }
    expiration {
      days = var.artifact_retention_days
    }
  }

  rule {
    id     = "expire-session-snapshots"
    status = "Enabled"
    filter {
      prefix = "sessions/"
    }
    expiration {
      days = var.session_artifact_retention_days
    }
  }

  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 2
    }
  }
}

data "aws_iam_policy_document" "artifacts_bucket" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.artifacts.arn,
      "${aws_s3_bucket.artifacts.arn}/*",
    ]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  policy = data.aws_iam_policy_document.artifacts_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.artifacts]
}

# ---------------------------------------------------------------------------
# Roles the Lambda service assumes on your behalf
# ---------------------------------------------------------------------------

data "aws_iam_policy_document" "lambda_service_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

# Assumed by the Lambda service while it builds a MicroVM image from the zip
# Runtm uploads to the artifact bucket.
data "aws_iam_policy_document" "build" {
  statement {
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.artifacts.arn}/images/*"]
  }
  statement {
    effect  = "Allow"
    actions = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = [
      "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda-microvms/*",
      "arn:aws:logs:${local.region}:${local.account_id}:log-group:/runtm/*",
    ]
  }
}

resource "aws_iam_role" "build" {
  name               = "RuntmMicrovmBuildRole-${local.suffix}"
  assume_role_policy = data.aws_iam_policy_document.lambda_service_trust.json
  tags               = local.tags
}

resource "aws_iam_role_policy" "build" {
  name   = "runtm-microvm-build"
  role   = aws_iam_role.build.id
  policy = data.aws_iam_policy_document.build.json
}

# Assumed by the MicroVM itself at runtime. Deliberately minimal: the sandbox
# gets CloudWatch logging and nothing else. Add statements if your agents
# should reach other AWS services from inside the sandbox.
data "aws_iam_policy_document" "execution" {
  statement {
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${local.region}:${local.account_id}:log-group:/runtm/*"]
  }
}

resource "aws_iam_role" "execution" {
  name               = "RuntmMicrovmExecutionRole-${local.suffix}"
  assume_role_policy = data.aws_iam_policy_document.lambda_service_trust.json
  tags               = local.tags
}

resource "aws_iam_role_policy" "execution" {
  name   = "runtm-microvm-exec"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution.json
}

# ---------------------------------------------------------------------------
# The role Runtm's control plane assumes
# ---------------------------------------------------------------------------

# Trust: Google-identity mode pins the role to your organization's Google
# service account (subject) and the audience Runtm requests. Google is a
# built-in web-identity provider in AWS, so no IAM OIDC provider resource is
# needed. aud AND oaud are bound because AWS maps the token's aud field to
# both keys when azp is absent (service-account tokens). Legacy mode trusts
# Runtm's single hub role, scoped by the per-organization external id.
data "aws_iam_policy_document" "access_trust" {
  dynamic "statement" {
    for_each = local.google_mode ? [1] : []
    content {
      sid     = "RuntmOrgGoogleIdentity"
      effect  = "Allow"
      actions = ["sts:AssumeRoleWithWebIdentity", "sts:TagSession"]
      principals {
        type        = "Federated"
        identifiers = ["accounts.google.com"]
      }
      condition {
        test     = "StringEquals"
        variable = "accounts.google.com:sub"
        values   = [var.runtm_google_subject]
      }
      condition {
        test     = "StringEquals"
        variable = "accounts.google.com:aud"
        values   = [var.runtm_audience]
      }
      condition {
        test     = "StringEquals"
        variable = "accounts.google.com:oaud"
        values   = [var.runtm_audience]
      }
    }
  }

  dynamic "statement" {
    for_each = local.legacy_mode ? [1] : []
    content {
      sid     = "RuntmHubRoleExternalId"
      effect  = "Allow"
      actions = ["sts:AssumeRole", "sts:TagSession"]
      principals {
        type        = "AWS"
        identifiers = [var.runtm_hub_role_arn]
      }
      condition {
        test     = "StringEquals"
        variable = "sts:ExternalId"
        values   = [var.runtm_external_id]
      }
    }
  }
}

data "aws_iam_policy_document" "access" {
  statement {
    sid    = "Discover"
    effect = "Allow"
    actions = [
      "lambda:ListMicrovms",
      "lambda:ListMicrovmImages",
      "lambda:ListManagedMicrovmImages",
      "lambda:ListManagedMicrovmImageVersions",
      "sts:GetCallerIdentity",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "ManageRuntmImages"
    effect = "Allow"
    actions = [
      "lambda:CreateMicrovmImage",
      "lambda:UpdateMicrovmImage",
      "lambda:UpdateMicrovmImageVersion",
      "lambda:DeleteMicrovmImage",
      "lambda:DeleteMicrovmImageVersion",
      "lambda:GetMicrovmImage",
      "lambda:GetMicrovmImageVersion",
      "lambda:GetMicrovmImageBuild",
      "lambda:ListMicrovmImageVersions",
      "lambda:ListMicrovmImageBuilds",
      "lambda:TagResource",
      "lambda:UntagResource",
      "lambda:ListTags",
    ]
    resources = ["arn:aws:lambda:${local.region}:${local.account_id}:microvm-image:runtm-*"]
  }

  statement {
    sid    = "RunSandboxes"
    effect = "Allow"
    actions = [
      "lambda:RunMicrovm",
      "lambda:GetMicrovm",
      "lambda:SuspendMicrovm",
      "lambda:ResumeMicrovm",
      "lambda:TerminateMicrovm",
      "lambda:CreateMicrovmAuthToken",
      "lambda:CreateMicrovmShellAuthToken",
    ]
    resources = ["*"]
  }

  statement {
    sid     = "PassMicrovmRoles"
    effect  = "Allow"
    actions = ["iam:PassRole"]
    resources = [
      aws_iam_role.build.arn,
      aws_iam_role.execution.arn,
    ]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["lambda.amazonaws.com"]
    }
  }

  statement {
    sid       = "Artifacts"
    effect    = "Allow"
    actions   = ["s3:PutObject", "s3:GetObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
    resources = ["${aws_s3_bucket.artifacts.arn}/*"]
  }

  statement {
    sid       = "ArtifactBucketMeta"
    effect    = "Allow"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.artifacts.arn]
  }

  statement {
    sid     = "ReadBuildLogs"
    effect  = "Allow"
    actions = ["logs:FilterLogEvents", "logs:GetLogEvents", "logs:DescribeLogStreams"]
    resources = [
      "arn:aws:logs:${local.region}:${local.account_id}:log-group:/aws/lambda-microvms/*",
      "arn:aws:logs:${local.region}:${local.account_id}:log-group:/runtm/*",
    ]
  }
}

resource "aws_iam_role" "access" {
  name               = "RuntmSandboxAccessRole-${local.suffix}"
  assume_role_policy = data.aws_iam_policy_document.access_trust.json
  # 12h in Google mode: AssumeRoleWithWebIdentity is not role chaining, so the
  # 1h chaining cap does not apply. Legacy hub-role AssumeRole IS chaining and
  # is capped at 1h by STS regardless of this value.
  max_session_duration = local.google_mode ? 43200 : 3600
  tags                 = local.tags
}

resource "aws_iam_role_policy" "access" {
  name   = "runtm-sandbox-access"
  role   = aws_iam_role.access.id
  policy = data.aws_iam_policy_document.access.json
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

resource "aws_cloudwatch_log_group" "sandboxes" {
  count = var.create_log_group ? 1 : 0

  name              = var.log_group_name
  retention_in_days = var.session_artifact_retention_days
  tags              = local.tags
}

# ---------------------------------------------------------------------------
# Optional egress VPC: private subnets behind a NAT gateway, security group
# restricted to your allow-list. Linking these subnets to Lambda MicroVMs is a
# separate "network connector" step (README) because the AWS provider has no
# resource for it yet; pass the resulting ARN back via egress_connector_arn.
# ---------------------------------------------------------------------------

data "aws_availability_zones" "available" {
  count = var.create_vpc_egress ? 1 : 0
  state = "available"
}

resource "aws_vpc" "egress" {
  count = var.create_vpc_egress ? 1 : 0

  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.tags, { Name = "runtm-sandbox-egress" })
}

resource "aws_internet_gateway" "egress" {
  count = var.create_vpc_egress ? 1 : 0

  vpc_id = aws_vpc.egress[0].id
  tags   = merge(local.tags, { Name = "runtm-sandbox-egress" })
}

resource "aws_subnet" "public" {
  count = var.create_vpc_egress ? 1 : 0

  vpc_id            = aws_vpc.egress[0].id
  cidr_block        = var.public_subnet_cidr
  availability_zone = data.aws_availability_zones.available[0].names[0]
  tags              = merge(local.tags, { Name = "runtm-sandbox-egress-public" })
}

resource "aws_subnet" "private" {
  count = var.create_vpc_egress ? 2 : 0

  vpc_id            = aws_vpc.egress[0].id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = data.aws_availability_zones.available[0].names[count.index]
  tags              = merge(local.tags, { Name = "runtm-sandbox-egress-private-${count.index}" })
}

resource "aws_eip" "nat" {
  count = var.create_vpc_egress ? 1 : 0

  domain = "vpc"
  tags   = merge(local.tags, { Name = "runtm-sandbox-egress-nat" })

  depends_on = [aws_internet_gateway.egress]
}

resource "aws_nat_gateway" "egress" {
  count = var.create_vpc_egress ? 1 : 0

  allocation_id = aws_eip.nat[0].id
  subnet_id     = aws_subnet.public[0].id
  tags          = merge(local.tags, { Name = "runtm-sandbox-egress" })

  depends_on = [aws_internet_gateway.egress]
}

resource "aws_route_table" "public" {
  count = var.create_vpc_egress ? 1 : 0

  vpc_id = aws_vpc.egress[0].id
  tags   = merge(local.tags, { Name = "runtm-sandbox-egress-public" })
}

resource "aws_route" "public_default" {
  count = var.create_vpc_egress ? 1 : 0

  route_table_id         = aws_route_table.public[0].id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.egress[0].id
}

resource "aws_route_table_association" "public" {
  count = var.create_vpc_egress ? 1 : 0

  subnet_id      = aws_subnet.public[0].id
  route_table_id = aws_route_table.public[0].id
}

resource "aws_route_table" "private" {
  count = var.create_vpc_egress ? 1 : 0

  vpc_id = aws_vpc.egress[0].id
  tags   = merge(local.tags, { Name = "runtm-sandbox-egress-private" })
}

resource "aws_route" "private_default" {
  count = var.create_vpc_egress ? 1 : 0

  route_table_id         = aws_route_table.private[0].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.egress[0].id
}

resource "aws_route_table_association" "private" {
  count = var.create_vpc_egress ? 2 : 0

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[0].id
}

# No ingress: nothing reaches a sandbox from the network. Egress limited to
# the CIDRs you allow; the NAT gateway is the only path out.
resource "aws_security_group" "sandbox" {
  count = var.create_vpc_egress ? 1 : 0

  name        = "runtm-sandbox-egress"
  description = "Runtm sandboxes: no ingress, egress restricted to the allow-list"
  vpc_id      = aws_vpc.egress[0].id
  tags        = merge(local.tags, { Name = "runtm-sandbox-egress" })
}

resource "aws_vpc_security_group_egress_rule" "sandbox" {
  for_each = var.create_vpc_egress ? toset(var.egress_allowed_cidrs) : toset([])

  security_group_id = aws_security_group.sandbox[0].id
  cidr_ipv4         = each.value
  ip_protocol       = "-1"
  description       = "Runtm sandbox egress allow-list"
  tags              = local.tags
}
