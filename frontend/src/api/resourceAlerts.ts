// PC alerts card (api/routers/resource_alert_routes.py). Reading needs admin.settings; saving is PC only.
import type { ResourceAlertChange, ResourceAlertRules } from '../types/resourceAlerts'
import { getJson, postJson, type Fetch } from './client'
import { pcOnlyFetch } from './pcOnly'

const BASE = '/api/settings/resource-alerts'

export const getResourceAlerts = (f?: Fetch) => getJson<ResourceAlertRules>(BASE, f)

export const setResourceAlerts = (rules: Record<string, ResourceAlertChange>, f?: Fetch) =>
  postJson<ResourceAlertRules>(BASE, { rules }, pcOnlyFetch(f))
