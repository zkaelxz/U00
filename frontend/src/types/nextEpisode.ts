export interface NextEpisode {
  drama_id: number | null
  title: string
  state: 'ready' | 'working' | 'idle' | 'no_media'
  /** Its translation engine costs nothing, so the page may start it without asking. */
  free: boolean
}
