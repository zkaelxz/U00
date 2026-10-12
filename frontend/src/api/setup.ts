// What is installed and configured, as booleans (api/routers/setup_routes.py; library.read).
import type { SetupReadiness } from '../types/setup'
import { getJson } from './client'

type Fetch = typeof fetch

export const getReadiness = (f?: Fetch) => getJson<SetupReadiness>('/api/setup/readiness', f)
