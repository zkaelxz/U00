/*
 * The one place that turns a message's mention of a setting into a link that opens it. A message names a
 * setting as "Settings > Spending" (a card's label from the Settings index) or as a bare "in Settings";
 * the first opens that card, the second the Settings page. Messages built from server or helper text go
 * through SettingsMentions; code that already knows the card renders SettingLink directly.
 */
import { Fragment, type ReactNode } from 'react'

import { routeHref } from '../router'
import { splitSettingMentions } from './settingMentions'

export function SettingLink({ card, children }: { card?: string; children: ReactNode }) {
  return <a href={routeHref(card ? { name: 'settings', card } : { name: 'settings' })}>{children}</a>
}

export function SettingsMentions({ text }: { text: string }) {
  const parts = splitSettingMentions(text)
  if (!parts.some((p) => p.link)) return <>{text}</>
  return (
    <>
      {parts.map((p, i) => (
        <Fragment key={i}>{p.link ? <SettingLink card={p.card}>{p.text}</SettingLink> : p.text}</Fragment>
      ))}
    </>
  )
}
