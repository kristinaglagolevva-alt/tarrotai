// api.ts
// Единая точка общения с бекендом (FastAPI)

const DEFAULT_API_BASE = 'https://api.tarrotai.ru'

const API_BASE =
  ((import.meta as any).env?.VITE_API_BASE as string | undefined)?.trim().replace(/\/$/, '') ||
  DEFAULT_API_BASE

type ApiRequestInit = RequestInit & { timeoutMs?: number }

async function readTextSafe(r: Response) {
  try {
    return await r.text()
  } catch {
    return ''
  }
}

async function apiJson<T>(url: string, init?: ApiRequestInit): Promise<T> {
  const timeoutMs = Math.max(0, Number(init?.timeoutMs || 0))
  const reqInit: RequestInit = { ...(init || {}) }
  delete (reqInit as any).timeoutMs

  let timer: ReturnType<typeof setTimeout> | null = null
  let controller: AbortController | null = null
  if (timeoutMs > 0) {
    controller = new AbortController()
    reqInit.signal = controller.signal
    timer = setTimeout(() => controller?.abort(), timeoutMs)
  }

  try {
    const r = await fetch(url, reqInit)
    if (!r.ok) {
      const txt = await readTextSafe(r)
      throw new Error(`${r.status} ${r.statusText}${txt ? `: ${txt}` : ''}`)
    }
    return (await r.json()) as T
  } catch (e: any) {
    if (String(e?.name || '') === 'AbortError' && timeoutMs > 0) {
      throw new Error(`Network timeout after ${timeoutMs}ms`)
    }
    throw e
  } finally {
    if (timer) clearTimeout(timer)
  }
}

// =============================== AUTH / ME ===============================

type AuthOut = { access_token: string; token_type?: string }

export type MeDto = {
  id: number
  telegram_id: number
  username?: string | null
  first_name?: string | null
  last_name?: string | null
  photo_url?: string | null
  paid_readings_balance?: number
  subscription_until?: string | null
  has_active_subscription?: boolean
  memory_opt_in?: boolean
  retention_nudges_opt_in?: boolean
  retention_nudge_hour_local?: number | null
  retention_nudge_tz?: string | null
}

export type MePreferencesInDto = {
  memory_opt_in: boolean
  retention_nudges_opt_in: boolean
  retention_nudge_hour_local?: number | null
  retention_nudge_tz?: string | null
}

export type MePreferencesOutDto = {
  memory_opt_in: boolean
  retention_nudges_opt_in: boolean
  retention_nudge_hour_local?: number | null
  retention_nudge_tz?: string | null
}

export type MemorySummaryDto = {
  recurring_topics: Array<{ topic?: string; count?: number; last_seen?: string | null }>
  repeated_cards: Array<{ card?: string; count?: number; last_seen?: string | null }>
  cycle_hints: string[]
  recurring_question_signals?: Array<{ signal?: string; label?: string; count?: number; last_seen?: string | null }>
  recurring_capsules?: Array<{ capsule?: string; count?: number; last_seen?: string | null }>
  home_hint?: string
  last_changes: Record<string, any>
}

export type SupportTicketDto = {
  ticket_id: string
  status: 'open' | 'pending_support' | 'pending_user' | 'closed' | string
  created_at: string
  updated_at: string
  last_user_message_at?: string | null
  last_support_reply_at?: string | null
  closed_at?: string | null
  messages_count: number
}

export type SupportTicketListDto = {
  items: SupportTicketDto[]
}

export async function getMe(token: string): Promise<MeDto> {
  return apiJson(`${API_BASE}/me`, {
    method: 'GET',
    headers: { Authorization: `Bearer ${token}` },
    timeoutMs: 12000,
  })
}

export async function updateMePreferences(
  token: string,
  payload: MePreferencesInDto
): Promise<MePreferencesOutDto> {
  return apiJson(`${API_BASE}/me/preferences`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(payload),
  })
}

export async function getMemorySummary(token: string): Promise<MemorySummaryDto> {
  return apiJson(`${API_BASE}/memory/summary`, {
    method: 'GET',
    headers: { Authorization: `Bearer ${token}` },
  })
}

export async function getSupportTicketsMe(token: string, limit = 20): Promise<SupportTicketListDto> {
  const q = new URLSearchParams({ limit: String(limit) }).toString()
  return apiJson(`${API_BASE}/support/tickets/me?${q}`, {
    method: 'GET',
    headers: { Authorization: `Bearer ${token}` },
  })
}

export type BillingStatusDto = {
  free_limit: number
  month_used: number
  free_left: number
  paid_readings_balance: number
  subscription_until?: string | null
  has_active_subscription?: boolean
  can_create_reading?: boolean
}

export type BillingPlanPublicDto = {
  code: string
  title: string
  days: number
  amount: number
  currency: string
}

export type BillingPlansOutDto = {
  sbp: BillingPlanPublicDto[]
  click: BillingPlanPublicDto[]
}

