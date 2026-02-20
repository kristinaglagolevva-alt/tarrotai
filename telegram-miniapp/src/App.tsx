/* =================================================================================================
   [1] ИМПОРТЫ
================================================================================================= */

import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import {
  telegramAuth,
  getMe,
  getBillingStatus,
  getCardOfDayToday,
  createCardOfDay,
  getUnifiedHistory,
  analyzeSpreadPhoto,
  createReading,
} from './api'



import micIcon from './assets/icons/microphone.svg'
import buttonIcon from './assets/icons/button_icon.png'
import cardDayIcon from './assets/icons/card_day_icon.png'
import cameraIcon from './assets/icons/camera.png'
import futureIcon from './assets/icons/future_icon.png'
import threeCardIcon from './assets/icons/three_card_icon.png'
import selectCardIcon from './assets/icons/select_icon.png'

// ✅ ЗАДНЯЯ СТОРОНА КАРТЫ — ВСЕГДА ТОЛЬКО ЭТА
import backCardImg from './assets/cards/back/back.png'

/* =================================================================================================
   [2] ЗАГРУЗКА ТАРО-РЕСУРСОВ (ФРОНТЫ) + СПИСОК ДЛЯ ПРЕЛОАДА
   FIX: гарантируем стабильный порядок (index -> одна и та же карта всегда)
================================================================================================= */

// ✅ Подхватываем все изображения из assets/cards/** кроме back
const frontCardModules = import.meta.glob('./assets/cards/**/*.{jpg,jpeg,png,webp}', { eager: true }) as Record<
  string,
  { default: string }
>

const FRONT_CARD_ENTRIES = Object.entries(frontCardModules).filter(([path]) => !/\/back\//.test(path))

// --- helpers ---
const norm = (s: string) =>
  s
    .toLowerCase()
    .replace(/\\/g, '/')
    .replace(/%20/g, ' ')
    .replace(/[^a-z0-9/.\s-]/g, '') // убираем странные символы
    .trim()

const basename = (p: string) => norm(p).split('/').pop() || ''

const findBy = (folder: string, name: string) => {
  const f = folder.toLowerCase()
  const n = name.toLowerCase()

  // ищем по: "/folder/" + "name" в конце, но допускаем мусор в начале имени ("50256 ", "-", etc)
  const hit = FRONT_CARD_ENTRIES.find(([path]) => {
    const p = norm(path)
    if (!p.includes(`/cards/${f}/`)) return false

    const b = basename(p)
    // допускаем префиксы: цифры/дефисы/пробелы
    return b.endsWith(n) || b.replace(/^[\d\s-]+/, '').endsWith(n)
  })

  if (!hit) {
    console.warn('[cards] missing image for', folder, name)
    return ''
  }
  return hit[1].default
}

const majors = [
  'the fool.jpg',
  'the magician.jpg',
  'the high priestess.jpg',
  'the empress.jpg',
  'the emperor.jpg',
  'the hierophant.jpg',
  'the lovers.jpg',
  'the chariot.jpg',
  'strength.jpg',
  'the hermit.jpg',
  'wheel of fortune.jpg',
  'justice.jpg',
  'the hanged man.jpg',
  'death.jpg',
  'temperance.jpg',
  'the devil.jpg',
  'the tower.jpg',
  'the star.jpg',
  'the moon.jpg',
  'the sun.jpg',
  'judgement.jpg',
  'the world.jpg',
]

const minorsWands = [
  'ace of wands.jpg',
  'two of wands.jpg',
  'three of wands.jpg',
  'four of wands.jpg',
  'five of wands.jpg',
  'six of wands.jpg',
  'seven of wands.jpg',
  'eight of wands.jpg',
  'nine of wands.jpg',
  'ten of wands.jpg',
  'page of wands.jpg',
  'knight of wands.jpg',
  'queen of wands.jpg',
  'king of wands.jpg',
]

const minorsCups = [
  'ace of cups.jpg',
  'two of cups.jpg',
  'three of cups.jpg',
  'four of cups.jpg',
  'five of cups.jpg',
  'six of cups.jpg',
  'seven of cups.jpg',
  'eight of cups.jpg',
  'nine of cups.jpg',
  'ten of cups.jpg',
  'page of cups.jpg',
  'knight of cups.jpg',
  'queen of cups.jpg',
  'king of cups.jpg', // у тебя "King of cups.jpg" — норм, мы матчим case-insensitive
]

const minorsSwords = [
  'ace of swords.jpg',
  'two of swords.jpg',
  'three of swords.jpg',
  'four of swords.jpg',
  'five of swords.jpg',
  'six of swords.jpg',
  'seven of swords.jpg',
  'eight of swords.jpg',
  'nine of swords.jpg', // у тебя "-Nine of Swords.jpg" — норм, мы срезаем "-"/цифры
  'ten of swords.jpg',
  'page of swords.jpg',
  'knight of swords.jpg',
  'queen of swords.jpg',
  'king of swords.jpg',
]

const minorsPentacles = [
  'ace of pentacles.jpg',
  'two of pentacles.jpg',
  'three of pentacles.jpg',
  'four of pentacles.jpg',
  'five of pentacles.jpg',
  'six of pentacles.jpg',
  'seven of pentacles.jpg',
  'eight of pentacles.jpg',
  'nine of pentacles.jpg',
  'ten of pentacles.jpg',
  'page of pentacles.jpg',
  'knight of pentacles.jpg',
  'queen of pentacles.jpg',
  'king of pentacles.jpg',
]

// ✅ ВОТ ЭТО — ключ: строим массив строго в порядке id 0..77 (как на беке)
const FRONT_CARD_URLS = [
  ...majors.map((n) => findBy('major', n)),

  ...minorsWands.map((n) => findBy('wands', n)),
  ...minorsCups.map((n) => findBy('cups', n)),
  ...minorsSwords.map((n) => findBy('swords', n)),
  ...minorsPentacles.map((n) => findBy('pentacles', n)),
].map((u) => u || '') // гарантируем длину

const CLEAN_FRONT_CARD_URLS = FRONT_CARD_URLS.filter((u): u is string => !!u)

// Для анимации перемешивания достаточно поднабора: меньше декодирований => меньше лагов.
const SHUFFLE_FRONT_URLS = (() => {
  const src = CLEAN_FRONT_CARD_URLS.length ? CLEAN_FRONT_CARD_URLS : [backCardImg]
  if (src.length <= 18) return src
  const step = Math.max(1, Math.floor(src.length / 18))
  const sampled = src.filter((_, i) => i % step === 0).slice(0, 18)
  return sampled.length >= 12 ? sampled : src.slice(0, 18)
})()

/* =================================================================================================
   [3] ТИПЫ И ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
================================================================================================= */

type Star = {
  x: number
  y: number
  r: number
  phase: number
  twSpeed: number
  base: number
  amp: number
}

type Comet = {
  x: number
  y: number
  vx: number
  vy: number
  life: number
  maxLife: number
  tail: number
  width: number
}

function clamp(n: number, min: number, max: number) {
  return Math.max(min, Math.min(max, n))
}

function rand(min: number, max: number) {
  return min + Math.random() * (max - min)
}

function renderMdInline(text: string, keyPrefix: string) {
  const nodes: any[] = []
  const re = /\*\*([^*]+)\*\*/g
  let last = 0
  let idx = 0
  let m: RegExpExecArray | null

  while ((m = re.exec(text)) !== null) {
    if (m.index > last) {
      nodes.push(
        <Fragment key={`${keyPrefix}-t-${idx++}`}>
          {text.slice(last, m.index)}
        </Fragment>,
      )
    }
    nodes.push(
      <strong key={`${keyPrefix}-b-${idx++}`} className="md-strong">
        {m[1]}
      </strong>,
    )
    last = m.index + m[0].length
  }

  if (last < text.length) {
    nodes.push(
      <Fragment key={`${keyPrefix}-t-${idx++}`}>
        {text.slice(last)}
      </Fragment>,
    )
  }

  return nodes.length ? nodes : [text]
}

function MarkdownText({ text, className = '' }: { text?: string; className?: string }) {
  const source = String(text || '').replace(/\r\n?/g, '\n').trim()
  if (!source) return null

  const lines = source.split('\n')
  const blocks: any[] = []
  let listType: 'ul' | 'ol' | null = null
  let listItems: string[] = []
  let key = 0

  const flushList = () => {
    if (!listType || !listItems.length) return
    const Tag = listType
    blocks.push(
      <Tag key={`md-list-${key++}`}>
        {listItems.map((item, i) => (
          <li key={`md-li-${key}-${i}`}>{renderMdInline(item, `md-li-${key}-${i}`)}</li>
        ))}
      </Tag>,
    )
    listType = null
    listItems = []
  }

  for (const raw of lines) {
    const line = raw.trim()
    if (!line) {
      flushList()
      continue
    }

    const h = line.match(/^(#{1,3})\s+(.+)$/)
    if (h) {
      flushList()
      blocks.push(
        <h4 key={`md-h-${key++}`}>{renderMdInline(h[2], `md-h-${key}`)}</h4>,
      )
      continue
    }

    const ul = line.match(/^[-*•]\s+(.+)$/)
    if (ul) {
      if (listType !== 'ul') {
        flushList()
        listType = 'ul'
      }
      listItems.push(ul[1])
      continue
    }

    const ol = line.match(/^\d+[.)]\s+(.+)$/)
    if (ol) {
      if (listType !== 'ol') {
        flushList()
        listType = 'ol'
      }
      listItems.push(ol[1])
      continue
    }

    flushList()
    blocks.push(
      <p key={`md-p-${key++}`}>{renderMdInline(line, `md-p-${key}`)}</p>,
    )
  }

  flushList()
  return <div className={`result-md ${className}`.trim()}>{blocks}</div>
}

/* =================================================================================================
   [4] КОНФИГ UI (ТЕМЫ / РАСКЛАДЫ)
================================================================================================= */

type Topic = 'relations' | 'career' | 'finance'

const TOPICS: { id: Topic; label: string }[] = [
  { id: 'relations', label: 'Отношения' },
  { id: 'career', label: 'Карьера' },
  { id: 'finance', label: 'Финансы' },
]

type SpreadId = 'card_of_day' | 'past_present_future' | 'three_cards' | 'decision'

const SPREADS: {
  id: SpreadId
  title: string
  subtitle: string
  cards: string
  icon: 'sun' | 'clock' | 'cards' | 'branch'
}[] = [
  { id: 'card_of_day', title: 'Карта Дня', subtitle: 'Ежедневное руководство', cards: '1 карта', icon: 'sun' },
  {
    id: 'past_present_future',
    title: 'Прошлое • Настоящее • Будущее',
    subtitle: 'Временная линия событий',
    cards: '3 карт',
    icon: 'clock',
  },
  { id: 'three_cards', title: 'Расклад по 3 картам', subtitle: 'Универсальный расклад', cards: '3 карт', icon: 'cards' },
  { id: 'decision', title: 'Принятие Решения', subtitle: 'Выбор между вариантами', cards: '2 карт', icon: 'branch' },
]

/* =================================================================================================
   [5] ИКОНКИ (INLINE SVG)
================================================================================================= */

function SpreadIcon({ kind }: { kind: 'sun' | 'clock' | 'cards' | 'branch' }) {
  if (kind === 'sun') {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path
          d="M12 18a6 6 0 1 1 0-12 6 6 0 0 1 0 12Zm0-16v2m0 16v2M4 12H2m20 0h-2M5.6 5.6 4.2 4.2m15.6 15.6-1.4-1.4M18.4 5.6l1.4-1.4M4.2 19.8l1.4-1.4"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    )
  }

  if (kind === 'clock') {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z" fill="none" stroke="currentColor" strokeWidth="1.8" />
        <path
          d="M12 6v6l4 2"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    )
  }

  if (kind === 'cards') {
    return (
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path
          d="M7 7h10a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2Z"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinejoin="round"
        />
        <path
          d="M9 3h10a2 2 0 0 1 2 2v11"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
          strokeLinejoin="round"
          opacity="0.75"
        />
      </svg>
    )
  }

  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M6 3a3 3 0 1 0 0 6 3 3 0 0 0 0-6Zm12 12a3 3 0 1 0 0 6 3 3 0 0 0 0-6Zm0-12a3 3 0 1 0 0 6 3 3 0 0 0 0-6Z" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path
        d="M8.5 7.5c4.5 0 4.5 9 9 9M8.5 7.5c4.5 0 4.5 3 9 3"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  )
}

/* =================================================================================================
   [6] PREMIUM FLIP CARD
   Требование: смена лицевой ТОЛЬКО в момент, когда видна рубашка (на половине оборота)
================================================================================================= */

function PremiumFlipCard({
  frontUrls,
  backUrl,
  active,
  durationMs = 2600,
  intensity = 0,
  className = '',
  scale = 1,
  top = '50%',
  clickable = false,
  onClick,
  ariaLabel = 'Карта',
  stopAtBack = false,
  onStoppedAtBack,
  onFrontChange,

  // ✅ фиксируем предвыбранную карту дня
  lockFront = false,
  lockedFrontUrl,
}: {
  frontUrls: string[]
  backUrl: string
  active: boolean
  durationMs?: number
  intensity?: number
  className?: string
  scale?: number
  top?: string
  clickable?: boolean
  onClick?: () => void
  ariaLabel?: string
  stopAtBack?: boolean
  onStoppedAtBack?: () => void
  onFrontChange?: (url: string) => void
  lockFront?: boolean
  lockedFrontUrl?: string
}) {
  const safeFronts = frontUrls.length ? frontUrls : [backUrl]

  const pickNext = (exclude?: string) => {
    if (safeFronts.length === 1) return safeFronts[0]
    let n = safeFronts[Math.floor(Math.random() * safeFronts.length)]
    if (exclude && n === exclude) {
      const idx = safeFronts.indexOf(n)
      n = safeFronts[(idx + 1) % safeFronts.length]
    }
    return n
  }

  const [front, setFront] = useState(() => pickNext())

  const halfTimeoutRef = useRef<number | null>(null)
  const cycleIntervalRef = useRef<number | null>(null)

  const clearTimers = () => {
    if (halfTimeoutRef.current) {
      window.clearTimeout(halfTimeoutRef.current)
      halfTimeoutRef.current = null
    }
    if (cycleIntervalRef.current) {
      window.clearInterval(cycleIntervalRef.current)
      cycleIntervalRef.current = null
    }
  }

  // ✅ отдаём наверх текущую лицевую
  useEffect(() => {
    onFrontChange?.(front)
  }, [front, onFrontChange])

  // ✅ пока active и не lockFront — меняем фронт ТОЛЬКО на половине оборота
  useEffect(() => {
    clearTimers()

    if (!active) return
    if (lockFront) return

    const dur = Math.max(300, durationMs)
    const half = Math.floor(dur * 0.5)

    // 1) первая смена — когда впервые показали рубашку
    halfTimeoutRef.current = window.setTimeout(() => {
      setFront((cur) => pickNext(cur))

      // 2) дальше каждые dur — снова на момент рубашки (половина каждого оборота)
      cycleIntervalRef.current = window.setInterval(() => {
        setFront((cur) => pickNext(cur))
      }, dur)
    }, half)

    return () => clearTimers()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, lockFront, durationMs])

  // ✅ когда нужно — жёстко фиксируем предвыбранный фронт (daily)
  useEffect(() => {
    if (!lockFront) return
    if (!lockedFrontUrl) return
    clearTimers()
    setFront(lockedFrontUrl)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lockFront, lockedFrontUrl])

  // ✅ stopAtBack: ждём половину цикла и зовём onStoppedAtBack
  useEffect(() => {
    if (!stopAtBack) return
    const timeout = window.setTimeout(() => {
      onStoppedAtBack?.()
    }, Math.floor(durationMs * 0.5))
    return () => window.clearTimeout(timeout)
  }, [stopAtBack, durationMs, onStoppedAtBack])

  return (
    <div
      className={`pflip ${className} ${active ? 'is-active' : ''} ${clickable ? 'is-clickable' : ''}`}
      style={{
        ['--dur' as any]: `${durationMs}ms`,
        ['--k' as any]: intensity.toFixed(3),

        ['--pflip-top' as any]: top,
        ['--pflip-s' as any]: scale.toFixed(3),
      }}
      role={clickable ? 'button' : undefined}
      tabIndex={clickable ? 0 : undefined}
      aria-label={clickable ? ariaLabel : undefined}
      aria-hidden={clickable ? undefined : true}
      onClick={clickable ? onClick : undefined}
      onKeyDown={
        clickable
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onClick?.()
              }
            }
          : undefined
      }
    >
      {/* ✅ НОВОЕ: отдельный слой для scale/blur/анимации, чтобы центр не “уезжал” */}
      <div className="pflip__inner">
        <div className="pflip__stage">
          <div className="pflip__card">
            <div className="pflip__face pflip__front" style={{ backgroundImage: `url(${front})` }} />
            <div className="pflip__face pflip__back" style={{ backgroundImage: `url(${backUrl})` }} />
          </div>

          <div className="pflip__glint" />
          <div className="pflip__shadow" />
        </div>
      </div>
    </div>
  )
}

/* =================================================================================================
   [7] ГЛАВНЫЙ КОМПОНЕНТ ПРИЛОЖЕНИЯ
================================================================================================= */

type Stage = 'question' | 'spread'
type View = 'home' | 'card_day_prep' | 'three_cards_prep' | 'past_present_future_prep' | 'decision_prep' | 'photo_analysis'
type BillingStatus = {
  free_limit: number
  month_used: number
  free_left: number
  paid_readings_balance: number
  subscription_until?: string | null
  has_active_subscription?: boolean
  can_create_reading?: boolean
}

const BOT_USERNAME =
  ((import.meta as any).env?.VITE_BOT_USERNAME as string | undefined)?.trim() || 'Tarot_AI_Bot'
const BOT_PAYMENT_URL = `https://t.me/${BOT_USERNAME}?start=menu`

