import { useId, useState } from 'react'

import {
  cancelNameReport,
  getGlossaryTerms,
  getNameReport,
  getNameReportText,
  markNameGroup,
  saveGlossaryTerm,
  startNameReport,
} from '../../../api/translateStage'
import { copyText } from '../../../components/clipboard'
import { describeError, isForbidden, LINES_READ_FORBIDDEN } from '../../../components/errorMessages'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Section } from '../../../components/Section'
import { buttonClass } from '../../../components/uiClasses'
import { lineNumber } from '../../../lineNumber'
import type { NameMark, NameReport, NameReportGroup } from '../../../types/translateStage'
import { isActiveStatus } from './autotuneGlossary'
import { groupsForTab, MARK_HINTS, NAME_TABS, planApply, scanNotes, tabCount, type NameTab } from './nameReportModel'
import { useRunStatus } from './useRunStatus'
import './nameReport.css'

const loadNameReport = () => getNameReport()
const MARK_LABELS: Record<NameMark, string> = { right: 'Right', wrong: 'Wrong', skip: 'Skip' }
const EMPTY_TEXT: Record<NameTab, string> = {
  open: 'No names left to check. Run the check again after adding titles or lines.',
  right: 'Nothing marked right yet.',
  wrong: 'Nothing marked wrong yet.',
  skip: 'Nothing skipped.',
}

interface Outcome { id: number; title: string; text: string; failed: boolean }

