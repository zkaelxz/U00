// Reader's Listen section and Watch / listen player, which share the version of the narration track.
import { useState } from 'react'

import { mediaStreamUrl } from '../../api/media'
import { captionUrl, readerApi } from '../../api/reader'
import { Section } from '../../components/Section'
import type { ReaderMediaAvailability } from '../../types/reader'
import { ListenSection } from './ListenSection'
import { ReadAlong } from './ReadAlong'

function WatchSection({ dramaId, media, sourceLanguage, trackVersion }: { dramaId: number; media: ReaderMediaAvailability; sourceLanguage: string; trackVersion: number }) {
  const [failed, setFailed] = useState(false)
  const kinds = [media.original === 'video' ? 'video' : media.original === 'audio' ? 'audio' : null, media.narration ? 'narration' : null]
  const summary = kinds.filter(Boolean).join(' · ')
  const lang = (t: string) => (t === 'English' ? 'en' : t === 'Source' ? sourceLanguage : 'und')
  return (
    <Section title="Watch / listen" storageKey="reader.media" summary={summary}>
      {media.original === 'video' && (
        <video className="reader-video" controls preload="metadata" src={mediaStreamUrl(dramaId, 'video')} onError={() => setFailed(true)}>
          {media.caption_tracks.map((t, i) => (
            <track key={t} kind="subtitles" label={t} srcLang={lang(t)} src={captionUrl(dramaId, t)} default={i === 0} />
          ))}
        </video>
      )}
      {media.original === 'audio' && (
        <audio controls preload="metadata" src={mediaStreamUrl(dramaId, 'audio')} onError={() => setFailed(true)} />
      )}
      {media.narration && <ReadAlong dramaId={dramaId} version={trackVersion} onError={() => setFailed(true)} />}
      {failed && <p className="muted">Can't play this here. It needs media playback permission, or the file is missing.</p>}
    </Section>
  )
}

export function ListenWatch({ dramaId, media, setMedia, sourceLanguage }: {
  dramaId: number
  media: ReaderMediaAvailability | null
  setMedia: (m: ReaderMediaAvailability | null) => void
  sourceLanguage: string
}) {
  // Bumped when Listen replaces the narration, so the player drops the old file it has cached.
  const [trackVersion, setTrackVersion] = useState(0)
  const onReady = () => {
    void readerApi.media(dramaId).then(setMedia, () => setMedia(null))
    setTrackVersion((v) => v + 1)
  }
  return (
    <>
      <ListenSection dramaId={dramaId} onReady={onReady} />
      {media && (media.original !== null || media.narration) && (
        <WatchSection dramaId={dramaId} media={media} sourceLanguage={sourceLanguage} trackVersion={trackVersion} />
      )}
    </>
  )
}
