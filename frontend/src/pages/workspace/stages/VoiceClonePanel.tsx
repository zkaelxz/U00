import { useEffect, useState } from 'react'

import { cancelJob } from '../../../api/jobs'
import { listSeriesCharacters } from '../../../api/stageDeletes'
import { applyVoiceBankEntry, getCharacters, getVoiceBank, saveCharacter } from '../../../api/translateStage'
import {
  candidateAudioUrl,
  chooseCandidate,
  extractCandidates,
  linkSeriesCharacter,
  listCandidates,
  removeReferenceClip,
  saveToVoiceBank,
  uploadReferenceClip,
} from '../../../api/voiceClone'
import { ConfirmButton } from '../../../components/ConfirmButton'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { safeDetail } from '../../../components/errorMessages'
import { Field } from '../../../components/Field'
import { Section } from '../../../components/Section'
import { buttonClass } from '../../../components/uiClasses'
import { useJob, useJobRun } from '../../../hooks/useJob'
import { usePcOnly, type PcMode } from '../../../hooks/usePcOnly'
import type { SeriesCharacter } from '../../../types/autotuneGlossary'
import type { DubConfig } from '../../../types/dub'
import { TERMINAL_STATUSES, jobFailed } from '../../../types/jobs'
import type { CharacterEntry, VoiceBankEntry } from '../../../types/translateStage'
import type { VoiceCloneCandidates, VoiceCloneSpeakerCandidates } from '../../../types/voiceClone'
import { useStage } from '../StageContext'
import {
  CLIP_ACCEPT,
  PC_ONLY_CLIP_NOTE,
  candidatesFor,
  clipStatus,
  cloneWarnings,
  formatSeconds,
  replaceEntry,
  skipText,
  speakerTitle,
} from './voiceClone'

interface CardProps {
  entry: CharacterEntry
  warning: string | undefined
  candidates: VoiceCloneSpeakerCandidates | null
  bank: VoiceBankEntry[]
  cast: SeriesCharacter[]
  pc: PcMode
  extracting: boolean
  onExtract: (label: string) => void
  onSaved: (e: CharacterEntry) => void
  onBankSaved: (e: VoiceBankEntry) => void
}

