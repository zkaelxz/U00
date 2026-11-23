import { ButtonLink } from '../../components/Button'
import { Card } from '../../components/Card'
import { routeHref } from '../../router'

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
