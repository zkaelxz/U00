import { useEffect, useState } from 'react'

import { buttonClass } from '../components/uiClasses'
import { stampNavEntry } from './navHistory'

// Returns to the page the user came from, wherever that was (the fixed "‹ Library" and breadcrumb links only go up).
export function BackButton() {
  const [canGoBack, setCanGoBack] = useState(() => stampNavEntry() > 0)
  useEffect(() => {
    const sync = () => setCanGoBack(stampNavEntry() > 0)
    window.addEventListener('hashchange', sync)
    window.addEventListener('popstate', sync)
    return () => {
      window.removeEventListener('hashchange', sync)
      window.removeEventListener('popstate', sync)
    }
  }, [])
  if (!canGoBack) return null
  return (
    <button type="button" className={buttonClass('ghost', 'sm', 'app-back')} aria-label="Return to the last page" onClick={() => window.history.back()}>
      <span aria-hidden="true">‹</span>
      <span className="app-back-text" aria-hidden="true">Back</span>
    </button>
  )
}
