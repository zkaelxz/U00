/*
 * "Read from a screenshot" for the Translate page: paste, drop or choose a
 * picture, optionally drag a box round the text and enlarge it, and a vision
 * engine returns the characters, which are added to the text box. The crop and
 * zoom are done here in a canvas, so what the engine sees is what the preview
 * shows. Pure arithmetic lives in readImage.ts.
 */
import { useEffect, useId, useRef, useState } from 'react'

import { ErrorBanner } from '../components/ErrorBanner'
import { buttonClass } from '../components/uiClasses'
import { engineOptionLabel, modelOptionLabel, translateApi } from '../api/translate'
import { usePersistedState } from '../hooks/usePersistedState'
import {
  ZOOM_CHOICES,
  cropPlan,
  firstImage,
  isAcceptedImage,
  pickVisionEngine,
  rectBetween,
  visionEngines,
  type CropPlan,
  type Rect,
} from './readImage'
import type { ReadImageEstimate, TranslateEngine } from '../types/translate'

type Point = { x: number; y: number }

async function croppedPng(img: HTMLImageElement, plan: CropPlan): Promise<Blob> {
  const canvas = document.createElement('canvas')
  canvas.width = plan.outW
  canvas.height = plan.outH
  const ctx = canvas.getContext('2d')
  if (!ctx) throw new Error('This browser cannot prepare the picture.')
  ctx.imageSmoothingQuality = 'high'
  ctx.drawImage(img, plan.sx, plan.sy, plan.sw, plan.sh, 0, 0, plan.outW, plan.outH)
  return new Promise((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('This browser cannot prepare the picture.'))), 'image/png'),
  )
}

