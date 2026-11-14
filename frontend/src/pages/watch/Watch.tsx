/*
 * The Watch page (#/watch/<id>): a title's own sound or video with its lines following it, the line being spoken
 * shown large, and one key to fix that line in place. It plays through Review's Player, so the player's own Loop line,
 * Speed, Jump and Pop out are here; Review's line tools (flags, timing, checking, its line editor) are not.
 */
import {
  memo, useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent, type PointerEvent as ReactPointerEvent, type RefObject, type SetStateAction,
} from 'react'

import { api } from '../../api/client'
import { readerApi } from '../../api/reader'
import { listAllLines, patchLine } from '../../api/review'
import { browserSession } from '../../api/sessionStore'
import type { DramaDetail } from '../../api/types'
import { Badge } from '../../components/Badge'
import { ButtonLink } from '../../components/Button'
import { readHref } from '../../components/libraryView'
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
import { holds } from '../diagnostics/adminUsers'
import { lineEditPatch } from '../reader/readerLineEdit'
import { stageLabel } from '../workspace/stages'
import { Player, type PlayerHandle } from '../workspace/stages/review/Player'
import { playerKeyAction } from '../workspace/stages/review/playerLogic'
import '../workspace/stages/review/review.css'
import { titleKind } from '../workspace/titleKind'
import { NowCard } from './NowCard'
import {
  currentLine, fixIsDirty, focusAfterFix, followPaused, followScroll, isReadOnlyKind, lineToFix, openFix, parkFix,
  positionKey, readParkedFix, resumePoint, rowMeta, saveFix, SAVE_EVERY_MS, spokenOrder, stepIndex,
  titleKindAndLanguages, unparkedFix, unparkFix, watchKey, watchVerb, type LineFix,
} from './watchLogic'
import './watch.css'

const titleOf = (d: DramaDetail) => d.title_en || d.title_zh || `Title #${d.id}`

const RESTORED = 'Your fix from before was not saved. It is back in the box.'
const RESTORE_GONE = 'An unsaved edit could not be brought back: its line no longer exists.'

// The player stays as it is while the fix box is typed in; every prop it gets here is stable.
const StillPlayer = memo(Player)

