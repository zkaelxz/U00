import { formatLineNumbers } from './useLineSelection'

export interface SelectionActionContext {
  dramaId: number
  selectedIds: number[]
  // Numbers shown on the rows (`#12`), ascending; lines never shown are left out.
  lineNumbers: number[]
  clear: () => void
  // A short message in the Review status line.
  notify: (message: string) => void
}

export interface SelectionAction {
  id: string
  label: string
  run: (ctx: SelectionActionContext) => void | Promise<void>
}

// The buttons of the selection bar, in order. Another feature adds its action
// by appending an entry here; neither the rows nor the bar change.
export const SELECTION_ACTIONS: SelectionAction[] = [
  {
    id: 'copy-line-numbers',
    label: 'Copy line numbers',
    run: async ({ lineNumbers, notify }) => {
      try {
        await navigator.clipboard.writeText(formatLineNumbers(lineNumbers))
        notify(`Copied ${lineNumbers.length} line number${lineNumbers.length === 1 ? '' : 's'}.`)
      } catch {
        notify('Could not copy to the clipboard.')
      }
    },
  },
]
