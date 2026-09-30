/*
 * Export > Export to Notion (roadmap item 112). Shown only at the PC, and
 * only once Notion has been set up in Settings (a muted hint when it is
 * half done). Runs as a background job; re-exporting updates the same page
 * in place and keeps the user's own notes outside the Baihe transcript
 * section (that section is replaced on every export).
 */
import { useEffect, useState } from 'react'

import { getNotionConfig, getNotionDramaPage, startNotionExport } from '../../../api/notion'
import { getPcMode, loadPcMode } from '../../../api/pcOnly'
import { Section } from '../../../components/Section'
import { buttonClass } from '../../../components/uiClasses'
import { useJob, useJobRun } from '../../../hooks/useJob'
import type { NotionConfig, NotionDramaPage, NotionField } from '../../../types/notion'
import { notionErrorMessage, partlySetUp, readyToExport, safeNotionUrl } from '../../settings/notion'
import { useStage } from '../StageContext'
import { JobPanel } from './JobPanel'

const FIELD_TEXT: Record<NotionField, string> = {
  en: 'the English text',
  zh: 'the source-language text',
  bilingual: 'both languages',
}

export function ExportNotion({ field }: { field: NotionField }) {
  const { dramaId } = useStage()
  const [cfg, setCfg] = useState<NotionConfig | null>(null)
  const [page, setPage] = useState<NotionDramaPage | null>(null)
  const [starting, setStarting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [jobId, setJobId, runKey] = useJobRun()

  useEffect(() => {
    let live = true
    void loadPcMode().then(() => {
      if (!live || getPcMode() === 'remote') return
      getNotionConfig().then((c) => {
        if (!live) return
        setCfg(c)
        if (readyToExport(c)) {
          getNotionDramaPage(dramaId).then((p) => live && setPage(p), () => undefined)
        }
      }, () => undefined)
    })
    return () => {
      live = false
    }
  }, [dramaId])

  const { job, done, error: pollError } = useJob(jobId, {
    runKey,
    // Any finish: a run that failed after creating the page has still stored it.
    onDone: () => {
      getNotionDramaPage(dramaId).then(setPage, (e: unknown) => setError(notionErrorMessage(e)))
    },
  })

  if (!readyToExport(cfg) && !partlySetUp(cfg)) return null // not set up (or not at the PC)

  const busy = starting || (jobId !== null && !done && !pollError)
  const url = safeNotionUrl(page?.page_url)

  const run = () => {
    setStarting(true)
    setError(null)
    startNotionExport(dramaId, field).then(
      (r) => {
        setJobId(r.job_id)
        setStarting(false)
      },
      (e: unknown) => {
        setError(notionErrorMessage(e))
        setStarting(false)
      },
    )
  }

  return (
    <section className="panel" aria-label="Export to Notion">
      <Section storageKey="export.notion" title="Export to Notion" summary="a page per drama in your Notion">
        <div className="source-panel">
          {!readyToExport(cfg) ? (
            <p className="muted">Finish the Notion setup in Settings (integration token and a page or database).</p>
          ) : (
            <>
              <p className="muted">
                Exports {FIELD_TEXT[field]} (the Language choice above). Re-exporting updates the same page in place;
                your notes outside the Baihe transcript section are kept (that section is replaced each time).
              </p>
              <div>
                <button type="button" className={buttonClass('primary')} disabled={busy} onClick={run}>
                  {busy ? 'Working…' : page?.page_id ? 'Update Notion page' : 'Export to Notion'}
                </button>
              </div>
              {error && <p className="error" role="alert">{error}</p>}
              {jobId && <JobPanel job={job} pollError={pollError} />}
              {url && !busy && (
                <p data-testid="notion-page-link">
                  <a className="button-link" href={url} target="_blank" rel="noopener noreferrer">Open in Notion</a>
                </p>
              )}
            </>
          )}
        </div>
      </Section>
    </section>
  )
}
