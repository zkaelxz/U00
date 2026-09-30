/*
 * SiteCheck (SO02): "Will this site work?" next to the link box. One paced
 * fetch on the PC (job sources_url_preflight): is the page reachable, does
 * its text pass the same checks an import uses, can chapters be followed,
 * do the site's terms allow it. Imports nothing.
 */
import { startPreflight, PREFLIGHT_JOB_ID } from '../../api/sourcesTools'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import type { UrlPreflight } from '../../types/sourcesTools'
import { describeSourceError, percent } from './sourcesFormat'
import { preflightFacts, preflightTone } from './sourcesToolsFormat'
import { useSourcesJob } from './useSourcesJob'
import './sources-tools.css'

export function SiteCheck({ url, disabled }: { url: string; disabled: boolean }) {
  // No reattach on 409: the running check may be for another link.
  const job = useSourcesJob<UrlPreflight>(PREFLIGHT_JOB_ID, { reattachOn409: false })
  const running = job.status === 'running'
  const result = job.startedHere && job.status === 'done' && job.result?.kind === 'url_preflight' ? job.result : null
  const failed = job.startedHere && job.status === 'error' ? job.error : null

  return (
    <div className="sources-sitecheck">
      <div className="actions">
        <button
          type="button"
          className={buttonClass('ghost', 'sm')}
          disabled={disabled || running || !url}
          aria-busy={running}
          title="Checks the page once, without importing anything."
          onClick={() => job.start(() => startPreflight(url))}
        >
          {running ? 'Checking…' : 'Will this site work?'}
        </button>
        {running && (
          <span className="muted" role="status">
            {job.message || 'Checking the site…'}
            {percent(job.progress)}
          </span>
        )}
      </div>
      <ErrorBanner error={job.startError} onDismiss={job.clearStartError} describe={{ serverText: true }} />
      <div aria-live="polite">
        {failed && (
          <p className="warn" role="alert">
            {describeSourceError(failed, 'The site').text}
          </p>
        )}
        {result && <PreflightCard p={result} />}
      </div>
    </div>
  )
}

function PreflightCard({ p }: { p: UrlPreflight }) {
  const tone = preflightTone(p)
  const facts = preflightFacts(p)
  return (
    <div className={`sources-card sources-preflight tone-${tone}`} data-testid="site-check">
      <p className={tone === 'ok' ? 'sources-ok' : 'warn'}>
        <strong>{p.verdict || (p.ok ? 'Looks importable.' : 'This page may not import.')}</strong>
      </p>
      {facts.length > 0 && <p className="sources-meta">{facts.join(' · ')}</p>}
      {p.warnings.map((w, i) => (
        <p key={i} className="warn">
          {w}
        </p>
      ))}
      {p.lines.length > 0 && (
        <Section title="What the check found" defaultOpen={!p.ok}>
          <ul className="sources-notes">
            {p.lines.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  )
}
