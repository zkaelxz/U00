// PC alerts (api/routers/resource_alert_routes.py), from the generated schema.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type ResourceAlertRule = Schemas['ResourceAlertRule']
export type ResourceAlertRules = Schemas['ResourceAlertRules']

// POST body: only the alerts named change, and within one only the fields sent.
export type ResourceAlertChange = { enabled?: boolean; limit?: number; minutes?: number }
