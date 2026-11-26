/*
 * The watching view of a Live session: the stream chips when two run, the
 * picture, the lines (or the chat) in a box that fills the height, and Stop.
 * Kept apart from the form in pages/Live.tsx, which hides itself while a
 * session runs.
 */
import { useEffect, useRef, useState } from 'react'

import { feedCues, isActive, lagLine, statusLine, statusTone } from '../../api/live'
import { buttonClass } from '../../components/uiClasses'
import { cancelsAnyJob, useSession } from '../../hooks/useSession'
import { usePcOnly } from '../../hooks/usePcOnly'
import { usePersistedState } from '../../hooks/usePersistedState'
import { canPopOut, popOut, setLiveWatching } from '../../phoneShell'
import { DelayedPlayer } from './DelayedPlayer'
import { DEFAULT_DELAY, autoDelay, canDelay, sessionStream, steadyDelay } from './embedLogic'
import { LinesPane } from './LinesPane'
import { LiveCaptions } from './LiveCaptions'
import { LiveChatSection } from './LiveChat'
import { SaveSession } from './SaveSession'
import { StreamEmbed } from './StreamEmbed'
import { isMetered, videoHeldBack } from '../comic/lowData'
import { TvBar } from './TvBar'
import type { TvTextSize } from './tvSize'
import { useTvMode } from './useTvMode'
import { ViewingOptions, type TextSize } from './ViewingOptions'
import { chipLabels, useRunningSessions } from './useRunningSessions'
import type { Session } from './useLiveSession'

type Props = {
  session: Session
  stopping: false | 'finishing' | 'now'
  joined: boolean
  onStop: () => void
  onClear: () => void
  onSwitch: (id: string) => void
  /** The form's language and chunk length, for a session whose status has not arrived yet. */
  language: string
  segmentSeconds: number
}

