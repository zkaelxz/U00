// User administration + audit log view (api/admin_users_schemas.py), from the generated schema.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

// The fields added after the first fixtures stay optional: the page treats a missing one as false,
// no spending and no limit.
type LaterFields = 'has_own_ollama_key' | 'month_paid_spend_usd' | 'monthly_paid_cap_usd'
export type AdminUser = Omit<Schemas['AdminUser'], LaterFields> & Partial<Pick<Schemas['AdminUser'], LaterFields>>
export type AdminUserList = Omit<Schemas['AdminUserList'], 'users'> & { users: AdminUser[] }
export type AdminSessionsRevoked = Schemas['AdminSessionsRevoked']
export type AuditEvent = Schemas['AuditEvent']
export type AuditPage = Schemas['AuditPage']

export interface AuditQuery {
  limit?: number
  before_id?: number | null
  action?: string | null
  user_id?: number | null
}
