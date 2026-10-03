/*
 * "Report a problem": a header button and its dialog.
 *
 *   <ReportProblemButton />   the button; renders the dialog while it is open
 *   openReportDialog()        (./reportDialogStore) opens it from anywhere, e.g.
 *                             an error fallback, while a <ReportProblemButton/>
 *                             is mounted
 *
 * The dialog sends the notes, an optional screenshot (PC only: the field is
 * hidden off the PC, and the server refuses one) and the capture buffers
 * (report/capture.ts) to POST /api/diagnostics/bug-reports, then offers
 * Copy report (markdown) and Open GitHub issue. Before sending, the form also
 * offers the redacted support report (Copy / Download) and the saved reports. The link is built only from
 * the server-scrubbed texts, never with the server log. If saving fails,
 * both still work with the client-side data, sanitized here first.
 */
import { useEffect, useId, useRef, useState, useSyncExternalStore } from 'react'

import { submitBugReport } from '../api/bugReports'
import { api } from '../api/client'
import { applyMeta, getPcMode } from '../api/pcOnly'
import type { MetaResponse } from '../api/types'
import { Sheet } from '../components/Sheet'
import { copyText } from '../components/clipboard'
import { describeError } from '../components/errorMessages'
import { useMediaQuery } from '../hooks/useMediaQuery'
import { usePcOnly } from '../hooks/usePcOnly'
import { captureSnapshot } from './capture'
import { SavedReports } from './SavedReports'
import { SupportReportPanel } from './SupportReportPanel'
import {
  buildIdFrom, buildReport, clientMarkdown, githubIssueUrl, sanitizeReport, sanitizeText, screenshotProblem,
  type IssueFields,
} from './reportBundle'
import { closeReportDialog, isReportDialogOpen, openReportDialog, subscribeReportDialog } from './reportDialogStore'
import './reportProblem.css'

export function ReportProblemButton() {
  const open = useSyncExternalStore(subscribeReportDialog, isReportDialogOpen, isReportDialogOpen)
  const buttonRef = useRef<HTMLButtonElement>(null)
  // The dialog unmounts on close, so focus is handed back here explicitly —
  // after the unmount, since the page is inert while the modal is still open.
  const close = () => {
    closeReportDialog()
    requestAnimationFrame(() => buttonRef.current?.focus())
  }
  return (
    <>
      <button ref={buttonRef} type="button" className="report-btn" aria-haspopup="dialog" onClick={openReportDialog}>
        <svg aria-hidden="true" viewBox="0 0 24 24" width="16" height="16" focusable="false">
          <path fill="currentColor" d="M5 3h2v18H5zM8 4h10l-2 4 2 4H8z" />
        </svg>
        <span>Report a problem</span>
      </button>
      {open && <ReportProblemDialog onClose={close} />}
    </>
  )
}

// `markdown` is what Copy report copies; `issue`/`issueMarkdown` feed the public link.
type Result =
  | { kind: 'saved'; id: number; markdown: string; issue: IssueFields; issueMarkdown: string }
  | { kind: 'failed'; error: unknown; markdown: string; issue: IssueFields; issueMarkdown: string }

function environment() {
  const scripts = Array.from(document.querySelectorAll<HTMLScriptElement>('script[type="module"][src]'))
  return {
    userAgent: navigator.userAgent,
    viewport: { width: window.innerWidth, height: window.innerHeight, dpr: window.devicePixelRatio || 1 },
    hostname: window.location.hostname,
    buildId: buildIdFrom(scripts.map((s) => s.src)),
  }
}

