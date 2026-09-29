/*
 * Badge: a small pill for status, type or language.
 *   <Badge tone={statusTone(d.status)}>{humanize('status', d.status)}</Badge>
 *   <Badge kind="language" value="zh" />      -> "Chinese"
 * With kind + value the label is humanized and a status gets its tone.
 */
import type { ReactNode } from 'react'
import { humanize, statusTone, type BadgeTone, type LabelKind } from './labels'
import { badgeClass } from './uiClasses'

type BadgeProps = {
  tone?: BadgeTone
  kind?: LabelKind
  value?: string | null
  title?: string
  children?: ReactNode
}

export function Badge({ tone, kind, value, title, children }: BadgeProps) {
  const label = children ?? (kind ? humanize(kind, value) : value)
  const t = tone ?? (kind === 'status' ? statusTone(value) : 'neutral')
  return (
    <span className={badgeClass(t)} title={title}>
      {label}
    </span>
  )
}
