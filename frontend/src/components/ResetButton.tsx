import { buttonClass } from './uiClasses'

interface Props {
  offered: boolean
  onReset: () => void
  onUndo: () => void
  size?: 'sm'
}

// "Reset to defaults" that turns into "Reset. Undo" for a few seconds.
export function ResetButton({ offered, onReset, onUndo, size }: Props) {
  if (!offered) {
    return <button type="button" className={buttonClass('ghost', size)} onClick={onReset}>Reset to defaults</button>
  }
  return (
    <span role="status">
      Reset. <button type="button" className={buttonClass('ghost', size)} onClick={onUndo}>Undo</button>
    </span>
  )
}
