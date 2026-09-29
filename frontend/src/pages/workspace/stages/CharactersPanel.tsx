import { useEffect, useState } from 'react'

import { getCharacters, saveCharacter } from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Section } from '../../../components/Section'
import type { CharacterEntry, CharacterUpdate } from '../../../types/translateStage'
import { useStage } from '../StageContext'

function Row({ entry, onSaved }: { entry: CharacterEntry; onSaved: (e: CharacterEntry) => void }) {
  const { dramaId } = useStage()
  const [name, setName] = useState(entry.character_name)
  const [pronouns, setPronouns] = useState(entry.pronouns)
  const [voice, setVoice] = useState(entry.tts_voice)
  const [error, setError] = useState<unknown>(null)
  const [problem, setProblem] = useState<string | null>(null)

  // Only changed fields are sent (omitted = leave alone).
  const changes: CharacterUpdate = { speaker_label: entry.speaker_label }
  if (name !== entry.character_name) changes.character_name = name.trim()
  if (pronouns !== entry.pronouns) changes.pronouns = pronouns
  if (voice !== entry.tts_voice) changes.tts_voice = voice
  const dirty = Object.keys(changes).length > 1

  const save = () => {
    if (changes.character_name === '') return setProblem('A character name cannot be blank.')
    setProblem(null)
    saveCharacter(dramaId, changes).then((e) => {
      setError(null)
      onSaved(e)
    }, setError)
  }

  return (
    <tr>
      <td>{entry.speaker_label}</td>
      <td><input aria-label={`Name for ${entry.speaker_label}`} value={name} onChange={(e) => setName(e.target.value)} /></td>
      <td>
        <select aria-label={`Gender for ${entry.speaker_label}`} value={pronouns} onChange={(e) => setPronouns(e.target.value)}>
          {['', 'she/her', 'he/him', 'they/them'].includes(pronouns) ? null : <option value={pronouns}>{pronouns}</option>}
          <option value="">Unspecified</option>
          <option value="she/her">she/her</option>
          <option value="he/him">he/him</option>
          <option value="they/them">they/them</option>
        </select>
      </td>
      <td><input aria-label={`Voice for ${entry.speaker_label}`} value={voice} onChange={(e) => setVoice(e.target.value)} /></td>
      <td>{entry.line_count}</td>
      <td>{entry.has_ref_audio ? 'reference audio set' : 'no reference audio'}</td>
      <td>
        <button type="button" disabled={!dirty} onClick={save}>Save</button>
        {problem && <p className="error" role="alert">{problem}</p>}
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
      </td>
    </tr>
  )
}

export function CharactersPanel() {
  const { dramaId } = useStage()
  const [entries, setEntries] = useState<CharacterEntry[] | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let cancelled = false
    getCharacters(dramaId).then(
      (c) => !cancelled && setEntries(c),
      (e: unknown) => !cancelled && setError(e),
    )
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
