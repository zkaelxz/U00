/*
 * "Add a Comic": makes a comic title and puts its first pages in by one of three ways (a pasted link, page
 * images, a CBZ file), reading in the chosen mode. Opens over the Comic page; on success it opens the new title.
 */
import { useState } from 'react'

import { createDrama, deleteDrama } from '../../api/library'
import { scanlateApi } from '../../api/scanlate'
import { startComicUrlImport } from '../../api/sourcesExtraction'
import { ErrorBanner } from '../../components/ErrorBanner'
import { Field } from '../../components/Field'
import { humanize } from '../../components/labels'
import { Sheet } from '../../components/Sheet'
import { Tabs } from '../../components/Tabs'
import { buttonClass } from '../../components/uiClasses'
import { browserStorage } from '../../hooks/usePersistedState'
import { useCanUpload } from '../../hooks/useHolds'
import { routeHref } from '../../router'
import { SOURCE_LANGUAGES } from '../libraryForm'
import { newDramaRequest } from '../sources/urlImportFormat'
import { ADD_WAYS, addComic, canAdd, cbzPages, folderPages, startPrefs, type AddComicInput, type AddWay } from './addComicLogic'
import { MODE_OPTIONS, saveComicPrefs, type ComicMode } from './comicLogic'

const PICK: Record<Exclude<AddWay, 'link'>, { accept: string; multiple: boolean }> = {
  images: { accept: '.png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp', multiple: true },
  file: { accept: '.cbz,application/vnd.comicbook+zip,application/zip', multiple: false },
  folder: { accept: '', multiple: true },
}

export function AddComicDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const canUpload = useCanUpload()
  const [title, setTitle] = useState('')
  const [language, setLanguage] = useState('zh')
  const [way, setWay] = useState<AddWay>('link')
  const [link, setLink] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [mode, setMode] = useState<ComicMode>('vertical')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  // The title an earlier try made, so a retry after a failed import does not make a second one.
  const [made, setMade] = useState<{ id: number; mediaType: string } | null>(null)

  const input: AddComicInput = { title, way, link, files }
  const ways = canUpload ? ADD_WAYS : ADD_WAYS.filter((w) => w.id === 'link')

  const add = async () => {
    setBusy(true)
    setError(null)
    const request = newDramaRequest(title, language, true)
    const mediaType = request.media_type ?? 'manhua'
    let id = made?.id ?? null
    let freshId: number | null = null
    try {
      const pages =
        way === 'file' ? cbzPages(new Uint8Array(await files[0].arrayBuffer())) : way === 'folder' ? folderPages(files) : files
      if (id === null) {
        id = (await createDrama(request)).id
        freshId = id
        setMade({ id, mediaType })
      }
      id = await addComic({ ...input, files: pages }, request, id, {
        create: createDrama,
        importLink: (url, dramaId) => startComicUrlImport(url, dramaId, {}),
        upload: (dramaId, f) => scanlateApi.upload(dramaId, f, true),
      })
      saveComicPrefs(browserStorage(), id, startPrefs(mediaType, mode))
      onClose()
      window.location.hash = routeHref({ name: 'comic', id, page: null })
    } catch (e) {
      setError(e)
      // An empty title nobody asked to keep. When it can't be removed (not on the PC), the retry reuses it.
      if (freshId !== null) {
        deleteDrama(freshId).then(() => setMade(null), () => undefined)
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <Sheet open={open} title="Add a Comic" onClose={onClose}>
      <div className="stack add-comic">
        <Field label="Title">
          <input type="text" value={title} maxLength={300} disabled={made !== null} onChange={(e) => setTitle(e.target.value)} />
        </Field>
        <Field label="Language">
          <select value={language} disabled={made !== null} onChange={(e) => setLanguage(e.target.value)}>
            {SOURCE_LANGUAGES.map((l) => <option key={l} value={l}>{humanize('language', l)}</option>)}
          </select>
        </Field>
        <Tabs
          label="Add a Comic"
          idPrefix="add-comic"
          tabs={ways}
          active={way}
          onSelect={(w) => {
            setWay(w)
            setFiles([])
            setError(null)
          }}
        />
        {way === 'link' ? (
          <Field label="Link">
            <input type="url" value={link} onChange={(e) => setLink(e.target.value)} />
          </Field>
        ) : (
          <Field label={ADD_WAYS.find((w) => w.id === way)!.label}>
            <input
              key={way}
              type="file"
              accept={PICK[way].accept}
              multiple={PICK[way].multiple}
              {...(way === 'folder' ? { webkitdirectory: '' } : {})}
              onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
            />
          </Field>
        )}
        <Field label="Reading mode">
          <select value={mode} onChange={(e) => setMode(e.target.value as ComicMode)}>
            {MODE_OPTIONS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
          </select>
        </Field>
        <ErrorBanner error={error} onDismiss={() => setError(null)} describe={{ serverText: true }} />
        <div className="actions">
          <button type="button" className={buttonClass('primary')} disabled={busy || !canAdd(input)} onClick={() => void add()}>
            {busy ? 'Adding…' : 'Add'}
          </button>
        </div>
      </div>
    </Sheet>
  )
}
