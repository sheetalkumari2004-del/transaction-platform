// Deploys the API and worker as two separate Azure Container Apps inside
// one Container Apps Environment. Postgres and Redis are expected to be
// external free-tier services (Neon, Upstash) per assignment section 28
// — their connection strings come in as secrets, not provisioned here.
//
// Usage:
//   az deployment group create \
//     --resource-group <rg> \
//     --template-file deployment/main.bicep \
//     --parameters containerRegistry=<acr-login-server> \
//                  imageTag=<tag> \
//                  databaseUrl=<neon-connection-string> \
//                  redisUrl=<upstash-connection-string>

param location string = resourceGroup().location
param appNamePrefix string = 'txn-platform'
param containerRegistry string
param imageTag string = 'latest'

@secure()
param databaseUrl string
@secure()
param redisUrl string
@secure()
param acrUsername string
@secure()
param acrPassword string

param apiMinReplicas int = 1
param apiMaxReplicas int = 3
param workerMinReplicas int = 1
param workerMaxReplicas int = 5

<<<<<<< HEAD
=======
var acrPullRoleId = '7f951dda-4ed3-4680-a7ca-43fe172d538d' // built-in AcrPull role

// trim() guards against stray whitespace/newlines that can sneak into
// GitHub secrets (e.g. from `az acr show ... | gh secret set`), which
// otherwise produce an invalid image reference like 'registry\n/name:tag'.
var cleanRegistry = trim(containerRegistry)
var cleanImageTag = trim(imageTag)

resource acr 'Microsoft.ContainerRegistry/registries@2023-07-01' existing = {
  name: acrName
}

>>>>>>> bcea66a996247b622899fe1fc302bbf7cb5ac9ed
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: '${appNamePrefix}-logs'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource containerAppEnv 'Microsoft.App/managedEnvironments@2023-05-01' = {
  name: '${appNamePrefix}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
  }
}

resource apiApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: '${appNamePrefix}-api'
  location: location
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
      }
      secrets: [
        { name: 'database-url', value: databaseUrl }
        { name: 'redis-url', value: redisUrl }
        { name: 'acr-password', value: acrPassword }
      ]
      registries: [
<<<<<<< HEAD
        // Azure for Students / newer subscriptions provision a
        // "Consumption (express)" Container Apps environment, which does
        // NOT support managed-identity-based ACR authentication
        // (identity: 'system' throws "ExpressEnvironmentFeatureNotSupported").
        // Username/password (ACR admin credentials) works on every
        // environment type, so that's used here instead.
        { server: containerRegistry, username: acrUsername, passwordSecretRef: 'acr-password' }
=======
        { server: cleanRegistry, identity: 'system' }
>>>>>>> bcea66a996247b622899fe1fc302bbf7cb5ac9ed
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: '${cleanRegistry}/transaction-platform:${cleanImageTag}'
          command: ['uvicorn', 'app.main:app', '--host', '0.0.0.0', '--port', '8000']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'REDIS_URL', secretRef: 'redis-url' }
            { name: 'APP_ENV', value: 'production' }
            { name: 'LOG_LEVEL', value: 'INFO' }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: { path: '/health/live', port: 8000 }
              initialDelaySeconds: 10
            }
            {
              type: 'Readiness'
              httpGet: { path: '/health/ready', port: 8000 }
              initialDelaySeconds: 5
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: apiMinReplicas
        maxReplicas: apiMaxReplicas
        rules: [
          {
            name: 'http-scale'
            http: { metadata: { concurrentRequests: '50' } }
          }
        ]
      }
    }
  }
}

resource workerApp 'Microsoft.App/containerApps@2023-05-01' = {
  name: '${appNamePrefix}-worker'
  location: location
  properties: {
    managedEnvironmentId: containerAppEnv.id
    configuration: {
      ingress: null
      secrets: [
        { name: 'database-url', value: databaseUrl }
        { name: 'redis-url', value: redisUrl }
        { name: 'acr-password', value: acrPassword }
      ]
      registries: [
<<<<<<< HEAD
        { server: containerRegistry, username: acrUsername, passwordSecretRef: 'acr-password' }
=======
        { server: cleanRegistry, identity: 'system' }
>>>>>>> bcea66a996247b622899fe1fc302bbf7cb5ac9ed
      ]
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: '${cleanRegistry}/transaction-platform:${cleanImageTag}'
          command: ['python', '-m', 'app.workers.import_worker']
          env: [
            { name: 'DATABASE_URL', secretRef: 'database-url' }
            { name: 'REDIS_URL', secretRef: 'redis-url' }
            { name: 'APP_ENV', value: 'production' }
            { name: 'LOG_LEVEL', value: 'INFO' }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        // No HTTP ingress to scale on, so this scales on a fixed range;
        // for real queue-depth-based scaling, add a KEDA Redis Streams
        // scaler rule here (Container Apps supports KEDA scalers natively).
        minReplicas: workerMinReplicas
        maxReplicas: workerMaxReplicas
      }
    }
  }
}

output apiUrl string = 'https://${apiApp.properties.configuration.ingress.fqdn}'

      