export function LiveWatch({ session, stopping, joined, onStop, onClear, onSwitch, language, segmentSeconds }: Props) {
  const pc = usePcOnly()
  const status = session.status?.status ?? 'queued'
  const active = isActive(status)
  const running = useRunningSessions(session.id, cancelsAnyJob(useSession()))
  const [storedShowVideo, setShowVideo] = usePersistedState<boolean>('live.showVideo', true)
  const [dataSaver, setDataSaver] = usePersistedState<boolean>('live.dataSaver', true)
  // Asking for the picture while it is held back lasts until the page is reloaded, so the saved choice stays as it was.
  const [forced, setForced] = useState(false)
  const held = videoHeldBack(dataSaver, isMetered(), forced)
  const root = useRef<HTMLDivElement>(null)
  const tv = useTvMode(root, !active)
  // TV mode's own "No video" leaves the saved Show video choice alone, so normal viewing comes back as it was.
  const showVideo = storedShowVideo && !held && !(tv.on && tv.video === 'off')
  const [captionsOn, setCaptionsOn] = usePersistedState<boolean>('live.captions', true)
  const [videoDelay, setVideoDelay] = usePersistedState<number>('live.videoDelay', DEFAULT_DELAY)
  const [delayAuto, setDelayAuto] = usePersistedState<boolean>('live.videoDelayAuto', true)
  const [textSize, setTextSize] = usePersistedState<TextSize>('live.textSize', 'm')
  const [tvTextSize, setTvTextSize] = usePersistedState<TvTextSize>('live.tvTextSize', 'l')
  const [showOriginal, setShowOriginal] = usePersistedState<boolean>('live.showOriginal', true)
  const [tab, setTab] = useState<'lines' | 'chat'>('lines')

  // The picture exists only while the session runs and Stop has not been
  // pressed: leaving it mounted would keep the stream's audio playing.
  // What the running session records, not the form. Its files are served to the PC only.
  // A player that stopped with a stream break and could not start again gives way to the site's embed.
  const ownPlayer = session.status?.delayed_player === true && session.status.player_stopped !== true && pc !== 'remote'
  const videoLive = status === 'running' && !stopping && (!!session.url || !!session.status?.stream || ownPlayer)
  const streamRef = videoLive ? sessionStream(session.url, session.status?.stream) : null
  const [feed, total] = [feedCues(session.cues), session.next ?? 0]
  const lag = session.status ? lagLine(session.status) : null

  // Held steady between small changes of the estimate: each change seeks the picture.
  const estimate = autoDelay(session.status?.segment_seconds || segmentSeconds,
    session.status?.processing_seconds ?? 0, (session.status?.chunks_measured ?? 0) > 0)
  const [autoValue, setAutoValue] = useState<number | null>(null)
  // A new session's estimate starts from its own chunks, not the last session's.
  const [valueFor, setValueFor] = useState(session.id)
  if (valueFor !== session.id) {
    setValueFor(session.id)
    setAutoValue(null)
  }
  const steadyValue = steadyDelay(valueFor === session.id ? autoValue : null, estimate)
  if (steadyValue !== autoValue) setAutoValue(steadyValue)
  // What the player reports it really runs behind live; the chat panel waits that long.
  const [pictureDelay, setPictureDelay] = useState(0)
  const delay = delayAuto ? (autoValue ?? estimate) : videoDelay
  const lang = session.status?.source_language ?? language
  const hasChat = session.status?.chat === true
  const captions = captionsOn
    ? (d: number) => <LiveCaptions key={session.id} cues={session.cues} delay={d} chunkSeconds={session.status?.segment_seconds} />
    : undefined
  const labels = chipLabels(running)
  const pictureShown = videoLive && (!!streamRef || ownPlayer) && showVideo
  // Whether TV mode has a picture to place: the choice stays offered while it is off, but not when none could ever show.
  const tvPicture = videoLive && (!!streamRef || ownPlayer) && storedShowVideo && !held
  // The phone app floats the picture (with its captions) when you leave it, so Live keeps playing over other apps.
  useEffect(() => {
    setLiveWatching(pictureShown)
    return () => setLiveWatching(false)
  }, [pictureShown])

  const delayable = (streamRef || ownPlayer) && showVideo && (ownPlayer || (streamRef && canDelay(streamRef)))
  // Beside the picture when there is one; above the lines otherwise, so the text options never depend on a video.
  const options = (
    <ViewingOptions textSize={textSize} onTextSize={setTextSize} showOriginal={showOriginal} onShowOriginal={setShowOriginal}
      captionsOn={captionsOn} onCaptions={setCaptionsOn} showVideo={showVideo} onShowVideo={(v) => (v && held ? setForced(true) : setShowVideo(v))} dataSaver={dataSaver} onDataSaver={setDataSaver} hasPicture={videoLive} onTv={tv.enter}
      onFloat={pictureShown && canPopOut() ? popOut : null}
      delay={delayable ? {
        seconds: delay, auto: delayAuto, manual: videoDelay,
        onSlide: (v) => { setDelayAuto(false); setVideoDelay(v) }, onAuto: setDelayAuto,
      } : null} />
  )

  // Under the picture when there is one, else above the lines: a bar pinned to the bottom covered the last lines.
  const stopBar = active && !tv.on && (
    <div className="live-stopbar">
      <button type="button" className={buttonClass('secondary')} onClick={onStop} disabled={stopping === 'now'} aria-busy={stopping === 'now' || undefined}>
      {stopping === 'finishing' ? 'Stop now' : stopping ? 'Stopping…' : joined ? 'Leave' : status === 'queued' ? 'Cancel' : 'Stop'}
    </button>
    </div>
  )

  return (
    <div ref={root} className={videoLive ? 'live-watch has-video' : 'live-watch'} data-running={active || undefined}
      data-tv={tv.on ? (tvPicture ? tv.video : 'off') : undefined} data-tv-bar={tv.on && tv.barShown ? '' : undefined}>
      {tv.on && <TvBar shown={tv.barShown} hasPicture={tvPicture} video={tv.video} onVideo={tv.setVideo} showOriginal={showOriginal} onShowOriginal={setShowOriginal} textSize={tvTextSize} onTextSize={setTvTextSize} onExit={tv.exit} />}
      {tv.on && !tv.barShown && <button type="button" className="live-tv-menu" aria-label="TV mode menu" onClick={tv.showBar}>Menu</button>}
      {running.length > 1 && !tv.on && (
        <div className="live-chips" role="tablist" aria-label="Running streams">
          {running.map((s, i) => (
            <button key={s.session_id} type="button" role="tab" aria-selected={s.session_id === session.id}
              onClick={() => s.session_id !== session.id && onSwitch(s.session_id)}>
              <span className="live-chip-dot" aria-hidden="true" />{labels[i]}
            </button>
          ))}
        </div>
      )}
      {videoLive && !(tv.on && !pictureShown) && (
        <div className="card live-video" role="group" aria-label="Stream video">
          {!streamRef && !ownPlayer && <p className="muted">This site can't be shown here; the lines still work.</p>}
          {(streamRef || ownPlayer) && showVideo && (
            ownPlayer
              ? <DelayedPlayer sessionId={session.id} ready={session.status?.player_ready === true} delay={delay} onPictureDelay={setPictureDelay} captions={captions} />
              : streamRef && <StreamEmbed stream={streamRef} delay={delay} onPictureDelay={setPictureDelay} captions={captions} />
          )}
          {stopBar}
          {!tv.on && options}
        </div>
      )}
      {!(videoLive && !(tv.on && !pictureShown)) && stopBar}
      <div className="live-feed">
        {/* Only the status line is announced: the lines below change every chunk. */}
        <p className={session.status ? statusTone(session.status) : 'muted'} data-testid="live-status" role="status">
          {session.status ? statusLine(session.status, total) : 'Connecting…'}
        </p>
        {lag && <p className="muted" data-testid="live-lag">{lag}</p>}
        {!!session.status?.notes?.length && (
          <ul className="muted live-notes" data-testid="live-notes" aria-label="Live events">
            {session.status.notes.map((n, i) => <li key={`${i}-${n}`}>{n}</li>)}
          </ul>
        )}
        {!videoLive && !tv.on && options}
        {active && status === 'running' && !feed.length && <p className="muted">Waiting for the first chunk…</p>}
        {hasChat && !tv.on && (
          <div className="live-tabs" role="tablist" aria-label="Show">
            {(['lines', 'chat'] as const).map((t) => (
              <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>{t === 'lines' ? 'Lines' : 'Chat'}</button>
            ))}
          </div>
        )}
        <div className="live-pane" hidden={hasChat && tab === 'chat' && !tv.on}>
          {feed.length > 0 && <LinesPane cues={feed} total={total} lang={lang} waiting={active} size={tv.on ? 'xl' : textSize} tvSize={tv.on ? tvTextSize : undefined} showOriginal={showOriginal} />}
          {total > feed.length && <p className="muted">Showing the newest {feed.length} of {total} lines.</p>}
        </div>
        {/* Mounted while hidden: the chat keeps reading, and its messages keep their timing. */}
        {hasChat && (
          <div className="live-pane" hidden={tab !== 'chat' || tv.on}>
            <LiveChatSection sessionId={session.id} running={active} language={lang} delay={showVideo && (streamRef || ownPlayer) ? pictureDelay : 0} />
          </div>
        )}
        {total > 0 && !tv.on && <SaveSession cues={session.cues} />}
        {!active && total > 0 && !tv.on && <button type="button" className="secondary" data-testid="live-clear-lines" onClick={onClear}>Clear lines</button>}
      </div>
    </div>
  )
}
