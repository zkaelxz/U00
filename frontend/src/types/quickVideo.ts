// /api/quick-video (api/schemas/quick_video.py), from the generated schema.
import type { components } from '../api/generated/schema'

type Schemas = components['schemas']

export type QuickVideoProfile = Schemas['QuickVideoProfile']
export type QuickVideoSettings = Schemas['QuickVideoSettings']
export type QuickVideoStarted = Schemas['QuickVideoStarted']
export type QuickVideoRun = Schemas['QuickVideoRun']
export type QuickVideoStep = QuickVideoRun['step']
