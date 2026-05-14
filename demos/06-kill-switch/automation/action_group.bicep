// Action group with Logic App receiver, common alert schema enabled.
// Deployed separately so the callbackUrl (which contains '&' SAS params)
// can be passed cleanly without shell quoting issues.

param actionGroupName string = 'kill-switch-ag'
param shortName string = 'killsw'

@description('Resource ID of the Logic App workflow.')
param logicAppId string

@description('Full callback URL for the Logic App manual trigger (includes SAS).')
param callbackUrl string

resource ag 'Microsoft.Insights/actionGroups@2024-10-01-preview' = {
  name: actionGroupName
  location: 'global'
  properties: {
    groupShortName: shortName
    enabled: true
    logicAppReceivers: [
      {
        name: 'killer'
        resourceId: logicAppId
        callbackUrl: callbackUrl
        useCommonAlertSchema: true
      }
    ]
  }
}

output actionGroupId string = ag.id