// A save sent on leaving the page, per title, while it runs. A page opened again waits for it, so it neither lists
// the lines from before that save nor offers back a fix that is about to be saved.
const leavingSaves = new Map<number, Promise<void>>()

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
  const [captionVersion, setCaptionVersion] = useState(0)

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
    void Promise.resolve(leavingSaves.get(id)).then(() => listAllLines(id)).then(
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
  const lineIndex = useMemo(() => new Map(ordered.map((l, i) => [l.id, i])), [ordered])
  const englishAbsent = useMemo(() => ordered.length > 0 && ordered.every((l) => !l.en.trim()), [ordered])
  const kind = drama ? titleKind(drama.media_type, drama.content_mode) : null
  const readOnly = kind !== null && isReadOnlyKind(kind)
  const original = media?.original === 'video' || media?.original === 'audio' ? media.original : null
  // The Player is on the page only once the title is known to be one that plays.
  const playable = kind !== null && !readOnly ? original : null
  const player = useRef<PlayerHandle | null>(null)
  const list = useRef<HTMLOListElement | null>(null)
  const { playerBoxRef, mediaRef, currentId, showCurrent, resumedAt, clearResumed } = useWatchClock(id, playable, ordered, player)
  const { resume, scrollHandlers } = useFollow(list, currentId)

  const currentIndex = currentId === null ? -1 : lineIndex.get(currentId) ?? -1
  const current = currentIndex >= 0 ? ordered[currentIndex] : null

  const playFrom = useCallback((start: number) => {
    resume()
    player.current?.seek(start)
    showCurrent(start)
    if (mediaRef.current?.paused) player.current?.togglePlay()
  }, [mediaRef, resume, showCurrent])

  const goTo = (line: ReviewLine) => {
    resume()
    player.current?.seek(line.start)
    showCurrent(line.start)
  }

  const { fix, busy, status, startFix, setDraft, cancel, save, fixButtonRef, fieldRef, cardRef } = useLineFix({
    id, ordered, lineIndex, canEdit, ready: lines !== null && playable !== null, setLines, player, mediaRef, current, playFrom,
    onSaved: () => setCaptionVersion((v) => v + 1),
  })
  const fixIndex = fix ? lineIndex.get(fix.lineId) : undefined
  const fixLine = fixIndex === undefined ? null : ordered[fixIndex]

  // Keys anywhere on the page. The picture's frame answers Space and the arrows itself while it has focus (that press
  // arrives already handled); elsewhere, and on an audio title, which has no frame, they are passed to the player here.
  useShortcut((combo, { inText, event }) => {
    if (!original || document.querySelector('dialog[open]')) return false
    const key = watchKey(combo, event.target as Element | null)
    if (key === 'play') {
      player.current?.togglePlay()
      return true
    }
    if (inText) return false
    if (key === 'loop') {
      player.current?.toggleLoop()
      return true
    }
    if (fix) return false
    if (key === 'fix') {
      startFix()
      return canEdit
    }
    if (key === 'prev' || key === 'next') {
      const to = stepIndex(ordered, currentIndex >= 0 ? currentIndex : null, key === 'next' ? 1 : -1)
      if (to === null) return false
      goTo(ordered[to])
      return true
    }
    const el = mediaRef.current
    if (!el) return false
    const action = playerKeyAction({ key: event.key, shiftKey: event.shiftKey, altKey: event.altKey, ctrlKey: event.ctrlKey, metaKey: event.metaKey, target: event.target as Element }, el.currentTime, el.duration)
    if (action?.kind === 'seek') {
      resume()
      player.current?.seek(action.to)
    } else if (action?.kind === 'playpause') player.current?.togglePlay()
    else return false
    return true
  })

  const startOver = () => {
    resume()
    player.current?.seek(0)
    showCurrent(0)
    writePref(browserStorage(), positionKey(id), 0)
    clearResumed()
  }

  // The person seeking with the player's own controls brings the list back to the sound. Loop line's repeat is a seek
  // too, but made by the player, so it is not counted.
  const seekedByHand = {
    onInputCapture: (e: { target: EventTarget }) => {
      if ((e.target as Element).matches?.('.review-seek')) resume()
    },
    onSubmitCapture: resume,
    onKeyDownCapture: (e: ReactKeyboardEvent) => {
      const el = mediaRef.current
      const action = el && playerKeyAction({ key: e.key, shiftKey: e.shiftKey, altKey: e.altKey, ctrlKey: e.ctrlKey, metaKey: e.metaKey, target: e.target as Element }, el.currentTime, el.duration)
      if (action?.kind === 'seek') resume()
    },
  }

  const title = drama ? titleOf(drama) : dramaError ? 'This title' : 'Loading…'
  const verb = kind ? watchVerb(kind, original) : 'Watch'
  const sourceStage = kind ? stageLabel('source', kind) : undefined
  const badge = drama ? titleKindAndLanguages(drama.media_type, drama.source_language) : ''
  const sourceLanguage = drama?.source_language || 'und'

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
      meta={badge ? <p className="page-meta"><Badge>{badge}</Badge></p> : null}
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
      {readOnly && drama ? (
        <section className="card" aria-label="Not a watch title">
          <p>This title is read, not watched.</p>
          <div className="watch-card-actions"><ButtonLink variant="primary" href={readHref(drama)}>Open the Reader</ButtonLink></div>
        </section>
      ) : drama && (
        <div className="watch-layout">
          <div className="watch-main">
            {media === undefined || mediaError ? null : original ? (
              <div className="card watch-player" ref={playerBoxRef} {...seekedByHand}>
                <StillPlayer
                  ref={player}
                  dramaId={id}
                  kind={original}
                  lines={ordered}
                  captionVersion={captionVersion}
                  englishAbsent={englishAbsent}
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
                sourceLanguage={sourceLanguage}
                canEdit={canEdit}
                fix={fix}
                busy={busy}
                status={status}
                onOpen={startFix}
                onDraft={setDraft}
                onSave={() => void save()}
                onCancel={cancel}
                onAgain={() => {
                  const line = fixLine ?? current
                  if (line) player.current?.playLine(line)
                }}
                fixButtonRef={fixButtonRef}
                fieldRef={fieldRef}
                cardRef={cardRef}
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
                <WatchList
                  lines={ordered}
                  currentId={currentId}
                  sourceLanguage={sourceLanguage}
                  canPlay={original !== null}
                  onPick={playFrom}
                  scroll={scrollHandlers}
                  listRef={list}
                />
              )}
            </section>
          </div>
        </div>
      )}
    </PageShell>
  )
}

