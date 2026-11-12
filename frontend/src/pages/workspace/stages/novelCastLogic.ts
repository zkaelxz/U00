/** What the cast card shows and says, kept apart from the component so it can be tested on its own. */
import type { CastPerson, Pronouns } from '../../../api/novelCast'

export const CHOICES: Pronouns[] = ['she/her', 'he/him', 'they/them']

export const isChoice = (v: string): v is Pronouns => (CHOICES as string[]).includes(v)

/** What to show for a person: the scan's reading unless pronouns are already set. */
export function initialPick(p: CastPerson): { checked: boolean; pronouns: Pronouns | '' } {
  const settled = !!p.current_pronouns
  return { checked: !settled && isChoice(p.pronouns), pronouns: isChoice(p.pronouns) ? p.pronouns : '' }
}

export function reportText(r: { set: string[]; added: string[]; kept: string[]; not_applied: string[] }): string {
  const parts = []
  if (r.set.length + r.added.length) parts.push(`Set pronouns for ${[...r.set, ...r.added].join(', ')}.`)
  if (r.kept.length) parts.push(`Left as they were: ${r.kept.join(', ')} (already set).`)
  if (r.not_applied.length) {
    parts.push(`Not applied: ${r.not_applied.join(', ')} (no speaker of that name yet, and the title has no series to keep them in).`)
  }
  return parts.join(' ') || 'Nothing to apply.'
}
