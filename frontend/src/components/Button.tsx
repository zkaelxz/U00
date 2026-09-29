/*
 * Button styles. Plain <button>s take the classes directly (uiClasses.ts):
 *   <button className={buttonClass('primary')}>Translate</button>
 * ButtonLink is an <a> that looks like a button, for navigation
 * ("Open workspace", "Read") so it keeps link semantics (new tab, copy link).
 *
 * Variants: primary (one per card), secondary (default), ghost (quiet, no
 * border), danger (destructive). Size "sm" for dense rows.
 */
import type { AnchorHTMLAttributes } from 'react'
import { buttonClass, type ButtonSize, type ButtonVariant } from './uiClasses'

type ButtonLinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & {
  href: string
  variant?: ButtonVariant
  size?: ButtonSize
}

export function ButtonLink({ variant = 'secondary', size = 'md', className, children, ...rest }: ButtonLinkProps) {
  return (
    <a {...rest} className={buttonClass(variant, size, className)}>
      {children}
    </a>
  )
}
