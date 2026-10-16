// Runtm Cloud - Azure Container Apps Sandboxes integration: the resource-group
// scoped half of the template. main.bicep (subscription scope) creates the
// resource group and calls this module. Twin of terraform/azure-sandbox/main.tf;
// keep the two in lockstep (parity table in 10-azure-customer-stack.md).
targetScope = 'resourceGroup'

@description('<suffix>-<slug> or <slug>: the per-organization part of every resource name (computed in main.bicep).')
param label string

@description('Numeric unique id of the organization\'s Runtm Google service account; the federated credential\'s subject.')
param googleSubject string

@description('Issuer of the tokens Runtm presents.')
param googleIssuer string

@description('Audience of the Google token Runtm mints for this subscription.')
param audience string

@description('Storage account name (3-24 lowercase alphanumerics).')
param storageAccountName string

@description('Container registry name (5-50 alphanumerics).')
param registryName string

@description('Days to keep paused-session workspace layers (sessions/).')
param sessionArtifactRetentionDays int

@description('Days a superseded template golden version (templates/) is kept.')
param templateVersionRetentionDays int

@description('Allow Shared Key authorization on the storage account.')
param storageSharedKeyAccess bool

@description('Create a VNet with a delegated subnet and connect the sandbox group to it.')
param createVnet bool

@description('VNet address space.')
param vnetCidr string

@description('Delegated subnet (inside vnetCidr) sandboxes attach to.')
param subnetCidr string

@description('Tags for every resource.')
param tags object

// ---------------------------------------------------------------------------
// Names (formulas: terraform/azure-sandbox/main.tf locals)
// ---------------------------------------------------------------------------
var location = resourceGroup().location
var brokerIdentityName = 'runtm-sandbox-broker-${label}' // <= 49 of 128
var sandboxGroupName = 'rtm-${label}' // <= 32 of 32 (binding)
var vnetName = 'runtm-sandbox-egress-${label}' // <= 49 of 64
var subnetName = 'sandboxes'
var vnetConnectionName = 'default'
var containerName = 'layers'
var federatedCredentialName = 'google-runtm-org' // immutable once created

// Built-in role definition ids (verified 2026-10-07).
var roleContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b24988ac-6180-42a0-ab88-20f7382dd24c')
var roleSandboxDataOwner = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'c24cf47c-5077-412d-a19c-45202126392c') // Container Apps SandboxGroup Data Owner
var roleAcrPull = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '7f951dda-4ed3-4680-a7ca-43fe172d538d')
var roleBlobContributor = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'ba92f5b4-2d11-453d-a403-e96b0029c9fe') // Storage Blob Data Contributor

// ---------------------------------------------------------------------------
// The identity Runtm's control plane acts as (twin of RuntmSandboxAccessRole).
// Trust: Google-issued ID tokens for exactly one Google service account
// (numeric unique id as subject) with exactly one audience. No secret anywhere.
// ---------------------------------------------------------------------------
resource broker 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: brokerIdentityName
  location: location
  tags: union(tags, { 'runtm:component': 'broker-identity' })
}

resource brokerGoogleTrust 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  parent: broker
  name: federatedCredentialName
  properties: {
    issuer: googleIssuer
    subject: googleSubject
    audiences: [audience]
  }
}

// ---------------------------------------------------------------------------
// Sandbox group with the broker identity attached. api-version 2026-07-01's
// published schema has no `identity` (Bicep warns BCP187), but the live
// service persists one (spike 04: attached by PATCH). Bicep can only PUT, so
// the identity rides in the PUT envelope — the same thing Microsoft's Bicep
// quickstart does with the preview api-version. VERIFY on 2026-07-01; the
// fallback is `az rest --method patch` after deployment (README).
// ---------------------------------------------------------------------------
#disable-next-line BCP187
resource sandboxGroup 'Microsoft.App/sandboxGroups@2026-07-01' = {
  name: sandboxGroupName
  location: location
  tags: union(tags, { 'runtm:component': 'sandbox-group' })
  #disable-next-line BCP187
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${broker.id}': {}
    }
  }
  properties: {}
}

// ---------------------------------------------------------------------------
// Container registry for sandbox images (Basic; builds happen on Runtm's
// side, the service pulls with the group identity).
// ---------------------------------------------------------------------------
resource registry 'Microsoft.ContainerRegistry/registries@2023-07-01' = {
  name: registryName
  location: location
  sku: { name: 'Basic' }
  tags: union(tags, { 'runtm:component': 'sandbox-images' })
  properties: {
    adminUserEnabled: false
  }
}

