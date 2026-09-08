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
param databaseUrlSync string
@secure()
param redisUrl string

param apiMinReplicas int = 1
param apiMaxReplicas int = 3
param workerMinReplicas int = 1
param workerMaxReplicas int = 5

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
      ]
      registries: [
        { server: containerRegistry, identity: 'system' }
      ]
    }
    template: {
      containers: [
        {
          name: 'api'
          image: '${containerRegistry}/transaction-platform:${imageTag}'
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
  identity: {
    type: 'SystemAssigned'
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
      ]
      registries: [
        { server: containerRegistry, identity: 'system' }
      ]
    }
    template: {
      containers: [
        {
          name: 'worker'
          image: '${containerRegistry}/transaction-platform:${imageTag}'
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
  identity: {
    type: 'SystemAssigned'
  }
}

output apiUrl string = 'https://${apiApp.properties.configuration.ingress.fqdn}'
