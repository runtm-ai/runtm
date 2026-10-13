# Runtm Cloud - Azure Container Apps Sandboxes integration (customer side).
#
# Terraform twin of the Bicep "Deploy to Azure" template Runtm hands out, and
# the Azure twin of runtm/terraform/aws-sandbox. It creates, in YOUR
# subscription: a resource group, the broker managed identity Runtm acts as
# (trusting your organization's Runtm Google identity, no secret), the sandbox
# group sandboxes run in, a container registry for sandbox images, a storage
# account for workspace layers, the role assignments between them, and
# optionally a VNet the sandbox group is connected to. Nothing here grants
# Runtm access outside that resource group.
#
# Everything below is exactly what Runtm's end-to-end spike proved against
# the live service on 2026-10-07 (api-version 2026-07-01).

data "azurerm_client_config" "current" {}

locals {
  subscription_id = data.azurerm_client_config.current.subscription_id
  tenant_id       = data.azurerm_client_config.current.tenant_id

  # The Runtm organization is the tenant. Every fixed-name resource below
  # carries a slug DERIVED from the org id — the first 12 hex chars of the same
  # sha256 the org's Google service account is named from — so two orgs (two
  # teams, PCI and non-PCI, Runtm staging and a developer) can share one
  # subscription and region without a single name collision. `suffix` is an
  # optional human-readable label (pci, ops, revenue) in front of the slug.
  # cloud-api computes the same names; the Bicep twin takes the slug as
  # tenantSlug and the two hyphen-less names as parameters, because ARM has no
  # sha256 function.
  slug  = substr(sha256(var.runtm_organization_id), 0, 12)
  label = var.suffix != "" ? "${var.suffix}-${local.slug}" : local.slug

  # Storage accounts (3-24 lowercase alphanumerics) and registries (5-50
  # alphanumerics) are globally unique and take no hyphens, so the readable
  # label and the region do not fit. They carry the slug plus a 9-hex
  # discriminator of (org, suffix, region) instead; the label lives in tags.
  disc = substr(sha256("${var.runtm_organization_id}|${var.suffix}|${var.location}"), 0, 9)

  # Name formulas and worst-case lengths (suffix 10, slug 12, region 18
  # "australiasoutheast"); limits in the README table. The suffix cap is the
  # same 10 as aws-sandbox so one dialog value works on both clouds.
  resource_group_name  = "runtm-sandbox-${local.label}-${var.location}"                                              # 14+23+1+18 = 56 <= 90
  broker_identity_name = "runtm-sandbox-broker-${local.label}"                                                       # 21+23     = 44 <= 128
  sandbox_group_name   = "rtm-${local.label}"                                                                        # 4+23      = 27 <= 32
  storage_account_name = var.storage_account_name != "" ? var.storage_account_name : "rtm${local.slug}${local.disc}" # 3+12+9 = 24 <= 24 (binding)
  registry_name        = var.registry_name != "" ? var.registry_name : "rtmacr${local.slug}${local.disc}"            # 6+12+9 = 27 <= 50
  vnet_name            = "runtm-sandbox-egress-${local.label}"                                                       # 21+23 = 44 <= 64
  subnet_name          = "sandboxes"
  vnet_connection_name = "default"
  container_name       = "layers"
  federated_credential = "google-runtm-org" # immutable once created

  sandbox_group_api = "2026-07-01"

  # Built-in role definition ids (verified in the spike tenant 2026-10-07).
  role_definitions        = "/subscriptions/${local.subscription_id}/providers/Microsoft.Authorization/roleDefinitions"
  role_contributor        = "${local.role_definitions}/b24988ac-6180-42a0-ab88-20f7382dd24c"
  role_sandbox_data_owner = "${local.role_definitions}/c24cf47c-5077-412d-a19c-45202126392c" # Container Apps SandboxGroup Data Owner
  role_acr_pull           = "${local.role_definitions}/7f951dda-4ed3-4680-a7ca-43fe172d538d"
  role_blob_contributor   = "${local.role_definitions}/ba92f5b4-2d11-453d-a403-e96b0029c9fe" # Storage Blob Data Contributor

  tags = merge(var.tags, {
    "runtm:organization" = var.runtm_organization_id
    "runtm:label"        = local.label
  })
}

