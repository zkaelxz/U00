import { useId } from 'react'

import type { SourcePace, SourceSummary } from '../../types/sources'
import { PACE_LABELS, paceHelp, paceOptionLabel } from './sourcesFormat'

type Props = { s: SourceSummary; onChange: (pace: SourcePace) => void }

/** A site's Rules: Gentle, Standard or (only where the site's rules were checked) Fast. */
export function PaceSelect({ s, onChange }: Props) {
  const helpId = useId()
  const help = paceHelp(s)
  return (
    <div className="source-pace">
      <select
        aria-label={`Rules: ${s.display_name}`}
        aria-describedby={help ? helpId : undefined}
        value={s.pace}
        onChange={(e) => onChange(e.target.value as SourcePace)}
      >
        <option value="careful">{PACE_LABELS.careful}</option>
        <option value="normal">{PACE_LABELS.normal}</option>
        <option value="fast" disabled={!s.fast_allowed}>
          {paceOptionLabel('fast', s.fast_allowed)}
        </option>
      </select>
      {help && (
        <small id={helpId} className="muted">
          {help}
        </small>
      )}
    </div>
  )
}
