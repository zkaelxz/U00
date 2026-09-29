import { useEffect, useState } from 'react'

import { getConsistency, getEmotions, searchLines } from '../../../../api/review'
import { ErrorBanner } from '../../../../components/ErrorBanner'
import { Section } from '../../../../components/Section'
import { buttonClass } from '../../../../components/uiClasses'
import { lineNumber } from '../../../../lineNumber'
import type { ConsistencyIssue, EmotionSummary, ReviewLine } from '../../../../types/review'
import { FindingList, FindingRow } from './FindingList'
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
  // Bumped when a review job finishes. Consistency issues name terms, not
  // lines, and only a job changes them.
  jobsDone: number
  // Bumped after every save, structure edit or job. Emotion tags come back
  // keyed by each line's CURRENT position, so they are refetched after any
  // change that can move lines; open "Show lines" searches re-run too.
  reloads: number
  onGoTo: GoToLine
}

// What the last consistency check and emotion tagging found (stored
// results). Nothing stored, nothing shown; one failed request does not hide
// the other's results.
export function ReviewFindings({ dramaId, jobsDone, reloads, onGoTo }: Props) {
  const [issues, setIssues] = useState<ConsistencyIssue[]>([])
  const [emotions, setEmotions] = useState<EmotionSummary | null>(null)
  const [issuesError, setIssuesError] = useState<unknown>(null)
  const [emotionsError, setEmotionsError] = useState<unknown>(null)

  useEffect(() => {
    let cancelled = false
    getConsistency(dramaId).then(
      (c) => {
        if (cancelled) return
        setIssuesError(null)
        setIssues(c)
      },
      (e: unknown) => !cancelled && setIssuesError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, jobsDone])

  useEffect(() => {
    let cancelled = false
    getEmotions(dramaId).then(
      (e) => {
        if (cancelled) return
        setEmotionsError(null)
        setEmotions(e)
      },
      (e: unknown) => !cancelled && setEmotionsError(e),
    )
    return () => {
      cancelled = true
    }
  }, [dramaId, reloads])

  return (
    <>
      <ErrorBanner error={issuesError} onDismiss={() => setIssuesError(null)} />
      <ErrorBanner error={emotionsError} onDismiss={() => setEmotionsError(null)} />
      {issues.length > 0 && <ConsistencySection dramaId={dramaId} issues={issues} reloads={reloads} onGoTo={onGoTo} />}
      {emotions && emotions.total > 0 && <EmotionSection summary={emotions} onGoTo={onGoTo} />}
    </>
  )
}

function ConsistencySection({ dramaId, issues, reloads, onGoTo }: { dramaId: number; issues: ConsistencyIssue[]; reloads: number; onGoTo: GoToLine }) {
  return (
    <Section
      storageKey="review.consistency"
      title="Consistency"
      count={issues.length}
      summary={issues.slice(0, 3).map((i) => i.term).join(' · ')}
    >
      <ul className="review-issues" data-testid="consistency-list">
        {issues.map((i) => (
          <ConsistencyItem key={i.id} dramaId={dramaId} issue={i} reloads={reloads} onGoTo={onGoTo} />
        ))}
      </ul>
    </Section>
  )
}

// An issue names a term, not a line: "Show lines" searches for the term and
// each variant and lists the matching lines (by id). Once shown, the search
// re-runs after every change, so line numbers and text stay current.
function ConsistencyItem({ dramaId, issue, reloads, onGoTo }: { dramaId: number; issue: ConsistencyIssue; reloads: number; onGoTo: GoToLine }) {
  const [hits, setHits] = useState<ReviewLine[] | null>(null)
  const [shown, setShown] = useState(false)
  const [tries, setTries] = useState(0)
  const [error, setError] = useState<unknown>(null)

  const wordsKey = JSON.stringify([...new Set([issue.term, ...issue.variants].map((w) => w.trim()).filter(Boolean))].slice(0, 6))
  useEffect(() => {
    if (!shown) return
    const words: string[] = JSON.parse(wordsKey)
    let cancelled = false
    Promise.all(words.map((w) => searchLines(dramaId, w.slice(0, 500))))
      .then(
        (r) => {
          if (cancelled) return
          setError(null)
          setHits(mergeSearchHits(r))
        },
        (e: unknown) => !cancelled && setError(e),
      )
    return () => {
      cancelled = true
    }
  }, [shown, dramaId, wordsKey, reloads, tries])
  const noWords = wordsKey === '[]'
  // Only the first search shows as busy; refreshes keep the list in place.
  const busy = shown && hits === null && !error
  const findings: Finding[] = (hits ?? []).map((l) => ({
    key: String(l.id),
    lineId: l.id,
    where: `#${lineNumber(l.idx)}`,
    text: l.en || l.zh,
  }))

  return (
    <li>
      <strong lang="zh">{issue.term}</strong>
      {issue.variants.length > 0 && <span> → {issue.variants.join(' / ')}</span>}
      {issue.note && <div className="muted">{issue.note}</div>}
      {hits === null ? (
        <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy || noWords} onClick={() => {
            setError(null)
            setShown(true)
            setTries((n) => n + 1)
          }}>
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
      {summary.high_risk > 0 && <p className="muted">{summary.high_risk} strong</p>}
      <ul className="review-findings" data-testid="emotion-list">
        {shown.map((t) => (
          <FindingRow
            key={t.line_idx}
            where={`#${lineNumber(t.line_idx)}`}
            target={{ lineNumber: lineNumber(t.line_idx) }}
            title={`Opens line #${lineNumber(t.line_idx)}`}
            onGoTo={onGoTo}
          >
            <strong>{t.emotion}</strong>
            {t.intensity !== null && ` (${t.intensity})`}
            {t.note && ` · ${t.note}`}
          </FindingRow>
        ))}
      </ul>
      {lines.length > shown.length && (
        <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => setAll(true)}>
          Show all {lines.length}
        </button>
      )}
    </Section>
  )
}
