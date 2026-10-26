import { useId, useState } from 'react'

import { getGlossaryNameClusters, getGlossaryVariants, saveGlossaryTerm } from '../../../api/translateStage'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { Field } from '../../../components/Field'
import { buttonClass } from '../../../components/uiClasses'
import { lineNumber } from '../../../lineNumber'
import type {
  GlossaryNameCluster,
  GlossaryNameClusters,
  GlossaryTerm,
  GlossaryVariantExample,
  GlossaryVariants,
} from '../../../types/translateStage'
import { useStage } from '../StageContext'
import { aliasBlocker, aliasesWith, clusterBlocker, clusterTermBody, matchLabel, sourceLabel } from './glossaryVariantsModel'
import './glossaryVariants.css'
import { useCancellableEffect } from '../../../hooks/useCancellableEffect'

function Examples({ items }: { items: GlossaryVariantExample[] }) {
  if (!items.length) return null
  return (
    <ul className="glossary-variants-examples muted" aria-label="Example lines">
      {items.map((e, i) => (
        <li key={`${e.line_idx}-${i}`}>#{lineNumber(e.line_idx)} {e.snippet}</li>
      ))}
    </ul>
  )
}

function Unavailable({ reason }: { reason: string }) {
  return <p role="status" className="muted">Not available here: {reason}</p>
}

