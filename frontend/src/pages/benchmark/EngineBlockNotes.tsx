import type { BenchmarkEngineOption } from '../../types/benchmark'
import { ButtonLink } from '../../components/Button'
import { packagesHref } from '../readiness/NeedsInstall'
import { routeHref } from '../../router'
import { engineOptionLabel } from '../translatePage'
import { engineBlock } from './benchmarkForm'

/** Under Start: engines picked that cannot run, grouped by what is missing (a key, the SDK, Ollama). */
export function EngineBlockNotes({ engines }: { engines: BenchmarkEngineOption[] }) {
  const gates = engines.map((e) => ({ e, gate: engineBlock(e) }))
  const noKey = gates.filter((g) => !g.gate || g.gate.id === 'engine_key').map((g) => g.e)
  const other = gates.filter((g) => g.gate && g.gate.id !== 'engine_key')
  return (
    <>
      {noKey.length > 0 && (
        <p className="warn bench-note">
          {noKey.map((e) => engineOptionLabel(e)).join(', ')}: no key yet.{' '}
          <ButtonLink href={routeHref({ name: 'settings' })} variant="ghost" size="sm">
            Set a key in Settings
          </ButtonLink>
        </p>
      )}
      {other.map(({ e, gate }) => (
        <p key={e.name} className="warn bench-note" data-testid="engine-block">
          {gate!.reason}{' '}
          {gate!.id === 'engine_package' && (
            <ButtonLink href={packagesHref(gate!)} variant="ghost" size="sm">
              Open Diagnostics
            </ButtonLink>
          )}
        </p>
      ))}
    </>
  )
}
