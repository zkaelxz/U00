/*
 * Toggle: an on/off switch for a boolean setting (use it instead of a checkbox).
 *
 *   <Field label="Use the GPU" help="...">
 *     <Toggle checked={on} onChange={setOn} />
 *   </Field>
 *
 * A <button role="switch" aria-checked>: Space and Enter flip it (native button
 * activation), it is focusable, and a <label htmlFor> (Field renders one) names
 * it and flips it on click. Standalone, pass aria-label instead.
 * Extra props (id, aria-describedby, aria-label...) go to the button, so Field
 * can wire its id, help and error to it. Phones get a 44px hit area via CSS.
 */
import type { ButtonHTMLAttributes } from 'react'

type ToggleProps = Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'onChange' | 'type' | 'role'> & {
  checked: boolean
  onChange: (next: boolean) => void
}

export function Toggle({ checked, onChange, disabled, className, ...rest }: ToggleProps) {
  return (
    <button
      {...rest}
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      className={className ? `toggle ${className}` : 'toggle'}
      onClick={() => onChange(!checked)}
    >
      <span className="toggle-thumb" aria-hidden="true" />
    </button>
  )
}
