export class ApiError extends Error {}

async function handle<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (body.detail) message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* not JSON */
    }
    throw new ApiError(message)
  }
  return response.json() as Promise<T>
}

export const api = {
  get: <T>(path: string, params?: Record<string, string | number | boolean>) => {
    const query = params ? '?' + new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])) : ''
    return fetch(`/api${path}${query}`).then(r => handle<T>(r))
  },
  post: <T>(path: string, body?: unknown) =>
    fetch(`/api${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    }).then(r => handle<T>(r)),
  put: <T>(path: string, body?: unknown) =>
    fetch(`/api${path}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(r => handle<T>(r)),
}

export interface Stage {
  available: boolean
  complete: boolean
  text: string
}

export type StageKey = 'data_target' | 'reduction' | 'inspection' | 'alignment' | 'photometry' | 'fitting'

export interface JobTask {
  key: string
  label: string
  current: number
  total: number
  message: string
  eta: string | null
  done: boolean
}

export interface Job {
  id: string
  kind: string
  status: 'running' | 'done' | 'error' | 'cancelled'
  message: string
  error: string | null
  result: any
  tasks: JobTask[]
  preview_version: number
  preview_overlays: Overlay[]
  preview_name: string | null
  live_version: number
  question: { id: string; text: string; options: string[] } | null
  stopping: boolean
  elapsed: number
}

export interface AppState {
  version: string
  latest_version: string
  new_version: string
  version_message: string
  directory: string
  directory_short: string
  directory_ok: boolean
  target_name: string
  target_ra_dec: string
  location: string
  filter: string
  stages: Record<StageKey, Stage>
  job: Job | null
}

export type Overlay =
  | { type: 'circle'; x: number; y: number; r: number; color: string; dash?: boolean; width?: number }
  | { type: 'rect'; x: number; y: number; w: number; h: number; color: string; dash?: boolean }
  | { type: 'text'; x: number; y: number; text: string; color: string; size?: number }
  | { type: 'arrow'; x: number; y: number; dx: number; dy: number; color: string }

export interface ImageInfo {
  width: number
  height: number
  factor: number
  x_offset?: number
  prebinned?: boolean
  y_offset?: number
  orig_width: number
  orig_height: number
  mean: number
  std: number
  min: number
  max: number
  name: string | null
  low_std: number
  upper_std: number
  size_mb?: number
  [key: string]: unknown
}

export interface FitsImage {
  info: ImageInfo
  data: Float32Array
}

export async function fetchImage(path: string, params?: Record<string, string | number | boolean>): Promise<FitsImage> {
  const query = params ? '?' + new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])) : ''
  const response = await fetch(`/api${path}${query}`)
  if (!response.ok) {
    let message = `${response.status}`
    try {
      message = (await response.json()).detail
    } catch {
      /* ignore */
    }
    throw new ApiError(message)
  }
  const info = JSON.parse(response.headers.get('X-Image-Info') || '{}') as ImageInfo
  const buffer = await response.arrayBuffer()
  return { info, data: new Float32Array(buffer) }
}
