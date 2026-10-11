import type {
  LanguagePack,
  LanguagePackChoice,
  LanguagePackDefaultResult,
  TitleLanguagePacks,
} from '../types/languagePacks'
import { getJson, postJson } from './client'

type Fetch = typeof fetch

// api/routers/language_pack_routes.py
export const getTitleLanguagePacks = (dramaId: number, f?: Fetch) =>
  getJson<TitleLanguagePacks>(`/api/language-packs/dramas/${dramaId}`, f)
export const setTitleLanguagePacks = (dramaId: number, body: LanguagePackChoice, f?: Fetch) =>
  postJson<TitleLanguagePacks>(`/api/language-packs/dramas/${dramaId}`, body, f)
export const getLanguagePack = (packId: string, f?: Fetch) =>
  getJson<LanguagePack>(`/api/language-packs/packs/${encodeURIComponent(packId)}`, f)
export const setLanguagePackDefault = (language: string, body: LanguagePackChoice, f?: Fetch) =>
  postJson<LanguagePackDefaultResult>(`/api/language-packs/defaults/${encodeURIComponent(language)}`, body, f)
