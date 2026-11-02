// "Which engine does what" (api/engine_routing_schemas.py), from the generated schema.
// Engine names and short redacted text only; never a key or a URL.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type CapabilityRoute = Schemas['CapabilityRoute']
export type EngineRouteStatus = Schemas['EngineRouteStatus']
export type EngineRouting = Schemas['EngineRouting']
export type EngineStatus = EngineRouteStatus['status']
