// The Watch page's sleep timer: pauses the sound or video after a chosen time, so falling asleep never leaves it playing all night.
import { useEffect, useState, type RefObject } from 'react'

import { buttonClass } from '../../components/uiClasses'
import { SLEEP_MINUTES, sleepLeftText, sleepRemainingMs } from './sleepLogic'

export function SleepTimerCard({ boxRef }: { boxRef: RefObject<HTMLElement | null> }) {
  const [chosen, setChosen] = useState<number | null>(null)
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
        setChosen(null)
        setFired(true)
      }
    }, 1000)
    return () => window.clearInterval(timer)
  }, [endAt, boxRef])

  const start = (minutes: number) => {
    const t = Date.now()
    setNow(t)
    setFired(false)
    setChosen(minutes)
    setEndAt(t + minutes * 60000)
  }
  const turnOff = () => {
    setEndAt(null)
    setChosen(null)
  }

  return (
    <section className="card watch-sleep" aria-labelledby="watch-sleep-title">
      <p>
        <strong id="watch-sleep-title">Sleep timer</strong>{' '}
        <span className="muted">
          {endAt !== null ? `Pauses in ${sleepLeftText(sleepRemainingMs(endAt, now))}` : fired ? 'Paused. Good night.' : 'Off'}
        </span>
        {/* Only the changes of state are announced; the countdown itself is not, so a screen reader stays quiet over the sound. */}
        <span className="visually-hidden" role="status">{endAt !== null ? `Sleep timer set for ${chosen} minutes` : fired ? 'Sleep timer paused the sound' : ''}</span>
      </p>
      <div className="watch-card-actions">
        {SLEEP_MINUTES.map((m) => (
          <button key={m} type="button" className={buttonClass()} aria-pressed={chosen === m} onClick={() => start(m)}>{m} min</button>
        ))}
        {/* Always on the row, so starting a timer never changes the card's height and moves the line under the thumb. */}
        <button type="button" className={buttonClass()} disabled={endAt === null} onClick={turnOff}>Turn off</button>
      </div>
    </section>
  )
}
