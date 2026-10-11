import type { ReactNode } from 'react'

import { Section } from '../../../components/Section'
import { EXPORT_BLOCK_TITLES, exportBlockStorageKey, firstExportBlock, type ExportBlockId } from '../exportBlocks'
import { useStage } from '../StageContext'
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
  const kind = titleKind(drama.media_type, drama.content_mode)
  const title = EXPORT_BLOCK_TITLES[id]
  return (
    <div className="export-block" role="group" aria-label={title}>
      <Section
        group="export"
        storageKey={exportBlockStorageKey(kind, id)}
        title={title}
        summary={reason ?? hint}
        defaultOpen={firstExportBlock(kind) === id}
      >
        {children}
      </Section>
      {status}
    </div>
  )
}
