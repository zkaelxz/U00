// Per-source domain lists (api/routers: /api/source-domains). Hosts are
// `host` or `host:port` (443 is never shown), plain ASCII DNS names.

export type SourceDomainEntry = {
  source: string
  display_name: string
  domains: string[]
  default_domains: string[]
  customized: boolean
  last_good: string | null
  pending_proposals: number
}

export type SourceDomainProposal = {
  source: string
  display_name: string
  host: string
  // Epoch seconds.
  found_at: number
}

export type SourceDomainDismissed = { dismissed: boolean }
