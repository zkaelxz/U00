// "Deliver as GitHub PR" for one proposed fix. Preview first: it
// shows the exact diff that will be sent (from the server), the files and
// the base; only then can the viewer confirm, once, for that exact diff.
import { useState } from 'react'

import { deliverGithubPr, previewGithubPr } from '../../api/assistantGithub'
import { ConfirmButton } from '../../components/ConfirmButton'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import type { GithubDelivered, GithubPreview, GithubStatus } from '../../types/assistant'
import { CHANGE_LABEL, defaultPrTitle, githubErrorText, githubNotReadyReason, githubReady } from './githubFormat'

type Props = { patch: string; question: string; github: GithubStatus | null }

export function DeliverPr({ patch, question, github }: Props) {
  const [open, setOpen] = useState(false)
  const [title, setTitle] = useState(() => defaultPrTitle(question))
  const [body, setBody] = useState('')
  const [preview, setPreview] = useState<GithubPreview | null>(null)
  const [done, setDone] = useState<GithubDelivered | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  if (!github) return null
  if (!githubReady(github)) {
    return <p className="muted assistant-deliver-off">GitHub pull requests: {githubNotReadyReason(github)}</p>
  }
  if (done) {
    return (
      <p role="status" className="assistant-deliver-done">
        Draft pull request opened on {done.repo}
        {done.pr_url ? (
          <>
            :{' '}
            <a href={done.pr_url} target="_blank" rel="noreferrer noopener">
              #{done.pr_number ?? 'open'}
            </a>
          </>
        ) : null}{' '}
        (branch {done.branch} into {done.base_branch}).
      </p>
    )
  }
  if (!open) {
    return (
      <button type="button" className={buttonClass('secondary', 'sm')} onClick={() => setOpen(true)}>
        Deliver as GitHub PR…
      </button>
    )
  }

  const doPreview = () => {
    setBusy(true)
    setError(null)
    previewGithubPr(patch, title).then(
      (p) => {
        setBusy(false)
        setPreview(p)
      },
      (e: unknown) => {
        setBusy(false)
        setError(githubErrorText(e))
      },
    )
  }

  const deliver = () => {
    if (!preview) return
    setBusy(true)
    setError(null)
    deliverGithubPr(preview.patch, preview.title, body, preview.sha256).then(
      (d) => {
        setBusy(false)
        setDone(d)
      },
      (e: unknown) => {
        setBusy(false)
        setError(githubErrorText(e))
      },
    )
  }

  return (
    <div className="assistant-deliver" aria-label="Deliver as GitHub PR" role="group">
      <Field label="Pull request title">
        <input
          type="text"
          value={title}
          maxLength={200}
          onChange={(e) => {
            setTitle(e.target.value)
            setPreview(null)
          }}
        />
      </Field>
      <Field label="Description" help="Optional. Added under a line saying the assistant proposed this fix.">
        <textarea rows={2} value={body} maxLength={20000} onChange={(e) => setBody(e.target.value)} />
      </Field>
      {!preview ? (
        <div className="assistant-actions">
          <button type="button" className={buttonClass('primary', 'sm')} disabled={busy || !title.trim()} onClick={doPreview}>
            {busy ? 'Checking…' : 'Preview pull request'}
          </button>
          <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy} onClick={() => setOpen(false)}>
            Cancel
          </button>
        </div>
      ) : (
        <div data-testid="github-preview">
          <p>
            A new branch <code>{preview.branch_prefix}…</code> on <strong>{preview.repo}</strong>, as a draft pull request into{' '}
            <strong>{preview.base_branch}</strong>. Nothing is pushed to {preview.base_branch}.
          </p>
          <ul className="assistant-deliver-files">
            {preview.files.map((f) => (
              <li key={f.path}>
                <code>{f.path}</code> <span className="muted">({CHANGE_LABEL[f.change]})</span>
              </li>
            ))}
          </ul>
          <p className="muted">This exact diff will be sent:</p>
          <pre className="assistant-pre">{preview.patch}</pre>
          <div className="assistant-actions">
            <ConfirmButton
              name="this pull request"
              label="Open draft pull request…"
              confirmLabel="Confirm: open the draft pull request"
              verb="open"
              tone="primary"
              busy={busy}
              onConfirm={deliver}
            />
            <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy} onClick={() => setPreview(null)}>
              Back
            </button>
          </div>
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
