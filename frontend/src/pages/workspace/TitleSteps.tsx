/*
 * The Simple page's step tabs for an audio or video title: five steps across the top, the current one marked, with its action as the one main button.
 */
import { ButtonLink } from '../../components/Button'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { buttonClass } from '../../components/uiClasses'
import { routeHref } from '../../router'
import { TitleFlowStart } from './TitleFlowStart'
import { checkActionLabel, titleSteps, type Step, type StepInput, type StepKey } from './stepModel'
import './titleSteps.css'

interface Props extends StepInput {
  id: number
  /** Once the one-button flow has started it keeps its button, so its progress text stays. */
  flowOn: boolean
  onFlowProgress: () => void
  /** The stage the page is showing, so its tab is marked while it is open. */
  openStep?: StepKey | null
  /** On the Review page itself the Check button opens the queue there instead of linking to the page already open. */
  onCheck?: () => void
}

export function TitleSteps({ id, flowOn, onFlowProgress, openStep = null, onCheck, ...input }: Props) {
  const steps = titleSteps(input)
  const hasLines = (input.lineCount ?? 0) > 0
  const left = (input.lineCount ?? 0) - input.translatedCount
  const href = (stage: string, tab?: 'add' | 'transcribe') => routeHref({ name: 'drama', id, stage, ...(tab ? { tab } : {}) })
  const tabHref: Record<StepKey, string> = {
    add: href('source', 'add'),
    transcribe: href('source', 'transcribe'),
    translate: href('translate'),
    check: href('review'),
    export: href('export'),
  }
  // The secondary links share one "More" menu on a phone so the lines start higher; the tabs still reach every stage.
  const phone = useMediaQuery('(max-width: 600px)')
  const current = steps.find((s) => s.state === 'current')

  const mainAction = (s: Step) => {
    switch (s.key) {
      case 'add':
        return <ButtonLink variant="primary" href={tabHref.add}>Add media</ButtonLink>
      case 'transcribe':
        // With audio attached one button transcribes, finds glossary names and stops at the glossary review.
        return flowOn || (input.audioReady && input.lineCount !== null)
          ? <TitleFlowStart id={id} onProgress={onFlowProgress} />
          : <ButtonLink variant="primary" href={tabHref.transcribe}>Transcribe and translate</ButtonLink>
      case 'translate':
        return <ButtonLink variant="primary" href={tabHref.translate}>{left > 0 ? `Translate ${left} ${left === 1 ? 'line' : 'lines'}` : 'Translate'}</ButtonLink>
      case 'check': {
        const label = checkActionLabel(input.riskCount ?? null)
        if (label === null) return <ButtonLink variant="primary" href={tabHref.export}>Export</ButtonLink>
        return onCheck && openStep === 'check'
          ? <button type="button" className={buttonClass('primary')} onClick={onCheck}>{label}</button>
          : <ButtonLink variant="primary" href={tabHref.check}>{label}</ButtonLink>
      }
      case 'export':
        return <ButtonLink variant="primary" href={tabHref.export}>Export</ButtonLink>
    }
  }

  const secondary = (
    <>
      {current?.key !== 'add' && <ButtonLink href={tabHref.add}>Add media</ButtonLink>}
      {hasLines && <ButtonLink href={tabHref.transcribe}>Redo transcription</ButtonLink>}
      <ButtonLink href={routeHref({ name: 'drama', id, stage: 'translate', tab: 'glossary' })}>Glossary</ButtonLink>
    </>
  )

  return (
    <div className="title-steps-bar">
      <ol className="title-steps" aria-label="Steps">
        {steps.map((s, n) => (
          <li key={s.key} className={`title-step is-${s.state}${openStep === s.key ? ' is-open' : ''}`} aria-current={s.state === 'current' ? 'step' : undefined}>
            <a className="title-step-link" href={tabHref[s.key]}>
              <span className="title-step-dot" aria-hidden="true">{s.state === 'done' ? '✓' : n + 1}</span>
              <span className="title-step-text">
                <span className="title-step-title">{s.title}</span>
                {s.note && <span className="title-step-note muted">{s.note}</span>}
              </span>
            </a>
          </li>
        ))}
      </ol>
      <div className="title-steps-actions">
        {current && mainAction(current)}
        {phone ? (
          <details className="title-steps-more">
            <summary className={buttonClass('secondary')}>More</summary>
            <div className="title-steps-more-list">{secondary}</div>
          </details>
        ) : secondary}
      </div>
    </div>
  )
}
