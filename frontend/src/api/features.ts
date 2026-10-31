/*
 * Feature switches from GET /api/meta (lib/features.py on the server). A
 * missing answer means on: the switches are a kill switch, so a failed or old
 * /api/meta must never hide a working area.
 */
export type FeatureName = 'jellyfin' | 'sources' | 'extension'
export type Features = Partial<Record<FeatureName, boolean>>

let features: Features = {}
const listeners = new Set<() => void>()

export function featureOn(name: FeatureName, from: Features = features): boolean {
  return from[name] !== false
}

export function getFeatures(): Features {
  return features
}

export function subscribeFeatures(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function applyFeatures(next: unknown): void {
  const parsed: Features = {}
  if (next && typeof next === 'object') {
    for (const name of ['jellyfin', 'sources', 'extension'] as const) {
      const v = (next as Record<string, unknown>)[name]
      if (typeof v === 'boolean') parsed[name] = v
    }
  }
  features = parsed
  listeners.forEach((l) => l())
}
