// Why a suggested speaker is offered, in words: the button's name and tooltip.
import type { SpeakerGuess } from '../../../../types/review'

export type Reason = SpeakerGuess['reasons'][number]

const WHY: Record<Reason, string> = {
  above: 'the line above',
  below: 'the line below',
  voice: 'what speaker detection heard',
}

export function guessReason(reasons: Reason[]): string {
  const said = reasons.map((r) => WHY[r])
  const joined = said.length > 1 ? `${said.slice(0, -1).join(', ')} and ${said[said.length - 1]}` : said[0]
  return reasons[0] === 'voice' ? `From ${joined}` : `Same as ${joined}`
}
