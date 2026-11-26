export interface CastStatus {
  state: 'ready' | 'working' | 'missing' | 'unsupported'
}

export interface CastLink {
  /** Relative to the address the page was opened at; the TV is given that address plus this. */
  path: string
  expires_in: number
}

export type ShareDays = 1 | 3 | 7