/**
 * The player's clock drives the current line. While the sound plays it is read every animation frame, so the card
 * changes with the voice rather than at the next timeupdate (about four a second); state changes only when the line
 * does, so the page does not redraw every frame. The position is kept per title in this browser.
 */
function useWatchClock(id: number, playable: 'video' | 'audio' | null, ordered: ReviewLine[], player: RefObject<PlayerHandle | null>) {
  const playerBoxRef = useRef<HTMLDivElement | null>(null)
  const mediaRef = useRef<HTMLMediaElement | null>(null)
  const [currentId, setCurrentId] = useState<number | null>(null)
  const [resumedAt, setResumedAt] = useState<number | null>(null)
  // Read by the clock's listeners, which are attached once per media element.
  const orderedRef = useRef(ordered)
  const currentRef = useRef<number | null>(null)
  // Until the clock is first read (a play, a pause, a seek), the card asks for play rather than naming line 1.
  const clockRead = useRef(false)

  const showCurrent = useCallback((time: number) => {
    clockRead.current = true
    const next = currentLine(orderedRef.current, time)?.id ?? null
    if (next === currentRef.current) return
    currentRef.current = next
    setCurrentId(next)
  }, [])

  useEffect(() => {
    orderedRef.current = ordered
    // The lines are read 200 at a time, one request after another, so on a long title the saved place can be sought
    // before they are in; the line under the playhead is worked out again whenever they change.
    const el = mediaRef.current
    if (el && clockRead.current) showCurrent(el.currentTime)
  }, [ordered, showCurrent])

  useEffect(() => {
    const el = playerBoxRef.current?.querySelector<HTMLMediaElement>('audio, video') ?? null
    mediaRef.current = el
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
  }, [id, playable, player, showCurrent])

  const clearResumed = useCallback(() => setResumedAt(null), [])
  return { playerBoxRef, mediaRef, currentId, showCurrent, resumedAt, clearResumed }
}

/**
 * The list follows the sound inside its own scroll area (the page itself never moves for it), with the spoken row's
 * top a little way down so the coming lines show. It waits while the person scrolls the list themselves, told apart
 * from the page's own scrolling by their input, and comes back at once when they press a row, step or seek.
 */
