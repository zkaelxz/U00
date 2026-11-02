// User administration + audit log view (api/admin_users_schemas.py), from the generated schema.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

// `has_own_ollama_key` stays optional: the page treats a missing flag as false and fixtures omit it.
export type AdminUser = Omit<Schemas['AdminUser'], 'has_own_ollama_key'> & Partial<Pick<Schemas['AdminUser'], 'has_own_ollama_key'>>
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
