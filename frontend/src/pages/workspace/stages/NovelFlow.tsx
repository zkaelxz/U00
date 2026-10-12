import { useState } from 'react'

import { ErrorBanner } from '../../../components/ErrorBanner'
import { buttonClass } from '../../../components/uiClasses'
import { useStage } from '../StageContext'
import { NarrationPanel } from './NarrationPanel'
import { startAsNovel } from './novelSetup'

interface Props {
  busy: boolean
  onJobStarted: (jobId: string) => void
}

// A novel is translated first; audio and dubbing come after, so this leads Add media for a novel title.
export function NovelFlow({ busy, onJobStarted }: Props) {
  const { dramaId, drama, refetchDrama } = useStage()
  const [error, setError] = useState<unknown>(null)
  const [saving, setSaving] = useState(false)
  const narration = drama.content_mode === 'novel_narration'

  const setUp = () => {
    setSaving(true)
    startAsNovel(dramaId).then(
      () => {
        setError(null)
        refetchDrama()
      },
      setError,
    ).finally(() => setSaving(false))
  }

  return (
    <section className="panel" aria-label="Translate the novel">
      <h3 className="source-subhead">Translate the novel</h3>
      <ol className="muted" data-testid="novel-steps">
        <li>Add the text below: paste it, attach an EPUB, or use the chapters scraped in Sources.</li>
        <li>Split it into lines and tag who speaks.</li>
        <li><a href={`#/drama/${dramaId}/translate`}>Translate the lines</a>.</li>
        <li>Later, if you want audio: <a href={`#/drama/${dramaId}/dub`}>make the narration</a>.</li>
      </ol>
      {narration ? (
        <NarrationPanel dramaId={dramaId} busy={busy} onJobStarted={onJobStarted} />
      ) : (
        <div className="actions">
          <button type="button" className={buttonClass('secondary')} disabled={saving} onClick={setUp}>
            Set up this title for translating text
          </button>
          <span className="muted">Switches the content mode to novel narration so the text can become lines.</span>
        </div>
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </section>
  )
}
