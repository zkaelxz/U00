// The Comic page's two ways to bring a comic in: "Add a Comic" (links, images, CBZ) and "Read translated" (a chapter link).
import { useState } from 'react'

import { AddComicDialog } from './AddComicDialog'

export function AddComicButtons() {
  const [open, setOpen] = useState<'add' | 'translated' | null>(null)
  const close = () => setOpen(null)
  return (
    <>
      <button type="button" className="comic-tools-btn" onClick={() => setOpen('add')}>
        Add a Comic
      </button>
      <button type="button" className="comic-tools-btn" onClick={() => setOpen('translated')}>
        Read translated
      </button>
      {open === 'add' && <AddComicDialog open onClose={close} />}
      {open === 'translated' && <AddComicDialog open readTranslated onClose={close} />}
    </>
  )
}
