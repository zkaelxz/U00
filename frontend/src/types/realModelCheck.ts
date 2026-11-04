// Mirrors api/schemas/real_model_check.py.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type RealModelCheckStatus = 'pass' | 'fail' | 'skipped' | 'could_not_check'

export type RealModelCheckResult = Schemas['RealModelCheckResult']

export type RealModelCheckState = Schemas['RealModelCheckState']
