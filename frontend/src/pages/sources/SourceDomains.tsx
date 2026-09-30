import { useCallback, useEffect, useId, useState } from 'react'

import {
  confirmDomainProposal,
  dismissDomainProposal,
  isMissingRoute,
  listDomainProposals,
  listSourceDomains,
  resetSourceDomains,
  saveSourceDomains,
} from '../../api/sourceDomains'
import { Badge } from '../../components/Badge'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import type { SourceDomainEntry, SourceDomainProposal } from '../../types/sourceDomains'
import {
  addDomain,
  domainMarker,
  domainsSummary,
  foundAtText,
  moveDomain,
  pendingTotal,
  removeDomain,
  sameList,
} from './sourceDomainsModel'

const CONFIRM_WARNING = 'Confirming makes requests for this source go to that host. Only confirm an address you recognise.'
const DESCRIBE = { pcOnly: true, serverText: true }

/** Per-source address lists (PC only). Renders nothing when the server has no such routes. */
export function SourceDomains() {
  const [entries, setEntries] = useState<SourceDomainEntry[] | null>(null)
  const [proposals, setProposals] = useState<SourceDomainProposal[]>([])
  const [error, setError] = useState<unknown>(null)

  const load = useCallback(async () => {
    try {
      const [e, p] = await Promise.all([listSourceDomains(), listDomainProposals()])
      setEntries(e)
      setProposals(p)
    } catch (err) {
      // An older server has no such routes: hide the card.
      if (!isMissingRoute(err)) setError(err)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  if (!entries) {
    return error ? <ErrorBanner error={error} onDismiss={() => setError(null)} describe={DESCRIBE} /> : null
  }
  const replace = (next: SourceDomainEntry) =>
    setEntries((cur) => (cur ? cur.map((e) => (e.source === next.source ? next : e)) : cur))

  return (
    <Section
      title="Source addresses"
      summary={domainsSummary(entries)}
      count={pendingTotal(entries) || undefined}
      storageKey="sources.domains"
    >
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={DESCRIBE} />
      {entries.map((e) => (
        <DomainRow
          key={e.source}
          entry={e}
          proposals={proposals.filter((p) => p.source === e.source)}
          onEntry={replace}
          onRefresh={load}
        />
      ))}
    </Section>
  )
}

function DomainRow({
  entry,
  proposals,
  onEntry,
  onRefresh,
}: {
  entry: SourceDomainEntry
  proposals: SourceDomainProposal[]
  onEntry: (e: SourceDomainEntry) => void
  onRefresh: () => Promise<void>
}) {
  const [draft, setDraft] = useState(entry.domains)
  const [text, setText] = useState('')
  const [problem, setProblem] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const inputId = useId()
  const dirty = !sameList(draft, entry.domains)

  // A saved list replaced from outside (confirm, reset) becomes the draft.
  useEffect(() => {
    setDraft(entry.domains)
  }, [entry.domains])

  async function run(job: () => Promise<void>) {
    setError(null)
    setBusy(true)
    try {
      await job()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const add = () => {
    const r = addDomain(draft, text)
    setProblem(r.error ?? '')
    if (!r.error) {
      setDraft(r.list)
      setText('')
    }
  }
  const save = () => run(async () => onEntry(await saveSourceDomains(entry.source, draft)))
  const reset = () => run(async () => onEntry(await resetSourceDomains(entry.source)))
  const confirm = (host: string) =>
    run(async () => {
      onEntry(await confirmDomainProposal(entry.source, host))
      await onRefresh()
    })
  const dismiss = (host: string) =>
    run(async () => {
      await dismissDomainProposal(entry.source, host)
      await onRefresh()
    })

  return (
    <div className="sources-subsection source-domains" role="group" aria-label={`${entry.display_name} addresses`}>
      <div className="source-card-line">
        <h4>{entry.display_name}</h4>
        <Badge tone={entry.customized ? 'info' : 'neutral'}>{domainMarker(entry)}</Badge>
        {entry.pending_proposals > 0 && <Badge tone="warn">{`${entry.pending_proposals} possible new`}</Badge>}
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={DESCRIBE} />

      <ol className="sources-rows source-domain-list" aria-label={`${entry.display_name} hosts, tried in this order`}>
        {draft.map((host, i) => (
          <li key={host}>
            <span>
              <code>{host}</code>
              {host === entry.last_good && <span className="muted"> · last worked</span>}
              {entry.default_domains.includes(host) && <span className="muted"> · default</span>}
            </span>
            <span className="actions">
              <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy || i === 0} aria-label={`Move ${host} up`} onClick={() => setDraft(moveDomain(draft, i, -1))}>
                Up
              </button>
              <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy || i === draft.length - 1} aria-label={`Move ${host} down`} onClick={() => setDraft(moveDomain(draft, i, 1))}>
                Down
              </button>
              <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy || draft.length <= 1} aria-label={`Remove ${host}`} onClick={() => setDraft(removeDomain(draft, i))}>
                Remove
              </button>
            </span>
          </li>
        ))}
      </ol>

      <div className="field-row source-domain-add">
        <label htmlFor={inputId} className="visually-hidden">{`Add a host for ${entry.display_name}`}</label>
        <input
          id={inputId}
          type="text"
          inputMode="url"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          placeholder="host.example or host.example:8080"
          value={text}
          aria-invalid={problem ? true : undefined}
          onChange={(ev) => {
            setText(ev.target.value)
            setProblem('')
          }}
          onKeyDown={(ev) => {
            if (ev.key === 'Enter') {
              ev.preventDefault()
              add()
            }
          }}
        />
        <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy || !text.trim()} onClick={add}>
          Add
        </button>
      </div>
      {problem && <p className="field-error" role="alert">{problem}</p>}

      <div className="actions">
        <button type="button" className={buttonClass('primary', 'sm')} disabled={busy || !dirty} onClick={save}>
          {busy ? 'Working…' : 'Save'}
        </button>
        {dirty && (
          <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy} onClick={() => setDraft(entry.domains)}>
            Discard changes
          </button>
        )}
        <ConfirmButton
          label="Reset to defaults…"
          name={`${entry.display_name} addresses`}
          verb="reset"
          confirmLabel="Confirm reset to defaults"
          ariaLabel={`Reset ${entry.display_name} to defaults`}
          busy={busy}
          disabled={!entry.customized}
          onConfirm={reset}
        />
      </div>

      {proposals.length > 0 && (
        <div role="group" aria-label={`Possible new address for ${entry.display_name}`} className="source-domain-proposals">
          <h5>Possible new address</h5>
          <p className="muted">{CONFIRM_WARNING}</p>
          <ul className="sources-rows">
            {proposals.map((p) => (
              <li key={p.host}>
                <span>
                  <code>{p.host}</code>
                  {foundAtText(p.found_at) && <span className="muted">{` · found ${foundAtText(p.found_at)}`}</span>}
                </span>
                <span className="actions">
                  <button type="button" className={buttonClass('primary', 'sm')} disabled={busy} aria-label={`Confirm ${p.host}`} onClick={() => confirm(p.host)}>
                    Confirm
                  </button>
                  <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy} aria-label={`Dismiss ${p.host}`} onClick={() => dismiss(p.host)}>
                    Dismiss
                  </button>
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
