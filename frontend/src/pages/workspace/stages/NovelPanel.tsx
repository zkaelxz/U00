import { useCallback, useEffect, useState } from 'react'

import { attachNovelEpub, attachNovelText, getNovelStatus, startNovelOcr } from '../../../api/workspace'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Section } from '../../../components/Section'
import type { NovelMode, NovelStatus } from '../../../types/workspace'
import { checkOcrImages, ocrBackendOptions } from '../sourceForm'
import { useStage } from '../StageContext'

interface Props {
  busy?: boolean
  onOcrStarted?: (jobId: string) => void
  // Bumped by the parent when a job finishes, so the status line reloads.
  reloadKey?: number
}

export function NovelPanel({ busy = false, onOcrStarted, reloadKey = 0 }: Props) {
  const { dramaId, drama, refetchDrama } = useStage()
  const [status, setStatus] = useState<NovelStatus | null>(null)
  const [mode, setMode] = useState<NovelMode>('replace')
  const [text, setText] = useState('')
  const [epub, setEpub] = useState<File | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [reloads, setReloads] = useState(0)
  const backends = ocrBackendOptions(drama.source_language)
  const [images, setImages] = useState<File[]>([])
  const [backend, setBackend] = useState('')
  const [tessCmd, setTessCmd] = useState('')
  const imageProblem = checkOcrImages(images.map((f) => f.name))
  const ocrBackend = backends.includes(backend) ? backend : backends[0]

  useEffect(() => {
    let cancelled = false
    getNovelStatus(dramaId).then(
      (s) => !cancelled && setStatus(s),
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, reloads, reloadKey])

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

  const base = status?.has_novel_text
    ? `${status.char_count.toLocaleString()} chars · ${status.chapters} chapters`
    : 'none attached'
  const summary = busy || status?.ocr_running ? `${base} · OCR running` : base

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
        <Section storageKey="source.novel.ocr" title="Chapter images (OCR)" summary={images.length ? `${images.length} images · ${ocrBackend}` : ocrBackend}>
          <Field label="Page images" help="PNG or JPG pages in reading order (up to 200). The text is read in the background and added using the Mode above.">
            <input
              type="file"
              accept=".png,.jpg,.jpeg"
              multiple
              onChange={(e) => setImages(Array.from(e.target.files ?? []))}
            />
          </Field>
          {imageProblem && <p className="error" role="alert">{imageProblem}</p>}
          <Field label="OCR engine" help="manga_ocr suits Japanese speech-bubble crops; paddle is heavier but more accurate for Chinese.">
            <select value={ocrBackend} onChange={(e) => setBackend(e.target.value)}>
              {backends.map((b) => (
                <option key={b} value={b}>{b}</option>
              ))}
            </select>
          </Field>
          {ocrBackend === 'tesseract' && (
            <Field label="Tesseract path" help="Only needed if Tesseract is installed but not on PATH. Leave blank otherwise.">
              <input type="text" value={tessCmd} onChange={(e) => setTessCmd(e.target.value)} />
            </Field>
          )}
          <button
            type="button"
            disabled={!images.length || !!imageProblem || busy || !!status?.ocr_running}
            onClick={() =>
              startNovelOcr(dramaId, images, ocrBackend, mode, ocrBackend === 'tesseract' ? tessCmd : undefined).then(
                (r) => {
                  setError(null)
                  setNotice(null)
                  setImages([])
                  onOcrStarted?.(r.job_id)
                },
                fail,
              )
            }
          >
            Extract text from images
          </button>
        </Section>
        {notice && <p role="status">{notice}</p>}
        <ErrorBanner error={error} onDismiss={() => setError(null)} />
      </Section>
    </section>
  )
}
