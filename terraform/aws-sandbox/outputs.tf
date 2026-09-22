output "role_arn" {
  description = "Paste into Runtm (Integrations -> AWS Lambda MicroVMs): the access role Runtm assumes."
  value       = aws_iam_role.access.arn
}

output "artifact_bucket" {
  description = "S3 bucket for image build artifacts and paused-session snapshots."
  value       = aws_s3_bucket.artifacts.bucket
}

output "build_role_arn" {
  description = "Role the Lambda service assumes to build MicroVM images."
  value       = aws_iam_role.build.arn
}

output "execution_role_arn" {
  description = "Role the MicroVM runs as (CloudWatch logging only by default)."
  value       = aws_iam_role.execution.arn
}

output "account_id" {
  description = "AWS account the resources were created in."
  value       = local.account_id
}

output "region" {
  description = "AWS region the resources were created in."
  value       = local.region
}

output "log_group" {
  description = "CloudWatch log group for sandbox app logs (null when create_log_group = false)."
  value       = var.create_log_group ? aws_cloudwatch_log_group.sandboxes[0].name : null
}

output "vpc_id" {
  description = "Egress VPC id (null unless create_vpc_egress)."
  value       = var.create_vpc_egress ? aws_vpc.egress[0].id : null
}

output "private_subnet_ids" {
  description = "Private subnet ids to attach the Lambda MicroVMs network connector to (empty unless create_vpc_egress)."
  value       = var.create_vpc_egress ? aws_subnet.private[*].id : []
}

output "security_group_id" {
  description = "Security group for the network connector (null unless create_vpc_egress)."
  value       = var.create_vpc_egress ? aws_security_group.sandbox[0].id : null
}

output "egress_connector_arn" {
  description = "The network connector ARN you passed in (null until you create one; see README)."
  value       = var.egress_connector_arn
}

output "runtm_connection" {
  description = "Everything the Runtm connect dialog asks for, in one object."
  value = {
    region               = local.region
    account_id           = local.account_id
    role_arn             = aws_iam_role.access.arn
    artifact_bucket      = aws_s3_bucket.artifacts.bucket
    build_role_arn       = aws_iam_role.build.arn
    execution_role_arn   = aws_iam_role.execution.arn
    log_group            = var.create_log_group ? aws_cloudwatch_log_group.sandboxes[0].name : null
    egress_connector_arn = var.egress_connector_arn
    identity             = local.google_mode ? "google_oidc" : "iam_role"
    google_subject       = local.google_mode ? var.runtm_google_subject : null
    audience             = local.google_mode ? var.runtm_audience : null
  }
}
