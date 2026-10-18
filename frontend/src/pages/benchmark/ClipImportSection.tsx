import { useEffect, useRef, useState, type DragEvent } from 'react'

import {
  checkBenchmarkClips, importBenchmarkClips,
  type BenchmarkOptions, type ClipImportResult, type ClipNote, type ClipRowPayload, type SourceLanguage,
} from '../../api/benchmark'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { humanize } from '../../components/labels'
import { buttonClass } from '../../components/uiClasses'
import type { PcMode } from '../../hooks/usePcOnly'
import { plainError } from './benchmarkForm'
import {
  MAX_CLIPS, addFiles, applyManifest, applyPaste, importBlocker, importSummary, notesFor, parseManifest, toPayload,
  type ClipRow,
} from './clipImportForm'

type Props = { options: BenchmarkOptions; pc: PcMode; onDone: () => void }

const CHECK_DELAY_MS = 400
const NO_NOTES = { problems: [], warnings: [] }

/** Your own clips: pick or drop audio, fill the table, save. Each clip becomes a transcription case and, with a translation, a translation case. */
export function ClipImportSection({ options, pc, onDone }: Props) {
  const [setName, setSetName] = useState('')
  const [language, setLanguage] = useState<SourceLanguage>('zh')
  const [title, setTitle] = useState('')
  const [rows, setRows] = useState<ClipRow[]>([])
  const [nextKey, setNextKey] = useState(1)
  const [pasteText, setPasteText] = useState('')
  const [check, setCheck] = useState<{ problems: ClipNote[]; warnings: ClipNote[] }>(NO_NOTES)
  const [info, setInfo] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<ClipImportResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const checkSeq = useRef(0)

  // Mismatch warnings follow what is typed, after a short pause. Only rows with a transcript are asked about,
  // and only the fields the check reads, so typing a title or tag does not ask again.
  const checkKey = JSON.stringify(
    rows.filter((r) => r.transcript.trim()).map((r) => ({ filename: r.file.name, language: r.language, transcript: r.transcript.trim() })),
  )
  useEffect(() => {
    if (checkKey === '[]') return undefined
    const seq = ++checkSeq.current
    const timer = setTimeout(() => {
      checkBenchmarkClips(JSON.parse(checkKey) as ClipRowPayload[]).then(
        (r) => seq === checkSeq.current && setCheck(r),
        () => seq === checkSeq.current && setCheck(NO_NOTES),
      )
    }, CHECK_DELAY_MS)
    return () => clearTimeout(timer)
  }, [checkKey])
  const notes = checkKey === '[]' ? NO_NOTES : check

  if (pc === 'remote') return null

  const update = (key: number, patch: Partial<ClipRow>) => setRows((cur) => cur.map((r) => (r.key === key ? { ...r, ...patch } : r)))
  const remove = (key: number) => setRows((cur) => cur.filter((r) => r.key !== key))

  const take = (list: FileList | File[] | null) => {
    if (!list || list.length === 0) return
    const added = addFiles(rows, Array.from(list), { language, title }, nextKey)
    setRows([...rows, ...added.rows])
    setNextKey(nextKey + added.rows.length)
    setResult(null)
    setInfo(added.skipped.length
      ? `Left out ${added.skipped.length} ${added.skipped.length === 1 ? 'file' : 'files'} that aren't audio, are already listed, or are past ${MAX_CLIPS} clips: ${added.skipped.slice(0, 3).join(', ')}${added.skipped.length > 3 ? '…' : ''}.`
      : null)
  }

  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragging(false)
    take(e.dataTransfer.files)
  }

  const fillFromPaste = () => {
    const filled = applyPaste(rows, pasteText)
    setRows(filled.rows)
    setInfo(filled.unused ? `${filled.unused} pasted ${filled.unused === 1 ? 'line has' : 'lines have'} no clip row; add more audio files.` : null)
  }

  const loadManifest = async (file: File | undefined) => {
    if (!file) return
    const parsed = parseManifest(await file.text())
    if (parsed.badLine !== null) {
      setInfo(`Manifest line ${parsed.badLine} isn't a JSON object with an "audio" name.`)
      return
    }
    const applied = applyManifest(rows, parsed.lines)
    setRows(applied.rows)
    setInfo(applied.unmatched.length
      ? `${applied.unmatched.length} manifest ${applied.unmatched.length === 1 ? 'line names' : 'lines name'} a file you haven't picked: ${applied.unmatched.slice(0, 3).join(', ')}${applied.unmatched.length > 3 ? '…' : ''}.`
      : null)
  }

  const blocker = importBlocker(setName, rows)

  const save = async () => {
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const r = await importBenchmarkClips({ set_name: setName.trim(), clips: rows.map(toPayload), files: rows.map((x) => x.file) })
      setResult(r)
      setRows([])
      setCheck(NO_NOTES)
      setInfo(null)
      onDone()
    } catch (e) {
      setError(plainError(e, { pcOnly: true }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section title="Your own clips" summary="Upload audio with its transcript" storageKey="benchmark.clips" defaultOpen>
      <p className="muted">
        Pick the audio files you want to test on, then type each clip's transcript (and, if you have one, its translation).
        A clip becomes a transcription case; with a translation it also becomes a translation case. Write each transcript
        exactly as spoken: a wrong reference counts against every setup. At most {MAX_CLIPS} clips per save.
      </p>
      <div className="field-row">
        <Field label="Clip set name" help="Clips saved together form one golden set. Use one set for one batch of clips.">
          <input value={setName} maxLength={60} onChange={(e) => setSetName(e.target.value)} placeholder="e.g. my-dramas-oct" />
        </Field>
        <Field label="Language for new clips" help="Each clip keeps its own language, so one set can mix Chinese, Japanese and Korean.">
          <select value={language} onChange={(e) => setLanguage(e.target.value as SourceLanguage)}>
            {options.source_languages.map((l) => (
              <option key={l} value={l}>{humanize('language', l)}</option>
            ))}
          </select>
        </Field>
        <Field label="Title for new clips" help="Clips of one title stay in the order they are listed, so a scene keeps its sequence. Optional.">
          <input value={title} maxLength={80} onChange={(e) => setTitle(e.target.value)} />
        </Field>
      </div>

      <div
        className={dragging ? 'bench-drop bench-drop-active' : 'bench-drop'}
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        data-testid="clip-drop"
      >
        <p>Drop audio files here, or choose them.</p>
        <Field label="Audio files" help="mp3, wav, m4a, flac or ogg. Choose several at once.">
          <input
            type="file"
            multiple
            accept=".mp3,.wav,.m4a,.flac,.ogg,audio/*"
            onChange={(e) => {
              take(e.target.files)
              e.target.value = ''
            }}
          />
        </Field>
      </div>

      {rows.length > 0 && (
        <ul className="bench-clip-rows" aria-label="Clips to save">
          {rows.map((r) => {
            const rowNotes = notesFor(notes.warnings, r.file.name)
            return (
              <li key={r.key} className="bench-clip-row">
                <div className="bench-clip-file">
                  <strong>{r.file.name}</strong>
                  <button type="button" className={buttonClass('ghost', 'sm')} aria-label={`Remove ${r.file.name}`} onClick={() => remove(r.key)}>
                    Remove
                  </button>
                </div>
                <div className="bench-clip-meta">
                  <input aria-label={`Title of ${r.file.name}`} placeholder="Title" value={r.title} maxLength={80} onChange={(e) => update(r.key, { title: e.target.value })} />
                  <select aria-label={`Language of ${r.file.name}`} value={r.language} onChange={(e) => update(r.key, { language: e.target.value as SourceLanguage })}>
                    {options.source_languages.map((l) => (
                      <option key={l} value={l}>{humanize('language', l)}</option>
                    ))}
                  </select>
                  <input aria-label={`Tags of ${r.file.name}`} placeholder="Tags: music, whisper" value={r.tags} onChange={(e) => update(r.key, { tags: e.target.value })} />
                </div>
                <textarea
                  aria-label={`Transcript of ${r.file.name}`}
                  placeholder="Transcript (exactly as spoken)"
                  rows={2}
                  maxLength={4000}
                  lang={r.language}
                  value={r.transcript}
                  onChange={(e) => update(r.key, { transcript: e.target.value })}
                />
                <textarea
                  aria-label={`Translation of ${r.file.name}`}
                  placeholder="Translation (optional)"
                  rows={2}
                  maxLength={4000}
                  value={r.translation}
                  onChange={(e) => update(r.key, { translation: e.target.value })}
                />
                {rowNotes.map((m) => (
                  <p key={m} className="warn" role="note">{m}</p>
                ))}
              </li>
            )
          })}
        </ul>
      )}

      {rows.length > 0 && (
        <>
          <Field label="Paste text for many clips" help="One clip per line, in the order listed above. Put the translation after a Tab. Fills the transcripts, replacing what is there.">
            <textarea rows={3} value={pasteText} onChange={(e) => setPasteText(e.target.value)} placeholder={'你好\tHello\n谢谢\tThank you'} spellCheck={false} />
          </Field>
          <div className="actions">
            <button type="button" className={buttonClass('secondary', 'sm')} disabled={!pasteText.trim()} onClick={fillFromPaste}>
              Fill rows
            </button>
          </div>
        </>
      )}

      <Field label="Or load a manifest" help="Optional: a file with one JSON object per line ({&quot;audio&quot;: &quot;clip01.wav&quot;, &quot;language&quot;: &quot;zh&quot;, &quot;transcript&quot;: …}). It fills the rows of the files you have chosen. You never need one.">
        <input type="file" accept=".jsonl,.json,.txt" disabled={rows.length === 0} onChange={(e) => void loadManifest(e.target.files?.[0])} />
      </Field>

      {info && <p className="muted" role="status">{info}</p>}
      {notes.problems.length > 0 && (
        <ul className="error" role="alert">
          {notes.problems.map((n) => (
            <li key={`${n.clip}:${n.message}`}>{n.message}</li>
          ))}
        </ul>
      )}
      <div className="actions">
        <button type="button" className={buttonClass('primary')} disabled={!!blocker || busy} onClick={() => void save()}>
          {busy ? 'Saving…' : 'Save clips'}
        </button>
        {blocker && <span className="muted">{blocker}</span>}
      </div>
      {result && (
        <div role="status">
          <p>{importSummary(result)}</p>
          {result.warnings.map((n) => (
            <p key={`${n.clip}:${n.message}`} className="warn">{n.clip}: {n.message}</p>
          ))}
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </Section>
  )
}
