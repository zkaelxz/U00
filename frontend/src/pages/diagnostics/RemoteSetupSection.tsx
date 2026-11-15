/*
 * People and access > Set up remote access: the steps of docs/household-access.md in
 * order, each with what the app can see about it. Read-only and PC only; the API
 * answers states and fixed sentences, never the public name, the Google id or secret.
 * Steps the PC can't see (the router) say so instead of guessing.
 */
import { useCallback, useEffect, useState } from 'react'

import { getRemoteSetup, saveRemoteSignIn } from '../../api/diagnostics'
import { Badge } from '../../components/Badge'
import { ErrorBanner } from '../../components/ErrorBanner'
import { ConfirmButton } from '../../components/ConfirmButton'
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { buttonClass } from '../../components/uiClasses'
import type { PcMode } from '../../hooks/usePcOnly'
import type { RemoteSetup } from '../../types/diagnostics'
import { ApiError } from '../../api/client'
import { keyWritesRefused } from '../../components/errorMessages'
import { redirectAddress, setupSummary, signInProblem, stepBadge } from './remoteSetupModel'

export function RemoteSetupSection({ pc }: { pc: PcMode }) {
  if (pc === 'remote') return null
  return <RemoteSetupBody />
}

function RemoteSetupBody() {
  const [setup, setSetup] = useState<RemoteSetup | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(() => {
    setBusy(true)
    getRemoteSetup().then(
      (r) => {
        setSetup(r)
        setBusy(false)
      },
      (e: unknown) => {
        setError(e)
        setBusy(false)
      },
    )
  }, [])

  useEffect(load, [load])

  const [draft, setDraft] = useState({ name: '', id: '', secret: '' })
  const [saving, setSaving] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const problem = signInProblem(draft)
  const redirect = redirectAddress(draft.name)
  const empty = !draft.name.trim() && !draft.id.trim() && !draft.secret.trim()

  const save = () => {
    setSaving(true)
    setFormError(null)
    const body = {
      ...(draft.name.trim() && { public_url: draft.name.trim() }),
      ...(draft.id.trim() && { google_client_id: draft.id.trim() }),
      ...(draft.secret.trim() && { google_client_secret: draft.secret.trim() }),
    }
    setDraft({ name: '', id: '', secret: '' }) // the request already holds the values; drop them now
    saveRemoteSignIn(body).then(
      (r) => {
        setSetup(r)
        setSaving(false)
      },
      (e: unknown) => {
        // A 422 text is fixed and never echoes a value; a 403 on the PC means key writes are off.
        setFormError(e instanceof ApiError && e.status === 403 ? keyWritesRefused() : e instanceof ApiError && e.status === 422 ? e.message : 'Could not save. Try again.')
        setSaving(false)
      },
    )
  }

  return (
    <Section title="Set up remote access" storageKey="admin.remote-setup" summary={setup ? setupSummary(setup) : undefined}>
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ pcOnly: true }} />
      {!setup ? (
        !error && <p className="muted">Loading…</p>
      ) : (
        <>
          <ol className="diag-rows diag-stack" data-testid="remote-setup-steps">
            {setup.steps.map((s) => {
              const badge = stepBadge(s)
              return (
                <li key={s.id} data-testid={`remote-setup-${s.id}`}>
                  <strong>{s.title}</strong> <Badge tone={badge.tone}>{badge.label}</Badge>
                  {s.id === setup.next && <Badge tone="neutral">Next</Badge>}
                  <br />
                  <span className="muted">{s.message}</span>
                </li>
              )
            })}
          </ol>
          <div className="status-form" data-testid="remote-sign-in-form">
            <h4>Sign-in settings</h4>
            <p className="settings-note">
              Saved in this PC&apos;s .env. They are write-only: Baihe never shows them back. Leave a box empty to
              keep what is saved.
            </p>
            <Field label={setup.fields.public_url ? 'Public name (saved; type to replace)' : 'Public name'} error={null}>
              <input type="text" autoComplete="off" spellCheck={false} placeholder="baihe.your-domain" value={draft.name} disabled={saving} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            </Field>
            {redirect && (
              <p className="muted" data-testid="remote-redirect">
                Give Google this redirect address: <code>{redirect}</code>
              </p>
            )}
            <Field label={setup.fields.google_client_id ? 'Google client id (saved; type to replace)' : 'Google client id'} error={null}>
              <input type="text" autoComplete="off" spellCheck={false} value={draft.id} disabled={saving} onChange={(e) => setDraft({ ...draft, id: e.target.value })} />
            </Field>
            <Field label={setup.fields.google_client_secret ? 'Google client secret (saved; type to replace)' : 'Google client secret'} error={formError ?? problem}>
              <input type="password" autoComplete="new-password" spellCheck={false} value={draft.secret} disabled={saving} onChange={(e) => setDraft({ ...draft, secret: e.target.value })} />
            </Field>
            <div className="settings-actions">
              <ConfirmButton
                label="Save…"
                ariaLabel="Save sign-in settings"
                verb="save"
                tone="primary"
                name="sign-in settings"
                busy={saving}
                disabled={empty || !!problem}
                onConfirm={save}
              />
            </div>
            {setup.restart_needed && (
              <p role="status" data-testid="remote-restart-needed">
                Saved, but Baihe has not loaded it yet. Restart Baihe (on a service install, Start menu &gt; Baihe
                Studio service) and the steps above will update.
              </p>
            )}
          </div>
          <div className="settings-actions">
            <button type="button" className={buttonClass('secondary')} disabled={busy} onClick={load}>
              {busy ? 'Checking…' : 'Check again'}
            </button>
          </div>
        </>
      )}
    </Section>
  )
}
