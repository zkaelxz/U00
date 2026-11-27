import { useCallback, useState } from 'react'

import { getRecap } from '../../api/library'
import { workspaceHref } from '../../components/libraryView'
import { buttonClass } from '../../components/uiClasses'
import { browserStorage } from '../../hooks/usePersistedState'
import { useLoad } from '../../hooks/useLoad'
import { useSession } from '../../hooks/useSession'
import type { LibraryRecap } from '../../types/library'
import { dismissKey, isDismissed, recapItems, recapMonth, rememberDismissed } from './monthRecapLogic'

// "Your October" at the top of the Library in the first week of November: this account's own month.
export function MonthRecap() {
  const session = useSession()
  const [when] = useState(() => recapMonth(new Date()))
  const account = session.status === 'ready' ? session.me.user?.id ?? null : null
  const [hidden, setHidden] = useState<string | null>(null)
  const load = useCallback(() => (when ? getRecap(when.month) : Promise.resolve(null)), [when])
  const recap = useLoad(load, 0)
  if (!when || session.status === 'loading' || !recap.data) return null
  const key = dismissKey(account, when.month)
  if (hidden === key || isDismissed(browserStorage(), account, when.month)) return null
  return (
    <MonthRecapView
      recap={recap.data}
      monthName={when.name}
      onDismiss={() => {
        rememberDismissed(browserStorage(), account, when.month)
        setHidden(key)
      }}
    />
  )
}

export function MonthRecapView({ recap, monthName, onDismiss }: { recap: LibraryRecap; monthName: string; onDismiss: () => void }) {
  const items = recapItems(recap)
  if (items.length === 0) return null
  const top = recap.most_days
  return (
    <section className="month-recap" aria-labelledby="month-recap-head" data-testid="month-recap">
      <header className="month-recap-head">
        <h2 id="month-recap-head">Your {monthName}</h2>
        <button type="button" className={buttonClass('ghost', 'sm')} aria-label="Dismiss" onClick={onDismiss}>
          <span aria-hidden="true">×</span>
        </button>
      </header>
      <dl className="month-recap-stats">
        {items.map((i) => (
          <div key={i.label} className="month-recap-stat">
            <dt>{i.label}</dt>
            <dd>{i.value}</dd>
          </div>
        ))}
      </dl>
      {top && top.days > 1 && (
        <p className="muted month-recap-top">
          Most days with <a href={workspaceHref(top.drama_id)}>{top.title}</a> · {top.days} days
        </p>
      )}
    </section>
  )
}
