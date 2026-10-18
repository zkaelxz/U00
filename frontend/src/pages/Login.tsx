/*
 * Login: the only screen when sign-in is on and nobody is signed in.
 * "Sign in with Google" is a plain same-tab link: a first login page has no
 * drafts to protect (the sign-in overlay keeps its new tab for that reason).
 * The server redirects to Google and back, then to `return_to`. The page
 * rechecks /me when it regains focus or on "I've signed in". A failed
 * sign-in comes back as /?login_error=<code>, shown here in plain words.
 */
import { useEffect, useState } from 'react'

import { recheckSession } from '../hooks/useSession'
import { loginErrorMessage, loginHref, readLoginError } from '../api/auth'
import './login.css'

function currentSearch(): string {
  return typeof window === 'undefined' ? '' : window.location.search
}

/** The page to come back to: path + hash, never the login_error query. */
function currentReturnTo(): string {
  if (typeof window === 'undefined') return '/'
  return `${window.location.pathname}${window.location.hash}`
}

export default function LoginPage({ configured }: { configured: boolean }) {
  const [errorCode] = useState(() => readLoginError(currentSearch()))
  const [stillOut, setStillOut] = useState(false)

  const recheck = async () => {
    await recheckSession()
    // Success swaps this page for the app, so only a failed recheck is still here to report.
    setStillOut(true)
  }

  // Drop ?login_error from the address bar so a reload doesn't repeat the message.
  useEffect(() => {
    if (!errorCode) return
    const params = new URLSearchParams(window.location.search)
    params.delete('login_error')
    const qs = params.toString()
    window.history.replaceState(
      window.history.state,
      '',
      `${window.location.pathname}${qs ? `?${qs}` : ''}${window.location.hash}`,
    )
  }, [errorCode])

  // Sign-in finishes in another tab, so this one must notice when the person comes back.
  useEffect(() => {
    const onFocus = () => void recheckSession()
    window.addEventListener('focus', onFocus)
    return () => window.removeEventListener('focus', onFocus)
  }, [])

  return (
    <main className="login-page">
      <section className="panel login-card" aria-labelledby="login-title">
        <h1 id="login-title">Baihe Studio</h1>
        {errorCode && (
          <p className="login-error" role="alert">
            {loginErrorMessage(errorCode)}
          </p>
        )}
        {configured ? (
          <>
            <p className="muted">Sign in with the Google account the PC owner added to this household.</p>
            <a className="login-button" href={loginHref(currentReturnTo())}>
              Sign in with Google
            </a>
            <button type="button" onClick={() => void recheck()}>
              I've signed in
            </button>
            {stillOut && (
              <p className="muted" role="status">
                Still not signed in
              </p>
            )}
          </>
        ) : (
          <div className="login-unset" data-testid="login-not-configured">
            <p>
              <strong>Sign-in isn't set up on the PC yet.</strong>
            </p>
            <p className="muted">
              The person who runs Baihe on the main PC needs to add the Google sign-in settings and restart it.
              Until then, use Baihe on the PC itself.
            </p>
          </div>
        )}
      </section>
    </main>
  )
}
