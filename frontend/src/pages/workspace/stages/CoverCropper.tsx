import { useEffect, useRef, useState } from 'react'

import { comicApi, comicImageUrl, fetchComicImage } from '../../../api/comic'
import { mediaStreamUrl } from '../../../api/media'
import { uploadCover } from '../../../api/metadata'
import type { ComicPageInfo } from '../../../types/comic'
import { ErrorBanner } from '../../../components/ErrorBanner'
import { buttonClass } from '../../../components/uiClasses'
import { cropRect, outputSize } from './coverCrop'

type Source = 'comic' | 'video'

/**
 * Picks a picture for the cover and crops it to the cover shape: a page of a comic, or a frame of
 * the title's video. The crop is cut in the browser and sent through the ordinary cover upload, so
 * the server still checks and re-encodes it.
 */
export function CoverCropper({ dramaId, source, onSaved, onClose }: {
  dramaId: number
  source: Source
  onSaved: (width: number, height: number) => void
  onClose: () => void
}) {
  const [pages, setPages] = useState<ComicPageInfo[]>([])
  const [pageIndex, setPageIndex] = useState(0)
  const [still, setStill] = useState<string | null>(null)
  const [nat, setNat] = useState<{ w: number; h: number } | null>(null)
  const [size, setSize] = useState(1)
  const [pos, setPos] = useState({ x: 0.5, y: 0.5 })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [videoFailed, setVideoFailed] = useState(false)
  const imgRef = useRef<HTMLImageElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(null)

  useEffect(() => {
    if (source !== 'comic') return
    comicApi.pages(dramaId).then((r) => setPages(r.pages), setError)
  }, [dramaId, source])

  // The page is read as a blob so the canvas is never tainted, whatever the API's address.
  const pageId = source === 'comic' ? pages[pageIndex]?.id : undefined
  useEffect(() => {
    if (pageId === undefined) return
    let url: string | null = null
    let stale = false
    fetchComicImage(comicImageUrl(dramaId, pageId)).then(
      (blob) => {
        if (stale) return
        url = URL.createObjectURL(blob)
        setStill(url)
      },
      (e: unknown) => !stale && setError(e),
    )
    return () => {
      stale = true
      if (url) URL.revokeObjectURL(url)
    }
  }, [dramaId, pageId])

  // Frames made here are blob URLs; the page's own are revoked by the effect that made them.
  useEffect(() => {
    if (source !== 'video' || !still) return
    return () => URL.revokeObjectURL(still)
  }, [source, still])

  const grabFrame = () => {
    const v = videoRef.current
    if (!v || !v.videoWidth) return
    const canvas = document.createElement('canvas')
    canvas.width = v.videoWidth
    canvas.height = v.videoHeight
    canvas.getContext('2d')?.drawImage(v, 0, 0)
    canvas.toBlob((blob) => blob && setStill(URL.createObjectURL(blob)), 'image/jpeg', 0.95)
    setNat(null)
  }

  const rect = nat ? cropRect(nat.w, nat.h, size, pos.x, pos.y) : null

  const onDown = (e: React.PointerEvent) => {
    e.currentTarget.setPointerCapture(e.pointerId)
    drag.current = { x: e.clientX, y: e.clientY, px: pos.x, py: pos.y }
  }
  const onMove = (e: React.PointerEvent) => {
    const d = drag.current
    const img = imgRef.current
    if (!d || !img || !nat || !rect) return
    const shown = img.getBoundingClientRect()
    const roomX = (nat.w - rect.w) * (shown.width / nat.w)
    const roomY = (nat.h - rect.h) * (shown.height / nat.h)
    setPos({
      x: roomX > 0 ? Math.min(1, Math.max(0, d.px + (e.clientX - d.x) / roomX)) : 0,
      y: roomY > 0 ? Math.min(1, Math.max(0, d.py + (e.clientY - d.y) / roomY)) : 0,
    })
  }

  const save = () => {
    const img = imgRef.current
    if (!img || !rect) return
    const out = outputSize(rect)
    const canvas = document.createElement('canvas')
    canvas.width = out.w
    canvas.height = out.h
    canvas.getContext('2d')?.drawImage(img, rect.x, rect.y, rect.w, rect.h, 0, 0, out.w, out.h)
    setBusy(true)
    setError(null)
    canvas.toBlob(
      (blob) => {
        if (!blob) {
          setBusy(false)
          return
        }
        uploadCover(dramaId, new File([blob], 'cover.jpg', { type: 'image/jpeg' })).then(
          (r) => onSaved(r.width, r.height),
          (e: unknown) => {
            setBusy(false)
            setError(e)
          },
        )
      },
      'image/jpeg',
      0.92,
    )
  }

  return (
    <div className="cover-cropper">
      {source === 'comic' ? (
        <label>
          Page{' '}
          <input
            type="number"
            min={1}
            max={Math.max(1, pages.length)}
            value={pageIndex + 1}
            onChange={(e) => {
              setPageIndex(Math.min(Math.max(0, Number(e.target.value) - 1), Math.max(0, pages.length - 1)))
              setPos({ x: 0.5, y: 0.5 })
              setNat(null)
            }}
          />{' '}
          of {pages.length}
        </label>
      ) : (
        <div>
            {videoFailed ? (
              <p className="muted">This title has no video to take a frame from.</p>
            ) : (
              <>
                <video
                  ref={videoRef}
                  className="cover-video"
                  src={mediaStreamUrl(dramaId, 'video')}
                  controls
                  preload="metadata"
                  onError={() => setVideoFailed(true)}
                />
                <p className="muted">Move to the frame you want, then use it.</p>
                <button type="button" onClick={grabFrame}>Use this frame</button>
              </>
            )}
          </div>
      )}
      {still && (
        <>
          <div className="crop-stage">
            <img
              ref={imgRef}
              src={still}
              alt="Picture to crop"
              draggable={false}
              onLoad={(e) => setNat({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
            />
            {rect && nat && (
              <div
                className="crop-box"
                data-testid="crop-box"
                onPointerDown={onDown}
                onPointerMove={onMove}
                onPointerUp={() => (drag.current = null)}
                onPointerCancel={() => (drag.current = null)}
                style={{
                  left: `${(rect.x / nat.w) * 100}%`,
                  top: `${(rect.y / nat.h) * 100}%`,
                  width: `${(rect.w / nat.w) * 100}%`,
                  height: `${(rect.h / nat.h) * 100}%`,
                }}
              />
            )}
          </div>
          <label>
            Size{' '}
            <input type="range" min={0.2} max={1} step={0.02} value={size} onChange={(e) => setSize(Number(e.target.value))} />
          </label>
          <p className="muted">Drag the box to move it. It keeps the cover shape.</p>
          <div>
            <button type="button" disabled={!rect || busy} onClick={save}>
              {busy ? 'Saving…' : 'Use as cover'}
            </button>
          </div>
        </>
      )}
      <button type="button" className={buttonClass('ghost', 'sm')} onClick={onClose}>Close</button>
      <ErrorBanner error={error} describe={{ pcOnly: true, reasonAsTitle: true }} onDismiss={() => setError(null)} />
    </div>
  )
}
