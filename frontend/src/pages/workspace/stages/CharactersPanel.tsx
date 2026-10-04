import { useEffect, useState } from 'react'

import { mergeSpeakers, rememberSeriesCharacter, renameSpeaker, undoMergeSpeakers, undoRenameSpeaker } from '../../../api/characters'
import { applyVoiceBankEntry, getCharacters, getCloneEngines, getVoiceBank, saveCharacter } from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { buttonClass } from '../../../components/uiClasses'
import { Section } from '../../../components/Section'
import { VoiceBankPlayButton } from '../../../components/VoiceBankPlayButton'
import type { MergeResult, MergeUndo, RememberResult, RenameResult, RenameUndo } from '../../../types/characters'
import type { CharacterEntry, CloneEngines, VoiceBankEntry } from '../../../types/translateStage'
import { useStage } from '../StageContext'
import { SeriesCast } from './SeriesCast'
import { VoiceSuggestions } from './VoiceSuggestions'
import {
  CUSTOM,
  MAX_PRONOUNS_LEN,
  PRONOUN_PRESETS,
  buildCharacterUpdate,
  canRemember,
  isDirty,
  sampleCaption,
  toCharacterForm,
  unsetPronounsLabel,
  type CharacterForm,
} from './characterForm'
import { mergeChoices, mergeSummary, type MergeChoice } from './mergeSpeakers'
import { readRenameUndo, renameProblem, saveRenameUndo, takenNames } from './renameSpeaker'
import './characters.css'

const COLUMNS = 7

