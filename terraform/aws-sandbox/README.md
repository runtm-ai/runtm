# runtm/aws-sandbox

Terraform module for the customer side of Runtm Cloud's **AWS Lambda MicroVMs**
sandbox provider. Apply it in **your** AWS account and Runtm can run sandboxes
there as Firecracker microVMs. It is the Terraform twin of the CloudFormation
quick-create stack the Runtm connect dialog offers.

It creates, and only creates:

| Resource | Purpose |
|---|---|
| `RuntmSandboxAccessRole-<region>` | The role Runtm's control plane assumes. Trust is pinned to **your organization's** identity (below). Permissions: Lambda MicroVMs, `iam:PassRole` on the two roles under it, the artifact bucket, build-log reads. |
| `RuntmMicrovmBuildRole-<region>` | Assumed by the Lambda service while it builds a MicroVM image from the zip Runtm uploads. |
| `RuntmMicrovmExecutionRole-<region>` | The identity a running sandbox has. CloudWatch logging only; add statements if your agents should reach other AWS services. |
| `runtm-sandbox-<account>-<region>` (S3) | Image build contexts (`images/`, 90 d), paused-session snapshots (`sessions/`, 90 d) and **template goldens (`templates/`, never expire; versioned, last 3 previous versions kept 30 d)**. Encrypted, private, TLS-only. |
| `/runtm/sandboxes` (CloudWatch, optional) | Sandbox app logs. |
| Egress VPC (optional) | Private subnets behind a NAT gateway, security group with your egress allow-list. See [Egress](#egress). |

Nothing grants Runtm access outside Lambda MicroVMs and that bucket.

## Usage

Copy this from **Integrations → AWS Lambda MicroVMs → Terraform** in Runtm.
Your organization id is the only input: the module derives the Google identity
Runtm presents and the token audience from it.

```hcl
module "runtm_sandbox" {
  source = "git::https://github.com/runtm-ai/runtm.git//terraform/aws-sandbox?ref=main"

  runtm_organization_id = "<org id>"
}

output "runtm_connection" {
  value = module.runtm_sandbox.runtm_connection
}
```

Then paste the outputs into the Runtm connect dialog (or read them with
`terraform output runtm_connection`):

| Output | Dialog field |
|---|---|
| `role_arn` | Role ARN |
| `artifact_bucket` | Artifact bucket |
| `build_role_arn` | Build role ARN (Advanced) |
| `execution_role_arn` | Execution role ARN (Advanced) |
| `log_group` | CloudWatch log group (Advanced) |
| `egress_connector_arn` | VPC egress connector ARN (Advanced) |

The region is the one your `aws` provider is configured for; it must be a
Lambda MicroVMs region (`us-east-1`, `us-east-2`, `us-west-2`, `eu-west-1`,
`ap-northeast-1` at launch). One module instance per region.

Pin `ref=` to a tag for reproducible applies.

## How Runtm authenticates (why there is no secret to paste)

Runtm mints **one Google service account per organization** and presents its
ID token to `sts:AssumeRoleWithWebIdentity`. AWS trusts Google as a built-in
web-identity provider, so the module creates **no IAM OIDC provider** and you
paste **no key or secret**. The account's email is *derived* from your org id:

```
runtm-aws-<first 20 hex of sha256(org id)>@<runtm_google_project>.iam.gserviceaccount.com
```

(GCP account ids are capped at 30 lowercase characters, so the org id is
hashed rather than used verbatim. The same formula lives in Runtm Cloud —
`terraform output google_sa_email` must equal what the Runtm dialog shows.)

The trust policy pins that email and the audience:

```hcl
data "aws_iam_policy_document" "access_trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity", "sts:TagSession"]
    principals {
      type        = "Federated"
      identifiers = ["accounts.google.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "accounts.google.com:email"
      values   = [local.google_sa_email]   # runtm-aws-<hash>@<project>.iam.gserviceaccount.com
    }
    condition {
      test     = "StringEquals"
      variable = "accounts.google.com:aud"
      values   = [local.audience]          # runtm-sandbox:<org id>
    }
    condition {
      test     = "StringEquals"
      variable = "accounts.google.com:oaud"
      values   = [local.audience]
    }
    # + accounts.google.com:sub when runtm_google_subject is set
  }
}
```

Only a service account inside Runtm's identity project can carry that email,
and Runtm creates exactly one per organization; no other Runtm tenant, and
nothing in Runtm's own AWS account, can satisfy the policy. `aud` and `oaud`
are both bound because AWS maps a Google token's `aud` field to both keys when
`azp` is absent (service-account tokens). Sessions last up to 12 h
(`max_session_duration = 43200`) since web-identity assumption is not role
chaining.

Optional hardening: set `runtm_google_subject` to the numeric id the Runtm
dialog shows and the trust additionally pins `accounts.google.com:sub`
(immutable per account; needs a re-apply if Runtm ever recreates the account).
`runtm_google_project` only changes for Runtm staging or a Runtm deployment on
another GCP account — the dialog's snippet includes it when needed.

### Legacy mode (hub role + external id)

Older Runtm connections trust a single Runtm hub role scoped by a
per-organization external id. If your Runtm dialog shows those values instead:

```hcl
module "runtm_sandbox" {
  source = "git::https://github.com/runtm-ai/runtm.git//terraform/aws-sandbox?ref=main"

  runtm_organization_id = "<org id>"
  runtm_hub_role_arn    = "arn:aws:iam::<runtm account>:role/runtm-sandbox-hub"
  runtm_external_id     = "runtm-<uuid>"
}
```

Exactly one of the two identity pairs must be set; the plan fails otherwise.
Legacy sessions are capped at 1 h by STS (role chaining).

## Egress

By default sandboxes use the AWS-managed internet egress. To route them
through a VPC you control, with a firewall you own:

```hcl
module "runtm_sandbox" {
  source = "git::https://github.com/runtm-ai/runtm.git//terraform/aws-sandbox?ref=main"

  runtm_organization_id = "<org id>"

  create_vpc_egress    = true
  vpc_cidr             = "10.77.0.0/16"
  private_subnet_cidrs = ["10.77.0.0/20", "10.77.16.0/20"]
  egress_allowed_cidrs = ["0.0.0.0/0"] # tighten to your allow-list

  # Step 2 (below): the connector ARN, once created.
  egress_connector_arn = null
}
```

This builds the VPC, one public subnet with a NAT gateway, two private
subnets, and a security group with **no ingress** and egress limited to
`egress_allowed_cidrs`.

**Step 2 is manual for now.** Lambda MicroVMs attaches to a VPC through a
*network connector*, and the AWS Terraform provider has no resource for it
yet, so the module does not create one. Create it against the module's
outputs and feed the ARN back:

```sh
aws lambda-microvms create-network-connector \
  --name runtm-sandbox-egress \
  --subnet-ids $(terraform output -json private_subnet_ids | jq -r 'join(" ")') \
  --security-group-ids $(terraform output -raw security_group_id)
# → copy "connectorArn" into egress_connector_arn and re-apply, or paste it
#   straight into the Runtm dialog's "VPC egress connector ARN" field.
```

`egress_connector_arn` is a passthrough: the module never reads it, it only
surfaces it in `runtm_connection` so the dialog values live in one place. When
the provider gains a connector resource this step folds into the module.

Ingress: there is none. Nothing reaches a sandbox over the network; previews
and terminals go through Runtm's authenticated proxy.

## Inputs

| Name | Default | Description |
|---|---|---|
| `runtm_organization_id` | — | Your Runtm organization id. Derives the trusted Google identity, the audience, and tags. |
| `runtm_google_project` | Runtm prod identity project | GCP project after the `@` in the trusted email. Override for Runtm staging / another Runtm deployment. |
| `runtm_google_subject` | `""` | Optional: also pin the account's numeric id as `sub`. |
| `runtm_audience` | `""` | Optional override; default `runtm-sandbox:<org>`. |
| `runtm_hub_role_arn` | `""` | Legacy: Runtm hub role ARN. |
| `runtm_external_id` | `""` | Legacy: per-org external id. |
| `name_suffix` | region | Suffix on the three role names. |
| `artifact_retention_days` | `90` | `images/` (build contexts) lifetime. |
| `template_versions_to_keep` | `3` | Previous versions of each `templates/` golden kept for rollback (current never expires). |
| `template_version_retention_days` | `30` | How long those previous versions live. |
| `session_artifact_retention_days` | `90` | `sessions/` lifetime; also the log group retention. |
| `create_log_group` | `true` | Create the CloudWatch log group. |
| `log_group_name` | `/runtm/sandboxes` | Must start with `/runtm/`. |
| `create_vpc_egress` | `false` | Build the egress VPC + security group. |
| `vpc_cidr` | `10.77.0.0/16` | |
| `private_subnet_cidrs` | two `/20`s | Exactly two. |
| `public_subnet_cidr` | `10.77.255.0/24` | NAT gateway subnet. |
| `egress_allowed_cidrs` | `["0.0.0.0/0"]` | Security-group egress allow-list. |
| `egress_connector_arn` | `null` | Passthrough to `runtm_connection`. |
| `tags` | `{}` | Extra tags. |

## Outputs

`role_arn`, `artifact_bucket`, `build_role_arn`, `execution_role_arn`,
`account_id`, `region`, `log_group`, `vpc_id`, `private_subnet_ids`,
`security_group_id`, `egress_connector_arn`, and `runtm_connection` (all of
the above plus the identity mode, as one object).

## Requirements

Terraform >= 1.5, `hashicorp/aws` >= 5.0. The caller needs IAM, S3,
CloudWatch Logs and (for egress) VPC permissions in the target account.
