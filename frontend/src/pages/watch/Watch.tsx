/*
 * The Watch page (#/watch/<id>): a title's own sound or video with its lines following it, the line being spoken
 * shown large, and one key to fix that line in place. It plays through Review's Player, so the player's own Loop line,
 * Speed, Jump and Pop out are here; Review's line tools (flags, timing, checking, its line editor) are not.
 */
import {
  memo, useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent, type RefObject, type SetStateAction, type UIEvent,
} from 'react'

import { api } from '../../api/client'
import { coverUrl } from '../../api/metadata'
import { readerApi } from '../../api/reader'
import { listAllLines, listRiskLineIds, patchLine } from '../../api/review'
import { browserSession } from '../../api/sessionStore'
import type { DramaDetail } from '../../api/types'
import { Badge } from '../../components/Badge'
import { ButtonLink } from '../../components/Button'
import { parseTime, readHref } from '../../components/libraryView'
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
import { LineFeedback } from './LineFeedback'
import { queueReason } from '../workspace/stages/review/checkQueueModel'
import { LineMarkBadge } from '../workspace/stages/review/LineMarkButtons'
import { CastCard } from './CastCard'
import { NextCard } from './NextCard'
import { SleepTimerCard } from './SleepTimerCard'
import { useWatchSwipe } from './useWatchSwipe'
import { SaveSoundCard } from './SaveSoundCard'
import { Player, type PlayerHandle } from '../workspace/stages/review/Player'
import { playerKeyAction } from '../workspace/stages/review/playerLogic'
import '../workspace/stages/review/review.css'
import { titleKind } from '../workspace/titleKind'
import { NowCard } from './NowCard'
import { WatchParty } from './WatchParty'
import { Bookmarks } from './Bookmarks'
import { TopicChapterList } from './TopicChapterList'
import { useLockScreen } from './useLockScreen'
import { useWatchMarks } from './useWatchMarks'
import {
  attentionIndex, checkStep, currentLine, fixIsDirty, focusAfterFix, followPaused, followScroll, isReadOnlyKind, lineToFix, linesToCheck, loopLineAt, openFix,
  freshServerPlace, newerPlace, ownScrollUntil, parkedOutcome, parkFix, positionKey, positionTimeKey, quietSkip, readParkedFix, resumePoint, rowMeta, saveFix, SAVE_EVERY_MS, SERVER_SAVE_EVERY_MS, serverSeenKey, speakerSlots, spokenOrder,
  stepIndex, titleKindAndLanguages, unparkedFix, unparkFix, watchKey, watchVerb, type LineFix,
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

export default function WatchPage({ id, party }: { id: number; party?: string }) {
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

  // The same lines the Library's "Check N lines" counts. Only someone who can edit lines is asked to check them.
  const [riskIds, setRiskIds] = useState<ReadonlySet<number> | null>(null)
  useEffect(() => {
    if (!canEdit) return
    let cancelled = false
    // A failed question leaves no check bar: the Prev. and Next Flag buttons still step through flagged lines.
    listRiskLineIds(id).then((ids) => !cancelled && setRiskIds(ids), () => undefined)
    return () => {
      cancelled = true
    }
  }, [id, linesTick, canEdit])

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
  const { playerBoxRef, mediaRef, currentId, showCurrent, lineAt, resumedAt, clearResumed, skipTo } = useWatchClock(id, playable, ordered, player)
  const { resume: backToSound, scrollHandlers } = useFollow(list, currentId)
  // Every move of the playhead by the person also ends "Back where you stopped", which would name a place left behind.
  const resume = useCallback(() => {
    backToSound()
    clearResumed()
  }, [backToSound, clearResumed])

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
  const { markStatus, markBusy, mark, say } = useWatchMarks(id, setLines)
  // A fix's own message (Saved., a refused save) replaces a mark message that is still showing, not the other way round.
  useEffect(() => say(''), [status, say])
  const [swipeNote, setSwipeNote] = useState('')
  useEffect(() => {
    if (!swipeNote) return
    const t = window.setTimeout(() => setSwipeNote(''), 700)
    return () => window.clearTimeout(t)
  }, [swipeNote])
  const onSeekSwipe = useCallback((by: number) => {
    const el = mediaRef.current
    if (!el) return
    resume()
    player.current?.seek(Math.max(0, Math.min(el.duration || Infinity, el.currentTime + by)))
    // A fresh object-like string each time so two swipes the same way restart the badge's timer.
    setSwipeNote(`${by > 0 ? '+' : '−'}${Math.abs(by)} s`)
  }, [mediaRef, resume])
  useWatchSwipe(playerBoxRef, playable !== null && media !== undefined, onSeekSwipe)
  const jumpToMarked = (direction: 1 | -1) => {
    const to = attentionIndex(ordered, currentIndex >= 0 ? currentIndex : null, direction)
    if (to === null) {
      say(direction === 1 ? 'No marked or flagged line after this one.' : 'No marked or flagged line before this one.')
      return
    }
    say('')
    goTo(ordered[to])
  }
  const checks = useMemo(() => (riskIds ? linesToCheck(ordered, riskIds) : []), [ordered, riskIds])
  const stepCheck = (direction: 1 | -1) => {
    const to = checkStep(checks, lineIndex, currentIndex >= 0 ? currentIndex : null, direction)
    // Past the last one it starts again from the first, so the bar's button always goes somewhere.
    const line = checks[to ?? (direction === 1 ? 0 : checks.length - 1)]
    if (line) goTo(line)
  }
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
    if (key === 'good' || key === 'wrong' || key === 'mixed') {
      if (!canEdit || !current) return false
      // The buttons are off while a save is out and for a line with no English; the keys follow them.
      if (!busy && current.en) void mark(current, key)
      return true
    }
    if (key === 'nextmarked' || key === 'prevmarked') {
      jumpToMarked(key === 'nextmarked' ? 1 : -1)
      return true
    }
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
    writePref(browserStorage(), positionTimeKey(id), Date.now())
    writePref(browserStorage(), serverSeenKey(id), 0)
    void readerApi.saveWatchPosition(id, 0).catch(() => undefined)
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
  useLockScreen(mediaRef, id, playable, { title: drama ? titleOf(drama) : '', artist: drama?.studio || drama?.author || '', artwork: drama?.has_cover_art ? coverUrl(id) : undefined })

  return (
    <PageShell
      width="wide"
      className="watch-page"
      title={title}
      back={phone ? (
        // One row for the way back and the menu, so the header takes no extra row and the spoken line is on the first screen.
        <div className="watch-back-row">
          <a className="watch-back" href={routeHref({ name: 'library' })}>‹ Library</a>
          {drama && !readOnly && (
            <span className="watch-back-actions">
              <ButtonLink href={routeHref({ name: 'drama', id, stage: null })} title="Work on this title">Edit</ButtonLink>
              <WatchMore id={id} />
            </span>
          )}
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
        <>
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
                  loopLineAt={lineAt}
                />
                {swipeNote && <div className="watch-swipe-note" role="status">{swipeNote}</div>}
                {skipTo !== null && (
                  <div className="watch-resume">
                    <button type="button" className={buttonClass()} onClick={() => playFrom(skipTo)}>Skip quiet</button>
                  </div>
                )}
                {resumedAt !== null && (
                  <div className={original === 'video' ? 'watch-resume is-over' : 'watch-resume'} data-testid="watch-resume">
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
            {original === 'video' && <CastCard key={id} id={id} canMake={holds(session, 'jobs.start')} canCast={holds(session, 'media.stream')} />}
            {original && <NextCard key={id} id={id} boxRef={playerBoxRef} canStart={holds(session, 'jobs.start')} />}
            {original && canEdit && riskIds !== null && riskIds.size > 0 && (
              <div className="card watch-check" role="group" aria-label="Lines to check" data-testid="watch-check">
                <p className="watch-check-count">
                  {checks.length > 0 ? <strong>{checks.length} {checks.length === 1 ? 'line' : 'lines'} to check</strong> : 'No lines left to check'}
                </p>
                {checks.length > 0 && (
                  <span className="watch-check-actions">
                    <button type="button" className={buttonClass()} aria-label="Previous line to check" title="Previous line to check" onClick={() => stepCheck(-1)}>‹</button>
                    <button type="button" className={buttonClass('primary')} onClick={() => stepCheck(1)}>Go to the next one</button>
                  </span>
                )}
              </div>
            )}
            {original && ordered.length > 0 && (
              <NowCard
                line={fixLine ?? current}
                dramaId={id}
                sourceLanguage={sourceLanguage}
                canEdit={canEdit}
                fix={fix}
                busy={busy}
                status={markStatus || status}
                onMark={canEdit ? (verdict) => void mark(fixLine ?? current, verdict) : undefined}
                check={checkOf(fixLine ?? current, checks)}
                onJump={jumpToMarked}
                markBusy={markBusy}
                feedback={canEdit && (fixLine ?? current)?.mark === 'wrong' && !fix ? <LineFeedback key={(fixLine ?? current)?.id} id={id} line={(fixLine ?? current) as ReviewLine} onSaved={(saved) => setLines((ls) => ls?.map((l) => (l.id === saved.id ? saved : l)) ?? ls)} /> : undefined}
                onOpen={startFix}
                onDraft={setDraft}
                onSave={() => void save()}
                onCancel={cancel}
                onAgain={() => {
                  const line = fixLine ?? current
                  if (!line) return
                  resume()
                  player.current?.playLine(line)
                }}
                fixButtonRef={fixButtonRef}
                fieldRef={fieldRef}
                cardRef={cardRef}
              />
            )}
            {original && <SleepTimerCard key={id} boxRef={playerBoxRef} />}
          </div>
          <div className="watch-side">
            {original && <TopicChapterList dramaId={id} at={current?.start ?? null} onPick={playFrom} />}
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
            {original === 'audio' && <SaveSoundCard key={id} id={id} canStream={holds(session, 'media.stream')} canReadLines={holds(session, 'lines.read')} />}
          </div>
        </div>
        {/* Under both columns: a card that appears or changes above the lines would move them (and the page) under the person. */}
        {original && <Bookmarks dramaId={id} mediaRef={mediaRef} canEdit={canEdit} onPlay={playFrom} />}
        {original && <WatchParty dramaId={id} mediaRef={mediaRef} joinCode={party} />}
        </>
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
  const [skipTo, setSkipTo] = useState<number | null>(null)
  const skipRef = useRef<number | null>(null)
  // Read by the clock's listeners, which are attached once per media element.
  const orderedRef = useRef(ordered)
  const currentRef = useRef<number | null>(null)
  // Until the clock is first read (a play, a pause, a seek), the card asks for play rather than naming line 1.
  const clockRead = useRef(false)

  const showCurrent = useCallback((time: number) => {
    clockRead.current = true
    // The target is the same for the whole quiet stretch, so state changes only where it starts and ends.
    const skip = quietSkip(orderedRef.current, time)
    if (skip !== skipRef.current) {
      skipRef.current = skip
      setSkipTo(skip)
    }
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
    let lastServerSave = 0
    // The account's place from another device. The saved place waits for it, but not for long: a slow answer must not
    // keep the title from opening where this browser stopped.
    let server: { seconds: number; at: number | null } | null = null
    let answered = false
    let metaRead = false
    let gone = false
    const settle = () => {
      if (settled || gone) return
      settled = true
      const local = { seconds: Number(readPref(storage, positionKey(id), 0)) || 0, at: Number(readPref(storage, positionTimeKey(id), 0)) || null }
      const seen = readPref<number | null>(storage, serverSeenKey(id), null)
      const at = resumePoint(newerPlace(local, freshServerPlace(server, typeof seen === 'number' ? seen : null)), el.duration)
      if (server) writePref(storage, serverSeenKey(id), server.seconds)
      if (at === null) return
      player.current?.seek(at)
      setResumedAt(at)
    }
    const heard = () => {
      answered = true
      if (metaRead) settle()
    }
    readerApi.watchPosition(id).then((p) => {
      server = { seconds: p.seconds, at: parseTime(p.updated_at) || null }
    }, () => undefined).finally(heard)
    const waitLimit = window.setTimeout(heard, 2000)
    const save = (toServer = true) => {
      if (!settled) return
      writePref(storage, positionKey(id), el.currentTime)
      writePref(storage, positionTimeKey(id), Date.now())
      if (!toServer) return
      lastServerSave = Date.now()
      writePref(storage, serverSeenKey(id), el.currentTime)
      void readerApi.saveWatchPosition(id, el.currentTime).catch(() => undefined)
    }
    const tick = () => save(Date.now() - lastServerSave >= SERVER_SAVE_EVERY_MS)
    const frame = () => {
      showCurrent(el.currentTime)
      raf = requestAnimationFrame(frame)
    }
    const stopClock = () => {
      cancelAnimationFrame(raf)
      window.clearInterval(saver)
    }
    const onPlay = () => {
      // Pressed before the saved place was settled: playing from where the person pressed wins over a late seek.
      settled = true
      stopClock()
      setResumedAt(null)
      raf = requestAnimationFrame(frame)
      saver = window.setInterval(tick, SAVE_EVERY_MS)
    }
    const onPause = () => {
      stopClock()
      showCurrent(el.currentTime)
      save()
    }
    const onSeeked = () => showCurrent(el.currentTime)
    const onMeta = () => {
      metaRead = true
      if (answered) settle()
    }
    const onLeave = () => save()
    el.addEventListener('play', onPlay)
    el.addEventListener('pause', onPause)
    el.addEventListener('ended', onPause)
    el.addEventListener('seeked', onSeeked)
    el.addEventListener('loadedmetadata', onMeta)
    window.addEventListener('pagehide', onLeave)
    if (el.readyState >= 1) onMeta()
    if (!el.paused) onPlay()
    return () => {
      stopClock()
      save()
      gone = true
      window.clearTimeout(waitLimit)
      el.removeEventListener('play', onPlay)
      el.removeEventListener('pause', onPause)
      el.removeEventListener('ended', onPause)
      el.removeEventListener('seeked', onSeeked)
      el.removeEventListener('loadedmetadata', onMeta)
      window.removeEventListener('pagehide', onLeave)
    }
  }, [id, playable, player, showCurrent])

  const clearResumed = useCallback(() => setResumedAt(null), [])
  // The line Loop line repeats, by the card's own rule, so the line repeated is the line the card shows.
  const lineAt = useCallback((time: number) => loopLineAt(orderedRef.current, time), [])
  return { playerBoxRef, mediaRef, currentId, showCurrent, lineAt, resumedAt, clearResumed, skipTo }
}

/**
 * The list follows the sound inside its own scroll area (the page itself never moves for it), with the spoken row's
 * top a little way down so the coming lines show. It waits while the person scrolls the list themselves, by any means
 * (a wheel, the scroll bar, keys, Tab to a row out of view): every scroll of the list the page did not start is
 * theirs. A press on a row, a step or a seek brings it back at once.
 */
function useFollow(list: RefObject<HTMLOListElement | null>, currentId: number | null) {
  const scrolledAt = useRef<number | null>(null)
  // The page's own scroll: until when its scroll events are its own, and where it ends.
  const ownUntil = useRef(0)
  const ownTarget = useRef(0)
  const [tick, setTick] = useState(0)

  const scrolledByHand = useCallback(() => {
    scrolledAt.current = Date.now()
  }, [])
  const resume = useCallback(() => {
    scrolledAt.current = null
    setTick((t) => t + 1)
  }, [])

  const scrollHandlers = useMemo(() => ({
    // Input during the page's own glide, whose scroll events count as the page's.
    // A wheel or a touch ends the page's glide, and what follows is the person's: the glide's scrollend would land
    // elsewhere and leave its mark standing, so later scrolls of the list would go uncounted.
    onWheel: () => {
      ownUntil.current = 0
      scrolledByHand()
    },
    onTouchMove: () => {
      ownUntil.current = 0
      scrolledByHand()
    },
    onScroll: () => {
      if (Date.now() >= ownUntil.current) scrolledByHand()
    },
    // Only the end of the page's own scroll: another scroll's end can come while the page's glide is still running
    // (a row scrolled into view by focus just as it is pressed), and the rest of that glide is still the page's.
    onScrollEnd: (e: UIEvent<HTMLOListElement>) => {
      if (Math.abs(e.currentTarget.scrollTop - ownTarget.current) < 2) ownUntil.current = 0
    },
  }), [scrolledByHand])

  useEffect(() => {
    const box = list.current
    const row = box?.querySelector<HTMLElement>('li.is-current')
    if (!box || !row || followPaused(scrolledAt.current, Date.now())) return
    const offset = row.getBoundingClientRect().top - box.getBoundingClientRect().top - box.clientTop
    const delta = followScroll(offset, { height: box.clientHeight, scrollTop: box.scrollTop, maxScroll: box.scrollHeight - box.clientHeight })
    if (!delta) return
    ownUntil.current = ownScrollUntil(Date.now(), 'onscrollend' in window)
    ownTarget.current = box.scrollTop + delta
    // A jump (a seek, the saved place) lands at once; gliding there would leave the row out of view for a while.
    box.scrollBy({ top: delta, behavior: Math.abs(delta) > box.clientHeight ? 'auto' : scrollBehavior() })
  }, [list, currentId, tick])

  return { resume, scrollHandlers }
}

/**
 * The open fix, its save, and its draft kept from being lost: leaving the page while it is dirty asks first; leaving
 * inside the app parks it in this tab and saves it, and if that save fails, the next visit opens it again. A fix whose
 * last Save was refused is parked but not sent, and a page put away parks its fix without sending it.
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
    // A save already running is the one to wait for; sending the draft again would be refused as a change. A refused
    // fix is not sent: it would go over the other person's text, which the viewer has seen but not chosen to replace.
    const sent = saving.current ?? (left.conflict ? null : patchLine(id, left.lineId, body).then(() => true))
    if (!sent) return
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

  // Phones rarely fire beforeunload and may discard a tab that is put away, so a hidden page parks its fix (sending
  // nothing) for the reloaded tab to offer back; shown again, the open box holds it and the parked copy goes.
  useEffect(() => {
    const store = browserSession()
    let parked = false
    const hide = () => {
      const left = fixRef.current
      if (!left || !fixIsDirty(left)) return
      parkFix(id, left, store)
      parked = true
    }
    const show = () => {
      if (parked) unparkFix(id, store)
      parked = false
    }
    const onVisibility = () => (document.visibilityState === 'hidden' ? hide() : show())
    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('pagehide', hide)
    window.addEventListener('pageshow', show)
    return () => {
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('pagehide', hide)
      window.removeEventListener('pageshow', show)
    }
  }, [id])

  const offered = useRef(false)
  useEffect(() => {
    if (offered.current || !ready || !canEdit) return
    offered.current = true
    const offer = () => {
      const store = browserSession()
      const parked = readParkedFix(id, store)
      if (!parked) return
      // From here the open box holds it, or there is nothing to bring back.
      unparkFix(id, store)
      const index = lineIndex.get(parked.lineId)
      const line = index === undefined ? undefined : ordered[index]
      const outcome = parkedOutcome(parked, line)
      if (outcome === 'gone') setStatus(RESTORE_GONE)
      else if (outcome === 'open' && line) {
        setFix(unparkedFix(parked, line))
        setStatus(RESTORED)
      }
    }
    const running = leavingSaves.get(id)
    if (running) void running.then(offer)
    else offer()
  }, [id, ready, canEdit, lineIndex, ordered])

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
    // A fix brought back and then dropped is in no box any more, so the sentence that says it is goes with it.
    setStatus('')
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
    }
    // An unchanged fix closes unsent, so a sentence about a fix brought back would speak of a box that is gone.
    setStatus(result.kind === 'saved' ? 'Saved.' : '')
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
  const slots = useMemo(() => speakerSlots(lines), [lines])
  return (
    <ol className="watch-list" ref={listRef} {...scroll}>
      {lines.map((l) => (
        <WatchRow key={l.id} line={l} slot={l.speaker ? slots.get(l.speaker) ?? null : null} current={l.id === currentId} sourceLanguage={sourceLanguage} canPlay={canPlay} onPick={onPick} />
      ))}
    </ol>
  )
})

const WatchRow = memo(function WatchRow({ line: l, slot, current, sourceLanguage, canPlay, onPick }: {
  line: ReviewLine
  slot: number | null
  current: boolean
  sourceLanguage: string
  canPlay: boolean
  onPick: (start: number) => void
}) {
  const text = (
    <>
      <span className="watch-row-meta">
        {rowMeta(l)}
        {l.speaker && <span className="watch-row-speaker">{l.speaker}</span>}
        <LineMarkBadge line={l} />
        {l.flag && <span className="watch-row-flag" role="img" aria-label="Flagged" title="Flagged">⚑</span>}
      </span>
      {l.zh && <span className="watch-row-zh" lang={l.lang || sourceLanguage}>{l.zh}</span>}
      <span className={l.en ? 'watch-row-en' : 'watch-row-en is-empty'} lang={l.en ? 'en' : undefined}>{l.en || 'No English yet'}</span>
    </>
  )
  return (
    <li className={current ? 'is-current' : undefined} data-speaker={slot ?? undefined}>
      {canPlay ? (
        <button
          type="button"
          className="watch-row"
          aria-current={current ? 'true' : undefined}
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

// "Check 2 of 7" and why, while the line on the card is one still to check.
function checkOf(line: ReviewLine | null, checks: ReviewLine[]) {
  const at = line ? checks.findIndex((l) => l.id === line.id) : -1
  if (!line || at < 0) return null
  const why = queueReason(line)?.text ?? (line.mark ? 'Someone marked this line as not right.' : 'This line looks unsure.')
  return { index: at + 1, total: checks.length, reason: why }
}

function WatchMore({ id }: { id: number }) {
  const more = useDetailsMenu()
  const close = (e: MouseEvent<HTMLAnchorElement>) => {
    const menu = e.currentTarget.closest('details')
    if (menu) menu.open = false
  }
  return (
    <details className="watch-more" ref={more}>
      <summary className={buttonClass()}>More</summary>
      <div className="watch-more-panel">
        <a href={routeHref({ name: 'follow', id })} onClick={close}>Follow along with sound from another app</a>
        <a href={routeHref({ name: 'read', id, page: null })} onClick={close}>Read the lines</a>
        <a href={routeHref({ name: 'drama', id, stage: 'export' })} onClick={close}>Save subtitles</a>
      </div>
    </details>
  )
}
