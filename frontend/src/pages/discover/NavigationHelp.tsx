/*
 * Discover > Site navigation helper (DI05). For a site in a language you
 * don't read: the PC reads the public page, translates its visible labels
 * and writes step-by-step help for the site's own interface (a job; 2+ AI
 * calls). It never signs in, buys or fetches content. Steps are shown as
 * plain text (the model writes Markdown; nothing is rendered as HTML).
 */
import { useEffect, useState, type FormEvent } from 'react'

import { NAV_JOB_ID, getNavigationHelpResult, listPlatforms, startNavigationHelp } from '../../api/discover'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import type { NavigationHelpResult, Platform } from '../../types/discover'
import { TARGET_LANGUAGES, isHttpUrl } from './discoverFormat'
import { useDiscoverJob } from './useDiscoverJob'

export function NavigationHelp({ engine, aiReady }: { engine: string; aiReady: boolean }) {
  const [platforms, setPlatforms] = useState<Platform[]>([])
  const [url, setUrl] = useState('')
  const [goal, setGoal] = useState('')
  const [lang, setLang] = useState('English')
  const job = useDiscoverJob<NavigationHelpResult>(NAV_JOB_ID, getNavigationHelpResult)

  useEffect(() => {
    listPlatforms().then(setPlatforms, () => setPlatforms([]))
  }, [])

  const missing = [
    !isHttpUrl(url) && 'a page URL',
    !goal.trim() && 'what you are trying to do',
    !aiReady && 'an AI engine (set a key in Settings)',
  ].filter(Boolean) as string[]
  const running = job.status === 'running'

  function submit(e: FormEvent) {
    e.preventDefault()
    if (missing.length || running) return
    job.start(() => startNavigationHelp({ url: url.trim(), goal: goal.trim(), target_language: lang }, engine || undefined))
  }

  const r = job.result
  return (
    <form className="discover-block" onSubmit={submit}>
      <p className="muted discover-lead">
        For sites in a language you don't read: paste a public page's address and say what you want to do. You get its
        menu labels translated and steps for using the site yourself. It doesn't sign in, buy or download anything.
      </p>
      <Field label="Start from a known site">
        <select value="" onChange={(e) => e.target.value && setUrl(e.target.value)}>
          <option value="">Pick one to fill the URL…</option>
          {platforms.map((p) => (
            <option key={p.url} value={p.url}>
              {p.name}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Page URL">
        <input type="url" value={url} maxLength={2000} onChange={(e) => setUrl(e.target.value)} placeholder="https://" />
      </Field>
      <Field label="What are you trying to do?">
        <textarea
          value={goal}
          maxLength={500}
          rows={2}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="e.g. find the audio drama section for a title"
        />
      </Field>
      <Field label="Your language">
        <select value={lang} onChange={(e) => setLang(e.target.value)}>
          {TARGET_LANGUAGES.map((l) => (
            <option key={l}>{l}</option>
          ))}
        </select>
      </Field>
      {missing.length > 0 && <p className="muted">Still needed: {missing.join('; ')}.</p>}
      <div className="discover-row">
        <button type="submit" className="primary" disabled={missing.length > 0 || running} aria-busy={running}>
          {running ? 'Working…' : 'Get navigation steps'}
        </button>
        {running && (
          <span className="muted" role="status">
            {job.message || 'Reading the page…'}
          </span>
        )}
      </div>
      <ErrorBanner error={job.startError} onDismiss={job.clearStartError} />
      <ErrorBanner error={job.error} />
      {job.status === 'done' && r && (
        <div className="discover-result" data-testid="nav-result">
          {r.message && <p className={r.needs_manual ? 'warn' : 'muted'}>{r.message}</p>}
          {r.steps && (
            <>
              <h4>Steps</h4>
              <div className="discover-steps">{r.steps}</div>
            </>
          )}
          {Object.keys(r.labels ?? {}).length > 0 && (
            <details className="discover-sub">
              <summary>Translated page labels ({Object.keys(r.labels).length})</summary>
              <ul className="discover-labels">
                {Object.entries(r.labels).map(([orig, tr]) => (
                  <li key={orig}>
                    <span lang="zh">{orig}</span> → {tr}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </form>
  )
}
