const METRIKA_COUNTER_ID = 107240734

type YmFunction = (
  counterId: number,
  method: string,
  ...args: any[]
) => void

declare global {
  interface Window {
    ym?: YmFunction
  }
}

const getYm = (): YmFunction | null => {
  if (typeof window === 'undefined') return null
  if (typeof window.ym !== 'function') return null
  return window.ym
}

const normalizePathToUrl = (pathOrUrl: string): string => {
  const value = String(pathOrUrl || '').trim()
  if (!value) return typeof window !== 'undefined' ? window.location.href : '/'
  if (/^https?:\/\//i.test(value)) return value
  if (typeof window === 'undefined') return value.startsWith('/') ? value : `/${value}`
  const path = value.startsWith('/') ? value : `/${value}`
  return `${window.location.origin}${path}`
}

export const metrikaHit = (
  pathOrUrl: string,
  options?: {
    title?: string
    referer?: string
    params?: Record<string, unknown>
  },
) => {
  const ym = getYm()
  if (!ym) return false
  try {
    const payload: Record<string, unknown> = {}
    if (options?.title) payload.title = options.title
    if (options?.referer) payload.referer = options.referer
    if (options?.params && Object.keys(options.params).length) payload.params = options.params

    const target = normalizePathToUrl(pathOrUrl)
    if (Object.keys(payload).length) ym(METRIKA_COUNTER_ID, 'hit', target, payload)
    else ym(METRIKA_COUNTER_ID, 'hit', target)
    return true
  } catch {
    return false
  }
}

export const metrikaReachGoal = (goalId: string, params?: Record<string, unknown>) => {
  const ym = getYm()
  if (!ym) return false
  const target = String(goalId || '').trim()
  if (!target) return false
  try {
    if (params && Object.keys(params).length) ym(METRIKA_COUNTER_ID, 'reachGoal', target, params)
    else ym(METRIKA_COUNTER_ID, 'reachGoal', target)
    return true
  } catch {
    return false
  }
}

