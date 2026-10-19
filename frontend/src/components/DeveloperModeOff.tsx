import { routeHref } from '../router'

/** The notice a Developer Mode page shows when it is opened by URL with the mode off. */
export function DeveloperModeOff() {
  return (
    <p className="muted" role="status">
      Developer Mode is off. <a href={routeHref({ name: 'settings', section: 'developer-mode' })}>Turn on in Settings</a>
    </p>
  )
}
