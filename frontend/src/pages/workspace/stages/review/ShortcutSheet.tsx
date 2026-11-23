import { Sheet } from '../../../../components/Sheet'

import { REVIEW_EDITING_KEYS, REVIEW_KEYS } from './reviewKeys'

export function ShortcutSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  return (
    <Sheet open={open} title="Keyboard shortcuts" onClose={onClose}>
      <dl className="review-keys-list">
        {REVIEW_KEYS.map(([k, v]) => (
          <div key={k}>
            <dt><kbd>{k}</kbd></dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      <h3>While editing</h3>
      <dl className="review-keys-list">
        {REVIEW_EDITING_KEYS.map(([k, v]) => (
          <div key={k}>
            <dt><kbd>{k}</kbd></dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
    </Sheet>
  )
}