# ---------------------------------------------------------------------------
# Resource group: the blast radius. Every grant below is scoped inside it.
# ---------------------------------------------------------------------------

resource "azurerm_resource_group" "this" {
  name     = local.resource_group_name
  location = var.location
  tags     = local.tags
}

# ---------------------------------------------------------------------------
# The identity Runtm's control plane acts as (twin of RuntmSandboxAccessRole)
# ---------------------------------------------------------------------------

resource "azurerm_user_assigned_identity" "broker" {
  name                = local.broker_identity_name
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  tags                = merge(local.tags, { "runtm:component" = "broker-identity" })
}

# Trust: Google-issued ID tokens for exactly one Google service account (the
# numeric unique id pinned as `subject`) with exactly one audience. Entra
# matches issuer + subject + audience; there is no email condition and no
# wildcard, which is why runtm_google_subject is required. New credentials take
# a few minutes to propagate (AADSTS70021 until then).
resource "azurerm_federated_identity_credential" "google" {
  name                = local.federated_credential
  resource_group_name = azurerm_resource_group.this.name
  parent_id           = azurerm_user_assigned_identity.broker.id
  issuer              = var.runtm_google_issuer
  subject             = var.runtm_google_subject
  audience            = [var.runtm_audience]
}

# ---------------------------------------------------------------------------
# Sandbox group: the ARM resource sandboxes live in. azurerm has no resource
# for Microsoft.App/sandboxGroups, so azapi. The broker identity is attached
# to the group so the service can pull images from the registry with it when
# it converts them to disk images (and so egress Transform rules can inject
# its tokens without a credential ever entering a sandbox).
#
# api-version 2026-07-01's published schema has no `identity`, but the live
# service accepts and persists one: the spike created the group with
# `{location}` only and attached the identity with a PATCH. azapi's `identity`
# block is sent as the ARM identity envelope of the PUT (not inside `body`);
# Microsoft's own sandbox_groups module does exactly that (with schema
# validation off). Both are done here: identity in the PUT, then the proven
# PATCH (attach_identity_by_patch). Once PUT-with-identity on 2026-07-01 is
# verified (output.sandbox_group_identity_type reads "UserAssigned" after an
# apply), the PATCH can be dropped.
# ---------------------------------------------------------------------------

resource "azapi_resource" "sandbox_group" {
  type      = "Microsoft.App/sandboxGroups@${local.sandbox_group_api}"
  name      = local.sandbox_group_name
  parent_id = azurerm_resource_group.this.id
  location  = azurerm_resource_group.this.location
  tags      = merge(local.tags, { "runtm:component" = "sandbox-group" })

  # `identity` is not in the embedded 2026-07-01 schema; do not let azapi
  # reject it client-side.
  schema_validation_enabled = false

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.broker.id]
  }

  body = {
    properties = {}
  }

  response_export_values = [
    "identity.type",
    "properties.provisioningState",
    "properties.managementEndpoint",
  ]
}

# The spike's exact call: PATCH {"identity":{"type":"UserAssigned",
# "userAssignedIdentities":{<id>:{}}}} on the group (azapi_update_resource is
# NOT this: it GETs, merges and PUTs; azapi_resource_action with method PATCH is).
resource "azapi_resource_action" "sandbox_group_identity" {
  count = var.attach_identity_by_patch ? 1 : 0

  type        = "Microsoft.App/sandboxGroups@${local.sandbox_group_api}"
  resource_id = azapi_resource.sandbox_group.id
  method      = "PATCH"
  when        = "apply"

  body = {
    identity = {
      type = "UserAssigned"
      userAssignedIdentities = {
        (azurerm_user_assigned_identity.broker.id) = {}
      }
    }
  }

  lifecycle {
    # An action runs on create and when its own arguments change, not on
    # every apply. Re-run the PATCH whenever the group itself is updated or
    # replaced (a tag change re-PUTs it): if the service drops the
    # unschema'd identity on a PUT it is repaired in the same apply instead
    # of silently breaking image pulls. The plan shows this as delete +
    # create of the action; nothing is deleted in Azure.
    replace_triggered_by = [azapi_resource.sandbox_group]
  }
}

