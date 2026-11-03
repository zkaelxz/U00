// api/schemas/title_restructure.py, from the generated schema. Ids, names and counts only; never a
// path or stored filename.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type TitleRestructureInfo = Schemas['TitleRestructureInfo']
export type TitleRestructureKind = 'merge' | 'move'

// Hand-written: the generated request marks move_duplicates required, but the client may leave it out.
export type TitleRestructureRequest = Schemas['TitleRestructureRequest']

export type TitleRestructurePreview = Schemas['TitleRestructurePreview']
export type TitleRestructureResult = Schemas['TitleRestructureResult']
