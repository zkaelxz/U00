import type { LinePatch } from '../../types/review'

// The text the line had when its pencil was clicked rides along as `expected`, so a
// translation changed meanwhile (another tab, Review, a retranslate) is refused with a
// 409 instead of silently overwritten. Null: nothing changed, so nothing is sent.
export function lineEditPatch(seenEn: string, draftEn: string): LinePatch | null {
  if (draftEn === seenEn) return null
  return { en: draftEn, expected: { en: seenEn } }
}
