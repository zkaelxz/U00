import { useCallback, useRef, useState } from 'react'

import { ErrorBanner } from '../../../../components/ErrorBanner'
import { applySameFix, findSameFix, type SameFixOffer } from './sameFix'
import { SameFixNotice } from './SameFixNotice'

/** After an English fix is saved, offers the same fix in the title's other lines that have the
 *  old wording. Nothing changes until Apply. */
export function useSameFix(dramaId: number, onChanged: () => void, setStatus: (text: string) => void) {
  // Kept with its title, so another title's offer is never shown.
  const [held, setOffer] = useState<(SameFixOffer & { dramaId: number }) | null>(null)
  const offer = held?.dramaId === dramaId ? held : null
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<unknown>(null)
  // A later save makes an answer still on its way stale.
  const asked = useRef(0)

  const englishSaved = useCallback((lineId: number, before: string, after: string) => {
    const ask = ++asked.current
    setOffer(null)
    findSameFix(dramaId, lineId, before, after).then((found) => {
      if (ask === asked.current) setOffer(found && { ...found, dramaId })
    }, () => {
      // The offer is a convenience: the saved edit stands without it.
    })
  }, [dramaId])

  const apply = async (always: boolean) => {
    if (!offer) return
    setBusy(true)
    try {
      const done = await applySameFix(dramaId, offer, always)
      if (!done.blocked) setOffer(null)
      setStatus(done.text)
      if (done.changed) onChanged()
    } catch (e) {
      setError(e)
    } finally {
      setBusy(false)
    }
  }

  const node = (
    <>
      {offer && (
        <SameFixNotice key={`${offer.fix.find}\u0000${offer.fix.replace}`} offer={offer} busy={busy} onApply={() => void apply(false)} onAlways={() => void apply(true)}
          onDismiss={() => setOffer(null)} />
      )}
      <ErrorBanner error={error} onDismiss={() => setError(null)} />
    </>
  )
  return { englishSaved, node }
}
