# Plans the access role's policy with realistic inputs and checks what Runtm
# may pass and read. Plans only, against overridden identities, so it needs no
# AWS account: `terraform init -backend=false && terraform test`.
#
# The policy document's JSON is only read at apply (it references resources
# that don't exist yet), so the checks read its planned statement blocks: the
# same statements, with the account and role ARNs filled in.
#
# The CloudFormation twin is covered by tests/test_access_policy.py; both must
# grant the same thing.

provider "aws" {
  region                      = "us-east-1"
  access_key                  = "test"
  secret_key                  = "test"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
  skip_region_validation      = true
}

# override_during = plan makes these values known while planning, so the
# policy document renders without an apply.
override_data {
  target          = data.aws_caller_identity.current
  override_during = plan
  values = {
    account_id = "123456789012"
  }
}

override_data {
  target          = data.aws_region.current
  override_during = plan
  values = {
    name   = "us-east-1"
    region = "us-east-1"
  }
}

override_resource {
  target          = aws_iam_role.build
  override_during = plan
  values = {
    arn = "arn:aws:iam::123456789012:role/RuntmMicrovmBuildRole-test"
  }
}

override_resource {
  target          = aws_iam_role.execution
  override_during = plan
  values = {
    arn = "arn:aws:iam::123456789012:role/RuntmMicrovmExecutionRole-test"
  }
}

override_resource {
  target          = aws_s3_bucket.artifacts
  override_during = plan
  values = {
    arn = "arn:aws:s3:::runtm-sandbox-test-us-east-1"
  }
}

variables {
  runtm_organization_id = "scrPBJ9qoiTMGwUIou1tFXLxQLtlneym"
  create_vpc_egress     = false
}

run "access_policy" {
  command = plan

  assert {
    condition = one([
      for s in data.aws_iam_policy_document.access.statement : s
      if s.sid == "PassMicrovmRoles"
      ]).resources == toset([
      "arn:aws:iam::123456789012:role/RuntmMicrovmBuildRole-test",
      "arn:aws:iam::123456789012:role/RuntmMicrovmExecutionRole-test",
      "arn:aws:iam::123456789012:role/runtm-sandbox/*",
    ])
    error_message = "PassMicrovmRoles must name exactly the build role, the execution role and the /runtm-sandbox/ path."
  }

  assert {
    condition = one([
      for s in data.aws_iam_policy_document.access.statement : s
      if s.sid == "PassMicrovmRoles"
    ]).actions == toset(["iam:PassRole"])
    error_message = "PassMicrovmRoles must grant iam:PassRole and nothing else."
  }

  assert {
    condition = alltrue([
      for s in data.aws_iam_policy_document.access.statement :
      s.sid != "ReadOwnStackRevision" || (
        s.effect == "Allow"
        && s.actions == toset(["iam:ListRoleTags"])
        && s.resources == toset(["arn:aws:iam::123456789012:role/RuntmSandboxAccessRole-*"])
      )
      ]) && length([
      for s in data.aws_iam_policy_document.access.statement : s
      if s.sid == "ReadOwnStackRevision"
    ]) == 1
    error_message = "ReadOwnStackRevision must allow only iam:ListRoleTags on the access role."
  }

  assert {
    condition     = startswith(aws_iam_role.access.name, "RuntmSandboxAccessRole-")
    error_message = "The access role's name must match ReadOwnStackRevision's RuntmSandboxAccessRole-* resource."
  }

  assert {
    condition     = aws_iam_role.access.tags["runtm:stack-revision"] == "2"
    error_message = "The access role must carry runtm:stack-revision = 2 (same as the CloudFormation template)."
  }
}