export function ApplyForm({ group, onDone }: { group: NameReportGroup; onDone: () => void }) {
  const englishId = useId()
  const [picked, setPicked] = useState<number[]>(group.titles.map((t) => t.drama_id))
  const [english, setEnglish] = useState(group.titles.find((t) => t.glossary_english)?.glossary_english ?? '')
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [outcomes, setOutcomes] = useState<Outcome[]>([])

  const toggle = (id: number) => setPicked((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]))
  const apply = async () => {
    setBusy(true)
    const results: Outcome[] = []
    // One title at a time through the normal term routes, so each title's own permission
    // and series rules apply and one refusal does not stop the rest.
    for (const t of group.titles.filter((x) => picked.includes(x.drama_id))) {
      try {
        const plan = planApply(await getGlossaryTerms(t.drama_id), group, english)
        if (plan.kind === 'covered') results.push({ id: t.drama_id, title: t.title, text: 'Already in the glossary.', failed: false })
        else if (plan.kind === 'needs-english') results.push({ id: t.drama_id, title: t.title, text: 'Not added: type the English name first.', failed: true })
        else {
          await saveGlossaryTerm(t.drama_id, plan.body)
          results.push({ id: t.drama_id, title: t.title, text: plan.kind === 'create' ? 'Term added.' : 'Aliases added.', failed: false })
        }
      } catch (e) {
        results.push({ id: t.drama_id, title: t.title, text: `Not added: ${describeError(e).title}`, failed: true })
      }
    }
    setOutcomes(results)
    setConfirming(false)
    setBusy(false)
    onDone()
  }

  return (
    <div className="name-report-apply" role="group" aria-label={`Apply ${group.canonical} to titles`}>
      <fieldset disabled={busy}>
        <legend>Add “{group.canonical}” and its other spellings to these glossaries</legend>
        {group.titles.map((t) => (
          <label key={t.drama_id} className="name-report-check">
            <input type="checkbox" checked={picked.includes(t.drama_id)} onChange={() => toggle(t.drama_id)} />
            <span>{t.title}{t.in_glossary ? ' (already has it)' : ''}</span>
          </label>
        ))}
        {group.titles_total > group.titles.length && (
          <p className="muted">
            {group.titles_total - group.titles.length} more {group.titles_total - group.titles.length === 1 ? 'title has' : 'titles have'} this name; add it to those from their own glossary.
          </p>
        )}
        <label htmlFor={englishId}>English name (used when a title has no term for it yet)</label>
        <input id={englishId} value={english} maxLength={80} onChange={(e) => setEnglish(e.target.value)} />
      </fieldset>
      {confirming ? (
        <div className="actions" role="group" aria-label="Confirm adding to glossaries">
          <span role="alert">Add to {picked.length} glossar{picked.length === 1 ? 'y' : 'ies'}? Existing terms keep their English; only aliases are added.</span>
          <button type="button" className={buttonClass('primary', 'sm')} disabled={busy} onClick={() => void apply()}>
            {busy ? 'Adding…' : 'Yes, add'}
          </button>
          <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy} onClick={() => setConfirming(false)}>Cancel</button>
        </div>
      ) : (
        <div className="actions">
          <button
            type="button"
            className={buttonClass('secondary', 'sm')}
            disabled={!picked.length}
            title={picked.length ? 'Shows what will be added before anything is saved' : 'Pick at least one title'}
            onClick={() => setConfirming(true)}
          >
            Add to {picked.length} title{picked.length === 1 ? '' : 's'}…
          </button>
          {!picked.length && <span className="muted">Pick at least one title.</span>}
        </div>
      )}
      {outcomes.length > 0 && (
        <ul className="name-report-outcomes" aria-label="Results">
          {outcomes.map((o) => (
            <li key={o.id} className={o.failed ? undefined : 'muted'}>{o.title}: {o.text}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

export function GroupCard({ group, busy, onMark, onApplied }: {
  group: NameReportGroup
  busy: boolean
  onMark: (mark: NameMark | 'clear') => void
  onApplied: () => void
}) {
  const [applying, setApplying] = useState(false)
  const titleName = (id: number) => group.titles.find((t) => t.drama_id === id)?.title ?? `Title ${id}`
  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`
  return (
    <li className="name-report-group" aria-label={`Name ${group.canonical}`}>
      <div>
        <strong>{group.canonical}</strong>{' '}
        <span className="muted">{plural(group.titles_total, 'title')} · {plural(group.total, 'mention')}</span>
      </div>
      <ul className="name-report-variants" aria-label="Spellings">
        {group.variants.map((v) => <li key={v.text}>{v.text} <span className="muted">×{v.count}</span></li>)}
      </ul>
      <ul className="name-report-titles" aria-label="Titles">
        {group.titles.map((t) => (
          <li key={t.drama_id}>
            {t.title} <span className="muted">
              · {plural(t.count, 'mention')} · {t.in_glossary
                ? `in glossary as “${t.glossary_term}”${t.glossary_english ? ` → “${t.glossary_english}”` : ''}`
                : 'not in glossary'}
            </span>
          </li>
        ))}
        {group.titles_total > group.titles.length && <li className="muted">and {group.titles_total - group.titles.length} more</li>}
      </ul>
      {group.examples.length > 0 && (
        <ul className="name-report-examples muted" aria-label="Example lines">
          {group.examples.map((e, i) => (
            <li key={`${e.drama_id}-${e.line_idx}-${i}`}>{titleName(e.drama_id)} #{lineNumber(e.line_idx)}: {e.snippet}</li>
          ))}
        </ul>
      )}
      <div className="actions name-report-actions">
        {(['right', 'wrong', 'skip'] as const).map((m) => (
          <button
            key={m}
            type="button"
            className={buttonClass(group.mark === m ? 'primary' : 'secondary', 'sm')}
            aria-pressed={group.mark === m}
            aria-label={`Mark ${group.canonical} ${m}`}
            title={MARK_HINTS[m]}
            disabled={busy}
            onClick={() => onMark(m)}
          >
            {MARK_LABELS[m]}
          </button>
        ))}
        {group.mark && (
          <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy} title="Remove the mark so it shows under To check again" onClick={() => onMark('clear')}>
            Undo mark
          </button>
        )}
        <button
          type="button"
          className={buttonClass('secondary', 'sm')}
          disabled={group.mark !== 'right'}
          aria-describedby={group.mark !== 'right' ? `name-report-apply-${group.key}` : undefined}
          title="Add this name and its spellings to the glossaries of the titles you pick"
          aria-expanded={applying}
          onClick={() => setApplying((v) => !v)}
        >
          Apply to titles…
        </button>
        {group.mark !== 'right' && <span className="muted" id={`name-report-apply-${group.key}`}>Mark it Right first.</span>}
      </div>
      {applying && group.mark === 'right' && <ApplyForm group={group} onDone={onApplied} />}
    </li>
  )
}

// "Check names across all titles": the same name heard or rendered differently from one title
// to the next, grouped so each group can be marked right, wrong or skipped in one place. The scan
// is read-only; adding to glossaries goes through the normal term routes, title by title.
export function NameReportPanel({ onClose }: { onClose: () => void }) {
  const run = useRunStatus(0, loadNameReport)
  const status = run.status
  const active = isActiveStatus(status?.status)
  const [tab, setTab] = useState<NameTab>('open')
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)
  const [fallbackText, setFallbackText] = useState<string | null>(null)

  const report: NameReport | null = status?.status === 'done' ? status.result : null
  const noRead = isForbidden(run.error)
  const failed = status?.status === 'error' || status?.status === 'cancelled'
  const visible = report ? groupsForTab(report, tab) : []

  const act = (fn: () => Promise<unknown>, after?: () => void) => {
    setBusy(true)
    setError(null)
    fn().then(() => { after?.() }, setError).finally(() => setBusy(false))
  }
  const start = () => act(startNameReport, run.refresh)
  const cancel = () => act(cancelNameReport, run.refresh)
  const mark = (g: NameReportGroup, m: NameMark | 'clear') => act(() => markNameGroup(g.key, m), run.refresh)
  const copy = () => {
    setCopied(null)
    setFallbackText(null)
    act(async () => {
      const text = await getNameReportText()
      if (await copyText(text)) setCopied('Copied the unresolved names.')
      else setFallbackText(text)
    })
  }

  return (
    <div className="name-report" role="region" aria-label="Name check across all titles">
      <h4>Name check across all titles</h4>
      <p className="muted">
        Looks through the source lines of every title you can see for a name that is heard or written
        differently from one title to the next. The check changes nothing; a group is added to a glossary
        only after you mark it Right and confirm.
      </p>
      {noRead && <p className="muted" role="status">{LINES_READ_FORBIDDEN}</p>}
      <ErrorBanner error={noRead ? null : run.error} onDismiss={run.clearError} />
      {isForbidden(error)
        ? <p className="muted" role="status">Your account cannot do that here. Ask the owner of the main PC.</p>
        : <ErrorBanner error={error} onDismiss={() => setError(null)} />}
      <div className="actions">
        <button
          type="button"
          className={buttonClass('primary', 'sm')}
          disabled={busy || active || noRead}
          aria-describedby={active || noRead ? 'name-report-run-reason' : undefined}
          title="Reads every title's source lines; nothing is saved"
          onClick={start}
        >
          {report || failed ? 'Check again' : 'Check names'}
        </button>
        {active && <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy} onClick={cancel}>Cancel</button>}
        <button
          type="button"
          className={buttonClass('secondary', 'sm')}
          disabled={busy || !report || report.counts.unresolved === 0}
          aria-describedby={!report || report.counts.unresolved === 0 ? 'name-report-copy-reason' : undefined}
          title="Copy the names still to check, with counts and example lines"
          onClick={copy}
        >
          Copy report
        </button>
        <button type="button" className={buttonClass('ghost', 'sm')} onClick={onClose}>Close</button>
      </div>
      {(active || noRead) && <p className="muted" id="name-report-run-reason">{noRead ? 'Reading lines is needed to run it.' : 'A check is already running.'}</p>}
      {!report && !active && <p className="muted" id="name-report-copy-reason">Run the check to get a report to copy.</p>}
      {report && report.counts.unresolved === 0 && <p className="muted" id="name-report-copy-reason">Nothing to copy: no names left to check.</p>}
      {copied && <p role="status" className="muted">{copied}</p>}
      {fallbackText !== null && (
        <label className="name-report-fallback">Could not copy automatically. Select and copy this text:
          <textarea readOnly rows={6} value={fallbackText} onFocus={(e) => e.currentTarget.select()} />
        </label>
      )}
      {!run.loaded && !run.error && <p role="status" className="muted">Loading…</p>}
      {active && (
        <p role="status">
          {status?.message || 'Checking…'}
          {typeof status?.progress === 'number' ? ` ${Math.round(Math.min(Math.max(status.progress, 0), 1) * 100)}%` : ''}
        </p>
      )}
      {failed && <p role="status" className="muted">{status?.status === 'cancelled' ? 'Cancelled. Nothing was changed.' : status?.message}</p>}
      {run.loaded && !status && !run.error && <p className="muted">No check has been run yet. It reads the titles you can see and takes a little while for a big library.</p>}
      {report && (
        <>
          <Section
            title="What was checked"
            summary={`${report.titles_scanned} of ${report.titles_total} titles`}
          >
            <p>Checked {report.titles_scanned} of {report.titles_total} titles. Names found in only one title under one spelling are left to the per-title check ({report.singletons_omitted} such).</p>
            {scanNotes(report).map((n) => <p key={n} className="muted">{n}</p>)}
          </Section>
          {/* A filter, not tabs: the Glossary panel already has the page's one tab level. */}
          <div className="actions" role="group" aria-label="Name groups">
            {NAME_TABS.map((t) => (
              <button
                key={t.id}
                type="button"
                className={buttonClass(t.id === tab ? 'secondary' : 'ghost', 'sm')}
                aria-pressed={t.id === tab}
                onClick={() => setTab(t.id)}
              >
                {t.label} <span className="badge section-count">{tabCount(report, t.id)}</span>
              </button>
            ))}
          </div>
          <div>
            {visible.length === 0 ? (
              <p role="status" className="muted">{EMPTY_TEXT[tab]}</p>
            ) : (
              <ul className="name-report-list">
                {visible.map((g) => (
                  <GroupCard key={g.key} group={g} busy={busy} onMark={(m) => mark(g, m)} onApplied={() => undefined} />
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  )
}
