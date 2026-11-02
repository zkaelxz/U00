import { ButtonLink } from '../../components/Button'
import { Card } from '../../components/Card'
import { routeHref } from '../../router'
import type { StageId } from './stages'

// The Translate stage's Glossary and Characters tabs hold the series glossary and cast that Scanlate translates with, so a link to either shows them even for a comic.
export function isCastTab(tab: string | undefined): boolean {
  return tab === 'glossary' || tab === 'characters'
}

// Comic bubbles live in Scanlate, not in lines, so these stages have nothing to show for them.
const LINE_STAGES: readonly StageId[] = ['translate', 'review', 'export']

export const COMIC_STAGE_TITLE = 'Comics are translated in Scanlate'

export function isLineStageForComic(stage: StageId | null): boolean {
  return stage !== null && LINE_STAGES.includes(stage)
}

export function ComicStageNotice({ id }: { id: number }) {
  return (
    <Card as="section" aria-label="Comic title" className="comic-stage-notice">
      <p>Comics are translated bubble by bubble in Scanlate, not as text lines.</p>
      <ButtonLink href={routeHref({ name: 'comic', id, page: null })} variant="primary" className="comic-open-scanlate" data-testid="open-in-scanlate">
        Open in Scanlate
      </ButtonLink>
      <ButtonLink href={routeHref({ name: 'drama', id, stage: 'translate', tab: 'glossary' })} data-testid="open-comic-glossary">
        Glossary and characters
      </ButtonLink>
    </Card>
  )
}
