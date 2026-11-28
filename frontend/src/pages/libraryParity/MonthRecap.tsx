import { useCallback, useState } from 'react'

import { getRecap } from '../../api/library'
import { workspaceHref } from '../../components/libraryView'
import { buttonClass } from '../../components/uiClasses'
import { browserStorage } from '../../hooks/usePersistedState'
import { useLoad } from '../../hooks/useLoad'
import { useSession } from '../../hooks/useSession'
import type { LibraryRecap } from '../../types/library'
import { dismissKey, isDismissed, recapItems, recapMonth, rememberDismissed } from './monthRecapLogic'

export function MonthRecap() {
  const session = useSession()
  const [when] = useState(() => recapMonth(new Date()))
  const [hidden, setHidden] = useState<string | null>(null)
  // The dismissal is per account, so nothing is asked until the session says whose it is.
  const account = session.status === 'ready' ? session.me.user?.id ?? null : undefined
  const key = when && account !== undefined ? dismissKey(account, when.month) : null
  const wanted = !!when && account !== undefined && hidden !== key && !isDismissed(browserStorage(), account, when.month)
  const month = wanted ? when.month : null
  const load = useCallback(() => (month ? getRecap(month) : Promise.resolve(null)), [month])
  const recap = useLoad(load, 0)
  if (!wanted || !recap.data || account === undefined) return null
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
