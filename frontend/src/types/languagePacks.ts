// /api/language-packs, from the generated schema (api/schemas/language_packs.py).
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type LanguagePackSummary = Schemas['LanguagePackSummary']
export type LanguagePackEntry = Schemas['LanguagePackEntry']
export type LanguagePack = Schemas['LanguagePack']
export type TitleLanguagePack = Schemas['TitleLanguagePack']
export type TitleLanguagePacks = Schemas['TitleLanguagePacks']
/** Pack id -> style id (null for the pack's default). Packs left out are off. */
export type LanguagePackChoice = Schemas['LanguagePackChoice']
export type LanguagePackDefaultResult = Schemas['LanguagePackDefaultResult']
