// "Try with a stronger engine" in Review (api/stronger_engine_schemas.py), from the generated schema.
// Engine names, reasons and costs only; never a key, URL or path.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type StrongerEngineSuggestions = Schemas['StrongerEngineSuggestions']
export type StrongerLineResult = Schemas['StrongerLineResult']
