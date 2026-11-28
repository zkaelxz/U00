// One correction to a line's English, as a find and replace other lines can take ("Same fix in N lines?").

import { previewFindReplace, unsentNote } from '../../../../api/review'
import { saveWordReplacement } from '../../../../api/translateStage'
import type { ReviewMatch } from '../../../../types/review'
import { applyAfterEdit } from './linesController'

export interface SameFix {
  find: string
  replace: string
}

export interface SameFixOffer {
  fix: SameFix
  matches: ReviewMatch[]
}

// Longer than this is a rewrite of the line, not a fix another line would share.
const MAX_FIX_CHARS = 80
const TOKENS = /[\p{L}\p{N}_]+|\s+|[^\p{L}\p{N}_\s]/gu
const WORDY = /[\p{L}\p{N}]/u

// Pronouns, articles and the like change because of the line around them, not because a
// word was wrong, so the same change in every other line would usually break them.
const GRAMMAR_WORDS = new Set(('a an the is was are were be been am do does did has have had he she it they '
  + 'him her his hers its them their we us our you your i me my mine this that these those to of in on at '
  + 'by for with from and or but not no').split(' '))

const tokens = (text: string) => text.match(TOKENS) ?? []

function usable(find: string, replace: string): SameFix | null {
  find = find.trim()
  replace = replace.trim()
  // One letter ("I", "a") is in nearly every line. A deletion would leave other lines with
  // doubled spaces or stray punctuation, so only a replacement is offered.
  if (find.length < 2 || !replace || find.length > MAX_FIX_CHARS || replace.length > MAX_FIX_CHARS) return null
  if (!WORDY.test(find) || find === replace || GRAMMAR_WORDS.has(find.toLowerCase())) return null
  return { find, replace }
}

/** Whether the fix can also be a series word replacement, which ignores capitals and so
 *  refuses a change of capitals only. */
export const canAlways = (fix: SameFix) => fix.find.toLowerCase() !== fix.replace.toLowerCase()

/** The find and replace that turned `before` into `after`, in whole words: the one stretch
 *  that changed. Null for an insertion, a deletion or a rewrite of most of the line. */
export function sameFixOf(before: string, after: string): SameFix | null {
  const a = tokens(before)
  const b = tokens(after)
  if (a.length === b.length) {
    // The same word fixed in several places of the line ("Lin met Lin") is still one fix.
    const changed = new Set<string>()
    for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) changed.add(`${a[i]}\u0000${b[i]}`)
    if (changed.size === 1) {
      const i = a.findIndex((t, k) => t !== b[k])
      return usable(a[i], b[i])
    }
  }
  let start = 0
  while (start < a.length && start < b.length && a[start] === b[start]) start += 1
  let end = 0
  while (end < a.length - start && end < b.length - start && a[a.length - 1 - end] === b[b.length - 1 - end]) end += 1
  return usable(a.slice(start, a.length - end).join(''), b.slice(start, b.length - end).join(''))
}

const PATTERN_SPECIAL = /[.*+?^${}()|[\]\\]/g

/** A Python regular expression matching `find` as whole words, for the find-and-replace preview. */
export const wholeWordPattern = (find: string) => `(?<!\\w)${find.replace(PATTERN_SPECIAL, '\\$&')}(?!\\w)`

/** `text` as a Python re.sub replacement that is used as written. */
export const literalReplacement = (text: string) => text.replace(/\\/g, '\\\\')

/** The other lines of the title that the fix saved to `lineId` would change, or null when the
 *  edit is not one fix or no other line has the old wording. */
export async function findSameFix(
  dramaId: number, lineId: number, before: string, after: string, preview = previewFindReplace,
): Promise<SameFixOffer | null> {
  const fix = sameFixOf(before, after)
  if (!fix) return null
  const found = await preview(dramaId, {
    find: wholeWordPattern(fix.find), replace: literalReplacement(fix.replace), case_sensitive: true, use_regex: true,
  })
  const matches = found.filter((m) => m.id !== lineId)
  return matches.length ? { fix, matches } : null
}

/** Applies the offer and, when `always` and some line took it, saves it as a series word
 *  replacement; returns what to say. `blocked` when an open edit stopped it and the offer stands. */
export async function applySameFix(
  dramaId: number, offer: SameFixOffer, always: boolean,
  { apply = applyAfterEdit, saveReplacement = saveWordReplacement } = {},
): Promise<{ text: string; changed: boolean; blocked?: boolean }> {
  const r = await apply(dramaId, offer.matches)
  if (typeof r === 'string') return { text: r, changed: false, blocked: true }
  const n = r.applied
  const saved = always && n > 0 && canAlways(offer.fix)
  let rule = ''
  if (saved) {
    // The lines are already fixed, so a refused rule must not hide that.
    rule = await saveReplacement(dramaId, { find_text: offer.fix.find, replace_text: offer.fix.replace }).then(
      () => ' New translations in this series get the same fix.',
      () => ' The fix could not be saved for new translations in this series.')
  }
  const stale = r.stale ? `; ${r.stale} changed meanwhile and ${r.stale === 1 ? 'was' : 'were'} left` : ''
  return {
    text: `Fixed ${n} more line${n === 1 ? '' : 's'}${stale}.${rule}${unsentNote(r)}`,
    changed: n > 0,
  }
}
