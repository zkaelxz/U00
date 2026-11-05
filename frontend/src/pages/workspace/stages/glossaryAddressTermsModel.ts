import type { AddressTermChoice } from '../../../types/translateStage'

export type Pick = { text: string; keep: boolean }

// A pick is ready to save once it has a rendering or is kept as written; an empty one is left out.
export function pickedChoices(picks: Record<string, Pick>): AddressTermChoice[] {
  return Object.entries(picks).flatMap<AddressTermChoice>(([term, p]) => {
    if (p.keep) return [{ term, keep_as_is: true }]
    const translation = p.text.trim()
    return translation ? [{ term, translation }] : []
  })
}