function ReportProblemDialog({ onClose }: { onClose: () => void }) {
  const [what, setWhat] = useState('')
  const [expected, setExpected] = useState('')
  const [includeLog, setIncludeLog] = useState(true)
  const [shot, setShot] = useState<File | null>(null)
  const [missing, setMissing] = useState(false)
  const [sending, setSending] = useState(false)
  const [result, setResult] = useState<Result | null>(null)
  const meta = useRef<MetaResponse | null>(null)
  const metaLoaded = useRef<Promise<void>>(Promise.resolve())
  const ids = useId()
  const pc = usePcOnly()
  const shotsAllowed = pc !== 'remote'
  const shotError = shotsAllowed ? screenshotProblem(shot) : null

  useEffect(() => {
    let live = true
    // Also feeds the shared PC-only mode: a page that never loaded it would
    // otherwise report mode "unknown".
    metaLoaded.current = api.meta().then((m) => {
      applyMeta(m)
      if (live) meta.current = m
    }, () => undefined)
    return () => {
      live = false
    }
  }, [])

  // Sheet calls showModal() in its own (later-running) effect, which moves
  // focus to the first focusable control; hand it to the textarea after that.
  useEffect(() => {
    const t = requestAnimationFrame(() => document.getElementById(`${ids}-what`)?.focus())
    return () => cancelAnimationFrame(t)
  }, [ids])

  const submit = async () => {
    if (!what.trim()) {
      setMissing(true)
      document.getElementById(`${ids}-what`)?.focus()
      return
    }
    if (shotError) return
    setSending(true)
    await metaLoaded.current
    const r = buildReport({ whatHappened: what, expected, includeServerLog: includeLog }, captureSnapshot(),
      meta.current, environment(), getPcMode())
    try {
      const saved = await submitBugReport(r, shotsAllowed ? shot : null)
      setResult({
        kind: 'saved', id: saved.id, markdown: saved.markdown, issueMarkdown: saved.issue_markdown,
        issue: { what_happened: saved.what_happened, expected: saved.expected, route: r.route, title: saved.title },
      })
    } catch (e) {
      const safe = sanitizeReport(r)
      const markdown = sanitizeText(clientMarkdown(safe))
      setResult({ kind: 'failed', error: e, markdown, issueMarkdown: markdown, issue: safe })
    } finally {
      setSending(false)
    }
  }

  return (
    <Sheet open title="Report a problem" onClose={onClose}>
      {result ? (
        <ReportResult result={result} hadScreenshot={shotsAllowed && !!shot} onClose={onClose} />
      ) : (
        <form
          className="report-form"
          noValidate
          onSubmit={(e) => {
            e.preventDefault()
            void submit()
          }}
        >
          <div className="field-item">
            <label htmlFor={`${ids}-what`}>What happened? <span className="muted">(required)</span></label>
            <textarea
              id={`${ids}-what`}
              rows={4}
              maxLength={5000}
              required
              value={what}
              aria-invalid={missing && !what.trim() ? true : undefined}
              aria-describedby={missing && !what.trim() ? `${ids}-what-err` : undefined}
              onChange={(e) => setWhat(e.target.value)}
            />
            {missing && !what.trim() && (
              <p id={`${ids}-what-err`} className="field-error error" role="alert">Say what happened.</p>
            )}
          </div>
          <div className="field-item">
            <label htmlFor={`${ids}-exp`}>What did you expect?</label>
            <textarea id={`${ids}-exp`} rows={2} maxLength={5000} value={expected}
              onChange={(e) => setExpected(e.target.value)} />
          </div>
          {shotsAllowed ? (
            <div className="field-item">
              <label htmlFor={`${ids}-shot`}>Screenshot <span className="muted">(optional, PNG or JPEG, max 5 MB)</span></label>
              <input id={`${ids}-shot`} type="file" accept="image/png,image/jpeg"
                aria-invalid={shotError ? true : undefined}
                aria-describedby={shotError ? `${ids}-shot-err` : undefined}
                onChange={(e) => setShot(e.target.files?.[0] ?? null)} />
              {shotError && <p id={`${ids}-shot-err`} className="field-error error" role="alert">{shotError}</p>}
            </div>
          ) : (
            <p className="muted" data-testid="report-shot-pc-only">
              Screenshots can only be attached at the main PC. Add one to the GitHub issue instead.
            </p>
          )}
          <label className="report-check">
            <input type="checkbox" checked={includeLog} onChange={(e) => setIncludeLog(e.target.checked)} />
            Include recent server log
          </label>
          <p className="muted report-note">
            Also sent: the pages you visited, recent errors and failed requests (never their contents),
            your browser and screen size. Keys, tokens and user folder names are removed before the report
            is saved and before it goes into the GitHub link; the server log is never put in the link.
          </p>
          <div className="actions report-actions">
            <button type="submit" className="primary" disabled={sending}>
              {sending ? 'Sending…' : 'Send report'}
            </button>
            <button type="button" onClick={onClose}>Cancel</button>
          </div>
        </form>
      )}
      {!result && (
        <div className="report-extra">
          <SupportReportPanel />
          <SavedReports pc={pc} />
        </div>
      )}
    </Sheet>
  )
}

function ReportResult({ result, hadScreenshot, onClose }: {
  result: Result
  hadScreenshot: boolean
  onClose: () => void
}) {
  const touch = useMediaQuery('(pointer: coarse)')
  const [note, setNote] = useState('')
  const [showText, setShowText] = useState(false)
  const textRef = useRef<HTMLTextAreaElement>(null)
  const issue = githubIssueUrl(result.issue, result.issueMarkdown)

  const copy = async () => {
    if (await copyText(result.markdown)) {
      setNote('Copied.')
    } else {
      setShowText(true)
      setNote(touch ? 'Long-press the text below to copy it.' : 'Press Ctrl+C to copy the selected text.')
      requestAnimationFrame(() => {
        textRef.current?.focus()
        textRef.current?.select()
      })
    }
  }

  const failed = result.kind === 'failed' ? describeError(result.error) : null
  return (
    <div className="report-form" data-testid="report-result">
      {result.kind === 'saved' ? (
        <p className="report-saved" role="status">Saved as report #{result.id}.</p>
      ) : (
        <div className="banner error-banner" role="alert">
          <div>
            <strong>Couldn't save the report on the server.</strong>
            <div className="muted">{failed?.title} You can still copy it or open a GitHub issue.</div>
          </div>
        </div>
      )}
      <div className="actions report-actions">
        <button type="button" className="primary" onClick={() => void copy()}>Copy report</button>
        <a className="button-link" href={issue.url} target="_blank" rel="noopener noreferrer">
          Open GitHub issue
        </a>
        <span className="muted" aria-live="polite">{note}</span>
      </div>
      {issue.truncated && (
        <p className="muted" data-testid="report-truncated">
          The report was too long for the link and was cut. Use Copy report and paste the full text into the issue.
        </p>
      )}
      {hadScreenshot && result.kind === 'saved' && (
        <p className="muted">The screenshot stays on the PC; attach it to the issue yourself.</p>
      )}
      <details open={showText} onToggle={(e) => setShowText(e.currentTarget.open)}>
        <summary>Report text</summary>
        <textarea ref={textRef} className="report-text" readOnly rows={10} value={result.markdown}
          aria-label="Report text" />
      </details>
      <div className="actions report-actions">
        <button type="button" onClick={onClose}>Done</button>
      </div>
    </div>
  )
}
