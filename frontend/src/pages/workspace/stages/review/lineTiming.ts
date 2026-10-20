import { patchLine } from '../../../../api/review'
import { lineNumber } from '../../../../lineNumber'
import type { ReviewLine } from '../../../../types/review'
import type { LinesControllerDeps, LinesCore } from './linesController'
import { timingPatch, type TimingField } from './reviewDraft'
import { formatTime } from './reviewLogic'
import type { Edge } from './Waveform'

export function createTiming(deps: LinesControllerDeps, core: LinesCore) {
  const { dramaId, st, setIssue, setStatus } = deps
  const { find, replaceLine, failLine } = core
  // Timing hotkeys and waveform drags queue, so each edit starts from the
  // line the previous save returned (a stale base would 409).
  let timingQueue: Promise<unknown> = Promise.resolve()
  const setTiming = (id: number, field: TimingField, value: (line: ReviewLine) => number): Promise<boolean> => {
    const run = timingQueue.then(async () => {
      const line = find(id)
      if (!line) return false
      if (st.current.edit?.lineId === id) {
        setStatus('Save or discard your edit first.')
        return false
      }
      const at = st.current.shown.findIndex((l) => l.id === id)
      const patch = timingPatch(line, { prev: st.current.shown[at - 1], next: st.current.shown[at + 1] }, field, value(line))
      if (typeof patch === 'string') {
        setStatus(patch)
        return false
      }
      if (patch === null) return true
      try {
        const saved = await patchLine(dramaId, id, patch)
        replaceLine(saved)
        setIssue(null)
        setStatus(`#${lineNumber(saved.idx)} ${field} ${formatTime(saved[field])}`)
        st.current.onChanged()
        return true
      } catch (e) {
        failLine(id, e)
        return false
      }
    })
    timingQueue = run
    return run
  }
  const retime = (id: number, edge: Edge, value: number) => setTiming(id, edge, () => value)
  return { setTiming, retime }
}
