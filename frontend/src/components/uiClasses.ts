// Class-name helpers for the design kit (kept out of the component files so
// React fast refresh stays happy). See Button.tsx and Badge.tsx.
import type { BadgeTone } from './labels'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'
export type ButtonSize = 'md' | 'sm'

// buttonClass('primary') -> "btn btn-primary"; buttonClass('ghost', 'sm') -> "btn btn-ghost btn-sm"
export function buttonClass(variant: ButtonVariant = 'secondary', size: ButtonSize = 'md', extra?: string): string {
  return ['btn', `btn-${variant}`, size === 'sm' ? 'btn-sm' : '', extra ?? ''].filter(Boolean).join(' ')
}

export function badgeClass(tone: BadgeTone = 'neutral'): string {
  return `pill pill-${tone}`
}
