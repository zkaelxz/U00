// The sheet a long press (or right-click) on a comic page opens: save or share the picture, or make it the cover.
import { useEffect, useRef, useState } from 'react'

import { fetchComicImage } from '../../api/comic'
import { uploadCover } from '../../api/metadata'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Sheet } from '../../components/Sheet'
import { buttonClass } from '../../components/uiClasses'
import { useCanUpload } from '../../hooks/useHolds'
import { saveInShell } from '../../phoneShell'
import { COVER_TYPES, pageFileName } from './pageActionsLogic'

export interface PagePick {
  number: number
  // The picture as the reader shows it now (typeset or original).
  src: string
}

async function pageFile(pick: PagePick, title: string): Promise<File> {
  const blob = await fetchComicImage(pick.src)
  return new File([blob], pageFileName(title, pick.number, blob.type), { type: blob.type })
}

function canShareFiles(): boolean {
  return typeof navigator !== 'undefined' && typeof navigator.share === 'function' && typeof navigator.canShare === 'function'
}

type Props = { dramaId: number; title: string; pick: PagePick | null; onClose: () => void }

export function PageActions({ dramaId, title, pick, onClose }: Props) {
  const canUpload = useCanUpload()
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [done, setDone] = useState<string | null>(null)

  // Fetched when the menu opens: a browser only lets a page share within a few seconds of the tap,
  // too short for a download on a slow link.
  const [fetched, setFetched] = useState<{ src: string; file: File } | null>(null)
  const open = useRef(true)
  useEffect(() => {
    open.current = true
    return () => {
      open.current = false
    }
  }, [])
  const src = pick?.src
  const number = pick?.number
  useEffect(() => {
    if (src === undefined || number === undefined) return
    let stale = false
    pageFile({ src, number }, title).then(
      (got) => {
        if (!stale) setFetched({ src, file: got })
      },
      (e: unknown) => {
        if (!stale) setError(e)
      },
    )
    return () => {
      stale = true
    }
  }, [src, number, title])
  const file = fetched && fetched.src === src ? fetched.file : null

  const close = () => {
    setError(null)
    setDone(null)
    onClose()
  }
  const run = (name: string, work: (file: File) => Promise<string | null>) => {
    if (!file) return
    setBusy(name)
    setError(null)
    setDone(null)
    work(file).then(
      (note) => {
        // A menu closed while this ran must not close the one opened since.
        if (!open.current) return
        setBusy(null)
        if (note) setDone(note)
        else close()
      },
      (e: unknown) => {
        if (!open.current) return
        setBusy(null)
        // Closing the phone's share menu is not a failure.
        if (!(e instanceof DOMException && e.name === 'AbortError')) setError(e)
      },
    )
  }

  const save = () =>
    run('save', async (file) => {
      if (saveInShell(file, file.name)) return null
      const url = URL.createObjectURL(file)
      const a = document.createElement('a')
      a.href = url
      a.download = file.name
      a.click()
      // Revoked later: some browsers read the link after the click returns.
      setTimeout(() => URL.revokeObjectURL(url), 60_000)
      return null
    })
  const share = () =>
    run('share', async (file) => {
      if (!navigator.canShare({ files: [file] })) throw new Error("This browser can't share pictures. Use Save picture instead.")
      await navigator.share({ files: [file], title: file.name })
      return null
    })
  const cover = () =>
    run('cover', async (file) => {
      if (!COVER_TYPES.includes(file.type)) throw new Error('A cover must be a JPEG, PNG or WebP picture.')
      await uploadCover(dramaId, file)
      return 'This page is now the cover.'
    })

  return (
    <Sheet open={pick !== null} title={pick ? `Page ${pick.number}` : 'Page'} onClose={close}>
      <div className="stack" data-testid="page-actions">
        <button type="button" className={buttonClass('secondary')} disabled={busy !== null || !file} onClick={save}>
          {busy === 'save' ? 'Saving…' : 'Save picture'}
        </button>
        {canShareFiles() && (
          <button type="button" className={buttonClass('secondary')} disabled={busy !== null || !file} onClick={share}>
            {busy === 'share' ? 'Opening…' : 'Share picture'}
          </button>
        )}
        {canUpload && (
          <button type="button" className={buttonClass('secondary')} disabled={busy !== null || !file} onClick={cover}>
            {busy === 'cover' ? 'Setting…' : 'Set as cover'}
          </button>
        )}
        {done && <p role="status">{done}</p>}
        <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ serverText: true }} />
      </div>
    </Sheet>
  )
}
