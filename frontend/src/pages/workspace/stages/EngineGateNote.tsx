import { NeedsInstall } from '../../readiness/NeedsInstall'
import type { EngineGate } from './engineReady'

/** Under a disabled AI button: why it is off and the way to fix it (Diagnostics for a package, Settings for a key). */
export function EngineGateNote({ gate }: { gate: EngineGate | null }) {
  if (!gate) return null
  return (
    <div data-testid="engine-gate">
      <NeedsInstall need={gate} />
    </div>
  )
}