export default function App() {
  /* =============================================================================================
   АВТОРИЗАЦИЯ В ТГ (при запуске мини‑приложения)
   Логика:
     1) если есть jwt в localStorage — пробуем /me
     2) если jwt невалиден / отсутствует — делаем POST /auth/telegram с initData
     3) пока идёт авторизация — показываем лоадер (фон/канвасы остаются)
============================================================================================= */

type AuthStatus = 'loading' | 'ready' | 'error'

const [token, setToken] = useState<string | null>(() => {
  try {
    return localStorage.getItem('jwt')
  } catch {
    return null
  }
})

const [user, setUser] = useState<any>(null)
const [billing, setBilling] = useState<BillingStatus | null>(null)
const [authStatus, setAuthStatus] = useState<AuthStatus>('loading')
const [authError, setAuthError] = useState<string>('')

useEffect(() => {
  let mounted = true

  const safe = (fn: () => void) => {
    if (!mounted) return
    fn()
  }

  const runAuth = async () => {
    safe(() => {
      setAuthStatus('loading')
      setAuthError('')
    })

    const tg = (window as any)?.Telegram?.WebApp
    try {
      tg?.ready?.()
    } catch {}

    // 1) Если jwt есть — проверяем /me
    if (token) {
      try {
        const me = await getMe(token)
        let billingOut: BillingStatus | null = null
        try {
          billingOut = await getBillingStatus(token)
        } catch {}
        safe(() => {
          setUser(me)
          setBilling(billingOut)
          setAuthStatus('ready')
        })
        return
      } catch {
        // jwt невалиден — очищаем и продолжаем телеграм‑авторизацию
        try {
          localStorage.removeItem('jwt')
        } catch {}
        safe(() => {
          setToken(null)
          setUser(null)
          setBilling(null)
        })
      }
    }

    // 2) Телеграм‑авторизация
    try {
      if (!tg?.initData) {
        safe(() => {
          setAuthStatus('error')
          setAuthError('Откройте мини‑приложение внутри Telegram (нет initData).')
        })
        return
      }

      const res = await telegramAuth()
      let billingOut: BillingStatus | null = null
      try {
        billingOut = await getBillingStatus(res.token)
      } catch {}

      try {
        localStorage.setItem('jwt', res.token)
      } catch {}

      safe(() => {
        setToken(res.token)
        setUser(res.user)
        setBilling(billingOut)
        setAuthStatus('ready')
      })
    } catch (e) {
      console.error('Auth error', e)
      try {
        localStorage.removeItem('jwt')
      } catch {}

      safe(() => {
        setToken(null)
        setUser(null)
        setBilling(null)
        setAuthStatus('error')
        setAuthError('Не удалось авторизоваться. Перезапустите мини‑приложение в Telegram.')
      })
    }
  }

  runAuth()

  return () => {
    mounted = false
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, [token])/* =============================================================================================
     [9] БАЗОВОЕ СОСТОЯНИЕ UI
  ============================================================================================= */

  const refreshBilling = async (jwt: string | null | undefined = token) => {
    if (!jwt) {
      setBilling(null)
      return
    }
    try {
      const out = await getBillingStatus(jwt)
      setBilling(out)
    } catch {}
  }

  const readBackendErrorDetail = (raw: string): any => {
    const text = String(raw || '').trim()
    if (!text) return null
    const fromColon = text.indexOf(': {')
    const fromBrace = text.indexOf('{')
    const idx = fromColon >= 0 ? fromColon + 2 : fromBrace
    if (idx < 0) return null
    try {
      return JSON.parse(text.slice(idx))
    } catch {
      return null
    }
  }

  const readingLimitMessage =
    `Бесплатный лимит раскладов за месяц исчерпан.\n\n` +
    `Оплатите пакет/подписку в боте: ${BOT_PAYMENT_URL}`

  const formatRuDate = (value?: string | null) => {
    if (!value) return '—'
    const d = new Date(value)
    if (!Number.isFinite(d.getTime())) return '—'
    return d.toLocaleDateString('ru-RU')
  }

  const mapReadingError = (raw: string) => {
    const msg = String(raw || '').trim()
    const parsed = readBackendErrorDetail(msg)
    const detail = parsed?.detail

    if (detail?.code === 'READING_LIMIT_EXCEEDED' || /READING_LIMIT_EXCEEDED|402/i.test(msg)) {
      return readingLimitMessage
    }
    if (/401|403/i.test(msg)) return 'Сессия устарела. Перезапустите мини-приложение и попробуйте снова.'
    if (/503|service unavailable/i.test(msg)) return 'AI-сервис временно недоступен. Повторите через минуту.'
    if (typeof detail === 'string' && detail.trim()) return detail.trim()
    return msg || 'Не удалось получить ответ от сервера.'
  }

  const [needsMotionPermission, setNeedsMotionPermission] = useState(false)
  const [pressed, setPressed] = useState(false)

  const [question, setQuestion] = useState('')

  /* =============================================================================================
     [10] ЗАПИСЬ ГОЛОСА
  ============================================================================================= */

  const [isRecording, setIsRecording] = useState(false)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<BlobPart[]>([])
  const streamRef = useRef<MediaStream | null>(null)
  const speechRecognitionRef = useRef<any>(null)
  const speechSeedRef = useRef<string>('')
  const speechFinalRef = useRef<string>('')

  const isIOS = useMemo(() => {
    const ua = navigator.userAgent || ''
    return /iPad|iPhone|iPod/.test(ua)
  }, [])

  /* =============================================================================================
     [11] ВЫБОР ТЕМЫ (SEG)
  ============================================================================================= */

  const [topic, setTopic] = useState<Topic>('relations')
  const prevTopicRef = useRef<Topic>('relations')
  const [prevTopic, setPrevTopic] = useState<Topic>('relations')

  const [isBumping, setIsBumping] = useState(false)
  const bumpTRef = useRef<number | null>(null)
  const [bump, setBump] = useState(0)

  const indices = useMemo(() => {
    const map = new Map<Topic, number>()
    TOPICS.forEach((t, i) => map.set(t.id, i))
    return map
  }, [])
  const activeIndex = indices.get(topic) ?? 0
  const prevIndex = indices.get(prevTopic) ?? 0

  const onPickTopic = (next: Topic) => {
    if (next === topic) return

    setPrevTopic(prevTopicRef.current)
    prevTopicRef.current = next
    setTopic(next)

    setBump((n) => n + 1)

    setIsBumping(false)
    if (bumpTRef.current) window.clearTimeout(bumpTRef.current)
    requestAnimationFrame(() => {
      setIsBumping(true)
      bumpTRef.current = window.setTimeout(() => setIsBumping(false), 440)
    })
  }

  /* =============================================================================================
     [12] ВЫБОР РАСКЛАДА + ИКОНКИ
  ============================================================================================= */

  const [spread, setSpread] = useState<SpreadId | null>(null)

  const SPREAD_ICON_IMAGES: Partial<Record<SpreadId, string>> = {
    card_of_day: cardDayIcon,
    past_present_future: futureIcon,
    three_cards: threeCardIcon,
    decision: selectCardIcon,
  }

  const SPREAD_THEME_CLASSES: Record<SpreadId, string> = {
    card_of_day: 'spread-card--sun',
    past_present_future: 'spread-card--violet',
    three_cards: 'spread-card--indigo',
    decision: 'spread-card--azure',
  }

  /* =============================================================================================
     [13] CTA / ВНИМАНИЕ (ОШИБКА + ПОДСВЕТКА СЕКЦИЙ)
  ============================================================================================= */

  const [ctaError, setCtaError] = useState(false)
  const ctaErrTRef = useRef<number | null>(null)

  const [attnStage, setAttnStage] = useState<Stage | null>(null)
  const [attnNonce, setAttnNonce] = useState(0)
  const attnTRef = useRef<number | null>(null)

  /* =============================================================================================
     [14] ПРЕЛОАД ИЗОБРАЖЕНИЙ
  ============================================================================================= */

  useEffect(() => {
    const urls = CLEAN_FRONT_CARD_URLS
    const imgs: HTMLImageElement[] = []
    let idleId: number | null = null
    let timeoutId: number | null = null
    let cancelled = false

    const warmup = () => {
      const pack = [...urls, backCardImg]
      for (const u of pack) {
        if (!u) continue
        const im = new Image()
        im.decoding = 'async'
        im.src = u
        try {
          ;(im as any).decode?.().catch(() => {})
        } catch {}
        imgs.push(im)
      }
    }

    const ric = (window as any).requestIdleCallback as
      | ((cb: (deadline?: any) => void, opts?: { timeout: number }) => number)
      | undefined
    if (ric) {
      idleId = ric(() => {
        if (!cancelled) warmup()
      }, { timeout: 1200 })
    } else {
      timeoutId = window.setTimeout(() => {
        if (!cancelled) warmup()
      }, 120)
    }

    return () => {
      cancelled = true
      if (idleId != null) {
        try {
          ;(window as any).cancelIdleCallback?.(idleId)
        } catch {}
      }
      if (timeoutId != null) {
        window.clearTimeout(timeoutId)
      }
      imgs.length = 0
    }
  }, [])

  /* =============================================================================================
     [15] CTA / ATTENTION ЛОГИКА
  ============================================================================================= */

  const pulseCtaRed = () => {
    setCtaError(false)
    if (ctaErrTRef.current) window.clearTimeout(ctaErrTRef.current)
    requestAnimationFrame(() => {
      setCtaError(true)
      ctaErrTRef.current = window.setTimeout(() => setCtaError(false), 520)
    })
  }

  const scrollToStage = (stage: Stage) => {
    const el = stage === 'question' ? askWrapRef.current : spreadListRef.current
    if (!el) return
    el.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }

  const flashStageBorder = (stage: Stage) => {
    if (attnTRef.current) window.clearTimeout(attnTRef.current)
    setAttnStage(null)

    requestAnimationFrame(() => {
      setAttnNonce((n) => n + 1)
      setAttnStage(stage)
      scrollToStage(stage)

      attnTRef.current = window.setTimeout(() => {
        setAttnStage((cur) => (cur === stage ? null : cur))
      }, 1700)
    })
  }

  const onBeginReading = () => {
    const qOk = question.trim().length > 0
    const spreadOk = !!spread

    if (!qOk || !spreadOk) {
      pulseCtaRed()
      if (!qOk) return flashStageBorder('question')
      return flashStageBorder('spread')
    }

    // роутинг по выбранному раскладу
    if (spread === 'card_of_day') {
      openCardDay()
      return
    }

    if (spread === 'three_cards') {
      openThreeCards()
      return
    }

    if (spread === 'past_present_future') {
      openPastPresentFuture()
      return
    }


    if (spread === 'decision') {
      openDecision()
      return
    }

    // остальные расклады пока не реализованы
    try {
      hapticPulse(0.22)
    } catch {}
    flashStageBorder('spread')
  }


  useEffect(() => {
    return () => {
      if (ctaErrTRef.current) window.clearTimeout(ctaErrTRef.current)
      if (attnTRef.current) window.clearTimeout(attnTRef.current)
      if (bumpTRef.current) window.clearTimeout(bumpTRef.current)
      if (navBumpTRef.current) window.clearTimeout(navBumpTRef.current)
    }
  }, [])

  /* =============================================================================================
     [16] ФОН: CANVAS (ЗВЁЗДЫ + КОМЕТЫ + ПАРАЛЛАКС)
  ============================================================================================= */

  useEffect(() => {
    const appEl = appRef.current
    const starsCanvas = starsCanvasRef.current
    const cometsCanvas = cometsCanvasRef.current
    if (!appEl || !starsCanvas || !cometsCanvas) return

    const sctx = starsCanvas.getContext('2d')
    const cctx = cometsCanvas.getContext('2d')
    if (!sctx || !cctx) return

    const coarsePointer = window.matchMedia('(pointer: coarse)').matches
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const lowPowerDevice = coarsePointer || (navigator.hardwareConcurrency || 4) <= 4
    const quality = reducedMotion ? 0.55 : lowPowerDevice ? 0.72 : 1
    const minFrameMs = lowPowerDevice ? 1000 / 42 : 1000 / 58

    let width = window.innerWidth
    let height = window.innerHeight
    const dpr = Math.min(window.devicePixelRatio || 1, lowPowerDevice ? 1.5 : 2)

    const resize = () => {
      width = window.innerWidth
      height = window.innerHeight

      for (const cv of [starsCanvas, cometsCanvas]) {
        cv.width = Math.floor(width * dpr)
        cv.height = Math.floor(height * dpr)
        cv.style.width = `${width}px`
        cv.style.height = `${height}px`
      }

      sctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      cctx.setTransform(dpr, 0, 0, dpr, 0, 0)
      cctx.clearRect(0, 0, width, height)
    }

    resize()
    window.addEventListener('resize', resize)

    let targetPX = 0
    let targetPY = 0
    let pX = 0
    let pY = 0

    const PARALLAX = {
      bgX: 7.5 * quality,
      bgY: 6.5 * quality,
      starsX: 32 * quality,
      starsY: 28 * quality,
      cometsX: 22 * quality,
      cometsY: 20 * quality,
      follow: lowPowerDevice ? 0.075 : 0.095,
    }

    const onMove = (e: MouseEvent) => {
      const x = (e.clientX / width - 0.5) * 2
      const y = (e.clientY / height - 0.5) * 2
      targetPX = x
      targetPY = y
    }
    window.addEventListener('mousemove', onMove, { passive: true })

    let gx = 0
    let gy = 0
    let tgtGX = 0
    let tgtGY = 0

    const onMotion = (e: DeviceMotionEvent) => {
      const ax = e.accelerationIncludingGravity?.x ?? 0
      const ay = e.accelerationIncludingGravity?.y ?? 0
      tgtGX = clamp(ax / 9.8, -1, 1)
      tgtGY = clamp(ay / 9.8, -1, 1)
    }
    const useGyroParallax = !lowPowerDevice && !reducedMotion
    if (useGyroParallax) {
      window.addEventListener('devicemotion', onMotion, { passive: true })
    }

    const starsCount = clamp(
      Math.floor((width * height) / (9000 / quality)),
      lowPowerDevice ? 42 : 70,
      lowPowerDevice ? 140 : 220
    )
    const stars: Star[] = new Array(starsCount).fill(0).map(() => ({
      x: Math.random() * width,
      y: Math.random() * height,
      r: rand(0.55, 1.65),
      phase: rand(0, Math.PI * 2),
      twSpeed: rand(0.002, 0.008),
      base: rand(0.25, 0.7),
      amp: rand(0.05, 0.35),
    }))

    const comets: Comet[] = []

    const spawnComet = () => {
      const edge = Math.random()
      let x = 0
      let y = 0

      if (edge < 0.5) {
        x = rand(-width * 0.15, width * 0.25)
        y = rand(-height * 0.25, 0)
      } else {
        x = rand(-width * 0.25, 0)
        y = rand(-height * 0.15, height * 0.25)
      }

      const speed = rand(lowPowerDevice ? 440 : 520, lowPowerDevice ? 760 : 920)
      const ang = rand(0.8, 1.05)
      const vx = Math.cos(ang) * speed
      const vy = Math.sin(ang) * speed

      comets.push({
        x,
        y,
        vx,
        vy,
        life: 0,
        maxLife: rand(lowPowerDevice ? 0.55 : 0.7, lowPowerDevice ? 1.05 : 1.25),
        tail: rand(lowPowerDevice ? 68 : 90, lowPowerDevice ? 112 : 140),
        width: rand(lowPowerDevice ? 1.1 : 1.3, lowPowerDevice ? 2.1 : 2.6),
      })
    }

    let last = performance.now()
    let cometTimer = 0
    const cometInterval = lowPowerDevice ? 2.05 : 1.35
    const cometChance = lowPowerDevice ? 0.4 : 0.75
    let rafId = 0

    const draw = (now: number) => {
      const frameGap = now - last
      if (frameGap < minFrameMs) {
        rafId = requestAnimationFrame(draw)
        return
      }

      const dt = clamp(frameGap / 1000, 0, 0.05)
      last = now

      if (document.hidden) {
        rafId = requestAnimationFrame(draw)
        return
      }

      pX += (targetPX - pX) * PARALLAX.follow
      pY += (targetPY - pY) * PARALLAX.follow

      gx += (tgtGX - gx) * 0.06
      gy += (tgtGY - gy) * 0.06

      const mixX = pX * 0.65 + gx * 0.35
      const mixY = pY * 0.65 + gy * 0.35

      appEl.style.setProperty('--bgx', `${50 + mixX * PARALLAX.bgX}%`)
      appEl.style.setProperty('--bgy', `${50 + mixY * PARALLAX.bgY}%`)

      const ox = mixX * PARALLAX.starsX
      const oy = mixY * PARALLAX.starsY

      sctx.clearRect(0, 0, width, height)
      for (const st of stars) {
        st.phase += dt * st.twSpeed
        const tw = st.base + Math.sin(st.phase) * st.amp
        const alpha = clamp(tw, 0, 1)

        const x = st.x + ox * (st.r * 0.7)
        const y = st.y + oy * (st.r * 0.7)

        sctx.beginPath()
        sctx.fillStyle = `rgba(255,255,255,${alpha})`
        sctx.arc(x, y, st.r, 0, Math.PI * 2)
        sctx.fill()
      }

      const cox = mixX * PARALLAX.cometsX
      const coy = mixY * PARALLAX.cometsY

      cctx.clearRect(0, 0, width, height)

      cometTimer += dt
      if (cometTimer > cometInterval) {
        cometTimer = 0
        if (Math.random() < cometChance) spawnComet()
      }

      for (let i = comets.length - 1; i >= 0; i--) {
        const c = comets[i]
        c.life += dt
        c.x += c.vx * dt
        c.y += c.vy * dt

        const t = c.life / c.maxLife
        const fade = 1 - clamp(t, 0, 1)

        const tx = -c.vx
        const ty = -c.vy
        const len = Math.hypot(tx, ty) || 1
        const nx = (tx / len) * c.tail
        const ny = (ty / len) * c.tail

        const x1 = c.x + cox
        const y1 = c.y + coy

        const grad = cctx.createLinearGradient(x1, y1, x1 + nx, y1 + ny)
        grad.addColorStop(0, `rgba(180, 160, 255, ${0.72 * fade})`)
        grad.addColorStop(1, `rgba(180, 160, 255, 0)`)

        cctx.strokeStyle = grad
        cctx.lineWidth = c.width
        cctx.lineCap = 'round'
        cctx.beginPath()
        cctx.moveTo(x1, y1)
        cctx.lineTo(x1 + nx, y1 + ny)
        cctx.stroke()

        cctx.fillStyle = `rgba(220, 210, 255, ${0.9 * fade})`
        cctx.beginPath()
        cctx.arc(x1, y1, c.width * 1.15, 0, Math.PI * 2)
        cctx.fill()

        if (c.life >= c.maxLife || x1 > width + 220 || y1 > height + 220) {
          comets.splice(i, 1)
        }
      }

      rafId = requestAnimationFrame(draw)
    }

    rafId = requestAnimationFrame(draw)

    return () => {
      cancelAnimationFrame(rafId)
      window.removeEventListener('resize', resize)
      window.removeEventListener('mousemove', onMove)
      if (useGyroParallax) {
        window.removeEventListener('devicemotion', onMotion as any)
      }
    }
  }, [])

  /* =============================================================================================
     [17] iOS: РАЗРЕШЕНИЕ НА MOTION
  ============================================================================================= */

  useEffect(() => {
    if (!isIOS) return
    const DME = (window as any).DeviceMotionEvent
    const needs = typeof DME?.requestPermission === 'function'
    setNeedsMotionPermission(needs)
  }, [isIOS])

  const requestMotion = async () => {
    try {
      const DME = (window as any).DeviceMotionEvent
      if (typeof DME?.requestPermission === 'function') {
        const res = await DME.requestPermission()
        setNeedsMotionPermission(res !== 'granted')
      } else {
        setNeedsMotionPermission(false)
      }
    } catch {
      setNeedsMotionPermission(true)
    }
  }

  /* =============================================================================================
     [18] ЗАПИСЬ ГОЛОСА: УПРАВЛЕНИЕ
  ============================================================================================= */

  const stopRecording = async () => {
    try {
      const recognition = speechRecognitionRef.current
      if (recognition) {
        recognition.onresult = null
        recognition.onerror = null
        recognition.onend = null
        recognition.stop?.()
      }
    } catch {
      // ignore
    } finally {
      speechRecognitionRef.current = null
      speechSeedRef.current = ''
      speechFinalRef.current = ''
    }

    try {
      mediaRecorderRef.current?.stop()
      streamRef.current?.getTracks().forEach((t) => t.stop())
    } catch {
      // ignore
    } finally {
      mediaRecorderRef.current = null
      streamRef.current = null
      chunksRef.current = []
      setIsRecording(false)
    }
  }

  const toggleRecording = async () => {
    if (isRecording) {
      await stopRecording()
      return
    }

    const SpeechRecognitionCtor = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
    if (typeof SpeechRecognitionCtor === 'function') {
      try {
        speechSeedRef.current = question.trim()
        speechFinalRef.current = ''

        const recognition = new SpeechRecognitionCtor()
        speechRecognitionRef.current = recognition
        recognition.lang = 'ru-RU'
        recognition.continuous = true
        recognition.interimResults = true
        recognition.maxAlternatives = 1

        recognition.onresult = (event: any) => {
          let interim = ''
          for (let i = event.resultIndex; i < event.results.length; i++) {
            const phrase = String(event.results[i]?.[0]?.transcript || '').trim()
            if (!phrase) continue
            if (event.results[i].isFinal) {
              speechFinalRef.current = [speechFinalRef.current, phrase].filter(Boolean).join(' ').trim()
            } else {
              interim = [interim, phrase].filter(Boolean).join(' ').trim()
            }
          }

          const full = [speechSeedRef.current, speechFinalRef.current, interim].filter(Boolean).join(' ').trim()
          if (full) setQuestion(full)
        }

        recognition.onerror = async () => {
          await stopRecording()
        }

        recognition.onend = () => {
          speechRecognitionRef.current = null
          const full = [speechSeedRef.current, speechFinalRef.current].filter(Boolean).join(' ').trim()
          if (full) setQuestion(full)
          setIsRecording(false)
        }

        recognition.start()
        setIsRecording(true)
        return
      } catch (e) {
        console.error('Speech recognition unavailable, fallback to MediaRecorder', e)
        speechRecognitionRef.current = null
      }
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      streamRef.current = stream

      const recorder = new MediaRecorder(stream)
      mediaRecorderRef.current = recorder
      chunksRef.current = []

      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data)
      }

      recorder.start()
      setIsRecording(true)
    } catch (e) {
      console.error(e)
      setIsRecording(false)
      await stopRecording()
    }
  }

  useEffect(() => {
    return () => {
      void stopRecording()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /* =============================================================================================
     [19] CTA “PRESS” RIPPLE ORIGIN
  ============================================================================================= */

  const onGlassPointerDown = (e: React.PointerEvent<HTMLButtonElement>) => {
    setPressed(true)

    const btn = btnRef.current
    if (!btn) return

    const rect = btn.getBoundingClientRect()
    const x = e.clientX - rect.left
    const y = e.clientY - rect.top

    btn.style.setProperty('--rx', `${x}px`)
    btn.style.setProperty('--ry', `${y}px`)
  }

  const onGlassPointerUp = () => setPressed(false)

  const shouldAttnSpreads = attnStage === 'spread' && !spread

  /* =============================================================================================
     [20] “РОУТИНГ” + КАРТА ДНЯ + SHAKE (DAILY предвыбор)
  ============================================================================================= */

  const [view, setView] = useState<View>('home')

  /* =============================================================================================
     [20.1] NAV (HOME): Главная / История / Профиль — слайд влево/вправо
  ============================================================================================= */

  type NavTab = 'main' | 'history' | 'profile'

  const [navTab, setNavTab] = useState<NavTab>('main')
  const prevNavTabRef = useRef<NavTab>('main')
  const [navPrev, setNavPrev] = useState<NavTab>('main')

  const [navIsBumping, setNavIsBumping] = useState(false)
  const navBumpTRef = useRef<number | null>(null)
  const [navBump, setNavBump] = useState(0)

  const NAV_INDEX = useMemo(() => {
    const map = new Map<NavTab, number>()
    ;(['main', 'history', 'profile'] as NavTab[]).forEach((t, i) => map.set(t, i))
    return map
  }, [])

  const navActiveIndex = NAV_INDEX.get(navTab) ?? 0
  const navPrevIndex = NAV_INDEX.get(navPrev) ?? 0

  // направление для data-dir (можно использовать в CSS для лёгких эффектов)
  const navDir: 'left' | 'right' | 'none' = navActiveIndex === navPrevIndex ? 'none' : navActiveIndex > navPrevIndex ? 'right' : 'left'

  const onPickNav = (next: NavTab) => {
    if (next === navTab) return

    setNavPrev(prevNavTabRef.current)
    prevNavTabRef.current = next
    setNavTab(next)

    setNavBump((n) => n + 1)

    setNavIsBumping(false)
    if (navBumpTRef.current) window.clearTimeout(navBumpTRef.current)
    requestAnimationFrame(() => {
      setNavIsBumping(true)
      navBumpTRef.current = window.setTimeout(() => setNavIsBumping(false), 440)
    })
  }
  useEffect(() => {
    let mounted = true

    const load = async () => {
      if (navTab !== 'history') return

      if (!token) {
        setHistory([])
        setHistoryError('')
        setHistoryLoading(false)
        return
      }

      setHistoryLoading(true)
      setHistoryError('')
      try {
        const items: any[] = await getUnifiedHistory(token, 120)
        if (!mounted) return

        const mapped: HistoryListItem[] = (items || [])
          .map((item: any) => {
            if (item?.kind === 'card_of_day') {
              const p = item?.payload || {}
              return {
                kind: 'card_of_day' as const,
                day_key: String(p.day_key || ''),
                topic: String(p.topic || ''),
                question: String(p.question || ''),
                card_index: Number(p.card_index ?? 0),
                card_name: String(p.card_name || ''),
                description: String(p.description || ''),
                created_at: String(item?.created_at || ''),
              }
            }

            if (item?.kind === 'reading') {
              const p = item?.payload || {}
              const cards = Array.isArray(p.cards) ? p.cards : []
              const first = cards[0] || {}
              const fallbackLabel = SPREAD_HISTORY_LABELS[String(p.spread_type || '')] || 'Расклад'
              return {
                kind: 'reading' as const,
                created_at: String(item?.created_at || ''),
                topic: String(p.topic || ''),
                question: String(p.question || ''),
                spread_type: String(p.spread_type || 'reading'),
                description: String(p.description || ''),
                cards_count: cards.length || 0,
                card_index: Number(first.card_index ?? 0),
                card_name: String(first.card_name || fallbackLabel),
              }
            }

            return null
          })
          .filter((x): x is HistoryListItem => !!x)

        const sorted = [...mapped].sort((a, b) => {
          const aDayKey = a.kind === 'card_of_day' ? a.day_key : ''
          const bDayKey = b.kind === 'card_of_day' ? b.day_key : ''
          const ta = Date.parse(a.created_at || aDayKey || '')
          const tb = Date.parse(b.created_at || bDayKey || '')
          return (isFinite(tb) ? tb : 0) - (isFinite(ta) ? ta : 0)
        })

        setHistory(sorted)
      } catch (e: any) {
        if (!mounted) return
        setHistoryError(e?.message ? String(e.message) : 'Не удалось загрузить историю')
      } finally {
        if (!mounted) return
        setHistoryLoading(false)
      }
    }

    load()

    return () => {
      mounted = false
    }
  }, [navTab, token])

  /* =============================================================================================
    [20.2] РАСКЛАД "3 КАРТЫ": 3 ЭКРАНА (SETUP → SHUFFLE → RESULT)
  ============================================================================================= */

  type ThreeScreen = 'setup' | 'shuffle' | 'result'
  type ThreeQuestionKind = 'open' | 'yesno' | 'advice'

  const THREE_QKINDS: { id: ThreeQuestionKind; label: string;}[] = [
    { id: 'open', label: 'Отношения'},
    { id: 'yesno', label: 'Карьера'},
    { id: 'advice', label: 'Финансы'},
  ]

  type ThreeCardPos = { x: number; y: number; r: number; s: number; z: number }
  type ThreeCardResult = { idx: number; url: string; name: string; role: string; text: string; isReversed?: boolean }

  // фиксированные 3 “слота” как на первом экране (места карт)
  const THREE_SLOTS: ThreeCardPos[] = [
    { x: -44, y: 6,  r: -7, s: 1.0,  z: 1 }, // левый
    { x: 0,   y: -8, r: 3,  s: 1.03, z: 2 }, // центр (чуть выше/больше)
    { x: 44,  y: 2,  r: 8,  s: 1.0,  z: 1 }, // правый
  ]

  // все перестановки (слот -> какая карта стоит в слоте)
  const THREE_PERMS: number[][] = [
    [0, 1, 2],
    [0, 2, 1],
    [1, 0, 2],
    [1, 2, 0],
    [2, 0, 1],
    [2, 1, 0],
  ]

  const [threeScreen, setThreeScreen] = useState<ThreeScreen>('setup')
  const [threeQuestion, setThreeQuestion] = useState('')
  const [threeKind, setThreeKind] = useState<ThreeQuestionKind>('open')

  // bump-анимация для свитчера типа вопроса
  const [threePrevKind, setThreePrevKind] = useState<ThreeQuestionKind>('open')
  const prevThreeKindRef = useRef<ThreeQuestionKind>('open')
  const [threeKindIsBumping, setThreeKindIsBumping] = useState(false)
  const threeKindBumpTRef = useRef<number | null>(null)
  const [threeKindBump, setThreeKindBump] = useState(0)

  const threeKindIndices = useMemo(() => {
    const m = new Map<ThreeQuestionKind, number>()
    THREE_QKINDS.forEach((k, i) => m.set(k.id, i))
    return m
  }, [])

  const threeKindActiveIndex = threeKindIndices.get(threeKind) ?? 0
  const threeKindPrevIndex = threeKindIndices.get(threePrevKind) ?? 0

  const onPickThreeKind = (next: ThreeQuestionKind) => {
    if (next === threeKind) return

    setThreePrevKind(prevThreeKindRef.current)
    prevThreeKindRef.current = next
    setThreeKind(next)

    setThreeKindBump((n) => n + 1)

    setThreeKindIsBumping(false)
    if (threeKindBumpTRef.current) window.clearTimeout(threeKindBumpTRef.current)
    requestAnimationFrame(() => {
      setThreeKindIsBumping(true)
      threeKindBumpTRef.current = window.setTimeout(() => setThreeKindIsBumping(false), 440)
    })
  }

  // карты результата (пока mock)
  const [threeCards, setThreeCards] = useState<ThreeCardResult[]>([])
  const [threeDayKey, setThreeDayKey] = useState<string>('') // дата расклада (как в карте дня)
  const [threeShowMeaning, setThreeShowMeaning] = useState(false)


  // ✅ ВАЖНО: порядок = какая карта (0/1/2) стоит в каком слоте (лев/центр/прав)
  const [threeOrder, setThreeOrder] = useState<number[]>([0, 1, 2])

  const [threeShakeEnabled, setThreeShakeEnabled] = useState(false)
  const [threeShuffleProgress, setThreeShuffleProgress] = useState(0)
  const [threeReadyToOpen, setThreeReadyToOpen] = useState(false)

  // refs для шейка (не мешаем “карте дня”)
  const threeLastAccelRef = useRef<{ x: number; y: number; z: number } | null>(null)
  const threeShakeCooldownRef = useRef(0)
  const threeLastPulseRef = useRef(0)

  // чтобы авто-тасовка не меняла порядок слишком часто (иначе не видно анимации)
  const threeLastSwapAtRef = useRef(0)

  // чтобы “финиш” (возврат к 123 и readyToOpen) сработал ровно 1 раз
  const threeFinishingRef = useRef(false)
  const threeMeaningTimerRef = useRef<number | null>(null)
  const threeRequestSeqRef = useRef(0)

  const THREE_SHAKE_THRESHOLD = 8.8
  const THREE_SHAKE_STEP_BASE = 0.085

  useEffect(() => {
    return () => {
      if (threeMeaningTimerRef.current) {
        window.clearTimeout(threeMeaningTimerRef.current)
        threeMeaningTimerRef.current = null
      }
    }
  }, [])

  /* =============================================================================================
    [20.3] РАСКЛАД "ПРОШЛОЕ • НАСТОЯЩЕЕ • БУДУЩЕЕ": 3 ЭКРАНА (SETUP → SHUFFLE → RESULT)
    Логика 1:1 как three_cards, но свитчер: Прошлое/Настоящее/Будущее
  ============================================================================================= */

  type PpfScreen = 'setup' | 'shuffle' | 'result'
  type PpfFocus = 'past' | 'present' | 'future'

  const PPF_FOCUS: { id: PpfFocus; label: string }[] = [
    { id: 'past', label: 'Прошлое' },
    { id: 'present', label: 'Настоящее' },
    { id: 'future', label: 'Будущее' },
  ]

  type PpfCardResult = { idx: number; url: string; name: string; role: string; text: string; isReversed?: boolean }

  const [ppfScreen, setPpfScreen] = useState<PpfScreen>('setup')
  const [ppfQuestion, setPpfQuestion] = useState('')
  const [ppfFocus, setPpfFocus] = useState<PpfFocus>('past')

  // bump-анимация для свитчера
  const [ppfPrevFocus, setPpfPrevFocus] = useState<PpfFocus>('past')
  const prevPpfFocusRef = useRef<PpfFocus>('past')
  const [ppfFocusIsBumping, setPpfFocusIsBumping] = useState(false)
  const ppfFocusBumpTRef = useRef<number | null>(null)
  const [ppfFocusBump, setPpfFocusBump] = useState(0)

  const ppfFocusIndices = useMemo(() => {
    const m = new Map<PpfFocus, number>()
    PPF_FOCUS.forEach((k, i) => m.set(k.id, i))
    return m
  }, [])

  const ppfFocusActiveIndex = ppfFocusIndices.get(ppfFocus) ?? 0
  const ppfFocusPrevIndex = ppfFocusIndices.get(ppfPrevFocus) ?? 0

  const onPickPpfFocus = (next: PpfFocus) => {
    if (next === ppfFocus) return

    setPpfPrevFocus(prevPpfFocusRef.current)
    prevPpfFocusRef.current = next
    setPpfFocus(next)

    setPpfFocusBump((n) => n + 1)

    setPpfFocusIsBumping(false)
    if (ppfFocusBumpTRef.current) window.clearTimeout(ppfFocusBumpTRef.current)
    requestAnimationFrame(() => {
      setPpfFocusIsBumping(true)
      ppfFocusBumpTRef.current = window.setTimeout(() => setPpfFocusIsBumping(false), 440)
    })
  }

  // карты результата (пока mock)
  const [ppfCards, setPpfCards] = useState<PpfCardResult[]>([])
  const [ppfDayKey, setPpfDayKey] = useState<string>('')

  // порядок = какая карта (0/1/2) стоит в каком слоте (лев/центр/прав)
  const [ppfOrder, setPpfOrder] = useState<number[]>([0, 1, 2])

  const [ppfShakeEnabled, setPpfShakeEnabled] = useState(false)
  const [ppfShuffleProgress, setPpfShuffleProgress] = useState(0)
  const [ppfReadyToOpen, setPpfReadyToOpen] = useState(false)

  // refs для шейка
  const ppfLastAccelRef = useRef<{ x: number; y: number; z: number } | null>(null)
  const ppfShakeCooldownRef = useRef(0)
  const ppfLastPulseRef = useRef(0)

  // чтобы авто-тасовка не меняла порядок слишком часто
  const ppfLastSwapAtRef = useRef(0)

  // чтобы “финиш” (возврат к 123 и readyToOpen) сработал ровно 1 раз
  const ppfFinishingRef = useRef(false)

  const PPF_SHAKE_THRESHOLD = 8.8
  const PPF_SHAKE_STEP_BASE = 0.085


  // ---------------------------------------------------------------------------------------------
  // history (backend) — реальные карты из БД
  // ---------------------------------------------------------------------------------------------

  type CardHistoryItem = {
    day_key: string
    topic: string
    question: string
    card_index: number
    card_name: string
    description: string
    created_at: string
  }

  type ReadingHistoryItem = {
    kind: 'reading'
    created_at: string
    topic: string
    question: string
    spread_type: string
    description: string
    cards_count: number
    card_index: number
    card_name: string
  }

  type HistoryListItem =
    | ({ kind: 'card_of_day' } & CardHistoryItem)
    | ReadingHistoryItem

  const SPREAD_HISTORY_LABELS: Record<string, string> = {
    three_cards: 'Расклад по 3 картам',
    ppf: 'Прошлое • Настоящее • Будущее',
    decision: 'Принятие решения',
    custom: 'Пользовательский расклад',
    photo_analysis: 'Анализ фото',
  }

  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyError, setHistoryError] = useState('')
  const [history, setHistory] = useState<HistoryListItem[]>([])

  const [shakeEnabled, setShakeEnabled] = useState(false)
  const [shakenOnce, setShakenOnce] = useState(false)
  const [cardRevealed, setCardRevealed] = useState(false)

  const [selectedFrontUrl, setSelectedFrontUrl] = useState<string>('')

  // ✅ NEW: “карта дня” — фиксированная на сутки для пользователя
  const [dailyFrontUrl, setDailyFrontUrl] = useState<string>('')
  // ✅ NEW: данные "карты дня" с бекенда
  const [dailyDesc, setDailyDesc] = useState<string>('')
  const [dailyCardName, setDailyCardName] = useState<string>('')
  const [dailyDayKey, setDailyDayKey] = useState<string>('')

  // Подогреваем конкретную карту дня заранее, чтобы reveal открывался без подгрузочного “фриза”.
  useEffect(() => {
    if (!dailyFrontUrl) return
    const im = new Image()
    im.decoding = 'async'
    im.src = dailyFrontUrl
    try {
      ;(im as any).decode?.().catch(() => {})
    } catch {}
  }, [dailyFrontUrl])

  // =================================================================================================
  // [ADDED] STATES FOR READINGS DESCRIPTIONS AND LOADING INDICATORS
  // These states hold the description returned from the backend for each reading type and track loading status.
  const [threeDesc, setThreeDesc] = useState<string>('')
  const [ppfDesc, setPpfDesc] = useState<string>('')
  const [decisionDesc, setDecisionDesc] = useState<string>('')
  const [threeLoading, setThreeLoading] = useState(false)
  const [ppfLoading, setPpfLoading] = useState(false)
  const [decisionLoading, setDecisionLoading] = useState(false)

  // ✅ NEW: чтобы повторно не дергать бекенд лишний раз при возврате
  const [cardDayLoading, setCardDayLoading] = useState(false)
  // ✅ NEW: форсим ремоунт PremiumFlipCard при каждом открытии экрана
  const [pflipMountKey, setPflipMountKey] = useState(0)

  const subtitleRef = useRef<HTMLParagraphElement | null>(null)

  const appRef = useRef<HTMLDivElement | null>(null)
  const starsCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const cometsCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const askWrapRef = useRef<HTMLDivElement | null>(null)
  const spreadListRef = useRef<HTMLDivElement | null>(null)
  const btnRef = useRef<HTMLButtonElement | null>(null)
  const spreadActiveRef = useRef<HTMLDivElement | null>(null)

  const isResult = view === 'card_day_prep' && cardRevealed
  const [stopRequested, setStopRequested] = useState(false)

  // ✅ прогресс перемешивания: 0..1
  const [shuffleProgress, setShuffleProgress] = useState(0)

  // shake detection
  const lastAccelRef = useRef<{ x: number; y: number; z: number } | null>(null)
  const lastPulseRef = useRef(0)
  const shakeCooldownRef = useRef(0)

  const HAPTIC_MIN_INTERVAL = 55 // ms
  const SHAKE_THRESHOLD = 7.6
  const SHAKE_STEP_BASE = 0.06
  const SHAKE_STEP_POWER = 0.08

  // ---------------------------------------------------------------------------------------------
  // ✅ helpers: стабильный daily random (userId + YYYY-MM-DD) -> индекс
  // ---------------------------------------------------------------------------------------------

  const getTelegramUserId = () => {
    try {
      const tg = (window as any)?.Telegram?.WebApp
      const id = tg?.initDataUnsafe?.user?.id
      if (id != null) return String(id)
    } catch {}
    return 'anon'
  }

  const getVilniusDayKey = () => {
    // YYYY-MM-DD в таймзоне Europe/Vilnius
    try {
      const s = new Date().toLocaleDateString('en-CA', { timeZone: 'Europe/Vilnius' })
      // en-CA обычно даёт YYYY-MM-DD
      return s
    } catch {
      // fallback
      const d = new Date()
      const yyyy = d.getFullYear()
      const mm = String(d.getMonth() + 1).padStart(2, '0')
      const dd = String(d.getDate()).padStart(2, '0')
      return `${yyyy}-${mm}-${dd}`
    }
  }

  // простенький хеш строки -> uint32
  const hash32 = (str: string) => {
    let h = 2166136261
    for (let i = 0; i < str.length; i++) {
      h ^= str.charCodeAt(i)
      h = Math.imul(h, 16777619)
    }
    return h >>> 0
  }

  const pickDailyCardUrl = () => {
    const userId = getTelegramUserId()
    const dayKey = getVilniusDayKey()

    const storageKey = `ai-tarot:card-day:${userId}:${dayKey}`

    try {
      const cached = localStorage.getItem(storageKey)
      if (cached && FRONT_CARD_URLS.includes(cached)) return cached
    } catch {}

    const seed = hash32(`${userId}|${dayKey}`)
    const idx = FRONT_CARD_URLS.length ? seed % FRONT_CARD_URLS.length : 0
    const url = FRONT_CARD_URLS[idx] || backCardImg

    try {
      localStorage.setItem(storageKey, url)
    } catch {}

    return url
  }

  // ---------------------------------------------------------------------------------------------
  // navigation
  // ---------------------------------------------------------------------------------------------

  const openCardDay = async () => {
    // ✅ форсим ремоунт карты (иначе мог сохраниться фронт с прошлого захода)
    setPflipMountKey((k) => k + 1)

    // сбрасываем UI
    setSelectedFrontUrl('')
    setShakenOnce(false)
    setShakeEnabled(false)
    setShuffleProgress(0)
    setCardRevealed(false)
    setStopRequested(false)

    setDailyDesc('')
    setDailyCardName('')
    setDailyDayKey('')

    // ✅ важно: включаем loading ДО перехода на экран, чтобы не было "вспышки" формы
    setCardDayLoading(!!token)
    setView('card_day_prep')

    // ✅ если токена нет — обычный сценарий (ввод/шейк)
    if (!token) {
      const dailyLocal = pickDailyCardUrl()
      setDailyFrontUrl(dailyLocal)
      return
    }

    try {
      const dto = await getCardOfDayToday(token)

      const idx = Math.max(0, Math.min(dto.card_index ?? 0, FRONT_CARD_URLS.length - 1))
      const url = FRONT_CARD_URLS[idx] || backCardImg

      setDailyFrontUrl(url)
      setSelectedFrontUrl(url)

      setDailyDesc(dto.description || '')
      setDailyCardName(dto.card_name || '')
      setDailyDayKey(dto.day_key || '')

      // ✅ сразу показываем результат
      setShakenOnce(true)
      setShuffleProgress(1)
      setCardRevealed(true)
      setShakeEnabled(false)
      setStopRequested(false)
    } catch (e) {
      // 404 = карты нет -> обычный сценарий
      const dailyLocal = pickDailyCardUrl()
      setDailyFrontUrl(dailyLocal)
    } finally {
      setCardDayLoading(false)
    }
  }

  const openCardDayFromHistory = (it: CardHistoryItem) => {
    const idx = clamp(it.card_index ?? 0, 0, 77)
    const img = FRONT_CARD_URLS[idx] || backCardImg

    // Переключаем “роут”
    setView('card_day_prep')

    // Чтобы flip-карта гарантированно показала нужную карту и не пыталась шейкаться
    setCardDayLoading(false)
    setStopRequested(false)
    setShakeEnabled(false)
    setShuffleProgress(1)
    setShakenOnce(true)
    setCardRevealed(true)

    // Данные результата (то, что выпало тогда)
    setDailyFrontUrl(img)
    setSelectedFrontUrl(img)
    setDailyDesc(it.description || '')
    setDailyCardName(it.card_name || '')
    setDailyDayKey(it.day_key || '')

    // (опционально) восстановим вопрос/тему в state — удобно, если где-то показываешь/используешь
    if (typeof it.question === 'string') setQuestion(it.question)
    if (it.topic) setTopic(it.topic as any)

    // Форсим ремоунт PremiumFlipCard, чтобы точно обновился на нужную картинку
    setPflipMountKey((k) => k + 1)

    // маленький хаптик (если хочешь)
    try {
      hapticPulse(0.25)
    } catch {}
  }


  const backHome = () => setView('home')


  // ---------------------------------------------------------------------------------------------
  // [PHOTO ANALYSIS] — AI анализ фото расклада (галерея/камера -> бекенд -> LLM)
  // ---------------------------------------------------------------------------------------------

  const [photoFile, setPhotoFile] = useState<File | null>(null)
  const [photoPreviewUrl, setPhotoPreviewUrl] = useState<string>('')
  const [photoStatus, setPhotoStatus] = useState<'idle' | 'uploading' | 'done' | 'error'>('idle')
  const [photoError, setPhotoError] = useState<string>('')
  const [photoResult, setPhotoResult] = useState<{ description: string; cards?: any[] } | null>(null)

  const galleryInputRef = useRef<HTMLInputElement | null>(null)
  const cameraInputRef = useRef<HTMLInputElement | null>(null)

  useEffect(() => {
    // cleanup old URL
    return () => {
      try {
        if (photoPreviewUrl) URL.revokeObjectURL(photoPreviewUrl)
      } catch {}
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    // update preview when file changes
    try {
      if (photoPreviewUrl) URL.revokeObjectURL(photoPreviewUrl)
    } catch {}

    if (!photoFile) {
      setPhotoPreviewUrl('')
      return
    }

    const url = URL.createObjectURL(photoFile)
    setPhotoPreviewUrl(url)
    return () => {
      try {
        URL.revokeObjectURL(url)
      } catch {}
    }
  }, [photoFile])

  const openPhotoAnalysis = () => {
    setPhotoFile(null)
    setPhotoResult(null)
    setPhotoStatus('idle')
    setPhotoError('')
    setView('photo_analysis')
  }

  const onPhotoInputChange = (e: any) => {
    const file = (e?.target?.files?.[0] as File | undefined) || null
    // allow picking the same file again
    if (e?.target) e.target.value = ''

    if (!file) return
    setPhotoError('')
    setPhotoStatus('idle')
    setPhotoResult(null)
    setPhotoFile(file)
  }

  const loadImageFromFile = (file: File) =>
    new Promise<HTMLImageElement>((resolve, reject) => {
      const url = URL.createObjectURL(file)
      const img = new Image()
      img.onload = () => {
        URL.revokeObjectURL(url)
        resolve(img)
      }
      img.onerror = () => {
        URL.revokeObjectURL(url)
        reject(new Error('Не удалось прочитать изображение.'))
      }
      img.src = url
    })

  const canvasToJpegBlob = (canvas: HTMLCanvasElement, quality: number) =>
    new Promise<Blob | null>((resolve) => {
      canvas.toBlob((blob) => resolve(blob), 'image/jpeg', quality)
    })

  const optimizePhotoForUpload = async (file: File, aggressive = false): Promise<File> => {
    const SOFT_LIMIT = aggressive ? 700 * 1024 : 1024 * 1024
    const maxSide = aggressive ? 1280 : 1680
    const supported = /^image\/(jpeg|jpg|png|webp)$/i.test(file.type || '')

    if (supported && file.size <= SOFT_LIMIT) return file

    try {
      const img = await loadImageFromFile(file)
      const srcW = Math.max(1, img.naturalWidth || (img as any).width || 1)
      const srcH = Math.max(1, img.naturalHeight || (img as any).height || 1)
      const ratio = Math.min(1, maxSide / Math.max(srcW, srcH))
      const width = Math.max(1, Math.round(srcW * ratio))
      const height = Math.max(1, Math.round(srcH * ratio))

      const canvas = document.createElement('canvas')
      canvas.width = width
      canvas.height = height
      const ctx = canvas.getContext('2d', { alpha: false })
      if (!ctx) return file

      ctx.drawImage(img, 0, 0, width, height)

      const qualities = aggressive ? [0.84, 0.76, 0.68, 0.58, 0.5] : [0.9, 0.82, 0.74, 0.66, 0.58]
      let best: Blob | null = null

      for (const q of qualities) {
        const blob = await canvasToJpegBlob(canvas, q)
        if (!blob) continue

        if (!best || blob.size < best.size) best = blob
        if (blob.size <= SOFT_LIMIT) {
          best = blob
          break
        }
      }

      if (!best) return file
      const base = (file.name || 'spread-photo').replace(/\.[a-z0-9]+$/i, '')
      return new File([best], `${base || 'spread-photo'}.jpg`, {
        type: 'image/jpeg',
        lastModified: Date.now(),
      })
    } catch {
      return file
    }
  }

  const mapPhotoError = (raw: string) => {
    const msg = (raw || '').trim()
    if (!msg) return 'Не удалось получить ответ от AI.'
    const parsed = readBackendErrorDetail(msg)
    const detail = parsed?.detail
    if (detail?.code === 'READING_LIMIT_EXCEEDED' || /READING_LIMIT_EXCEEDED|402/i.test(msg)) {
      return readingLimitMessage
    }
    if (/401|403/i.test(msg)) return 'Сессия устарела. Перезапустите мини-приложение и попробуйте снова.'
    if (/413|too large/i.test(msg)) return 'Фото слишком большое. Выберите более лёгкое изображение.'
    if (/415|unsupported/i.test(msg)) return 'Формат фото не поддерживается. Лучше использовать JPG или PNG.'
    if (/503|service unavailable/i.test(msg)) return 'AI-сервис временно недоступен. Повторите через минуту.'
    if (/load failed|failed to fetch|networkerror/i.test(msg)) {
      return 'Не удалось отправить фото на сервер. Проверьте интернет и попробуйте ещё раз.'
    }
    if (typeof detail === 'string' && detail.trim()) return detail.trim()
    return msg
  }

  const runPhotoAnalysis = async () => {
    if (photoStatus === 'uploading') return

    if (!token) {
      setPhotoStatus('error')
      setPhotoError('Нужен вход через Telegram, чтобы отправить фото на анализ.')
      return
    }

    if (!photoFile) {
      setPhotoStatus('error')
      setPhotoError('Выберите фото расклада (из галереи или сделайте снимок).')
      return
    }

    setPhotoStatus('uploading')
    setPhotoError('')

    try {
      const sourceFile = photoFile
      const preparedFile = await optimizePhotoForUpload(sourceFile, false)

      let out: any = null
      let lastErr: any = null
      for (let attempt = 0; attempt < 2; attempt++) {
        const fileForAttempt = attempt === 0 ? preparedFile : await optimizePhotoForUpload(sourceFile, true)
        try {
          out = await analyzeSpreadPhoto(token, fileForAttempt, {
            topic,
            question: (question || '').trim(),
          })
          break
        } catch (err: any) {
          lastErr = err
          const text = String(err?.message || '')
          const retryable = /load failed|failed to fetch|networkerror|503|502|504|gateway timeout/i.test(text)
          if (attempt === 0 && retryable) continue
          throw err
        }
      }
      if (!out && lastErr) throw lastErr

      setPhotoResult({
        description: (out as any)?.description || '',
        cards: (out as any)?.cards || [],
      })
      setPhotoStatus('done')
      void refreshBilling(token)
    } catch (err: any) {
      setPhotoStatus('error')
      setPhotoError(mapPhotoError(String(err?.message || '')))
      void refreshBilling(token)
    }
  }

  // ---------------------------------------------------------------------------------------------
  // [3 CARDS] routing + helpers (пока без бэка: рандом из FRONT_CARD_URLS)
  // ---------------------------------------------------------------------------------------------

  const cardNameFromUrl = (url: string) => {
    const raw = (url.split('/').pop() || 'Card').replace(/\.(png|jpg|jpeg|webp)$/i, '')
    return raw
      .replace(/_/g, ' ')
      .replace(/-/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
  }

  const pickUniqueIndexes = (n: number, max: number) => {
    const set = new Set<number>()
    const safeMax = Math.max(1, max)
    while (set.size < n) set.add(Math.floor(Math.random() * safeMax))
    return Array.from(set)
  }

  const buildThreeCardsMock = (): ThreeCardResult[] => {
    const roles = ['Карта 1', 'Карта 2', 'Карта 3']
    const idxs = pickUniqueIndexes(3, FRONT_CARD_URLS.length || 78)

    return idxs.map((idx, i) => {
      const url = FRONT_CARD_URLS[idx] || backCardImg
      const name = cardNameFromUrl(url)

      const text =
        threeKind === 'yesno'
          ? 'Ответ будет подтянут с бэкенда. Сейчас это mock.\n\nСфокусируйтесь на ощущениях от карты и переформулируйте вопрос, если нужно.'
          : threeKind === 'advice'
          ? 'Совет будет подтянут с бэкенда. Сейчас это mock.\n\nПодумайте: какой маленький шаг вы можете сделать уже сегодня?'
          : 'Интерпретация будет подтянута с бэкенда. Сейчас это mock.\n\nЗаметьте эмоции и ассоциации — это уже часть ответа.'

      return { idx, url, name, role: roles[i], text, isReversed: false }
    })
  }

  const resetThreeCardsState = () => {
    if (threeMeaningTimerRef.current) {
      window.clearTimeout(threeMeaningTimerRef.current)
      threeMeaningTimerRef.current = null
    }

    setThreeScreen('setup')
    setThreeShakeEnabled(false)
    setThreeShuffleProgress(0)
    setThreeReadyToOpen(false)
    setThreeShowMeaning(false)

    setThreeCards([])
    setThreeOrder([0, 1, 2])

    // Reset description and loading state for three card reading
    setThreeDesc('')
    setThreeLoading(false)

    threeFinishingRef.current = false
    threeRequestSeqRef.current += 1
    threeLastSwapAtRef.current = 0

    threeLastAccelRef.current = null
    threeShakeCooldownRef.current = 0
    threeLastPulseRef.current = 0
  }


  const openThreeCards = () => {
    resetThreeCardsState()

    // удобно: подхватим уже введенный вопрос с главной
    setThreeQuestion(question || '')
    setView('three_cards_prep')

    try {
      hapticPulse(0.22)
    } catch {}
  }

  const beginThreeShuffle = async () => {
    if (!threeQuestion.trim()) {
      try {
        hapticPulse(0.28)
      } catch {}
      return
    }

    if (needsMotionPermission) await requestMotion()

    threeLastAccelRef.current = null
    threeShakeCooldownRef.current = 0
    threeLastPulseRef.current = 0

    // обнулим старые карты: настоящие значения будут получены с бэка
    setThreeCards([])
    // Reset description and mark as loading while we fetch from the backend
    setThreeDesc('')
    setThreeLoading(true)
    setThreeShowMeaning(false)
    // дата расклада (Europe/Vilnius) — используем уже существующий helper
    setThreeDayKey(getVilniusDayKey())
    // переходим на экран перемешивания
    setThreeScreen('shuffle')
    setThreeShakeEnabled(true)
    setThreeReadyToOpen(false)
    setThreeShuffleProgress(0)

    // стартуем всегда “123”
    setThreeOrder([0, 1, 2])
    threeFinishingRef.current = false
    threeLastSwapAtRef.current = 0

    try {
      hapticPulse(0.35)
    } catch {}

    // Запрос запускаем сразу в момент начала шейка (до открытия карт), чтобы сократить ожидание результата.
    const requestSeq = threeRequestSeqRef.current + 1
    threeRequestSeqRef.current = requestSeq
    buildThreeCardsReal().then((cards) => {
      if (threeRequestSeqRef.current !== requestSeq) return
      setThreeCards(cards)
      setThreeLoading(false)
    }).catch((err: any) => {
      if (threeRequestSeqRef.current !== requestSeq) return
      console.warn('[reading] three_cards failed:', err)
      setThreeCards([])
      setThreeDesc(mapReadingError(String(err?.message || err || '')))
      setThreeLoading(false)
    })
  }

  const pickNextThreeOrder = (cur: number[]) => {
    const curKey = cur.join('')
    // пытаемся несколько раз выбрать другую перестановку
    for (let t = 0; t < 8; t++) {
      const next = THREE_PERMS[Math.floor(Math.random() * THREE_PERMS.length)]
      if (next.join('') !== curKey) return next
    }
    // fallback: просто swap крайних
    return [cur[2], cur[1], cur[0]]
  }

  const finishThreeShuffle = () => {
    if (threeFinishingRef.current) return
    threeFinishingRef.current = true
    setThreeShowMeaning(false)

    // ✅ по итогу карты “на своих местах” (возвращаемся к 123) и даём время доехать анимации
    setThreeShakeEnabled(false)
    setThreeOrder([0, 1, 2])

    window.setTimeout(() => {
      setThreeReadyToOpen(true)
      setThreeShuffleProgress(1)

      window.setTimeout(() => {
        setThreeScreen('result')
        try {
          hapticPulse(0.7)
        } catch {}

        // Сначала визуально раскрываем карты, потом показываем текст интерпретации.
        if (threeMeaningTimerRef.current) window.clearTimeout(threeMeaningTimerRef.current)
        threeMeaningTimerRef.current = window.setTimeout(() => {
          setThreeShowMeaning(true)
        }, 520)
      }, 180)
    }, 420)
  }

  // визуальная перестановка мест (без прогресса)
  const swapThreeVisual = () => {
    setThreeOrder((cur) => pickNextThreeOrder(cur))
  }

  // один “импульс” перемешивания (визуал + прогресс)
  const shuffleThreeOnce = (power01: number) => {
    const p = clamp(power01, 0, 1)

    swapThreeVisual()

    setThreeShuffleProgress((cur) => {
      const step = THREE_SHAKE_STEP_BASE + p * 0.11
      const next = clamp(cur + step, 0, 1)

      if (next >= 1) {
        requestAnimationFrame(() => finishThreeShuffle())
      }

      return next
    })
  }

  const autoShuffleThree = async () => {
    if (needsMotionPermission) await requestMotion()

    setThreeShakeEnabled(true)
    setThreeReadyToOpen(false)
    threeFinishingRef.current = false

    const from = threeShuffleProgress
    const start = performance.now()
    const dur = 1200

    let lastPulse = 0
    threeLastSwapAtRef.current = 0

    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / dur)
      const eased = 1 - Math.pow(1 - t, 3)
      const next = clamp(from + (1 - from) * eased, 0, 1)

      // ✅ перестановка мест НЕ чаще, чем раз в 140мс — чтобы глаз видел “123 → 321 → …”
      if (now - threeLastSwapAtRef.current > 140 && next < 1) {
        threeLastSwapAtRef.current = now
        swapThreeVisual()
      }

      setThreeShuffleProgress(next)

      if (now - lastPulse > 90) {
        lastPulse = now
        try {
          hapticPulse(clamp(next, 0.12, 1))
        } catch {}
      }

      if (t < 1) {
        requestAnimationFrame(tick)
        return
      }

      finishThreeShuffle()
    }

    requestAnimationFrame(tick)
  }


  const restartThreeCards = () => {
    resetThreeCardsState()
    // вопрос оставим — удобно перетасовать заново
    setThreeScreen('setup')
  }
  // ---------------------------------------------------------------------------------------------
  // [PAST • PRESENT • FUTURE] routing + helpers (пока без бэка: рандом из FRONT_CARD_URLS)
  // ---------------------------------------------------------------------------------------------

  const buildPpfCardsMock = (): PpfCardResult[] => {
    const roles = ['Прошлое', 'Настоящее', 'Будущее']
    const idxs = pickUniqueIndexes(3, FRONT_CARD_URLS.length || 78)

    return idxs.map((idx, i) => {
      const url = FRONT_CARD_URLS[idx] || backCardImg
      const name = cardNameFromUrl(url)

      const focusLine =
        ppfFocus === 'past'
          ? 'Фокус: прошлое — что привело к ситуации?'
          : ppfFocus === 'present'
          ? 'Фокус: настоящее — что происходит прямо сейчас?'
          : 'Фокус: будущее — куда ведёт текущая динамика?'

      const text =
        `${focusLine}\n\n` +
        'Интерпретация будет подтянута с бэкенда. Сейчас это mock.\n\n' +
        'Смотрите на символы, ощущения и первую ассоциацию — это часто самый точный ответ.'

      return { idx, url, name, role: roles[i], text, isReversed: false }
    })
  }

  const resetPpfState = () => {
    setPpfScreen('setup')
    setPpfShakeEnabled(false)
    setPpfShuffleProgress(0)
    setPpfReadyToOpen(false)

    setPpfCards([])
    setPpfOrder([0, 1, 2])

    // Reset description and loading state for PPF reading
    setPpfDesc('')
    setPpfLoading(false)

    ppfFinishingRef.current = false
    ppfLastSwapAtRef.current = 0

    ppfLastAccelRef.current = null
    ppfShakeCooldownRef.current = 0
    ppfLastPulseRef.current = 0
  }

  const openPastPresentFuture = () => {
    resetPpfState()

    // подхватим вопрос с главной
    setPpfQuestion(question || '')
    setView('past_present_future_prep')

    try {
      hapticPulse(0.22)
    } catch {}
  }

  const beginPpfShuffle = async () => {
    if (!ppfQuestion.trim()) {
      try {
        hapticPulse(0.28)
      } catch {}
      return
    }

    if (needsMotionPermission) await requestMotion()

    ppfLastAccelRef.current = null
    ppfShakeCooldownRef.current = 0
    ppfLastPulseRef.current = 0

    // обнулим старые карты: настоящие значения будут получены с бэка
    setPpfCards([])
    // Reset description and mark as loading while we fetch from the backend
    setPpfDesc('')
    setPpfLoading(true)
    setPpfDayKey(getVilniusDayKey())

    setPpfScreen('shuffle')
    setPpfShakeEnabled(true)
    setPpfReadyToOpen(false)
    setPpfShuffleProgress(0)

    // стартуем всегда “123”
    setPpfOrder([0, 1, 2])
    ppfFinishingRef.current = false
    ppfLastSwapAtRef.current = 0

    try {
      hapticPulse(0.35)
    } catch {}

    // подгружаем реальные карты: fallback на мок при ошибках
    buildPpfCardsReal().then((cards) => {
      setPpfCards(cards)
      setPpfLoading(false)
    }).catch((err: any) => {
      console.warn('[reading] ppf failed:', err)
      setPpfCards([])
      setPpfDesc(mapReadingError(String(err?.message || err || '')))
      setPpfLoading(false)
    })
  }

  const pickNextPpfOrder = (cur: number[]) => {
    const curKey = cur.join('')
    for (let t = 0; t < 8; t++) {
      const next = THREE_PERMS[Math.floor(Math.random() * THREE_PERMS.length)]
      if (next.join('') !== curKey) return next
    }
    return [cur[2], cur[1], cur[0]]
  }

  const finishPpfShuffle = () => {
    if (ppfFinishingRef.current) return
    ppfFinishingRef.current = true

    setPpfShakeEnabled(false)
    setPpfOrder([0, 1, 2])

    window.setTimeout(() => {
      setPpfReadyToOpen(true)
      setPpfShuffleProgress(1)

      window.setTimeout(() => {
        setPpfScreen('result')
        try {
          hapticPulse(0.7)
        } catch {}
      }, 180)
    }, 420)
  }

  const swapPpfVisual = () => {
    setPpfOrder((cur) => pickNextPpfOrder(cur))
  }

  const shufflePpfOnce = (power01: number) => {
    const p = clamp(power01, 0, 1)

    swapPpfVisual()

    setPpfShuffleProgress((cur) => {
      const step = PPF_SHAKE_STEP_BASE + p * 0.11
      const next = clamp(cur + step, 0, 1)

      if (next >= 1) {
        requestAnimationFrame(() => finishPpfShuffle())
      }
      return next
    })
  }

  const autoShufflePpf = async () => {
    if (needsMotionPermission) await requestMotion()

    setPpfShakeEnabled(true)
    setPpfReadyToOpen(false)
    ppfFinishingRef.current = false

    const from = ppfShuffleProgress
    const start = performance.now()
    const dur = 1200

    let lastPulse = 0
    ppfLastSwapAtRef.current = 0

    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / dur)
      const eased = 1 - Math.pow(1 - t, 3)
      const next = clamp(from + (1 - from) * eased, 0, 1)

      if (now - ppfLastSwapAtRef.current > 140 && next < 1) {
        ppfLastSwapAtRef.current = now
        swapPpfVisual()
      }

      setPpfShuffleProgress(next)

      if (now - lastPulse > 90) {
        lastPulse = now
        try {
          hapticPulse(clamp(next, 0.12, 1))
        } catch {}
      }

      if (t < 1) {
        requestAnimationFrame(tick)
        return
      }

      finishPpfShuffle()
    }

    requestAnimationFrame(tick)
  }

  const restartPpf = () => {
    resetPpfState()
    setPpfScreen('setup')
  }
  /* =============================================================================================
    [20.4] РАСКЛАД "ПРИНЯТИЕ РЕШЕНИЯ" (2 КАРТЫ): 3 ЭКРАНА (SETUP → SHUFFLE → RESULT)
    Логика 1:1 как three_cards, но 2 карты и 2 слота (меняются местами при тряске)
  ============================================================================================= */

  type DecisionScreen = 'setup' | 'shuffle' | 'result'
  type DecisionFocus = 'a' | 'b'

  const DECISION_FOCUS: { id: DecisionFocus; label: string }[] = [
    { id: 'a', label: 'Вариант A' },
    { id: 'b', label: 'Вариант B' },
  ]

  type DecisionCardResult = { idx: number; url: string; name: string; role: string; text: string; isReversed?: boolean }

  // 2 “слота” (как две карты на первом экране)
  const DECISION_SLOTS: ThreeCardPos[] = [
    { x: -26, y: 2, r: -6, s: 1.0, z: 1 }, // левый
    { x: 26, y: -2, r: 6, s: 1.0, z: 1 },  // правый
  ]

  const DECISION_PERMS: number[][] = [
    [0, 1],
    [1, 0],
  ]

  const [decisionScreen, setDecisionScreen] = useState<DecisionScreen>('setup')
  const [decisionQuestion, setDecisionQuestion] = useState('')
  const [decisionFocus, setDecisionFocus] = useState<DecisionFocus>('a')

  // bump-анимация для свитчера фокуса
  const [decisionPrevFocus, setDecisionPrevFocus] = useState<DecisionFocus>('a')
  const prevDecisionFocusRef = useRef<DecisionFocus>('a')
  const [decisionFocusIsBumping, setDecisionFocusIsBumping] = useState(false)
  const decisionFocusBumpTRef = useRef<number | null>(null)
  const [decisionFocusBump, setDecisionFocusBump] = useState(0)

  const decisionFocusIndices = useMemo(() => {
    const m = new Map<DecisionFocus, number>()
    DECISION_FOCUS.forEach((k, i) => m.set(k.id, i))
    return m
  }, [])

  const decisionFocusActiveIndex = decisionFocusIndices.get(decisionFocus) ?? 0
  const decisionFocusPrevIndex = decisionFocusIndices.get(decisionPrevFocus) ?? 0

  const onPickDecisionFocus = (next: DecisionFocus) => {
    if (next === decisionFocus) return

    setDecisionPrevFocus(prevDecisionFocusRef.current)
    prevDecisionFocusRef.current = next
    setDecisionFocus(next)

    setDecisionFocusBump((n) => n + 1)

    setDecisionFocusIsBumping(false)
    if (decisionFocusBumpTRef.current) window.clearTimeout(decisionFocusBumpTRef.current)
    requestAnimationFrame(() => {
      setDecisionFocusIsBumping(true)
      decisionFocusBumpTRef.current = window.setTimeout(() => setDecisionFocusIsBumping(false), 440)
    })
  }

  const [decisionCards, setDecisionCards] = useState<DecisionCardResult[]>([])
  const [decisionOrder, setDecisionOrder] = useState<number[]>([0, 1])

  const [decisionShakeEnabled, setDecisionShakeEnabled] = useState(false)
  const [decisionShuffleProgress, setDecisionShuffleProgress] = useState(0)
  const [decisionReadyToOpen, setDecisionReadyToOpen] = useState(false)

  const [decisionDayKey, setDecisionDayKey] = useState<string>('')

  const decisionLastAccelRef = useRef<{ x: number; y: number; z: number } | null>(null)
  const decisionShakeCooldownRef = useRef(0)
  const decisionLastPulseRef = useRef(0)

  const decisionLastSwapAtRef = useRef(0)
  const decisionFinishingRef = useRef(false)

  const DECISION_SHAKE_THRESHOLD = 8.8
  const DECISION_SHAKE_STEP_BASE = 0.11

  // держим актуальный прогресс в refs, чтобы listeners не пересоздавались на каждый тик
  const shuffleProgressRef = useRef(0)
  const threeShuffleProgressRef = useRef(0)
  const ppfShuffleProgressRef = useRef(0)
  const decisionShuffleProgressRef = useRef(0)

  useEffect(() => {
    shuffleProgressRef.current = shuffleProgress
  }, [shuffleProgress])

  useEffect(() => {
    threeShuffleProgressRef.current = threeShuffleProgress
  }, [threeShuffleProgress])

  useEffect(() => {
    ppfShuffleProgressRef.current = ppfShuffleProgress
  }, [ppfShuffleProgress])

  useEffect(() => {
    decisionShuffleProgressRef.current = decisionShuffleProgress
  }, [decisionShuffleProgress])

  const buildDecisionCardsMock = (): DecisionCardResult[] => {
    const roles = ['Вариант A', 'Вариант B']
    const idxs = pickUniqueIndexes(2, FRONT_CARD_URLS.length || 78)

    return idxs.map((idx, i) => {
      const url = FRONT_CARD_URLS[idx] || backCardImg
      const name = cardNameFromUrl(url)
      const text =
        'Интерпретация будет подтянута с бэкенда. Сейчас это mock.\n\nПодумайте, как этот образ соотносится с вашим вариантом и какие чувства он вызывает.'

      return { idx, url, name, role: roles[i], text, isReversed: false }
    })
  }

  // =================================================================================================
  // [REAL READINGS] FUNCTIONS TO FETCH REAL LLM INTERPRETATIONS FOR SPREADS
  // These helpers call the backend API (createReading) to get actual card meanings.
  // They gracefully fallback to the existing mock builders when unauthenticated or on errors.
  // =================================================================================================

  // Build 3 cards reading with real LLM meanings
  const buildThreeCardsReal = async (): Promise<ThreeCardResult[]> => {
    if (!token) {
      return buildThreeCardsMock()
    }
    const params = {
      spread_type: 'three_cards' as const,
      topic: topic,
      question: threeQuestion.trim(),
      consider_reversed: true,
    }
    const reading: any = await createReading(token, params)
    void refreshBilling(token)
    // Save the description from the backend so we can display it in the UI
    setThreeDesc(String(reading?.description ?? ''))
    const roles = ['Карта 1', 'Карта 2', 'Карта 3']
    return (reading.cards || []).slice(0, 3).map((c: any, i: number) => {
      const idx = Number(c.card_index ?? 0)
      const url = FRONT_CARD_URLS[idx] || backCardImg
      const isReversed = Boolean(c?.is_reversed)
      const baseName = String(c.card_name || cardNameFromUrl(url))
      const name = isReversed ? `${baseName} (перевёрнутая)` : baseName
      const text = String(c.meaning || '').trim() || ''
      return {
        idx,
        url,
        name,
        role: roles[i] || `Карта ${i + 1}`,
        text,
        isReversed,
      }
    })
  }

  // Build Past-Present-Future reading with real LLM meanings
  const buildPpfCardsReal = async (): Promise<PpfCardResult[]> => {
    if (!token) {
      return buildPpfCardsMock()
    }
    const params = {
      spread_type: 'ppf' as const,
      topic: topic,
      question: ppfQuestion.trim(),
      consider_reversed: true,
    }
    const reading: any = await createReading(token, params)
    void refreshBilling(token)
    // Save description returned from the backend
    setPpfDesc(String(reading?.description ?? ''))
    const roles = ['Прошлое', 'Настоящее', 'Будущее']
    const focusLine =
      ppfFocus === 'past'
        ? 'Фокус: прошлое — что привело к ситуации?'
        : ppfFocus === 'present'
        ? 'Фокус: настоящее — что происходит прямо сейчас?'
        : 'Фокус: будущее — куда ведёт текущая динамика?'
    return (reading.cards || []).slice(0, 3).map((c: any, i: number) => {
      const idx = Number(c.card_index ?? 0)
      const url = FRONT_CARD_URLS[idx] || backCardImg
      const isReversed = Boolean(c?.is_reversed)
      const baseName = String(c.card_name || cardNameFromUrl(url))
      const name = isReversed ? `${baseName} (перевёрнутая)` : baseName
      const meaning = String(c.meaning || '').trim() || ''
      const text = `${focusLine}\n\n${meaning}`
      return {
        idx,
        url,
        name,
        role: roles[i] || '',
        text,
        isReversed,
      }
    })
  }

  // Build Decision reading with real LLM meanings
  const buildDecisionCardsReal = async (): Promise<DecisionCardResult[]> => {
    if (!token) {
      return buildDecisionCardsMock()
    }
    const params = {
      spread_type: 'decision' as const,
      topic: topic,
      question: decisionQuestion.trim(),
      consider_reversed: true,
    }
    const reading: any = await createReading(token, params)
    void refreshBilling(token)
    // Save description returned from the backend
    setDecisionDesc(String(reading?.description ?? ''))
    const roles = ['Вариант A', 'Вариант B']
    return (reading.cards || []).slice(0, 2).map((c: any, i: number) => {
      const idx = Number(c.card_index ?? 0)
      const url = FRONT_CARD_URLS[idx] || backCardImg
      const isReversed = Boolean(c?.is_reversed)
      const baseName = String(c.card_name || cardNameFromUrl(url))
      const name = isReversed ? `${baseName} (перевёрнутая)` : baseName
      const text = String(c.meaning || '').trim() || ''
      return {
        idx,
        url,
        name,
        role: roles[i] || '',
        text,
        isReversed,
      }
    })
  }

  const resetDecisionState = () => {
    setDecisionScreen('setup')
    setDecisionShakeEnabled(false)
    setDecisionShuffleProgress(0)
    setDecisionReadyToOpen(false)

    setDecisionCards([])
    setDecisionOrder([0, 1])

    // Reset description and loading state for decision reading
    setDecisionDesc('')
    setDecisionLoading(false)

    setDecisionDayKey('')

    decisionFinishingRef.current = false
    decisionLastSwapAtRef.current = 0

    decisionLastAccelRef.current = null
    decisionShakeCooldownRef.current = 0
    decisionLastPulseRef.current = 0
  }

  const openDecision = () => {
    resetDecisionState()

    // удобно: подхватим уже введённый вопрос с главной
    setDecisionQuestion(question || '')
    setView('decision_prep')

    try {
      hapticPulse(0.22)
    } catch {}
  }

  const beginDecisionShuffle = async () => {
    if (!decisionQuestion.trim()) {
      try {
        hapticPulse(0.28)
      } catch {}
      return
    }

    if (needsMotionPermission) await requestMotion()

    decisionLastAccelRef.current = null
    decisionShakeCooldownRef.current = 0
    decisionLastPulseRef.current = 0

    // обнулим старые карты: настоящие значения будут получены с бэка
    setDecisionCards([])
    // Reset description and mark as loading while we fetch from the backend
    setDecisionDesc('')
    setDecisionLoading(true)
    setDecisionDayKey(getVilniusDayKey())

    setDecisionScreen('shuffle')
    setDecisionShakeEnabled(true)
    setDecisionReadyToOpen(false)
    setDecisionShuffleProgress(0)

    setDecisionOrder([0, 1])
    decisionFinishingRef.current = false
    decisionLastSwapAtRef.current = 0

    try {
      hapticPulse(0.35)
    } catch {}

    // подгружаем реальные карты: fallback на мок при ошибках
    buildDecisionCardsReal().then((cards) => {
      setDecisionCards(cards)
      setDecisionLoading(false)
    }).catch((err: any) => {
      console.warn('[reading] decision failed:', err)
      setDecisionCards([])
      setDecisionDesc(mapReadingError(String(err?.message || err || '')))
      setDecisionLoading(false)
    })
  }

  const pickNextDecisionOrder = (cur: number[]) => {
    const curKey = cur.join('')
    for (let t = 0; t < 6; t++) {
      const next = DECISION_PERMS[Math.floor(Math.random() * DECISION_PERMS.length)]
      if (next.join('') !== curKey) return next
    }
    return [cur[1], cur[0]]
  }

  const swapDecisionVisual = () => {
    setDecisionOrder((cur) => pickNextDecisionOrder(cur))
  }

  const finishDecisionShuffle = () => {
    if (decisionFinishingRef.current) return
    decisionFinishingRef.current = true

    setDecisionShakeEnabled(false)
    setDecisionOrder([0, 1])

    window.setTimeout(() => {
      setDecisionReadyToOpen(true)
      setDecisionShuffleProgress(1)
    }, 420)
  }

  const shuffleDecisionOnce = (power01: number) => {
    const p = clamp(power01, 0, 1)
    swapDecisionVisual()

    setDecisionShuffleProgress((cur) => {
      const step = DECISION_SHAKE_STEP_BASE + p * 0.16
      const next = clamp(cur + step, 0, 1)

      if (next >= 1) {
        requestAnimationFrame(() => finishDecisionShuffle())
      }

      return next
    })
  }

  const autoShuffleDecision = async () => {
    if (needsMotionPermission) await requestMotion()

    setDecisionShakeEnabled(true)
    setDecisionReadyToOpen(false)
    decisionFinishingRef.current = false

    const from = decisionShuffleProgress
    const start = performance.now()
    const dur = 1050

    let lastPulse = 0
    decisionLastSwapAtRef.current = 0

    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / dur)
      const eased = 1 - Math.pow(1 - t, 3)
      const next = clamp(from + (1 - from) * eased, 0, 1)

      if (now - decisionLastSwapAtRef.current > 150 && next < 1) {
        decisionLastSwapAtRef.current = now
        swapDecisionVisual()
      }

      setDecisionShuffleProgress(next)

      if (now - lastPulse > 90) {
        lastPulse = now
        try {
          hapticPulse(clamp(next, 0.12, 1))
        } catch {}
      }

      if (t < 1) {
        requestAnimationFrame(tick)
        return
      }

      finishDecisionShuffle()
    }

    requestAnimationFrame(tick)
  }

  const openDecisionResult = () => {
    if (!decisionReadyToOpen && decisionShuffleProgress < 1) return
    setDecisionScreen('result')
    setDecisionShakeEnabled(false)

    try {
      hapticPulse(0.7)
    } catch {}
  }

  const restartDecision = () => {
    resetDecisionState()
    setDecisionScreen('setup')
  }

  /* =============================================================================================
    [21.2] SHAKE LISTENER — 2 КАРТЫ (отдельно от “карты дня” / “3 карты” / PPF)
  ============================================================================================= */

  useEffect(() => {
    if (view !== 'decision_prep') return
    if (decisionScreen !== 'shuffle') return
    if (!decisionShakeEnabled) return
    if (decisionReadyToOpen) return

    let mounted = true

    const onMotion = (e: DeviceMotionEvent) => {
      if (!mounted) return
      if (view !== 'decision_prep') return
      if (decisionScreen !== 'shuffle') return
      if (!decisionShakeEnabled || decisionReadyToOpen) return

      const a = e.accelerationIncludingGravity
      if (!a) return

      const x = a.x ?? 0
      const y = a.y ?? 0
      const z = a.z ?? 0

      const prev = decisionLastAccelRef.current
      decisionLastAccelRef.current = { x, y, z }
      if (!prev) return

      const dx = x - prev.x
      const dy = y - prev.y
      const dz = z - prev.z
      const delta = Math.abs(dx) + Math.abs(dy) + Math.abs(dz)

      const now = Date.now()
      if (now < decisionShakeCooldownRef.current) return

      if (delta > DECISION_SHAKE_THRESHOLD) {
        decisionShakeCooldownRef.current = now + 70
        const power = clamp((delta - DECISION_SHAKE_THRESHOLD) / 18, 0, 1)

        shuffleDecisionOnce(power)

        const currentProgress = decisionShuffleProgressRef.current
        const interval = Math.round(120 - clamp(currentProgress, 0, 1) * 70)
        if (now - decisionLastPulseRef.current > Math.max(HAPTIC_MIN_INTERVAL, interval)) {
          decisionLastPulseRef.current = now
          try {
            hapticPulse(clamp(currentProgress * 0.85 + power * 0.45, 0.12, 1))
          } catch {}
        }
      }
    }

    window.addEventListener('devicemotion', onMotion, { passive: true })
    return () => {
      mounted = false
      window.removeEventListener('devicemotion', onMotion as any)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, decisionScreen, decisionShakeEnabled, decisionReadyToOpen])


  const enableShake = async () => {
    lastAccelRef.current = null
    lastPulseRef.current = 0
    shakeCooldownRef.current = 0
    setShuffleProgress(0)
    setStopRequested(false)

    // ✅ отправляем question + topic на бекенд, чтобы он выдал/вернул карту дня
    if (token) {
      setCardDayLoading(true)
      try {
        const dto = await createCardOfDay(token, {
          question: question || '',
          topic: topic,
          deck_size: 78,
          consider_reversed: true,
        })

        const idx = Math.max(0, Math.min(dto.card_index ?? 0, 77))
        const url = FRONT_CARD_URLS[idx] || backCardImg

        setDailyFrontUrl(url)

        setDailyDesc(dto.description || '')
        setDailyCardName(dto.card_name || '')
        setDailyDayKey(dto.day_key || '')
      } catch (e) {
        // если бекенд не доступен — оставим текущий локальный daily, чтобы UX не умер
        const fallback = pickDailyCardUrl()
        setDailyFrontUrl(fallback)
      } finally {
        setCardDayLoading(false)
      }
    } else {
      // без токена — fallback на локальный daily
      const fallback = pickDailyCardUrl()
      setDailyFrontUrl(fallback)
    }

    // ✅ дальше — текущая логика шейка (ввод скрываем, показываем shake-блок)
    if (needsMotionPermission) await requestMotion()
    setShakeEnabled(true)
  }

  // ✅ haptics: Telegram + fallback vibrate
  const hapticPulse = (strength01: number) => {
    const strength = clamp(strength01, 0, 1)
    const tg = (window as any)?.Telegram?.WebApp

    try {
      const hf = tg?.HapticFeedback
      if (hf?.impactOccurred) {
        if (strength > 0.75) hf.impactOccurred('heavy')
        else if (strength > 0.4) hf.impactOccurred('medium')
        else hf.impactOccurred('light')
        return
      }
    } catch {}

    try {
      const ms = strength > 0.85 ? 75 : strength > 0.65 ? 55 : strength > 0.45 ? 38 : strength > 0.25 ? 24 : 14

      navigator.vibrate?.(ms)
    } catch {}
  }

  // ✅ когда карта доехала до рубашки — фиксируем результат шейка
  const onStoppedAtBack = () => {
    setStopRequested(false)
    setCardRevealed(false)
    setShakenOnce(true)

    // ✅ теперь фронт уже залочен в PremiumFlipCard на dailyFrontUrl,
    // но дополнительно сохраним в selectedFrontUrl, чтобы result точно совпадал
    setSelectedFrontUrl(dailyFrontUrl || backCardImg)

    setTimeout(() => {
      hapticPulse(1)
      try {
        navigator.vibrate?.([35, 18, 85])
      } catch {}
    }, 60)
  }

  const revealCard = () => {
    if (!shakenOnce) return
    setCardRevealed(true)
    hapticPulse(0.6)
  }

  /* =============================================================================================
     [21] SHAKE LISTENER + НАРАСТАЮЩАЯ ВИБРАЦИЯ (ОТДЕЛЬНЫЙ EFFECT, НЕ В CANVAS!)
  ============================================================================================= */

  useEffect(() => {
    if (view !== 'card_day_prep') return
    if (!shakeEnabled) return
    if (shakenOnce) return
    if (stopRequested) return

    let mounted = true

    const onMotion = (e: DeviceMotionEvent) => {
      if (!mounted) return
      if (!shakeEnabled || shakenOnce || stopRequested) return

      const a = e.accelerationIncludingGravity
      if (!a) return

      const x = a.x ?? 0
      const y = a.y ?? 0
      const z = a.z ?? 0

      const prev = lastAccelRef.current
      lastAccelRef.current = { x, y, z }

      if (!prev) return

      const dx = x - prev.x
      const dy = y - prev.y
      const dz = z - prev.z

      // "рывок" — чем больше, тем сильнее тряска
      const delta = Math.abs(dx) + Math.abs(dy) + Math.abs(dz)

      const now = Date.now()
      if (now < shakeCooldownRef.current) return

      if (delta > SHAKE_THRESHOLD) {
        // небольшой cooldown, чтобы не летело 200 событий/сек
        shakeCooldownRef.current = now + 55

        // сила импульса 0..1
        const power = clamp((delta - SHAKE_THRESHOLD) / 14, 0, 1)

        setShuffleProgress((p) => {
          const step = SHAKE_STEP_BASE + power * SHAKE_STEP_POWER
          const next = clamp(p + step, 0, 1)
          shuffleProgressRef.current = next

          // при достижении 1 — просим карту остановиться на рубашке
          if (next >= 1) {
            requestAnimationFrame(() => setStopRequested(true))
          }

          return next
        })

        // haptics ramp: чем ближе к 1, тем сильнее и чаще
        const currentProgress = shuffleProgressRef.current
        const interval = Math.round(120 - clamp(currentProgress, 0, 1) * 70) // 120..50ms
        if (now - lastPulseRef.current > Math.max(HAPTIC_MIN_INTERVAL, interval)) {
          lastPulseRef.current = now

          // strength: мягко растёт + добавим power
          const strength = clamp(currentProgress * 0.9 + power * 0.4, 0.12, 1)
          hapticPulse(strength)
        }
      }
    }

    window.addEventListener('devicemotion', onMotion, { passive: true })

    return () => {
      mounted = false
      window.removeEventListener('devicemotion', onMotion as any)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, shakeEnabled, shakenOnce, stopRequested])

  /* =============================================================================================
   [21.1] SHAKE LISTENER — 3 КАРТЫ (отдельно от “карты дня”)
  ============================================================================================= */
  useEffect(() => {
    if (view !== 'three_cards_prep') return
    if (threeScreen !== 'shuffle') return
    if (!threeShakeEnabled) return
    if (threeReadyToOpen) return

    let mounted = true

    const onMotion = (e: DeviceMotionEvent) => {
      if (!mounted) return
      if (view !== 'three_cards_prep') return
      if (threeScreen !== 'shuffle') return
      if (!threeShakeEnabled || threeReadyToOpen) return

      const a = e.accelerationIncludingGravity
      if (!a) return

      const x = a.x ?? 0
      const y = a.y ?? 0
      const z = a.z ?? 0

      const prev = threeLastAccelRef.current
      threeLastAccelRef.current = { x, y, z }
      if (!prev) return

      const dx = x - prev.x
      const dy = y - prev.y
      const dz = z - prev.z
      const delta = Math.abs(dx) + Math.abs(dy) + Math.abs(dz)

      const now = Date.now()
      if (now < threeShakeCooldownRef.current) return

      if (delta > THREE_SHAKE_THRESHOLD) {
        threeShakeCooldownRef.current = now + 70
        const power = clamp((delta - THREE_SHAKE_THRESHOLD) / 18, 0, 1)

        shuffleThreeOnce(power)

        // haptics
        const currentProgress = threeShuffleProgressRef.current
        const interval = Math.round(120 - clamp(currentProgress, 0, 1) * 70)
        if (now - threeLastPulseRef.current > Math.max(HAPTIC_MIN_INTERVAL, interval)) {
          threeLastPulseRef.current = now
          try {
            hapticPulse(clamp(currentProgress * 0.85 + power * 0.45, 0.12, 1))
          } catch {}
        }
      }
    }

    window.addEventListener('devicemotion', onMotion, { passive: true })
    return () => {
      mounted = false
      window.removeEventListener('devicemotion', onMotion as any)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, threeScreen, threeShakeEnabled, threeReadyToOpen])

    /* =============================================================================================
   [21.2] SHAKE LISTENER — ПРОШЛОЕ/НАСТОЯЩЕЕ/БУДУЩЕЕ (отдельно от “карты дня” и three_cards)
  ============================================================================================= */
  useEffect(() => {
    if (view !== 'past_present_future_prep') return
    if (ppfScreen !== 'shuffle') return
    if (!ppfShakeEnabled) return
    if (ppfReadyToOpen) return

    let mounted = true

    const onMotion = (e: DeviceMotionEvent) => {
      if (!mounted) return
      if (view !== 'past_present_future_prep') return
      if (ppfScreen !== 'shuffle') return
      if (!ppfShakeEnabled || ppfReadyToOpen) return

      const a = e.accelerationIncludingGravity
      if (!a) return

      const x = a.x ?? 0
      const y = a.y ?? 0
      const z = a.z ?? 0

      const prev = ppfLastAccelRef.current
      ppfLastAccelRef.current = { x, y, z }
      if (!prev) return

      const dx = x - prev.x
      const dy = y - prev.y
      const dz = z - prev.z
      const delta = Math.abs(dx) + Math.abs(dy) + Math.abs(dz)

      const now = Date.now()
      if (now < ppfShakeCooldownRef.current) return

      if (delta > PPF_SHAKE_THRESHOLD) {
        ppfShakeCooldownRef.current = now + 70
        const power = clamp((delta - PPF_SHAKE_THRESHOLD) / 18, 0, 1)

        shufflePpfOnce(power)

        const currentProgress = ppfShuffleProgressRef.current
        const interval = Math.round(120 - clamp(currentProgress, 0, 1) * 70)
        if (now - ppfLastPulseRef.current > Math.max(HAPTIC_MIN_INTERVAL, interval)) {
          ppfLastPulseRef.current = now
          try {
            hapticPulse(clamp(currentProgress * 0.85 + power * 0.45, 0.12, 1))
          } catch {}
        }
      }
    }

    window.addEventListener('devicemotion', onMotion, { passive: true })
    return () => {
      mounted = false
      window.removeEventListener('devicemotion', onMotion as any)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view, ppfScreen, ppfShakeEnabled, ppfReadyToOpen])

  /* =============================================================================================
     [22] POS: карта под spread-card (до reveal) и под подписью (после reveal)
  ============================================================================================= */

  const bottomPanelRef = useRef<HTMLDivElement | null>(null)
  const [pflipScale, setPflipScale] = useState(1)
  const [pflipTop, setPflipTop] = useState('50%')

  useEffect(() => {
    if (view !== 'card_day_prep') return

    const BASE_W = 116 * 1.28
    const BASE_H = 280
    const MARGIN = 18

    const recalc = () => {
      const vw = window.innerWidth
      const vh = window.innerHeight

      const panelRect = bottomPanelRef.current?.getBoundingClientRect()
      const panelTop = panelRect ? panelRect.top : vh

      const maxW = vw - MARGIN * 2

      const sW = maxW / BASE_W
      let s = Math.max(0.58, Math.min(1, sW))

      // 1) после reveal — под подпись
      if (cardRevealed) {
        s = Math.min(s, 0.86)

        const subRect = subtitleRef.current?.getBoundingClientRect()
        const subBottom = subRect ? subRect.bottom : 120

        const topPx = subBottom + 14 + (BASE_H * s) / 2

        setPflipScale(s)
        setPflipTop(`${topPx}px`)
        return
      }

      // 2) до reveal — под spread-card
      const spreadRect = spreadActiveRef.current?.getBoundingClientRect()
      const spreadBottom = spreadRect ? spreadRect.bottom : 150

      const desiredTop = spreadBottom + 14 + (BASE_H * s) / 2

      const bottomLimit = panelTop - MARGIN
      const desiredCardBottom = desiredTop + (BASE_H * s) / 2

      if (desiredCardBottom > bottomLimit) {
        const availableH = Math.max(160, bottomLimit - (spreadBottom + 14))
        const sH = availableH / BASE_H
        s = Math.max(0.58, Math.min(s, sH))

        const topPx = spreadBottom + 14 + (BASE_H * s) / 2
        setPflipScale(s)
        setPflipTop(`${topPx}px`)
        return
      }

      setPflipScale(s)
      setPflipTop(`${desiredTop}px`)
    }

    recalc()
    window.addEventListener('resize', recalc)
    return () => window.removeEventListener('resize', recalc)
  }, [view, cardRevealed])

  /* =============================================================================================
     [23] AUTO SHUFFLE (кнопка) — тоже с нарастанием вибрации
  ============================================================================================= */

  const autoShuffle = async () => {
    if (view !== 'card_day_prep') return
    if (shakenOnce) return
    if (needsMotionPermission) await requestMotion()

    setShakeEnabled(true)

    const from = shuffleProgress
    const start = performance.now()
    const dur = 1200

    let lastPulse = 0

    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / dur)
      const eased = 1 - Math.pow(1 - t, 3)

      const next = clamp(from + (1 - from) * eased, 0, 1)
      setShuffleProgress(next)

      // ramp haptic (частота + сила)
      if (now - lastPulse > 70) {
        lastPulse = now
        const strength = clamp(next, 0.12, 1)
        hapticPulse(strength)
      }

      if (t < 1) {
        requestAnimationFrame(tick)
        return
      }

      setStopRequested(true)
    }

    requestAnimationFrame(tick)
  }

  /* =============================================================================================
     [24] TELEGRAM BACK BUTTON
  ============================================================================================= */

  useEffect(() => {
    const tg = (window as any)?.Telegram?.WebApp
    if (!tg?.BackButton) return

    try {
      tg.ready()
    } catch {}

    const onBack = () => {
      if (view === 'card_day_prep' || view === 'three_cards_prep' || view === 'past_present_future_prep' || view === 'decision_prep' || view === 'photo_analysis') {
        backHome()
        return
      }
    }

    const shouldShow = view === 'card_day_prep' || view === 'three_cards_prep' || view === 'past_present_future_prep' || view === 'decision_prep' || view === 'photo_analysis'

    try {
      tg.BackButton.offClick(onBack)
    } catch {}

    try {
      if (shouldShow) tg.BackButton.show()
      else tg.BackButton.hide()
    } catch {}

    try {
      if (shouldShow) tg.BackButton.onClick(onBack)
    } catch {}

    return () => {
      try {
        tg.BackButton.offClick(onBack)
      } catch {}
    }
  }, [view])


  /* =============================================================================================
     [25] FULLSCREEN
  ============================================================================================= */

  useEffect(() => {
    const tg = (window as any)?.Telegram?.WebApp
    if (!tg) return

    const platform = String(tg.platform || '').toLowerCase()
    const isMobile = platform.includes('android') || platform.includes('ios')

    try {
      tg.ready()
    } catch {}

    if (isMobile) {
      try {
        tg.requestFullscreen?.()
      } catch {}
      try {
        tg.sendEvent?.('web_app_request_fullscreen')
      } catch {}
      return
    }

    try {
      tg.exitFullscreen?.()
    } catch {}
    try {
      tg.sendEvent?.('web_app_exit_fullscreen')
    } catch {}
  }, [])

  /* =============================================================================================
     [26] RENDER
  ============================================================================================= */

  return (
    <div className="app" ref={appRef}>
      <canvas className="stars-canvas" ref={starsCanvasRef} aria-hidden="true" />
      <canvas className="comets-canvas" ref={cometsCanvasRef} aria-hidden="true" />

{/* AUTH OVERLAY */}
{authStatus === 'loading' && (
  <div
    style={{
      position: 'fixed',
      inset: 0,
      display: 'grid',
      placeItems: 'center',
      zIndex: 9999,
      background: 'rgba(6, 8, 18, 0.62)',
      backdropFilter: 'blur(10px)',
      WebkitBackdropFilter: 'blur(10px)',
    }}
  >
    <div style={{ textAlign: 'center', padding: 18, maxWidth: 320 }}>
      <div
        style={{
          width: 34,
          height: 34,
          borderRadius: '50%',
          border: '3px solid rgba(255,255,255,0.25)',
          borderTopColor: 'rgba(255,255,255,0.9)',
          margin: '0 auto 12px',
          animation: 'spin 900ms linear infinite',
        }}
        aria-hidden="true"
      />
      <div style={{ fontSize: 14, opacity: 0.9 }}>Авторизация…</div>
      <div style={{ fontSize: 12, opacity: 0.7, marginTop: 6 }}>Проверяем Telegram и загружаем профиль</div>
    </div>
  </div>
)}

{authStatus === 'error' && (
  <div
    style={{
      position: 'fixed',
      inset: 0,
      display: 'grid',
      placeItems: 'center',
      zIndex: 9999,
      background: 'rgba(6, 8, 18, 0.78)',
      backdropFilter: 'blur(12px)',
      WebkitBackdropFilter: 'blur(12px)',
      padding: 18,
    }}
  >
    <div
      style={{
        width: '100%',
        maxWidth: 420,
        borderRadius: 18,
        padding: 18,
        background: 'rgba(255,255,255,0.08)',
        border: '1px solid rgba(255,255,255,0.14)',
        boxShadow: '0 18px 60px rgba(0,0,0,0.35)',
      }}
    >
      <div style={{ fontWeight: 700, marginBottom: 8 }}>Нужна авторизация</div>
      <div style={{ fontSize: 13, opacity: 0.85, lineHeight: 1.35 }}>{authError || 'Ошибка авторизации.'}</div>

      <div style={{ display: 'flex', gap: 10, marginTop: 14, flexWrap: 'wrap' }}>
        <button
          type="button"
          className="glassbtn"
          onClick={() => {
            try {
              localStorage.removeItem('jwt')
            } catch {}
            setToken(null)
          }}
        >
          Повторить
        </button>

        <button
          type="button"
          className="glassbtn"
          onClick={() => {
            const tg = (window as any)?.Telegram?.WebApp
            try {
              tg?.close?.()
            } catch {}
          }}
        >
          Закрыть
        </button>
      </div>
    </div>
  </div>
)}

      <div className="content">
        {view === 'home' && (
          <>
            <h1>AI Tarot</h1>
            <p>Мудрость карт и искусственного интеллекта</p>

            {/* NAV: Главная / История / Профиль */}
            <div
              className={`seg navseg ${navIsBumping ? 'is-bump' : ''}`}
              data-bump={navBump}
              style={{ ['--i' as any]: navActiveIndex }}
              role="tablist"
              aria-label="Навигация"
            >
              <div className="seg__pill" aria-hidden="true" />
              <button
                type="button"
                className={`seg__btn ${navTab === 'main' ? 'is-active' : ''}`}
                onClick={() => onPickNav('main')}
                role="tab"
                aria-selected={navTab === 'main'}
              >
                Главная
              </button>
              <button
                type="button"
                className={`seg__btn ${navTab === 'history' ? 'is-active' : ''}`}
                onClick={() => onPickNav('history')}
                role="tab"
                aria-selected={navTab === 'history'}
              >
                История
              </button>
              <button
                type="button"
                className={`seg__btn ${navTab === 'profile' ? 'is-active' : ''}`}
                onClick={() => onPickNav('profile')}
                role="tab"
                aria-selected={navTab === 'profile'}
              >
                Профиль
              </button>
            </div>

            {/* PAGES: slide left/right */}
            <div className="nav-pages" data-dir={navDir} style={{ ['--pi' as any]: navActiveIndex }}>
              <div className="nav-track">
                <div className="nav-page" data-page="main">
                  <div className="card-day card-day--sun" role="button" tabIndex={0} onClick={openCardDay} onKeyDown={(e) => e.key === 'Enter' && openCardDay()}>
                    <div className="card-day__rim" aria-hidden="true" />
                    <div className="card-day__spark" aria-hidden="true" />
                    <div className="card-day__text">
                      <div className="card-day__title">Карта дня</div>
                      <div className="card-day__subtitle">
                        <span>Ежедневное руководство</span>
                        <span>от Вселенной</span>
                      </div>
                    </div>
                    <div className="card-day__media" aria-hidden="true">
                      <img className="card-day__img" src={cardDayIcon} alt="" />
                    </div>
                  </div>

                  <div className="card-day card-day--photo" role="button" tabIndex={0} onClick={openPhotoAnalysis} onKeyDown={(e) => e.key === 'Enter' && openPhotoAnalysis()}>
                    <div className="card-day__rim" aria-hidden="true" />
                    <div className="card-day__spark" aria-hidden="true" />
                    <div className="card-day__text">
                      <div className="card-day__title">Анализ расклада</div>
                      <div className="card-day__subtitle">
                        <span>Сфотографируйте свои карты</span>
                        <span>для AI анализа</span>
                      </div>
                    </div>
                    <div className="card-day__media" aria-hidden="true">
                      <img className="card-day__img" src={cameraIcon} alt="" />
                    </div>
                  </div>

                  <h2 className="home-section-title">Задайте ваш вопрос</h2>

                  <div
                    className={`ask-wrap ${attnStage === 'question' ? 'is-attn' : ''}`}
                    data-attn={attnStage === 'question' ? attnNonce : undefined}
                    ref={askWrapRef}
                  >
                    <div className="ask-glass">
                      <textarea
                        className="ask-input"
                        value={question}
                        onChange={(e) => setQuestion(e.target.value)}
                        placeholder="Что вас беспокоит? О чем хотели бы узнать?"
                        rows={2}
                      />
                      <button
                        type="button"
                        className={`ask-mic ${isRecording ? 'recording' : ''}`}
                        onClick={toggleRecording}
                        aria-label={isRecording ? 'Остановить запись' : 'Начать запись'}
                        title={isRecording ? 'Остановить запись' : 'Записать голосом'}
                      >
                        <img className="ask-mic__icon" src={micIcon} alt="" aria-hidden="true" />
                      </button>
                    </div>

                    <div className={`ask-hint ${isRecording ? 'is-visible' : ''}`}>Идёт запись… нажмите ещё раз, чтобы остановить</div>
                  </div>

                  <h2 className="home-section-title">Выберите категорию вопроса</h2>

                  <div
                    className={`seg ${isBumping ? 'is-bump' : ''}`}
                    data-bump={bump}
                    style={{
                      ['--i' as any]: activeIndex,
                      ['--from' as any]: prevIndex,
                    }}
                    role="tablist"
                    aria-label="Выбор темы"
                  >
                    <svg className="seg__svg" aria-hidden="true">
                      <filter id="seg-goo">
                        <feGaussianBlur in="SourceGraphic" stdDeviation="8" result="blur" />
                        <feColorMatrix
                          in="blur"
                          mode="matrix"
                          values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 18 -7"
                          result="goo"
                        />
                        <feComposite in="SourceGraphic" in2="goo" operator="atop" />
                      </filter>
                    </svg>

                    <div className="seg__pill" aria-hidden="true" />

                    {TOPICS.map((t) => (
                      <button
                        key={t.id}
                        type="button"
                        className={`seg__btn ${topic === t.id ? 'is-active' : ''}`}
                        onClick={() => onPickTopic(t.id)}
                        role="tab"
                        aria-selected={topic === t.id}
                      >
                        {t.label}
                      </button>
                    ))}
                  </div>

                  <h2 className="home-section-title">Выберите тип расклада</h2>

                  <div className={`spread-list ${shouldAttnSpreads ? 'is-attn' : ''}`} ref={spreadListRef}>
                    {SPREADS.map((s) => {
                      const isActive = spread === s.id
                      return (
                        <div
                          key={s.id}
                          className={`spread-card ${SPREAD_THEME_CLASSES[s.id]} ${isActive ? 'is-active' : ''}`}
                          role="button"
                          tabIndex={0}
                          onClick={() => setSpread(s.id)}
                          onKeyDown={(e) => e.key === 'Enter' && setSpread(s.id)}
                        >
                          <div className="spread-icon__svg" aria-hidden="true">
                            {SPREAD_ICON_IMAGES[s.id] ? <img src={SPREAD_ICON_IMAGES[s.id]} alt="" /> : <SpreadIcon kind={s.icon} />}
                          </div>

                          <div className="spread-body">
                            <div className="spread-title">{s.title}</div>
                            <div className="spread-subtitle">{s.subtitle}</div>
                            <div className="spread-meta">{s.cards}</div>
                          </div>
                        </div>
                      )
                    })}
                  </div>

                  <button
                    ref={btnRef}
                    type="button"
                    className={`glass-cta glass-cta--hero ${pressed ? 'pressed' : ''} ${ctaError ? 'is-error' : ''}`}
                    onPointerDown={onGlassPointerDown}
                    onPointerUp={onGlassPointerUp}
                    onPointerCancel={onGlassPointerUp}
                    onPointerLeave={onGlassPointerUp}
                    onClick={onBeginReading}
                  >
                    <span className="glass-cta__inner">
                      <span className="glass-cta__rim" aria-hidden="true" />
                      <span className="glass-cta__icon">
                        <img src={buttonIcon} alt="" />
                      </span>
                      <span className="glass-cta__text">Начать расклад</span>
                      <span className="glass-cta__spark" aria-hidden="true" />
                    </span>
                  </button>
                </div>

                <div className="nav-page" data-page="history">
                  <div className="history-wrap">

                    {!token && (
                      <div className="history-empty">История доступна после входа через Telegram.</div>
                    )}

                    {token && historyLoading && <div className="history-loading">Загружаем историю...</div>}

                    {token && !!historyError && <div className="history-error">{historyError}</div>}

                    {token && !historyLoading && !historyError && history.length === 0 && (
                      <div className="history-empty">
                        Ваша история только начинается...
                      </div>
                    )}

                    {token && history.length > 0 && (
                      <div className="spread-list">
                        {history.map((it) => {
                          const idx = clamp(it.card_index ?? 0, 0, 77)
                          const img = FRONT_CARD_URLS[idx] || backCardImg
                          const topicLabel = TOPICS.find((t) => t.id === (it.topic as any))?.label || it.topic
                          const isCardDay = it.kind === 'card_of_day'
                          const title = isCardDay
                            ? (it.card_name || 'Карта дня')
                            : (SPREAD_HISTORY_LABELS[it.spread_type] || 'Расклад')
                          const subtitle = isCardDay
                            ? `${topicLabel}${it.question ? ` • ${it.question}` : ''}`
                            : `${topicLabel}${it.question ? ` • ${it.question}` : ''}`
                          const when = (() => {
                            const d = new Date(it.created_at)
                            if (!isFinite(d.getTime())) return isCardDay ? it.day_key : ''
                            return d.toLocaleString('ru-RU', {
                              day: '2-digit',
                              month: '2-digit',
                              year: 'numeric',
                              hour: '2-digit',
                              minute: '2-digit',
                            })
                          })()
                          const metaPrefix = isCardDay
                            ? 'Карта дня'
                            : `${it.cards_count || 0} карт`

                          const handleOpen = () => {
                            if (!isCardDay) return
                            openCardDayFromHistory(it)
                          }

                          return (
                            <div
                              key={`${it.kind}:${it.created_at}:${it.card_index}:${it.card_name}`}
                              className="spread-card spread-card--history"
                              role={isCardDay ? 'button' : 'article'}
                              tabIndex={isCardDay ? 0 : -1}
                              onClick={isCardDay ? handleOpen : undefined}
                              onKeyDown={isCardDay ? (e) => e.key === 'Enter' && handleOpen() : undefined}
                              aria-label={isCardDay ? 'Открыть карту дня из истории' : 'Элемент истории расклада'}
                            >
                              <div className="history-card-media" aria-hidden="true">
                                <img className="history-card-image" src={img} alt="" />
                              </div>

                              <div className="spread-body">
                                <div className="spread-title">{title}</div>
                                <div className="spread-subtitle">
                                  {subtitle}
                                </div>
                                <div className="spread-meta">
                                  {metaPrefix}
                                  {when ? ` • ${when}` : ''}
                                </div>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </div>
                </div>


                <div className="nav-page" data-page="profile">

<div
  className="card-day_2"
  style={{
    display: 'flex',
    flexDirection: 'column',
    cursor: 'default',
    padding: 16,
    borderRadius: 18,
    background: 'rgba(255,255,255,0.06)',
    border: '1px solid rgba(255,255,255,0.12)',
    boxShadow: '0 18px 60px rgba(0,0,0,0.25)',
  }}
>
  <div style={{ display: 'flex', alignItems: 'center', gap: 14,flexDirection: 'column', }}>
    <div
      style={{
        width: 96,
        height: 96,
        borderRadius: 999,
        overflow: 'hidden',
        background: 'rgba(255,255,255,0.10)',
        border: '1px solid rgba(255,255,255,0.16)',
        flex: '0 0 auto',
      }}
      aria-hidden={!user?.photo_url}
    >
      {user?.photo_url ? (
        <img
          src={user.photo_url}
          alt=""
          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
        />
      ) : (
        <div
          style={{
            width: '100%',
            height: '100%',
            display: 'grid',
            placeItems: 'center',
            fontWeight: 700,
            opacity: 0.85,
          }}
        >
          {(user?.first_name?.[0] || user?.username?.[0] || 'U').toUpperCase()}
        </div>
      )}
    </div>

    <div style={{ minWidth: 0 }}>
      <div className="card-day__title" style={{ fontSize: 18, lineHeight: 1.15 }}>
        {user?.first_name || user?.last_name
          ? `${user?.first_name || ''}${user?.last_name ? ` ${user.last_name}` : ''}`.trim()
          : 'Профиль'}
      </div>

      <div className="card-day__subtitle" style={{ marginTop: 4 }}>
        <span style={{ opacity: 0.9 }}>
          {user?.username ? `@${user.username}` : 'username не указан'}
        </span>
      </div>
    </div>
  </div>

  <div style={{ marginTop: 14, display: 'grid', gap: 8 }}>
    <div style={{ fontSize: 12, opacity: 0.82, lineHeight: 1.35 }}>
      Мы идентифицируем вас по данным Telegram. Карта дня доступна 1 раз в сутки,
      остальные расклады: {billing?.free_limit ?? 5} бесплатных в месяц, далее по оплате.
    </div>

    <div style={{ display: 'grid', gap: 6, marginTop: 2 }}>
      <div style={{ fontSize: 12, opacity: 0.9 }}>
        Бесплатно в этом месяце: {Math.max(0, Number(billing?.free_left ?? 0))} из {billing?.free_limit ?? 5}
      </div>
      <div style={{ fontSize: 12, opacity: 0.9 }}>
        Платный баланс: {Math.max(0, Number(billing?.paid_readings_balance ?? 0))} раскладов
      </div>
      <div style={{ fontSize: 12, opacity: 0.9 }}>
        Подписка:{' '}
        {billing?.has_active_subscription
          ? `активна до ${formatRuDate(billing?.subscription_until)}`
          : 'не активна'}
      </div>
      <a
        href={BOT_PAYMENT_URL}
        target="_blank"
        rel="noreferrer"
        style={{ fontSize: 12, opacity: 0.95, color: 'rgba(255,255,255,0.9)', textDecoration: 'underline' }}
      >
        Открыть бота для оплаты
      </a>
      <div style={{ fontSize: 12, opacity: 0.9 }}>Telegram подключен</div>
    </div>
  </div>
</div>
                </div>
              </div>
            </div>
          </>
        )}


        {view === 'photo_analysis' && (
          <>
            <h1>AI Tarot</h1>
            <p>AI анализ фото расклада</p>

            <div className="photo-page">


              {/* скрытые инпуты */}
              <input
                ref={galleryInputRef}
                className="photo-input"
                type="file"
                accept="image/*"
                onChange={onPhotoInputChange}
              />
              <input
                ref={cameraInputRef}
                className="photo-input"
                type="file"
                accept="image/*"
                capture="environment"
                onChange={onPhotoInputChange}
              />

              {!photoPreviewUrl ? (
                <div className="photo-guide" aria-label="Советы для съёмки">
                  <div className="photo-guide__title">Советы для точного анализа</div>
                  <ul className="photo-guide__list">
                    <li>Снимайте расклад сверху и целиком.</li>
                    <li>Используйте ровный свет без бликов и теней.</li>
                    <li>Оставьте небольшой отступ вокруг карт в кадре.</li>
                  </ul>
                </div>
              ) : null}

              {photoPreviewUrl ? (
                <div className="photo-preview" aria-label="Предпросмотр фото">
                  <img src={photoPreviewUrl} alt="Фото расклада" />
                </div>
              ) : (
                <div className="photo-placeholder" aria-label="Фото не выбрано">
                  <div className="photo-placeholder__icon" aria-hidden="true">
                    <img src={cameraIcon} alt="" />
                  </div>
                  <div className="photo-placeholder__text">Выберите или сделайте фото расклада</div>
                  <div className="photo-placeholder__sub">После выбора фото этот экран останется в привычном вам виде.</div>
                </div>
              )}

              <div className={`photo-actions ${photoPreviewUrl ? 'is-compact' : ''}`}>
                <button
                  type="button"
                  className="glass-cta mini-cta"
                  onClick={() => galleryInputRef.current?.click()}
                  disabled={photoStatus === 'uploading'}
                >
                  <span className="glass-cta__inner">
                    <span className="glass-cta__rim" aria-hidden="true" />
                    <span className="glass-cta__text">Выбрать из галереи</span>
                    <span className="glass-cta__spark" aria-hidden="true" />
                  </span>
                </button>

                <button
                  type="button"
                  className="glass-cta mini-cta"
                  onClick={() => cameraInputRef.current?.click()}
                  disabled={photoStatus === 'uploading'}
                >
                  <span className="glass-cta__inner">
                    <span className="glass-cta__rim" aria-hidden="true" />
                    <span className="glass-cta__text">Сделать фото</span>
                    <span className="glass-cta__spark" aria-hidden="true" />
                  </span>
                </button>
              </div>


              {/* вопрос */}
              <div className="photo-question">
                <div className="photo-question__title">Ваш вопрос</div>
                <div className={`ask-wrap ${isRecording ? 'is-attn' : ''}`}>
                  <div className="ask-glass">
                    <textarea
                      className="ask-input"
                      value={question}
                      onChange={(e) => setQuestion(e.target.value)}
                      placeholder="Вопрос (необязательно). Например: «Что мне важно понять в отношениях?»"
                      rows={3}
                    />
                    <button
                      type="button"
                      className={`ask-mic ${isRecording ? 'recording' : ''}`}
                      onClick={toggleRecording}
                      aria-label={isRecording ? 'Остановить запись' : 'Начать запись'}
                      title={isRecording ? 'Остановить запись' : 'Записать голосом'}
                    >
                      <img className="ask-mic__icon" src={micIcon} alt="" aria-hidden="true" />
                    </button>
                  </div>
                  <div className={`ask-hint ${isRecording ? 'is-visible' : ''}`}>Идёт запись… нажмите ещё раз, чтобы остановить</div>
                </div>
              </div>

              {/* темы (используем тот же переключатель) */}
              <div
                className={`seg ${isBumping ? 'is-bump' : ''}`}
                data-bump={bump}
                style={{
                  ['--i' as any]: activeIndex,
                  ['--from' as any]: prevIndex,
                }}
                role="tablist"
                aria-label="Выбор темы"
              >
                <svg className="seg__svg" aria-hidden="true">
                  <filter id="seg-goo-photo">
                    <feGaussianBlur in="SourceGraphic" stdDeviation="8" result="blur" />
                    <feColorMatrix
                      in="blur"
                      mode="matrix"
                      values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 18 -7"
                      result="goo"
                    />
                    <feComposite in="SourceGraphic" in2="goo" operator="atop" />
                  </filter>
                </svg>

                <div className="seg__pill" aria-hidden="true" />

                {TOPICS.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    className={`seg__btn ${topic === t.id ? 'is-active' : ''}`}
                    onClick={() => onPickTopic(t.id)}
                    role="tab"
                    aria-selected={topic === t.id}
                  >
                    {t.label}
                  </button>
                ))}
              </div>

              <div className="photo-cta">
                <button
                  type="button"
                  className={`glass-cta ${photoStatus === 'uploading' ? 'is-loading' : ''}`}
                  onClick={runPhotoAnalysis}
                  disabled={photoStatus === 'uploading'}
                >
                  <span className="glass-cta__inner">
                    <span className="glass-cta__rim" aria-hidden="true" />
                    <span className="glass-cta__text">{photoStatus === 'uploading' ? 'Анализируем…' : 'Проанализировать фото'}</span>
                    <span className="glass-cta__spark" aria-hidden="true" />
                  </span>
                </button>

              </div>

              {photoError ? <div className="photo-error">{photoError}</div> : null}

              {photoResult && (
                <div className="photo-result">
                  <div className="result-card">
                    <div className="result-card__title">Результат AI анализа</div>
                    <div className="result-card__name">Фото расклада</div>

                    <div className="result-card__scroll">
                      {photoResult.description
                        ? <MarkdownText text={photoResult.description || ''} />
                        : (
                          <p style={{ marginTop: 0, marginBottom: 0, opacity: 0.8 }}>
                            Ответ пустой. Проверьте бэкенд/LLM и попробуйте ещё раз.
                          </p>
                        )}

                      {Array.isArray(photoResult.cards) && photoResult.cards.length > 0 ? (
                        <div style={{ marginTop: 16 }}>
                          <p style={{ marginTop: 0, marginBottom: 10, opacity: 0.85 }}>
                            <b>Распознанные карты:</b>
                          </p>

                          {photoResult.cards.map((c: any, idx: number) => (
                            <div key={idx} style={{ marginTop: idx === 0 ? 0 : 12 }}>
                              <p style={{ marginTop: 0, marginBottom: 6 }}>
                                <b>
                                  {c.position ? `${c.position}: ` : ''}
                                  {c.card_name || c.title || 'Карта'}
                                  {c.is_reversed ? ' (перевёрнутая)' : ''}
                                </b>
                              </p>

                              <MarkdownText text={String(c.meaning || '') || ''} />
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  </div>
                </div>
              )}
            </div>
          </>
        )}

        {view === 'card_day_prep' && (
          <>
            <h1>AI Tarot</h1>
            <p ref={subtitleRef}>Мудрость карт и искусственного интеллекта</p>
            {cardDayLoading && (
              <div
                style={{
                  marginTop: 14,
                  borderRadius: 16,
                  padding: 14,
                  background: 'rgba(255,255,255,0.06)',
                  border: '1px solid rgba(255,255,255,0.12)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: 10,
                }}
              >
                <div
                  style={{
                    width: 16,
                    height: 16,
                    borderRadius: '50%',
                    border: '2px solid rgba(255,255,255,0.25)',
                    borderTopColor: 'rgba(255,255,255,0.9)',
                    animation: 'spin 900ms linear infinite',
                    flex: '0 0 auto',
                  }}
                  aria-hidden="true"
                />
              </div>
            )}
            {/* активный spread-card */}
            {!isResult && !cardDayLoading && (
              <div className="spread-list">
                <div ref={spreadActiveRef} className="spread-card spread-card--sun is-active" role="button" tabIndex={0}>
                  <div className="spread-icon__svg" aria-hidden="true">
                    <img src={cardDayIcon} alt="" />
                  </div>

                  <div className="spread-body">
                    <div className="spread-title">Карта дня</div>
                    <div className="spread-subtitle">Ежедневное руководство</div>
                    <div className="spread-meta">1 карта</div>
                  </div>
                </div>
              </div>
            )}

            <PremiumFlipCard
              key={pflipMountKey}
              frontUrls={SHUFFLE_FRONT_URLS}
              backUrl={backCardImg}
              active={!shakenOnce && !stopRequested && !cardDayLoading}
              durationMs={2600}
              intensity={cardDayLoading ? 0 : shakeEnabled ? shuffleProgress : 0}
              clickable={shakenOnce}
              onClick={revealCard}
              ariaLabel="Перевернуть карту"
              stopAtBack={stopRequested}
              onStoppedAtBack={onStoppedAtBack}
              className={`${shakenOnce ? 'is-done' : ''} ${cardRevealed ? 'is-revealed' : ''} ${isResult ? 'is-top' : ''}`.trim()}
              scale={pflipScale}
              top={pflipTop}
              onFrontChange={setSelectedFrontUrl}
              // ✅ во время загрузки — рубашка, без рандомных фронтов
              lockFront={cardDayLoading || stopRequested || shakenOnce || cardRevealed}
              lockedFrontUrl={cardDayLoading ? backCardImg : dailyFrontUrl || backCardImg}
            />

            {!isResult && !cardDayLoading && (
              <>
                {!shakeEnabled ? (
                  <div className="bottom-panel bottom-panel--input" ref={bottomPanelRef}>
                    <div className={`ask-wrap ${isRecording ? 'is-attn' : ''}`} ref={askWrapRef}>
                      <div className="ask-glass">
                        <textarea
                          className="ask-input"
                          value={question}
                          onChange={(e) => setQuestion(e.target.value)}
                          placeholder="Что вас беспокоит? О чем хотели бы узнать?"
                          rows={2}
                        />
                        <div className={`ask-mic ${isRecording ? 'recording' : ''}`} onClick={toggleRecording} role="button" tabIndex={0}>
                          <img className="ask-mic__icon" src={micIcon} alt="" aria-hidden="true" />
                        </div>
                      </div>
                    </div>
                    {/* [CARD DAY] SWITCHER ТЕМЫ: Отношения / Карьера / Финансы */}
                    <div
                      className={`seg ${isBumping ? 'is-bump' : ''}`}
                      data-bump={bump}
                      style={{
                        ['--i' as any]: activeIndex,
                        ['--from' as any]: prevIndex,
                      }}
                      role="tablist"
                      aria-label="Выбор темы"
                    >
                      <svg className="seg__svg" aria-hidden="true">
                        <filter id="seg-goo">
                          <feGaussianBlur in="SourceGraphic" stdDeviation="8" result="blur" />
                          <feColorMatrix
                            in="blur"
                            mode="matrix"
                            values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 18 -7"
                            result="goo"
                          />
                          <feComposite in="SourceGraphic" in2="goo" operator="atop" />
                        </filter>
                      </svg>

                      <div className="seg__pill" aria-hidden="true" />

                      {TOPICS.map((t) => (
                        <button
                          key={t.id}
                          type="button"
                          className={`seg__btn ${topic === t.id ? 'is-active' : ''}`}
                          onClick={() => onPickTopic(t.id)}
                          role="tab"
                          aria-selected={topic === t.id}
                        >
                          {t.label}
                        </button>
                      ))}
                    </div>

                    <button type="button" className="glass-cta" onClick={enableShake}>
                      <span className="glass-cta__inner">
                        <span className="glass-cta__rim" aria-hidden="true" />
                        <span className="glass-cta__text">Продолжить</span>
                        <span className="glass-cta__spark" aria-hidden="true" />
                      </span>
                    </button>
                  </div>
                ) : (
                  <div className="bottom-panel bottom-panel--shake" ref={bottomPanelRef}>
                    {shuffleProgress < 1 ? (
                      <>
                        <div className="shake__badge">
                          <div className="shake__title">Потрясите телефон</div>
                          <div className="shake__sub">Потрясите устройство, чтобы перемешать карты, или нажмите кнопку ниже.</div>
                        </div>

                        <button type="button" className="glass-cta mini-cta" onClick={autoShuffle}>
                          <span className="glass-cta__inner">
                            <span className="glass-cta__rim" aria-hidden="true" />
                            <span className="glass-cta__text">Перемешать автоматически</span>
                            <span className="glass-cta__spark" aria-hidden="true" />
                          </span>
                        </button>
                      </>
                    ) : (
                      <>
                        <div className="shake__badge is-done">
                          <div className="shake__title">Переверните карту</div>
                          <div className="shake__sub">Нажмите на карту — и мы расскажем о её значении.</div>
                        </div>

                        <button type="button" className="glass-cta mini-cta" disabled>
                          <span className="glass-cta__inner">
                            <span className="glass-cta__rim" aria-hidden="true" />
                            <span className="glass-cta__text">Нажмите на карту</span>
                            <span className="glass-cta__spark" aria-hidden="true" />
                          </span>
                        </button>
                      </>
                    )}
                  </div>
                )}
              </>
            )}

            {isResult && (
              <div className="result-layout">
                <div className="result-layout__desc">
                  <div className="result-card">
                    <div className="result-card__title">Значение карты</div>
                    <div className="result-card__name">
                      {dailyCardName
                        ? dailyCardName
                        : (selectedFrontUrl.split('/').pop() || 'Карта').replace(/\.(png|jpg|jpeg|webp)$/i, '')}
                    </div>

                    <div className="result-card__scroll">
                      {dailyDayKey ? (
                        <p style={{ opacity: 0.72, marginTop: 0 }}>Карта дня: {dailyDayKey}</p>
                      ) : null}

                      {dailyDesc ? (
                        <MarkdownText text={dailyDesc} />
                      ) : (
                        <p>Описание пока недоступно.</p>
                      )}
                    </div>

                    <div className="result-card__scroll">
                    </div>
                  </div>
                </div>

                <button type="button" className="glass-cta result-back" onClick={backHome}>
                  <span className="glass-cta__inner">
                    <span className="glass-cta__rim" aria-hidden="true" />
                    <span className="glass-cta__text">Вернуться в меню</span>
                    <span className="glass-cta__spark" aria-hidden="true" />
                  </span>
                </button>
              </div>
            )}
          </>
        )}
        {view === 'three_cards_prep' && (
          <>
            <h1>AI Tarot</h1>
            <p>Расклад по 3 картам</p>

            <div className="threepage">

              {/* 1) SETUP */}
              {threeScreen === 'setup' && (
                <>
                  <div className="threecards-row" aria-label="Три карты (рубашка)">
                    {[0, 1, 2].map((i) => (
                      <div key={i} className="threecard">
                        <img src={backCardImg} alt="" />
                      </div>
                    ))}
                  </div>

                  <div className="threeform">
                    <div className="ask-wrap">
                      <div className="ask-glass">
                        <textarea
                          className="ask-input"
                          value={threeQuestion}
                          onChange={(e) => setThreeQuestion(e.target.value)}
                          placeholder="Ваш вопрос…"
                          rows={2}
                        />
                      </div>
                    </div>

                    <div
                      className={`seg seg--threekind ${threeKindIsBumping ? 'is-bump' : ''}`}
                      data-bump={threeKindBump}
                      style={{
                        ['--i' as any]: threeKindActiveIndex,
                        ['--from' as any]: threeKindPrevIndex,
                      }}
                      role="tablist"
                      aria-label="Тип вопроса"
                    >
                      <div className="seg__pill" aria-hidden="true" />
                      {THREE_QKINDS.map((k) => (
                        <button
                          key={k.id}
                          type="button"
                          className={`seg__btn ${threeKind === k.id ? 'is-active' : ''}`}
                          onClick={() => onPickThreeKind(k.id)}
                          role="tab"
                          aria-selected={threeKind === k.id}
                        >
                          {k.label}
                        </button>
                      ))}
                    </div>

                    <button type="button" className="glass-cta" onClick={beginThreeShuffle}>
                      <span className="glass-cta__inner">
                        <span className="glass-cta__rim" aria-hidden="true" />
                        <span className="glass-cta__text">Продолжить</span>
                        <span className="glass-cta__spark" aria-hidden="true" />
                      </span>
                    </button>
                  </div>
                </>
              )}

              {/* 2) SHUFFLE */}
              {threeScreen === 'shuffle' && (
                <>
                  <div
                    className={`three-mix-area ${threeShuffleProgress < 1 ? 'is-shuffling' : 'is-done'}`}
                    aria-label="Перемешивание"
                  >
                    {[0, 1, 2].map((cardIdx) => {
                      const slot = Math.max(0, threeOrder.indexOf(cardIdx)) // 0..2
                      const p = THREE_SLOTS[slot] || THREE_SLOTS[0]

                      return (
                        <div
                          key={cardIdx}
                          className="three-mix-card"
                          style={{
                            ['--x' as any]: `${p.x}px`,
                            ['--y' as any]: `${p.y}px`,
                            ['--r' as any]: `${p.r}deg`,
                            ['--s' as any]: `${p.s}`,
                            zIndex: p.z,
                          }}
                        >
                          <img src={backCardImg} alt="" />
                        </div>
                      )
                    })}
                  </div>


                  <div className="bottom-panel bottom-panel--shake">
                    {threeShuffleProgress < 1 ? (
                      <>
                        <div className="shake__badge">
                          <div className="shake__title">Потрясите телефон</div>
                          <div className="shake__sub">
                            Карты будут перемешиваться при тряске. Можно и автоматически — кнопкой ниже.
                          </div>
                        </div>

                        {needsMotionPermission && (
                          <button type="button" className="glassbtn" onClick={requestMotion}>
                            Разрешить датчики
                          </button>
                        )}

                        <div className="threehint">Прогресс: {Math.round(threeShuffleProgress * 100)}%</div>

                        <button type="button" className="glass-cta mini-cta" onClick={autoShuffleThree}>
                          <span className="glass-cta__inner">
                            <span className="glass-cta__rim" aria-hidden="true" />
                            <span className="glass-cta__text">Перемешать автоматически</span>
                            <span className="glass-cta__spark" aria-hidden="true" />
                          </span>
                        </button>
                      </>
                    ) : (
                      <>
                        <div className="shake__badge is-done">
                          <div className="shake__title">Открываем карты…</div>
                          <div className="shake__sub">Сейчас покажем 3 карты и интерпретацию.</div>
                        </div>
                      </>
                    )}
                  </div>
                </>
              )}

              {/* 3) RESULT */}
              {threeScreen === 'result' && (
                <>
                  <div className="threecards-row" aria-label="Три карты (результат)">
                    {(threeCards.length ? threeCards : []).map((c, i) => (
                      <div key={`${c.idx}-${i}`} className="threecard">
                        <img className={c.isReversed ? 'is-reversed' : ''} src={c.url || backCardImg} alt={c.name} />
                      </div>
                    ))}
                  </div>

                  {/* ✅ один большой блок результата со скроллом, как на "Карта дня" */}
                  <div className="three-result">
                    <div className="result-layout__desc">
                      <div className="result-card">
                        <div className="result-card__title">Значение расклада</div>
                        <div className="result-card__name">Расклад по 3 картам</div>

                        <div className="result-card__scroll">
                          {threeDayKey ? (
                            <p style={{ opacity: 0.72, marginTop: 0 }}>Дата расклада: {threeDayKey}</p>
                          ) : null}

                          {threeQuestion ? (
                            <p style={{ opacity: 0.85, marginTop: 10, marginBottom: 0 }}>
                              <b>Вопрос:</b> {threeQuestion}
                            </p>
                          ) : null}

                          <p style={{ opacity: 0.82, marginTop: 10, marginBottom: 0 }}>
                            <b>Тип:</b>{' '}
                            {threeKind === 'yesno' ? 'Да / Нет' : threeKind === 'advice' ? 'Совет' : 'Открытый вопрос'}
                          </p>

                          <div style={{ height: 10 }} />

                          {!threeShowMeaning ? (
                            <p style={{ opacity: 0.8, marginTop: 10 }}>Карты раскрываются…</p>
                          ) : (
                            <>
                              {/* Loading indicator while waiting for backend */}
                              {threeLoading && (
                                <p style={{ opacity: 0.8, marginTop: 10 }}>
                                  Идёт анализ расклада...
                                </p>
                              )}

                              {/* Description from backend */}
                              {!!threeDesc && (
                                <div style={{ marginTop: 10 }}>
                                  <MarkdownText text={threeDesc} />
                                  <div style={{ height: 10 }} />
                                </div>
                              )}

                              {threeCards.map((c, idx) => (
                                <div key={`${c.role}-${idx}`} style={{ marginTop: idx === 0 ? 0 : 14 }}>
                                  <p style={{ marginTop: 0, marginBottom: 8 }}>
                                    <b>
                                      {c.role}: {c.name}
                                    </b>
                                  </p>

                                  <MarkdownText text={c.text || ''} />
                                </div>
                              ))}
                            </>
                          )}
                        </div>
                      </div>
                    </div>

                    <button type="button" className="glass-cta" onClick={restartThreeCards}>
                      <span className="glass-cta__inner">
                        <span className="glass-cta__rim" aria-hidden="true" />
                        <span className="glass-cta__text">Новый расклад</span>
                        <span className="glass-cta__spark" aria-hidden="true" />
                      </span>
                    </button>

                    <button type="button" className="glass-cta result-back" onClick={backHome}>
                      <span className="glass-cta__inner">
                        <span className="glass-cta__rim" aria-hidden="true" />
                        <span className="glass-cta__text">Вернуться в меню</span>
                        <span className="glass-cta__spark" aria-hidden="true" />
                      </span>
                    </button>
                  </div>
                </>
              )}

            </div>
          </>
        )}
        {view === 'past_present_future_prep' && (
          <>
            <h1>AI Tarot</h1>
            <p>Прошлое • Настоящее • Будущее</p>

            <div className="threepage">
              {/* 1) SETUP */}
              {ppfScreen === 'setup' && (
                <>
                  <div className="threecards-row" aria-label="Три карты (рубашка)">
                    {[0, 1, 2].map((i) => (
                      <div key={i} className="threecard">
                        <img src={backCardImg} alt="" />
                      </div>
                    ))}
                  </div>

                  <div className="threeform">
                    <div className="ask-wrap">
                      <div className="ask-glass">
                        <textarea
                          className="ask-input"
                          value={ppfQuestion}
                          onChange={(e) => setPpfQuestion(e.target.value)}
                          placeholder="Ваш вопрос…"
                          rows={2}
                        />
                      </div>
                    </div>

                    <div
                      className={`seg seg--threekind ${ppfFocusIsBumping ? 'is-bump' : ''}`}
                      data-bump={ppfFocusBump}
                      style={{
                        ['--i' as any]: ppfFocusActiveIndex,
                        ['--from' as any]: ppfFocusPrevIndex,
                      }}
                      role="tablist"
                      aria-label="Фокус"
                    >
                      <div className="seg__pill" aria-hidden="true" />
                      {PPF_FOCUS.map((k) => (
                        <button
                          key={k.id}
                          type="button"
                          className={`seg__btn ${ppfFocus === k.id ? 'is-active' : ''}`}
                          onClick={() => onPickPpfFocus(k.id)}
                          role="tab"
                          aria-selected={ppfFocus === k.id}
                        >
                          {k.label}
                        </button>
                      ))}
                    </div>

                    <button type="button" className="glass-cta" onClick={beginPpfShuffle}>
                      <span className="glass-cta__inner">
                        <span className="glass-cta__rim" aria-hidden="true" />
                        <span className="glass-cta__text">Продолжить</span>
                        <span className="glass-cta__spark" aria-hidden="true" />
                      </span>
                    </button>
                  </div>
                </>
              )}



              {/* 2) SHUFFLE */}
              {ppfScreen === 'shuffle' && (
                <>
                  <div className={`three-mix-area ${ppfShuffleProgress < 1 ? 'is-shuffling' : 'is-done'}`} aria-label="Перемешивание">
                    {[0, 1, 2].map((cardIdx) => {
                      const slot = Math.max(0, ppfOrder.indexOf(cardIdx))
                      const p = THREE_SLOTS[slot] || THREE_SLOTS[0]

                      return (
                        <div
                          key={cardIdx}
                          className="three-mix-card"
                          style={{
                            ['--x' as any]: `${p.x}px`,
                            ['--y' as any]: `${p.y}px`,
                            ['--r' as any]: `${p.r}deg`,
                            ['--s' as any]: `${p.s}`,
                            zIndex: p.z,
                          }}
                        >
                          <img src={backCardImg} alt="" />
                        </div>
                      )
                    })}
                  </div>

                  <div className="bottom-panel bottom-panel--shake">
                    {ppfShuffleProgress < 1 ? (
                      <>
                        <div className="shake__badge">
                          <div className="shake__title">Потрясите телефон</div>
                          <div className="shake__sub">Карты будут перемешиваться при тряске. Можно и автоматически — кнопкой ниже.</div>
                        </div>

                        {needsMotionPermission && (
                          <button type="button" className="glassbtn" onClick={requestMotion}>
                            Разрешить датчики
                          </button>
                        )}

                        <div className="threehint">Прогресс: {Math.round(ppfShuffleProgress * 100)}%</div>

                        <button type="button" className="glass-cta mini-cta" onClick={autoShufflePpf}>
                          <span className="glass-cta__inner">
                            <span className="glass-cta__rim" aria-hidden="true" />
                            <span className="glass-cta__text">Перемешать автоматически</span>
                            <span className="glass-cta__spark" aria-hidden="true" />
                          </span>
                        </button>
                      </>
                    ) : (
                      <>
                        <div className="shake__badge is-done">
                          <div className="shake__title">Открываем карты…</div>
                          <div className="shake__sub">Сейчас покажем 3 карты и интерпретацию.</div>
                        </div>
                      </>
                    )}
                  </div>
                </>
              )}

              {/* 3) RESULT */}
              {ppfScreen === 'result' && (
                <>
                  <div className="threecards-row" aria-label="Три карты (результат)">
                    {(ppfCards.length ? ppfCards : []).map((c, i) => (
                      <div key={`${c.idx}-${i}`} className="threecard">
                        <img className={c.isReversed ? 'is-reversed' : ''} src={c.url || backCardImg} alt={c.name} />
                      </div>
                    ))}
                  </div>

                  <div className="three-result">
                    <div className="result-layout__desc">
                      <div className="result-card">
                        <div className="result-card__title">Значение расклада</div>

                        <div className="result-card__scroll">
                          {ppfDayKey ? <p style={{ opacity: 0.72, marginTop: 0 }}>Дата расклада: {ppfDayKey}</p> : null}

                          {!!ppfQuestion && (
                            <p style={{ opacity: 0.86, marginTop: 10 }}>
                              <b>Вопрос:</b> {ppfQuestion}
                            </p>
                          )}

                          <p style={{ opacity: 0.86, marginTop: 10 }}>
                            <b>Фокус:</b> {PPF_FOCUS.find((x) => x.id === ppfFocus)?.label || '—'}
                          </p>

                          {/* Loading indicator while waiting for backend */}
                          {ppfLoading && (
                            <p style={{ opacity: 0.8, marginTop: 10 }}>
                              Идет получение ответа от сервера...
                            </p>
                          )}

                          {/* Description from backend */}
                          {!!ppfDesc && (
                            <div style={{ marginTop: 10 }}>
                              <MarkdownText text={ppfDesc} />
                              <div style={{ height: 10 }} />
                            </div>
                          )}

                          {ppfCards.map((c, idx) => (
                            <div key={`${c.role}-${idx}`} style={{ marginTop: idx === 0 ? 0 : 14 }}>
                              <p style={{ marginTop: 0, marginBottom: 8 }}>
                                <b>
                                  {c.role}: {c.name}
                                </b>
                              </p>

                              <MarkdownText text={c.text || ''} />
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>

                    <button type="button" className="glass-cta" onClick={restartPpf}>
                      <span className="glass-cta__inner">
                        <span className="glass-cta__rim" aria-hidden="true" />
                        <span className="glass-cta__text">Новый расклад</span>
                        <span className="glass-cta__spark" aria-hidden="true" />
                      </span>
                    </button>

                    <button type="button" className="glass-cta result-back" onClick={backHome}>
                      <span className="glass-cta__inner">
                        <span className="glass-cta__rim" aria-hidden="true" />
                        <span className="glass-cta__text">Вернуться в меню</span>
                        <span className="glass-cta__spark" aria-hidden="true" />
                      </span>
                    </button>
                  </div>
                </>
              )}
            </div>
          </>
        )}

        {view === 'decision_prep' && (
          <>
            <h1>AI Tarot</h1>
            <p>Принятие решения</p>

            <div className="threepage">
              {/* 1) SETUP */}
              {decisionScreen === 'setup' && (
                <>
                  <div className="threecards-row" aria-label="Две карты (рубашка)">
                    {[0, 1].map((i) => (
                      <div key={i} className="threecard">
                        <img src={backCardImg} alt="" />
                      </div>
                    ))}
                  </div>

                  <div className="threeform">
                    <div className="ask-wrap">
                      <div className="ask-glass">
                        <textarea
                          className="ask-input"
                          value={decisionQuestion}
                          onChange={(e) => setDecisionQuestion(e.target.value)}
                          placeholder="Сформулируйте вопрос и мысленно обозначьте Вариант A и Вариант B…"
                          rows={2}
                        />
                      </div>
                    </div>

                    <div
                      className={`seg seg--ppf ${decisionFocusIsBumping ? 'is-bump' : ''}`}
                      data-bump={decisionFocusBump}
                      style={{
                        ['--i' as any]: decisionFocusActiveIndex,
                        ['--from' as any]: decisionFocusPrevIndex,
                      }}
                      role="tablist"
                      aria-label="Фокус"
                    >
                      <div className="seg__pill" aria-hidden="true" />
                      {DECISION_FOCUS.map((k) => (
                        <button
                          key={k.id}
                          type="button"
                          className={`seg__btn ${decisionFocus === k.id ? 'is-active' : ''}`}
                          onClick={() => onPickDecisionFocus(k.id)}
                          role="tab"
                          aria-selected={decisionFocus === k.id}
                        >
                          {k.label}
                        </button>
                      ))}
                    </div>

                    <button type="button" className="glass-cta" onClick={beginDecisionShuffle}>
                      <span className="glass-cta__inner">
                        <span className="glass-cta__rim" aria-hidden="true" />
                        <span className="glass-cta__text">Продолжить</span>
                        <span className="glass-cta__spark" aria-hidden="true" />
                      </span>
                    </button>
                  </div>
                </>
              )}

              {/* 2) SHUFFLE */}
              {decisionScreen === 'shuffle' && (
                <>
                  <div
                    className={`three-mix-area ${decisionShuffleProgress < 1 ? 'is-shuffling' : 'is-done'}`}
                    aria-label="Перемешивание"
                  >
                    {[0, 1].map((cardIdx) => {
                      const slot = Math.max(0, decisionOrder.indexOf(cardIdx)) // 0..1
                      const p = DECISION_SLOTS[slot] || DECISION_SLOTS[0]

                      return (
                        <div
                          key={cardIdx}
                          className="three-mix-card"
                          style={{
                            ['--x' as any]: `${p.x}px`,
                            ['--y' as any]: `${p.y}px`,
                            ['--r' as any]: `${p.r}deg`,
                            ['--s' as any]: `${p.s}`,
                            zIndex: p.z,
                          }}
                        >
                          <img src={backCardImg} alt="" />
                        </div>
                      )
                    })}
                  </div>

                  <div className="bottom-panel bottom-panel--shake">
                    {decisionShuffleProgress < 1 ? (
                      <>
                        <div className="shake__badge">
                          <div className="shake__title">Потрясите телефон</div>
                          <div className="shake__sub">Карты будут меняться местами при тряске. Можно и автоматически — кнопкой ниже.</div>
                        </div>

                        {needsMotionPermission && (
                          <button type="button" className="glassbtn" onClick={requestMotion}>
                            Разрешить датчики
                          </button>
                        )}

                        <div className="threehint">Прогресс: {Math.round(decisionShuffleProgress * 100)}%</div>

                        <button type="button" className="glass-cta mini-cta" onClick={autoShuffleDecision}>
                          <span className="glass-cta__inner">
                            <span className="glass-cta__rim" aria-hidden="true" />
                            <span className="glass-cta__text">Перемешать автоматически</span>
                            <span className="glass-cta__spark" aria-hidden="true" />
                          </span>
                        </button>
                      </>
                    ) : (
                      <>
                        <div className="shake__badge is-done">
                          <div className="shake__title">Откройте карты</div>
                          <div className="shake__sub">Нажмите кнопку ниже — покажем 2 карты и их значения.</div>
                        </div>

                        <button type="button" className="glass-cta mini-cta" onClick={openDecisionResult}>
                          <span className="glass-cta__inner">
                            <span className="glass-cta__rim" aria-hidden="true" />
                            <span className="glass-cta__text">Открыть карты</span>
                            <span className="glass-cta__spark" aria-hidden="true" />
                          </span>
                        </button>
                      </>
                    )}
                  </div>
                </>
              )}

              {/* 3) RESULT */}
              {decisionScreen === 'result' && (
                <>
                  <div className="threecards-row" aria-label="Две карты (результат)">
                    {(decisionCards.length ? decisionCards : []).map((c, i) => (
                      <div key={`${c.idx}-${i}`} className="threecard">
                        <img className={c.isReversed ? 'is-reversed' : ''} src={c.url || backCardImg} alt={c.name} />
                      </div>
                    ))}
                  </div>

                  <div className="three-result">
                    <div className="result-layout__desc">
                      <div className="result-card">
                        <div className="result-card__title">Значение расклада</div>

                        <div className="result-card__scroll">
                          {decisionDayKey ? <p style={{ opacity: 0.72, marginTop: 0 }}>Расклад: {decisionDayKey}</p> : null}

                          {decisionQuestion ? (
                            <p style={{ marginTop: 10, opacity: 0.86 }}>
                              <b>Вопрос:</b> {decisionQuestion}
                            </p>
                          ) : null}

                          <p style={{ marginTop: 10, opacity: 0.86 }}>
                            <b>Фокус:</b> {DECISION_FOCUS.find((x) => x.id === decisionFocus)?.label || '—'}
                          </p>

                          {/* Loading indicator while waiting for backend */}
                          {decisionLoading && (
                            <p style={{ opacity: 0.8, marginTop: 10 }}>
                              Идет получение ответа от сервера...
                            </p>
                          )}

                          {/* Description from backend */}
                          {!!decisionDesc && (
                            <div style={{ marginTop: 10 }}>
                              <MarkdownText text={decisionDesc} />
                              <div style={{ height: 10 }} />
                            </div>
                          )}

                          {decisionCards.map((c) => (
                            <div key={c.role} style={{ marginTop: 14 }}>
                              <div style={{ fontWeight: 700, marginBottom: 6 }}>
                                {c.role}: {c.name}
                              </div>

                              <MarkdownText text={String(c.text || '')} />
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>

                    <button type="button" className="glass-cta" onClick={restartDecision}>
                      <span className="glass-cta__inner">
                        <span className="glass-cta__rim" aria-hidden="true" />
                        <span className="glass-cta__text">Новый расклад</span>
                        <span className="glass-cta__spark" aria-hidden="true" />
                      </span>
                    </button>

                    <button type="button" className="glass-cta result-back" onClick={backHome}>
                      <span className="glass-cta__inner">
                        <span className="glass-cta__rim" aria-hidden="true" />
                        <span className="glass-cta__text">Вернуться в меню</span>
                        <span className="glass-cta__spark" aria-hidden="true" />
                      </span>
                    </button>
                  </div>
                </>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
