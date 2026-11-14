import type { ReactNode } from 'react'

import { Section } from '../../../components/Section'
import { readSectionOpen } from '../../../components/sectionStorage'
import { browserStorage } from '../../../hooks/usePersistedState'
import { EXPORT_BLOCK_TITLES, exportBlockStorageKey, shownExportBlocks, type ExportBlockId } from '../exportBlocks'
import { useSimple, useStage } from '../StageContext'
import { titleKind } from '../titleKind'

interface Props {
  id: ExportBlockId
  // The block the page opens when the viewer has not chosen one (openingExportBlock).
  opening: boolean
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
export function ExportBlock({ id, opening, reason, hint, children, status }: Props) {
  const { drama } = useStage()
  const simple = useSimple()
  const kind = titleKind(drama.media_type, drama.content_mode)
  const title = EXPORT_BLOCK_TITLES[id]
  // A block the viewer left open wins; opening this one as well would leave two open in the accordion.
  const remembered = shownExportBlocks(kind, simple).some((b) => readSectionOpen(browserStorage(), exportBlockStorageKey(kind, b), false))
  return (
    <div className="export-block" role="group" aria-label={title}>
      <Section
        // What can run is only known once the page's reads answer, which can move the opening block. Section
        // reads its default when it mounts, so the two blocks that swap mount again (a remembered state still wins).
        key={opening ? 'opening' : 'other'}
        group="export"
        storageKey={exportBlockStorageKey(kind, id)}
        title={title}
        summary={reason ?? hint}
        defaultOpen={opening && !remembered}
      >
        {children}
      </Section>
      {status}
    </div>
  )
}
