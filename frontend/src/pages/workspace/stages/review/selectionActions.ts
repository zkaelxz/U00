import { copyText } from '../../../../components/clipboard'
import { compareSelectedProblem } from './compareTranscriptionLogic'
import { retimeSelectedProblem } from './retimeLogic'
import { retranscribeLinesProblem } from './retranscribeLinesLogic'
import { formatLineNumbers } from './useLineSelection'

export interface SelectionActionContext {
  dramaId: number
  selectedIds: number[]
  // Numbers shown on the rows (`#12`), ascending; lines never shown are left out.
  lineNumbers: number[]
  clear: () => void
  // A short message in the Review status line.
  notify: (message: string) => void
  // Opens Review's Compare transcription section on the ticked lines.
  openCompare: () => void
  // Opens Review's Re-time with Qwen3 aligner section on the ticked lines.
  openRetime: () => void
  // Opens Review's Re-transcribe section and starts it on exactly these lines.
  openRetranscribe: (lineIds: number[]) => void
}

export interface SelectionAction {
  id: string
  label: string
  // A plain-words reason the action can't run on this selection; the bar then
  // disables the button and shows it.
  unavailable?: (ctx: SelectionActionContext) => string | null
  run: (ctx: SelectionActionContext) => void | Promise<void>
}

// The buttons of the selection bar, in order. Another feature adds its action
// by appending an entry here; neither the rows nor the bar change.
export const SELECTION_ACTIONS: SelectionAction[] = [
  {
    id: 'copy-line-numbers',
    label: 'Copy line numbers',
    run: async ({ lineNumbers, notify }) => {
      notify(
        (await copyText(formatLineNumbers(lineNumbers)))
          ? `Copied ${lineNumbers.length} line number${lineNumbers.length === 1 ? '' : 's'}.`
          : 'Could not copy to the clipboard.',
      )
    },
  },
  {
    id: 'compare-transcription',
    label: 'Compare transcription…',
    unavailable: ({ selectedIds }) => compareSelectedProblem(selectedIds.length),
    run: ({ openCompare }) => openCompare(),
  },
  {
    id: 're-time',
    label: 'Re-time with Qwen3 aligner…',
    unavailable: ({ selectedIds }) => retimeSelectedProblem(selectedIds.length),
    run: ({ openRetime }) => openRetime(),
  },
  {
    id: 're-transcribe',
    label: 'Re-transcribe selected',
    unavailable: ({ selectedIds }) => retranscribeLinesProblem(selectedIds.length),
    run: ({ selectedIds, openRetranscribe }) => openRetranscribe(selectedIds),
  },
]
