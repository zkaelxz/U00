import { Sheet } from '../../../../components/Sheet'

// Every shortcut also has a visible control; this sheet is the list.
const LIST: [string, string][] = [
  ['↓ or J / ↑ or K', 'Next / previous line (crosses pages)'],
  ['Alt+↓ / Alt+↑', 'Next / previous flagged line'],
  ['] / [', 'Next / previous page'],
  ['Enter or E', 'Edit the translation'],
  ['D', 'Edit details (timing, speaker, source)'],
  ['/', 'Search'],
  ['F', 'Dismiss flag'],
  ['I / W', 'Improve translation / Why this? (AI)'],
  ['M', 'Merge with next line'],
  ['A', 'Add a line after'],
  ['Shift+Delete', 'Delete line (asks to confirm)'],
  ['Space', 'Play or stop the line (with audio)'],
  ['Alt+Space', 'Play or pause'],
  ['L', 'Loop line on or off'],
  ['S / T', 'Set the line’s start / end to the playhead (with audio)'],
  ['Z / X', 'Nudge the start 0.1 s earlier / later (Shift: 0.5 s)'],
  ['C / V', 'Nudge the end 0.1 s earlier / later (Shift: 0.5 s)'],
  ['?', 'This list'],
]

const EDITING: [string, string][] = [
  ['Enter', 'Save and edit the next line'],
  ['Ctrl/⌘+S', 'Save and keep editing'],
  ['Shift+Enter', 'New line in the text'],
  ['Alt+Enter', 'Split the line at the cursor'],
  ['Esc', 'Cancel'],
]

export function ShortcutSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Sheet open={open} title="Keyboard shortcuts" onClose={onClose}>
      <dl className="review-keys-list">
        {LIST.map(([k, v]) => (
          <div key={k}>
            <dt><kbd>{k}</kbd></dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      <h4>While editing</h4>
      <dl className="review-keys-list">
        {EDITING.map(([k, v]) => (
          <div key={k}>
            <dt><kbd>{k}</kbd></dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
    </Sheet>
  )
}
