import { useState } from 'react'

import { usePersistedState } from '../../../../hooks/usePersistedState'
import { FLOAT_DEFAULT_WIDTH } from './floatLogic'

// State for the in-page floating player: whether it is out, the element the panel moves into, and its remembered width.
export function useFloatBox() {
  const [boxed, setBoxed] = useState(false)
  const [boxHost, setBoxHost] = useState<HTMLElement | null>(null)
  const [boxWidth, setBoxWidth] = usePersistedState('review.floatWidth', FLOAT_DEFAULT_WIDTH)
  return { boxed, setBoxed, boxHost, setBoxHost, boxWidth, setBoxWidth }
}
