// What is installed and configured, as booleans (api/routers/setup_routes.py; library.read).
import type { SetupReadiness } from '../types/setup'
import { getJson, type Fetch } from './client'

export const getReadiness = (f?: Fetch) => getJson<SetupReadiness>('/api/setup/readiness', f)
