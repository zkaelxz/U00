import { useCallback, useEffect, useState } from 'react'

import { attachNovelEpub, attachNovelText, getNovelStatus } from '../../../api/workspace'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Section } from '../../../components/Section'
import type { NovelMode, NovelStatus } from '../../../types/workspace'
import { useStage } from '../StageContext'

export function NovelPanel() {
  const { dramaId, refetchDrama } = useStage()
  const [status, setStatus] = useState<NovelStatus | null>(null)
  const [mode, setMode] = useState<NovelMode>('replace')
  const [text, setText] = useState('')
  const [epub, setEpub] = useState<File | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [reloads, setReloads] = useState(0)

  useEffect(() => {
    let cancelled = false
    getNovelStatus(dramaId).then(
      (s) => !cancelled && setStatus(s),
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, reloads])

  const attached = useCallback(
    (chars: number) => {
      setError(null)
      setNotice(`Attached ${chars.toLocaleString()} characters.`)
      setReloads((n) => n + 1)
      refetchDrama()
    },
    [refetchDrama],
  )
  const fail = (e: unknown) => {
    setNotice(null)
    setError(e)
  }

  const summary = status?.has_novel_text
    ? `${status.char_count.toLocaleString()} chars · ${status.chapters} chapters`
    : 'none attached'

  return (
    <section className="panel" aria-label="Novel text">
      <Section storageKey="source.novel" title="Novel text" summary={summary}>
        <p className="muted" data-testid="novel-status">
          {status?.has_novel_text
            ? `Attached: ${status.char_count.toLocaleString()} characters, ${status.chapters} chapters.`
            : 'No novel text attached.'}
        </p>
        <Field label="Mode" help="Replace overwrites any attached novel text; Append adds to it.">
          <select value={mode} onChange={(e) => setMode(e.target.value as NovelMode)}>
            <option value="replace">Replace existing</option>
            <option value="append">Append</option>
          </select>
        </Field>
        <Field label="Paste text" help="Paste the novel text, then attach it to this drama.">
          <textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} />
        </Field>
        <button
          type="button"
          disabled={!text.trim()}
          onClick={() =>
            attachNovelText(dramaId, text, mode).then((r) => {
              setText('')
              attached(r.char_count)
            }, fail)
          }
        >
          Attach text
        </button>
        <Field label="EPUB file" help="Or attach an .epub file instead of pasting.">
          <input type="file" accept=".epub" onChange={(e) => setEpub(e.target.files?.[0] ?? null)} />
        </Field>
        <button
          type="button"
          disabled={!epub}
          onClick={() => epub && attachNovelEpub(dramaId, epub, mode).then((r) => attached(r.char_count), fail)}
        >
          Attach EPUB
        </button>
        {notice && <p role="status">{notice}</p>}
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
      </Section>
    </section>
  )
}
