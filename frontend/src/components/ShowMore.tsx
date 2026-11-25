/** "Showing N of M / Show more" footer and its page-size state, so long lists grow in steps instead of all at once. */
import { useState } from 'react'
import { buttonClass } from './uiClasses'

export const SHOW_MORE_STEP = 50

/** The page size is reset by giving the list a new `resetKey` (a filter or a new load), so a shorter list is not left
 *  showing a long page it never asked for. */
export function useShowMore(resetKey: string | number = '', step = SHOW_MORE_STEP) {
  const [state, setState] = useState({ key: resetKey, limit: step })
  const limit = state.key === resetKey ? state.limit : step
  return { limit, more: () => setState({ key: resetKey, limit: limit + step }) }
}

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
