output "broker_client_id" {
  description = "Paste into Runtm (Integrations -> Azure Sandboxes): client id of the broker identity Runtm exchanges its Google token for."
  value       = azurerm_user_assigned_identity.broker.client_id
}

output "broker_principal_id" {
  description = "Object (principal) id of the broker identity; what the role assignments reference."
  value       = azurerm_user_assigned_identity.broker.principal_id
}

output "broker_identity_id" {
  description = "ARM resource id of the broker identity (what the sandbox group and disk-image pulls reference)."
  value       = azurerm_user_assigned_identity.broker.id
}

output "tenant_id" {
  description = "Entra tenant the resources were created in (the token exchange endpoint is https://login.microsoftonline.com/<tenant_id>)."
  value       = local.tenant_id
}

output "subscription_id" {
  description = "Subscription the resources were created in."
  value       = local.subscription_id
}

output "resource_group" {
  description = "Resource group holding everything (the blast radius of the broker identity)."
  value       = azurerm_resource_group.this.name
}

output "location" {
  description = "Azure region (the data plane is https://management.<location>.azuredevcompute.io)."
  value       = azurerm_resource_group.this.location
}

output "sandbox_group" {
  description = "Name of the Microsoft.App/sandboxGroups resource sandboxes run in."
  value       = azapi_resource.sandbox_group.name
}

output "sandbox_group_id" {
  description = "ARM resource id of the sandbox group."
  value       = azapi_resource.sandbox_group.id
}

output "sandbox_group_identity_type" {
  description = "Must read \"UserAssigned\" after an apply: proves the service persisted the broker identity on the sandbox group (api-version 2026-07-01 does not publish it in its schema)."
  value       = try(azapi_resource.sandbox_group.output.identity.type, null)
}

output "management_endpoint" {
  description = "Data-plane endpoint reported by the service for the group, when the API returns it (null otherwise; Runtm derives it from location anyway)."
  value       = try(azapi_resource.sandbox_group.output.properties.managementEndpoint, null)
}

output "acr_login_server" {
  description = "Registry Runtm pushes sandbox images to (<name>.azurecr.io)."
  value       = azurerm_container_registry.images.login_server
}

output "storage_account" {
  description = "Storage account for workspace layers and template goldens."
  value       = azurerm_storage_account.layers.name
}

output "container" {
  description = "Blob container inside it (prefixes sessions/ and templates/)."
  value       = azurerm_storage_container.layers.name
}

output "vnet_id" {
  description = "VNet id (null unless create_vnet)."
  value       = var.create_vnet ? azurerm_virtual_network.sandboxes[0].id : null
}

output "subnet_id" {
  description = "Delegated subnet id the sandbox group is connected to (null unless create_vnet)."
  value       = var.create_vnet ? azurerm_subnet.sandboxes[0].id : null
}

output "vnet_connection" {
  description = "Name of the sandbox group's vnetConnections child, to pass as vnetConnectionName when creating sandboxes (null unless create_vnet)."
  value       = var.create_vnet ? azapi_resource.vnet_connection[0].name : null
}

output "resource_label" {
  description = "<suffix>-<slug> (or <slug>): the per-organization part every resource name carries."
  value       = local.label
}

output "runtm_connection" {
  description = "Everything the Runtm connect dialog asks for, in one object (paste the JSON)."
  value = {
    identity            = "google_oidc"
    tenant_id           = local.tenant_id
    subscription_id     = local.subscription_id
    resource_group      = azurerm_resource_group.this.name
    location            = azurerm_resource_group.this.location
    broker_client_id    = azurerm_user_assigned_identity.broker.client_id
    broker_principal_id = azurerm_user_assigned_identity.broker.principal_id
    broker_identity_id  = azurerm_user_assigned_identity.broker.id
    sandbox_group       = azapi_resource.sandbox_group.name
    acr_login_server    = azurerm_container_registry.images.login_server
    storage_account     = azurerm_storage_account.layers.name
    container           = azurerm_storage_container.layers.name
    vnet_connection     = var.create_vnet ? azapi_resource.vnet_connection[0].name : null
    google_subject      = var.runtm_google_subject
    google_issuer       = var.runtm_google_issuer
    audience            = local.audience
  }
}