export async function getBillingStatus(token: string): Promise<BillingStatusDto> {
  return apiJson(`${API_BASE}/billing/status`, {
    method: 'GET',
    headers: { Authorization: `Bearer ${token}` },
    timeoutMs: 10000,
  })
}

export async function getBillingPlans(): Promise<BillingPlansOutDto> {
  return apiJson(`${API_BASE}/billing/plans`, {
    method: 'GET',
    timeoutMs: 10000,
  })
}

export type SbpPlanCode = 'sub_week' | 'sub_2weeks' | 'sub_month' | 'sub_year'
export type ClickPlanCode = 'sub_week' | 'sub_2weeks' | 'sub_month' | 'sub_year'

export type SbpCreateOutDto = {
  order_id: string
  plan_code: SbpPlanCode | string
  amount: number
  currency: string
  payment_id: string
  status: string
  confirmation_url: string
}

export type SbpStatusOutDto = {
  order_id: string
  plan_code: SbpPlanCode | string
  status: string
  amount: number
  currency: string
  paid_at?: string | null
  has_active_subscription?: boolean
  subscription_until?: string | null
  message?: string
}

export type ClickCreateOutDto = {
  order_id: string
  plan_code: ClickPlanCode | string
  amount: number
  currency: string
  status: string
  payment_url: string
}

export type ClickStatusOutDto = {
  order_id: string
  plan_code: ClickPlanCode | string
  status: string
  amount: number
  currency: string
  paid_at?: string | null
  has_active_subscription?: boolean
  subscription_until?: string | null
  message?: string
}

export async function createSbpPayment(token: string, planCode: SbpPlanCode): Promise<SbpCreateOutDto> {
  return apiJson(`${API_BASE}/billing/sbp/create`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ plan_code: planCode }),
  })
}

export async function getSbpPaymentStatus(token: string, orderId: string): Promise<SbpStatusOutDto> {
  const q = new URLSearchParams({ order_id: String(orderId || '') }).toString()
  return apiJson(`${API_BASE}/billing/sbp/status?${q}`, {
    method: 'GET',
    headers: { Authorization: `Bearer ${token}` },
  })
}

export async function createClickPayment(token: string, planCode: ClickPlanCode): Promise<ClickCreateOutDto> {
  return apiJson(`${API_BASE}/billing/click/create`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify({ plan_code: planCode }),
  })
}

export async function getClickPaymentStatus(token: string, orderId: string): Promise<ClickStatusOutDto> {
  const q = new URLSearchParams({ order_id: String(orderId || '') }).toString()
  return apiJson(`${API_BASE}/billing/click/status?${q}`, {
    method: 'GET',
    headers: { Authorization: `Bearer ${token}` },
  })
}

/**
 * Telegram auth.
 * Можно вызывать без аргументов: возьмём initData из window.Telegram.WebApp.
 * Можно передать initData вручную: telegramAuth(initData).
 *
 * ВАЖНО: бек обычно возвращает {access_token}, а фронту удобнее {token, user}
 */
export async function telegramAuth(initData?: string): Promise<{ token: string; user: any }> {
  const tg = (window as any)?.Telegram?.WebApp
  const init_data = (initData ?? tg?.initData ?? '').trim()

  if (!init_data) {
    throw new Error('Telegram initData is empty (open the app inside Telegram WebApp).')
  }

  const auth = await apiJson<AuthOut>(`${API_BASE}/auth/telegram`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ init_data }),
    timeoutMs: 12000,
  })

  const token = (auth?.access_token || '').trim()
  if (!token) throw new Error('Auth response has no access_token')

  const user = await getMe(token)
  return { token, user }
}

export type BotBootstrapOutDto = {
  status: 'sent' | 'already_sent' | 'not_configured' | 'forbidden' | 'error'
  message?: string
}

