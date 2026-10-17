// Runtm Cloud - Azure Container Apps Sandboxes integration (customer side).
//
// "Deploy to Azure" twin of the CloudFormation quick-create stack Runtm hands
// out for AWS, and of terraform/azure-sandbox. Deployed at SUBSCRIPTION scope
// so it can create the resource group and the role assignments:
//   az deployment sub create --location <region> --template-file main.bicep --parameters ...
// It creates, in YOUR subscription: a resource group, the broker managed
// identity Runtm acts as (trusting your organization's Runtm Google identity,
// no secret), the sandbox group, a container registry, a storage account for
// workspace layers, the role assignments between them, and optionally a VNet.
// Nothing here grants Runtm access outside that resource group.
targetScope = 'subscription'

// --- Runtm (pre-filled, do not edit) ---------------------------------------

@description('Runtm organization id (pre-filled by Runtm; used for tagging).')
@maxLength(64)
param organizationId string

@description('Per-organization resource slug: the first 12 characters of the organization id, lower-cased (what Runtm, the Terraform module and this template all compute). Leave the default.')
@minLength(12)
@maxLength(12)
param tenantSlug string = toLower(substring(organizationId, 0, 12))

@description('Numeric unique id of the ONE Google service account Runtm presents to every tenant (Entra matches the token subject exactly). The default is Runtm production\'s identity; the same for every organization, so the audience below is what keeps stacks apart. Only Runtm staff pointing a test stack at Runtm staging override it.')
@minLength(10)
@maxLength(30)
param googleSubject string = '115475383003204837105'

@description('Audience of the Google token Runtm mints for THIS organization: runtm-sandbox:<organization id>. The federated credential accepts only it, so a token minted for another organization is refused. Leave the default.')
@minLength(8)
param audience string = 'runtm-sandbox:${organizationId}'

@description('Issuer of the tokens Runtm presents. Do not change.')
param googleIssuer string = 'https://accounts.google.com'

// --- Placement / naming ----------------------------------------------------

@description('Azure region for every resource; must offer Container Apps Sandboxes (e.g. westus2).')
param location string = deployment().location

@description('Optional label for this stack\'s resource names, e.g. pci or ops (1-10 lowercase letters, digits or hyphens, the same cap as the AWS stack; the sandbox group name rtm-<suffix>-<slug> stays at 27 of Azure\'s 32 chars). Enter the same value in the Runtm dialog.')
@maxLength(10)
param suffix string = ''

@description('Storage account name (3-24 lowercase alphanumerics, globally unique). Pre-filled by Runtm as rtm<slug><sha256(org|suffix|region)[:9]>, which is what the Terraform module computes; the fallback default below uses uniqueString() and differs from it.')
@minLength(3)
@maxLength(24)
param storageAccountName string = 'rtm${tenantSlug}${take(uniqueString(organizationId, suffix, location), 9)}'

@description('Container registry name (5-50 alphanumerics, globally unique). Pre-filled by Runtm as rtmacr<slug><sha256(org|suffix|region)[:9]>; same fallback caveat.')
@minLength(5)
@maxLength(50)
param registryName string = 'rtmacr${tenantSlug}${take(uniqueString(organizationId, suffix, location), 9)}'

// --- Retention -------------------------------------------------------------

@description('Days to keep paused-session workspace layers (sessions/ prefix). Matches Runtm\'s paused-session retention.')
@minValue(1)
param sessionArtifactRetentionDays int = 90

@description('Template goldens (templates/) never expire; superseded versions are purged this many days after creation (Azure has no keep-newest-N rule).')
@minValue(1)
param templateVersionRetentionDays int = 30

@description('Allow Shared Key (account key) authorization on the storage account. Runtm never uses keys.')
param storageSharedKeyAccess bool = false

// --- Optional VNet (private-database leg) ----------------------------------

@description('Create a VNet with a subnet delegated to Microsoft.App/environments and connect the sandbox group to it.')
param createVnet bool = false

@description('VNet address space.')
param vnetCidr string = '10.77.0.0/16'

@description('Delegated subnet inside vnetCidr (keep at least a /23).')
param subnetCidr string = '10.77.0.0/23'

@description('Extra tags for every resource.')
param tags object = {}

// ---------------------------------------------------------------------------
var label = empty(suffix) ? tenantSlug : '${suffix}-${tenantSlug}'
var resourceGroupName = 'runtm-sandbox-${label}-${location}' // <= 61 of 90
var allTags = union(tags, {
  'runtm:organization': organizationId
  'runtm:label': label
})

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: allTags
}

module sandbox 'modules/sandbox.bicep' = {
  name: 'runtm-sandbox-${label}'
  scope: rg
  params: {
    label: label
    googleSubject: googleSubject
    googleIssuer: googleIssuer
    audience: audience
    storageAccountName: storageAccountName
    registryName: registryName
    sessionArtifactRetentionDays: sessionArtifactRetentionDays
    templateVersionRetentionDays: templateVersionRetentionDays
    storageSharedKeyAccess: storageSharedKeyAccess
    createVnet: createVnet
    vnetCidr: vnetCidr
    subnetCidr: subnetCidr
    tags: allTags
  }
}

// --- Outputs: individual + the runtm_connection bundle the dialog takes -----

@description('Paste into Runtm (Integrations -> Azure Sandboxes): client id of the broker identity.')
output brokerClientId string = sandbox.outputs.brokerClientId
output brokerPrincipalId string = sandbox.outputs.brokerPrincipalId
output brokerIdentityId string = sandbox.outputs.brokerIdentityId
output tenantId string = tenant().tenantId
output subscriptionId string = subscription().subscriptionId
output resourceGroup string = rg.name
output location string = location
output sandboxGroup string = sandbox.outputs.sandboxGroupName
output sandboxGroupId string = sandbox.outputs.sandboxGroupId
output acrLoginServer string = sandbox.outputs.acrLoginServer
output storageAccount string = sandbox.outputs.storageAccount
output container string = sandbox.outputs.container
output vnetId string = sandbox.outputs.vnetId
output subnetId string = sandbox.outputs.subnetId
output vnetConnection string = sandbox.outputs.vnetConnectionName
output resourceLabel string = label
output googleSubject string = googleSubject
output audience string = audience

@description('Everything the Runtm connect dialog asks for, in one object (paste the JSON). Same keys as the Terraform module\'s runtm_connection output.')
output runtmConnection object = {
  identity: 'google_oidc'
  tenant_id: tenant().tenantId
  subscription_id: subscription().subscriptionId
  resource_group: rg.name
  location: location
  broker_client_id: sandbox.outputs.brokerClientId
  broker_principal_id: sandbox.outputs.brokerPrincipalId
  broker_identity_id: sandbox.outputs.brokerIdentityId
  sandbox_group: sandbox.outputs.sandboxGroupName
  acr_login_server: sandbox.outputs.acrLoginServer
  storage_account: sandbox.outputs.storageAccount
  container: sandbox.outputs.container
  vnet_connection: createVnet ? sandbox.outputs.vnetConnectionName : null
  google_subject: googleSubject
  google_issuer: googleIssuer
  audience: audience
}
