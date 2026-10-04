import type { NovelGlossaryProposal } from '../../../types/autotuneGlossary'
import type { GlossaryCatalogues } from '../../../types/translateStage'
import { proposalValues, type Edits, type ProposalValues } from './glossaryExtract'

interface Props {
  proposals: NovelGlossaryProposal[]
  selected: Set<string>
  onToggle: (term: string) => void
  edits: Edits
  onEdit: <K extends keyof ProposalValues>(p: NovelGlossaryProposal, field: K, value: ProposalValues[K]) => void
  // null while loading or if the read failed: category/policy show as text.
  catalogues: GlossaryCatalogues | null
  isPhone: boolean
  testId: string
}

function OptionSelect({ label, value, options, onChange }: {
  label: string
  value: string | null
  options: { key: string; label: string }[]
  onChange: (v: string | null) => void
}) {
  // Keep a proposal's value selectable even if the catalogue doesn't list it.
  const known = value === null || options.some((o) => o.key === value)
  return (
    <select aria-label={label} value={value ?? ''} onChange={(e) => onChange(e.target.value || null)}>
      <option value="">None</option>
      {!known && value !== null && <option value={value}>{value}</option>}
      {options.map((o) => (
        <option key={o.key} value={o.key}>{o.label}</option>
      ))}
    </select>
  )
}

// The proposals of a glossary extraction: a checkbox per term plus editable
// translation, category and policy. Cards on a phone, a table otherwise.
// Rows are keyed by term text.
export function GlossaryProposals({ proposals, selected, onToggle, edits, onEdit, catalogues, isPhone, testId }: Props) {
  const inGlossary = <span className="badge">Already in glossary</span>
  const checkbox = (p: NovelGlossaryProposal) => (
    <input type="checkbox" aria-label={`Select ${p.term}`} checked={selected.has(p.term)} onChange={() => onToggle(p.term)} />
  )
  const fields = (p: NovelGlossaryProposal) => {
    const v = proposalValues(p, edits)
    return {
      translation: (
        <input
          type="text"
          aria-label={`Translation for ${p.term}`}
          maxLength={200}
          value={v.translation}
          onChange={(e) => onEdit(p, 'translation', e.target.value)}
        />
      ),
      category: catalogues ? (
        <OptionSelect
          label={`Category for ${p.term}`}
          value={v.category}
          options={catalogues.term_categories}
          onChange={(c) => onEdit(p, 'category', c)}
        />
      ) : (
        (v.category ?? '')
      ),
      policy: catalogues ? (
        <OptionSelect
          label={`Policy for ${p.term}`}
          value={v.policy}
          options={catalogues.term_policies}
          onChange={(c) => onEdit(p, 'policy', c)}
        />
      ) : (
        (v.policy ?? '')
      ),
    }
  }

  if (isPhone) {
    return (
      <ul className="novel-glossary-cards" data-testid={`${testId}-proposals`}>
        {proposals.map((p) => {
          const f = fields(p)
          return (
            <li key={p.term}>
              <label>
                {checkbox(p)} <strong>{p.term}</strong> {p.already_in_glossary && inGlossary}
              </label>
              <div className="glossary-proposal-edit">
                {f.translation}
                {catalogues && (
                  <div className="glossary-proposal-selects">
                    {f.category}
                    {f.policy}
                  </div>
                )}
              </div>
              {!catalogues && <div className="muted">{[f.category, f.policy].filter(Boolean).join(' · ')}</div>}
              {p.reason && <div className="muted novel-glossary-reason">{p.reason}</div>}
            </li>
          )
        })}
      </ul>
    )
  }

  return (
    <div className="table-scroll">
      <table data-testid={`${testId}-proposals`}>
        <thead>
          <tr>
            <th />
            <th>Original</th>
            <th>Translation</th>
            <th>Category</th>
            <th>Policy</th>
            <th>Reason</th>
          </tr>
        </thead>
        <tbody>
          {proposals.map((p) => {
            const f = fields(p)
            return (
              <tr key={p.term}>
                <td>
                  <label className="novel-glossary-check">
                    {checkbox(p)}
                    <span className="visually-hidden">Select {p.term}</span>
                  </label>
                </td>
                <td>
                  {p.term} {p.already_in_glossary && inGlossary}
                </td>
                <td>{f.translation}</td>
                <td>{f.category}</td>
                <td>{f.policy}</td>
                <td className="novel-glossary-reason">{p.reason}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
