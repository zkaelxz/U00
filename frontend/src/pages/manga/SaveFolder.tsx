/*
 * The folder chapters saved as CBZ files go to, on the main PC only: see it,
 * pick another (an existing folder, as a full path), go back to the default,
 * and open it in the PC's file manager. From another device none of this is
 * shown (the routes are PC only and would refuse).
 */
import { useEffect, useState, type FormEvent } from 'react'

import { getSaveFolder, openSaveFolder, setSaveFolder } from '../../api/savedComics'
import { Card } from '../../components/Card'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { buttonClass } from '../../components/uiClasses'
import { usePcOnly } from '../../hooks/usePcOnly'
import type { SavedComicsFolder } from '../../types/savedComics'
import './manga.css'

export function OpenFolderButton({ size = 'md' }: { size?: 'md' | 'sm' }) {
  const pc = usePcOnly()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  if (pc === 'remote') return null
  const open = async () => {
    setError(null)
    setBusy(true)
    try {
      await openSaveFolder()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <button type="button" className={buttonClass('secondary', size)} disabled={busy} onClick={open}>
        Open folder
      </button>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </>
  )
}

export function SaveFolderCard() {
  const pc = usePcOnly()
  const [folder, setFolder] = useState<SavedComicsFolder | null>(null)
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    if (pc === 'remote') return
    getSaveFolder().then(setFolder, setError)
  }, [pc])

  if (pc === 'remote') return null

  const save = async (value: string) => {
    setError(null)
    setBusy(true)
    try {
      setFolder(await setSaveFolder(value))
      setDraft('')
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (draft.trim()) void save(draft)
  }

  return (
    <Card
      className="manga-folder"
      aria-label="Save folder"
      title="Save folder"
      meta={folder ? (folder.custom ? 'Picked on this PC' : 'Default') : 'PC only'}
      actions={<OpenFolderButton size="sm" />}
    >
      <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ serverText: true }} />
      {folder && (
        <p>
          Chapters are saved to <code data-testid="save-folder">{folder.folder}</code>
        </p>
      )}
      {folder?.picked_missing && (
        <p className="warn" role="status">
          The folder you picked can’t be found (an unplugged drive?), so saves go to the default for now.
        </p>
      )}
      <form className="manga-folder-form" onSubmit={submit}>
        <Field label="Save to another folder" help="A full path to a folder that already exists, for example D:\Manga or a Jellyfin library folder.">
          <input
            type="text"
            value={draft}
            maxLength={1000}
            autoComplete="off"
            spellCheck={false}
            placeholder="D:\Manga"
            onChange={(e) => setDraft(e.target.value)}
          />
        </Field>
        <div className="actions">
          <button type="submit" className={buttonClass('secondary', 'sm')} disabled={busy || !draft.trim()}>
            Use this folder
          </button>
          {folder?.custom && (
            <button type="button" className={buttonClass('ghost', 'sm')} disabled={busy} onClick={() => save('')}>
              Use the default
            </button>
          )}
        </div>
      </form>
    </Card>
  )
}
