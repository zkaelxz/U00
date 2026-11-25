// The Reader's "Save to phone": the translated text as one .epub, to read with no signal in any book app.
import { buttonClass } from '../../components/uiClasses'
import { bookUrl } from './bookUrl'

export function SaveBookCard({ id, canRead }: { id: number; canRead: boolean }) {
  if (!canRead) return null
  return (
    <section className="card" aria-label="Save for offline">
      <p><strong>Save to phone</strong></p>
      <p>Keep the English text as an e-book (.epub) to read with no signal.</p>
      <div className="actions">
        <a className={buttonClass()} href={bookUrl(id)} download>Save the e-book</a>
      </div>
    </section>
  )
}
