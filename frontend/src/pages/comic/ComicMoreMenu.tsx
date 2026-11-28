// The phone comic header's "More" sheet: the secondary actions that would otherwise take two extra rows.
import { useState } from 'react'

import { Sheet } from '../../components/Sheet'
import { SourceLink } from '../discover/ExternalLink'
import { AddComicDialog } from './AddComicDialog'

type Props = { dramaId: number; sourceUrl: string | null | undefined; editHref: string | null }

export function ComicMoreMenu({ dramaId, sourceUrl, editHref }: Props) {
  const [open, setOpen] = useState<'more' | 'add' | 'translated' | null>(null)
  const close = () => setOpen(null)
  return (
    <>
      <button type="button" className="comic-tools-btn" aria-haspopup="dialog" onClick={() => setOpen('more')}>
        More
      </button>
      <Sheet open={open === 'more'} title="More" onClose={close}>
        <div className="comic-more">
          <button type="button" onClick={() => setOpen('add')}>Add a Comic</button>
          <button type="button" onClick={() => setOpen('translated')}>Read translated</button>
          {editHref && <a className="comic-tools-btn" href={editHref}>Editor</a>}
          <SourceLink href={sourceUrl} dramaId={dramaId} className="comic-tools-btn" />
        </div>
      </Sheet>
      {open === 'add' && <AddComicDialog open onClose={close} />}
      {open === 'translated' && <AddComicDialog open readTranslated onClose={close} />}
    </>
  )
}
