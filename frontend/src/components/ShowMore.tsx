/** "Showing N of M / Show more" footer and its page-size state, so long lists grow in steps instead of all at once. */
import { SHOW_MORE_STEP, useShowMore } from '../hooks/useShowMore'
import { buttonClass } from './uiClasses'

export function ShowMore({ shown, total, onMore, testId }: { shown: number; total: number; onMore: () => void; testId?: string }) {
  if (shown >= total) return null
  return (
    <p className="actions" data-testid={testId ?? 'show-more'}>
      <span className="muted">Showing {shown} of {total}.</span>
      <button type="button" className={buttonClass('secondary', 'sm')} onClick={onMore}>Show more</button>
    </p>
  )
}

/** For a list drawn inside conditional markup, where a hook can't be called: renders the first page of `items` through
 *  `children`, then the footer. */
export function Paged<T>({ items, children, resetKey = '', step = SHOW_MORE_STEP, testId }: {
  items: readonly T[]
  children: (visible: readonly T[]) => React.ReactNode
  resetKey?: string | number
  step?: number
  testId?: string
}) {
  const page = useShowMore(resetKey, step)
  const visible = items.slice(0, page.limit)
  return (
    <>
      {children(visible)}
      <ShowMore shown={visible.length} total={items.length} onMore={page.more} testId={testId} />
    </>
  )
}
