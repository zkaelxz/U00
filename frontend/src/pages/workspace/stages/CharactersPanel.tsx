import { useEffect, useState } from 'react'

import { mergeSpeakers, rememberSeriesCharacter, renameSpeaker, swapSpeakers, undoMergeSpeakers, undoRenameSpeaker, undoSpeakerChange } from '../../../api/characters'
import { applyVoiceBankEntry, getCharacters, getCloneEngines, getVoiceBank, saveCharacter } from '../../../api/translateStage'
import { voiceBankAudioUrl } from '../../../api/voiceBankAudio'
import { candidateAudioUrl, extractCandidates, listCandidates } from '../../../api/voiceClone'
import { ClipPlayButton } from '../../../components/ClipPlayButton'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { isForbidden, LINES_READ_FORBIDDEN, safeDetail } from '../../../components/errorMessages'
import { Field } from '../../../components/Field'
import { buttonClass } from '../../../components/uiClasses'
import { Section } from '../../../components/Section'
import { useCancellableEffect } from '../../../hooks/useCancellableEffect'
import { useJob, useJobRun } from '../../../hooks/useJob'
import { useReattachJob } from '../../../hooks/useReattachJob'
import { isTerminalStatus, jobFailed } from '../../../types/jobs'
import type { MergeResult, RememberResult, RenameResult, RenameUndo, SpeakerChangeEntry, SpeakerChangeResult } from '../../../types/characters'
import type { CharacterEntry, CloneEngines, VoiceBankEntry } from '../../../types/translateStage'
import type { VoiceCloneCandidates, VoiceCloneSpeakerCandidates } from '../../../types/voiceClone'
import { useStage, useStageFocus } from '../StageContext'
import { voiceCloneJobId } from '../stageJobIds'
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
import { VOICE_CLIP_NOTE, leavesVoiceClip, mergeChoices, mergeSummary, readMergeUndo, saveMergeUndo, undoIdSurvives, type MergeChoice, type MergeUndoHandle } from './mergeSpeakers'
import { readRenameUndo, renameProblem, saveRenameUndo, takenNames } from './renameSpeaker'
import { candidatesFor, formatSeconds, skipText } from './voiceClone'
import './characters.css'

const formKey = (e: CharacterEntry) => JSON.stringify(toCharacterForm(e))

// Sample clips are the same candidates Review > Voices finds for a reference
// clip, so finding them here also fills that list (and replaces what it had
// for this speaker).
interface SampleProps {
  candidates: VoiceCloneSpeakerCandidates | null
  // One extraction runs per title at a time, whichever speaker it is for.
  extracting: boolean
  finding: boolean
  // Why the last search for this speaker failed, in words.
  failed: string | null
  error: unknown
  onFind: () => void
  onDismiss: () => void
}

function SampleCell({ label, sample }: { label: string; sample: SampleProps }) {
  const { dramaId } = useStage()
  const clips = sample.candidates?.candidates ?? []
  return (
    <td role="cell" data-label="Sample" className="card-meta character-sample">
      {clips.length > 0 ? (
        <span className="character-sample-clips" role="group" aria-label={`Sample clips for ${label}`}>
          {clips.map((c, i) => (
            <ClipPlayButton
              key={c.id}
              src={candidateAudioUrl(dramaId, c.id)}
              name={`sample ${i + 1} for ${label}`}
              text={String(i + 1)}
              title={`${formatSeconds(c.duration)}${c.ref_text ? ` · ${c.ref_text}` : ''}`}
            />
          ))}
        </span>
      ) : (
        <button
          type="button"
          className={buttonClass('ghost', 'sm')}
          aria-label={`Find sample clips for ${label}`}
          disabled={sample.extracting}
          title={sample.extracting && !sample.finding
            ? 'Another speaker\'s clips are being found; wait for it to finish.'
            : 'Cuts a few short clips of this speaker from the audio, so you can hear whose voice it is.'}
          onClick={sample.onFind}
        >
          {sample.finding ? 'Finding…' : 'Find clips'}
        </button>
      )}
    </td>
  )
}

function SampleNotes({ label, sample }: { label: string; sample: SampleProps }) {
  const skip = skipText(sample.candidates)
  if (!skip && !sample.failed && !sample.error) return null
  return (
    <div className="character-extras" data-testid={`sample-notes-${label}`}>
      {skip && <p className="muted character-samples">{skip}</p>}
      {sample.failed && <p className="error" role="alert">{sample.failed}</p>}
      <ErrorBanner error={sample.error} describe={{ serverText: true }} onDismiss={sample.onDismiss} />
    </div>
  )
}

