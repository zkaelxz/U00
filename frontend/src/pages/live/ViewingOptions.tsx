/* The viewing options of a watched session in one place: the lines' text size and original, the captions over the picture, the picture itself and its delay. */
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import { DELAY_RANGE } from './embedLogic'

export type TextSize = 's' | 'm' | 'l'
const SIZES: { value: TextSize; label: string; name: string }[] = [
  { value: 's', label: 'S', name: 'Small' },
  { value: 'm', label: 'M', name: 'Medium' },
  { value: 'l', label: 'L', name: 'Large' },
]

type Props = {
  textSize: TextSize
  onTextSize: (s: TextSize) => void
  showOriginal: boolean
  onShowOriginal: (v: boolean) => void
  captionsOn: boolean
  onCaptions: (v: boolean) => void
  showVideo: boolean
  onShowVideo: (v: boolean) => void
  /** Whether a picture can be shown at all (the video options mean nothing without one). */
  hasPicture: boolean
  /** The delay slider: set when the picture can be held back, with its current state. */
  delay: { seconds: number; auto: boolean; manual: number; onSlide: (s: number) => void; onAuto: (v: boolean) => void } | null
  onFloat: (() => void) | null
}

export function ViewingOptions({ textSize, onTextSize, showOriginal, onShowOriginal, captionsOn, onCaptions, showVideo, onShowVideo, hasPicture, delay, onFloat }: Props) {
  return (
    <Section storageKey="live.viewing" title="Viewing options" defaultOpen>
      <Field label="Text size" help="How large the lines are in the list. It does not change the captions drawn over the picture.">
        <div className="live-sizes" role="radiogroup" aria-label="Text size">
          {SIZES.map((s) => (
            <button key={s.value} type="button" role="radio" aria-checked={textSize === s.value} aria-label={s.name}
              className={buttonClass(textSize === s.value ? 'primary' : 'secondary')} onClick={() => onTextSize(s.value)}>
              {s.label}
            </button>
          ))}
        </div>
      </Field>
      <Field label="Show original" help="Keeps the spoken-language line under each translation. Turn it off to read the translation alone.">
        <Toggle checked={showOriginal} onChange={onShowOriginal} />
      </Field>
      {hasPicture && (
        <>
          <Field label="Captions over video" help="Draws the current line over the picture, the translation once it is ready and the transcript until then.">
            <Toggle checked={captionsOn} onChange={onCaptions} />
          </Field>
          {onFloat && <button type="button" className={buttonClass('secondary', 'sm')} onClick={onFloat}>Float the picture</button>}
          <Field label="Show video" help="Plays the stream next to the lines. It stops when you stop the session or turn this off.">
            <Toggle checked={showVideo} onChange={onShowVideo} />
          </Field>
          {delay && (
            <>
              <Field label="Video delay" unit="s" help="The translation arrives several seconds after the speech. The picture plays this far behind live so they line up. Moving the slider sets it by hand.">
                <input type="range" min={DELAY_RANGE[0]} max={DELAY_RANGE[1]} step={1} value={delay.seconds} aria-valuetext={`${delay.seconds} s`}
                  onChange={(e) => delay.onSlide(e.target.valueAsNumber)} />
              </Field>
              <Field label="Automatic video delay" help="Sets the delay from the chunk length and how long the last chunks took to caption.">
                <Toggle checked={delay.auto} onChange={delay.onAuto} />
              </Field>
              <p className="muted" data-testid="live-delay-mode">
                {delay.auto ? `Video delay: auto (about ${delay.seconds} s)` : `Video delay: ${delay.manual} s, set by hand`}
              </p>
            </>
          )}
        </>
      )}
    </Section>
  )
}
