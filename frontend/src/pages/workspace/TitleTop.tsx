/** The top of an audio or video title's Simple pages: the header with the title's controls, the step links and the one main button. */
// On a phone the steps and the controls fold behind one "Step N of 5" button so the page's own content starts high.
import { useId, useState, type ReactNode } from 'react'

import { openDramaFolder } from '../../api/library'
import type { DramaDetail } from '../../api/types'
import { ButtonLink } from '../../components/Button'
import { OpenFolderButton } from '../../components/OpenFolderButton'
import { buttonClass } from '../../components/uiClasses'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { routeHref } from '../../router'
import { safeHref } from '../discover/discoverFormat'
import { SourceLink } from '../discover/ExternalLink'
import { JobPill } from './JobPill'
import { kindBadgeText } from './titleKind'
import { TitleFlowStart } from './TitleFlowStart'
import { TitleViewSwitch } from './TitleViewSwitch'
import { checkActionLabel, focusStep, mainInStepsPanel, stepLinks, stepPlace, titleSteps, type StepInput, type StepKey } from './stepModel'
import type { TitleView } from './titleView'
import './titleTop.css'

// The same width as Review's phone layout, so the header and the check queue change together.
export const TITLE_TOP_PHONE = '(max-width: 640px)'

export interface TitleProgressInput extends StepInput {
  /** Once the one-button flow has started it keeps its button, so its progress text stays. */
  flowOn: boolean
  onFlowProgress: () => void
}

const stepHrefs = (id: number): Record<StepKey, string> => {
  const href = (stage: string, tab?: 'add' | 'transcribe') => routeHref({ name: 'drama', id, stage, ...(tab ? { tab } : {}) })
  return { add: href('source', 'add'), transcribe: href('source', 'transcribe'), translate: href('translate'), check: href('review'), export: href('export') }
}

/** The current step's action. Where the lines are on the page, the Check button opens the queue there instead of linking to Review. */
export function TitleMainAction({ id, progress, onCheck }: {
  id: number
  progress: TitleProgressInput
  onCheck?: () => void
}) {
  const { flowOn, onFlowProgress, ...input } = progress
  // The run writes the lines it then goes on from, so it keeps its button and progress text while the steps move on.
  if (flowOn) return <TitleFlowStart id={id} onProgress={onFlowProgress} />
  const current = titleSteps(input).find((s) => s.state === 'current')
  if (!current) return null
  const to = stepHrefs(id)
  const left = (input.lineCount ?? 0) - input.translatedCount
  switch (current.key) {
    case 'add':
      return <ButtonLink variant="primary" href={to.add}>Add media</ButtonLink>
    case 'transcribe':
      // With audio attached one button transcribes, finds glossary names and stops at the glossary review.
      return input.audioReady && input.lineCount !== null
        ? <TitleFlowStart id={id} onProgress={onFlowProgress} />
        : <ButtonLink variant="primary" href={to.transcribe}>Transcribe and translate</ButtonLink>
    case 'translate':
      return <ButtonLink variant="primary" href={to.translate}>{left > 0 ? `Translate ${left} ${left === 1 ? 'line' : 'lines'}` : 'Translate'}</ButtonLink>
    case 'check': {
      const label = checkActionLabel(input.riskCount ?? null)
      if (label === null) return <ButtonLink variant="primary" href={to.export}>Export</ButtonLink>
      return onCheck
        ? <button type="button" className={buttonClass('primary')} onClick={onCheck}>{label}</button>
        : <ButtonLink variant="primary" href={to.check}>{label}</ButtonLink>
    }
    case 'export':
      return <ButtonLink variant="primary" href={to.export}>Export</ButtonLink>
  }
}

function StepRow({ id, input, openStep }: { id: number; input: StepInput; openStep: StepKey | null }) {
  const to = stepHrefs(id)
  return (
    <ol className="title-steps" aria-label="Steps">
      {stepLinks(input, openStep).map((s) => (
        <li key={s.key} className={`title-step is-${s.state}${s.marked ? ' is-marked' : ''}`} aria-current={s.state === 'current' ? 'step' : undefined}>
          <a className="title-step-link" href={to[s.key]} title={s.tip || undefined}>
            <span>
              {s.word && <><span className="title-step-word">{s.word}</span>{' · '}</>}
              {s.name}
              {s.note && `, ${s.note}`}
            </span>
          </a>
        </li>
      ))}
    </ol>
  )
}

