/*
 * Discover > Bulk import from listing pages (DI07). Up to 10 tag/ranking
 * pages are read on the PC and their entries extracted by AI (a job):
 * title, author, tags and an audio-drama hint only, never chapters. Review
 * and untick before adding; the server skips titles already catalogued.
 * The optional pattern generator fills in paginated URLs from "{page}".
 */
import { useState, type FormEvent } from 'react'

import { BULK_JOB_ID, bulkCommit, getBulkExtractResult, startBulkExtract } from '../../api/discover'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import type { BulkCommitResult, BulkExtractResult } from '../../types/discover'
import { MAX_BULK_URLS, bulkUrlsProblem, commitEntries, paginateUrls, parseUrlList } from './discoverFormat'
import { ExternalLink } from './ExternalLink'
import { useDiscoverJob } from './useDiscoverJob'

export function BulkImport({ engine, aiReady, onAdded }: { engine: string; aiReady: boolean; onAdded: () => void }) {
  const [urlsText, setUrlsText] = useState('')
  const [label, setLabel] = useState('jjwxc_baihe_tag')
  const [pattern, setPattern] = useState('')
  const [from, setFrom] = useState(1)
  const [to, setTo] = useState(5)
  const job = useDiscoverJob<BulkExtractResult>(BULK_JOB_ID, getBulkExtractResult)

  const urls = parseUrlList(urlsText)
  const problem = urlsText.trim() ? bulkUrlsProblem(urls) : null
  const running = job.status === 'running'
  const generated = paginateUrls(pattern.trim(), from, to)

  function extract(e: FormEvent) {
    e.preventDefault()
    if (!aiReady || running || bulkUrlsProblem(urls)) return
    job.start(() => startBulkExtract(urls, label.trim(), engine || undefined))
  }

  return (
    <div className="discover-block">
      <p className="muted discover-lead">
        For pages that list many titles (a tag or ranking listing). Extracts title, author, tags and whether there is an
        audio drama, never chapter or episode content. Review before adding.
      </p>
      <Section title="Fill in page URLs from a pattern" summary="Optional, for paginated listings">
        <div className="discover-block">
          <p className="muted">Copy page 2's address, put {'{page}'} where the page number goes.</p>
          <Field label="URL pattern">
            <input
              type="url"
              value={pattern}
              maxLength={2000}
              onChange={(e) => setPattern(e.target.value)}
              placeholder="https://www.jjwxc.net/tag.php?tag=百合&page={page}"
            />
          </Field>
          <div className="discover-filters">
            <Field label="From page">
              <input type="number" min={1} value={from} onChange={(e) => setFrom(Number(e.target.value))} />
            </Field>
            <Field label="To page">
              <input type="number" min={1} value={to} onChange={(e) => setTo(Number(e.target.value))} />
            </Field>
          </div>
          <button type="button" className={buttonClass('ghost')} disabled={generated.length === 0} onClick={() => setUrlsText(generated.join('\n'))}>
            Fill in {generated.length || ''} URL{generated.length === 1 ? '' : 's'}
          </button>
          {generated.length > MAX_BULK_URLS && (
            <p className="muted">One run reads at most {MAX_BULK_URLS} pages; split the rest into later runs.</p>
          )}
        </div>
      </Section>
      <form className="discover-block" onSubmit={extract}>
        <Field label="Listing page URLs" help={`One per line, up to ${MAX_BULK_URLS}.`} error={problem}>
          <textarea rows={3} value={urlsText} onChange={(e) => setUrlsText(e.target.value)} placeholder="https://" />
        </Field>
        <Field label="Source label" help="For your own reference; stored with each added title.">
          <input type="text" value={label} maxLength={100} onChange={(e) => setLabel(e.target.value)} />
        </Field>
        {!aiReady && <p className="muted">Still needed: an AI engine (set a key in Settings).</p>}
        <div className="discover-row">
          <button type="submit" className={buttonClass('secondary')} disabled={!aiReady || running || !!bulkUrlsProblem(urls)} aria-busy={running}>
            {running ? 'Extracting…' : 'Extract entries'}
          </button>
          {running && (
            <span className="muted" role="status">
              {job.message || 'Reading pages…'}
              {job.progress ? ` · ${Math.round(job.progress * 100)}%` : ''}
            </span>
          )}
        </div>
      </form>
      <ErrorBanner error={job.startError} onDismiss={job.clearStartError} />
      <ErrorBanner error={job.error} />
      {job.status === 'done' && job.result && (
        <BulkReview key={job.result.entries.map((e) => e.entry_id ?? e.title).join('|')} result={job.result} onAdded={onAdded} />
      )}
    </div>
  )
}

function BulkReview({ result, onAdded }: { result: BulkExtractResult; onAdded: () => void }) {
  const [included, setIncluded] = useState<Set<number>>(() => new Set(result.entries.map((_, i) => i)))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [done, setDone] = useState<BulkCommitResult | null>(null)
  const failed = result.pages.filter((p) => !p.ok)
  const toAdd = commitEntries(result.entries, included)

  function toggle(i: number) {
    setIncluded((s) => {
      const n = new Set(s)
      if (n.has(i)) n.delete(i)
      else n.add(i)
      return n
    })
  }

  async function add() {
    setBusy(true)
    setError(null)
    try {
      setDone(await bulkCommit(toAdd, result.source_label))
      onAdded()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="discover-result" data-testid="bulk-review">
      {failed.length > 0 && (
        <div className="warn">
          {failed.length} of {result.pages.length} page{result.pages.length === 1 ? '' : 's'} couldn't be read:
          <ul className="discover-links">
            {failed.map((p) => (
              <li key={p.url}>
                <ExternalLink href={p.url}>{p.url}</ExternalLink>
                {p.message && <span className="muted"> — {p.message}</span>}
              </li>
            ))}
          </ul>
          {failed.some((p) => p.needs_manual) && (
            <p className="muted">
              Some sites build their listings with JavaScript. Open the page in your browser and add the titles with “Add a
              title” instead.
            </p>
          )}
        </div>
      )}
      {result.entries.length === 0 ? (
        <p className="muted">No entries were found.</p>
      ) : done ? (
        <p className="discover-ok" role="status">
          Added {done.added} title{done.added === 1 ? '' : 's'} to your catalogue
          {done.skipped ? ` (${done.skipped} already there)` : ''}.
        </p>
      ) : (
        <>
          <h4>Review {result.entries.length} entries</h4>
          <p className="muted">Untick any that look wrong. The audio-drama hint is a best guess.</p>
          <ul className="discover-review">
            {result.entries.map((e, i) => (
              <li key={e.entry_id ?? `${i}-${e.title}`}>
                <label>
                  <input type="checkbox" checked={included.has(i)} onChange={() => toggle(i)} />
                  <span>
                    <strong lang="zh">{e.title}</strong>
                    <span className="muted">
                      {' '}
                      {[e.author, e.tags, e.has_audio_drama ? 'audio drama' : ''].filter(Boolean).join(' · ')}
                    </span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
          <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ serverText: true }} />
          <button type="button" className={buttonClass('secondary')} disabled={toAdd.length === 0 || busy} aria-busy={busy} onClick={add}>
            {busy ? 'Adding…' : `Add ${toAdd.length} to catalogue`}
          </button>
        </>
      )}
    </div>
  )
}
