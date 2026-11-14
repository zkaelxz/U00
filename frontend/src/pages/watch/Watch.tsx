/*
 * The Watch page (#/watch/<id>): a title's own sound or video with its lines following it, the line being spoken
 * shown large, and one key to fix that line in place. It plays through Review's Player; none of Review's tools are here.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent } from 'react'

import { api } from '../../api/client'
import { readerApi } from '../../api/reader'
import { listAllLines, patchLine } from '../../api/review'
import type { DramaDetail } from '../../api/types'
import { Badge } from '../../components/Badge'
import { ButtonLink } from '../../components/Button'
import { PageShell } from '../../components/PageShell'
import { RetryBanner } from '../../components/RetryBanner'
import { buttonClass } from '../../components/uiClasses'
import { useDetailsMenu } from '../../hooks/useDetailsMenu'
import { scrollBehavior, useMediaQuery } from '../../hooks/useMediaQuery'
import { browserStorage, readPref, writePref } from '../../hooks/usePersistedState'
import { useSession } from '../../hooks/useSession'
import { useShortcut } from '../../hooks/useShortcut'
import { formatClock } from '../../labels'
import { Breadcrumbs } from '../../nav/BreadcrumbNav'
import { routeCrumbs } from '../../nav/breadcrumbs'
import { routeHref } from '../../router'
import type { ReaderMediaAvailability } from '../../types/reader'
import type { ReviewLine } from '../../types/review'
import { isComicType } from '../comic/comicLogic'
import { holds } from '../diagnostics/adminUsers'
import { stageLabel } from '../workspace/stages'
import { Player, type PlayerHandle } from '../workspace/stages/review/Player'
import { playerKeyAction } from '../workspace/stages/review/playerLogic'
import '../workspace/stages/review/review.css'
import { titleKind } from '../workspace/titleKind'
import { NowCard } from './NowCard'
import {
  currentLine, isReadOnlyKind, kindBadge, nearestScroll, openFix, positionKey, resumePoint, rowMeta, saveFix,
  SAVE_EVERY_MS, spokenOrder, stepIndex, watchKey, watchVerb, type LineFix,
} from './watchLogic'
import './watch.css'

const titleOf = (d: DramaDetail) => d.title_en || d.title_zh || `Title #${d.id}`

export default function WatchPage({ id }: { id: number }) {
  const phone = useMediaQuery('(max-width: 640px)')
  const session = useSession()
  const canEdit = holds(session, 'lines.edit')
  const [drama, setDrama] = useState<DramaDetail | null>(null)
  const [dramaError, setDramaError] = useState<unknown>(null)
  // undefined while the answer is out (or the question failed); null when the title has nothing to play.
  const [media, setMedia] = useState<ReaderMediaAvailability | null | undefined>(undefined)
  // Kept apart from "nothing to play": a question that failed says nothing about the title's files.
  const [mediaError, setMediaError] = useState<unknown>(null)
  const [loadTick, setLoadTick] = useState(0)
  const [lines, setLines] = useState<ReviewLine[] | null>(null)
  const [linesFailed, setLinesFailed] = useState(false)
  const [linesTick, setLinesTick] = useState(0)
  const [currentId, setCurrentId] = useState<number | null>(null)
  const [fix, setFix] = useState<LineFix | null>(null)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const [captionVersion, setCaptionVersion] = useState(0)
  const [resumedAt, setResumedAt] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    api.getDrama(id).then(
      (d) => {
        if (cancelled) return
        setDrama(d)
        setDramaError(null)
      },
      (e) => !cancelled && setDramaError(e),
    )
    readerApi.media(id).then(
      (m) => {
        if (cancelled) return
        setMedia(m)
        setMediaError(null)
      },
      (e) => !cancelled && setMediaError(e),
    )
    return () => {
      cancelled = true
    }
  }, [id, loadTick])

  useEffect(() => {
    let cancelled = false
    listAllLines(id).then(
      (rows) => {
        if (cancelled) return
        setLines(rows)
        setLinesFailed(false)
      },
      () => !cancelled && setLinesFailed(true),
    )
    return () => {
      cancelled = true
    }
  }, [id, linesTick])

  const ordered = useMemo(() => spokenOrder(lines ?? []), [lines])
  const kind = drama ? titleKind(drama.media_type, drama.content_mode) : null
  const readOnly = kind !== null && isReadOnlyKind(kind)
  const original = media?.original === 'video' || media?.original === 'audio' ? media.original : null
  // The Player is on the page only once the title is known to be one that plays.
  const playable = kind !== null && !readOnly ? original : null
  const player = useRef<PlayerHandle | null>(null)
  const playerBox = useRef<HTMLDivElement | null>(null)
  const mediaEl = useRef<HTMLMediaElement | null>(null)
  // Read by the clock's listeners, which are attached once per media element.
  const orderedRef = useRef(ordered)
  const currentRef = useRef<number | null>(null)
  useEffect(() => {
    orderedRef.current = ordered
  }, [ordered])

  const showCurrent = useCallback((time: number) => {
    const next = currentLine(orderedRef.current, time)?.id ?? null
    if (next === currentRef.current) return
    currentRef.current = next
    setCurrentId(next)
  }, [])

  // The player's clock drives the current line. While the sound plays it is read every animation frame, so the card
  // changes with the voice rather than at the next timeupdate (about four a second); state changes only when the line
  // does, so the page does not redraw every frame. The position is kept per title in this browser.
  useEffect(() => {
    const el = playerBox.current?.querySelector<HTMLMediaElement>('audio, video') ?? null
    mediaEl.current = el
    if (!el) return
    const storage = browserStorage()
    // Nothing is written until the saved place has been read back, or an early leave would overwrite it with 0.
    let settled = false
    let raf = 0
    let saver = 0
    const save = () => {
      if (settled) writePref(storage, positionKey(id), el.currentTime)
    }
    const frame = () => {
      showCurrent(el.currentTime)
      raf = requestAnimationFrame(frame)
    }
    const stopClock = () => {
      cancelAnimationFrame(raf)
      window.clearInterval(saver)
    }
    const onPlay = () => {
      stopClock()
      setResumedAt(null)
      raf = requestAnimationFrame(frame)
      saver = window.setInterval(save, SAVE_EVERY_MS)
    }
    const onPause = () => {
      stopClock()
      showCurrent(el.currentTime)
      save()
    }
    const onSeeked = () => showCurrent(el.currentTime)
    const onMeta = () => {
      if (settled) return
      settled = true
      const at = resumePoint(readPref(storage, positionKey(id), 0), el.duration)
      if (at === null) return
      player.current?.seek(at)
      setResumedAt(at)
    }
    el.addEventListener('play', onPlay)
    el.addEventListener('pause', onPause)
    el.addEventListener('ended', onPause)
    el.addEventListener('seeked', onSeeked)
    el.addEventListener('loadedmetadata', onMeta)
    window.addEventListener('pagehide', save)
    if (el.readyState >= 1) onMeta()
    if (!el.paused) onPlay()
    return () => {
      stopClock()
      save()
      el.removeEventListener('play', onPlay)
      el.removeEventListener('pause', onPause)
      el.removeEventListener('ended', onPause)
      el.removeEventListener('seeked', onSeeked)
      el.removeEventListener('loadedmetadata', onMeta)
      window.removeEventListener('pagehide', save)
    }
  }, [id, playable, showCurrent])

  const currentIndex = currentId === null ? -1 : ordered.findIndex((l) => l.id === currentId)
  const current = currentIndex >= 0 ? ordered[currentIndex] : null
  const fixLine = fix ? ordered.find((l) => l.id === fix.lineId) ?? null : null

  const playFrom = useCallback((start: number) => {
    player.current?.seek(start)
    showCurrent(start)
    if (mediaEl.current?.paused) player.current?.togglePlay()
  }, [showCurrent])

  const goTo = (line: ReviewLine) => {
    player.current?.seek(line.start)
    showCurrent(line.start)
  }

  const fixButton = useRef<HTMLButtonElement | null>(null)
  const startFix = () => {
    if (!canEdit || fix || !original) return
    const el = mediaEl.current
    if (el && !el.paused) player.current?.togglePlay()
    // The line under the playhead at the moment of the press, not the one last drawn.
    const line = el ? currentLine(ordered, el.currentTime) : current
    if (!line) return
    setStatus('')
    setFix(openFix(line))
  }
  const closeFix = () => {
    setFix(null)
    requestAnimationFrame(() => fixButton.current?.focus())
  }
  const save = async () => {
    if (!fix || busy) return
    const line = ordered.find((l) => l.id === fix.lineId)
    setBusy(true)
    const result = await saveFix(fix, {
      patch: (lineId, body) => patchLine(id, lineId, body),
      reload: () => listAllLines(id),
    })
    setBusy(false)
    if (result.kind === 'conflict' || result.kind === 'failed') {
      if (result.kind === 'conflict' && result.lines) setLines(result.lines)
      setFix(result.fix)
      return
    }
    if (result.kind === 'saved') {
      setLines((ls) => ls?.map((l) => (l.id === result.line.id ? result.line : l)) ?? ls)
      setCaptionVersion((v) => v + 1)
      setStatus('Saved.')
    }
    closeFix()
    if (line) playFrom(line.start)
  }

  // Keys anywhere on the page outside a text field. The picture's frame answers Space and the arrows itself while it
  // has focus (that press arrives already handled); elsewhere, and on an audio title, which has no frame, they are
  // passed to the player here.
  useShortcut((combo, { inText, event }) => {
    if (inText || fix || !original || document.querySelector('dialog[open]')) return false
    const key = watchKey(combo, event.target as Element | null)
    if (key === 'fix') {
      startFix()
      return canEdit
    }
    if (key === 'prev' || key === 'next') {
      const to = stepIndex(currentIndex >= 0 ? currentIndex : null, key === 'next' ? 1 : -1, ordered.length)
      if (to === null) return false
      goTo(ordered[to])
      return true
    }
    const el = mediaEl.current
    if (!el) return false
    const action = playerKeyAction({ key: event.key, shiftKey: event.shiftKey, altKey: event.altKey, ctrlKey: event.ctrlKey, metaKey: event.metaKey, target: event.target as Element }, el.currentTime, el.duration)
    if (action?.kind === 'seek') player.current?.seek(action.to)
    else if (action?.kind === 'playpause') player.current?.togglePlay()
    else return false
    return true
  })

  // The spoken row stays in view inside the list's own scroll area; the page itself never moves for it.
  const list = useRef<HTMLOListElement | null>(null)
  useEffect(() => {
    const box = list.current
    const row = box?.querySelector<HTMLElement>('[aria-current="true"]')
    if (!box || !row) return
    const rect = box.getBoundingClientRect()
    const delta = nearestScroll(row.getBoundingClientRect(), rect)
    // A jump (a seek, the saved place) lands at once; gliding there would leave the row out of view for a while.
    if (delta) box.scrollBy({ top: delta, behavior: Math.abs(delta) > rect.height ? 'auto' : scrollBehavior() })
  }, [currentId])

  const startOver = () => {
    player.current?.seek(0)
    showCurrent(0)
    writePref(browserStorage(), positionKey(id), 0)
    setResumedAt(null)
  }

  const title = drama ? titleOf(drama) : dramaError ? 'This title' : 'Loading…'
  const verb = kind ? watchVerb(kind, original) : 'Watch'
  const readerHref = routeHref({ name: drama && isComicType(drama.media_type) ? 'comic' : 'read', id, page: null })
  const sourceStage = kind ? stageLabel('source', kind) : undefined

  return (
    <PageShell
      width="wide"
      className="watch-page"
      title={title}
      back={phone ? (
        // One row for the way back and the menu, so the header takes no extra row and the spoken line is on the first screen.
        <div className="watch-back-row">
          <a className="watch-back" href={routeHref({ name: 'library' })}>‹ Library</a>
          {drama && !readOnly && <WatchMore id={id} withWork />}
        </div>
      ) : <Breadcrumbs crumbs={routeCrumbs({ name: 'watch', id }, { title: drama ? titleOf(drama) : null, verb })} />}
      meta={drama && <p className="page-meta"><Badge>{kindBadge(drama.media_type, drama.source_language)}</Badge></p>}
      actions={!phone && drama && !readOnly && (
        <div className="watch-actions">
          <ButtonLink href={routeHref({ name: 'drama', id, stage: null })}>Work on this title</ButtonLink>
          <WatchMore id={id} />
        </div>
      )}
    >
      <RetryBanner
        error={dramaError ?? mediaError}
        what={dramaError ? 'this title' : "this title's sound or video"}
        onRetry={() => {
          setDramaError(null)
          setMediaError(null)
          setLoadTick((n) => n + 1)
        }}
      />
      {readOnly ? (
        <section className="card" aria-label="Not a watch title">
          <p>This title is read, not watched.</p>
          <div className="watch-card-actions"><ButtonLink variant="primary" href={readerHref}>Open the Reader</ButtonLink></div>
        </section>
      ) : drama && (
        <div className="watch-layout">
          <div className="watch-main">
            {media === undefined || mediaError ? null : original ? (
              <div className="card watch-player" ref={playerBox}>
                <Player
                  ref={player}
                  dramaId={id}
                  kind={original}
                  lines={ordered}
                  captionVersion={captionVersion}
                  englishAbsent={ordered.length > 0 && ordered.every((l) => !l.en.trim())}
                  sourceStage={sourceStage}
                  alwaysOpen
                  lineShownByPage
                />
                {resumedAt !== null && (
                  <div className="watch-resume">
                    <p>Back where you stopped, at {formatClock(resumedAt)}.</p>
                    <button type="button" className={buttonClass()} onClick={startOver}>Start from the beginning</button>
                  </div>
                )}
              </div>
            ) : (
              <section className="card" aria-label="Nothing to play">
                <p>This title has no sound or video on this PC, so there is nothing to play here.</p>
                <div className="watch-card-actions">
                  <ButtonLink href={routeHref({ name: 'follow', id })}>Follow along</ButtonLink>
                  <ButtonLink href={routeHref({ name: 'read', id, page: null })}>Read the lines</ButtonLink>
                </div>
              </section>
            )}
            {original && ordered.length > 0 && (
              <NowCard
                line={fixLine ?? current}
                sourceLanguage={drama.source_language || 'und'}
                canEdit={canEdit}
                fix={fix}
                busy={busy}
                status={status}
                onOpen={startFix}
                onDraft={(draft) => setFix((f) => (f ? { ...f, draft } : f))}
                onSave={() => void save()}
                onCancel={closeFix}
                onAgain={() => {
                  const line = fixLine ?? current
                  if (line) player.current?.playLine(line)
                }}
                fixButtonRef={fixButton}
              />
            )}
          </div>
          <div className="watch-side">
            <section className="card watch-lines" aria-labelledby="watch-lines-title">
              <header className="watch-lines-head">
                <h2 className="card-title" id="watch-lines-title">Lines</h2>
                <span className="muted">follows the sound</span>
              </header>
              {linesFailed ? (
                <div className="watch-lines-note">
                  <p>The lines could not be loaded.</p>
                  <div className="watch-card-actions">
                    <button type="button" className={buttonClass()} onClick={() => setLinesTick((n) => n + 1)}>Try again</button>
                  </div>
                </div>
              ) : lines === null ? (
                <p className="muted">Loading…</p>
              ) : ordered.length === 0 ? (
                <div className="watch-lines-note">
                  <p>This title has no lines yet.</p>
                  <div className="watch-card-actions">
                    <ButtonLink href={routeHref({ name: 'drama', id, stage: null })}>Open the title</ButtonLink>
                  </div>
                </div>
              ) : (
                <ol className="watch-list" ref={list} aria-label="Lines">
                  {ordered.map((l) => {
                    const on = l.id === currentId
                    const text = (
                      <>
                        <span className="watch-row-meta">{rowMeta(l)}</span>
                        {l.zh && <span className="watch-row-zh" lang={l.lang || drama.source_language || 'und'}>{l.zh}</span>}
                        <span className={l.en ? 'watch-row-en' : 'watch-row-en is-empty'} lang={l.en ? 'en' : undefined}>{l.en || 'No English yet'}</span>
                      </>
                    )
                    return (
                      <li key={l.id} className={on ? 'is-current' : undefined} aria-current={on ? 'true' : undefined}>
                        {original ? (
                          <button type="button" className="watch-row" onClick={() => playFrom(l.start)}>{text}</button>
                        ) : (
                          <div className="watch-row">{text}</div>
                        )}
                      </li>
                    )
                  })}
                </ol>
              )}
            </section>
          </div>
        </div>
      )}
    </PageShell>
  )
}

function WatchMore({ id, withWork = false }: { id: number; withWork?: boolean }) {
  const more = useDetailsMenu()
  const close = (e: MouseEvent<HTMLAnchorElement>) => {
    const menu = e.currentTarget.closest('details')
    if (menu) menu.open = false
  }
  return (
    <details className="watch-more" ref={more}>
      <summary className={buttonClass()}>More</summary>
      <div className="watch-more-panel">
        {withWork && <a href={routeHref({ name: 'drama', id, stage: null })} onClick={close}>Work on this title</a>}
        <a href={routeHref({ name: 'follow', id })} onClick={close}>Follow along with sound from another app</a>
        <a href={routeHref({ name: 'read', id, page: null })} onClick={close}>Read the lines</a>
        <a href={routeHref({ name: 'drama', id, stage: 'export' })} onClick={close}>Save subtitles</a>
      </div>
    </details>
  )
}
