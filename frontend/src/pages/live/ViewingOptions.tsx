/* The viewing options of a watched session in one place: the lines' text size and original, the captions over the picture, the picture itself and its delay. */
import { Field } from '../../components/Field'
import { Section } from '../../components/Section'
import { Toggle } from '../../components/Toggle'
import { buttonClass } from '../../components/uiClasses'
import { DELAY_RANGE } from './embedLogic'

export type TextSize = 's' | 'm' | 'l' | 'xl'
const SIZES: { value: TextSize; label: string; name: string }[] = [
  { value: 's', label: 'S', name: 'Small' },
  { value: 'm', label: 'M', name: 'Medium' },
  { value: 'l', label: 'L', name: 'Large' },
  { value: 'xl', label: 'XL', name: 'XL' },
]

type Props = {
  textSize: TextSize
  onTextSize: (s: TextSize) => void
  showOriginal: boolean
  onShowOriginal: (v: boolean) => void
  /** Read each new English line aloud with the browser's voice; null where the browser has none. */
  speak: { on: boolean; onChange: (v: boolean) => void } | null
  captionsOn: boolean
  onCaptions: (v: boolean) => void
  showVideo: boolean
  onShowVideo: (v: boolean) => void
  /** Keep the picture off on mobile data until asked for; the lines still arrive as text. */
  dataSaver: boolean
  onDataSaver: (v: boolean) => void
  /** Whether a picture can be shown at all (the video options mean nothing without one). */
  hasPicture: boolean
  /** The delay slider: set when the picture can be held back, with its current state. */
  delay: { seconds: number; auto: boolean; manual: number; onSlide: (s: number) => void; onAuto: (v: boolean) => void } | null
  onFloat: (() => void) | null
  /** Full-screen layout for a TV or other big screen. */
  onTv: () => void
}

export function ViewingOptions({ textSize, onTextSize, showOriginal, onShowOriginal, speak, captionsOn, onCaptions, showVideo, onShowVideo, dataSaver, onDataSaver, hasPicture, delay, onFloat, onTv }: Props) {
  return (
    <Section storageKey="live.viewing" title="Viewing options" defaultOpen>
      <Field label="Text size" help="How large the lines are in the list. For a TV across the room, use TV mode. It does not change the captions drawn over the picture.">
        <div className="live-sizes" role="radiogroup" aria-label="Text size">
          {SIZES.map((s) => (
            <button key={s.value} type="button" role="radio" aria-checked={textSize === s.value} aria-label={s.name}
              className={buttonClass(textSize === s.value ? 'primary' : 'secondary')} onClick={() => onTextSize(s.value)}>
              {s.label}
            </button>
          ))}
        </div>
      </Field>
      <Field label="TV mode" help="Full screen for a TV or big screen: large white lines on black, the picture beside or above. Leave with Esc, the remote's Back or Exit.">
        <button type="button" className={buttonClass('secondary')} data-testid="live-tv-enter" onClick={onTv}>Enter</button>
      </Field>
      <Field label="Show original" help="Keeps the spoken-language line under each translation. Turn it off to read the translation alone.">
        <Toggle checked={showOriginal} onChange={onShowOriginal} />
      </Field>
      {speak && (
        <Field label="Speak the English" help="Reads each new line aloud with your browser's own voice, so you can follow without looking. When lines come faster than it can read, it skips ahead to the newest.">
          <Toggle checked={speak.on} onChange={speak.onChange} data-testid="live-speak" />
        </Field>
      )}
      {hasPicture && (
        <>
          <Field label="Captions over video" help="Draws the current line over the picture, the translation once it is ready and the transcript until then.">
            <Toggle checked={captionsOn} onChange={onCaptions} />
          </Field>
          {onFloat && <button type="button" className={buttonClass('secondary', 'sm')} onClick={onFloat}>Float the picture</button>}
          <Field label="Show video" help="Plays the stream next to the lines. It stops when you stop the session or turn this off.">
            <Toggle checked={showVideo} onChange={onShowVideo} />
          </Field>
          <Field label="Save data" help="On mobile data (where your browser reports it) the picture stays off until you turn Show video on; the lines and captions still arrive as text.">
            <Toggle checked={dataSaver} onChange={onDataSaver} />
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
