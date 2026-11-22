/** The Live form's note for an Ollama that is not ready or may not fit in graphics memory. */
import type { useOllamaCheck } from './useOllamaCheck'

type Check = ReturnType<typeof useOllamaCheck>

// A graphics-memory warning carries its own advice, and Start still goes ahead, so it is never an alert.
export function OllamaNote({ check, alert, onStartAnyway }: { check: Check; alert: boolean; onStartAnyway: () => void }) {
  const { note, fitWarning, asking } = check
  return (
    <p className={alert ? 'error' : 'muted'} role={alert ? 'alert' : undefined} data-testid="live-ollama-note">
      {note} {fitWarning ? '' : 'Pick another engine or model above, or fix this and '}
      <button type="button" className="link" onClick={check.recheck}>{fitWarning ? 'Check again' : 'check again'}</button>.
      {asking && (
        <>
          {' '}<span role="alert">Start anyway?</span>{' '}
          <button type="button" className="secondary" data-testid="live-start-anyway" onClick={onStartAnyway}>Yes, start</button>
        </>
      )}
    </p>
  )
}
