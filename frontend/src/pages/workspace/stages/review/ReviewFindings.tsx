import { useEffect, useState } from 'react'

import { getConsistency, getEmotions, searchLines } from '../../../../api/review'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Section } from '../../../../components/Section'
import { lineNumber } from '../../../../lineNumber'
import type { ConsistencyIssue, EmotionSummary, ReviewLine } from '../../../../types/review'
import { FindingList } from './FindingList'
import {
  emotionCounts,
  emotionLines,
  FINDINGS_SHOWN,
  mergeSearchHits,
  type Finding,
  type GoToLine,
} from './reviewResults'

interface Props {
  dramaId: number
  reloads: number
  onGoTo: GoToLine
}

// What the last consistency check and emotion tagging found (stored results,
// refetched after every job). Nothing stored, nothing shown.
export function ReviewFindings({ dramaId, reloads, onGoTo }: Props) {
  const [issues, setIssues] = useState<ConsistencyIssue[]>([])
  const [emotions, setEmotions] = useState<EmotionSummary | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([getConsistency(dramaId), getEmotions(dramaId)]).then(
      ([c, e]) => {
        if (cancelled) return
        setError(null)
        setIssues(c)
        setEmotions(e)
      },
      (e: unknown) => !cancelled && setError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, reloads])

  return (
    <>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {issues.length > 0 && <ConsistencySection dramaId={dramaId} issues={issues} onGoTo={onGoTo} />}
      {emotions && emotions.total > 0 && <EmotionSection summary={emotions} onGoTo={onGoTo} />}
    </>
  )
}

function ConsistencySection({ dramaId, issues, onGoTo }: { dramaId: number; issues: ConsistencyIssue[]; onGoTo: GoToLine }) {
  return (
    <Section
      storageKey="review.consistency"
      title="Consistency"
      count={issues.length}
      summary={issues.slice(0, 3).map((i) => i.term).join(' · ')}
    >
      <ul className="review-issues" data-testid="consistency-list">
        {issues.map((i) => (
          <ConsistencyItem key={i.id} dramaId={dramaId} issue={i} onGoTo={onGoTo} />
        ))}
      </ul>
    </Section>
  )
}

// An issue names a term, not a line: "Show lines" searches for the term and
// each variant and lists the matching lines (by id).
function ConsistencyItem({ dramaId, issue, onGoTo }: { dramaId: number; issue: ConsistencyIssue; onGoTo: GoToLine }) {
  const [hits, setHits] = useState<ReviewLine[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const words = [...new Set([issue.term, ...issue.variants].map((w) => w.trim()).filter(Boolean))].slice(0, 6)
  const show = () => {
    setBusy(true)
    Promise.all(words.map((w) => searchLines(dramaId, w.slice(0, 500))))
      .then(
        (r) => {
          setError(null)
          setHits(mergeSearchHits(r))
        },
        setError,
      )
      .finally(() => setBusy(false))
  }
  const findings: Finding[] = (hits ?? []).map((l) => ({
    key: String(l.id),
    lineId: l.id,
    where: `#${lineNumber(l.idx)}`,
    text: l.en || l.zh,
  }))

  return (
    <li>
      <strong lang="zh">{issue.term}</strong>{' '}
      {issue.variants.length > 0 && <span>→ {issue.variants.join(' / ')}</span>}
      {issue.note && <div className="muted">{issue.note}</div>}
      {hits === null ? (
        <button type="button" className="link review-jump" disabled={busy || words.length === 0} onClick={show}>
          {busy ? 'Finding lines…' : 'Show lines'}
        </button>
      ) : hits.length === 0 ? (
        <p className="muted">No line uses these words now.</p>
      ) : (
        <FindingList items={findings} onGoTo={onGoTo} />
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </li>
  )
}

function EmotionSection({ summary, onGoTo }: { summary: EmotionSummary; onGoTo: GoToLine }) {
  const [all, setAll] = useState(false)
  const lines = emotionLines(summary)
  const shown = all ? lines : lines.slice(0, FINDINGS_SHOWN)
  const counts = emotionCounts(summary)
  return (
    <Section storageKey="review.emotion" title="Emotion" count={summary.total} summary={counts}>
      <p className="muted">
        {counts}
        {summary.high_risk > 0 && ` · ${summary.high_risk} strong`}
      </p>
      <ul className="review-findings" data-testid="emotion-list">
        {shown.map((t) => (
          <li key={t.line_idx}>
            <button
              type="button"
              className="link review-jump"
              title="Open this line in the editor"
              onClick={() => onGoTo({ lineNumber: lineNumber(t.line_idx) })}
            >
              #{lineNumber(t.line_idx)}
            </button>{' '}
            <span className="review-finding-text">
              <strong>{t.emotion}</strong>
              {t.intensity !== null && ` (${t.intensity})`}
              {t.note && ` · ${t.note}`}
            </span>
          </li>
        ))}
      </ul>
      {lines.length > shown.length && (
        <button type="button" className="link review-jump" onClick={() => setAll(true)}>
          Show all {lines.length}
        </button>
      )}
    </Section>
  )
}
