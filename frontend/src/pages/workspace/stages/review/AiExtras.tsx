import { useState } from 'react'

import { Section } from '../../../../components/Section'
import { readSectionOpen } from '../../../../components/sectionStorage'
import { AiExtrasBurnPreview } from './AiExtrasBurnPreview'
import { AiExtrasMerge } from './AiExtrasMerge'
import { AiExtrasSenseVoice } from './AiExtrasSenseVoice'
import { AiExtrasStyle } from './AiExtrasStyle'

interface Props {
  dramaId: number
  reloads: number
  jobRunning: boolean
  onChanged: () => void
}

const KEY = 'review.aiExtras'

function initiallyOpen(): boolean {
  try {
    return readSectionOpen(window.localStorage, KEY, false)
  } catch {
    return false
  }
}

// Review "AI extras" (inventory R46, R37, R35, R03): optional tools, each in
// its own collapsible section. Nothing is fetched until the group is opened.
export function AiExtras({ dramaId, reloads, jobRunning, onChanged }: Props) {
  const [opened, setOpened] = useState(initiallyOpen)
  return (
    <div role="group" aria-label="AI extras">
      <Section
        storageKey={KEY}
        title="AI extras"
        summary="Merge short lines · learn my style · audio tags · burned preview"
        onToggle={(open) => open && setOpened(true)}
      >
        {opened && (
          <div className="stack">
            <AiExtrasMerge dramaId={dramaId} jobRunning={jobRunning} onChanged={onChanged} />
            <AiExtrasStyle dramaId={dramaId} reloads={reloads} />
            <AiExtrasSenseVoice dramaId={dramaId} reloads={reloads} />
            <AiExtrasBurnPreview dramaId={dramaId} />
          </div>
        )}
      </Section>
    </div>
  )
}
