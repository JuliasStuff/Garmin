targetScope = 'resourceGroup'

@minLength(1)
@maxLength(32)
@description('Environment name (azd-env-name). Used as a suffix on resource names.')
param environmentName string

@description('Azure region for all resources.')
param location string = resourceGroup().location

var tags = {
  'azd-env-name': environmentName
  application: 'garmin-tracker'
}

var resourceToken = uniqueString(subscription().id, resourceGroup().id, environmentName)
var prefix = 'gar'

var nameStorage      = toLower('${prefix}st${take(resourceToken, 10)}')
var nameKv           = toLower('${prefix}-kv-${take(resourceToken, 10)}')
var nameFunc         = toLower('${prefix}-func-${take(resourceToken, 10)}')
var nameSwa          = toLower('${prefix}-swa-${take(resourceToken, 10)}')
var nameAppi         = toLower('${prefix}-appi-${take(resourceToken, 10)}')
var nameLog          = toLower('${prefix}-log-${take(resourceToken, 10)}')
var nameHostingPlan  = toLower('${prefix}-plan-${take(resourceToken, 10)}')

resource logWorkspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: nameLog
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: nameAppi
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logWorkspace.id
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: nameStorage
  location: location
  tags: tags
  sku: { name: 'Standard_LRS' }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    publicNetworkAccess: 'Enabled'
    allowSharedKeyAccess: false
  }
}

resource storageBlobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource deployContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: storageBlobService
  name: 'deployment'
}

resource hostingPlan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: nameHostingPlan
  location: location
  tags: tags
  kind: 'functionapp'
  sku: {
    tier: 'FlexConsumption'
    name: 'FC1'
  }
  properties: {
    reserved: true
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: nameKv
  location: location
  tags: tags
  properties: {
    sku: { family: 'A', name: 'standard' }
    tenantId: subscription().tenantId
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    publicNetworkAccess: 'Enabled'
  }
}

resource funcApp 'Microsoft.Web/sites@2024-04-01' = {
  name: nameFunc
  location: location
  tags: union(tags, { 'azd-service-name': 'api' })
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: hostingPlan.id
    httpsOnly: true
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storage.properties.primaryEndpoints.blob}deployment'
          authentication: {
            type: 'SystemAssignedIdentity'
          }
        }
      }
      runtime: {
        name: 'python'
        version: '3.12'
      }
      scaleAndConcurrency: {
        maximumInstanceCount: 40
        instanceMemoryMB: 2048
      }
    }
    siteConfig: {
      cors: {
        // PWA hostname is randomized by Azure Static Web Apps; the function
        // is gated by a function-level key, so wildcard CORS is acceptable.
        allowedOrigins: [ '*' ]
        supportCredentials: false
      }
      appSettings: [
        { name: 'AzureWebJobsStorage__accountName', value: storage.name }
        { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
        { name: 'KEY_VAULT_URI', value: keyVault.properties.vaultUri }
        { name: 'FIREBASE_COLLECTION', value: 'garminTrackers' }
        { name: 'GARMIN_PROFILE_ID', value: 'default' }
        { name: 'GARMIN_HISTORY_DAYS', value: '30' }
        { name: 'GARMIN_ACTIVITY_LIMIT', value: '20' }
        { name: 'GARMIN_STEP_GOAL', value: '10000' }
      ]
    }
  }
}

var roleKvSecretsUser = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', '4633458b-17de-408a-b874-0445c86b69e6')
resource kvSecretsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, funcApp.id, roleKvSecretsUser)
  scope: keyVault
  properties: {
    principalId: funcApp.identity.principalId
    roleDefinitionId: roleKvSecretsUser
    principalType: 'ServicePrincipal'
  }
}

var roleBlobOwner = subscriptionResourceId('Microsoft.Authorization/roleDefinitions', 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b')
resource storageBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, funcApp.id, roleBlobOwner)
  scope: storage
  properties: {
    principalId: funcApp.identity.principalId
    roleDefinitionId: roleBlobOwner
    principalType: 'ServicePrincipal'
  }
}

resource staticSite 'Microsoft.Web/staticSites@2024-04-01' = {
  name: nameSwa
  location: location
  tags: union(tags, { 'azd-service-name': 'web' })
  sku: { name: 'Free', tier: 'Free' }
  properties: {
    buildProperties: {
      appLocation: '/'
      outputLocation: '/'
    }
  }
}

output AZURE_FUNCTION_NAME string = funcApp.name
output AZURE_FUNCTION_HOSTNAME string = funcApp.properties.defaultHostName
output AZURE_FUNCTION_SYNC_URL string = 'https://${funcApp.properties.defaultHostName}/api/sync'
output AZURE_STATIC_WEB_APP_NAME string = staticSite.name
output AZURE_STATIC_WEB_APP_URL string = 'https://${staticSite.properties.defaultHostname}'
output AZURE_KEY_VAULT_NAME string = keyVault.name
output AZURE_KEY_VAULT_URI string = keyVault.properties.vaultUri
output AZURE_STORAGE_ACCOUNT string = storage.name
output AZURE_RESOURCE_GROUP string = resourceGroup().name
