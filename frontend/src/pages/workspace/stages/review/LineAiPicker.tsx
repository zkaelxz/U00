import { EngineModelFields } from './EngineModelFields'
import { engineBlockReason, modelBlockReason, type LinePick, type useLineAiPick } from './lineAiPick'

type Picker = ReturnType<typeof useLineAiPick>

// The engine and model for one per-line AI request. Hidden while the engine
// list is unavailable, in which case the server's default engine answers.
export function LineAiPicker({ picker, disabled }: { picker: Picker; disabled?: boolean }) {
  if (picker.engines.length === 0) return null
  const { engines, defaultEngine, holdsPaid, pick, setPick } = picker
  return (
    <fieldset className="review-ai-engine" disabled={disabled} data-testid="line-ai-picker">
      <EngineModelFields
        engines={engines}
        defaultEngine={defaultEngine}
        engine={pick.engine}
        model={pick.model}
        help=""
        markPaid
        blocked={(e) => engineBlockReason(e, holdsPaid)}
        modelBlocked={(e, m) => modelBlockReason(e, m, holdsPaid)}
        onChange={(next: LinePick) => setPick(next)}
      />
    </fieldset>
  )
}
