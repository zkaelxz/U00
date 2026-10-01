// Step 60: labels for the independent review role's verdict.
import type { BadgeTone } from '../../components/labels'
import type { ReviewVerdict } from '../../types/assistant'

const REVIEW_VERDICTS: Record<ReviewVerdict, { label: string; tone: BadgeTone; text: string }> = {
  agrees: { label: 'Agrees', tone: 'ok', text: 'The reviewer found no problem with this fix.' },
  concerns: { label: 'Concerns', tone: 'warn', text: 'The reviewer found problems. Read them before using this fix.' },
  unclear: { label: 'No verdict', tone: 'neutral', text: 'The reviewer gave no clear verdict. Read its notes.' },
  unavailable: { label: 'Not reviewed', tone: 'neutral', text: 'The independent review could not run.' },
}

export function verdictInfo(v: string) {
  return REVIEW_VERDICTS[v as ReviewVerdict] ?? REVIEW_VERDICTS.unclear
}

// Whether the saved review engine can give an independent review of the implementing engine.
export function reviewEngineProblem(implementEngine: string, reviewEngine: string): string | null {
  if (!reviewEngine) return 'Pick a review engine.'
  if (implementEngine && implementEngine === reviewEngine) return 'Pick an engine different from the one that answers.'
  return null
}