export async function bootstrapBotChat(
  token: string,
  options?: { force?: boolean }
): Promise<BotBootstrapOutDto> {
  const force = Boolean(options?.force)
  const query = force ? '?force=1' : ''
  return apiJson(`${API_BASE}/bot/bootstrap-chat${query}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    timeoutMs: 12000,
  })
}

// =============================== CARD OF DAY ===============================

export type CardOfDayDto = {
  day_key: string
  topic: string
  question: string
  card_index: number
  card_name: string
  is_reversed?: boolean
  is_new?: boolean
  description: string
}

export type CardOfDayCreateParams = {
  question: string
  topic: string
  deck_size: number
  consider_reversed?: boolean
  force_llm?: boolean
}

export type CardOfDayHistoryItem = CardOfDayDto & {
  created_at: string
}

export async function getCardOfDayToday(token: string): Promise<CardOfDayDto> {
  return apiJson(`${API_BASE}/card-of-day/today`, {
    method: 'GET',
    headers: { Authorization: `Bearer ${token}` },
  })
}

export async function createCardOfDay(token: string, params: CardOfDayCreateParams): Promise<CardOfDayDto> {
  return apiJson(`${API_BASE}/card-of-day`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(params),
    timeoutMs: 16000,
  })
}

export async function getCardOfDayHistory(token: string): Promise<CardOfDayHistoryItem[]> {
  return apiJson(`${API_BASE}/card-of-day/history`, {
    method: 'GET',
    headers: { Authorization: `Bearer ${token}` },
  })
}

// =============================== READINGS / SPREADS ===============================

// держим совместимость и с твоими названиями, и с возможными короткими на бэке
export type SpreadType = 'past_present_future' | 'three_cards' | 'decision' | 'ppf' | 'custom' | 'photo_analysis'

export type ReadingCardDto = {
  position: string
  title: string
  card_index: number
  card_name: string
  is_reversed: boolean
  meaning: string
}

export type ReadingOutDto = {
  id: number
  spread_type: SpreadType | string
  topic: string
  question: string
  cards: ReadingCardDto[]
  description: string
  created_at: string
}

export type ReadingCreateParams = {
  spread_type: SpreadType
  topic: string
  question: string
  consider_reversed?: boolean
  deck_size?: number
  forced_cards?: Array<{
    card_index: number
    is_reversed?: boolean
  }>

  // decision
  option_a?: string
  option_b?: string

  // custom
  positions?: string[]
  position_titles?: string[]
  extra_context?: string

  force_llm?: boolean
}

export async function createReading(token: string, params: ReadingCreateParams): Promise<ReadingOutDto> {
  return apiJson(`${API_BASE}/reading`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(params),
  })
}

// =============================== UNIFIED HISTORY ===============================

export type UnifiedHistoryCardOfDayPayload = {
  day_key: string
  topic: string
  question: string
  card_index: number
  card_name: string
  is_reversed?: boolean
  description: string
  theme_capsule?: string
}

export type UnifiedHistoryReadingPayload = {
  id: number
  spread_type: SpreadType | string
  topic: string
  question: string
  cards: ReadingCardDto[]
  description: string
  theme_capsule?: string
}

export type UnifiedHistoryItem =
  | { kind: 'card_of_day'; created_at: string; payload: UnifiedHistoryCardOfDayPayload }
  | { kind: 'reading'; created_at: string; payload: UnifiedHistoryReadingPayload }

export async function getUnifiedHistory(token: string, limit = 80): Promise<UnifiedHistoryItem[]> {
  const q = new URLSearchParams({ limit: String(limit) }).toString()
  return apiJson(`${API_BASE}/history?${q}`, {
    method: 'GET',
    headers: { Authorization: `Bearer ${token}` },
  })
}

// =============================== PHOTO ANALYSIS ===============================

export type PhotoAnalysisOutDto = {
  description: string
  cards?: Array<{
    position?: string
    title?: string
    card_index?: number
    card_name?: string
    is_reversed?: boolean
    meaning?: string
  }>
  topic?: string
  question?: string
  spread_type?: string
}

export type PhotoFollowupCardDto = {
  position?: string
  title?: string
  card_index?: number | null
  card_name?: string
  is_reversed?: boolean
  meaning?: string
}

export type PhotoFollowupInDto = {
  topic?: string
  main_question?: string
  followup_question: string
  cards?: PhotoFollowupCardDto[]
  base_interpretation?: string
  extra_context?: string
}

export type PhotoFollowupOutDto = {
  description: string
  topic?: string
  question?: string
  spread_type?: string
}

export type PhotoAnalysisParams = {
  topic?: string
  question?: string
  consider_reversed?: boolean
  extra_context?: string
  force_llm?: boolean
  detect_only?: boolean
}

/**
 * Upload a spread photo for LLM analysis.
 * Backend endpoint: POST /photo-analysis (multipart/form-data)
 * We send both "image" and "file" fields for compatibility.
 */
export async function analyzeSpreadPhoto(
  token: string,
  file: File,
  params?: PhotoAnalysisParams
): Promise<PhotoAnalysisOutDto> {
  const form = new FormData()
  form.append('image', file)
  form.append('file', file)

  if (params?.topic) form.append('topic', params.topic)
  if (params?.question != null) form.append('question', params.question)
  if (params?.extra_context) form.append('extra_context', params.extra_context)
  if (params?.consider_reversed != null) form.append('consider_reversed', String(params.consider_reversed))
  if (params?.force_llm != null) form.append('force_llm', String(params.force_llm))
  if (params?.detect_only != null) form.append('detect_only', String(params.detect_only))

  const r = await fetch(`${API_BASE}/photo-analysis`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: form,
  })

  if (!r.ok) {
    const txt = await readTextSafe(r)
    throw new Error(`${r.status} ${r.statusText}${txt ? `: ${txt}` : ''}`)
  }

  const ct = (r.headers.get('content-type') || '').toLowerCase()
  if (ct.includes('application/json')) {
    return (await r.json()) as PhotoAnalysisOutDto
  }

  const text = await readTextSafe(r)
  return { description: text || '' }
}

export async function askPhotoFollowup(
  token: string,
  payload: PhotoFollowupInDto
): Promise<PhotoFollowupOutDto> {
  return apiJson(`${API_BASE}/photo-analysis/followup`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${token}`,
    },
    body: JSON.stringify(payload || {}),
  })
}