interface Props {
  id: number
  drama: DramaDetail
  title: string
  back: { href: string; label: string; text: string }
  /** The title page and Review show the kind of title beside its name; the other stage pages leave it out. */
  badge?: boolean
  /** The title's own page, which outlines the current step and, on a phone, puts the main button where that step wants it. */
  home?: boolean
  onJobFinished: () => void
  onView: (v: TitleView) => void
  progress: TitleProgressInput
  /** The step whose page is open; null on the title page. */
  openStep?: StepKey | null
  onCheck?: () => void
  /** The lines' Need a look row shows the main button instead, so it is never drawn twice. */
  mainElsewhere?: boolean
}

export function TitleTop({ id, drama, title, back, badge = false, home = false, onJobFinished, onView, progress, openStep = null, onCheck, mainElsewhere = false }: Props) {
  const phone = useMediaQuery(TITLE_TOP_PHONE)
  const [moreOpen, setMoreOpen] = useState(false)
  const [foldOpen, setFoldOpen] = useState(false)
  const moreId = useId()
  const foldId = useId()
  const steps = titleSteps(progress)
  const current = steps.find((s) => s.state === 'current')
  const hasLines = (progress.lineCount ?? 0) > 0
  const to = stepHrefs(id)

  const main = <TitleMainAction id={id} progress={progress} onCheck={onCheck} />
  const secondary = (
    <>
      {current?.key !== 'add' && <ButtonLink href={to.add}>Add media</ButtonLink>}
      {hasLines && <ButtonLink href={to.transcribe}>Redo transcription</ButtonLink>}
      <ButtonLink href={routeHref({ name: 'drama', id, stage: 'translate', tab: 'glossary' })}>Glossary</ButtonLink>
    </>
  )
  const switchView = <TitleViewSwitch view="simple" onChange={onView} />
  const source = (
    <>
      <OpenFolderButton open={() => openDramaFolder(id)} className="ws-folder" title="Show this title's files on the PC" />
      <SourceLink href={drama.source_url} dramaId={id} className={buttonClass('secondary', 'md', 'ws-source')} />
      {!safeHref(drama.source_url) && (
        <a className="ws-source-add muted" href={routeHref({ name: 'drama', id, stage: 'source', tab: 'details' })}>Add a source link</a>
      )}
    </>
  )
  const focus = focusStep(steps, openStep)
  // On the Check step the phone keeps the main button inside the panel, so the check card stays near the top.
  const mainInPanel = mainInStepsPanel(openStep, current?.key ?? null, home)

  return (
    <>
      <header className={phone ? 'workspace-header title-top-header is-phone' : 'workspace-header title-top-header'}>
        <ButtonLink href={back.href} variant="ghost" size="sm" className="ws-back" aria-label={back.label}>
          <span aria-hidden="true">‹</span>
          <span className="ws-back-text" aria-hidden="true">{back.text}</span>
        </ButtonLink>
        <div className="title-top-name">
          <h1 data-testid="drama-title" title={title}>{title}</h1>
          {/* Under the name, not beside it, so the controls fit on the name's row; a phone's header leaves it out. */}
          {badge && !phone && <p className="title-top-kind" data-testid="title-kind">{kindBadgeText(drama.media_type, drama.source_language)}</p>}
        </div>
        <JobPill dramaId={id} onFinished={onJobFinished} />
        {phone ? (
          <button type="button" className={buttonClass('secondary', undefined, 'steps-fold-toggle')} aria-expanded={foldOpen} aria-controls={foldId} onClick={() => setFoldOpen((o) => !o)}>
            {(focus && stepPlace(steps, focus)) ?? 'Steps'}
          </button>
        ) : (
          <div className="title-top-actions">
            {secondary}
            {switchView}
            <button type="button" className={buttonClass('secondary', undefined, 'title-top-more')} aria-expanded={moreOpen} aria-controls={moreId} onClick={() => setMoreOpen((o) => !o)}>
              More
            </button>
          </div>
        )}
      </header>
      {phone ? (
        <>
          <div id={foldId} className="steps-fold-body" hidden={!foldOpen}>
            <StepRow id={id} input={progress} openStep={openStep} />
            {current && mainInPanel && <TitleMainRow>{main}</TitleMainRow>}
            <div className="steps-fold-controls">{secondary}</div>
            <div className="steps-fold-controls">
              {switchView}
              {source}
            </div>
          </div>
          {current && !mainInPanel && <TitleMainRow>{main}</TitleMainRow>}
        </>
      ) : (
        <>
          <div id={moreId} className="title-top-more-row" hidden={!moreOpen}>{source}</div>
          <StepRow id={id} input={progress} openStep={openStep} />
          {current && !mainElsewhere && <TitleMainRow>{main}</TitleMainRow>}
        </>
      )}
    </>
  )
}

function TitleMainRow({ children }: { children: ReactNode }) {
  return <div className="title-main-row">{children}</div>
}
