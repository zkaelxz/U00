/*
 * Login: the only screen when sign-in is on and nobody is signed in.
 * "Sign in with Google" is a plain link (a top-level navigation); the
 * server redirects to Google and back, then to `return_to`. A failed
 * sign-in comes back as /?login_error=<code>, shown here in plain words.
 */
import { useEffect, useState } from 'react'

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
