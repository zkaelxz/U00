/*
 * Card: a flat, always-open block with an optional header (title, one-line
 * meta, actions on the right). Use it for anything people need often; keep
 * Section (a <details> fold) for rare options and destructive tools.
 *
 *   <Card title="Appearance" meta="Dark" actions={<button className={buttonClass('ghost', 'sm')}>Reset</button>}>
 *     ...body...
 *   </Card>
 *
 * Inside a CardFoldContext provider (Settings) a titled Card is a Section instead: the title and
 * the current value show while it is closed, and the meta, actions and body show once it is open.
 * The provider does not reach Cards nested in the body.
 */
import { useContext, type ReactNode } from 'react'
import { capFirst } from '../labels'
import { CardFoldContext, type CardFold } from './cardFold'
import { Section } from './Section'

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
  const fold = useContext(CardFoldContext)
  const hasHead = title != null || meta != null || actions != null
  if (fold && typeof title === 'string') {
    return (
      <Tag className={className ? `card card-fold ${className}` : 'card card-fold'} aria-label={rest['aria-label']}>
        <CardFoldContext.Provider value={null}>
          <FoldedCard fold={fold} title={title} meta={meta} actions={actions}>{children}</FoldedCard>
        </CardFoldContext.Provider>
      </Tag>
    )
  }
  return (
    <Tag className={className ? `card ${className}` : 'card'} aria-label={rest['aria-label']}>
      {hasHead && (
        <header className="card-head">
          <div className="card-heading">
            {title != null && <h2 className="card-title">{title}</h2>}
            {meta != null && <p className="card-meta">{typeof meta === 'string' ? capFirst(meta) : meta}</p>}
          </div>
          {actions != null && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children != null && <div className="card-body">{children}</div>}
    </Tag>
  )
}

function FoldedCard({ fold, title, meta, actions, children }: { fold: CardFold; title: string; meta?: ReactNode; actions?: ReactNode; children?: ReactNode }) {
  const metaText = typeof meta === 'string' ? meta : undefined
  return (
    <Section
      title={title}
      summary={fold.summary ?? metaText}
      storageKey={fold.storageKey}
      defaultOpen={fold.defaultOpen}
      openSignal={fold.openSignal}
      expandSignal={fold.expandSignal}
      closeSignal={fold.closeSignal}
    >
      {(meta != null || actions != null) && (
        <div className="card-head">
          {meta != null && <p className="card-meta">{typeof meta === 'string' ? capFirst(meta) : meta}</p>}
          {actions != null && <div className="card-actions">{actions}</div>}
        </div>
      )}
      {children}
    </Section>
  )
}
