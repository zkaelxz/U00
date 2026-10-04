import { useEffect, useRef, useState } from 'react'

import { getJob } from '../../../api/jobs'
import { getLncrawlStatus, lncrawlJobId, startLncrawlImport } from '../../../api/workspace'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { Section } from '../../../components/Section'
import { buttonClass } from '../../../components/uiClasses'
import { useJob, useJobRun } from '../../../hooks/useJob'
import { usePcOnly } from '../../../hooks/usePcOnly'
import { TERMINAL_STATUSES } from '../../../types/jobs'
import type { LncrawlChapters, NovelMode } from '../../../types/workspace'
import { useStage } from '../StageContext'
import { JobPanel } from './JobPanel'
import { lncrawlNotice, lncrawlRequest } from './lncrawlForm'

interface Props {
  mode: NovelMode
  // Called when an import attached text, so the Novel text status reloads.
  onImported?: () => void
}

// Step 115b: shown only when the user-installed lightnovel-crawler program is
// found on the PC (GET /api/novel/lncrawl), and never on another device.
export function LncrawlPanel({ mode, onImported }: Props) {
  const { dramaId } = useStage()
  const pc = usePcOnly()
  const [installed, setInstalled] = useState(false)
  const [url, setUrl] = useState('')
  const [chapters, setChapters] = useState<LncrawlChapters>('all')
  const [count, setCount] = useState('')
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [jobId, setJobId, runKey] = useJobRun()

  useEffect(() => {
    let cancelled = false
    getLncrawlStatus().then(
      (s) => !cancelled && setInstalled(s.installed),
      () => !cancelled && setInstalled(false),
    )
    return () => {
      cancelled = true
    }
  }, [])

  // Reattach to an import started before this stage was left or reloaded.
  const startedRef = useRef(false)
  useEffect(() => {
    startedRef.current = jobId !== null
  })
  useEffect(() => {
    if (!installed) return
    let cancelled = false
    getJob(lncrawlJobId(dramaId)).then(
      (j) => {
        if (!cancelled && !startedRef.current && !TERMINAL_STATUSES.includes(j.status)) setJobId(j.job_id)
      },
      () => undefined,
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, installed, setJobId])

  const { job, done, error: pollError } = useJob(jobId, {
    runKey,
    onDone: (j) => {
      if (j.status === 'done') {
        setNotice(lncrawlNotice(j.result))
        onImported?.()
      }
    },
  })
  const running = jobId !== null && !done && !pollError

  if (!installed || pc === 'remote') return null

  const req = lncrawlRequest(url, chapters, count, mode)
  const detail = job?.status === 'error' && typeof job.result?.detail === 'string' ? job.result.detail : null
  const start = () => {
    if (!('body' in req)) return
    setNotice(null)
    startLncrawlImport(dramaId, req.body).then((r) => {
      setError(null)
      setJobId(r.job_id)
    }, setError)
  }

  return (
    <Section storageKey="source.novel.lncrawl" title="Import with lightnovel-crawler" summary={running ? 'Running' : 'Installed'}>
      <p className="muted">
        Runs lightnovel-crawler, a separate GPL-3.0 program installed on this PC, to download the
        novel as an EPUB, then attaches its text using the Mode above. Whether a site&apos;s terms
        allow downloading a title is your call, for each title.
      </p>
      <Field label="Novel address" help="The novel's main page on a site lightnovel-crawler supports (http:// or https://).">
        <input type="url" inputMode="url" spellCheck={false} value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" />
      </Field>
      <div className="epub-range">
        <Field label="Chapters" help="The lightnovel-crawler program can download all chapters, the first few or the latest few. Up to about 2 million characters of text can be attached, so for a very long novel pick the first or latest few.">
          <select value={chapters} onChange={(e) => setChapters(e.target.value as LncrawlChapters)}>
            <option value="all">All chapters</option>
            <option value="first">First…</option>
            <option value="last">Latest…</option>
          </select>
        </Field>
        {chapters !== 'all' && (
          <Field label="How many" help="A number of chapters, from 1.">
            <input type="number" inputMode="numeric" min={1} value={count} onChange={(e) => setCount(e.target.value)} />
          </Field>
        )}
      </div>
      {url.trim() && 'problem' in req && <p className="error" role="alert">{req.problem}</p>}
      <button type="button" className={buttonClass('secondary')} disabled={!('body' in req) || running} onClick={start}>
        Start import
      </button>
      {jobId && <JobPanel job={job} pollError={pollError} />}
      {detail && (
        <details>
          <summary>What lightnovel-crawler printed</summary>
          <pre className="muted" style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{detail}</pre>
        </details>
      )}
      {notice && <p role="status">{notice}</p>}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </Section>
  )
}
