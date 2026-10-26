// Entry point. Deployed at subscription scope because it creates the resource group.
targetScope = 'subscription'

@description('Azure region for all resources.')
param location string = 'norwayeast'

@description('Name of the resource group to create.')
param resourceGroupName string = 'rg-traffic-pred'

@description('SSH public key for the VM admin user (contents of ~/.ssh/id_ed25519.pub).')
param sshPublicKey string

@description('Your public IP address. SSH is only allowed from here.')
param allowedSshSourceIp string

resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
}

module resources 'resources.bicep' = {
  name: 'traffic-pred-resources'
  scope: rg
  params: {
    location: location
    sshPublicKey: sshPublicKey
    allowedSshSourceIp: allowedSshSourceIp
  }
}

output vmPublicIp string = resources.outputs.vmPublicIp
output storageAccountName string = resources.outputs.storageAccountName
