import { describe, expect, it } from 'vitest'

import type { AuthMe, MemberLimits } from '../../api/auth'
import { limitLines, myLimits } from './limitsModel'

const limits = (o: Partial<MemberLimits> = {}): MemberLimits => ({
  daily_job_limit: null, jobs_today: 0, monthly_paid_cap_usd: null, month_paid_spend_usd: 0,
  title_limit: 200, titles_owned: 3, ...o,
})

describe('limitLines', () => {
  it('shows the limits the owner set, and always the title limit', () => {
    expect(limitLines(limits()).map((l) => l.text)).toEqual(['Titles you own: 3 of 200'])
    expect(limitLines(limits({ monthly_paid_cap_usd: 2, month_paid_spend_usd: 0.5, daily_job_limit: 5, jobs_today: 1 }))
      .map((l) => l.text)).toEqual([
      'AI spending this month: $0.50 of $2.00',
      'Jobs started today: 1 of 5',
      'Titles you own: 3 of 200',
    ])
  })
  it('warns from 80% of a limit', () => {
    const near = (o: Partial<MemberLimits>) => limitLines(limits(o)).filter((l) => l.near).map((l) => l.id)
    expect(near({ monthly_paid_cap_usd: 1, month_paid_spend_usd: 0.79 })).toEqual([])
    expect(near({ monthly_paid_cap_usd: 1, month_paid_spend_usd: 0.8 })).toEqual(['spend'])
    expect(near({ daily_job_limit: 5, jobs_today: 4, titles_owned: 160 })).toEqual(['jobs', 'titles'])
  })
})

describe('myLimits', () => {
  it('is the member’s own limits, and none for an admin or while loading', () => {
    const me = { auth_enabled: true, signed_in: true, sign_in_configured: true, zone: 'internet', user: null, permissions: [] } as AuthMe
    expect(myLimits({ status: 'ready', me: { ...me, limits: limits() } })).toEqual(limits())
    expect(myLimits({ status: 'ready', me })).toBeNull()
    expect(myLimits({ status: 'loading' })).toBeNull()
  })
})
