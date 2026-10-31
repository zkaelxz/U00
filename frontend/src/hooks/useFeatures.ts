import { useEffect, useSyncExternalStore } from 'react'

import { getFeatures, subscribeFeatures, type Features } from '../api/features'
import { loadPcMode } from '../api/pcOnly'

/** The feature switches; every area reads as on until /api/meta says otherwise. */
export function useFeatures(): Features {
  const features = useSyncExternalStore(subscribeFeatures, getFeatures, getFeatures)
  useEffect(() => {
    void loadPcMode()
  }, [])
  return features
}
