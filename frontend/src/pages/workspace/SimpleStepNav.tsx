/*
 * The Simple view's way between stages: the title page and each stage a Simple page links to, so Translate, Review and Export are one tap apart.
 */
import { routeHref } from '../../router'
import { STAGE_LABELS, type StageId } from './stages'
import './simpleStepNav.css'

export function SimpleStepNav({ id, stages, active }: { id: number; stages: readonly StageId[]; active: StageId | null }) {
  return (
    <nav className="simple-step-nav" aria-label="Title steps">
      <a href={routeHref({ name: 'drama', id, stage: null })}>Steps</a>
      {stages.map((s) => (
        <a key={s} href={routeHref({ name: 'drama', id, stage: s })} aria-current={s === active ? 'page' : undefined}>
          {STAGE_LABELS[s]}
        </a>
      ))}
    </nav>
  )
}
