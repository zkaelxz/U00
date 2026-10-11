import type { LanguagePackDefaultResult, LanguagePackSummary } from '../../types/languagePacks'

export const PACK_LANGUAGES = [
  { id: 'zh', label: 'Chinese' },
  { id: 'ja', label: 'Japanese' },
  { id: 'ko', label: 'Korean' },
  { id: 'any', label: 'Any language' },
] as const

export interface PackGroup {
  id: string
  label: string
  packs: { pack: LanguagePackSummary; onByDefault: boolean | null }[]
}

/**
 * Packs per source language. `onByDefault` is null for "any" packs: the default choice is stored
 * per zh/ja/ko, so a pack for every language has no single default of its own.
 */
export function groupPacks(
  packs: LanguagePackSummary[],
  defaults: Record<string, LanguagePackDefaultResult | undefined>,
): PackGroup[] {
  return PACK_LANGUAGES.map(({ id, label }) => {
    const on = new Set(defaults[id]?.packs ?? [])
    return {
      id,
      label,
      packs: packs
        .filter((p) => p.language === id)
        .map((pack) => ({ pack, onByDefault: id === 'any' ? null : on.has(pack.id) })),
    }
  }).filter((g) => g.packs.length > 0)
}

export function entryText(en: string | Record<string, string>, style: string): string {
  if (typeof en === 'string') return en
  return en[style] ?? Object.values(en)[0] ?? ''
}