# ---------------------------------------------------------------------------
# Container registry: sandbox images Runtm builds on its side are pushed here
# and converted to disk images by the service, pulling with the group's
# identity (AcrPull). Basic SKU (ACR Tasks are not used; builds are external).
# ---------------------------------------------------------------------------

resource "azurerm_container_registry" "images" {
  name                = local.registry_name
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  sku                 = "Basic"
  admin_enabled       = false
  tags                = merge(local.tags, { "runtm:component" = "sandbox-images" })
}

# ---------------------------------------------------------------------------
# Storage account: workspace layers of paused sessions (sessions/) and
# template goldens (templates/), as block blobs written through
# user-delegation SAS the broker identity signs. Versioned, TLS 1.2, HTTPS
# only, no anonymous blob access, no account keys.
# ---------------------------------------------------------------------------

resource "azurerm_storage_account" "layers" {
  name                = local.storage_account_name
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location

  account_kind             = "StorageV2"
  account_tier             = "Standard"
  account_replication_type = "LRS"

  min_tls_version                 = "TLS1_2"
  https_traffic_only_enabled      = true
  allow_nested_items_to_be_public = false
  shared_access_key_enabled       = var.storage_shared_key_access
  default_to_oauth_authentication = true

  # Versioned so an overwritten template golden (templates/<alias>.tar.zst is
  # rewritten on every rebuild) keeps its previous versions for a while: a bad
  # rebuild can be rolled back and a template never silently loses the layer
  # every session imports.
  blob_properties {
    versioning_enabled = true
  }

  tags = merge(local.tags, { "runtm:component" = "sandbox-artifacts" })
}

resource "azurerm_storage_container" "layers" {
  name                  = local.container_name
  storage_account_id    = azurerm_storage_account.layers.id
  container_access_type = "private"
}

# Blob lifecycle. Prefix filters are <container>/<prefix>. Azure can delete
# base blobs N days after modification and previous versions N days after
# creation, and nothing else: there is no "keep the newest N versions"
# (AWS NewerNoncurrentVersions) and no abort-incomplete-multipart rule
# (uncommitted blocks are garbage-collected after 7 days by the service).
resource "azurerm_storage_management_policy" "layers" {
  storage_account_id = azurerm_storage_account.layers.id

  # sessions/ — paused-session workspace layers. Matches Runtm's paused
  # session retention (a session past this window is cleaned up anyway).
  # Superseded versions go a day after they were written.
  rule {
    name    = "expire-session-layers"
    enabled = true
    filters {
      prefix_match = ["${local.container_name}/sessions/"]
      blob_types   = ["blockBlob"]
    }
    actions {
      base_blob {
        delete_after_days_since_modification_greater_than = var.session_artifact_retention_days
      }
      version {
        delete_after_days_since_creation = 1
      }
    }
  }

  # templates/ — template goldens. NEVER expire the current version: the
  # template is used for as long as it exists and is only rewritten on
  # rebuild. Previous versions are kept for rollback, then purged.
  rule {
    name    = "retain-template-goldens"
    enabled = true
    filters {
      prefix_match = ["${local.container_name}/templates/"]
      blob_types   = ["blockBlob"]
    }
    actions {
      version {
        delete_after_days_since_creation = var.template_version_retention_days
      }
    }
  }

  depends_on = [azurerm_storage_container.layers]
}

# Twin of CloudFormation's DeletionPolicy: Retain. Azure has no per-resource
# retain; a CanNotDelete lock stops `az group delete` / `terraform destroy`
# from taking the layers with it. Remove the lock first when you really mean it.
resource "azurerm_management_lock" "layers" {
  name       = "runtm-sandbox-layers-retain"
  scope      = azurerm_storage_account.layers.id
  lock_level = "CanNotDelete"
  notes      = "Runtm sandbox workspace layers and template goldens. Remove this lock before deleting the storage account."
}

