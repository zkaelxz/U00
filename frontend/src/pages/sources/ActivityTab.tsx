import { ErrorBanner } from '../../components/ErrorBanner'
import { emptyTabMessage } from './manageTabs'
import { RecentExtractions } from './RecentExtractions'
import { useExtractions } from './useExtractions'

/** What Baihe did with pasted links. With nothing to list, the tab says so instead of showing an empty panel. */
export function ActivityTab() {
  const x = useExtractions()
  if (x.rows && x.rows.length > 0) return <RecentExtractions {...x} rows={x.rows} />
  if (x.error) return <ErrorBanner error={x.error} onDismiss={x.clearError} />
  return <p className="muted">{x.rows ? emptyTabMessage('activity') : 'Loading…'}</p>
}
