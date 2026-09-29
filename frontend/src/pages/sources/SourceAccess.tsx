/*
 * SourceAccess: inside a source's Details (Source settings, PC only).
 *
 * One "Page on this site" box feeds both parts:
 *   - Test now, one access tier at a time (static page, browser, signed-in
 *     browser); each run updates only that tier's line in the details;
 *   - Sign in (sources that support it): opens a real browser window on the
 *     PC and waits until it is closed; "Forget sign-in" deletes the saved
 *     profile. The app never sees the password or the cookies.
 * All of it is local_only on the server; the jobs' results are answered to
 * the PC only.
 */
import { useEffect, useId, useRef, useState } from 'react'

import { forgetSignin, openSignin, signinJobId, startTierTest, tierTestJobId } from '../../api/sources'
import { ConfirmButton } from '../../components/ConfirmButton'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import type { SigninResult, SourceDetail, SourceTier, TierTestResult } from '../../types/sources'
import { TIER_TESTS, pageUrlProblem, tierLabel, tierTestLine } from './sourcesFormat'
import { useSourcesJob } from './useSourcesJob'

type Props = {
  detail: SourceDetail
  // A tier test or a sign-in finished: reload the details (tiers, sign-in state).
  onChanged: () => void
  // The saved sign-in appeared or was forgotten.
  onSignin: (has: boolean) => void
}

const SIGNIN_NOTE =
  'You sign in yourself, in a browser window on this PC. Baihe never sees your password, never solves a CAPTCHA and never gets around a purchase check. Close the window once the page is open.'

export function SourceAccess({ detail, onChanged, onSignin }: Props) {
  const name = detail.name
  const [url, setUrl] = useState('')
  const [forgetting, setForgetting] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [lastTier, setLastTier] = useState<SourceTier | null>(null)
  const tier = useSourcesJob<TierTestResult>(tierTestJobId(name))
  const signin = useSourcesJob<SigninResult>(detail.auth_supported ? signinJobId(name) : null)
  const noteId = useId()

  const problem = pageUrlProblem(url)
  const page = url.trim()
  const hasLogin = !!detail.has_saved_signin
  const testing = tier.status === 'running'
  const signingIn = signin.status === 'running'

  // Reload the details once per finished run started here.
  const seen = useRef(new WeakSet<object>())
  const tierDone = tier.status === 'done' && tier.startedHere ? tier.result : null
  const signinDone = signin.status === 'done' && signin.startedHere ? signin.result : null
  useEffect(() => {
    if (tierDone && !seen.current.has(tierDone)) {
      seen.current.add(tierDone)
      onChanged()
    }
  }, [tierDone, onChanged])
  useEffect(() => {
    if (signinDone && !seen.current.has(signinDone)) {
      seen.current.add(signinDone)
      onSignin(signinDone.has_saved_signin)
      onChanged()
    }
  }, [signinDone, onChanged, onSignin])

  function test(t: SourceTier) {
    setLastTier(t)
    tier.start(() => startTierTest(name, t, page))
  }

  async function forget() {
    setError(null)
    setForgetting(true)
    try {
      const r = await forgetSignin(name)
      signin.reset()
      onSignin(r.has_saved_signin)
      onChanged()
    } catch (e) {
      setError(e)
    } finally {
      setForgetting(false)
    }
  }

  const tierResult = tier.status === 'done' ? tier.result : null
  const signinResult = signin.status === 'done' ? signin.result : null
  const needPage = !page || !!problem

  return (
    <div className="source-access" role="group" aria-label={`Access tests and sign-in: ${detail.display_name}`}>
      <Field
        label="Page on this site"
        help="Any real page on this source, e.g. a chapter. Tests and sign-in use it; the server refuses a page on another site."
        error={problem ?? undefined}
      >
        <input
          type="url"
          inputMode="url"
          autoComplete="off"
          spellCheck={false}
          placeholder="https://"
          value={url}
          maxLength={2000}
          onChange={(e) => setUrl(e.target.value)}
        />
      </Field>

      <div className="actions" aria-describedby={noteId}>
        <span>Test now:</span>
        {TIER_TESTS.map(({ tier: t }) => (
          <button
            key={t}
            type="button"
            disabled={needPage || testing || signingIn || (t === 'signed_in' && !hasLogin)}
            onClick={() => test(t)}
          >
            {testing && lastTier === t ? 'Testing…' : tierLabel(t)}
          </button>
        ))}
      </div>
      <p className="muted" id={noteId} aria-live="polite">
        {testing
          ? `Testing ${lastTier ? tierLabel(lastTier).toLowerCase() : ''}…`
          : tierResult
            ? tierTestLine(tierResult)
            : !page
              ? 'Paste a page first.'
              : detail.auth_supported && !hasLogin
                ? 'Signed-in needs a saved sign-in.'
                : 'Each test runs one tier and updates only its line above.'}
      </p>
      <ErrorBanner
        error={tier.startError ?? tier.error}
        onDismiss={tier.startError ? tier.clearStartError : tier.reset}
        describe={{ pcOnly: true, serverText: true }}
      />

      {detail.auth_supported && (
        <div className="source-signin">
          <h4>Sign in</h4>
          <p className="muted">{SIGNIN_NOTE}</p>
          <div className="actions">
            <button
              type="button"
              disabled={signingIn || testing || (!!page && !!problem)}
              onClick={() => signin.start(() => openSignin(name, page))}
            >
              {signingIn ? 'Waiting for the window…' : 'Open sign-in window'}
            </button>
            {hasLogin && (
              <ConfirmButton
                label="Forget sign-in…"
                ariaLabel={`Forget ${detail.display_name} sign-in`}
                verb="forget"
                name={`${detail.display_name} sign-in`}
                busy={forgetting}
                disabled={signingIn}
                onConfirm={forget}
              />
            )}
          </div>
          <p className="muted" aria-live="polite">
            {signingIn
              ? 'A browser window is open on the PC. Close it once the page is open; there is no time limit.'
              : signinResult
                ? signinResult.message
                : hasLogin
                  ? 'A sign-in is saved: this site is read through it.'
                  : page
                    ? 'The window opens at the page above.'
                    : 'The window opens at the page above, or at the site’s sign-in page if it has one.'}
          </p>
          {signinResult && signinResult.lines.length > 0 && (
            <details>
              <summary>What was checked</summary>
              <ul>
                {signinResult.lines.map((l, i) => (
                  <li key={i}>{l}</li>
                ))}
              </ul>
            </details>
          )}
          <ErrorBanner
            error={error ?? signin.startError ?? signin.error}
            onDismiss={() => (error ? setError(null) : signin.startError ? signin.clearStartError() : signin.reset())}
            describe={{ pcOnly: true, serverText: true }}
          />
        </div>
      )}
    </div>
  )
}