function useFollow(list: RefObject<HTMLOListElement | null>, currentId: number | null) {
  const scrolledAt = useRef<number | null>(null)
  const draggingBar = useRef(false)
  const [tick, setTick] = useState(0)

  const scrolledByHand = useCallback(() => {
    scrolledAt.current = Date.now()
  }, [])
  const resume = useCallback(() => {
    scrolledAt.current = null
    setTick((t) => t + 1)
  }, [])

  const scrollHandlers = useMemo(() => ({
    onWheel: scrolledByHand,
    onTouchMove: scrolledByHand,
    // A press on the list itself, beside its rows' width, is on its scroll bar; the drag is counted until it ends.
    onPointerDown: (e: ReactPointerEvent<HTMLOListElement>) => {
      if (e.target !== e.currentTarget || e.nativeEvent.offsetX < e.currentTarget.clientWidth) return
      scrolledByHand()
      draggingBar.current = true
      window.addEventListener('pointerup', () => { draggingBar.current = false }, { once: true })
    },
    onScroll: () => {
      if (draggingBar.current) scrolledByHand()
    },
  }), [scrolledByHand])

  useEffect(() => {
    // These keys scroll the list when focus is in it, or nowhere after a press on it.
    const onKey = (e: KeyboardEvent) => {
      if (!['PageUp', 'PageDown', 'Home', 'End'].includes(e.key)) return
      const t = e.target as Node | null
      if (t === document.body || (t && list.current?.contains(t))) scrolledByHand()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [list, scrolledByHand])

  useEffect(() => {
    const box = list.current
    const row = box?.querySelector<HTMLElement>('li.is-current')
    if (!box || !row || followPaused(scrolledAt.current, Date.now())) return
    const offset = row.getBoundingClientRect().top - box.getBoundingClientRect().top - box.clientTop
    const delta = followScroll(offset, { height: box.clientHeight, scrollTop: box.scrollTop, maxScroll: box.scrollHeight - box.clientHeight })
    // A jump (a seek, the saved place) lands at once; gliding there would leave the row out of view for a while.
    if (delta) box.scrollBy({ top: delta, behavior: Math.abs(delta) > box.clientHeight ? 'auto' : scrollBehavior() })
  }, [list, currentId, tick])

  return { resume, scrollHandlers }
}

/**
 * The open fix, its save, and its draft kept from being lost: leaving the page while it is dirty asks first; leaving
 * inside the app parks it in this tab and saves it, and if that save fails, the next visit opens it again.
 */
function useLineFix({ id, ordered, lineIndex, canEdit, ready, setLines, player, mediaRef, current, playFrom, onSaved }: {
  id: number
  ordered: ReviewLine[]
  lineIndex: Map<number, number>
  canEdit: boolean
  /** The lines are in and the title plays, so a fix can be shown. */
  ready: boolean
  setLines: Dispatch<SetStateAction<ReviewLine[] | null>>
  player: RefObject<PlayerHandle | null>
  mediaRef: RefObject<HTMLMediaElement | null>
  current: ReviewLine | null
  playFrom: (start: number) => void
  onSaved: () => void
}) {
  const [fix, setFix] = useState<LineFix | null>(null)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const fixButtonRef = useRef<HTMLButtonElement | null>(null)
  const fieldRef = useRef<HTMLTextAreaElement | null>(null)
  const cardRef = useRef<HTMLElement | null>(null)
  // Read when the page goes away, after the last render.
  const fixRef = useRef(fix)
  const saving = useRef<Promise<boolean> | null>(null)
  useEffect(() => {
    fixRef.current = fix
  }, [fix])

  const focus = (where: ReturnType<typeof focusAfterFix>) => requestAnimationFrame(() => {
    const target = where === 'card' ? cardRef : where === 'field' ? fieldRef : fixButtonRef
    target.current?.focus()
  })

  const dirty = fixIsDirty(fix)
  useEffect(() => {
    if (!dirty) return
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  useEffect(() => () => {
    const left = fixRef.current
    const body = left && lineEditPatch(left.seen, left.draft)
    if (!left || !body) return
    const store = browserSession()
    parkFix(id, left, store)
    // A save already running is the one to wait for; sending the draft again would be refused as a change.
    const sent = saving.current ?? patchLine(id, left.lineId, body).then(() => true)
    const run = sent.then((ok) => {
      if (ok) unparkFix(id, store)
    }, () => {
      // Kept parked for the next visit.
    })
    leavingSaves.set(id, run)
    void run.finally(() => {
      if (leavingSaves.get(id) === run) leavingSaves.delete(id)
    })
  }, [id])

  const offered = useRef(false)
  useEffect(() => {
    if (offered.current || !ready || !canEdit) return
    offered.current = true
    const offer = () => {
      const store = browserSession()
      const parked = readParkedFix(id, store)
      if (!parked) return
      // The open box holds it from here.
      unparkFix(id, store)
      if (!lineIndex.has(parked.lineId)) {
        setStatus(RESTORE_GONE)
        return
      }
      setFix(unparkedFix(parked))
      setStatus(RESTORED)
    }
    const running = leavingSaves.get(id)
    if (running) void running.then(offer)
    else offer()
  }, [id, ready, canEdit, lineIndex])

  const startFix = () => {
    if (!canEdit || fix || !ready) return
    const el = mediaRef.current
    if (el && !el.paused) player.current?.togglePlay()
    const line = lineToFix(ordered, el ? el.currentTime : null, current)
    if (!line) return
    setStatus('')
    setFix(openFix(line))
  }

  const setDraft = useCallback((draft: string) => setFix((f) => (f ? { ...f, draft } : f)), [])

  const cancel = () => {
    setFix(null)
    focus(focusAfterFix('cancel'))
  }

  const save = async () => {
    if (!fix || busy) return
    const index = lineIndex.get(fix.lineId)
    const line = index === undefined ? null : ordered[index]
    setBusy(true)
    const run = saveFix(fix, {
      patch: (lineId, body) => patchLine(id, lineId, body),
      reload: () => listAllLines(id),
    })
    saving.current = run.then((r) => r.kind === 'saved' || r.kind === 'unchanged')
    const result = await run
    saving.current = null
    setBusy(false)
    if (result.kind === 'conflict' || result.kind === 'failed') {
      if (result.kind === 'conflict' && result.lines) setLines(result.lines)
      setFix(result.fix)
      focus(focusAfterFix(result.kind))
      return
    }
    if (result.kind === 'saved') {
      setLines((ls) => ls?.map((l) => (l.id === result.line.id ? result.line : l)) ?? ls)
      onSaved()
      setStatus('Saved.')
    }
    setFix(null)
    focus(focusAfterFix(result.kind))
    if (line) playFrom(line.start)
  }

  return { fix, busy, status, startFix, setDraft, cancel, save, fixButtonRef, fieldRef, cardRef }
}

const WatchList = memo(function WatchList({ lines, currentId, sourceLanguage, canPlay, onPick, scroll, listRef }: {
  lines: ReviewLine[]
  currentId: number | null
  sourceLanguage: string
  canPlay: boolean
  onPick: (start: number) => void
  scroll: ReturnType<typeof useFollow>['scrollHandlers']
  listRef: RefObject<HTMLOListElement | null>
}) {
  return (
    <ol className="watch-list" ref={listRef} {...scroll}>
      {lines.map((l) => (
        <WatchRow key={l.id} line={l} current={l.id === currentId} sourceLanguage={sourceLanguage} canPlay={canPlay} onPick={onPick} />
      ))}
    </ol>
  )
})

const WatchRow = memo(function WatchRow({ line: l, current, sourceLanguage, canPlay, onPick }: {
  line: ReviewLine
  current: boolean
  sourceLanguage: string
  canPlay: boolean
  onPick: (start: number) => void
}) {
  const text = (
    <>
      <span className="watch-row-meta">{rowMeta(l)}</span>
      {l.zh && <span className="watch-row-zh" lang={l.lang || sourceLanguage}>{l.zh}</span>}
      <span className={l.en ? 'watch-row-en' : 'watch-row-en is-empty'} lang={l.en ? 'en' : undefined}>{l.en || 'No English yet'}</span>
    </>
  )
  const mark = current ? 'true' : undefined
  return (
    <li className={current ? 'is-current' : undefined} aria-current={canPlay ? undefined : mark}>
      {canPlay ? (
        <button
          type="button"
          className="watch-row"
          aria-current={mark}
          onClick={(e) => {
            onPick(l.start)
            // A pointer press leaves focus on the row, where the next Space would press it again and start the line
            // over. A press from the keyboard (detail 0) keeps focus there, as a button does.
            if (e.detail !== 0) e.currentTarget.blur()
          }}
        >
          {text}
        </button>
      ) : (
        <div className="watch-row">{text}</div>
      )}
    </li>
  )
})

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
