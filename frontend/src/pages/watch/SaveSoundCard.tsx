// The Watch page's "Save to phone" for a sound-only title: its sound and English subtitles as two files.
import { buttonClass } from '../../components/uiClasses'
import { saveSoundLinks } from './saveSoundLinks'

export function SaveSoundCard({ id, canStream, canReadLines }: { id: number; canStream: boolean; canReadLines: boolean }) {
  const links = saveSoundLinks(id, canStream, canReadLines)
  if (links.length === 0) return null
  return (
    <section className="card watch-cast" aria-label="Save for offline">
      <p><strong>Save to phone</strong></p>
      <p>Keep the sound and the English lines to use with no signal.</p>
      <div className="watch-card-actions">
        {links.map((l) => (
          <a key={l.href} className={buttonClass()} href={l.href} download>{l.label}</a>
        ))}
      </div>
    </section>
  )
}
