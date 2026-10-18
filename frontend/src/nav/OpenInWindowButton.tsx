import { buttonClass } from '../components/uiClasses'
import { openInNewWindow } from './popOut'

/** Opens the page being viewed in a slim second window; the address comes from the browser so a stage or tab is kept. */
export function OpenInWindowButton({ className }: { className?: string }) {
  return (
    <button
      type="button"
      className={buttonClass('secondary', 'sm', className)}
      data-testid="open-in-window"
      title="Open this page in a second window to work on another page at the same time"
      onClick={() => openInNewWindow(window.location.hash || '#/')}
    >
      Open in new window ↗
    </button>
  )
}
