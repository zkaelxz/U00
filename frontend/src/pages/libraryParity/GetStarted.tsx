import { ButtonLink } from '../../components/Button'
import { buttonClass } from '../../components/uiClasses'
import { useHolds } from '../../hooks/useHolds'
import { useReadiness } from '../../hooks/useReadiness'
import { firstRunSteps } from './getStartedLogic'

// The first-run view of an empty Library: the few things to do before the first title.
export function GetStarted({ canCreate, onHide }: { canCreate: boolean; onHide: () => void }) {
  // Installing is the admin's job; a member would only land on a page that says so.
  const canSetUp = useHolds('admin.diagnostics')
  const { readiness } = useReadiness()
  const steps = firstRunSteps({ canSetUp, canCreate, readiness, totalDramas: 0 })
  return (
    <section className="get-started" aria-label="Get started">
      <p className="muted">No titles yet.</p>
      <ol className="get-started-steps">
        {steps.map((s) => (
          <li key={s.id} className="get-started-step">
            {!s.done && s.href
              ? <ButtonLink size="sm" variant={s.id === 'subtitles' ? 'primary' : 'secondary'} href={s.href}>{s.label}</ButtonLink>
              : <span>{s.label}</span>}
            {s.done && <span className="muted">Done</span>}
            {!s.done && !s.href && <span className="muted">Ask the person who runs Baihe</span>}
          </li>
        ))}
        <li className="get-started-step">
          <button type="button" className={buttonClass('ghost', 'sm')} onClick={onHide}>Hide</button>
        </li>
      </ol>
    </section>
  )
}
