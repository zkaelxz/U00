// The English e-book export of a title, saved by the Reader's Save to phone card.
import { apiUrl } from '../../api/client'

export const bookUrl = (id: number) => apiUrl(`/api/export/dramas/${id}/epub?field=en`)