// Lists spellings in the source lines that sound like the term. Nothing is added
// until the person confirms one; the add uses the normal term update.
export function SimilarSpellings({ term, onAdded, onClose }: {
  term: GlossaryTerm
  onAdded: () => void
  onClose: () => void
}) {
  const { dramaId } = useStage()
  const [result, setResult] = useState<GlossaryVariants | null>(null)
  const [aliases, setAliases] = useState(term.aliases)
  const [error, setError] = useState<unknown>(null)
  const [confirming, setConfirming] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [added, setAdded] = useState<string[]>([])

  useCancellableEffect((cancelled) => {
    getGlossaryVariants(dramaId, term.term_original).then(
      (r) => !cancelled() && setResult(r),
      (e: unknown) => !cancelled() && setError(e),
    )
  }, [dramaId, term.term_original])

  const add = (text: string) => {
    setSaving(true)
    saveGlossaryTerm(dramaId, { id: term.id, aliases: aliasesWith(aliases, text) })
      .then(
        (saved) => {
          setError(null)
          setAliases(saved.aliases)
          setAdded((cur) => [...cur, text])
          setConfirming(null)
          onAdded()
        },
        setError,
      )
      .finally(() => setSaving(false))
  }

  return (
    <div className="glossary-variants" role="region" aria-label={`Similar spellings of ${term.term_original}`}>
      <h2 className="subhead">Similar spellings of “{term.term_original}”</h2>
      <p className="muted">
        Spellings in this title’s source lines that sound alike. Nothing is added until you confirm it.
      </p>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {!result && !error && <p role="status">Looking…</p>}
      {result && !result.available && <Unavailable reason={result.reason} />}
      {result?.available && result.candidates.length === 0 && (
        <p role="status">No other spellings found in {result.lines_scanned} lines.</p>
      )}
      {result?.available && result.truncated && (
        <p className="muted">Only the first {result.lines_scanned} lines were checked.</p>
      )}
      {result?.available && result.candidates.length > 0 && (
        <ul className="glossary-variants-list">
          {result.candidates.map((c) => {
            const blocker = aliasBlocker({ is_alias: c.is_alias || added.includes(c.text), is_term: c.is_term })
            return (
              <li key={c.text}>
                <div>
                  <strong>{c.text}</strong>{' '}
                  <span className="muted">
                    {c.count} mention{c.count === 1 ? '' : 's'} · {matchLabel(c.score)}
                  </span>
                </div>
                <Examples items={c.examples} />
                {confirming === c.text ? (
                  <div className="actions" role="group" aria-label={`Confirm alias ${c.text}`}>
                    <span role="alert">Add “{c.text}” as an alias of “{term.term_original}”?</span>
                    <button type="button" className={buttonClass('primary', 'sm')} disabled={saving} onClick={() => add(c.text)}>
                      {saving ? 'Adding…' : 'Yes, add alias'}
                    </button>
                    <button type="button" className={buttonClass('secondary', 'sm')} disabled={saving} onClick={() => setConfirming(null)}>
                      Cancel
                    </button>
                  </div>
                ) : blocker ? (
                  <span className="muted">{added.includes(c.text) ? 'Added as an alias.' : blocker}</span>
                ) : (
                  <div className="actions">
                    <button
                      type="button"
                      className={buttonClass('secondary', 'sm')}
                      title="Future glossary matches will count this spelling as the same term"
                      aria-label={`Add ${c.text} as alias`}
                      onClick={() => setConfirming(c.text)}
                    >
                      Add as alias
                    </button>
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}
      <div className="actions">
        <button type="button" className={buttonClass('ghost', 'sm')} onClick={onClose}>Close</button>
      </div>
    </div>
  )
}

function ClusterCard({ cluster, hasSeries, onCreated }: {
  cluster: GlossaryNameCluster
  hasSeries: boolean
  onCreated: (cluster: GlossaryNameCluster) => void
}) {
  const { dramaId } = useStage()
  const id = useId()
  const [open, setOpen] = useState(false)
  const [english, setEnglish] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const blocker = clusterBlocker(cluster, english, hasSeries)
  const create = () => {
    setSaving(true)
    saveGlossaryTerm(dramaId, clusterTermBody(cluster, english))
      .then(() => onCreated(cluster), setError)
      .finally(() => setSaving(false))
  }
  const others = cluster.spellings.filter((s) => s.text !== cluster.suggested_original)
  return (
    <li>
      <div>
        <strong>{cluster.spellings.map((s) => s.text).join(' · ')}</strong>{' '}
        <span className="muted">
          {cluster.total} mention{cluster.total === 1 ? '' : 's'}
          {cluster.spellings.length > 1 ? ` · ${cluster.spellings.length} spellings, may be one name` : ''}
        </span>
      </div>
      <div className="muted">
        {cluster.spellings.map((s) => `${s.text}: ${s.count}×${s.sources.length ? ` (${s.sources.map(sourceLabel).join(', ')})` : ''}`).join('; ')}
      </div>
      <Examples items={cluster.examples} />
      {cluster.existing_term && <span className="muted">Already in the glossary as “{cluster.existing_term}”.</span>}
      {!open ? (
        <div className="actions">
          <button
            type="button"
            className={buttonClass('secondary', 'sm')}
            disabled={!hasSeries || !!cluster.existing_term}
            title="Name this person in English and save the spellings as one glossary term"
            aria-label={`Make a term from ${cluster.suggested_original}`}
            onClick={() => setOpen(true)}
          >
            Make a term
          </button>
          {!hasSeries && <span className="muted">Still needed: a series (above).</span>}
        </div>
      ) : (
        <div className="glossary-variants-form">
          <Field label="English name" help="What the glossary should translate this name to.">
            <input id={id} value={english} onChange={(e) => setEnglish(e.target.value)} />
          </Field>
          <p className="muted">
            Original “{cluster.suggested_original}”{others.length ? `, aliases ${others.map((s) => s.text).join(', ')}` : ', no aliases'}.
          </p>
          <ErrorBanner error={error} onDismiss={() => setError(null)} />
          <div className="actions">
            <button type="button" className={buttonClass('primary', 'sm')} disabled={!!blocker || saving} onClick={create}>
              {saving ? 'Creating…' : 'Create term'}
            </button>
            <button type="button" className={buttonClass('secondary', 'sm')} disabled={saving} onClick={() => setOpen(false)}>
              Cancel
            </button>
            {blocker && <span className="muted">{blocker}</span>}
          </div>
        </div>
      )}
    </li>
  )
}

// Names that recur under several spellings. It runs only when asked (the scan reads
// every line) and creates nothing until the person names one and presses Create term.
export function PossibleNames({ hasSeries, onCreated }: { hasSeries: boolean; onCreated: () => void }) {
  const { dramaId } = useStage()
  const [result, setResult] = useState<GlossaryNameClusters | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const scan = () => {
    setLoading(true)
    getGlossaryNameClusters(dramaId)
      .then((r) => { setError(null); setResult(r) }, setError)
      .finally(() => setLoading(false))
  }
  return (
    <div className="glossary-variants" role="region" aria-label="Possible names">
      <div className="actions">
        <button
          type="button"
          className={buttonClass('secondary')}
          disabled={loading}
          title="Looks through the source lines for names that are written several ways. Free; nothing is changed."
          onClick={scan}
        >
          {loading ? 'Looking…' : result ? 'Look again' : 'Find possible names'}
        </button>
        <span className="muted">Finds names written several slightly different ways. Free, and nothing changes until you create a term.</span>
      </div>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {result && !result.available && <Unavailable reason={result.reason} />}
      {result?.available && result.truncated && <p className="muted">Only the first {result.lines_scanned} lines were checked.</p>}
      {result?.available && result.clusters.length === 0 && <p role="status">No recurring names found yet.</p>}
      {result && result.known_vtuber_names.length > 0 && (
        <>
          <h2 className="subhead">Known VTuber names</h2>
          <p className="muted">From your downloaded VTuber list, found in this title’s lines. Suggestions only; nothing is added until you make a term.</p>
          <ul className="glossary-variants-list">
            {result.known_vtuber_names.map((c) => (
              <ClusterCard
                key={c.suggested_original}
                cluster={c}
                hasSeries={hasSeries}
                onCreated={(done) => {
                  setResult((cur) => cur && { ...cur, known_vtuber_names: cur.known_vtuber_names.filter((x) => x !== done) })
                  onCreated()
                }}
              />
            ))}
          </ul>
        </>
      )}
      {result?.available && result.clusters.length > 0 && (
        <ul className="glossary-variants-list">
          {result.clusters.map((c) => (
            <ClusterCard
              key={c.suggested_original}
              cluster={c}
              hasSeries={hasSeries}
              onCreated={(done) => {
                setResult((cur) => cur && { ...cur, clusters: cur.clusters.filter((x) => x !== done) })
                onCreated()
              }}
            />
          ))}
        </ul>
      )}
    </div>
  )
}
