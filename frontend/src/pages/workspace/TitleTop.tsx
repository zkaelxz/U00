/*
 * The top of an audio or video title's Simple pages: the header with the title's controls, one row of step links and the one main button.
 * On a phone the steps and the controls fold behind one "Step N of 5" button so the page's own content starts high.
 */
import { useId, useState, type ReactNode } from 'react'

import { openDramaFolder } from '../../api/library'
import type { DramaDetail } from '../../api/types'
import { Badge } from '../../components/Badge'
import { ButtonLink } from '../../components/Button'
import { OpenFolderButton } from '../../components/OpenFolderButton'
import { buttonClass } from '../../components/uiClasses'
import { useMediaQuery } from '../../hooks/useMediaQuery'
import { routeHref } from '../../router'
import { safeHref } from '../discover/discoverFormat'
import { SourceLink } from '../discover/ExternalLink'
import { JobPill } from './JobPill'
import { TitleFlowStart } from './TitleFlowStart'
import { TitleViewSwitch } from './TitleViewSwitch'
import { checkActionLabel, focusStep, stepLinks, stepPlace, titleSteps, type StepInput, type StepKey } from './stepModel'
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

/** The current step's action. On the Review page the Check button opens the queue there instead of linking to the page already open. */
export function TitleMainAction({ id, progress, openStep = null, onCheck }: {
  id: number
  progress: TitleProgressInput
  openStep?: StepKey | null
  onCheck?: () => void
}) {
  const { flowOn, onFlowProgress, ...input } = progress
  const current = titleSteps(input).find((s) => s.state === 'current')
  if (!current) return null
  const to = stepHrefs(id)
  const left = (input.lineCount ?? 0) - input.translatedCount
  switch (current.key) {
    case 'add':
      return <ButtonLink variant="primary" href={to.add}>Add media</ButtonLink>
    case 'transcribe':
      // With audio attached one button transcribes, finds glossary names and stops at the glossary review.
      return flowOn || (input.audioReady && input.lineCount !== null)
        ? <TitleFlowStart id={id} onProgress={onFlowProgress} />
        : <ButtonLink variant="primary" href={to.transcribe}>Transcribe and translate</ButtonLink>
    case 'translate':
      return <ButtonLink variant="primary" href={to.translate}>{left > 0 ? `Translate ${left} ${left === 1 ? 'line' : 'lines'}` : 'Translate'}</ButtonLink>
    case 'check': {
      const label = checkActionLabel(input.riskCount ?? null)
      if (label === null) return <ButtonLink variant="primary" href={to.export}>Export</ButtonLink>
      return onCheck && openStep === 'check'
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
  /** The title page shows the kind of title beside its name; a stage page leaves it to the title page. */
  badge?: boolean
  onJobFinished: () => void
  onView: (v: TitleView) => void
  progress: TitleProgressInput
  /** The step whose page is open; null on the title page. */
  openStep?: StepKey | null
  onCheck?: () => void
  /** Review shows the main button in its Need a look row instead, so it is never drawn twice. */
  mainElsewhere?: boolean
}

export function TitleTop({ id, drama, title, back, badge = false, onJobFinished, onView, progress, openStep = null, onCheck, mainElsewhere = false }: Props) {
  const phone = useMediaQuery(TITLE_TOP_PHONE)
  const [moreOpen, setMoreOpen] = useState(false)
  const [foldOpen, setFoldOpen] = useState(false)
  const moreId = useId()
  const foldId = useId()
  const steps = titleSteps(progress)
  const current = steps.find((s) => s.state === 'current')
  const hasLines = (progress.lineCount ?? 0) > 0
  const to = stepHrefs(id)

  const main = <TitleMainAction id={id} progress={progress} openStep={openStep} onCheck={onCheck} />
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
  // On Review the phone keeps the main button inside the panel, so the check card stays near the top.
  const mainOnReviewPhone = openStep === 'check'

  return (
    <>
      <header className={phone ? 'workspace-header title-top-header is-phone' : 'workspace-header title-top-header'}>
        <ButtonLink href={back.href} variant="ghost" size="sm" className="ws-back" aria-label={back.label}>
          <span aria-hidden="true">‹</span>
          <span className="ws-back-text" aria-hidden="true">{back.text}</span>
        </ButtonLink>
        <h1 data-testid="drama-title" title={title}>{title}</h1>
        {/* A phone's header keeps its one line for the title and the steps button. */}
        {badge && !phone && (
          <div className="ws-badges">
            <Badge kind="mediaType" value={drama.media_type} />
          </div>
        )}
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
            {current && mainOnReviewPhone && <TitleMainRow>{main}</TitleMainRow>}
            <div className="steps-fold-controls">{secondary}</div>
            <div className="steps-fold-controls">
              {switchView}
              {source}
            </div>
          </div>
          {current && !mainOnReviewPhone && <TitleMainRow>{main}</TitleMainRow>}
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
