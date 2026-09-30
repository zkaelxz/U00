import { useEffect, useRef, useState } from 'react'

import { getSupportReport } from '../../api/diagnostics'
import { Card } from '../../components/Card'
import { copyText } from '../../components/clipboard'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { COPIED_MS, copyFallbackText } from './diagnosticsAdmin'
import { parseSupportReport, supportReportFileName } from './supportReport'

/**
 * "Copy a report for a bug": one press builds the redacted support report
 * and copies it. "Download .txt" saves it; "What's in it" previews it as
 * readable rows, with the plain text one press away.
 */
export function SupportReportSection() {
  const [report, setReport] = useState<string | null>(null)
  const [working, setWorking] = useState<'copy' | 'download' | 'load' | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [note, setNote] = useState('')
  const [plain, setPlain] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)
  // Bumped only by the no-clipboard fallback, to re-mount the fold open.
  const [forceOpen, setForceOpen] = useState(0)
  const preRef = useRef<HTMLPreElement>(null)
  const touch = useMediaQuery('(pointer: coarse)')

  useEffect(() => {
    if (!note) return
    const t = setTimeout(() => setNote(''), COPIED_MS * 2)
    return () => clearTimeout(t)
  }, [note])

  // Always a fresh report: it reflects what is installed and logged right now.
  const load = (): Promise<string> =>
    getSupportReport().then(
      (r) => {
        setReport(r.report)
        return r.report
      },
      (e: unknown) => {
        setError(e)
        throw e
      },
    )

  // Opening the preview the first time builds the report.
  const togglePreview = (open: boolean) => {
    setPreviewOpen(open)
    if (!open || report !== null || working) return
    setWorking('load')
    setError(null)
    load().catch(() => undefined).finally(() => setWorking(null))
  }

  // Without clipboard access: show the plain text, select it, say how to copy.
  // The selection waits for the <pre> to render (the effect below).
  const selectPending = useRef(false)
  const selectPlain = () => {
    if (!previewOpen) setForceOpen((n) => n + 1)
    setPreviewOpen(true)
    setPlain(true)
    selectPending.current = true
    setSelectTick((n) => n + 1)
  }
  const [selectTick, setSelectTick] = useState(0)
  useEffect(() => {
    const pre = preRef.current
    const sel = window.getSelection()
    if (!selectPending.current || !pre || !sel) return
    selectPending.current = false
    pre.scrollIntoView({ block: 'nearest' })
    const range = document.createRange()
    range.selectNodeContents(pre)
    sel.removeAllRanges()
    sel.addRange(range)
  }, [selectTick, forceOpen, plain, report])

  const copy = async () => {
    setWorking('copy')
    setError(null)
    setNote('')
    const pending = load()
    try {
      // A ClipboardItem holding the pending text keeps Safari's user
      // gesture across the fetch; other browsers write once it arrives.
      if (typeof ClipboardItem !== 'undefined' && navigator.clipboard?.write) {
        const blob = pending.then((t) => new Blob([t], { type: 'text/plain' }))
        await navigator.clipboard.write([new ClipboardItem({ 'text/plain': blob })])
      } else {
        if (!(await copyText(await pending))) throw new Error('no clipboard')
      }
      setNote('Copied. Paste it into your bug report.')
    } catch {
      // A failed build already shows its error; a refused clipboard gets the fallback.
      const text = await pending.catch(() => null)
      if (text !== null) {
        selectPlain()
        setNote(copyFallbackText(touch))
      }
    } finally {
      setWorking(null)
    }
  }

  const download = async () => {
    setWorking('download')
    setError(null)
    setNote('')
    try {
      const text = await load()
      const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }))
      const a = document.createElement('a')
      a.href = url
      a.download = supportReportFileName()
      document.body.appendChild(a)
      a.click()
      a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 0)
      setNote('Saved.')
    } catch {
      // load() already set the error.
    } finally {
      setWorking(null)
    }
  }

  return (
    <Card
      title="Copy a report for a bug"
      meta="Your versions, missing packages and recent errors, ready to paste into a bug report."
      aria-label="Copy a report for a bug"
    >
      <p className="muted">
        Redacted, so it's safe to share: it lists which keys are set, never the keys. To add a
        screenshot, use Report a problem at the top of the page.
      </p>
      <div className="actions">
        <button type="button" className={buttonClass('primary')} disabled={!!working} aria-busy={working === 'copy'}
          onClick={() => void copy()}>
          {working === 'copy' ? 'Copying…' : 'Copy report'}
        </button>
        <button type="button" className={buttonClass('secondary')} disabled={!!working} aria-busy={working === 'download'}
          onClick={() => void download()}>
          {working === 'download' ? 'Saving…' : 'Download .txt'}
        </button>
        <span className="muted" aria-live="polite" data-testid="report-note">
          {note}
        </span>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {/* Keyed so the fallback can re-mount it open; opening it by hand keeps focus. */}
      <Section key={forceOpen} title="What's in it" defaultOpen={forceOpen > 0}
        summary="Preview the report" onToggle={togglePreview}>
        {report === null ? (
          <p className="muted">{working ? 'Building the report…' : 'Nothing built yet.'}</p>
        ) : (
          <div className="diag-stack">
            <div className="actions">
              <button type="button" className={buttonClass('ghost', 'sm')} aria-pressed={plain} onClick={() => setPlain((p) => !p)}>
                Plain text
              </button>
            </div>
            {plain ? (
              <pre ref={preRef} className="diag-pre" aria-label="Support report" tabIndex={0}>
                {report}
              </pre>
            ) : (
              <ReportList text={report} />
            )}
          </div>
        )}
      </Section>
    </Card>
  )
}

function ReportList({ text }: { text: string }) {
  return (
    <dl className="report-list" aria-label="Support report" data-testid="report-list">
      {parseSupportReport(text).map((r, i) => (
        <div key={i} className="report-row">
          <dt>{r.label || '—'}</dt>
          <dd>
            {r.value && <span>{r.value}</span>}
            {r.items.length > 0 && (
              <ul className={r.mono ? 'report-items mono' : 'report-items'}>
                {r.items.map((it, j) => (
                  <li key={j}>
                    {it.name != null ? (
                      <>
                        <span className="report-item-name">{it.name}</span> <span className="muted">{it.value}</span>
                      </>
                    ) : (
                      it.value
                    )}
                  </li>
                ))}
              </ul>
            )}
          </dd>
        </div>
      ))}
    </dl>
  )
}
