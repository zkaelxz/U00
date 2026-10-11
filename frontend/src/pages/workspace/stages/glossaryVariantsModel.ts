// Pure helpers for the "Find similar spellings" and "Possible names" panels.
import type { GlossaryNameCluster, GlossaryTermUpsert } from '../../../types/translateStage'

/** Plain words for a 0..1 sound match, so people don't read a bare decimal. */
export function matchLabel(score: number): string {
  if (score >= 0.95) return 'Sounds the same'
  if (score >= 0.8) return 'Very close'
  return 'Close'
}

/** The aliases after adding `text`, keeping the order and never listing it twice. */
export function aliasesWith(aliases: readonly string[], text: string): string[] {
  const t = text.trim()
  return !t || aliases.includes(t) ? [...aliases] : [...aliases, t]
}

/** Why a candidate can't be added as a new alias, or null when it can. */
export function aliasBlocker(c: { is_alias: boolean; is_term: boolean }): string | null {
  if (c.is_alias) return 'Already an alias of this term.'
  if (c.is_term) return 'Already a term of its own.'
  return null
}

/** Why a cluster can't become a new term yet, or null when it can. */
export function clusterBlocker(cluster: GlossaryNameCluster, english: string, hasSeries: boolean): string | null {
  if (!hasSeries) return 'Still needed: a series (above).'
  if (cluster.existing_term) return `Already in the glossary as “${cluster.existing_term}”.`
  if (!english.trim()) return 'Type the English name first.'
  return null
}

/** The create-term body for a cluster: its most common spelling is the original, the rest are aliases. */
export function clusterTermBody(cluster: GlossaryNameCluster, english: string): GlossaryTermUpsert {
  return {
    term_original: cluster.suggested_original,
    term_translation: english.trim(),
    aliases: cluster.spellings.map((s) => s.text).filter((t) => t !== cluster.suggested_original),
  }
}

const SOURCE_WORDS: Record<string, string> = {
  speaker: 'speaker label',
  katakana: 'katakana word',
  latin: 'capitalised word',
  honorific: 'before an honorific',
  vocative: 'start of a line',
}

export function sourceLabel(source: string): string {
  return SOURCE_WORDS[source] ?? source
}