function Row({ entry, engines, bank, hasSeries, taken, mergeTargets, onSaved, onRemembered, onRenamed, onMerged }: {
  entry: CharacterEntry
  engines: CloneEngines | null
  bank: VoiceBankEntry[]
  hasSeries: boolean
  taken: string[]
  mergeTargets: MergeChoice[]
  onSaved: (e: CharacterEntry) => void
  onRemembered: (r: RememberResult) => void
  onRenamed: (r: RenameResult) => void
  onMerged: (r: MergeResult, sourceLabel: string) => void
}) {
  const { dramaId } = useStage()
  const [form, setForm] = useState<CharacterForm>(() => toCharacterForm(entry))
  // A change from outside the row (an accepted voice suggestion) resets
  // the form to the saved values.
  const [shown, setShown] = useState(entry)
  if (shown !== entry) {
    setShown(entry)
    setForm(toCharacterForm(entry))
  }
  const [bankId, setBankId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [renaming, setRenaming] = useState<string | null>(null)
  // '' is the form open with no speaker picked yet.
  const [mergeInto, setMergeInto] = useState<string | null>(null)
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

  const remember = () => {
    setBusy(true)
    rememberSeriesCharacter(dramaId, label).then((r) => {
      done(r.character)
      onRemembered(r)
    }, fail)
  }

  const renameProblemText = renaming === null ? null : renameProblem(label, renaming, taken)
  const rename = () => {
    if (renaming === null || renameProblemText) return
    setBusy(true)
    renameSpeaker(dramaId, label, renaming.trim()).then((r) => {
      setBusy(false)
      onRenamed(r)
    }, fail)
  }

  const merge = () => {
    if (!mergeInto) return
    setBusy(true)
    mergeSpeakers(dramaId, label, mergeInto).then((r) => {
      setBusy(false)
      onMerged(r, label)
    }, fail)
  }

  const samples = sampleCaption(entry)
  const nameEdited = form.character_name !== entry.character_name
  const engineIds = engines?.engines.map((e) => e.id) ?? []
  const reference = entry.has_ref_audio
    ? `Reference audio set${entry.ref_text_present ? ', transcript set' : ''}`
    : 'No reference audio'

  return (
    <>
      <tr role="row" className="card-main">
        <td role="cell" data-label="Speaker" className="card-title">{label}</td>
        <td role="cell" data-label="Name" className="card-wide"><input aria-label={`Name for ${label}`} value={form.character_name} onChange={(e) => set('character_name', e.target.value)} /></td>
        <td role="cell" data-label="Gender" className="card-half">
          <div className="character-pronouns">
            <select aria-label={`Gender for ${label}`} value={form.pronoun_choice} onChange={(e) => set('pronoun_choice', e.target.value)}>
              <option value="">{unsetPronounsLabel(entry)}</option>
              {PRONOUN_PRESETS.map((p) => <option key={p} value={p}>{p}</option>)}
              <option value={CUSTOM}>Custom…</option>
            </select>
            {form.pronoun_choice === CUSTOM && (
              <input
                aria-label={`Custom pronouns for ${label}`}
                value={form.custom_pronouns}
                maxLength={MAX_PRONOUNS_LEN}
                placeholder="e.g. xe/xem"
                onChange={(e) => set('custom_pronouns', e.target.value)}
              />
            )}
          </div>
        </td>
        <td role="cell" data-label="Voice" className="card-half"><input aria-label={`Voice for ${label}`} value={form.tts_voice} onChange={(e) => set('tts_voice', e.target.value)} /></td>
        <td role="cell" data-label="Lines" className="card-meta">{entry.line_count}</td>
        <td role="cell" data-label="Reference" className="card-meta">{reference}</td>
        <td role="cell" className="card-action">
          <button type="button" className={buttonClass('secondary', 'sm')} disabled={!dirty || busy} title={dirty ? undefined : 'No changes to save.'} onClick={save}>Save</button>
          {problem && <p className="error" role="alert">{problem}</p>}
          <ErrorBanner error={error} onDismiss={() => setError(null)} />
        </td>
      </tr>
      <tr role="row" className="card-extra">
        <td role="cell" colSpan={COLUMNS}>
          {(samples || entry.series_character_id || canRemember(entry, hasSeries)) && (
            <div className="character-extras" data-testid={`character-extras-${label}`}>
              {samples && <p className="muted character-samples">{samples}</p>}
              {entry.series_character_id ? (
                <p className="muted character-shared">
                  Shared with other dramas in this series: pronoun and voice defaults can come from there.
                </p>
              ) : null}
              {canRemember(entry, hasSeries) && (
                <button
                  type="button"
                  className={buttonClass('ghost', 'sm')}
                  disabled={busy || nameEdited}
                  title={nameEdited ? 'Save the name first.' : 'Adds this name to the series cast so later dramas can pick it.'}
                  onClick={remember}
                >
                  Remember {entry.character_name.trim()} in this series
                </button>
              )}
            </div>
          )}
          <div className="character-extras">
            {renaming === null ? (
              <button type="button" disabled={busy || Boolean(entry.series_character_id)}
                title={entry.series_character_id
                  ? 'This speaker is linked to a series character; unlink it first or rename the series character.'
                  : 'Gives this speaker a name on every one of its lines and in translation.'}
                onClick={() => setRenaming(entry.character_name || '')}>
                Rename speaker
              </button>
            ) : (
              <form className="character-rename" onSubmit={(e) => { e.preventDefault(); rename() }}>
                <input aria-label={`New name for ${label}`} value={renaming} autoFocus
                  onChange={(e) => setRenaming(e.target.value)} />
                <button type="submit" disabled={busy || renameProblemText !== null} title={renameProblemText ?? undefined}>
                  {entry.line_count ? `Rename on all ${entry.line_count} lines` : 'Rename'}
                </button>
                <button type="button" disabled={busy} onClick={() => setRenaming(null)}>Cancel</button>
              </form>
            )}
            {mergeInto === null ? (
              <button type="button" disabled={busy || mergeTargets.length === 0}
                title={mergeTargets.length === 0 ? 'There is no other speaker to merge into.'
                  : 'Moves this speaker\'s lines to another speaker, for when one person got two labels.'}
                onClick={() => setMergeInto('')}>
                Merge into…
              </button>
            ) : (
              <form className="character-merge" onSubmit={(e) => { e.preventDefault(); merge() }}>
                <select aria-label={`Merge ${label} into`} value={mergeInto} autoFocus
                  onChange={(e) => setMergeInto(e.target.value)}>
                  <option value="">Choose a speaker</option>
                  {mergeTargets.map((t) => (
                    <option key={t.label} value={t.label} disabled={t.blocked !== null}>
                      {t.text}{t.blocked ? ` (${t.blocked})` : ''}
                    </option>
                  ))}
                </select>
                {mergeInto && <p className="character-merge-confirm">{mergeSummary(entry, mergeInto)}</p>}
                <button type="submit" disabled={busy || !mergeInto}>
                  {mergeInto ? `Merge ${entry.line_count} ${entry.line_count === 1 ? 'line' : 'lines'}` : 'Merge'}
                </button>
                <button type="button" disabled={busy} onClick={() => setMergeInto(null)}>Cancel</button>
              </form>
            )}
          </div>
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
                      {bankId && (
                        <VoiceBankPlayButton key={bankId} entryId={Number(bankId)} name={bank.find((b) => String(b.id) === bankId)?.name ?? 'voice'} />
                      )}
                      <button type="button" className={buttonClass('secondary', 'sm')} disabled={!bankId || busy} title={bankId ? undefined : 'Choose a voice first.'} onClick={apply}>
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
  const { dramaId, drama } = useStage()
  const [entries, setEntries] = useState<CharacterEntry[] | null>(null)
  const [engines, setEngines] = useState<CloneEngines | null>(null)
  const [bank, setBank] = useState<VoiceBankEntry[]>([])
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  // Bumped to re-read the voice suggestions (a speaker changed) and the
  // series cast (someone was remembered).
  const [suggestRefresh, setSuggestRefresh] = useState(0)
  const [castRefresh, setCastRefresh] = useState(0)
  const [undo, setUndo] = useState<RenameUndo | null>(() => readRenameUndo(dramaId))
  const [undoBusy, setUndoBusy] = useState(false)
  // Held in the page only: it carries both Characters rows as they were.
  const [mergeUndo, setMergeUndo] = useState<MergeUndo | null>(null)

  // Replace by speaker label, never by position.
  const replace = (saved: CharacterEntry) =>
    setEntries((cur) => cur && cur.map((x) => (x.speaker_label === saved.speaker_label ? saved : x)))
  const remembered = (r: RememberResult) => {
    setNotice(r.created
      ? `Added ${r.series_character.character_name} to the series cast.`
      : `Linked ${r.character.speaker_label} to ${r.series_character.character_name} in the series cast.`)
    setCastRefresh((n) => n + 1)
  }

  const renamed = (r: RenameResult) => {
    setEntries(r.characters)
    setUndo(r.undo)
    saveRenameUndo(dramaId, r.undo)
    setNotice(r.undo ? `Renamed ${r.undo.previous_label} to ${r.undo.speaker_label} on ${r.renamed} lines.` : null)
    setSuggestRefresh((n) => n + 1)
  }
  const merged = (r: MergeResult, sourceLabel: string) => {
    setEntries(r.characters)
    setMergeUndo(r.undo)
    // A rename's undo names labels that may be gone now.
    setUndo(null)
    saveRenameUndo(dramaId, null)
    setNotice(r.undo ? `Merged ${sourceLabel} into ${r.undo.target_label}: ${r.moved} ${r.moved === 1 ? 'line' : 'lines'} moved.` : null)
    setSuggestRefresh((n) => n + 1)
  }
  const runUndoMerge = () => {
    if (!mergeUndo) return
    setUndoBusy(true)
    undoMergeSpeakers(dramaId, mergeUndo).then(
      (r) => {
        setUndoBusy(false)
        setEntries(r.characters)
        setNotice(`Put ${mergeUndo.source_label} back on ${r.moved} ${r.moved === 1 ? 'line' : 'lines'}.`)
        setMergeUndo(null)
        setSuggestRefresh((n) => n + 1)
      },
      (e: unknown) => {
        setUndoBusy(false)
        setError(e)
        // Refused (lines edited since, label reused): retrying won't work.
        setMergeUndo(null)
      },
    )
  }
  const runUndo = () => {
    if (!undo) return
    setUndoBusy(true)
    undoRenameSpeaker(dramaId, undo).then(
      (r) => {
        setUndoBusy(false)
        setEntries(r.characters)
        setUndo(null)
        saveRenameUndo(dramaId, null)
        setNotice(`Put ${undo.previous_label} back on ${r.renamed} lines.`)
        setSuggestRefresh((n) => n + 1)
      },
      (e: unknown) => {
        setUndoBusy(false)
        setError(e)
        // A refused undo (renamed again, label reused) won't work later either.
        setUndo(null)
        saveRenameUndo(dramaId, null)
      },
    )
  }

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
      {entries && entries.length > 0 && drama.series_id ? (
        <VoiceSuggestions dramaId={dramaId} refresh={suggestRefresh} onAccepted={replace} />
      ) : null}
      {entries && entries.length > 0 && (
        <div className="table-scroll"><table role="table" className="card-table">
          <thead>
            <tr role="row"><th role="columnheader">Speaker</th><th role="columnheader">Name</th><th role="columnheader">Gender</th><th role="columnheader">Voice</th><th role="columnheader">Lines</th><th role="columnheader">Reference</th><th role="columnheader"><span className="visually-hidden">Save</span></th></tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <Row
                key={e.speaker_label}
                entry={e}
                engines={engines}
                bank={bank}
                hasSeries={Boolean(drama.series_id)}
                taken={takenNames(entries, e.speaker_label)}
                mergeTargets={mergeChoices(entries, e)}
                onSaved={(saved) => {
                  replace(saved)
                  setSuggestRefresh((n) => n + 1)
                }}
                onRemembered={remembered}
                onRenamed={renamed}
                onMerged={merged}
              />
            ))}
          </tbody>
        </table></div>
      )}
      {(notice || undo || mergeUndo) && (
        <p role="status" className="character-notice">
          {notice}
          {undo && <button type="button" disabled={undoBusy} onClick={runUndo}>Undo rename</button>}
          {mergeUndo && <button type="button" disabled={undoBusy} onClick={runUndoMerge}>Undo merge</button>}
        </p>
      )}
      {drama.series_id ? <SeriesCast key={drama.series_id} seriesId={drama.series_id} refresh={castRefresh} /> : null}
      </div>
    </Section>
  )
}
