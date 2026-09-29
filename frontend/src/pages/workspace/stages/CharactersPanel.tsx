import { useEffect, useState } from 'react'

import { applyVoiceBankEntry, getCharacters, getCloneEngines, getVoiceBank, saveCharacter } from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Section } from '../../../components/Section'
import type { CharacterEntry, CloneEngines, VoiceBankEntry } from '../../../types/translateStage'
import { useStage } from '../StageContext'
import { buildCharacterUpdate, isDirty, toCharacterForm, type CharacterForm } from './characterForm'

const COLUMNS = 7

function Row({ entry, engines, bank, onSaved }: {
  entry: CharacterEntry
  engines: CloneEngines | null
  bank: VoiceBankEntry[]
  onSaved: (e: CharacterEntry) => void
}) {
  const { dramaId } = useStage()
  const [form, setForm] = useState<CharacterForm>(() => toCharacterForm(entry))
  const [bankId, setBankId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const set = <K extends keyof CharacterForm>(k: K, v: CharacterForm[K]) => setForm((f) => ({ ...f, [k]: v }))
  const label = entry.speaker_label

  const changes = buildCharacterUpdate(entry, form)
  const dirty = isDirty(changes)

  const done = (e: CharacterEntry) => {
    setBusy(false)
    setError(null)
    setForm(toCharacterForm(e))
    onSaved(e)
  }
  const fail = (e: unknown) => {
    setBusy(false)
    setError(e)
  }

  const save = () => {
    if (changes.character_name === '') return setProblem('A character name cannot be blank.')
    setProblem(null)
    setBusy(true)
    saveCharacter(dramaId, changes).then(done, fail)
  }

  const apply = () => {
    setBusy(true)
    applyVoiceBankEntry(dramaId, label, Number(bankId)).then((e) => {
      setBankId('')
      done(e)
    }, fail)
  }

  const engineIds = engines?.engines.map((e) => e.id) ?? []
  const reference = entry.has_ref_audio
    ? `reference audio set${entry.ref_text_present ? ', transcript set' : ''}`
    : 'no reference audio'

  return (
    <>
      <tr>
        <td>{label}</td>
        <td><input aria-label={`Name for ${label}`} value={form.character_name} onChange={(e) => set('character_name', e.target.value)} /></td>
        <td>
          <select aria-label={`Gender for ${label}`} value={form.pronouns} onChange={(e) => set('pronouns', e.target.value)}>
            {['', 'she/her', 'he/him', 'they/them'].includes(form.pronouns) ? null : <option value={form.pronouns}>{form.pronouns}</option>}
            <option value="">Unspecified</option>
            <option value="she/her">she/her</option>
            <option value="he/him">he/him</option>
            <option value="they/them">they/them</option>
          </select>
        </td>
        <td><input aria-label={`Voice for ${label}`} value={form.tts_voice} onChange={(e) => set('tts_voice', e.target.value)} /></td>
        <td>{entry.line_count}</td>
        <td>{reference}</td>
        <td>
          <button type="button" disabled={!dirty || busy} title={dirty ? undefined : 'No changes to save.'} onClick={save}>Save</button>
          {problem && <p className="error" role="alert">{problem}</p>}
          <ErrorBanner error={error} onDismiss={() => setError(null)} />
        </td>
      </tr>
      <tr>
        <td colSpan={COLUMNS}>
          <details className="voice-details">
            <summary>Voice settings for {label}{form.clone_engine ? ` (${form.clone_engine})` : ''}</summary>
            <div className="voice-grid">
              <Field label="Offline voice" help="Voice used by the offline TTS engine.">
                <input aria-label={`Offline voice for ${label}`} value={form.offline_voice} onChange={(e) => set('offline_voice', e.target.value)} />
              </Field>
              <Field label="Clone engine" help={engines ? `Engines usable for ${engines.source_language || 'this source language'}.` : undefined}>
                <select aria-label={`Clone engine for ${label}`} value={form.clone_engine} onChange={(e) => set('clone_engine', e.target.value)}>
                  <option value="">Default{engines?.default_engine ? ` (${engines.default_engine})` : ''}</option>
                  {form.clone_engine && !engineIds.includes(form.clone_engine) && (
                    <option value={form.clone_engine}>{form.clone_engine}</option>
                  )}
                  {engines?.engines.map((e) => <option key={e.id} value={e.id}>{e.label}</option>)}
                </select>
              </Field>
              <Field label="Voice design" help="Describe the voice for engines that design one from text.">
                <input aria-label={`Voice design for ${label}`} value={form.voice_design} onChange={(e) => set('voice_design', e.target.value)} />
              </Field>
              <div className="voice-wide">
                <Field label="Reference transcript" help="What the reference clip says. Blank leaves the stored text unchanged.">
                  <textarea
                    aria-label={`Reference transcript for ${label}`}
                    rows={2}
                    placeholder={entry.ref_text_present ? 'Set (type to replace)' : ''}
                    value={form.ref_text}
                    onChange={(e) => set('ref_text', e.target.value)}
                  />
                </Field>
              </div>
              {bank.length > 0 && (
                <div className="voice-wide">
                  <Field label="Voice bank" help="Copies the clip, transcript, engine and design to this speaker.">
                    <div className="voice-bank-row">
                      <select aria-label={`Voice bank entry for ${label}`} value={bankId} onChange={(e) => setBankId(e.target.value)}>
                        <option value="">Choose a voice</option>
                        {bank.map((b) => (
                          <option key={b.id} value={b.id}>{b.name}{b.clone_engine ? ` (${b.clone_engine})` : ''}</option>
                        ))}
                      </select>
                      <button type="button" disabled={!bankId || busy} title={bankId ? undefined : 'Choose a voice first.'} onClick={apply}>
                        Apply
                      </button>
                    </div>
                  </Field>
                </div>
              )}
            </div>
          </details>
        </td>
      </tr>
    </>
  )
}

export function CharactersPanel() {
  const { dramaId } = useStage()
  const [entries, setEntries] = useState<CharacterEntry[] | null>(null)
  const [engines, setEngines] = useState<CloneEngines | null>(null)
  const [bank, setBank] = useState<VoiceBankEntry[]>([])
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let cancelled = false
    getCharacters(dramaId).then(
      (c) => !cancelled && setEntries(c),
      (e: unknown) => !cancelled && setError(e),
    )
    // Options are conveniences: a failure leaves free-text/empty pickers.
    getCloneEngines(dramaId).then((c) => !cancelled && setEngines(c), () => undefined)
    getVoiceBank().then((b) => !cancelled && setBank(b), () => undefined)
    return () => {
      cancelled = true
    }
  }, [dramaId])

  return (
    <Section
      storageKey="translate.characters"
      title="Characters"
      count={entries?.length}
      summary={entries ? (entries.length ? 'names, pronouns and voices' : 'no speakers yet') : undefined}
    >
      <div role="region" aria-label="Characters">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {entries && entries.length === 0 && <p className="muted">No speakers yet. They appear after transcription.</p>}
      {entries && entries.length > 0 && (
        <div className="table-scroll"><table>
          <thead>
            <tr><th>Speaker</th><th>Name</th><th>Gender</th><th>Voice</th><th>Lines</th><th>Reference</th><th /></tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <Row
                key={e.speaker_label}
                entry={e}
                engines={engines}
                bank={bank}
                onSaved={(saved) => setEntries((cur) => cur && cur.map((x) => (x.speaker_label === saved.speaker_label ? saved : x)))}
              />
            ))}
          </tbody>
        </table></div>
      )}
      </div>
    </Section>
  )
}
