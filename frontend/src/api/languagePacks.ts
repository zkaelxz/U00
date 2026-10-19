import type {
  LanguagePack,
  LanguagePackChoice,
  LanguagePackDefaultResult,
  LanguagePackSummary,
  TitleLanguagePacks,
} from '../types/languagePacks'
import { getJson, postJson, type Fetch } from './client'

// api/routers/language_pack_routes.py
export const getTitleLanguagePacks = (dramaId: number, f?: Fetch) =>
  getJson<TitleLanguagePacks>(`/api/language-packs/dramas/${dramaId}`, f)
export const setTitleLanguagePacks = (dramaId: number, body: LanguagePackChoice, f?: Fetch) =>
  postJson<TitleLanguagePacks>(`/api/language-packs/dramas/${dramaId}`, body, f)
export const getLanguagePack = (packId: string, f?: Fetch) =>
  getJson<LanguagePack>(`/api/language-packs/packs/${encodeURIComponent(packId)}`, f)
export const listLanguagePacks = (f?: Fetch) => getJson<LanguagePackSummary[]>('/api/language-packs', f)
export const getLanguagePackDefault = (language: string, f?: Fetch) =>
  getJson<LanguagePackDefaultResult>(`/api/language-packs/defaults/${encodeURIComponent(language)}`, f)
export const setLanguagePackDefault = (language: string, body: LanguagePackChoice, f?: Fetch) =>
  postJson<LanguagePackDefaultResult>(`/api/language-packs/defaults/${encodeURIComponent(language)}`, body, f)
