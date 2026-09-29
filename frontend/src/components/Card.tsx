/*
 * Card: a flat, always-open block with an optional header (title, one-line
 * meta, actions on the right). Use it for anything people need often; keep
 * Section (a <details> fold) for rare options and destructive tools.
 *
 *   <Card title="Appearance" meta="Dark" actions={<button className={buttonClass('ghost', 'sm')}>Reset</button>}>
 *     ...body...
 *   </Card>
 */
import type { ReactNode } from 'react'

type CardProps = {
  title?: ReactNode
  meta?: ReactNode
  actions?: ReactNode
  as?: 'section' | 'div' | 'article'
  className?: string
  'aria-label'?: string
  children?: ReactNode
}

export function Card({ title, meta, actions, as: Tag = 'section', className, children, ...rest }: CardProps) {
  const hasHead = title != null || meta != null || actions != null
  return (
    <Tag className={className ? `card ${className}` : 'card'} aria-label={rest['aria-label']}>
      {hasHead && (
        <header className="card-head">
          <div className="card-heading">
            {title != null && <h3 className="card-title">{title}</h3>}
            {meta != null && <p className="card-meta">{meta}</p>}
          </div>
          {actions != null && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children != null && <div className="card-body">{children}</div>}
    </Tag>
  )
}
