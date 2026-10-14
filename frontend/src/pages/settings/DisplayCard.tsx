/*
 * Settings > Display: how wide the page column is and how large the text is, for this browser only.
 */
import { Card } from '../../components/Card'
import { Field } from '../../components/Field'
import { parseTextSize, parseWidth, setDisplay, TEXT_OPTIONS, useDisplay, WIDTH_OPTIONS } from '../../display'

export function DisplayCard() {
  const display = useDisplay()
  return (
    <Card title="Display" meta="This browser only" aria-label="Display">
      <div className="setting-list">
        <Field label="Page width">
          <select value={display.width} onChange={(e) => setDisplay({ width: parseWidth(e.target.value) })}>
            {WIDTH_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </Field>
        <Field label="Text size">
          <select value={display.text} onChange={(e) => setDisplay({ text: parseTextSize(e.target.value) })}>
            {TEXT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </Field>
      </div>
      <p className="muted">Standard keeps lines short; Full window uses the whole width. Text size scales all text and controls, and browser zoom still works on top.</p>
    </Card>
  )
}
