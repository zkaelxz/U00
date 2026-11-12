/*
 * The Simple view's way between stages: the title page and each stage a Simple page links to, so Translate, Review and Export are one tap apart.
 */
import { routeHref } from '../../router'
import { stageLabel, type StageId } from './stages'
import type { TitleKind } from './titleKind'
import './simpleStepNav.css'

export function SimpleStepNav({ id, stages, active, kind }: { id: number; stages: readonly StageId[]; active: StageId | null; kind: TitleKind }) {
  return (
    <nav className="simple-step-nav" aria-label="Title steps">
      <a href={routeHref({ name: 'drama', id, stage: null })}>Steps</a>
      {stages.map((s) => (
        <a key={s} href={routeHref({ name: 'drama', id, stage: s })} aria-current={s === active ? 'page' : undefined}>
          {stageLabel(s, kind)}
        </a>
      ))}
    </nav>
  )
}
