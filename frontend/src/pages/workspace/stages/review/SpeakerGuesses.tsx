// One-tap speakers for a line that has none; nothing is set until one is pressed.
import { buttonClass } from '../../../../components/uiClasses'
import { lineNumber } from '../../../../lineNumber'
import type { ReviewLine, SpeakerGuess } from '../../../../types/review'
import type { RowActions } from './LineRow'
import { needsSpeaker } from './reviewLogic'

type Reason = SpeakerGuess['reasons'][number]

const WHY: Record<Reason, string> = {
  above: 'the line above',
  below: 'the line below',
  voice: 'the voice detection’s unsure guess',
}
// Short enough for a phone's chip; the full reason is in the button's name.
const MARK: Record<Reason, string> = { above: '↑', below: '↓', voice: 'voice' }

export function guessReason(reasons: Reason[]): string {
  const said = reasons.map((r) => WHY[r])
  const joined = said.length > 1 ? `${said.slice(0, -1).join(', ')} and ${said[said.length - 1]}` : said[0]
  return reasons[0] === 'voice' ? `From ${joined}` : `Same as ${joined}`
}

interface Props {
  guesses: SpeakerGuess[]
  lineNumber: number
  // Desktop, active row: shows the 1/2/3 keys that pick a guess.
  showKeys: boolean
  // Roving Tab order: only the active row's buttons take Tab.
  tabbable: boolean
  onAccept: (speaker: string) => void
}

export function SpeakerGuesses({ guesses, lineNumber, showKeys, tabbable, onAccept }: Props) {
  return (
    <div className="review-speaker-guess" role="group" aria-label={`Suggested speaker for line ${lineNumber}`} data-testid="speaker-guess">
      <span className="muted">Speaker?</span>
      {guesses.map((g, i) => (
        <button
          key={g.speaker}
          type="button"
          className={buttonClass('secondary', 'sm')}
          tabIndex={tabbable ? undefined : -1}
          aria-label={`${g.speaker}: ${guessReason(g.reasons)}`}
          title={`${guessReason(g.reasons)}.${showKeys ? ` Key ${i + 1}.` : ''}`}
          onClick={() => onAccept(g.speaker)}
        >
          {showKeys && <kbd aria-hidden="true">{i + 1}</kbd>}
          <span>{g.speaker}</span>
          <span className="review-speaker-guess-why" aria-hidden="true">{g.reasons.map((r) => MARK[r]).join(' ')}</span>
        </button>
      ))}
    </div>
  )
}

// A line row's guesses: shown only while the line still needs a speaker, so a taken one disappears at once.
export function RowSpeakerGuesses({ line, guesses, active, isPhone, actions }: {
  line: ReviewLine
  guesses?: SpeakerGuess[] | null
  active: boolean
  isPhone: boolean
  actions: RowActions
}) {
  if (!guesses?.length || !needsSpeaker(line)) return null
  return (
    <SpeakerGuesses guesses={guesses} lineNumber={lineNumber(line.idx)} showKeys={active && !isPhone} tabbable={active}
      onAccept={(speaker) => void actions.acceptSpeaker(line.id, speaker)} />
  )
}
