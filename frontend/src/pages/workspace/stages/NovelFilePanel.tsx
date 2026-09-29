/*
 * NovelFilePanel: status plus PC-only upload / replace / remove for one of
 * the drama's two novel files (api/routers/novel_files_routes.py):
 *
 *   kind="reference"  the English novel translation, a terminology
 *                     reference for translation (Translate stage).
 *   kind="raw"        the original-language novel, which primes the
 *                     automatic Whisper prompt (Transcribe, Source stage).
 *
 * Status is readable remotely; the upload and remove controls are PC-only
 * (usePcOnly, pcOnlyFetch). The server answers 409 while a job for the
 * drama runs; `busy` disables the controls meanwhile.
 */
import { useEffect, useState } from 'react'

import { getNovelReference, getRawNovel, removeNovelReference, uploadNovelReference, uploadRawNovel } from '../../../api/novelFiles'
import { removeRawNovel } from '../../../api/stageDeletes'
import { ConfirmButton } from '../../../components/ConfirmButton'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Section } from '../../../components/Section'
import { PC_ONLY_BODY, usePcOnly } from '../../../hooks/usePcOnly'
import type { NovelFileStatus } from '../../../types/novelFiles'
import { useStage } from '../StageContext'
import { NOVEL_FILE_EXTENSIONS, checkNovelFile, novelFileStatusLine, novelFileSummary, type NovelFileKind } from './novelFile'

const COPY: Record<NovelFileKind, { title: string; name: string; label: string; help: string }> = {
  reference: {
    title: 'Novel reference (English translation)',
    name: 'novel reference',
    label: 'Reference file',
    help: 'An existing English translation of the novel (.txt or .md). Translation uses it to keep names and terms consistent.',
  },
  raw: {
    title: 'Raw novel (original language)',
    name: 'raw novel',
    label: 'Raw novel file',
    help: 'The original-language novel (.txt, .md or .epub). The automatic prompt uses its names and phrasing to help speech recognition.',
  },
}

const API = {
  reference: { get: getNovelReference, upload: uploadNovelReference, remove: removeNovelReference },
  raw: { get: getRawNovel, upload: uploadRawNovel, remove: removeRawNovel },
}

interface Props {
  kind: NovelFileKind
  busy?: boolean
  // Called after a successful upload or removal (e.g. to reload the auto prompt).
  onChanged?: () => void
}

export function NovelFilePanel({ kind, busy = false, onChanged }: Props) {
  const { dramaId } = useStage()
  const pc = usePcOnly()
  const copy = COPY[kind]
  const api = API[kind]
  const [status, setStatus] = useState<NovelFileStatus | null>(null)
  const [file, setFile] = useState<File | null>(null)
  const [working, setWorking] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [reloads, setReloads] = useState(0)
  const problem = file ? checkNovelFile(kind, file.name) : null

  useEffect(() => {
    let cancelled = false
    api.get(dramaId).then(
      (s) => !cancelled && setStatus(s),
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [api, dramaId, reloads])

  const done = (message: string) => {
    setWorking(false)
    setError(null)
    setNotice(message)
    setReloads((n) => n + 1)
    onChanged?.()
  }
  const fail = (e: unknown) => {
    setWorking(false)
    setNotice(null)
    setError(e)
  }

  const upload = () => {
    if (!file || problem) return
    setWorking(true)
    api.upload(dramaId, file).then((r) => {
      setFile(null)
      done(`${r.replaced ? 'Replaced' : 'Saved'}: ${r.char_count.toLocaleString()} characters.`)
    }, fail)
  }
  const remove = () => {
    setWorking(true)
    api.remove(dramaId).then(() => done('Removed.'), fail)
  }

  return (
    <section className="panel" aria-label={copy.title}>
      <Section storageKey={`novelfile.${kind}`} title={copy.title} summary={novelFileSummary(status)}>
        <p className="muted" data-testid={`novel-file-status-${kind}`}>{novelFileStatusLine(status)}</p>
        {pc === 'remote' ? (
          <p className="muted">{PC_ONLY_BODY}</p>
        ) : (
          <>
            <Field label={copy.label} help={copy.help} error={problem}>
              <input
                key={reloads}
                type="file"
                accept={NOVEL_FILE_EXTENSIONS[kind].join(',')}
                onChange={(e) => {
                  setNotice(null)
                  setFile(e.target.files?.[0] ?? null)
                }}
              />
            </Field>
            <div className="actions">
              <button type="button" disabled={!file || !!problem || busy || working} onClick={upload}>
                {status?.present ? 'Replace saved file' : 'Save file'}
              </button>
              {status?.present && (
                <ConfirmButton
                  name={copy.name}
                  label="Remove…"
                  confirmLabel={`Confirm remove ${copy.name}`}
                  verb="remove"
                  busy={working}
                  disabled={busy}
                  onConfirm={remove}
                />
              )}
              {busy && <span className="muted">Wait for the running job to finish.</span>}
            </div>
          </>
        )}
        {notice && <p role="status">{notice}</p>}
        <ErrorBanner error={error} describe={{ pcOnly: true }} onDismiss={() => setError(null)} />
      </Section>
    </section>
  )
}
