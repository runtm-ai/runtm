# runtm/aws-sandbox

Terraform module for running Runtm Cloud sandboxes (AWS Lambda MicroVMs) in
your own AWS account. It creates the IAM roles Runtm needs and an S3 bucket
for sandbox artifacts. Nothing else.

## Usage

```hcl
module "runtm_sandbox" {
  source = "git::https://github.com/runtm-ai/runtm.git//terraform/aws-sandbox?ref=main"

  runtm_organization_id = "<org id>"
}

output "runtm_connection" {
  value = module.runtm_sandbox.runtm_connection
}
```

Your organization id is in Runtm under **Integrations → AWS Lambda MicroVMs**.
Apply, then paste `terraform output runtm_connection` into that dialog.

The module uses the region your `aws` provider is configured for. Supported
regions: `us-east-1`, `us-east-2`, `us-west-2`, `eu-west-1`, `ap-northeast-1`.
One module instance per region. Pin `ref=` to a tag for reproducible applies.

## Optional egress VPC

Route sandbox traffic through a VPC you control:

```hcl
module "runtm_sandbox" {
  source = "git::https://github.com/runtm-ai/runtm.git//terraform/aws-sandbox?ref=main"

  runtm_organization_id = "<org id>"

  create_vpc_egress    = true
  egress_allowed_cidrs = ["0.0.0.0/0"] # tighten to your allow-list
}
```

Then create the network connector and pass its ARN back as
`egress_connector_arn` (the AWS provider has no resource for it yet):

```sh
aws lambda-microvms create-network-connector \
  --name runtm-sandbox-egress \
  --subnet-ids $(terraform output -json private_subnet_ids | jq -r 'join(" ")') \
  --security-group-ids $(terraform output -raw security_group_id)
```

## CloudFormation instead of Terraform

The same stack as a CloudFormation template lives in `cloudformation/v2/`
(Google-identity trust; `v1/` is the legacy hub-role variant). The Runtm
dialog offers a pre-filled one-click link. From a terminal, with the values
the dialog shows:

```sh
aws cloudformation create-stack --stack-name runtm-sandbox --capabilities CAPABILITY_NAMED_IAM \
  --template-body "$(curl -fsSL https://raw.githubusercontent.com/runtm-ai/runtm/main/terraform/aws-sandbox/cloudformation/v2/runtm-sandbox.yaml)" \
  --parameters ParameterKey=GoogleEmail,ParameterValue=<google identity> \
               ParameterKey=GoogleAudience,ParameterValue=runtm-sandbox:<org id> \
               ParameterKey=OrganizationId,ParameterValue=<org id>
```

## Inputs

| Name | Default | Description |
|---|---|---|
| `runtm_organization_id` | — | Your Runtm organization id. |
| `runtm_google_project` | Runtm production | Only for Runtm staging or a self-hosted Runtm; the dialog's snippet includes it when needed. |
| `runtm_google_subject` | `""` | Optional extra pin on the identity's numeric id. |
| `create_vpc_egress` | `false` | Build the egress VPC + security group. |
| `egress_allowed_cidrs` | `["0.0.0.0/0"]` | Destinations sandboxes may reach. |
| `egress_connector_arn` | `null` | Network connector ARN, once created. |
| `artifact_retention_days` | `90` | Lifetime of image build contexts. |
| `session_artifact_retention_days` | `90` | Lifetime of paused-session snapshots. |
| `template_versions_to_keep` | `3` | Previous template versions kept for rollback (current never expires). |
| `create_log_group` / `log_group_name` | `true` / `/runtm/sandboxes` | CloudWatch log group for sandbox logs. |
| `artifact_bucket_name` | `runtm-sandbox-<account>-<region>` | Override when that name is taken. |
| `tags` | `{}` | Extra tags. |

See `variables.tf` for the full list.

## Outputs

`runtm_connection` bundles everything the Runtm dialog asks for. Individual
outputs: `role_arn`, `artifact_bucket`, `build_role_arn`,
`execution_role_arn`, `log_group`, `vpc_id`, `private_subnet_ids`,
`security_group_id`, `google_sa_email`.

## Requirements

Terraform ≥ 1.5, AWS provider ≥ 5.0.
