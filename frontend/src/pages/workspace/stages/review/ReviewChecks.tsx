import { useEffect, useState } from 'react'

import {
  compareVersions,
  getCoverage,
  getPacingFlags,
  getTendencies,
  listNotes,
  listVersions,
  notesMarkdownUrl,
} from '../../../../api/review'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Field } from '../../../../components/Field'
import { Section } from '../../../../components/Section'
import { lineNumber } from '../../../../lineNumber'
import type { Coverage, Pacing, Tendencies, VersionCompare, VersionItem } from '../../../../types/review'
import { FindingList } from './FindingList'
import { coverageGroups, pacingFindings, type GoToLine } from './reviewResults'

interface Props {
  dramaId: number
  reloads: number
  onGoTo: GoToLine
}

interface Checks {
  coverage: Coverage
  pacing: Pacing
  tendencies: Tendencies
  versions: VersionItem[]
  noteCount: number
}

// Small read-only checks, each a folded Section that only appears when it
// has something to show: coverage and pacing, edit tendencies, a version
// compare and the notes-as-Markdown link.
export function ReviewChecks({ dramaId, reloads, onGoTo }: Props) {
  const [checks, setChecks] = useState<Checks | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([getCoverage(dramaId), getPacingFlags(dramaId), getTendencies(dramaId), listVersions(dramaId), listNotes(dramaId)]).then(
      ([coverage, pacing, tendencies, versions, notes]) => {
        if (cancelled) return
        setError(null)
        setChecks({ coverage, pacing, tendencies, versions, noteCount: notes.length })
      },
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, reloads])

  if (!checks) return <ErrorBanner error={error} onDismiss={() => setError(null)} />
  const { coverage, pacing, tendencies, versions, noteCount } = checks
  return (
    <>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <CoverageSection coverage={coverage} pacing={pacing} onGoTo={onGoTo} />
      <TendenciesSection t={tendencies} />
      {versions.length >= 2 && <CompareSection key={versions.map((v) => v.id).join(',')} dramaId={dramaId} versions={versions} />}
      {noteCount > 0 && (
        <Section storageKey="review.notesExport" title="Notes export" count={noteCount} summary="Markdown">
          <a href={notesMarkdownUrl(dramaId)} target="_blank" rel="noreferrer" className="review-jump" data-testid="notes-markdown">
            Open translation notes as Markdown
          </a>
        </Section>
      )}
    </>
  )
}

function CoverageSection({ coverage, pacing, onGoTo }: { coverage: Coverage; pacing: Pacing; onGoTo: GoToLine }) {
  const groups = coverageGroups(coverage)
  const pace = pacingFindings(pacing.flags)
  const total = groups.reduce((n, g) => n + g.items.length, 0) + pace.length
  if (total === 0) return null
  const summary = [...groups.map((g) => `${g.title.replace(/ \(.*\)$/, '')} ${g.items.length}`), ...(pace.length ? [`Pacing ${pace.length}`] : [])]
  return (
    <Section storageKey="review.coverage" title="Coverage and pacing" count={total} summary={summary.join(' · ')}>
      {groups.map((g) => (
        <div key={g.title}>
          <h4>{g.title}</h4>
          <FindingList items={g.items} onGoTo={onGoTo} />
        </div>
      ))}
      {pace.length > 0 && (
        <div>
          <h4>Pacing (translation vs. time slot)</h4>
          <FindingList items={pace} onGoTo={onGoTo} testId="pacing-list" />
        </div>
      )}
    </Section>
  )
}

function TendenciesSection({ t }: { t: Tendencies }) {
  const s = t.tendencies
  if (s.total === 0 && !t.profile) return null
  const delta = s.avg_word_delta
  return (
    <Section
      storageKey="review.tendencies"
      title="Edit tendencies"
      count={s.total}
      summary={`${s.shortened} shortened · ${s.expanded} expanded · ${s.rephrased} rephrased`}
    >
      {s.total > 0 && (
        <p data-testid="tendency-stats">
          {s.total} of your edits: {s.shortened} shortened, {s.expanded} expanded, {s.rephrased} rephrased. Average
          change {delta > 0 ? '+' : ''}
          {delta.toFixed(1)} words per line.
        </p>
      )}
      {t.profile && (
        <>
          <h4>Learned style ({t.scope.startsWith('series:') ? 'this series' : 'all projects'})</h4>
          {t.profile.summary && <p>{t.profile.summary}</p>}
          <ul>
            {t.profile.preferences.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
          <p className="muted">
            From {t.profile.sample_count} edits{t.profile.updated_at ? ` · ${t.profile.updated_at}` : ''}
          </p>
        </>
      )}
    </Section>
  )
}

const versionName = (v: { id: number; label: string | null }) => v.label || `Version ${v.id}`

// Pick two saved translations and list the lines whose English differs. The
// line numbers are those saved with the versions, so they are shown, not
// opened.
function CompareSection({ dramaId, versions }: { dramaId: number; versions: VersionItem[] }) {
  const [left, setLeft] = useState(versions[1].id)
  const [right, setRight] = useState(versions[0].id)
  const [result, setResult] = useState<VersionCompare | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const run = () => {
    setBusy(true)
    compareVersions(dramaId, left, right)
      .then(
        (r) => {
          setError(null)
          setResult(r)
        },
        setError,
      )
      .finally(() => setBusy(false))
  }
  const options = versions.map((v) => (
    <option key={v.id} value={v.id}>
      {versionName(v)}
      {v.is_active ? ' (active)' : ''}
    </option>
  ))

  return (
    <Section storageKey="review.compare" title="Compare versions" count={versions.length} summary="pick two saved translations">
      <div className="review-edit-row review-compare-pick">
        <Field label="Older">
          <select value={left} onChange={(e) => { setLeft(Number(e.target.value)); setResult(null) }}>{options}</select>
        </Field>
        <Field label="Newer">
          <select value={right} onChange={(e) => { setRight(Number(e.target.value)); setResult(null) }}>{options}</select>
        </Field>
      </div>
      <div className="review-actions">
        <button type="button" disabled={busy || left === right} onClick={run}>
          {busy ? 'Comparing…' : 'Show differences'}
        </button>
        {left === right && <span className="muted">Pick two different versions.</span>}
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {result && (
        <>
          <p role="status" data-testid="compare-count">
            {result.diff_count === 0
              ? 'No differences.'
              : `${result.diff_count} of ${result.left_line_count} lines differ.`}
          </p>
          {result.diffs.length > 0 && (
            <ul className="review-diffs" data-testid="compare-list">
              {result.diffs.map((d) => (
                <li key={d.idx}>
                  <span className="review-idx">#{lineNumber(d.idx)}</span> <span lang="zh">{d.zh}</span>
                  <div className="review-diff-pair">
                    <del>{d.left_en || '(blank)'}</del>
                    <ins>{d.right_en || '(blank)'}</ins>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </Section>
  )
}
