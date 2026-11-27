// A floating "5 / 13" that appears while the page changes, then fades, so the position shows even with the bars hidden.
import { useEffect, useState } from 'react'

const SHOW_MS = 1600

export function ComicPageBadge({ label, phone }: { label: string; phone: boolean }) {
  const [shown, setShown] = useState(false)
  useEffect(() => {
    setShown(true)
    const t = window.setTimeout(() => setShown(false), SHOW_MS)
    return () => window.clearTimeout(t)
  }, [label])
  return (
    <div className={`comic-page-badge${phone ? ' comic-page-badge-phone' : ''}${shown ? ' comic-page-badge-on' : ''}`} data-testid="comic-page-badge" aria-hidden="true">
      {label}
    </div>
  )
}