function SpeakerCard({ entry, warning, candidates, bank, cast, pc, extracting, onExtract, onSaved, onBankSaved }: CardProps) {
  const { dramaId } = useStage()
  const label = entry.speaker_label
  const [actor, setActor] = useState(entry.voice_actor ?? '')
  const [file, setFile] = useState<File | null>(null)
  const [fileKey, setFileKey] = useState(0)
  const [refText, setRefText] = useState('')
  const [bankName, setBankName] = useState('')
  const [bankId, setBankId] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [pcError, setPcError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const run = <T,>(p: Promise<T>, ok: (v: T) => void, pcOnly = false) => {
    setBusy(true)
    setError(null)
    setPcError(null)
    setNotice(null)
    p.then(
      (v) => {
        setBusy(false)
        ok(v)
      },
      (e: unknown) => {
        setBusy(false)
        if (pcOnly) setPcError(e)
        else setError(e)
      },
    )
  }
  const saved = (msg: string) => (e: CharacterEntry) => {
    setActor(e.voice_actor ?? '')
    setNotice(msg)
    onSaved(e)
  }

  const actorDirty = actor.trim() !== (entry.voice_actor ?? '')
  const skip = skipText(candidates)
  const bankHint = entry.has_ref_audio ? undefined : 'Set a reference clip first.'

  return (
    <li className="voice-card" aria-label={`Voice for ${label}`}>
      <h4>{speakerTitle(entry)}</h4>
      {warning && <p className="voice-warning" data-testid="clone-warning">{warning}</p>}
      <p className="muted" data-testid="clip-status">
        {clipStatus(entry)} · {entry.line_count} line{entry.line_count === 1 ? '' : 's'}
      </p>

      <div className="voice-row">
        <Field label="Voice actor">
          <input aria-label={`Voice actor for ${label}`} value={actor} onChange={(e) => setActor(e.target.value)} />
        </Field>
        <button
          type="button"
          disabled={!actorDirty || busy}
          onClick={() => run(saveCharacter(dramaId, { speaker_label: label, voice_actor: actor.trim() }), saved('Voice actor saved.'))}
        >
          Save actor
        </button>
      </div>

      {cast.length > 0 && (
        <Field label="Series character" help="Pick a person already known in this series; their name is used here.">
          <select
            aria-label={`Series character for ${label}`}
            value={entry.series_character_id ?? ''}
            disabled={busy}
            onChange={(e) => {
              const id = e.target.value ? Number(e.target.value) : null
              run(linkSeriesCharacter(dramaId, label, id), saved(id ? 'Linked to the series character.' : 'Unlinked.'))
            }}
          >
            <option value="">Not linked</option>
            {cast.map((c) => <option key={c.id} value={c.id}>{c.character_name}</option>)}
          </select>
        </Field>
      )}

      <div className="voice-block">
        <h5>Reference clip</h5>
        {pc === 'remote' ? (
          <p className="muted">{PC_ONLY_CLIP_NOTE}</p>
        ) : (
          <>
            <div className="voice-row">
              <Field label="Clip file" help="wav, mp3, m4a, flac or ogg; 1 to 30 seconds of this speaker alone.">
                <input
                  key={fileKey}
                  type="file"
                  accept={CLIP_ACCEPT}
                  aria-label={`Clip file for ${label}`}
                  onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                />
              </Field>
              <Field label="What the clip says" help="Original-language words spoken in the clip (optional).">
                <input aria-label={`Clip transcript for ${label}`} value={refText} onChange={(e) => setRefText(e.target.value)} />
              </Field>
            </div>
            <div className="voice-actions">
              <button
                type="button"
                disabled={!file || busy}
                title={file ? undefined : 'Choose a file first.'}
                onClick={() =>
                  file &&
                  run(
                    uploadReferenceClip(dramaId, label, file, file.name, refText),
                    (e) => {
                      setFile(null)
                      setFileKey((k) => k + 1)
                      setRefText('')
                      saved(entry.has_ref_audio ? 'Clip replaced.' : 'Clip uploaded.')(e)
                    },
                    true,
                  )
                }
              >
                {entry.has_ref_audio ? 'Replace clip' : 'Upload clip'}
              </button>
              {entry.has_ref_audio && (
                <ConfirmButton
                  name={`clip for ${label}`}
                  label="Remove clip…"
                  ariaLabel={`Remove clip for ${label}`}
                  verb="remove"
                  busy={busy}
                  onConfirm={() => run(removeReferenceClip(dramaId, label), saved('Clip removed.'), true)}
                />
              )}
            </div>
          </>
        )}
        <div className="voice-actions">
          <button type="button" disabled={extracting || busy} onClick={() => onExtract(label)}>
            Find clips in the audio
          </button>
        </div>
        {skip && <p className="muted" data-testid="skip-reason">{skip}</p>}
        {candidates && candidates.candidates.length > 0 && (
          <ul className="voice-candidates" aria-label={`Candidate clips for ${label}`}>
            {candidates.candidates.map((c, i) => (
              <li key={c.id}>
                <audio controls preload="none" src={candidateAudioUrl(dramaId, c.id)} aria-label={`Play candidate ${i + 1} for ${label}`} />
                <span className="voice-candidate-text">
                  {formatSeconds(c.duration)} · {c.ref_text || <span className="muted">no matching line</span>}
                </span>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => run(chooseCandidate(dramaId, c.id), saved('Clip chosen.'))}
                >
                  Use clip {i + 1}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="voice-block">
        <h5>Voice bank</h5>
        <div className="voice-row">
          <Field label="Save as" help="Name this voice to reuse it in other projects.">
            <input aria-label={`Voice bank name for ${label}`} value={bankName} onChange={(e) => setBankName(e.target.value)} />
          </Field>
          <button
            type="button"
            disabled={!entry.has_ref_audio || !bankName.trim() || busy}
            title={bankHint ?? (bankName.trim() ? undefined : 'Type a name first.')}
            onClick={() =>
              run(saveToVoiceBank(dramaId, label, bankName), (e) => {
                setBankName('')
                setNotice(`Saved "${e.name}" to the voice bank.`)
                onBankSaved(e)
              })
            }
          >
            Save to voice bank
          </button>
        </div>
        {bankHint && <p className="muted">{bankHint}</p>}
        {bank.length > 0 && (
          <div className="voice-row">
            <Field label="Use a saved voice">
              <select aria-label={`Voice bank entry for ${label}`} value={bankId} onChange={(e) => setBankId(e.target.value)}>
                <option value="">Choose a voice</option>
                {bank.map((b) => (
                  <option key={b.id} value={b.id}>{b.name}{b.language ? ` (${b.language})` : ''}</option>
                ))}
              </select>
            </Field>
            <button
              type="button"
              disabled={!bankId || busy}
              onClick={() =>
                run(applyVoiceBankEntry(dramaId, label, Number(bankId)), (e) => {
                  setBankId('')
                  saved('Voice applied.')(e)
                })
              }
            >
              Apply voice
            </button>
          </div>
        )}
        <p className="muted">Voices cloned from commercial audio dramas are for personal use only.</p>
      </div>

      {notice && <p role="status">{notice}</p>}
      <ErrorBanner error={error} describe={{ serverText: true }} onDismiss={() => setError(null)} />
      <ErrorBanner error={pcError} describe={{ pcOnly: true, serverText: true }} onDismiss={() => setPcError(null)} />
    </li>
  )
}

// Dub stage -> "Voices and cloning": per speaker, a reference clip (upload,
// or extract candidates from the drama's audio and pick one), the voice
// bank, voice actor and series-character link. onChanged reloads the Dub
// config so its clone warnings stay current.
export function VoiceClonePanel({ cfg, onChanged }: { cfg: DubConfig | null; onChanged: () => void }) {
  const { dramaId, drama } = useStage()
  const pc = usePcOnly()
  const [entries, setEntries] = useState<CharacterEntry[] | null>(null)
  const [bank, setBank] = useState<VoiceBankEntry[]>([])
  const [cast, setCast] = useState<SeriesCharacter[]>([])
  const [cands, setCands] = useState<VoiceCloneCandidates | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [jobError, setJobError] = useState<unknown>(null)
  const [extractFor, setExtractFor] = useState<string | null>(null)
  const [jobId, setJobId, runKey] = useJobRun()
  const [candReload, setCandReload] = useState(0)

  useEffect(() => {
    let cancelled = false
    getCharacters(dramaId).then(
      (c) => !cancelled && setEntries(c),
      (e: unknown) => !cancelled && setError(e),
    )
    // Conveniences: a failure leaves the pickers empty.
    getVoiceBank().then((b) => !cancelled && setBank(b), () => undefined)
    if (drama.series_id) {
      listSeriesCharacters(drama.series_id).then((c) => !cancelled && setCast(c), () => undefined)
    }
    return () => {
      cancelled = true
    }
  }, [dramaId, drama.series_id])

  useEffect(() => {
    let cancelled = false
    listCandidates(dramaId).then((c) => !cancelled && setCands(c), () => undefined)
    return () => {
      cancelled = true
    }
  }, [dramaId, candReload])

  const { job, error: pollError } = useJob(jobId, {
    runKey,
    onDone: () => setCandReload((n) => n + 1),
  })
  const extracting = jobId !== null && !pollError && !(job && TERMINAL_STATUSES.includes(job.status))

  const startExtract = (label: string) => {
    setJobError(null)
    extractCandidates(dramaId, label).then((r) => {
      setExtractFor(label)
      setJobId(r.job_id)
    }, setJobError)
  }

  const warnings = cloneWarnings(cfg)
  const onSaved = (e: CharacterEntry) => {
    setEntries((cur) => replaceEntry(cur, e))
    onChanged()
  }

  // Nothing to do here before transcription finds speakers (rule 8).
  if (entries && entries.length === 0 && !jobId && !error) return null

  return (
    <Section
      storageKey="dub.voices"
      title="Voices and cloning"
      count={entries?.length}
      summary={
        warnings.size
          ? `${warnings.size} need${warnings.size === 1 ? 's' : ''} attention`
          : entries
            ? 'reference clips, voice bank, actors'
            : undefined
      }
    >
      <div className="voice-clone" role="region" aria-label="Voices and cloning">
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
        {jobId && (
          <div className="voice-job" data-testid="voice-extract-status">
            <span>
              Finding clips for {extractFor}: {job ? job.status : 'starting'}
              {job?.message ? ` · ${job.message}` : ''}
              {job && jobFailed(job) && job.error ? ` · ${safeDetail(job.error) ?? 'failed'}` : ''}
            </span>
            {extracting && (
              <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => void cancelJob(jobId).catch(setJobError)}>
                Cancel extraction
              </button>
            )}
          </div>
        )}
        <ErrorBanner error={jobError ?? pollError} describe={{ serverText: true }} onDismiss={() => setJobError(null)} />
        {entries && entries.length > 0 && (
          <ul className="voice-cards">
            {entries.map((e) => (
              <SpeakerCard
                key={e.speaker_label}
                entry={e}
                warning={warnings.get(e.speaker_label)}
                candidates={candidatesFor(cands, e.speaker_label)}
                bank={bank}
                cast={cast}
                pc={pc}
                extracting={extracting}
                onExtract={startExtract}
                onSaved={onSaved}
                onBankSaved={(b) => setBank((cur) => [...cur, b].sort((x, y) => x.name.localeCompare(y.name)))}
              />
            ))}
          </ul>
        )}
      </div>
    </Section>
  )
}
