import { SORT_LABELS, type SortKey } from './runRanking'

type Props = {
  /** Distinguishes several bars on one page (radio group name and label). */
  name: string
  value: SortKey
  onChange: (key: SortKey) => void
  /** Sorts to leave out, such as speed where no time was measured. */
  hide?: SortKey[]
}

/** Best-first sort choice shared by the Recent runs tables. */
export function SortBar({ name, value, onChange, hide = [] }: Props) {
  const keys = (Object.keys(SORT_LABELS) as SortKey[]).filter((k) => !hide.includes(k))
  return (
    <div className="bench-sort">
      <span className="muted" aria-hidden="true">Best first by</span>
      <fieldset className="segmented">
        <legend className="visually-hidden">Sort best first by</legend>
        {keys.map((k) => (
          <label key={k}>
            <input type="radio" name={`sort-${name}`} value={k} checked={value === k} onChange={() => onChange(k)} />
            <span>{SORT_LABELS[k]}</span>
          </label>
        ))}
      </fieldset>
    </div>
  )
}
