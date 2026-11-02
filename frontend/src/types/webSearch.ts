// The optional web-search fallback (api/web_search_schemas.py), from the generated schema.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type WebSearchStatus = Schemas['WebSearchStatus']
export type WebSearchConfig = Schemas['WebSearchConfig']
export type WebSearchResults = Schemas['WebSearchResults']
export type WebSearchTestResult = Schemas['WebSearchTestResult']
