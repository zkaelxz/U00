// The two files that make a sound-only title usable with no signal: its own sound and the English subtitles.
import { apiUrl } from '../../api/client'
import { mediaStreamUrl } from '../../api/media'

export interface SaveLink {
  label: string
  href: string
}

export function saveSoundLinks(id: number, canStream: boolean, canReadLines: boolean): SaveLink[] {
  const links: SaveLink[] = []
  if (canStream) links.push({ label: 'Save the sound', href: mediaStreamUrl(id, 'audio') })
  if (canReadLines) links.push({ label: 'Save English subtitles', href: apiUrl(`/api/export/dramas/${id}/subtitle?fmt=srt&field=en`) })
  return links
}
