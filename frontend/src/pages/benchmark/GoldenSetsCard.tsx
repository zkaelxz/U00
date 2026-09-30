import { useCallback, useEffect, useState, type SelectHTMLAttributes } from 'react'

import {
  createBenchmarkCase, deleteBenchmarkCase, getBenchmarkCases, importGoldenSet,
  type BenchmarkCase, type BenchmarkOptions, type BenchmarkSet, type BenchmarkTier, type ImportFormat,
  type SourceLanguage,
} from '../../api/benchmark'
import { Badge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { ConfirmButton } from '../../components/ConfirmButton'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { humanize } from '../../components/labels'
import { buttonClass } from '../../components/uiClasses'
import { PC_ONLY_BODY, PC_ONLY_DELETE_NOTE, PC_ONLY_SUMMARY, type PcMode } from '../../hooks/usePcOnly'
import { usePersistedState } from '../../hooks/usePersistedState'
import { TIER_HELP, TIER_LABELS, plainError, setDisplayName, stageLabel, tierLabel, tierTone } from './benchmarkForm'

type Props = {
  sets: BenchmarkSet[]
  options: BenchmarkOptions
  pc: PcMode
  phone: boolean
  onChanged: () => void
}

const setKey = (s: Pick<BenchmarkSet, 'stage' | 'tier' | 'set_name'>) => `${s.stage ?? ''}|${s.tier}|${s.set_name}`

/** Golden sets: one row per stage, tier and set; its cases on demand; import and add (PC only). */
export function GoldenSetsCard({ sets, options, pc, phone, onChanged }: Props) {
  const [openSet, setOpenSet] = useState<BenchmarkSet | null>(null)
  const total = sets.reduce((n, s) => n + s.case_count, 0)
  const shown = openSet && sets.find((s) => setKey(s) === setKey(openSet))

  return (
    <Card
      title="Golden sets"
      meta={sets.length ? `${sets.length} ${sets.length === 1 ? 'set' : 'sets'} · ${total} ${total === 1 ? 'case' : 'cases'}` : 'No cases yet'}
      className="bench-sets"
      aria-label="Golden sets"
    >
      {sets.length === 0 ? (
        <p className="muted">
          A golden set is a list of source lines with a reference answer. Import one below (a public test set, or your own
          corrected lines), then run engines against it.
        </p>
      ) : phone ? (
        <ul className="bench-set-cards" aria-label="Golden sets list">
          {sets.map((s) => (
            <li key={setKey(s)}>
              <div className="bench-set-title">
                <strong>{setDisplayName(s.set_name)}</strong>
                <Badge tone={tierTone(s.tier)}>{tierLabel(s.tier)}</Badge>
              </div>
              <p className="muted num">
                {stageLabel(s.stage)} · {s.case_count} cases · {s.with_reference} with reference
              </p>
              <ShowCasesButton set={s} open={!!shown && setKey(shown) === setKey(s)} onToggle={setOpenSet} />
            </li>
          ))}
        </ul>
      ) : (
        <div className="table-scroll">
          <table className="bench-table" aria-label="Golden sets list">
            <thead>
              <tr>
                <th>Set</th>
                <th>Tier</th>
                <th>Stage</th>
                <th className="num">Cases</th>
                <th className="num">With reference</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {sets.map((s) => (
                <tr key={setKey(s)}>
                  <td>{setDisplayName(s.set_name)}</td>
                  <td><Badge tone={tierTone(s.tier)}>{tierLabel(s.tier)}</Badge></td>
                  <td>{stageLabel(s.stage)}</td>
                  <td className="num">{s.case_count}</td>
                  <td className="num">{s.with_reference}</td>
                  <td className="bench-cell-action">
                    <ShowCasesButton set={s} open={!!shown && setKey(shown) === setKey(s)} onToggle={setOpenSet} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {shown && <CasesPanel key={setKey(shown)} set={shown} pc={pc} onChanged={onChanged} onClose={() => setOpenSet(null)} />}

      <ImportSection options={options} pc={pc} onDone={onChanged} />
      <AddCaseSection options={options} pc={pc} onDone={onChanged} />
    </Card>
  )
}

function ShowCasesButton({ set, open, onToggle }: { set: BenchmarkSet; open: boolean; onToggle: (s: BenchmarkSet | null) => void }) {
  return (
    <button
      type="button"
      className={buttonClass('ghost', 'sm')}
      aria-expanded={open}
      aria-label={`${open ? 'Hide' : 'Show'} cases in ${setDisplayName(set.set_name)} (${tierLabel(set.tier)})`}
      onClick={() => onToggle(open ? null : set)}
    >
      {open ? 'Hide cases' : 'Show cases'}
    </button>
  )
}

function CasesPanel({ set, pc, onChanged, onClose }: { set: BenchmarkSet; pc: PcMode; onChanged: () => void; onClose: () => void }) {
  const [cases, setCases] = useState<BenchmarkCase[] | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    getBenchmarkCases({ stage: set.stage, tier: set.tier, set_name: set.set_name }).then(
      // "(no set)" can't be asked for by name: the server reads an empty name as "any set".
      (r) => setCases(r.cases.filter((c) => c.set_name === set.set_name)),
      (e: unknown) => setError(plainError(e)),
    )
  }, [set.stage, set.tier, set.set_name])
  useEffect(load, [load])

  const remove = (c: BenchmarkCase) => {
    setBusyId(c.id)
    setError(null)
    deleteBenchmarkCase(c.id).then(
      () => {
        setBusyId(null)
        load()
        onChanged()
      },
      (e: unknown) => {
        setBusyId(null)
        setError(plainError(e, { pcOnly: true }))
      },
    )
  }

  return (
    <div className="bench-cases" aria-label={`Cases in ${setDisplayName(set.set_name)}`} role="region">
      <div className="bench-cases-head">
        <h4>
          {setDisplayName(set.set_name)} <Badge tone={tierTone(set.tier)}>{tierLabel(set.tier)}</Badge>
        </h4>
        <button type="button" className={buttonClass('ghost', 'sm')} onClick={onClose}>
          Close
        </button>
      </div>
      {cases === null && !error && <p className="muted">Loading…</p>}
      {cases && cases.length === 0 && <p className="muted">No cases left in this set.</p>}
      {cases && cases.length > 0 && (
        <ul className="bench-case-list">
          {cases.map((c) => (
            <li key={c.id}>
              <div className="bench-case-text">
                <strong>{c.label || `Case ${c.id}`}</strong>
                <span className="muted"> · {humanize('language', c.source_language)}</span>
                {c.source_text ? (
                  <p lang={c.source_language}>{c.source_text}</p>
                ) : (
                  <p className="muted">{c.has_input_file ? 'An audio or image file on the PC.' : 'No source text.'}</p>
                )}
                <p className={c.reference_text ? undefined : 'muted'}>
                  <span className="muted">Reference: </span>
                  {c.reference_text || 'none (this case is run but not scored)'}
                </p>
              </div>
              {pc !== 'remote' && (
                <ConfirmButton
                  name={c.label || `case ${c.id}`}
                  busy={busyId === c.id}
                  disabled={busyId !== null && busyId !== c.id}
                  onConfirm={() => remove(c)}
                />
              )}
            </li>
          ))}
        </ul>
      )}
      {pc === 'remote' && <p className="muted">{PC_ONLY_DELETE_NOTE}</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}

// Field clones its id and aria-describedby onto its child: pass them on to the <select>.
type SelectPassThrough = Omit<SelectHTMLAttributes<HTMLSelectElement>, 'value' | 'onChange'>

function LanguageSelect({ value, options, onChange, ...rest }: SelectPassThrough & { value: string; options: string[]; onChange: (v: SourceLanguage) => void }) {
  return (
    <select {...rest} value={value} onChange={(e) => onChange(e.target.value as SourceLanguage)}>
      {options.map((l) => (
        <option key={l} value={l}>{humanize('language', l)}</option>
      ))}
    </select>
  )
}

function TierSelect({ value, options, onChange, ...rest }: SelectPassThrough & { value: string; options: string[]; onChange: (v: BenchmarkTier) => void }) {
  return (
    <select {...rest} value={value} onChange={(e) => onChange(e.target.value as BenchmarkTier)}>
      {options.map((t) => (
        <option key={t} value={t}>{TIER_LABELS[t] ?? t}</option>
      ))}
    </select>
  )
}

const TIER_FIELD_HELP = Object.entries(TIER_HELP).map(([t, h]) => `${TIER_LABELS[t]}: ${h}`).join(' ')

const FORMAT_HELP: Record<ImportFormat, string> = {
  jsonl: 'One JSON object per line: {"source": "…", "reference": "…"}. "label" or "id" is optional.',
  tsv: 'One case per line: source text, a Tab, then the reference.',
}

const IMPORT_PLACEHOLDER: Record<ImportFormat, string> = {
  jsonl: '{"source": "你好", "reference": "Hello"}\n{"source": "谢谢", "reference": "Thank you"}',
  tsv: '你好\tHello\n谢谢\tThank you',
}

function ImportSection({ options, pc, onDone }: { options: BenchmarkOptions; pc: PcMode; onDone: () => void }) {
  const [setName, setSetName] = useState('')
  const [tier, setTier] = usePersistedState<string>('benchmark.importTier', 'public')
  const [lang, setLang] = usePersistedState<string>('benchmark.importLanguage', 'zh')
  const [format, setFormat] = usePersistedState<string>('benchmark.importFormat', 'jsonl')
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const fmt: ImportFormat = format === 'tsv' ? 'tsv' : 'jsonl'

  if (pc === 'remote') {
    return (
      <Section title="Import golden set" summary={PC_ONLY_SUMMARY} storageKey="benchmark.import">
        <p className="muted">{PC_ONLY_BODY}</p>
      </Section>
    )
  }
  const missing = !setName.trim() ? 'Give the set a name.' : !text.trim() ? 'Paste the cases or open a file.' : null

  const openFile = async (file: File | undefined) => {
    if (!file) return
    setText(await file.text())
    if (/\.tsv$/i.test(file.name)) setFormat('tsv')
    else if (/\.jsonl?$/i.test(file.name)) setFormat('jsonl')
    if (!setName.trim()) setSetName(file.name.replace(/\.[^.]+$/, '').slice(0, 60))
  }

  const submit = async () => {
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const r = await importGoldenSet({
        set_name: setName.trim(),
        text,
        format: fmt,
        tier: (options.tiers as string[]).includes(tier) ? (tier as BenchmarkTier) : 'public',
        source_language: (options.source_languages as string[]).includes(lang) ? (lang as SourceLanguage) : 'zh',
      })
      setResult(`Added ${r.added} ${r.added === 1 ? 'case' : 'cases'} to “${r.set_name}”${r.skipped ? `; ${r.skipped} already in the set were skipped` : ''}.`)
      setText('')
      onDone()
    } catch (e) {
      setError(plainError(e, { pcOnly: true }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section title="Import golden set" summary="Paste JSONL or TSV" storageKey="benchmark.import">
      <p className="muted">
        Paste a test set you already have (e.g. a FLORES-200 or WMT slice you downloaded, or your own corrected lines).
        Nothing is downloaded. At most 500 cases per import; a case already in the set is skipped.
      </p>
      <div className="field-row">
        <Field label="Set name">
          <input value={setName} maxLength={60} onChange={(e) => setSetName(e.target.value)} placeholder="e.g. flores-zh-200" />
        </Field>
        <Field label="Tier" help={TIER_FIELD_HELP}>
          <TierSelect value={tier} options={options.tiers} onChange={setTier} />
        </Field>
        <Field label="Source language">
          <LanguageSelect value={lang} options={options.source_languages} onChange={setLang} />
        </Field>
        <Field label="Format" help={FORMAT_HELP[fmt]}>
          <select value={fmt} onChange={(e) => setFormat(e.target.value)}>
            <option value="jsonl">JSONL</option>
            <option value="tsv">Tab-separated (TSV)</option>
          </select>
        </Field>
      </div>
      <p className="muted">{FORMAT_HELP[fmt]}</p>
      <Field label="Cases">
        <textarea rows={6} value={text} onChange={(e) => setText(e.target.value)} placeholder={IMPORT_PLACEHOLDER[fmt]} spellCheck={false} />
      </Field>
      <Field label="Or open a file" help="Read from this device into the box above; nothing is uploaded until you press Import.">
        <input type="file" accept=".jsonl,.json,.tsv,.txt" onChange={(e) => void openFile(e.target.files?.[0])} />
      </Field>
      <div className="actions">
        <button type="button" className={buttonClass('primary')} disabled={!!missing || busy} onClick={() => void submit()}>
          {busy ? 'Importing…' : 'Import'}
        </button>
        {missing && <span className="muted">{missing}</span>}
      </div>
      {result && <p role="status">{result}</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </Section>
  )
}

function AddCaseSection({ options, pc, onDone }: { options: BenchmarkOptions; pc: PcMode; onDone: () => void }) {
  const [label, setLabel] = useState('')
  const [source, setSource] = useState('')
  const [reference, setReference] = useState('')
  const [lang, setLang] = usePersistedState<string>('benchmark.caseLanguage', 'zh')
  const [tier, setTier] = usePersistedState<string>('benchmark.caseTier', 'application')
  const [setName, setSetName] = usePersistedState<string>('benchmark.caseSet', '')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  if (pc === 'remote') {
    return (
      <Section title="Add one case" summary={PC_ONLY_SUMMARY} storageKey="benchmark.addCase">
        <p className="muted">{PC_ONLY_BODY}</p>
      </Section>
    )
  }
  const missing = !label.trim() ? 'Give the case a label.' : !source.trim() ? 'Enter the source text.' : null

  const submit = async () => {
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const body = {
        label: label.trim(),
        source_text: source.trim(),
        source_language: ((options.source_languages as string[]).includes(lang) ? lang : 'zh') as SourceLanguage,
        tier: ((options.tiers as string[]).includes(tier) ? tier : 'application') as BenchmarkTier,
        ...(reference.trim() ? { reference_text: reference.trim() } : {}),
        ...(setName.trim() ? { set_name: setName.trim() } : {}),
      }
      const c = await createBenchmarkCase(body)
      setResult(`Added “${c.label}” to ${setDisplayName(c.set_name)}.`)
      setLabel('')
      setSource('')
      setReference('')
      onDone()
    } catch (e) {
      setError(plainError(e, { pcOnly: true }))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section title="Add one case" summary="A translation case by hand" storageKey="benchmark.addCase">
      <div className="field-row">
        <Field label="Label">
          <input value={label} maxLength={120} onChange={(e) => setLabel(e.target.value)} />
        </Field>
        <Field label="Set name" help="Cases with the same set name form one golden set. Remembered.">
          <input value={setName} maxLength={60} onChange={(e) => setSetName(e.target.value)} placeholder="Optional" />
        </Field>
        <Field label="Tier" help={TIER_FIELD_HELP}>
          <TierSelect value={tier} options={options.tiers} onChange={setTier} />
        </Field>
        <Field label="Source language">
          <LanguageSelect value={lang} options={options.source_languages} onChange={setLang} />
        </Field>
      </div>
      <Field label="Source text">
        <textarea rows={2} value={source} maxLength={4000} onChange={(e) => setSource(e.target.value)} />
      </Field>
      <Field label="Reference translation" help="The answer a run is scored against. Without one the case is run but not scored.">
        <textarea rows={2} value={reference} maxLength={4000} onChange={(e) => setReference(e.target.value)} />
      </Field>
      <div className="actions">
        <button type="button" className={buttonClass('secondary')} disabled={!!missing || busy} onClick={() => void submit()}>
          {busy ? 'Adding…' : 'Add case'}
        </button>
        {missing && <span className="muted">{missing}</span>}
      </div>
      {result && <p role="status">{result}</p>}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </Section>
  )
}