function Row({ entry, engines, bank, hasSeries, taken, mergeTargets, leavesClip, sample, columns, onSaved, onRemembered, onRenamed, onMerged, onSwapped }: {
  entry: CharacterEntry
  engines: CloneEngines | null
  bank: VoiceBankEntry[]
  hasSeries: boolean
  taken: string[]
  mergeTargets: MergeChoice[]
  leavesClip: (target: string) => boolean
  // Null when the title has no audio to cut a sample from.
  sample: SampleProps | null
  columns: number
  onSaved: (e: CharacterEntry) => void
  onRemembered: (r: RememberResult) => void
  onRenamed: (r: RenameResult) => void
  onMerged: (r: MergeResult, sourceLabel: string, targetLabel: string) => void
  onSwapped: (r: SpeakerChangeResult, first: string, second: string) => void
}) {
  const { dramaId } = useStage()
  const [form, setForm] = useState<CharacterForm>(() => toCharacterForm(entry))
  // A change from outside the row (an accepted voice suggestion) resets
  // the form to the saved values. Rename, merge and undo hand every row a
  // new object, so only a change to what the form shows resets it; otherwise
  // typing in another row would be wiped.
  const [shown, setShown] = useState(entry)
  if (shown !== entry) {
    setShown(entry)
    if (formKey(shown) !== formKey(entry)) setForm(toCharacterForm(entry))
  }
  const [bankId, setBankId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [problem, setProblem] = useState<string | null>(null)
  const [renaming, setRenaming] = useState<string | null>(null)
  // '' is the form open with no speaker picked yet.
  const [mergeInto, setMergeInto] = useState<string | null>(null)
  const [swapWith, setSwapWith] = useState<string | null>(null)
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
      onMerged(r, label, mergeInto)
    }, fail)
  }

  const swap = () => {
    if (!swapWith) return
    setBusy(true)
    swapSpeakers(dramaId, label, swapWith).then((r) => {
      setBusy(false)
      setSwapWith(null)
      onSwapped(r, label, swapWith)
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
        <td role="cell" data-label="Lines" className="card-meta">{entry.line_count}</td>
        {sample && <SampleCell label={label} sample={sample} />}
        <td role="cell" data-label="Reference" className="card-meta">{reference}</td>
        <td role="cell" className="card-action">
          <button type="button" className={buttonClass('secondary', 'sm')} disabled={!dirty || busy} title={dirty ? undefined : 'No changes to save.'} onClick={save}>Save</button>
          {problem && <p className="error" role="alert">{problem}</p>}
          <ErrorBanner error={error} onDismiss={() => setError(null)} />
        </td>
      </tr>
      <tr role="row" className="card-extra">
        <td role="cell" colSpan={columns}>
          {sample && <SampleNotes label={label} sample={sample} />}
          {(samples || entry.series_character_id || canRemember(entry, hasSeries)) && (
            <div className="character-extras" data-testid={`character-extras-${label}`}>
              {samples && <p className="muted character-samples">{samples}</p>}
              {entry.series_character_id ? (
                <p className="muted character-shared">
                  Shared with other titles in this series: pronoun and voice defaults can come from there.
                </p>
              ) : null}
              {canRemember(entry, hasSeries) && (
                <button
                  type="button"
                  className={buttonClass('ghost', 'sm')}
                  disabled={busy || nameEdited}
                  title={nameEdited ? 'Save the name first.' : 'Adds this name to the series cast so later titles can pick it.'}
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
                <button type="button" className={buttonClass('secondary')} disabled={busy} onClick={() => setRenaming(null)}>Cancel</button>
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
                {mergeInto && <p className="character-merge-confirm">{mergeSummary(entry, mergeInto)}{leavesClip(mergeInto) && ` ${VOICE_CLIP_NOTE}`}</p>}
                <button type="submit" disabled={busy || !mergeInto}>
                  {mergeInto ? `Merge ${entry.line_count} ${entry.line_count === 1 ? 'line' : 'lines'}` : 'Merge'}
                </button>
                <button type="button" className={buttonClass('secondary')} disabled={busy} onClick={() => setMergeInto(null)}>Cancel</button>
              </form>
            )}
            {swapWith === null ? (
              <button type="button" disabled={busy || mergeTargets.length === 0}
                title={mergeTargets.length === 0 ? 'There is no other speaker to swap with.'
                  : 'Trades this speaker\'s lines with another speaker\'s, for when two labels are the wrong way round.'}
                onClick={() => setSwapWith('')}>
                Swap speakers…
              </button>
            ) : (
              <form className="character-merge" onSubmit={(e) => { e.preventDefault(); swap() }}>
                <select aria-label={`Swap ${label} with`} value={swapWith} autoFocus
                  onChange={(e) => setSwapWith(e.target.value)}>
                  <option value="">Choose a speaker</option>
                  {mergeTargets.map((t) => <option key={t.label} value={t.label}>{t.text}</option>)}
                </select>
                <button type="submit" disabled={busy || !swapWith}>Swap</button>
                <button type="button" className={buttonClass('secondary')} disabled={busy} onClick={() => setSwapWith(null)}>Cancel</button>
              </form>
            )}
          </div>
          <details className="voice-details">
            <summary>Voice settings for {label}{form.clone_engine ? ` (${form.clone_engine})` : ''}</summary>
            <div className="voice-grid">
              {entry.clone_engine_removed && (
                <>
                  <Field label="Clone engine" help="Pick OmniVoice, or Default, to use this character again.">
                    <select aria-label={`Clone engine for ${label}`} value={form.clone_engine} onChange={(e) => set('clone_engine', e.target.value)}>
                      <option value="">Default{engines?.default_engine ? ` (${engines.default_engine})` : ''}</option>
                      {form.clone_engine && !engineIds.includes(form.clone_engine) && (
                        <option value={form.clone_engine}>{`${form.clone_engine} (removed)`}</option>
                      )}
                      {engines?.engines.map((e) => <option key={e.id} value={e.id}>{e.label}</option>)}
                    </select>
                  </Field>
                  <p className="voice-warning voice-wide" role="note" data-testid={`engine-removed-${label}`}>
                    {entry.clone_engine_removed}
                  </p>
                </>
              )}
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
                        <ClipPlayButton key={bankId} src={voiceBankAudioUrl(Number(bankId))} name={bank.find((b) => String(b.id) === bankId)?.name ?? 'voice'} />
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

export function CharactersPanel({ focusReady, defaultOpen }: { focusReady?: boolean; defaultOpen?: boolean }) {
  const { dramaId, drama } = useStage()
  const focusSignal = useStageFocus('characters', 'translate-characters', focusReady)
  const [entries, setEntries] = useState<CharacterEntry[] | null>(null)
  const [engines, setEngines] = useState<CloneEngines | null>(null)
  const [bank, setBank] = useState<VoiceBankEntry[]>([])
  const [error, setError] = useState<unknown>(null)
  const [loadForbidden, setLoadForbidden] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  // Bumped to re-read the voice suggestions (a speaker changed) and the
  // series cast (someone was remembered).
  const [suggestRefresh, setSuggestRefresh] = useState(0)
  const [castRefresh, setCastRefresh] = useState(0)
  const [undo, setUndo] = useState<RenameUndo | null>(() => readRenameUndo(dramaId))
  const [undoBusy, setUndoBusy] = useState(false)
  // Kept in memory only: a reload ends it, since the lines it names may have changed.
  const [swapUndo, setSwapUndo] = useState<SpeakerChangeEntry[] | null>(null)
  // Only the server's opaque undo id and the two labels; the rows stay on the server.
  const [mergeUndo, setMergeUndo] = useState<MergeUndoHandle | null>(() => readMergeUndo(dramaId))
  const forgetMergeUndo = () => {
    setMergeUndo(null)
    saveMergeUndo(dramaId, null)
  }
  const [cands, setCands] = useState<VoiceCloneCandidates | null>(null)
  const [candReload, setCandReload] = useState(0)
  // A clip goes with the line it was cut from, so a rename, merge or swap
  // (or its undo) can move clips between speakers.
  const reloadSamples = () => setCandReload((n) => n + 1)
  const [findFor, setFindFor] = useState<string | null>(null)
  // The start request is in flight: a second press would only be refused.
  const [findStarting, setFindStarting] = useState(false)
  const [findError, setFindError] = useState<{ label: string; error: unknown } | null>(null)
  const [findJobId, setFindJobId, findRunKey, adoptFindJob] = useJobRun()
  // Picks up a search still running from an earlier visit (or from Review >
  // Voices), so the buttons stay disabled and the clips appear when it ends.
  useReattachJob(drama.has_audio ? [voiceCloneJobId(dramaId)] : [], adoptFindJob)
  const { job: findJob, error: findPollError } = useJob(findJobId, {
    runKey: findRunKey,
    onDone: () => setCandReload((n) => n + 1),
  })
  const finding = findStarting || (findJobId !== null && !findPollError && !(findJob && isTerminalStatus(findJob.status)))
  const findFailed = findPollError
    ? "Couldn't follow the search for clips; reload the page to see what it found."
    : findJob && jobFailed(findJob)
      ? `Couldn't find clips: ${(findJob.error && safeDetail(findJob.error)) || 'the search failed.'}`
      : null
  const startFind = (label: string) => {
    setFindError(null)
    setFindFor(label)
    // Drops the last run, so its failure doesn't show on this row meanwhile.
    setFindJobId(null)
    setFindStarting(true)
    extractCandidates(dramaId, label).then((r) => {
      setFindStarting(false)
      setFindJobId(r.job_id)
    }, (error: unknown) => {
      setFindStarting(false)
      setFindFor(null)
      setFindError({ label, error })
    })
  }
  useCancellableEffect((cancelled) => {
    if (!drama.has_audio) return
    // A convenience: without it the rows offer Find clips.
    listCandidates(dramaId).then((c) => !cancelled() && setCands(c), () => undefined)
  }, [dramaId, drama.has_audio, candReload])
  const sampleFor = (label: string): SampleProps | null => drama.has_audio
    ? {
        candidates: candidatesFor(cands, label),
        extracting: finding,
        finding: finding && findFor === label,
        failed: findFor === label ? findFailed : null,
        error: findError?.label === label ? findError.error : null,
        onFind: () => startFind(label),
        onDismiss: () => setFindError(null),
      }
    : null
  const columns = drama.has_audio ? 7 : 6

  // Replace by speaker label, never by position.
  const replace = (saved: CharacterEntry) => {
    setEntries((cur) => cur && cur.map((x) => (x.speaker_label === saved.speaker_label ? saved : x)))
    // The undo would put the target back as it was, so editing the target ends it.
    if (mergeUndo && saved.speaker_label === mergeUndo.target) forgetMergeUndo()
  }
  const remembered = (r: RememberResult) => {
    setNotice(r.created
      ? `Added ${r.series_character.character_name} to the series cast.`
      : `Linked ${r.character.speaker_label} to ${r.series_character.character_name} in the series cast.`)
    setCastRefresh((n) => n + 1)
  }

  const renamed = (r: RenameResult) => {
    setEntries(r.characters)
    forgetMergeUndo()
    setSwapUndo(null)
    setUndo(r.undo)
    saveRenameUndo(dramaId, r.undo)
    setNotice(r.undo ? `Renamed ${r.undo.previous_label} to ${r.undo.speaker_label} on ${r.renamed} lines.` : null)
    setSuggestRefresh((n) => n + 1)
    reloadSamples()
  }
  const merged = (r: MergeResult, sourceLabel: string, targetLabel: string) => {
    setEntries(r.characters)
    const handle = r.undo
      ? { id: r.undo.undo_id, source: sourceLabel, target: targetLabel, expiresAt: Date.now() + r.undo.expires_in * 1000 }
      : null
    setMergeUndo(handle)
    saveMergeUndo(dramaId, handle)
    // A rename's undo names labels that may be gone now.
    setSwapUndo(null)
    setUndo(null)
    saveRenameUndo(dramaId, null)
    setNotice(`Merged ${sourceLabel} into ${targetLabel}: ${r.moved} ${r.moved === 1 ? 'line' : 'lines'} moved.`)
    setSuggestRefresh((n) => n + 1)
    reloadSamples()
  }
  const swapped = (r: SpeakerChangeResult, first: string, second: string) => {
    getCharacters(dramaId).then(setEntries, setError)
    setSwapUndo(r.undo.length ? r.undo : null)
    setNotice(`Swapped ${first} and ${second}: ${r.updated} ${r.updated === 1 ? 'line' : 'lines'} changed.`)
    setSuggestRefresh((n) => n + 1)
    reloadSamples()
  }
  const runUndoSwap = () => {
    if (!swapUndo) return
    setUndoBusy(true)
    undoSpeakerChange(dramaId, swapUndo).then(
      (r) => {
        setUndoBusy(false)
        setSwapUndo(null)
        getCharacters(dramaId).then(setEntries, setError)
        setNotice(`Put the speakers back on ${r.updated} ${r.updated === 1 ? 'line' : 'lines'}.`)
        setSuggestRefresh((n) => n + 1)
        reloadSamples()
      },
      (e: unknown) => {
        setUndoBusy(false)
        setError(e)
        // Lines edited since: retrying won't work. A busy job or a lost
        // connection leaves the undo valid, so the button stays.
        if (!undoIdSurvives(e)) setSwapUndo(null)
      },
    )
  }
  const runUndoMerge = () => {
    if (!mergeUndo) return
    setUndoBusy(true)
    undoMergeSpeakers(dramaId, mergeUndo.id).then(
      (r) => {
        setUndoBusy(false)
        setEntries(r.characters)
        setNotice(`Put ${mergeUndo.source} back on ${r.moved} ${r.moved === 1 ? 'line' : 'lines'}.`)
        forgetMergeUndo()
        setSuggestRefresh((n) => n + 1)
        reloadSamples()
      },
      (e: unknown) => {
        setUndoBusy(false)
        setError(e)
        // Refused, spent or expired: retrying won't work. A busy job or a lost
        // connection leaves the id with the server, so the button stays.
        if (!undoIdSurvives(e)) forgetMergeUndo()
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
        reloadSamples()
      },
      (e: unknown) => {
        setUndoBusy(false)
        setError(e)
        // A refused undo (renamed again, label reused) won't work later either;
        // a busy job or a lost connection leaves it valid, so the button stays.
        if (!undoIdSurvives(e)) {
          setUndo(null)
          saveRenameUndo(dramaId, null)
        }
      },
    )
  }

  useEffect(() => {
    let cancelled = false
    setLoadForbidden(false)
    getCharacters(dramaId).then(
      (c) => !cancelled && setEntries(c),
      (e: unknown) => !cancelled && (isForbidden(e) ? setLoadForbidden(true) : setError(e)),
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
      defaultOpen={defaultOpen}
      openSignal={focusSignal}
      title="Characters"
      count={entries?.length}
      summary={entries ? (entries.length ? 'names, pronouns and voices' : 'no speakers yet') : undefined}
    >
      <div id="translate-characters" role="region" aria-label="Characters">
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {loadForbidden && <p className="muted" role="status">{LINES_READ_FORBIDDEN}</p>}
      {/* A search picked up from Review > Voices belongs to no row here. */}
      {findFailed && findFor === null && <p className="error" role="alert">{findFailed}</p>}
      {entries && entries.length === 0 && <p className="muted">No speakers yet. They appear after transcription.</p>}
      {entries && entries.length > 0 && drama.series_id ? (
        <VoiceSuggestions dramaId={dramaId} refresh={suggestRefresh} onAccepted={replace} />
      ) : null}
      {entries && entries.length > 0 && (
        <div className="table-scroll"><table role="table" className="card-table">
          <thead>
            <tr role="row"><th role="columnheader">Speaker</th><th role="columnheader">Name</th><th role="columnheader">Gender</th><th role="columnheader">Lines</th>{drama.has_audio && <th role="columnheader">Sample</th>}<th role="columnheader">Reference</th><th role="columnheader"><span className="visually-hidden">Save</span></th></tr>
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
                leavesClip={(t) => leavesVoiceClip(e, entries.find((x) => x.speaker_label === t))}
                sample={sampleFor(e.speaker_label)}
                columns={columns}
                onSaved={(saved) => {
                  replace(saved)
                  setSuggestRefresh((n) => n + 1)
                }}
                onRemembered={remembered}
                onRenamed={renamed}
                onMerged={merged}
                onSwapped={swapped}
              />
            ))}
          </tbody>
        </table></div>
      )}
      {(notice || undo || mergeUndo || swapUndo) && (
        <p role="status" className="character-notice">
          {notice}
          {undo && <button type="button" disabled={undoBusy} onClick={runUndo}>Undo rename</button>}
          {mergeUndo && <button type="button" disabled={undoBusy} onClick={runUndoMerge}>Undo merge</button>}
          {swapUndo && <button type="button" disabled={undoBusy} onClick={runUndoSwap}>Undo swap</button>}
        </p>
      )}
      {drama.series_id ? <SeriesCast key={drama.series_id} seriesId={drama.series_id} refresh={castRefresh} /> : null}
      </div>
    </Section>
  )
}
