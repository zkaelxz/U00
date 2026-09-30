// Step 72: GitHub delivery settings. Off by default; the token is
// write-only (never shown back). Until it's on with a token and a repo,
// no GitHub call is made.
import { useState } from 'react'

import { clearGithubToken, saveGithubSettings, setGithubToken, testGithub } from '../../api/assistantGithub'
import { Badge } from '../../components/Badge'
import { Card } from '../../components/Card'
import { ConfirmButton } from '../../components/ConfirmButton'
import { Field } from '../../components/Field'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import type { GithubConnection, GithubStatus } from '../../types/assistant'
import { githubErrorText } from './githubFormat'

type Props = { status: GithubStatus; onStatus: (s: GithubStatus) => void }

export function GithubCard({ status, onStatus }: Props) {
  const [repo, setRepo] = useState(status.repo ?? '')
  const [base, setBase] = useState(status.base_branch)
  const [token, setToken] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [conn, setConn] = useState<GithubConnection | null>(null)

  const run = <T,>(p: Promise<T>, done: (v: T) => void, tokenWrite = false) => {
    setBusy(true)
    setError(null)
    p.then(
      (v) => {
        setBusy(false)
        done(v)
      },
      (e: unknown) => {
        setBusy(false)
        setError(githubErrorText(e, tokenWrite))
      },
    )
  }

  const changed = repo.trim() !== (status.repo ?? '') || base.trim() !== status.base_branch

  return (
    <Card
      title="GitHub pull requests"
      meta="Send a proposed fix to your own repository as a draft pull request, after you review the exact diff. Off by default."
      aria-label="GitHub pull requests"
    >
      <div className="setting-list">
        <Field label="Deliver fixes as pull requests" help="Nothing is sent to GitHub while this is off.">
          <Toggle
            checked={status.enabled}
            disabled={busy}
            onChange={(next) => run(saveGithubSettings({ enabled: next }), onStatus)}
          />
        </Field>
      </div>
      <div className="assistant-engine">
        <Field label="Repository" help="owner/name of your own repository.">
          <input type="text" value={repo} spellCheck={false} autoComplete="off" placeholder="owner/name" onChange={(e) => setRepo(e.target.value)} />
        </Field>
        <Field label="Base branch" help={`Pull requests go into this branch. The app never pushes to it; it creates a new ${status.branch_prefix}… branch for each fix.`}>
          <input type="text" value={base} spellCheck={false} autoComplete="off" onChange={(e) => setBase(e.target.value)} />
        </Field>
      </div>
      <div className="assistant-actions">
        <button
          type="button"
          className={buttonClass('secondary', 'sm')}
          disabled={busy || !changed}
          onClick={() => run(saveGithubSettings({ repo: repo.trim() || null, base_branch: base.trim() || null }), onStatus)}
        >
          Save repository
        </button>
      </div>
      {/* Not a <form>: a browser shouldn't offer to save the token as a password. */}
      <div className="assistant-engine">
        <Field
          label="Token"
          help="A fine-grained GitHub token with Contents and Pull requests write access to this repository only (not Workflows). Stored on this PC; never shown again."
        >
          <input type="password" value={token} autoComplete="new-password" placeholder={status.token_configured ? 'Set (hidden)' : 'Paste a token'} onChange={(e) => setToken(e.target.value)} />
        </Field>
        <div className="assistant-actions">
          <ConfirmButton
            name="GitHub token"
            label="Save token…"
            confirmLabel="Confirm: save the GitHub token"
            verb="save"
            tone="primary"
            busy={busy}
            disabled={!token.trim()}
            onConfirm={() =>
              run(
                setGithubToken(token.trim()),
                (r) => {
                  setToken('')
                  onStatus({ ...status, token_configured: r.token_configured })
                },
                true,
              )
            }
          />
          {status.token_configured && (
            <ConfirmButton
              name="GitHub token"
              label="Remove token…"
              busy={busy}
              verb="remove"
              onConfirm={() => run(clearGithubToken(), (r) => onStatus({ ...status, token_configured: r.token_configured }), true)}
            />
          )}
        </div>
      </div>
      <p className="muted">
        Token: <Badge tone={status.token_configured ? 'ok' : 'neutral'}>{status.token_configured ? 'Set' : 'Not set'}</Badge>
      </p>
      <div className="assistant-actions">
        <button
          type="button"
          className={buttonClass('secondary', 'sm')}
          disabled={busy || !status.enabled || !status.token_configured || !status.repo}
          onClick={() => run(testGithub(), setConn)}
        >
          Test connection
        </button>
      </div>
      {conn && (
        <p role="status" data-testid="github-connection">
          {conn.repo}: {conn.can_push ? 'the token can push' : 'the token can’t push (read only)'}; base branch{' '}
          {conn.base_exists ? `${conn.base_branch} found` : `${conn.base_branch} not found`}.
        </p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </Card>
  )
}
