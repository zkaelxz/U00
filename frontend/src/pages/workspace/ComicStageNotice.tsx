import { ButtonLink } from '../../components/Button'
import { Card } from '../../components/Card'
import { routeHref } from '../../router'
import type { StageId } from './stages'

// Comic bubbles live in Scanlate, not in lines, so these stages have nothing to show for them.
const LINE_STAGES: readonly StageId[] = ['translate', 'review', 'dub', 'export']

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
    </Card>
  )
}
