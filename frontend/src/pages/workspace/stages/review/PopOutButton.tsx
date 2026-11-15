import { buttonClass } from '../../../../components/uiClasses'

interface PopOutButtonProps {
  supported: boolean
  floating: boolean
  boxed: boolean
  onPop: () => void
  onReturn: () => void
  onToggleBox: () => void
}

// One button for both kinds of floating: the always-on-top window where the browser has one, the in-page box otherwise.
export function PopOutButton({ supported, floating, boxed, onPop, onReturn, onToggleBox }: PopOutButtonProps) {
  return (
    <button
      type="button"
      className={buttonClass('ghost', 'sm')}
      onClick={supported ? (floating ? onReturn : onPop) : onToggleBox}
      title={supported ? 'Floating player' : 'Floating player (drag to move)'}
    >
      {floating || boxed ? 'Return player' : 'Pop out'}
    </button>
  )
}
