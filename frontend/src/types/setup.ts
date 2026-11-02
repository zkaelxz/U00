// GET /api/setup/readiness (api/schemas/setup.py), from the generated schema. Booleans only.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type SetupReadiness = Schemas['SetupReadiness']
