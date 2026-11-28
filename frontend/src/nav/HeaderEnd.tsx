/** The header's right-hand controls: search, Jobs, the bell, the server-down badge, and the You menu (wide) or the PIN and account buttons (phone). */
import { useEffect, useRef, useState } from 'react'

import { type AuthUser } from '../api/auth'
import { api } from '../api/client'
import { lockPcProfile, rememberProfilePicked } from '../api/pcProfile'
import { watchApiReachability, type ApiState } from '../apiReachability'
import { HelpTip } from '../components/HelpTip'
import { JobsMenu } from '../components/JobsMenu'
import { NotificationBell } from '../components/NotificationBell'
import { ThemeChoices } from '../components/ThemeMenu'
import { buttonClass } from '../components/uiClasses'
import { useDetailsMenu } from '../hooks/useDetailsMenu'
import { usePcProfile } from '../hooks/usePcProfile'
import { canSignOut, menuUser, signOut, type SessionState } from '../hooks/useSession'
import { ReportProblemHost } from '../report/ReportProblem'
import { openReportDialog } from '../report/reportDialogStore'
import type { Route } from '../router'
import { CommandPalette } from './CommandPalette'
import { openShortcutHelp } from './shortcutHelp'
import type { NavContext } from './navItems'
import { ViewModeSwitch } from './ViewModeSwitch'
import type { ViewMode } from './viewMode'

// Not a live region: it would be read out on every page. Before the first answer nothing shows, so a slow start
// never claims a state it hasn't checked.
function ApiDownBadge({ state }: { state: ApiState }) {
  if (state !== 'down') return null
  return (
    <span className="badge bad api-down" data-testid="api-status">
      Can't reach Baihe
      <HelpTip label="Can't reach Baihe" id="api-status-help">Check that Baihe Studio is still running on this PC.</HelpTip>
    </span>
  )
}

// The display name wins over the email: an address's first letter is rarely the person's.
function accountInitial(user: AuthUser | null): string | null {
  const first = (user?.display_name || user?.email || '').trim().charAt(0)
  return /\p{L}|\p{N}/u.test(first) ? first.toLocaleUpperCase() : null
}

function Avatar({ user, dot }: { user: AuthUser | null; dot?: boolean }) {
  const initial = accountInitial(user)
  return (
    <span className="user-menu-avatar hb-icon" aria-hidden="true">
      {initial ?? (
        <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <circle cx="12" cy="8" r="4" />
          <path d="M4 21c0-4 3.6-7 8-7s8 3 8 7" />
        </svg>
      )}
      {dot && <span className="you-menu-dot" data-testid="api-connected" />}
    </span>
  )
}

// With /me unavailable the user is unknown, but Sign out stays reachable.
function AccountRows({ user }: { user: AuthUser | null }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Never shown on the closed button: the address is long and private enough that it only shows inside the open panel.
  const label = user ? (user.email ?? user.display_name ?? 'Signed in') : 'Account'

  async function onSignOut() {
    setBusy(true)
    setError(null)
    try {
      await signOut()
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't sign out. Please try again.")
      setBusy(false)
    }
  }

  return (
    <>
      {user ? (
        <>
          <p className="muted">Signed in as</p>
          {user.email && user.display_name?.trim() && <p className="user-menu-fullname" data-testid="user-name">{user.display_name.trim()}</p>}
          <p className="user-menu-email" data-testid="user-email">{label}</p>
        </>
      ) : (
        <p className="muted">Can't check who is signed in right now.</p>
      )}
      <button type="button" onClick={onSignOut} disabled={busy}>
        {busy ? 'Signing out…' : 'Sign out'}
      </button>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </>
  )
}

// Phone header: signed in with auth on. Absent with auth off.
function UserMenu({ user }: { user: AuthUser | null }) {
  const ref = useDetailsMenu()
  const label = user ? (user.email ?? user.display_name ?? 'Signed in') : 'Account'
  return (
    <details className="user-menu" ref={ref}>
      <summary className="hb-box" aria-label={`Account: ${user?.display_name ?? label}`} title={user?.display_name ?? label}>
        <Avatar user={user} />
      </summary>
      <div className="user-menu-panel">
        <AccountRows user={user} />
      </div>
    </details>
  )
}

// Locks the PC window back to Guest and asks again, so a title stays hidden after the owner walks away.
function ProfileSwitch({ guest }: { guest: boolean }) {
  const switchProfile = async () => {
    try {
      await lockPcProfile()
    } catch {
      return
    }
    rememberProfilePicked(false)
    window.location.reload()
  }
  return (
    <button type="button" className={buttonClass()} data-testid="profile-switch" onClick={() => void switchProfile()}>
      {guest ? 'Guest · Switch profile' : 'Owner · Switch profile'}
    </button>
  )
}

// The approved header keeps search, Jobs and the bell; the rarer controls live in one menu. The dot on the avatar says the
// server answered the last check, and the "Can't reach Baihe" badge stays in the row.
function YouMenu({ session, apiUp, viewMode, onViewMode }: { session: SessionState; apiUp: boolean; viewMode: ViewMode; onViewMode: (m: ViewMode) => void }) {
  const ref = useDetailsMenu()
  const user = menuUser(session)
  const profile = usePcProfile()
  const name = user?.display_name?.trim() || user?.email || null
  const summary = useRef<HTMLElement>(null)
  // A row that opens a dialog closes the menu first and leaves focus on its button, which the dialog returns to.
  const run = (open: () => void) => {
    if (ref.current) ref.current.open = false
    summary.current?.focus()
    open()
  }
  return (
    <details className="you-menu user-menu" ref={ref}>
      <summary ref={summary} className="hb-box" aria-label={name ? `You: ${name}` : 'You'} title={name ?? 'You'}>
        <span className="you-menu-word">You</span>
        <Avatar user={user} dot={apiUp} />
      </summary>
      <div className="user-menu-panel you-menu-panel">
        <ViewModeSwitch mode={viewMode} onChange={onViewMode} />
        <ThemeChoices />
        <button type="button" className="you-menu-row" aria-haspopup="dialog" onClick={() => run(openShortcutHelp)}>
          Keyboard shortcuts
        </button>
        <button type="button" className="you-menu-row" aria-haspopup="dialog" onClick={() => run(openReportDialog)}>
          Report a problem
        </button>
        {profile.status?.pin_set && <ProfileSwitch guest={profile.status.guest} />}
        {canSignOut(session) && (
          <div className="you-menu-account">
            <AccountRows user={user} />
          </div>
        )}
      </div>
    </details>
  )
}

export function HeaderEnd({ route, context, room, session, viewMode, onViewMode }: {
  route: Route
  context: NavContext
  /** A wide window or a pop-out: room for the You menu. */
  room: boolean
  session: SessionState
  viewMode: ViewMode
  onViewMode: (m: ViewMode) => void
}) {
  const [apiState, setApiState] = useState<ApiState>('unknown')
  const profile = usePcProfile()
  useEffect(() => watchApiReachability(() => api.meta(), setApiState, window, document), [])
  return (
    <div className={room ? 'header-end has-room' : 'header-end'}>
      <CommandPalette route={route} context={context} />
      <ReportProblemHost />
      <JobsMenu />
      <NotificationBell />
      <ApiDownBadge state={apiState} />
      {room ? (
        <YouMenu session={session} apiUp={apiState === 'up'} viewMode={viewMode} onViewMode={onViewMode} />
      ) : (
        <>
          {profile.status?.pin_set && <ProfileSwitch guest={profile.status.guest} />}
          {canSignOut(session) && <UserMenu user={menuUser(session)} />}
        </>
      )}
    </div>
  )
}
