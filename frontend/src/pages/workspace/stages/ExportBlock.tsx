import type { ReactNode } from 'react'

import { Section } from '../../../components/Section'
import { EXPORT_BLOCK_TITLES, SIMPLE_EXPORT_BLOCKS, exportBlockOrder, exportBlockStorageKey, firstExportBlock, type ExportBlockId } from '../exportBlocks'
import { useSimple, useStage } from '../StageContext'
import { titleKind } from '../titleKind'

interface Props {
  id: ExportBlockId
  // Why the export cannot run yet; shown beside the title while folded, so no block hides a reason.
  reason?: string | null
  // Shown beside the title while folded and nothing blocks the export.
  hint?: string
  children: ReactNode
  // Progress and the finished file. Rendered outside the fold so a running export is never hidden.
  status?: ReactNode
}

// One collapsible export. The blocks share an accordion group, so the last one opened is the one remembered
// open (per title kind) and the page stays short on a phone.
export function ExportBlock({ id, reason, hint, children, status }: Props) {
  const { drama } = useStage()
  const simple = useSimple()
  const kind = titleKind(drama.media_type, drama.content_mode)
  const title = EXPORT_BLOCK_TITLES[id]
  // The block that opens first is the first one shown, which in Simple is not always the Full one.
  const first = simple ? exportBlockOrder(kind).find((b) => SIMPLE_EXPORT_BLOCKS.includes(b)) : firstExportBlock(kind)
  return (
    <div className="export-block" role="group" aria-label={title}>
      <Section
        group="export"
        storageKey={exportBlockStorageKey(kind, id)}
        title={title}
        summary={reason ?? hint}
        defaultOpen={first === id}
      >
        {children}
      </Section>
      {status}
    </div>
  )
}
