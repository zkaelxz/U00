// Per-source domain lists (api/routers: /api/source-domains), from the generated schema.
// Hosts are `host` or `host:port` (443 is never shown), plain ASCII DNS names.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

// last_good is generated as optional; callers read it as `string | null`.
export type SourceDomainEntry = Omit<Schemas['SourceDomainList'], 'last_good'> & { last_good: string | null }
export type SourceDomainProposal = Schemas['SourceDomainProposal']
export type SourceDomainDismissed = Schemas['SourceDomainProposalDismissed']