// ---------------------------------------------------------------------------
// Storage account for workspace layers (sessions/) and template goldens
// (templates/): versioned, TLS 1.2, HTTPS only, no anonymous access, no keys.
// ---------------------------------------------------------------------------
resource layers 'Microsoft.Storage/storageAccounts@2024-01-01' = {
  name: storageAccountName
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  tags: union(tags, { 'runtm:component': 'sandbox-artifacts' })
  properties: {
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowBlobPublicAccess: false
    allowSharedKeyAccess: storageSharedKeyAccess
    defaultToOAuthAuthentication: true
    accessTier: 'Hot'
  }
}

resource layersBlob 'Microsoft.Storage/storageAccounts/blobServices@2024-01-01' = {
  parent: layers
  name: 'default'
  properties: {
    isVersioningEnabled: true
  }
}

resource layersContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2024-01-01' = {
  parent: layersBlob
  name: containerName
  properties: {
    publicAccess: 'None'
  }
}

// Blob lifecycle (prefixes are <container>/<prefix>). Azure has no "keep the
// newest N versions" rule and no abort-incomplete-multipart rule.
resource layersLifecycle 'Microsoft.Storage/storageAccounts/managementPolicies@2024-01-01' = {
  parent: layers
  name: 'default'
  dependsOn: [layersContainer]
  properties: {
    policy: {
      rules: [
        {
          // sessions/ = paused-session workspace layers; superseded versions go after a day.
          name: 'expire-session-layers'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: ['blockBlob']
              prefixMatch: ['${containerName}/sessions/']
            }
            actions: {
              baseBlob: { delete: { daysAfterModificationGreaterThan: sessionArtifactRetentionDays } }
              version: { delete: { daysAfterCreationGreaterThan: 1 } }
            }
          }
        }
        {
          // templates/ = template goldens. The CURRENT version never expires; only superseded versions are purged.
          name: 'retain-template-goldens'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: ['blockBlob']
              prefixMatch: ['${containerName}/templates/']
            }
            actions: {
              version: { delete: { daysAfterCreationGreaterThan: templateVersionRetentionDays } }
            }
          }
        }
      ]
    }
  }
}

// Twin of DeletionPolicy: Retain. Remove the lock before deleting the account.
resource layersLock 'Microsoft.Authorization/locks@2020-05-01' = {
  name: 'runtm-sandbox-layers-retain'
  scope: layers
  properties: {
    level: 'CanNotDelete'
    notes: 'Runtm sandbox workspace layers and template goldens. Remove this lock before deleting the storage account.'
  }
}

// ---------------------------------------------------------------------------
// Role assignments for the broker identity, exactly as the spike granted them.
// ---------------------------------------------------------------------------
resource brokerContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, broker.id, roleContributor)
  properties: {
    roleDefinitionId: roleContributor
    principalId: broker.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource brokerSandboxDataOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(sandboxGroup.id, broker.id, roleSandboxDataOwner)
  scope: sandboxGroup
  properties: {
    roleDefinitionId: roleSandboxDataOwner
    principalId: broker.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource brokerAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(registry.id, broker.id, roleAcrPull)
  scope: registry
  properties: {
    roleDefinitionId: roleAcrPull
    principalId: broker.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

resource brokerBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(layers.id, broker.id, roleBlobContributor)
  scope: layers
  properties: {
    roleDefinitionId: roleBlobContributor
    principalId: broker.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Optional VNet: delegated subnet + vnetConnections child on the group.
// ---------------------------------------------------------------------------
resource vnet 'Microsoft.Network/virtualNetworks@2024-05-01' = if (createVnet) {
  name: vnetName
  location: location
  tags: union(tags, { 'runtm:component': 'sandbox-vnet' })
  properties: {
    addressSpace: { addressPrefixes: [vnetCidr] }
    subnets: [
      {
        name: subnetName
        properties: {
          addressPrefix: subnetCidr
          delegations: [
            {
              name: 'sandboxes'
              properties: { serviceName: 'Microsoft.App/environments' }
            }
          ]
        }
      }
    ]
  }
}

resource vnetConnection 'Microsoft.App/sandboxGroups/vnetConnections@2026-07-01' = if (createVnet) {
  parent: sandboxGroup
  name: vnetConnectionName
  properties: {
    subnetId: resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, subnetName)
  }
  dependsOn: [vnet]
}

output brokerClientId string = broker.properties.clientId
output brokerPrincipalId string = broker.properties.principalId
output brokerIdentityId string = broker.id
output sandboxGroupName string = sandboxGroup.name
output sandboxGroupId string = sandboxGroup.id
output acrLoginServer string = registry.properties.loginServer
output storageAccount string = layers.name
output container string = layersContainer.name
output vnetId string = createVnet ? vnet.id : ''
output subnetId string = createVnet ? resourceId('Microsoft.Network/virtualNetworks/subnets', vnetName, subnetName) : ''
output vnetConnectionName string = createVnet ? vnetConnectionName : ''
