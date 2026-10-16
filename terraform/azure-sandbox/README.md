# runtm/azure-sandbox

Terraform module for running Runtm Cloud sandboxes (Azure Container Apps
Sandboxes) in your own Azure subscription. It creates the broker identity Runtm
acts as, the sandbox group, a container registry, a storage account for
workspace layers, and the role assignments between them. Nothing else.

The same stack ships as a Bicep/ARM "Deploy to Azure" template in `bicep/`
(see [Deploy to Azure instead of Terraform](#deploy-to-azure-instead-of-terraform)).

## Usage

```hcl
provider "azurerm" {
  features {}
  subscription_id = "<subscription id>"
  # The stack disables storage account keys; without this the provider polls
  # blob storage with a key after creating the account and fails with 403.
  storage_use_azuread = true
}

provider "azapi" {
  subscription_id = "<subscription id>"
}

module "runtm_sandbox" {
  source = "git::https://github.com/runtm-ai/runtm.git//terraform/azure-sandbox?ref=main"

  runtm_organization_id = "<org id>"
  runtm_google_subject  = "<Runtm's identity id, shown in the dialog>"
  location              = "westus2"
}

output "runtm_connection" {
  value = module.runtm_sandbox.runtm_connection
}
```

Your organization id and the Google subject are in Runtm under
**Integrations → Azure Sandboxes**. Apply, then give that dialog the two
values it asks for — `terraform output subscription_id` and
`terraform output broker_client_id` — and press **Test connection**. Runtm
derives every other name from the convention below (and reads the identity
with its own token); the whole `terraform output -json runtm_connection`
object can still be pasted under *Advanced* if your stack renamed anything.

### The dialog's snippet is shorter than this

Runtm serves each organization its own thin wrapper of this module
(`source = "https://app.runtm.com/api/cloud/azure/templates/<id>/runtm-sandbox.zip"`),
with `runtm_organization_id` and `runtm_google_subject` baked in as variable
defaults, so the block Runtm shows names only `location` and `suffix`. The
wrapper re-exports every output and passes every variable through. Use this
public module directly when you want to pin a version or read the code.

### One Runtm identity, per-organization audience

Both clouds trust a Google service account Runtm operates. AWS lets a role
trust it by **email**, so the AWS module derives a per-organization account
from the org id. Entra workload identity federation has no email condition:
a federated credential matches the token's **issuer**
(`https://accounts.google.com`), **subject** (the account's numeric id,
assigned by Google) and **audience** exactly. A per-organization account would
make the subject a per-customer value no public template could carry, so on
Azure Runtm presents **one** account to every tenant: `runtm_google_subject`
is the same number for every organization (the dialog shows it), and the
**audience** `runtm-sandbox:<organization id>` is what keeps stacks apart — a
token Runtm mints for organization A is refused by organization B's broker
(`AADSTS700212`). Every name derives from the organization id (slug = its
first 12 characters, lower-cased), so a stack needs the org id and an optional
label and nothing else.

There is no Runtm Entra application and no secret anywhere: the broker is a
managed identity in **your** tenant, and nothing in the stack needs tenant-admin
consent.

Every resource name carries a slug derived from the organization id, so several
organizations (or several Runtm environments) can share one subscription and
region. Add an optional `suffix` for a human-readable label, and enter the same
value in the dialog:

```hcl
module "runtm_sandbox" {
  source = "git::https://github.com/runtm-ai/runtm.git//terraform/azure-sandbox?ref=main"

  runtm_organization_id = "<org id>"
  runtm_google_subject  = "<numeric id>"
  location              = "westus2"
  suffix                = "pci" # runtm-sandbox-pci-<slug>-westus2, sandbox group rtm-pci-<slug>
}
```

One module instance per region. Pin `ref=` to a tag for reproducible applies.

## What it creates

| Resource | Name | Notes |
|---|---|---|
| Resource group | `runtm-sandbox-<label>-<region>` | Everything lives here; every grant is scoped inside it. |
| User-assigned managed identity | `runtm-sandbox-broker-<label>` | The identity Runtm acts as. |
| Federated identity credential | `google-runtm-org` | issuer `https://accounts.google.com`, subject = your Google subject, audience `api://AzureADTokenExchange`. |
| Sandbox group (`Microsoft.App/sandboxGroups@2026-07-01`) | `rtm-<label>` | Broker identity attached (PUT envelope + a PATCH, see below). |
| Container registry (Basic) | `rtmacr<slug><disc>` | Sandbox images; the service pulls with the group identity. |
| Storage account + container `layers` | `rtm<slug><disc>` | Versioned, TLS 1.2, HTTPS only, no public access, no account keys; lifecycle on `sessions/` and `templates/`. |
| Management lock (CanNotDelete) | on the storage account | Twin of CloudFormation `Retain`. Remove before `terraform destroy`. |
| Role assignments for the broker | Contributor (resource group), Container Apps SandboxGroup Data Owner (sandbox group), AcrPull (registry), Storage Blob Data Contributor (storage) | Each scoped to the one resource it is needed on. |
| Optional VNet + delegated subnet + `vnetConnections` | `runtm-sandbox-egress-<label>` / `sandboxes` / `default` | `create_vnet = true`. |

`<label>` is `<suffix>-<slug>` or `<slug>`; `<slug>` is the first 12
characters of the organization id, lower-cased (so an ARM template can derive
it); `<disc>` is the first 9 hex characters of
`sha256("<org>|<suffix>|<region>")`, needed because storage and registry names
are globally unique, take no hyphens and cap at 24 characters (the Bicep
template's own default uses `uniqueString()` for `<disc>` instead; Runtm reads
the names a portal deployment actually produced from its outputs).

## Name lengths

Worst case: `suffix` 10, slug 12, region 18 (`australiasoutheast`).

| Name | Formula | Worst case | Azure limit |
|---|---|---|---|
| Resource group | `runtm-sandbox-<label>-<region>` | 56 | 90 |
| Broker identity | `runtm-sandbox-broker-<label>` | 44 | 128 |
| Sandbox group | `rtm-<label>` | 27 | 32, `[A-Za-z0-9-]`, no leading `.`/`-` |
| Storage account | `rtm<slug><disc>` | **24** | 24, lowercase alphanumeric |
| Container registry | `rtmacr<slug><disc>` | 27 | 50, alphanumeric |
| VNet | `runtm-sandbox-egress-<label>` | 44 | 64 |

The `suffix` cap is 10, the same as `aws-sandbox`, so one value entered in the
Runtm dialog works on both clouds. The sandbox group name is the tightest
Azure limit and still has 5 characters to spare (4 + 10 + 1 + 12 = 27).

## Optional VNet

Sandboxes can reach private databases on your network:

```hcl
module "runtm_sandbox" {
  source = "git::https://github.com/runtm-ai/runtm.git//terraform/azure-sandbox?ref=main"

  runtm_organization_id = "<org id>"
  runtm_google_subject  = "<numeric id>"
  location              = "westus2"

  create_vnet = true
  vnet_cidr   = "10.77.0.0/16"
  subnet_cidr = "10.77.0.0/23" # delegated to Microsoft.App/environments; immutable once connected
}
```

Unlike AWS, the link is declarative (a `vnetConnections` child on the sandbox
group); there is no manual connector step. Runtm passes
`vnetConnectionName = "default"` when it creates sandboxes.

## Deploy to Azure instead of Terraform

The same stack as a Bicep/ARM template lives in `bicep/`: `main.bicep`
(subscription scope; creates the resource group and calls
`modules/sandbox.bicep`), the compiled `azuredeploy.json` (what the button and
the CLI deploy; regenerate with `az bicep build -f bicep/main.bicep --outfile
bicep/azuredeploy.json`), and `createUiDefinition.json` (the portal wizard).
All three paths produce the same resources with the same names, so a stack
created from the template can be adopted by Terraform later with `import`
blocks.

**Deploy to Azure button.** The Runtm dialog offers a pre-filled one-click
link into the Azure portal. The portal cannot take parameter values from the
link, so Runtm serves a per-organization copy of `azuredeploy.json` with your
`organizationId`, `tenantSlug`, `googleSubject`, `audience`, `suffix`,
`storageAccountName` and `registryName` baked in as defaults; everything else
is byte-identical to the committed file. Pick the subscription and region,
review, create. When the deployment finishes, open **Outputs**, copy
`subscriptionId` and `brokerClientId` into the dialog's two fields, and press
**Test connection** (the whole `runtmConnection` object is accepted under
*Advanced*).

**Azure CLI.** From a terminal, with the values the dialog shows:

```sh
az deployment sub create --location westus2 --name runtm-sandbox \
  --template-uri https://raw.githubusercontent.com/runtm-ai/runtm/main/terraform/azure-sandbox/bicep/azuredeploy.json \
  --parameters organizationId=<org id> tenantSlug=<slug> googleSubject=<numeric id> \
               storageAccountName=<shown in the dialog> registryName=<shown in the dialog>
az deployment sub show -n runtm-sandbox --query properties.outputs.runtmConnection.value
```

(`--template-file bicep/main.bicep` works too from a checkout.) Enter the
output's `subscription_id` and `broker_client_id` in the dialog, or paste the
whole object under *Advanced*.

**Terraform.** The module above.

## Deployer permissions and prerequisites

- **Owner** on the subscription, or **Contributor + User Access Administrator**:
  the stack creates role assignments and a resource lock
  (`Microsoft.Authorization/*/write`, which Contributor alone lacks). This is
  the Azure twin of CloudFormation's `CAPABILITY_NAMED_IAM`. The template is a
  subscription-scope deployment (it creates the resource group), which Owner
  and Contributor both include.
- Resource providers `Microsoft.App`, `Microsoft.ManagedIdentity`,
  `Microsoft.ContainerRegistry`, `Microsoft.Storage` (and `Microsoft.Network`
  for the VNet) registered. ARM registers them during a deployment when the
  deployer may, and azurerm v4 does the same unless
  `resource_provider_registrations` is set to `none`. Preflight anyway:
  `for ns in Microsoft.App Microsoft.ManagedIdentity Microsoft.ContainerRegistry Microsoft.Storage Microsoft.Network; do az provider register -n $ns --wait; done`.
- A region that offers Sandboxes:
  `az provider show -n Microsoft.App --query "resourceTypes[?resourceType=='sandboxGroups'].locations[]"`.
  `westus2` is closest to Runtm's hub.
- A **SandboxCores** quota that fits your concurrency. It is per region per
  subscription; a fresh or sponsored subscription can carry a very low default
  (1 core per region was observed, i.e. one sandbox at a time). Open a support
  request (*Service and subscription limits (quotas)* → Container Apps →
  Sandboxes) before go-live; it can take days.
- Role assignments and federated credentials take 1-5 minutes to propagate;
  Runtm's Test connection retries on `AADSTS70021` and data-plane 403s.

## If the identity did not attach

`Microsoft.App/sandboxGroups@2026-07-01` publishes no `identity` in its schema,
but the live service persists one. The Terraform module sends it in the PUT
envelope and then repeats it with a PATCH (`attach_identity_by_patch = true`,
the sequence verified end to end). Bicep can only PUT, so the template relies
on the envelope alone. If Test connection reports the identity missing on the
group, attach it by hand:

```sh
az rest --method patch \
  --url "https://management.azure.com/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.App/sandboxGroups/<group>?api-version=2026-07-01" \
  --body '{"identity":{"type":"UserAssigned","userAssignedIdentities":{"<broker_identity_id>":{}}}}'
```

Verified 2026-10-08 against `api-version=2026-07-01`: a PUT that carries the
`identity` envelope persists it (a GET straight after a PUT-only create reads
`UserAssigned`), so the template path needs no PATCH. The module keeps its
PATCH as belt-and-braces, re-runs it whenever the group is replaced
(`replace_triggered_by`), and exports `sandbox_group_identity_type` so an
apply can prove it. If a future API version changes this, Test connection
reports the identity missing and the command above repairs it.

## Inputs

| Name | Default | Description |
|---|---|---|
| `runtm_organization_id` | — | Your Runtm organization id. |
| `runtm_google_subject` | — | **Required.** Numeric unique id of your organization's Runtm Google identity (shown in the dialog). |
| `location` | — | Azure region offering Container Apps Sandboxes. |
| `runtm_audience` | `api://AzureADTokenExchange` | Token audience the federated credential accepts. |
| `runtm_google_issuer` | `https://accounts.google.com` | Leave unset. |
| `suffix` | `""` | Optional label in front of the org slug (`pci`, `ops`); 1-10 lowercase chars. |
| `session_artifact_retention_days` | `90` | Lifetime of paused-session workspace layers. |
| `template_version_retention_days` | `30` | Lifetime of superseded template golden versions (current never expires). |
| `storage_shared_key_access` | `false` | Allow account-key auth on the storage account. With the default (`false`) your `azurerm` provider block needs `storage_use_azuread = true`, or the provider's post-create blob poll fails with 403 `KeyBasedAuthenticationNotPermitted`. |
| `attach_identity_by_patch` | `true` | Also PATCH the identity onto the sandbox group after creation. |
| `create_vnet` / `vnet_cidr` / `subnet_cidr` | `false` / `10.77.0.0/16` / `10.77.0.0/23` | Optional VNet. |
| `storage_account_name` / `registry_name` | derived | Override only when the derived name is taken. |
| `tags` | `{}` | Extra tags. |

See `variables.tf` for the full list. The Bicep template takes the same
values in camelCase (`organizationId`, `googleSubject`, `suffix`, ...);
`tenantSlug` and `audience` default to the same derivations from the org id,
and `storageAccountName` / `registryName` default to `uniqueString()`-based
names (Runtm pre-fills the sha256 ones when you arrive from the dialog).

## Outputs

`runtm_connection` bundles everything the Runtm dialog asks for (the Bicep
template's `runtmConnection` output has the same keys). Individual outputs:
`broker_client_id`, `broker_principal_id`, `broker_identity_id`, `tenant_id`,
`subscription_id`, `resource_group`, `location`, `sandbox_group`,
`sandbox_group_id`, `management_endpoint`, `acr_login_server`,
`storage_account`, `container`, `vnet_id`, `subnet_id`, `vnet_connection`,
`resource_label`.

## Deleting the stack

The storage account carries a CanNotDelete lock (the twin of CloudFormation's
`Retain`): `az group delete` and `terraform destroy` fail until it is removed
(`az lock delete -n runtm-sandbox-layers-retain --resource <storage account id>`).
Deleting the sandbox group deletes every sandbox, snapshot and disk image in it.

## Requirements

Terraform ≥ 1.5, azurerm ~> 4.0, azapi ~> 2.0 (validated with azurerm 4.81.0
and azapi 2.13.0). Bicep CLI ≥ 0.30 to rebuild `azuredeploy.json`.
