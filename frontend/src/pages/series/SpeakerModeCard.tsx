// The series page's speaker setting: whether speaker detection keeps every voice it finds or only the one creator.
import { useState } from 'react'

import { setSeriesSpeakerMode, type SeriesPageData, type SpeakerMode } from '../../api/series'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'

export function SpeakerModeCard({ data }: { data: SeriesPageData }) {
  const [mode, setMode] = useState<SpeakerMode>(data.speaker_mode)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const change = (next: SpeakerMode) => {
    setBusy(true)
    setError(null)
    setSeriesSpeakerMode(data.id, next).then(
      (r) => { setBusy(false); setMode(r.speaker_mode) },
      (e: unknown) => { setBusy(false); setError(e) },
    )
  }

  return (
    <Card title="Speakers">
      <p className="muted">
        Solo is for one creator talking alone. Speaker detection then keeps one speaker: the voice you enrolled
        for this series is named, and lines in any other voice stay blank. It applies the next time speakers are
        detected or re-assigned; lines already labelled are not changed.
      </p>
      <Field label="Speakers in this series" help={data.can_edit ? undefined : 'You can view this but not change it.'}>
          <select
            value={mode}
            disabled={busy || !data.can_edit}
            data-testid="series-speaker-mode"
            onChange={(e) => change(e.target.value as SpeakerMode)}
          >
            <option value="auto">Auto (keep every voice found)</option>
            <option value="solo">Solo creator (one speaker)</option>
          </select>
      </Field>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </Card>
  )
}
