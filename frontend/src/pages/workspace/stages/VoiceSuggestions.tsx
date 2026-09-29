import { useEffect, useState } from 'react'

import { answerVoiceSuggestion, getVoiceSuggestions } from '../../../api/characters'
import { ErrorBanner } from '../../../components/ErrorBanner'
import type { VoiceSuggestion } from '../../../types/characters'
import type { CharacterEntry } from '../../../types/translateStage'

const keyOf = (s: VoiceSuggestion) => `${s.speaker_label}\u0000${s.series_character_id}`

// Characters → "sounds like X" (inventory C02): experimental matches of a
// speaker's voice against the series cast. Nothing changes until Accept;
// Reject only hides that pair for this drama. Renders nothing when there
// are no suggestions (no series, no stored voice embeddings, no match).
export function VoiceSuggestions({ dramaId, refresh, onAccepted }: {
  dramaId: number
  /** Bumped by the panel after a speaker changes, to re-ask the server. */
  refresh: number
  onAccepted: (e: CharacterEntry) => void
}) {
  const [items, setItems] = useState<VoiceSuggestion[]>([])
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let cancelled = false
    // A convenience: a failed read just shows no suggestions.
    getVoiceSuggestions(dramaId).then((s) => !cancelled && setItems(s), () => undefined)
    return () => {
      cancelled = true
    }
  }, [dramaId, refresh])

  const answer = (s: VoiceSuggestion, action: 'accept' | 'reject') => {
    setBusy(keyOf(s))
    setError(null)
    answerVoiceSuggestion(dramaId, action, s).then(
      (r) => {
        setBusy(null)
        setItems(r.suggestions)
        if (r.character) onAccepted(r.character)
      },
      (e: unknown) => {
        setBusy(null)
        setError(e)
      },
    )
  }

  if (items.length === 0 && !error) return null
  return (
    <div className="voice-suggestions" role="region" aria-label="Voice suggestions">
      <p className="muted">Experimental: these speakers sound like people in the series cast. Nothing changes until you accept.</p>
      <ul>
        {items.map((s) => (
          <li key={keyOf(s)}>
            <span>
              <strong>{s.speaker_label}</strong> sounds like <strong>{s.character_name}</strong>
              <span className="muted"> · similarity {s.similarity.toFixed(2)}</span>
            </span>
            <span className="actions">
              <button
                type="button"
                disabled={busy !== null}
                aria-label={`Accept ${s.character_name} for ${s.speaker_label}`}
                onClick={() => answer(s, 'accept')}
              >
                Accept
              </button>
              <button
                type="button"
                disabled={busy !== null}
                aria-label={`Reject ${s.character_name} for ${s.speaker_label}`}
                onClick={() => answer(s, 'reject')}
              >
                Reject
              </button>
            </span>
          </li>
        ))}
      </ul>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </div>
  )
}
