import { useState } from 'react'

import { clearVoice, getVoices, removeVoiceSample, retrainVoice } from '../../api/voices'
import { Badge } from '../../components/Badge'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { useLoad } from '../../hooks/useLoad'
import type { PcMode } from '../../hooks/usePcOnly'
import type { VoiceEntry } from '../../types/voices'
import { TabCard } from './TabCard'
import { LOW_MATCH, retrainNote, similarityText, voiceSummary } from './voicesModel'

const NOTE = 'A voice print is the average of the speakers you confirmed as one person. Speaker detection offers a match '
  + 'to a named person once a print exists. Clearing or retraining keeps the person; they only change the print.'

function VoiceRow({ voice, canChange, onChanged }: { voice: VoiceEntry; canChange: boolean; onChanged: () => void }) {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const run = (key: string, action: () => Promise<unknown>) => {
    setBusy(key)
    setError(null)
    action().then(
      () => { setBusy(null); onChanged() },
      (e: unknown) => { setBusy(null); setError(e) },
    )
  }
  const { series_id: sid, character_id: cid, character_name: name } = voice
  return (
    <li className="voice-row">
      <div className="voice-head">
        <strong>{name}</strong> <span className="muted">in {voice.series_name}</span>
        <span className="muted voice-summary" data-testid="voice-summary">{voiceSummary(voice)}</span>
      </div>
      <ul className="tool-list" aria-label={`Speakers linked to ${name}`}>
        {voice.linked.map((s) => {
          const key = `${s.drama_id}/${s.speaker_label}`
          const low = s.similarity != null && s.similarity < LOW_MATCH
          return (
            <li key={key} className="voice-sample">
              <span className="tool-row">
                <span>{s.title} <span className="muted">· {s.speaker_label}</span></span>
                <span className="row-actions">
                  <span className="muted num">{similarityText(s.similarity)}</span>
                  {low && <Badge>Low match</Badge>}
                  {canChange && (
                    <ConfirmButton
                      size="sm"
                      name={`${s.speaker_label} in ${s.title}`}
                      label="Remove…"
                      verb="remove"
                      ariaLabel={`Remove ${s.speaker_label} in ${s.title} from ${name}'s voice`}
                      busy={busy === key}
                      onConfirm={() => run(key, () => removeVoiceSample(sid, cid, s.drama_id, s.speaker_label))}
                    />
                  )}
                </span>
              </span>
            </li>
          )
        })}
        {voice.linked.length === 0 && <li className="muted">No speaker is linked to {name} now.</li>}
      </ul>
      {canChange && (
        <>
          <div className="actions">
            <ConfirmButton
              size="sm" name={`${name}'s voice print`} label="Retrain…" verb="retrain" tone="primary"
              ariaLabel={`Retrain ${name}'s voice print`} disabled={voice.rebuildable === 0}
              describedBy={`retrain-note-${cid}`} busy={busy === 'retrain'}
              onConfirm={() => run('retrain', () => retrainVoice(sid, cid))}
            />
            <ConfirmButton
              size="sm" name={`${name}'s voice print`} label="Clear print…" verb="clear"
              ariaLabel={`Clear ${name}'s voice print`} busy={busy === 'clear'}
              onConfirm={() => run('clear', () => clearVoice(sid, cid))}
            />
          </div>
          <p className="muted" id={`retrain-note-${cid}`}>{retrainNote(voice)} Remove unlinks that speaker (it keeps its name) and rebuilds the print without it.</p>
        </>
      )}
      <ErrorBanner error={error} describe={{ pcOnly: true }} onDismiss={() => setError(null)} />
    </li>
  )
}

/** Every enrolled voice across series: how many samples built it, how well each linked speaker matches, and clear, retrain or remove. */
export function VoicesTab({ pc }: { pc: PcMode }) {
  const [reloadKey, setReloadKey] = useState(0)
  const voices = useLoad(getVoices, reloadKey)
  const items = voices.data?.items
  return (
    <TabCard card="enrolled-voices" count={items?.length} summary={NOTE}>
      <ErrorBanner error={voices.error} />
      {!voices.data && !voices.error && <p className="muted">Loading…</p>}
      {items && items.length === 0 && (
        <p className="muted" data-testid="voices-empty">
          No enrolled voices yet. In a title's Characters step, name a speaker and choose Remember as a known character, or accept a suggested match.
        </p>
      )}
      <ul className="tool-list voice-list">
        {items?.map((v) => (
          <VoiceRow key={`${v.series_id}/${v.character_id}`} voice={v} canChange={pc !== 'remote'} onChanged={() => setReloadKey((k) => k + 1)} />
        ))}
      </ul>
      {pc === 'remote' && !!items?.length && <p className="muted">Changing a voice print is done at the PC.</p>}
    </TabCard>
  )
}
