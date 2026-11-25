import { useState } from 'react'

import { HelpTip } from '../../components/HelpTip'
import { buttonClass } from '../../components/uiClasses'
import type { AdminUser } from '../../types/adminUsers'
import { MAX_JOB_LIMIT, parseJobLimit } from './adminUsers'

/** A member's own daily limit on the background jobs they start, beside their paid-engine limit. */
export function JobLimitForm({ user: u, busy, onLimit }: { user: AdminUser; busy: boolean; onLimit: (jobs: number | null) => void }) {
  const saved = u.daily_job_limit ?? null
  const [text, setText] = useState(saved === null ? '' : String(saved))
  const jobs = parseJobLimit(text)
  const id = `admin-job-limit-${u.id}`
  const bad = text.trim() !== '' && jobs === null
  return (
    <form className="admin-user-limit" onSubmit={(e) => {
      e.preventDefault()
      if (jobs !== null) onLimit(jobs)
    }}>
      <label htmlFor={id}>Jobs they can start a day</label>{' '}
      <HelpTip label="Jobs they can start a day" id={`${id}-tip`}>
        Every background job counts: a transcription, a translation, a download. Once they have started this many
        today, new ones are refused until tomorrow. Jobs already running finish. The count starts again at midnight on
        this PC. Nobody is limited unless you set a number.
      </HelpTip>
      <input id={id} type="text" inputMode="numeric" size={6} value={text} placeholder="No limit"
        aria-invalid={bad || undefined} aria-describedby={bad ? `${id}-bad` : undefined}
        onChange={(e) => setText(e.target.value)} />{' '}
      <button type="submit" className={buttonClass('secondary', 'sm')} disabled={busy || jobs === null || jobs === saved}>Save limit</button>{' '}
      {saved !== null && (
        <button type="button" className={buttonClass('secondary', 'sm')} disabled={busy} onClick={() => {
          setText('')
          onLimit(null)
        }}>Remove limit</button>
      )}
      {bad && <div className="muted" id={`${id}-bad`}>Enter a whole number from 1 to {MAX_JOB_LIMIT.toLocaleString('en-US')}, such as 10.</div>}
    </form>
  )
}