# ---------------------------------------------------------------------------
# What the broker identity may do, exactly as the spike granted it:
#   Contributor on the resource group (manage the group, registry, storage);
#   Container Apps SandboxGroup Data Owner (every sandbox data-plane call;
#     subscription Owner is NOT enough, it has no data actions);
#   AcrPull on the registry (the service pulls images with the group identity);
#   Storage Blob Data Contributor on the storage account (layers, and the
#     user-delegation key that signs SAS URLs).
# principal_type = ServicePrincipal lets ARM accept the assignment before the
# new identity has replicated. Data-plane propagation can still take 1-2 min.
# ---------------------------------------------------------------------------

resource "azurerm_role_assignment" "broker_contributor" {
  scope              = azurerm_resource_group.this.id
  role_definition_id = local.role_contributor
  principal_id       = azurerm_user_assigned_identity.broker.principal_id
  principal_type     = "ServicePrincipal"
}

resource "azurerm_role_assignment" "broker_sandbox_data_owner" {
  scope              = azapi_resource.sandbox_group.id
  role_definition_id = local.role_sandbox_data_owner
  principal_id       = azurerm_user_assigned_identity.broker.principal_id
  principal_type     = "ServicePrincipal"
}

resource "azurerm_role_assignment" "broker_acr_pull" {
  scope              = azurerm_container_registry.images.id
  role_definition_id = local.role_acr_pull
  principal_id       = azurerm_user_assigned_identity.broker.principal_id
  principal_type     = "ServicePrincipal"
}

resource "azurerm_role_assignment" "broker_blob_contributor" {
  scope              = azurerm_storage_account.layers.id
  role_definition_id = local.role_blob_contributor
  principal_id       = azurerm_user_assigned_identity.broker.principal_id
  principal_type     = "ServicePrincipal"
}

# ---------------------------------------------------------------------------
# Optional VNet: a subnet delegated to Microsoft.App/environments, connected
# to the sandbox group declaratively (no manual connector step as on AWS).
# Sandboxes created with vnetConnectionName = "default" get a NIC on it and
# can reach your private databases. The subnet id is immutable once connected.
# ---------------------------------------------------------------------------

resource "azurerm_virtual_network" "sandboxes" {
  count = var.create_vnet ? 1 : 0

  name                = local.vnet_name
  resource_group_name = azurerm_resource_group.this.name
  location            = azurerm_resource_group.this.location
  address_space       = [var.vnet_cidr]
  tags                = merge(local.tags, { "runtm:component" = "sandbox-vnet" })
}

resource "azurerm_subnet" "sandboxes" {
  count = var.create_vnet ? 1 : 0

  name                 = local.subnet_name
  resource_group_name  = azurerm_resource_group.this.name
  virtual_network_name = azurerm_virtual_network.sandboxes[0].name
  address_prefixes     = [var.subnet_cidr]

  delegation {
    name = "sandboxes"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }

  # The service rewrites the delegation's action list after it takes the
  # subnet; do not fight it (Microsoft's quickstart does the same).
  lifecycle {
    ignore_changes = [delegation]
  }
}

resource "azapi_resource" "vnet_connection" {
  count = var.create_vnet ? 1 : 0

  type      = "Microsoft.App/sandboxGroups/vnetConnections@${local.sandbox_group_api}"
  name      = local.vnet_connection_name
  parent_id = azapi_resource.sandbox_group.id

  # Not in azapi's embedded schema yet (the type is 2026-07-01); the live
  # service has it (spec VnetConnection.tsp, Microsoft's quickstart).
  schema_validation_enabled = false

  body = {
    properties = {
      subnetId = azurerm_subnet.sandboxes[0].id
    }
  }

  depends_on = [azapi_resource_action.sandbox_group_identity]
}
