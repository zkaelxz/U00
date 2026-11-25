// The Watch page's sleep timer: pauses the sound or video after a chosen time, so falling asleep never leaves it playing all night.
import { useEffect, useState, type RefObject } from 'react'

import { buttonClass } from '../../components/uiClasses'
import { SLEEP_MINUTES, sleepLeftText, sleepRemainingMs } from './sleepLogic'

export function SleepTimerCard({ boxRef }: { boxRef: RefObject<HTMLElement | null> }) {
  const [endAt, setEndAt] = useState<number | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const [fired, setFired] = useState(false)

  useEffect(() => {
    if (endAt === null) return
    const timer = window.setInterval(() => {
      const t = Date.now()
      setNow(t)
      if (sleepRemainingMs(endAt, t) === 0) {
        boxRef.current?.querySelector<HTMLMediaElement>('audio, video')?.pause()
        setEndAt(null)
        setFired(true)
      }
    }, 1000)
    return () => window.clearInterval(timer)
  }, [endAt, boxRef])

  const start = (minutes: number) => {
    const t = Date.now()
    setNow(t)
    setFired(false)
    setEndAt(t + minutes * 60000)
  }

  return (
    <section className="card watch-sleep" aria-label="Sleep timer">
      <p>
        <strong>Sleep timer</strong>{' '}
        <span className="muted" role="status">
          {endAt !== null ? sleepLeftText(sleepRemainingMs(endAt, now)) : fired ? 'Paused. Good night.' : 'Off'}
        </span>
      </p>
      <div className="watch-card-actions">
        {SLEEP_MINUTES.map((m) => (
          <button key={m} type="button" className={buttonClass()} onClick={() => start(m)}>{m} min</button>
        ))}
        {endAt !== null && (
          <button type="button" className={buttonClass()} onClick={() => setEndAt(null)}>Cancel</button>
        )}
      </div>
    </section>
  )
}