export function TranslateReadImage({
  engines,
  pageEngine,
  language,
  allowed,
  onText,
  onClose,
}: {
  engines: TranslateEngine[]
  pageEngine: string
  /** Source language code (zh, ja, ko) the engine is told to expect; '' for none. */
  language: string
  /** Holds engines.paid, like Translate itself. */
  allowed: boolean
  onText: (read: string) => void
  onClose: () => void
}) {
  const id = useId()
  const [enginePref, setEnginePref] = usePersistedState('translate.vision.engine', '')
  const [modelPref, setModelPref] = usePersistedState('translate.vision.model', '')
  const [source, setSource] = useState<{ url: string; width: number; height: number } | null>(null)
  const [rect, setRect] = useState<Rect | null>(null)
  const [zoom, setZoom] = useState(2)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [estimate, setEstimate] = useState<ReadImageEstimate | null>(null)
  const imgRef = useRef<HTMLImageElement>(null)
  const dragFrom = useRef<Point | null>(null)

  const offered = visionEngines(engines)
  const engine = pickVisionEngine(engines, enginePref, pageEngine)
  const selected = offered.find((e) => e.name === engine)
  const model = modelPref && selected?.models?.includes(modelPref) ? modelPref : ''
  const noKey = selected !== undefined && !selected.key_configured
  const plan = source ? cropPlan(source.width, source.height, rect, zoom) : null

  // The object URL is the only copy of the picture kept; nothing is stored.
  useEffect(() => () => { if (source) URL.revokeObjectURL(source.url) }, [source])

  const load = (file: File) => {
    setError(null)
    setNotice(null)
    if (!isAcceptedImage(file)) {
      setNotice('That file is not a PNG, JPEG or WebP picture.')
      return
    }
    const url = URL.createObjectURL(file)
    const probe = new Image()
    probe.onload = () => {
      setRect(null)
      setSource({ url, width: probe.naturalWidth, height: probe.naturalHeight })
    }
    probe.onerror = () => {
      URL.revokeObjectURL(url)
      setNotice('That picture could not be opened.')
    }
    probe.src = url
  }

  // Ctrl+V anywhere on the page while the panel is shown, so a screenshot can be pasted without focusing anything.
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const file = e.clipboardData ? firstImage(Array.from(e.clipboardData.items)) : null
      if (!file) return
      e.preventDefault()
      load(file)
    }
    document.addEventListener('paste', onPaste)
    return () => document.removeEventListener('paste', onPaste)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load only touches state setters
  }, [])

  const paidEngine = selected !== undefined && !selected.free
  useEffect(() => {
    setEstimate(null)
    if (!allowed || !paidEngine || !engine || !plan) return
    let current = true
    const timer = setTimeout(() => {
      translateApi
        .readImageEstimate({ engine, model: model || null, width: plan.outW, height: plan.outH })
        .then((r) => current && setEstimate(r), () => undefined)
    }, 400)
    return () => {
      current = false
      clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- plan is rebuilt each render; its size is listed
  }, [allowed, paidEngine, engine, model, plan?.outW, plan?.outH])

  const pointAt = (e: React.PointerEvent<HTMLElement>): Point => {
    const box = e.currentTarget.getBoundingClientRect()
    return { x: (e.clientX - box.left) / box.width, y: (e.clientY - box.top) / box.height }
  }

  const read = async () => {
    const img = imgRef.current
    if (!img || !plan || !engine) return
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const blob = await croppedPng(img, plan)
      const done = await translateApi.readImage(blob, { engine, model: model || null, language })
      if (done.text) onText(done.text)
      setNotice(done.text ? 'Added to the text box.' : (done.message ?? 'No text was found in the picture.'))
    } catch (err) {
      setError(err)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="translate-read translate-read-open" id={id} role="group" aria-label="Read from a screenshot">
      <div className="translate-read-head">
        <strong>Read from a screenshot</strong>
        <button type="button" className={buttonClass('ghost', 'sm')} onClick={onClose}>
          Close
        </button>
      </div>
      <p className="muted translate-read-hint">
        Paste a picture (Ctrl+V), drop one here, or choose a file. An AI model reads the characters in it and adds
        them to the text box. The picture is not kept.
      </p>
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      <div
        className="translate-read-drop"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault()
          const file = firstImage(Array.from(e.dataTransfer.items)) ?? Array.from(e.dataTransfer.files).find(isAcceptedImage) ?? null
          if (file) load(file)
          else setNotice('Drop a PNG, JPEG or WebP picture.')
        }}
      >
        {source ? (
          <div
            className="translate-read-stage"
            onPointerDown={(e) => {
              e.currentTarget.setPointerCapture(e.pointerId)
              dragFrom.current = pointAt(e)
              setRect(null)
            }}
            onPointerMove={(e) => {
              if (dragFrom.current) setRect(rectBetween(dragFrom.current, pointAt(e)))
            }}
            onPointerUp={() => { dragFrom.current = null }}
            onPointerCancel={() => { dragFrom.current = null }}
          >
            <img ref={imgRef} src={source.url} alt="The picture to read" draggable={false} />
            {rect && (
              <span
                className="translate-read-box"
                style={{ left: `${rect.x * 100}%`, top: `${rect.y * 100}%`, width: `${rect.w * 100}%`, height: `${rect.h * 100}%` }}
              />
            )}
          </div>
        ) : (
          <p className="muted">No picture yet.</p>
        )}
        <div className="translate-file">
          <input
            id={`${id}-file`}
            type="file"
            className="visually-hidden"
            accept="image/png,image/jpeg,image/webp"
            onChange={(e) => {
              const input = e.currentTarget
              const file = input.files?.[0]
              if (file) load(file)
              input.value = ''
            }}
          />
          <label htmlFor={`${id}-file`} className={buttonClass('secondary', 'sm')}>
            Choose a picture…
          </label>
        </div>
      </div>
      {source && (
        <p className="muted translate-read-hint">
          Drag on the picture to pick just the text, which reads far better than a whole frame. Without a box the
          whole picture is sent.{' '}
          {rect && (
            <button type="button" className={buttonClass('ghost', 'sm')} onClick={() => setRect(null)}>
              Clear the box
            </button>
          )}
        </p>
      )}
      <div className="translate-read-controls">
        <label className="field">
          <span>Read with</span>
          <select aria-label="Read with" value={engine} onChange={(e) => { setEnginePref(e.target.value); setModelPref('') }}>
            {offered.map((en) => (
              <option key={en.name} value={en.name}>
                {engineOptionLabel(en)}
              </option>
            ))}
          </select>
        </label>
        {selected?.models && (
          <label className="field">
            <span>Model</span>
            <select aria-label="Read with model" value={model} onChange={(e) => setModelPref(e.target.value)}>
              <option value="">Default</option>
              {selected.models.map((m) => (
                <option key={m} value={m}>
                  {modelOptionLabel(selected, m)}
                </option>
              ))}
            </select>
          </label>
        )}
        <label className="field">
          <span>Enlarge the text</span>
          <select aria-label="Enlarge the text" value={zoom} onChange={(e) => setZoom(Number(e.target.value))}>
            {ZOOM_CHOICES.map((z) => (
              <option key={z} value={z}>
                {z === 1 ? 'No' : `${z}×`}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="actions">
        <button type="button" className={buttonClass('primary')} disabled={!source || busy || noKey || !allowed || !engine} onClick={() => void read()}>
          {busy ? 'Reading…' : 'Read the text'}
        </button>
        {plan && <span className="muted">Sends {plan.outW}×{plan.outH} px</span>}
      </div>
      {estimate && estimate.estimated_usd > 0 && (
        <p data-testid="read-estimate" className={estimate.fits_cap ? 'muted' : 'translate-warning'} role="status">
          About ${estimate.estimated_usd.toFixed(4).replace(/0+$/, '').replace(/\.$/, '')}
          {estimate.cap_left_usd !== null
            ? `; cap left $${estimate.cap_left_usd.toFixed(2)}${estimate.fits_cap ? '' : ': it will not be sent'}`
            : ''}
          .
        </p>
      )}
      {!allowed && <p className="translate-warning">Reading pictures isn't allowed for this account. Ask the PC owner.</p>}
      {allowed && noKey && selected && (
        <p className="translate-warning">That engine is not set up yet. Add its key in Settings, or pick another.</p>
      )}
      {selected?.name === 'ollama' && (
        <p className="muted translate-read-hint">A local model must support pictures (Gemma 4 does). It is free but slower.</p>
      )}
      {notice && <p className="muted" role="status">{notice}</p>}
    </div>
  )
}
