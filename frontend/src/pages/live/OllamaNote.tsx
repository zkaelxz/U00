/** The Live form's note for an Ollama that is not ready. */
import type { useOllamaCheck } from './useOllamaCheck'

type Check = ReturnType<typeof useOllamaCheck>

export function OllamaNote({ check, alert }: { check: Check; alert: boolean }) {
  return (
    <p className={alert ? 'error' : 'muted'} role={alert ? 'alert' : undefined} data-testid="live-ollama-note">
      {check.note} Pick another engine or model above, or fix this and{' '}
      <button type="button" className="link" onClick={check.recheck}>check again</button>.
    </p>
  )
}
