/*
 * The Simple page's checklist for an audio or video title: five steps, the current one large with its button.
 */
import { ButtonLink } from '../../components/Button'
import { routeHref } from '../../router'
import { TitleFlowStart } from './TitleFlowStart'
import { titleSteps, type Step, type StepInput, type StepKey } from './stepModel'
import './titleSteps.css'

interface Props extends StepInput {
  id: number
  /** Once the one-button flow has started it keeps its button, so its progress text stays. */
  flowOn: boolean
  onFlowProgress: () => void
}

export function TitleSteps({ id, flowOn, onFlowProgress, ...input }: Props) {
  const steps = titleSteps(input)
  const hasLines = (input.lineCount ?? 0) > 0
  const href = (stage: string, tab?: 'add' | 'transcribe') => routeHref({ name: 'drama', id, stage, ...(tab ? { tab } : {}) })

  const action = (s: Step) => {
    const primary = s.state === 'current'
    // Full-size buttons throughout: the small size is under the 44px touch target on a phone.
    const link = (label: string, to: string) => <ButtonLink href={to} variant={primary ? 'primary' : undefined}>{label}</ButtonLink>
    switch (s.key as StepKey) {
      case 'add':
        return link('Add media', href('source', 'add'))
      case 'transcribe':
        if (hasLines) return link('Redo', href('source', 'transcribe'))
        // With audio attached one button transcribes, finds glossary names and stops at the glossary review.
        return flowOn || (input.audioReady && input.lineCount !== null)
          ? <TitleFlowStart id={id} onProgress={onFlowProgress} />
          : link('Transcribe and translate', href('source', 'transcribe'))
      case 'translate':
        return link('Translate', href('translate'))
      case 'check':
        return link('Review', href('review'))
      case 'export':
        return link('Export', href('export'))
    }
  }

  return (
    <ol className="title-steps" aria-label="Steps">
      {steps.map((s, n) => (
        <li key={s.key} className={`title-step is-${s.state}`} aria-current={s.state === 'current' ? 'step' : undefined}>
          <span className="title-step-dot" aria-hidden="true">{s.state === 'done' ? '✓' : n + 1}</span>
          <div className="title-step-text">
            <h2 className="title-step-title">{s.title}</h2>
            {s.note && <p className="title-step-note muted">{s.note}</p>}
          </div>
          <div className="title-step-action">{action(s)}</div>
        </li>
      ))}
    </ol>
  )
}
