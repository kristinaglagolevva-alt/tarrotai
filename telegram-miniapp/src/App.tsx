/* =================================================================================================
   [1] ИМПОРТЫ
================================================================================================= */

import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import {
  telegramAuth,
  getMe,
  updateMePreferences,
  getBillingStatus,
  createSbpPayment,
  getSbpPaymentStatus,
  getCardOfDayToday,
  createCardOfDay,
  getUnifiedHistory,
  analyzeSpreadPhoto,
  askPhotoFollowup,
  createReading,
} from './api'
import type {
  MeDto,
} from './api'
import SubscriptionManageCard from './features/profile/SubscriptionManageCard'



import micIcon from './assets/icons/microphone.svg'
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

// ✅ Подхватываем только лицевые карты из папок мастей/старших арканов
const frontCardModules = import.meta.glob('./assets/cards/{major,wands,cups,swords,pentacles}/*.{jpg,jpeg,png,webp}', { eager: true }) as Record<
  string,
  { default: string }
>

const FRONT_CARD_ENTRIES = Object.entries(frontCardModules)

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

const hiddenDragPoint = () => ({ x: -10000, y: -10000 })

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

function normalizeInterpretationText(text: string) {
  let out = String(text || '')
  if (!out) return out
  // Guardrail for stale backend responses with a repetitive, non-actionable practical line.
  out = out.replace(
    /Практический вывод:\s*сравните текущее решение с прошлым\s*[—-]\s*именно в этом точка развилки\.?/gi,
    'Практический вывод: выберите один конкретный шаг на сегодня и проверьте результат по факту.',
  )
  return out
}

function MarkdownText({ text, className = '' }: { text?: string; className?: string }) {
  const source = normalizeInterpretationText(String(text || '')).replace(/\r\n?/g, '\n').trim()
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

function InterpretationLoader({ text = 'Получаем интерпретацию' }: { text?: string }) {
  return (
    <div className="interp-loader" role="status" aria-live="polite">
      <div className="interp-loader__ring" aria-hidden="true">
        {Array.from({ length: 12 }).map((_, i) => (
          <span key={i} style={{ ['--i' as any]: i }} />
        ))}
      </div>

      <div className="interp-loader__text">
        {text}
        <span className="interp-loader__dots" aria-hidden="true">
          <span />
          <span />
          <span />
        </span>
      </div>
    </div>
  )
}

function stripDailyQuestionContextSection(text: string, question: string) {
  if (String(question || '').trim()) return String(text || '')
  let out = String(text || '')
  if (!out) return out
  out = out.replace(
    /(^|\n)\s*##\s*Что это значит в контексте вопроса\s*\n[\s\S]*?(?=(\n\s*##\s)|\n\s*\*\*Итог|\Z)/gi,
    '\n',
  )
  out = out.replace(
    /(^|\n)\s*Что это значит в контексте вопроса\s*\n[\s\S]*?(?=(\n\s*##\s)|\n\s*\*\*Итог|\Z)/gi,
    '\n',
  )
  out = out.replace(/\n{3,}/g, '\n\n').trim()
  return out
}

/* =================================================================================================
   [4] КОНФИГ UI (ТЕМЫ / РАСКЛАДЫ)
================================================================================================= */

type Topic = 'relations' | 'career' | 'finance' | 'other'

const TOPICS: { id: Topic; label: string }[] = [
  { id: 'other', label: 'Другое' },
  { id: 'relations', label: 'Отношения' },
  { id: 'career', label: 'Карьера' },
  { id: 'finance', label: 'Финансы' },
]

type SafetyKind = 'medical' | 'crisis'

type SafetyNoticeData = {
  kind: SafetyKind
  title: string
  message: string
  contacts: string[]
}

const SELF_HARM_RE = /суицид|самоубий|поконч(ить|у)\s+с\s+собой|не\s+хочу\s+жить|убить\s+себя|самоповреж|self[-\s]?harm|suicid|kill\s+myself|end\s+my\s+life/i
const MEDICAL_RE = /болезн|заболев|симптом|диагноз|лечени|лекарств|температур|боль|депресси|тревог|паник|врач|doctor|symptom|diagnos|disease|illness|medicine|panic|anxiety/i

const RUSSIA_TZ_RE = /Europe\/(Moscow|Kaliningrad|Kirov|Samara|Volgograd|Astrakhan|Ulyanovsk)|Asia\/(Yekaterinburg|Omsk|Novosibirsk|Barnaul|Tomsk|Krasnoyarsk|Irkutsk|Yakutsk|Vladivostok|Magadan|Sakhalin|Kamchatka|Anadyr)/i
const US_TZ_RE = /America\/(New_York|Chicago|Denver|Los_Angeles|Anchorage|Phoenix|Detroit|Indiana|Adak|Boise|Juneau|Sitka|Metlakatla|Yakutat|Nome)/i

const getRuntimeLocale = () => {
  const tgLang = String((window as any)?.Telegram?.WebApp?.initDataUnsafe?.user?.language_code || '').trim().toLowerCase()
  if (tgLang) return tgLang
  return String(navigator.language || '').trim().toLowerCase()
}

const getRuntimeTimeZone = () => {
  try {
    return String(Intl.DateTimeFormat().resolvedOptions().timeZone || '')
  } catch {
    return ''
  }
}

const buildSafetyNotice = (questionRaw: string): SafetyNoticeData | null => {
  const question = String(questionRaw || '').trim()
  if (!question) return null

  const isCrisis = SELF_HARM_RE.test(question)
  const isMedical = isCrisis || MEDICAL_RE.test(question)
  if (!isMedical) return null

  const locale = getRuntimeLocale()
  const tz = getRuntimeTimeZone()
  const isRuRegion = locale.startsWith('ru') || RUSSIA_TZ_RE.test(tz)
  const isUzRegion = locale.startsWith('uz') || /Asia\/Tashkent/i.test(tz)
  const isUsRegion = locale.startsWith('en-us') || US_TZ_RE.test(tz)
  const isEnglish = locale.startsWith('en')

  if (isCrisis) {
    if (isUsRegion) {
      return {
        kind: 'crisis',
        title: 'Safety first',
        message: 'This looks like a crisis topic. Please contact emergency help right now and talk to a live specialist.',
        contacts: ['US/Canada Suicide & Crisis Lifeline: 988', 'Emergency services: 911'],
      }
    }
    if (isUzRegion) {
      return {
        kind: 'crisis',
        title: 'Важно',
        message: 'Похоже на кризисный вопрос. Пожалуйста, срочно обратитесь за живой помощью к специалисту.',
        contacts: ['Экстренные службы: 112', 'Скорая медицинская помощь: 103'],
      }
    }
    if (isRuRegion || !isEnglish) {
      return {
        kind: 'crisis',
        title: 'Важно',
        message: 'Похоже на кризисный вопрос. Пожалуйста, срочно обратитесь за живой помощью к специалисту.',
        contacts: ['Экстренные службы: 112', 'Скорая медицинская помощь: 103'],
      }
    }
    return {
      kind: 'crisis',
      title: 'Safety first',
      message: 'This looks like a crisis topic. Please contact your local emergency service now.',
      contacts: ['Emergency services: local emergency number (for example 112/911)'],
    }
  }

  if (isUsRegion) {
    return {
      kind: 'medical',
      title: 'Medical note',
      message: 'This question may need medical evaluation. Please contact a licensed doctor for diagnosis and treatment.',
      contacts: ['Urgent emergency: 911'],
    }
  }
  if (isUzRegion) {
    return {
      kind: 'medical',
      title: 'Медицинская памятка',
      message: 'Вопрос может требовать медицинской оценки. Пожалуйста, обратитесь к врачу для точной диагностики.',
      contacts: ['Скорая медицинская помощь: 103', 'Экстренные службы: 112'],
    }
  }
  if (isRuRegion || !isEnglish) {
    return {
      kind: 'medical',
      title: 'Медицинская памятка',
      message: 'Вопрос может требовать медицинской оценки. Пожалуйста, обратитесь к врачу для точной диагностики.',
      contacts: ['Скорая медицинская помощь: 103', 'Экстренные службы: 112'],
    }
  }
  return {
    kind: 'medical',
    title: 'Medical note',
    message: 'This question may need medical evaluation. Please contact a licensed doctor for diagnosis and treatment.',
    contacts: ['Emergency services: local emergency number'],
  }
}

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

function StartReadingIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" fill="none">
      <g stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="12" cy="12" r="4.25" />
        <circle cx="12" cy="12" r="5.8" opacity="0.55" />

        <path d="M12 2.2c.6 1 .95 2 .95 3.35" />
        <path d="M21.8 12c-1 .6-2 .95-3.35.95" />
        <path d="M12 21.8c-.6-1-.95-2-.95-3.35" />
        <path d="M2.2 12c1-.6 2-.95 3.35-.95" />

        <path d="M18.6 5.4c-.95 1-1.95 1.65-3.1 2.25" />
        <path d="M18.6 18.6c-1-.95-1.65-1.95-2.25-3.1" />
        <path d="M5.4 18.6c.95-1 1.95-1.65 3.1-2.25" />
        <path d="M5.4 5.4c1 .95 1.65 1.95 2.25 3.1" />
      </g>
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
  lockedFrontReversed = false,
  previewExcludeUrl,
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
  lockedFrontReversed?: boolean
  previewExcludeUrl?: string
}) {
  const safeFronts = frontUrls.length ? frontUrls : [backUrl]
  const previewFronts = !lockFront && previewExcludeUrl
    ? safeFronts.filter((u) => u !== previewExcludeUrl)
    : safeFronts
  const pool = previewFronts.length ? previewFronts : safeFronts

  const pickNext = (exclude?: string) => {
    if (pool.length === 1) return pool[0]
    let n = pool[Math.floor(Math.random() * pool.length)]
    if (exclude && n === exclude) {
      const idx = pool.indexOf(n)
      n = pool[(idx + 1) % pool.length]
    }
    return n
  }

  const [front, setFront] = useState(() => {
    if (lockFront && lockedFrontUrl) return lockedFrontUrl
    return pickNext()
  })

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

  // ✅ пока active и не lockFront — меняем фронт строго в "слепой зоне" (90°/270°)
  useEffect(() => {
    clearTimers()

    if (!active) return
    if (lockFront) return

    const firstSwap = Math.max(220, Math.floor(durationMs * 0.25))
    const loopSwap = Math.max(420, Math.floor(durationMs * 0.5))
    halfTimeoutRef.current = window.setTimeout(() => {
      setFront((cur) => pickNext(cur))

      cycleIntervalRef.current = window.setInterval(() => {
        setFront((cur) => pickNext(cur))
      }, loopSwap)
    }, firstSwap)

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

  // Когда выходим из lockFront в режим preview — сразу уводим с финальной карты на рандомную.
  useEffect(() => {
    if (lockFront) return
    if (!previewExcludeUrl) return
    setFront((cur) => {
      if (cur !== previewExcludeUrl) return cur
      return pickNext(cur)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lockFront, previewExcludeUrl])

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
        ['--front-rot' as any]: lockFront && lockedFrontReversed ? '180deg' : '0deg',
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

type SbpPlanCode = 'sub_2weeks' | 'sub_month' | 'sub_year'

const BOT_USERNAME = 'Ttaarrroobot'
const BOT_PAYMENT_URL = `https://t.me/${BOT_USERNAME}?start=menu`
const BOT_CARD_URL = `https://t.me/${BOT_USERNAME}?start=card`
const BOT_CLICK_URL = `https://t.me/${BOT_USERNAME}?start=click`
const BOT_CLICK_CARD_URL = `https://t.me/${BOT_USERNAME}?start=click_card`
const BOT_SUB_MANAGE_URL = `https://t.me/${BOT_USERNAME}?start=sub_manage`
const SUPPORT_URL = `https://t.me/${BOT_USERNAME}?start=support`
const TERMS_URL = `https://t.me/${BOT_USERNAME}?start=terms`
const PRIVACY_URL = `https://t.me/${BOT_USERNAME}?start=privacy`
const LEGAL_CONSENT_VERSION = '2026-02-25-v1'
const HOME_TOUR_VERSION = '2026-03-08-v3'
const TERMS_PDF_URL = '/docs/ai_taro_user_agreement_draft.pdf'
const PRIVACY_PDF_URL = '/docs/ai_taro_privacy_policy_draft.pdf'

type HomeTourStepId = 'card_day' | 'photo' | 'question_zone' | 'cta'
type HomeTourSpotlight = {
  top: number
  left: number
  width: number
  height: number
  radius: number
  placement: 'top' | 'bottom'
  bubbleTop: number
  bubbleLeft: number
  bubbleWidth: number
  bubbleArrowLeft: number
}

const HOME_TOUR_STEPS: Array<{ id: HomeTourStepId; title: string; text: string }> = [
  {
    id: 'card_day',
    title: 'Карта дня',
    text: 'Нажмите здесь, чтобы открыть вашу карту дня и получить короткий AI-разбор на сегодня.',
  },
  {
    id: 'photo',
    title: 'Фото расклада',
    text: 'Разложите обычные карты Таро перед собой, сфотографируйте их сверху (или выберите фото из галереи) — приложение распознает именно эти карты и даст разбор.',
  },
  {
    id: 'question_zone',
    title: 'Вопрос и тип расклада',
    text: 'Сначала напишите, что вас волнует, затем выберите категорию и формат расклада ниже.',
  },
  {
    id: 'cta',
    title: 'Запуск расклада',
    text: 'Когда тип расклада выбран, нажмите кнопку «Начать расклад» внизу экрана.',
  },
]

type LegalDocKind = 'terms' | 'privacy'

const LEGAL_DOCS: Record<LegalDocKind, { title: string; intro: string; body: string[]; pdf: string }> = {
  terms: {
    title: 'Пользовательское соглашение',
    intro: 'Черновая версия документа. Финальная юридическая редакция будет добавлена позже без изменения структуры экрана.',
    body: [
      '1. AI Taro предоставляет информационные интерпретации раскладов и не является медицинской, юридической или финансовой консультацией.',
      '2. Пользователь самостоятельно принимает решения на основе полученной информации.',
      '3. При первом входе пользователь подтверждает согласие с документами и отдельное согласие на персональную память раскладов (хранение до 90 дней).',
      '4. Память используется только для персонализации интерпретаций. Для удаления персональных данных используйте /forgetme.',
      '5. Пользователь может запросить полное удаление персональных данных командой /forgetme в боте @Ttaarrroobot.',
      '6. Для вопросов поддержки используйте кнопку «Написать в поддержку» в профиле или команду /support в боте.',
    ],
    pdf: TERMS_PDF_URL,
  },
  privacy: {
    title: 'Политика конфиденциальности',
    intro: 'Черновая версия документа. Финальная редакция будет обновлена позже без изменения пользовательского пути.',
    body: [
      '1. Для работы сервиса обрабатываются данные Telegram-профиля (id, username, имя) и введённые пользователем запросы.',
      '2. Данные используются только для авторизации, расчёта лимитов, оплаты и генерации ответов.',
      '3. При включённой персональной памяти сохраняются структурированные данные раскладов (тема, тип, карты, выводы) со сроком хранения до 90 дней.',
      '4. Сервис не продаёт персональные данные третьим лицам и использует их только для работы AI Taro.',
      '5. Для удаления персональных данных используйте команду /forgetme в боте @Ttaarrroobot.',
      '6. Для медико-кризисных вопросов сервис показывает мягкое предупреждение и контакты экстренной помощи по региону, без постановки диагнозов.',
    ],
    pdf: PRIVACY_PDF_URL,
  },
}

const openTelegramUrl = (url: string) => {
  const safeUrl = String(url || '').trim()
  if (!safeUrl) return

  try {
    const tg = (window as any)?.Telegram?.WebApp
    if (safeUrl.startsWith('https://t.me/') && typeof tg?.openTelegramLink === 'function') {
      tg.openTelegramLink(safeUrl)
      return
    }
    if (typeof tg?.openLink === 'function') {
      tg.openLink(safeUrl)
      return
    }
  } catch {}

  try {
    window.open(safeUrl, '_blank', 'noopener,noreferrer')
  } catch {}
}

const openTelegramAndCloseMiniApp = (url: string) => {
  openTelegramUrl(url)
  try {
    window.setTimeout(() => {
      ;(window as any)?.Telegram?.WebApp?.close?.()
    }, 120)
  } catch {}
}

const isGenericThemeCapsule = (value: string) => {
  const t = String(value || '').trim().toLowerCase()
  if (!t) return true
  return ['other', 'другое', 'open', 'open question', 'открытый вопрос', 'эта тема'].includes(t)
}

const LEGAL_DOC_BOT_DEEPLINK: Record<LegalDocKind, string> = {
  terms: TERMS_URL,
  privacy: PRIVACY_URL,
}

const JWT_STORAGE_KEY = 'jwt'

const readStoredJwt = (): string | null => {
  try {
    const current = sessionStorage.getItem(JWT_STORAGE_KEY)
    if (current && current.trim()) return current.trim()
  } catch {}

  try {
    const persisted = localStorage.getItem(JWT_STORAGE_KEY)
    if (persisted && persisted.trim()) {
      try {
        sessionStorage.setItem(JWT_STORAGE_KEY, persisted.trim())
      } catch {}
      return persisted.trim()
    }
  } catch {}
  return null
}

const writeStoredJwt = (token: string) => {
  const value = String(token || '').trim()
  if (!value) {
    clearStoredJwt()
    return
  }
  try {
    sessionStorage.setItem(JWT_STORAGE_KEY, value)
  } catch {}
  try {
    localStorage.setItem(JWT_STORAGE_KEY, value)
  } catch {}
}

const clearStoredJwt = () => {
  try {
    sessionStorage.removeItem(JWT_STORAGE_KEY)
  } catch {}
  try {
    localStorage.removeItem(JWT_STORAGE_KEY)
  } catch {}
}

const resolveTelegramInitData = (): string => {
  try {
    const tgData = String((window as any)?.Telegram?.WebApp?.initData || '').trim()
    if (tgData) return tgData
  } catch {}

  const fromParams = (raw: string) => {
    const p = new URLSearchParams(raw)
    const v = String(
      p.get('tgWebAppData') ||
      p.get('tgwebappdata') ||
      p.get('init_data') ||
      ''
    ).trim()
    if (!v) return ''
    try {
      return decodeURIComponent(v)
    } catch {
      return v
    }
  }

  try {
    const fromSearch = fromParams(window.location.search || '')
    if (fromSearch) return fromSearch
  } catch {}
  try {
    const hash = String(window.location.hash || '').replace(/^#/, '')
    const fromHash = fromParams(hash)
    if (fromHash) return fromHash
  } catch {}
  return ''
}

const delayMs = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms))

const resolveTelegramInitDataWithRetry = async (attempts = 8, stepMs = 250): Promise<string> => {
  for (let i = 0; i < attempts; i++) {
    const data = resolveTelegramInitData()
    if (data) return data
    await delayMs(stepMs)
  }
  return ''
}

export default function App() {
  /* =============================================================================================
   АВТОРИЗАЦИЯ В ТГ (при запуске мини‑приложения)
   Логика:
     1) если есть jwt в sessionStorage — пробуем /me
     2) если jwt невалиден / отсутствует — делаем POST /auth/telegram с initData
     3) пока идёт авторизация — показываем лоадер (фон/канвасы остаются)
============================================================================================= */

type AuthStatus = 'loading' | 'ready' | 'error'

const [token, setToken] = useState<string | null>(() => {
  return readStoredJwt()
})

const [user, setUser] = useState<MeDto | null>(null)
const [billing, setBilling] = useState<BillingStatus | null>(null)
const [authStatus, setAuthStatus] = useState<AuthStatus>('loading')
const [authError, setAuthError] = useState<string>('')
const [authRetryNonce, setAuthRetryNonce] = useState(0)
const [showHomeTour, setShowHomeTour] = useState(false)
const [homeTourIndex, setHomeTourIndex] = useState(0)
const [homeTourSpotlight, setHomeTourSpotlight] = useState<HomeTourSpotlight | null>(null)
const [sbpOrderId, setSbpOrderId] = useState<string | null>(() => {
  try {
    const v = localStorage.getItem('sbp_pending_order_id')
    return v && v.trim() ? v.trim() : null
  } catch {
    return null
  }
})
const [sbpBusyPlan, setSbpBusyPlan] = useState<SbpPlanCode | null>(null)
const [sbpStatusText, setSbpStatusText] = useState('')
const [sbpPolling, setSbpPolling] = useState(false)
const [showAccessPaywall, setShowAccessPaywall] = useState(false)
const [showLegalConsent, setShowLegalConsent] = useState(false)
const [legalConsentChecked, setLegalConsentChecked] = useState(false)
const [activeLegalDoc, setActiveLegalDoc] = useState<LegalDocKind | null>(null)
const [showPersonalizationModal, setShowPersonalizationModal] = useState(false)
const [memoryOptIn, setMemoryOptIn] = useState<boolean>(true)
const [prefsSaving, setPrefsSaving] = useState(false)
const [prefsError, setPrefsError] = useState('')

const [question, setQuestion] = useState('')

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
        clearStoredJwt()
        safe(() => {
          setToken(null)
          setUser(null)
          setBilling(null)
        })
      }
    }

    // 2) Телеграм‑авторизация
    try {
      const initData = await resolveTelegramInitDataWithRetry(8, 250)
      if (!initData) {
        safe(() => {
          setAuthStatus('error')
          setAuthError('Откройте мини‑приложение из кнопки в Telegram (initData не передан).')
        })
        return
      }

      let res: Awaited<ReturnType<typeof telegramAuth>>
      try {
        res = await telegramAuth(initData)
      } catch (firstErr: any) {
        const msg = String(firstErr?.message || firstErr || '')
        const retryable = /Invalid Telegram initData|initData expired|Invalid initData timestamp/i.test(msg)
        if (!retryable) throw firstErr
        await delayMs(350)
        const refreshed = await resolveTelegramInitDataWithRetry(6, 250)
        if (!refreshed) throw firstErr
        res = await telegramAuth(refreshed)
      }
      let billingOut: BillingStatus | null = null
      try {
        billingOut = await getBillingStatus(res.token)
      } catch {}

      writeStoredJwt(res.token)

      safe(() => {
        setToken(res.token)
        setUser(res.user)
        setBilling(billingOut)
        setAuthStatus('ready')
      })
    } catch (e: any) {
      console.error('Auth error', e)
      clearStoredJwt()
      const raw = String(e?.message || e || '')
      let uiError = 'Не удалось авторизоваться. Перезапустите мини‑приложение в Telegram.'
      if (/initData is empty|нет initData|initData не передан/i.test(raw)) {
        uiError = 'Telegram не передал данные входа. Откройте приложение только через кнопку в боте.'
      } else if (/Invalid Telegram initData|initData expired|Invalid initData timestamp/i.test(raw)) {
        uiError = 'Данные Telegram устарели. Закройте мини‑приложение и откройте заново из бота.'
      } else if (/401|403/.test(raw)) {
        uiError = 'Ошибка авторизации Telegram. Закройте мини‑приложение и откройте снова.'
      }

      safe(() => {
        setToken(null)
        setUser(null)
        setBilling(null)
        setAuthStatus('error')
        setAuthError(uiError)
      })
    }
  }

  runAuth()

  return () => {
    mounted = false
  }
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, [token, authRetryNonce])/* =============================================================================================
     [9] БАЗОВОЕ СОСТОЯНИЕ UI
  ============================================================================================= */

  useEffect(() => {
    setMemoryOptIn(Boolean(user?.memory_opt_in ?? true))
  }, [user?.memory_opt_in])

  useEffect(() => {
    if (authStatus !== 'ready') {
      setShowLegalConsent(false)
      return
    }
    const tgId = Number(user?.telegram_id || 0)
    if (!tgId) return
    const key = `ai_taro_legal_consent:${LEGAL_CONSENT_VERSION}:${tgId}`
    let accepted = false
    try {
      accepted = localStorage.getItem(key) === '1'
    } catch {}
    if (accepted) {
      setShowLegalConsent(false)
      return
    }
    setLegalConsentChecked(false)
    setShowLegalConsent(true)
  }, [authStatus, user?.telegram_id])

  const acceptLegalConsent = () => {
    const tgId = Number(user?.telegram_id || 0)
    if (!tgId) return
    const key = `ai_taro_legal_consent:${LEGAL_CONSENT_VERSION}:${tgId}`
    try {
      localStorage.setItem(key, '1')
    } catch {}
    setShowLegalConsent(false)
  }

  const openLegalDoc = (kind: LegalDocKind) => {
    setActiveLegalDoc(kind)
  }

  const closeLegalDoc = () => {
    setActiveLegalDoc(null)
  }

  const savePersonalization = async () => {
    if (!token) return
    setPrefsSaving(true)
    setPrefsError('')
    try {
      const out = await updateMePreferences(token, {
        memory_opt_in: memoryOptIn,
        retention_nudges_opt_in: false,
        retention_nudge_hour_local: null,
        retention_nudge_tz: null,
      })
      setMemoryOptIn(Boolean(out.memory_opt_in ?? true))
      setUser((prev) => (prev ? { ...prev, memory_opt_in: Boolean(out.memory_opt_in ?? true) } : prev))
      setShowPersonalizationModal(false)
    } catch {
      setPrefsError('Не удалось сохранить. Попробуйте ещё раз.')
    } finally {
      setPrefsSaving(false)
    }
  }

  useEffect(() => {
    if (!activeLegalDoc) return
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeLegalDoc()
    }
    window.addEventListener('keydown', onEsc)
    return () => window.removeEventListener('keydown', onEsc)
  }, [activeLegalDoc])

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

  const setPendingSbpOrder = (orderId: string | null) => {
    const clean = String(orderId || '').trim()
    const next = clean || null
    setSbpOrderId(next)
    try {
      if (next) localStorage.setItem('sbp_pending_order_id', next)
      else localStorage.removeItem('sbp_pending_order_id')
    } catch {}
  }

  useEffect(() => {
    if (!showAccessPaywall) return
    if (billing?.can_create_reading) {
      setShowAccessPaywall(false)
    }
  }, [showAccessPaywall, billing?.can_create_reading])

  useEffect(() => {
    try {
      const qs = new URLSearchParams(window.location.search || '')
      const fromQuery = String(qs.get('sbp_order_id') || '').trim()
      if (fromQuery) {
        setPendingSbpOrder(fromQuery)
        qs.delete('sbp_order_id')
        const queryLeft = qs.toString()
        const nextUrl = `${window.location.pathname}${queryLeft ? `?${queryLeft}` : ''}${window.location.hash || ''}`
        window.history.replaceState({}, '', nextUrl)
      }
    } catch {}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const checkSbpStatus = async (orderIdArg?: string | null, silent = false) => {
    const jwt = token
    const orderId = String(orderIdArg ?? sbpOrderId ?? '').trim()
    if (!jwt || !orderId) return

    try {
      if (!silent) setSbpPolling(true)
      const out = await getSbpPaymentStatus(jwt, orderId)
      const status = String(out?.status || '').toLowerCase()
      const msg = String(out?.message || '').trim()

      if (status === 'succeeded') {
        setPendingSbpOrder(null)
        void refreshBilling(jwt)
      } else if (status === 'canceled' || status === 'cancelled') {
        setPendingSbpOrder(null)
      }

      if (!silent || msg) {
        setSbpStatusText(msg || 'Статус платежа обновлён.')
      }
    } catch (err: any) {
      if (!silent) {
        const text = String(err?.message || '')
        const parsed = readBackendErrorDetail(text)
        const detail = parsed?.detail
        if (detail?.code === 'SBP_ORDER_NOT_FOUND') {
          setPendingSbpOrder(null)
          setSbpStatusText('Счёт не найден или уже закрыт. Создайте новый платёж.')
        } else {
          setSbpStatusText('Не удалось проверить статус оплаты. Попробуйте ещё раз.')
        }
      }
    } finally {
      if (!silent) setSbpPolling(false)
    }
  }

  const startSbpPayment = async (planCode: SbpPlanCode) => {
    const jwt = token
    if (!jwt) return

    try {
      setSbpBusyPlan(planCode)
      setSbpStatusText('')

      const out = await createSbpPayment(jwt, planCode)
      const orderId = String(out?.order_id || '').trim()
      const link = String(out?.confirmation_url || '').trim()

      if (!orderId || !link) {
        throw new Error('Провайдер не вернул ссылку оплаты.')
      }

      setPendingSbpOrder(orderId)
      setSbpStatusText('Счёт СБП открыт. Завершите оплату и вернитесь в приложение.')
      openTelegramUrl(link)
      window.setTimeout(() => {
        void checkSbpStatus(orderId, true)
      }, 3500)
    } catch (err: any) {
      const text = String(err?.message || '')
      const parsed = readBackendErrorDetail(text)
      const detail = parsed?.detail

      if (detail?.code === 'SBP_NOT_CONFIGURED') {
        setSbpStatusText('СБП ещё не настроен на сервере. Нужны shop_id и secret_key ЮKassa.')
      } else if (detail?.message) {
        setSbpStatusText(String(detail.message))
      } else {
        setSbpStatusText('Не удалось создать счёт СБП. Попробуйте ещё раз.')
      }
    } finally {
      setSbpBusyPlan(null)
    }
  }

  useEffect(() => {
    if (!token || !sbpOrderId) return

    let stopped = false
    const tick = async () => {
      if (stopped) return
      await checkSbpStatus(sbpOrderId, true)
    }
    void tick()
    const t = window.setInterval(() => {
      void tick()
    }, 10000)

    return () => {
      stopped = true
      window.clearInterval(t)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, sbpOrderId])

  const readingLimitMessage =
    `Бесплатный лимит раскладов за месяц исчерпан.\n\n` +
    `Оплатите подписку в профиле (СБП) или в боте: ${BOT_PAYMENT_URL}`

  const isReadingLimitExceeded = (raw: string) => {
    const msg = String(raw || '').trim()
    if (!msg) return false
    const parsed = readBackendErrorDetail(msg)
    const detail = parsed?.detail
    return Boolean(detail?.code === 'READING_LIMIT_EXCEEDED' || /READING_LIMIT_EXCEEDED|402/i.test(msg))
  }

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

    if (isReadingLimitExceeded(msg)) {
      return readingLimitMessage
    }
    if (/401|403/i.test(msg)) return 'Сессия устарела. Перезапустите мини-приложение и попробуйте снова.'
    if (/503|service unavailable/i.test(msg)) return 'AI-сервис временно недоступен. Повторите через минуту.'
    if (typeof detail === 'string' && detail.trim()) return detail.trim()
    return msg || 'Не удалось получить ответ от сервера.'
  }

  const [needsMotionPermission, setNeedsMotionPermission] = useState(false)
  const [pressed, setPressed] = useState(false)

  const [askInputFocused, setAskInputFocused] = useState(false)
  const [keyboardInset, setKeyboardInset] = useState(0)
  const focusSyncTRef = useRef<number | null>(null)

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

  useEffect(() => {
    const isAskInput = (target: EventTarget | null): target is HTMLElement => {
      return !!target && target instanceof HTMLElement && target.classList.contains('ask-input')
    }

    const syncFromActive = () => {
      const active = document.activeElement
      setAskInputFocused(active instanceof HTMLElement && active.classList.contains('ask-input'))
    }

    const onFocusIn = (event: FocusEvent) => {
      if (isAskInput(event.target)) {
        setAskInputFocused(true)
        return
      }
      syncFromActive()
    }

    const onFocusOut = () => {
      if (focusSyncTRef.current) window.clearTimeout(focusSyncTRef.current)
      focusSyncTRef.current = window.setTimeout(syncFromActive, 0)
    }

    document.addEventListener('focusin', onFocusIn)
    document.addEventListener('focusout', onFocusOut)
    syncFromActive()

    return () => {
      document.removeEventListener('focusin', onFocusIn)
      document.removeEventListener('focusout', onFocusOut)
      if (focusSyncTRef.current) window.clearTimeout(focusSyncTRef.current)
    }
  }, [])

  useEffect(() => {
    const vv = window.visualViewport

    const syncInset = () => {
      const raw = vv ? window.innerHeight - vv.height - vv.offsetTop : 0
      setKeyboardInset(Math.max(0, Math.round(raw)))
    }

    syncInset()
    vv?.addEventListener('resize', syncInset)
    vv?.addEventListener('scroll', syncInset)
    window.addEventListener('resize', syncInset)
    window.addEventListener('orientationchange', syncInset)

    return () => {
      vv?.removeEventListener('resize', syncInset)
      vv?.removeEventListener('scroll', syncInset)
      window.removeEventListener('resize', syncInset)
      window.removeEventListener('orientationchange', syncInset)
    }
  }, [])

  /* =============================================================================================
     [11] ВЫБОР ТЕМЫ (SEG)
  ============================================================================================= */

  const [topic, setTopic] = useState<Topic>('other')
  const prevTopicRef = useRef<Topic>('other')
  const [prevTopic, setPrevTopic] = useState<Topic>('other')

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
    decision: 'spread-card--emerald',
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
    const spreadOk = !!spread

    if (!spreadOk) {
      pulseCtaRed()
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

  const renderSafetyNotice = (sourceQuestion: string) => {
    const note = buildSafetyNotice(sourceQuestion)
    if (!note) return null
    return (
      <div className={`safety-notice ${note.kind === 'crisis' ? 'is-crisis' : 'is-medical'}`} role="note">
        <div className="safety-notice__title">{note.title}</div>
        <p className="safety-notice__text">{note.message}</p>
        <ul className="safety-notice__contacts">
          {note.contacts.map((line, i) => (
            <li key={`safety-${i}`}>{line}</li>
          ))}
        </ul>
      </div>
    )
  }

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

  const NAV_INDEX = useMemo(() => {
    const map = new Map<NavTab, number>()
    ;(['main', 'history', 'profile'] as NavTab[]).forEach((t, i) => map.set(t, i))
    return map
  }, [])

  const navActiveIndex = NAV_INDEX.get(navTab) ?? 0
  const navPrevIndex = NAV_INDEX.get(navPrev) ?? 0

  // направление для data-dir (можно использовать в CSS для лёгких эффектов)
  const navDir: 'left' | 'right' | 'none' = navActiveIndex === navPrevIndex ? 'none' : navActiveIndex > navPrevIndex ? 'right' : 'left'
  const showKeyboardToolbar = askInputFocused && !(view === 'home' && navTab === 'main')

  useEffect(() => {
    if (!askInputFocused) return
    if (!(view === 'home' && navTab === 'main')) return

    const scrollQuestionIntoView = (behavior: ScrollBehavior) => {
      const askWrap = askWrapRef.current
      if (!askWrap) return

      try {
        askWrap.scrollIntoView({ behavior, block: 'start', inline: 'nearest' })
      } catch {
        askWrap.scrollIntoView()
      }

      const scroller = contentRef.current
      const footerRect = homePrimaryFooterRef.current?.getBoundingClientRect()
      if (!scroller || !footerRect) return

      const scrollerRect = scroller.getBoundingClientRect()
      const askRect = askWrap.getBoundingClientRect()
      const bottomSafe = scrollerRect.bottom - footerRect.height - 12
      if (askRect.bottom > bottomSafe) {
        scroller.scrollBy({ top: askRect.bottom - bottomSafe + 8, behavior })
      }
    }

    scrollQuestionIntoView('auto')
    const t1 = window.setTimeout(() => scrollQuestionIntoView('smooth'), 90)
    const t2 = window.setTimeout(() => scrollQuestionIntoView('smooth'), 240)
    const t3 = window.setTimeout(() => scrollQuestionIntoView('smooth'), 420)
    return () => {
      window.clearTimeout(t1)
      window.clearTimeout(t2)
      window.clearTimeout(t3)
    }
  }, [askInputFocused, keyboardInset, navTab, view])

  const onPickNav = (next: NavTab) => {
    if (next === navTab) return

    setNavPrev(prevNavTabRef.current)
    prevNavTabRef.current = next
    setNavTab(next)
  }

  const goToMainTab = () => {
    if (navTab === 'main') return
    onPickNav('main')
  }

  const toggleHistoryTab = () => {
    onPickNav(navTab === 'history' ? 'main' : 'history')
  }

  const toggleProfileTab = () => {
    onPickNav(navTab === 'profile' ? 'main' : 'profile')
  }

  const handleProfileLogout = () => {
    clearStoredJwt()

    setToken(null)
    setUser(null)
    setBilling(null)
    setOpenedReadingId(null)
    onPickNav('main')
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
          .map((item: any): HistoryListItem | null => {
            if (item?.kind === 'card_of_day') {
              const p = item?.payload || {}
              return {
                kind: 'card_of_day' as const,
                day_key: String(p.day_key || ''),
                topic: String(p.topic || ''),
                question: String(p.question || ''),
                card_index: Number(p.card_index ?? 0),
                card_name: String(p.card_name || ''),
                is_reversed: Boolean(p.is_reversed),
                theme_capsule: String(p.theme_capsule || '').trim() || undefined,
                description: String(p.description || ''),
                created_at: String(item?.created_at || ''),
              }
            }

            if (item?.kind === 'reading') {
              const p = item?.payload || {}
              const cardsRaw = Array.isArray(p.cards) ? p.cards : []
              const cards: HistoryReadingCard[] = cardsRaw.map((card: any) => ({
                position: String(card?.position || ''),
                title: String(card?.title || ''),
                card_index: Number(card?.card_index ?? 0),
                card_name: String(card?.card_name || ''),
                is_reversed: Boolean(card?.is_reversed),
                meaning: String(card?.meaning || ''),
              }))
              const first = cards[0] || {}
              const fallbackLabel = SPREAD_HISTORY_LABELS[String(p.spread_type || '')] || 'Расклад'
              return {
                kind: 'reading' as const,
                reading_id: Number(p.id ?? 0),
                created_at: String(item?.created_at || ''),
                topic: String(p.topic || ''),
                question: String(p.question || ''),
                spread_type: String(p.spread_type || 'reading'),
                description: String(p.description || ''),
                theme_capsule: String(p.theme_capsule || '').trim() || undefined,
                cards_count: cards.length || 0,
                card_index: Number(first.card_index ?? 0),
                card_name: String(first.card_name || fallbackLabel),
                cards,
              }
            }

            return null
          })
          .filter((x): x is HistoryListItem => x !== null)

        const sorted = [...mapped].sort((a, b) => {
          const aDayKey = a.kind === 'card_of_day' ? a.day_key : ''
          const bDayKey = b.kind === 'card_of_day' ? b.day_key : ''
          const ta = Date.parse(a.created_at || aDayKey || '')
          const tb = Date.parse(b.created_at || bDayKey || '')
          return (isFinite(tb) ? tb : 0) - (isFinite(ta) ? ta : 0)
        })

        setHistory(sorted)
        setOpenedReadingId((prev) => {
          if (prev == null) return prev
          const exists = sorted.some((x) => x.kind === 'reading' && x.reading_id === prev)
          return exists ? prev : null
        })
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

  useEffect(() => {
    if (navTab !== 'history') setOpenedReadingId(null)
  }, [navTab])

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

  // расширенная геометрия для экрана перемешивания в "Расклад по 3 картам"
  const THREE_SLOTS_WIDE: ThreeCardPos[] = [
    { x: -64, y: 10, r: -9, s: 1.05, z: 1 },
    { x: 0, y: -10, r: 2, s: 1.12, z: 2 },
    { x: 64, y: 10, r: 9, s: 1.05, z: 1 },
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
  const PPF_SLOT_LABELS = ['Прошлое', 'Настоящее', 'Будущее'] as const

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
  const [ppfDeckCards, setPpfDeckCards] = useState<PpfCardResult[]>([])
  const [ppfPlacedCards, setPpfPlacedCards] = useState<Array<PpfCardResult | null>>([null, null, null])
  const [ppfRevealMap, setPpfRevealMap] = useState<boolean[]>([false, false, false])
  const [ppfPlacedCount, setPpfPlacedCount] = useState(0)
  const ppfPlacedCountRef = useRef(0)
  const ppfAutoDealTRef = useRef<number | null>(null)
  const ppfSlotRefs = useRef<Array<HTMLDivElement | null>>([])

  const [ppfDragging, setPpfDragging] = useState(false)
  const [ppfDragDelta, setPpfDragDelta] = useState(hiddenDragPoint())
  const [ppfDragOverSlot, setPpfDragOverSlot] = useState<number | null>(null)
  const [ppfActiveFanIndex, setPpfActiveFanIndex] = useState(3)
  const ppfDeckRef = useRef<HTMLDivElement | null>(null)
  const ppfDragCardRef = useRef<HTMLDivElement | null>(null)
  const ppfFanCardRefs = useRef<Array<HTMLSpanElement | null>>([])
  const ppfDragOriginRef = useRef(hiddenDragPoint())
  const ppfDragReturnTRef = useRef<number | null>(null)
  const ppfDragPointerRef = useRef<number | null>(null)
  const ppfDragTouchIdRef = useRef<number | null>(null)
  const ppfGlobalDragCleanupRef = useRef<null | (() => void)>(null)
  const ppfDragStartRef = useRef({ x: 0, y: 0 })
  const ppfDragStartDeltaRef = useRef({ x: 0, y: 0 })

  const [ppfDayKey, setPpfDayKey] = useState<string>('')

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

  useEffect(() => {
    ppfPlacedCountRef.current = ppfPlacedCount
  }, [ppfPlacedCount])

  useEffect(() => {
    return () => {
      if (ppfAutoDealTRef.current) {
        window.clearTimeout(ppfAutoDealTRef.current)
      }
      if (ppfDragReturnTRef.current) {
        window.clearTimeout(ppfDragReturnTRef.current)
      }
      unbindGlobalPpfDrag()
    }
  }, [])


  // ---------------------------------------------------------------------------------------------
  // history (backend) — реальные карты из БД
  // ---------------------------------------------------------------------------------------------

  type CardHistoryItem = {
    day_key: string
    topic: string
    question: string
    card_index: number
    card_name: string
    is_reversed?: boolean
    theme_capsule?: string
    description: string
    created_at: string
  }

  type HistoryReadingCard = {
    position: string
    title: string
    card_index: number
    card_name: string
    is_reversed: boolean
    meaning: string
  }

  type ReadingHistoryItem = {
    kind: 'reading'
    reading_id: number
    created_at: string
    topic: string
    question: string
    spread_type: string
    description: string
    theme_capsule?: string
    cards_count: number
    card_index: number
    card_name: string
    cards: HistoryReadingCard[]
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
  const [openedReadingId, setOpenedReadingId] = useState<number | null>(null)

  const [shakeEnabled, setShakeEnabled] = useState(false)
  const [shakenOnce, setShakenOnce] = useState(false)
  const [cardRevealed, setCardRevealed] = useState(false)

  const [selectedFrontUrl, setSelectedFrontUrl] = useState<string>('')

  // ✅ NEW: “карта дня” — фиксированная на сутки для пользователя
  const [dailyFrontUrl, setDailyFrontUrl] = useState<string>('')
  const [dailyFrontReady, setDailyFrontReady] = useState(false)
  // ✅ NEW: данные "карты дня" с бекенда
  const [dailyDesc, setDailyDesc] = useState<string>('')
  const [dailyCardName, setDailyCardName] = useState<string>('')
  const [dailyDayKey, setDailyDayKey] = useState<string>('')
  const [dailyIsReversed, setDailyIsReversed] = useState(false)
  const [dailyQuestion, setDailyQuestion] = useState<string>('')

  // Подогреваем конкретную карту дня заранее, чтобы reveal открывался без подгрузочного “фриза”.
  useEffect(() => {
    if (!dailyFrontUrl || dailyFrontUrl === backCardImg) {
      setDailyFrontReady(false)
      return
    }

    let cancelled = false
    setDailyFrontReady(false)

    const im = new Image()
    im.decoding = 'async'
    im.src = dailyFrontUrl

    const markReady = () => {
      if (cancelled) return
      setDailyFrontReady(true)
    }

    im.onload = markReady
    im.onerror = () => {
      if (cancelled) return
      setDailyFrontReady(false)
    }

    try {
      ;(im as any).decode?.().then(markReady).catch(() => {})
    } catch {}

    return () => {
      cancelled = true
      im.onload = null
      im.onerror = null
    }
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
  const contentRef = useRef<HTMLDivElement | null>(null)
  const starsCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const cometsCanvasRef = useRef<HTMLCanvasElement | null>(null)
  const homeCardDayRef = useRef<HTMLDivElement | null>(null)
  const homePhotoRef = useRef<HTMLDivElement | null>(null)
  const homeQuestionZoneRef = useRef<HTMLDivElement | null>(null)
  const askWrapRef = useRef<HTMLDivElement | null>(null)
  const spreadListRef = useRef<HTMLDivElement | null>(null)
  const btnRef = useRef<HTMLButtonElement | null>(null)
  const homePrimaryFooterRef = useRef<HTMLDivElement | null>(null)
  const spreadActiveRef = useRef<HTMLDivElement | null>(null)

  const getHomeTourTarget = (stepId: HomeTourStepId | null): HTMLElement | null => {
    if (!stepId) return null
    if (stepId === 'card_day') return homeCardDayRef.current
    if (stepId === 'photo') return homePhotoRef.current
    if (stepId === 'question_zone') return homeQuestionZoneRef.current
    return btnRef.current || homePrimaryFooterRef.current
  }

  const markHomeTourSeen = () => {
    const tgId = Number(user?.telegram_id || 0)
    if (!tgId) return
    const key = `ai_taro_home_tour:${HOME_TOUR_VERSION}:${tgId}`
    try {
      localStorage.setItem(key, '1')
    } catch {}
  }

  const closeHomeTour = () => {
    markHomeTourSeen()
    setShowHomeTour(false)
    setHomeTourSpotlight(null)
  }

  const nextHomeTourStep = () => {
    if (homeTourIndex >= HOME_TOUR_STEPS.length - 1) {
      closeHomeTour()
      return
    }
    setHomeTourIndex((i) => Math.min(HOME_TOUR_STEPS.length - 1, i + 1))
  }

  const prevHomeTourStep = () => {
    setHomeTourIndex((i) => Math.max(0, i - 1))
  }

  useEffect(() => {
    if (authStatus !== 'ready' || showLegalConsent) {
      setShowHomeTour(false)
      setHomeTourSpotlight(null)
      return
    }
    if (view !== 'home' || navTab !== 'main') return
    const tgId = Number(user?.telegram_id || 0)
    if (!tgId) return
    const key = `ai_taro_home_tour:${HOME_TOUR_VERSION}:${tgId}`
    let seen = false
    try {
      seen = localStorage.getItem(key) === '1'
    } catch {}
    if (seen) return
    setHomeTourIndex(0)
    setShowHomeTour(true)
  }, [authStatus, showLegalConsent, view, navTab, user?.telegram_id])

  useEffect(() => {
    if (!showHomeTour || view !== 'home' || navTab !== 'main') return
    const step = HOME_TOUR_STEPS[homeTourIndex]
    if (!step) return
    const target = getHomeTourTarget(step.id)
    if (!target) return
    window.setTimeout(() => {
      try {
        target.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' })
      } catch {}
    }, 90)
  }, [showHomeTour, homeTourIndex, view, navTab])

  useEffect(() => {
    if (!showHomeTour || view !== 'home' || navTab !== 'main') {
      setHomeTourSpotlight(null)
      return
    }
    const step = HOME_TOUR_STEPS[homeTourIndex]
    if (!step) {
      setHomeTourSpotlight(null)
      return
    }

    let raf = 0
    const refreshSpotlight = () => {
      const target = getHomeTourTarget(step.id)
      if (!target) {
        setHomeTourSpotlight(null)
        return
      }
      const rect = target.getBoundingClientRect()
      const vw = window.innerWidth
      const vh = window.innerHeight
      if (rect.width < 6 || rect.height < 6 || vh < 120 || vw < 120) {
        setHomeTourSpotlight(null)
        return
      }

      const pad = 8
      const minGap = 12
      const viewportOffsetTop = Number(window.visualViewport?.offsetTop || 0)
      const topSafe = Math.max(minGap, 72 + viewportOffsetTop)
      const cardHeight = 232
      const top = Math.max(minGap, rect.top - pad)
      const left = Math.max(minGap, rect.left - pad)
      const width = Math.min(vw - left - minGap, rect.width + pad * 2)
      let height = Math.min(vh - top - minGap, rect.height + pad * 2)
      if (step.id === 'question_zone') {
        const maxFocusHeight = Math.min(460, vh * 0.62)
        if (height > maxFocusHeight) height = maxFocusHeight
      }
      const centerX = left + width / 2
      const topSpace = Math.max(0, top - topSafe)
      const bottomSpace = vh - (top + height)
      const canBottom = bottomSpace >= cardHeight + 20
      const canTop = topSpace >= cardHeight + 20
      const placement: 'top' | 'bottom' = canBottom ? 'bottom' : canTop ? 'top' : bottomSpace >= topSpace ? 'bottom' : 'top'

      const bubbleWidth = Math.min(420, vw - minGap * 2)
      let bubbleLeft = centerX - bubbleWidth / 2
      bubbleLeft = Math.max(minGap, Math.min(vw - bubbleWidth - minGap, bubbleLeft))
      let bubbleTop = placement === 'bottom' ? top + height + 14 : top - cardHeight - 14
      bubbleTop = Math.max(topSafe, Math.min(vh - cardHeight - minGap, bubbleTop))
      const bubbleArrowLeft = Math.max(28, Math.min(bubbleWidth - 28, centerX - bubbleLeft))

      const radius = target.classList.contains('home-primary-footer') ? 24 : target.classList.contains('home-guided-zone') ? 26 : 22
      const nextSpotlight: HomeTourSpotlight = {
        top,
        left,
        width,
        height,
        radius,
        placement,
        bubbleTop,
        bubbleLeft,
        bubbleWidth,
        bubbleArrowLeft,
      }

      setHomeTourSpotlight((prev) => {
        if (
          prev &&
          Math.abs(prev.top - nextSpotlight.top) < 1 &&
          Math.abs(prev.left - nextSpotlight.left) < 1 &&
          Math.abs(prev.width - nextSpotlight.width) < 1 &&
          Math.abs(prev.height - nextSpotlight.height) < 1 &&
          Math.abs(prev.bubbleTop - nextSpotlight.bubbleTop) < 1 &&
          Math.abs(prev.bubbleLeft - nextSpotlight.bubbleLeft) < 1 &&
          prev.placement === nextSpotlight.placement
        ) {
          return prev
        }
        return nextSpotlight
      })
    }

    const schedule = () => {
      if (raf) window.cancelAnimationFrame(raf)
      raf = window.requestAnimationFrame(refreshSpotlight)
    }

    schedule()
    const contentNode = contentRef.current
    window.addEventListener('resize', schedule)
    window.addEventListener('orientationchange', schedule)
    window.addEventListener('scroll', schedule, true)
    contentNode?.addEventListener('scroll', schedule, { passive: true })

    return () => {
      if (raf) window.cancelAnimationFrame(raf)
      window.removeEventListener('resize', schedule)
      window.removeEventListener('orientationchange', schedule)
      window.removeEventListener('scroll', schedule, true)
      contentNode?.removeEventListener('scroll', schedule as EventListener)
    }
  }, [showHomeTour, homeTourIndex, view, navTab, keyboardInset])

  const isResult = view === 'card_day_prep' && cardRevealed
  const [stopRequested, setStopRequested] = useState(false)

  // ✅ прогресс перемешивания: 0..1
  const [shuffleProgress, setShuffleProgress] = useState(0)
  const cardDayShuffleStarted = shuffleProgress > 0.01 || stopRequested
  const threeShuffleStarted = threeShuffleProgress > 0.01 || threeReadyToOpen

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

  const getDailyOpenedStorageKey = (dayKey?: string) => {
    const userId = getTelegramUserId()
    const day = String(dayKey || getVilniusDayKey())
    return `ai-tarot:card-day-opened:${userId}:${day}`
  }

  const wasDailyOpened = (dayKey?: string) => {
    try {
      return localStorage.getItem(getDailyOpenedStorageKey(dayKey)) === '1'
    } catch {
      return false
    }
  }

  const markDailyOpened = (dayKey?: string) => {
    try {
      localStorage.setItem(getDailyOpenedStorageKey(dayKey), '1')
    } catch {}
  }

  // ---------------------------------------------------------------------------------------------
  // navigation
  // ---------------------------------------------------------------------------------------------

  const openCardDay = async () => {
    // ✅ форсим ремоунт карты (иначе мог сохраниться фронт с прошлого захода)
    setPflipMountKey((k) => k + 1)

    // сбрасываем UI
    setSelectedFrontUrl('')
    setDailyFrontUrl('')
    setDailyFrontReady(false)
    setShakenOnce(false)
    setShakeEnabled(false)
    setShuffleProgress(0)
    setCardRevealed(false)
    setStopRequested(false)

    setDailyDesc('')
    setDailyCardName('')
    setDailyDayKey('')
    setDailyIsReversed(false)
    setDailyQuestion('')

    // сразу открываем шейк-сценарий без экрана вопроса/категории
    setCardDayLoading(true)
    setView('card_day_prep')
    let shouldOpenReadyResult = false
    let resolvedDayKey = getVilniusDayKey()

    const applyDailyDto = (dto: any) => {
      const idx = Math.max(0, Math.min(Number(dto?.card_index ?? 0), FRONT_CARD_URLS.length - 1))
      const url = FRONT_CARD_URLS[idx] || backCardImg
      const cardName = String(dto?.card_name || '')
      const desc = String(dto?.description || '')
      const isReversed =
        typeof dto?.is_reversed === 'boolean'
          ? Boolean(dto.is_reversed)
          : /\(перев[её]рнут/i.test(cardName) || /перев[её]рнут|обратн|reversed|reverse/i.test(desc)
      const dayKey = String(dto?.day_key || getVilniusDayKey())
      resolvedDayKey = dayKey

      setDailyFrontUrl(url)
      setSelectedFrontUrl(url)
      setDailyDesc(String(dto?.description || ''))
      setDailyCardName(cardName)
      setDailyDayKey(dayKey)
      setDailyIsReversed(isReversed)
      setDailyQuestion(String(dto?.question || ''))
    }

    const applyLocalFallback = () => {
      const dailyLocal = pickDailyCardUrl()
      const day = getVilniusDayKey()
      resolvedDayKey = day
      setDailyFrontUrl(dailyLocal)
      setSelectedFrontUrl(dailyLocal)
      setDailyDesc('')
      setDailyCardName(cardNameFromUrl(dailyLocal))
      setDailyDayKey(day)
      setDailyIsReversed(false)
      setDailyQuestion('')
      shouldOpenReadyResult = wasDailyOpened(day)
    }

    try {
      if (!token) {
        applyLocalFallback()
      } else {
        try {
          const dto = await getCardOfDayToday(token)
          applyDailyDto(dto)
          // Уже есть карта дня: повторный вход сразу в результат, без шейка.
          shouldOpenReadyResult = true
        } catch {
          const dto = await createCardOfDay(token, {
            question: '',
            topic: 'other',
            deck_size: 78,
            consider_reversed: true,
          })
          applyDailyDto(dto)
          // Свежесозданная карта дня: первый вход, нужен шаг перемешивания.
          shouldOpenReadyResult = false
        }
      }
    } catch {
      applyLocalFallback()
    } finally {
      if (shouldOpenReadyResult) {
        setStopRequested(false)
        setShakeEnabled(false)
        setShuffleProgress(1)
        setShakenOnce(true)
        setCardRevealed(true)
        markDailyOpened(resolvedDayKey)
      } else {
        setStopRequested(false)
        setShakeEnabled(true)
        setShuffleProgress(0)
        setShakenOnce(false)
        setCardRevealed(false)
        if (needsMotionPermission) {
          void requestMotion()
        }
      }
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
    setDailyIsReversed(
      Boolean(it.is_reversed) ||
        /\(перев[её]рнут/i.test(String(it.card_name || '')) ||
        /перев[её]рнут|обратн|reversed|reverse/i.test(String(it.description || '')),
    )
    setDailyDayKey(it.day_key || '')
    setDailyQuestion(String(it.question || ''))

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

  type PhotoFlowStep = 'start' | 'analyzing' | 'detected' | 'error' | 'result'
  type PhotoCardItem = {
    position: string
    title: string
    card_index: number | null
    card_name: string
    is_reversed: boolean
    meaning: string
    confidence: number | null
  }

  const [photoFile, setPhotoFile] = useState<File | null>(null)
  const [photoPreviewUrl, setPhotoPreviewUrl] = useState<string>('')
  const [photoStep, setPhotoStep] = useState<PhotoFlowStep>('start')
  const [photoBusy, setPhotoBusy] = useState(false)
  const [photoError, setPhotoError] = useState<string>('')
  const [photoDetectedCards, setPhotoDetectedCards] = useState<PhotoCardItem[]>([])
  const [photoMainQuestion, setPhotoMainQuestion] = useState('')
  const [photoInterpretation, setPhotoInterpretation] = useState('')
  const [photoFollowupQuestion, setPhotoFollowupQuestion] = useState('')
  const [photoFollowupAnswer, setPhotoFollowupAnswer] = useState('')
  const [photoFollowupUsed, setPhotoFollowupUsed] = useState(false)
  const [photoFollowupError, setPhotoFollowupError] = useState('')

  const galleryInputRef = useRef<HTMLInputElement | null>(null)
  const photoReqSeqRef = useRef(0)

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

  const resetPhotoFlow = () => {
    setPhotoFile(null)
    setPhotoStep('start')
    setPhotoBusy(false)
    setPhotoError('')
    setPhotoDetectedCards([])
    setPhotoMainQuestion('')
    setPhotoInterpretation('')
    setPhotoFollowupQuestion('')
    setPhotoFollowupAnswer('')
    setPhotoFollowupUsed(false)
    setPhotoFollowupError('')
  }

  const openPhotoAnalysis = () => {
    resetPhotoFlow()
    setView('photo_analysis')
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
    if (isReadingLimitExceeded(msg)) {
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

  const normalizePhotoCards = (raw: any): PhotoCardItem[] => {
    if (!Array.isArray(raw)) return []
    return raw.slice(0, 10).map((card: any, idx: number) => {
      const cardIndex = Number(card?.card_index)
      const confRaw = Number(card?.confidence)
      const fallbackTitle = String(card?.title || card?.position || `Карта ${idx + 1}`).trim()
      const fallbackName = String(card?.card_name || card?.title || card?.position || '').trim()
      return {
        position: String(card?.position || '').trim(),
        title: fallbackTitle,
        card_index: Number.isFinite(cardIndex) ? clamp(Math.round(cardIndex), 0, 77) : null,
        card_name: fallbackName,
        is_reversed: Boolean(card?.is_reversed),
        meaning: String(card?.meaning || '').trim(),
        confidence: Number.isFinite(confRaw) ? confRaw : null,
      }
    })
  }

  const assessPhotoDetection = (cards: PhotoCardItem[]) => {
    if (!cards.length) {
      return { ok: false as const, reason: 'Карты не распознаны. Попробуйте сделать фото сверху при хорошем свете.' }
    }

    const known = cards.filter((card) => {
      const name = String(card.card_name || card.title || '').trim()
      if (!name) return false
      return !/^unknown$/i.test(name)
    })
    const usable = known.length ? known : cards

    return { ok: true as const, cards: usable }
  }

  const buildPhotoFallbackText = (cards: PhotoCardItem[], questionText: string) => {
    const cardsLine = cards
      .slice(0, 4)
      .map((card) => String(card.card_name || card.title || card.position || 'Карта').trim())
      .filter(Boolean)
      .join(', ')
    const main = String(questionText || '').trim()
      ? `По вашему вопросу сочетание карт (${cardsLine || 'этот расклад'}) показывает необходимость выбрать более устойчивую линию действий.`
      : `По сочетанию карт (${cardsLine || 'этот расклад'}) видно, что вы в моменте пересмотра приоритетов и внутренней настройки.`
    return [
      '## Общий вектор',
      main,
      '',
      '## Рекомендации',
      '- Отделите факты от тревожных сценариев и не принимайте решения в спешке.',
      '- Выберите один конкретный шаг, который реально сделать сегодня.',
      '- На ближайшие 24 часа держите спокойный и последовательный темп.',
    ].join('\n')
  }

  const buildPhotoFanLayout = (count: number, compact = false) => {
    const safeCount = Math.max(1, Math.min(count, 10))
    const center = (safeCount - 1) / 2
    const baseSpread = compact ? 24 : 30
    const baseLift = compact ? 10 : 16
    const shift = compact ? 30 : 38
    return Array.from({ length: safeCount }).map((_, idx) => {
      const rel = center === 0 ? 0 : (idx - center) / center
      return {
        rotate: rel * baseSpread,
        y: Math.abs(rel) * baseLift,
        x: rel * shift * Math.min(1.45, 0.9 + safeCount * 0.06),
        depth: 100 - Math.round(Math.abs(rel) * 45) + idx,
      }
    })
  }

  const renderPhotoCardsFan = (cards: PhotoCardItem[], compact = false) => {
    const source = (cards || []).slice(0, 10)
    if (!source.length) return null
    const layout = buildPhotoFanLayout(source.length, compact)
    const sizeClass = source.length >= 8 ? 'is-dense' : source.length >= 5 ? 'is-mid' : 'is-wide'
    return (
      <div className={`photo-cards-fan ${compact ? 'is-compact' : ''} ${sizeClass}`.trim()} aria-label="Распознанные карты">
        {source.map((card, idx) => {
          const name = String(card.card_name || card.title || card.position || `Карта ${idx + 1}`).trim()
          const cardIndex = Number(card.card_index)
          const imageSrc =
            Number.isFinite(cardIndex) && cardIndex >= 0 && cardIndex < FRONT_CARD_URLS.length
              ? FRONT_CARD_URLS[cardIndex]
              : ''
          const tr = layout[idx]
          return (
            <div
              key={`photo-card-${idx}-${name}`}
              className="photo-fan-card"
              style={{
                transform: `translateX(${tr.x}px) translateY(${tr.y}px) rotate(${tr.rotate}deg)`,
                zIndex: tr.depth,
              }}
            >
              {imageSrc ? (
                <img className={card.is_reversed ? 'is-reversed' : ''} src={imageSrc} alt={name} />
              ) : (
                <div className="photo-fan-card__fallback">
                  <span>{name}</span>
                </div>
              )}
            </div>
          )
        })}
      </div>
    )
  }

  const runPhotoDetection = async (incomingFile?: File | null) => {
    if (photoBusy) return
    if (!token) {
      setPhotoStep('error')
      setPhotoError('Нужен вход через Telegram, чтобы отправить фото на анализ.')
      return
    }

    const sourceFile = incomingFile || photoFile
    if (!sourceFile) {
      setPhotoStep('error')
      setPhotoError('Выберите фото расклада (из галереи или сделайте снимок).')
      return
    }

    setPhotoBusy(true)
    setPhotoStep('analyzing')
    setPhotoError('')
    setPhotoDetectedCards([])
    setPhotoInterpretation('')
    setPhotoFollowupQuestion('')
    setPhotoFollowupAnswer('')
    setPhotoFollowupUsed(false)
    setPhotoFollowupError('')

    const reqId = photoReqSeqRef.current + 1
    photoReqSeqRef.current = reqId
    try {
      const preparedFile = await optimizePhotoForUpload(sourceFile, false)

      let out: any = null
      let lastErr: any = null
      for (let attempt = 0; attempt < 2; attempt++) {
        const fileForAttempt = attempt === 0 ? preparedFile : await optimizePhotoForUpload(sourceFile, true)
        try {
          out = await analyzeSpreadPhoto(token, fileForAttempt, {
            topic,
            question: '',
            detect_only: true,
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

      if (photoReqSeqRef.current !== reqId) return
      const normalizedCards = normalizePhotoCards((out as any)?.cards)
      const detection = assessPhotoDetection(normalizedCards)
      if (!detection.ok) {
        setPhotoError(detection.reason)
        setPhotoStep('error')
        return
      }
      setPhotoDetectedCards(detection.cards || [])
      setPhotoStep('detected')
      void refreshBilling(token)
    } catch (err: any) {
      if (photoReqSeqRef.current !== reqId) return
      const raw = String(err?.message || '')
      if (isReadingLimitExceeded(raw)) {
        setPhotoStep('start')
        setPhotoError('')
        setShowAccessPaywall(true)
        void refreshBilling(token)
        return
      }
      setPhotoStep('error')
      setPhotoError(mapPhotoError(raw))
      void refreshBilling(token)
    } finally {
      if (photoReqSeqRef.current === reqId) setPhotoBusy(false)
    }
  }

  const runPhotoInterpretation = async () => {
    if (photoBusy) return
    if (!token || !photoFile) return
    if (photoStep !== 'detected') return

    setPhotoBusy(true)
    setPhotoError('')
    setPhotoFollowupError('')

    const reqId = photoReqSeqRef.current + 1
    photoReqSeqRef.current = reqId
    try {
      const preparedFile = await optimizePhotoForUpload(photoFile, false)
      const out = await analyzeSpreadPhoto(token, preparedFile, {
        topic,
        question: String(photoMainQuestion || '').trim(),
      })
      if (photoReqSeqRef.current !== reqId) return

      const normalizedCards = normalizePhotoCards((out as any)?.cards)
      const detection = assessPhotoDetection(normalizedCards)
      if (detection.ok && detection.cards?.length) {
        setPhotoDetectedCards(detection.cards)
      }

      const desc = String((out as any)?.description || '').trim()
      setPhotoInterpretation(
        desc || buildPhotoFallbackText(detection.ok ? (detection.cards || photoDetectedCards) : photoDetectedCards, photoMainQuestion)
      )
      setPhotoStep('result')
      setPhotoFollowupQuestion('')
      setPhotoFollowupAnswer('')
      setPhotoFollowupUsed(false)
      setPhotoFollowupError('')
      void refreshBilling(token)
    } catch (err: any) {
      if (photoReqSeqRef.current !== reqId) return
      setPhotoError(mapPhotoError(String(err?.message || err || '')))
    } finally {
      if (photoReqSeqRef.current === reqId) setPhotoBusy(false)
    }
  }

  const runPhotoFollowup = async () => {
    if (photoBusy || photoFollowupUsed) return
    if (!token || photoStep !== 'result') return

    const followText = String(photoFollowupQuestion || '').trim()
    if (!followText) {
      setPhotoFollowupError('Введите уточняющий вопрос.')
      return
    }

    setPhotoBusy(true)
    setPhotoFollowupError('')

    const reqId = photoReqSeqRef.current + 1
    photoReqSeqRef.current = reqId
    try {
      const mainQ = String(photoMainQuestion || '').trim()
      const out = await askPhotoFollowup(token, {
        topic,
        main_question: mainQ,
        followup_question: followText,
        cards: photoDetectedCards.map((card) => ({
          position: card.position || '',
          title: card.title || '',
          card_index: Number.isFinite(Number(card.card_index)) ? Number(card.card_index) : null,
          card_name: card.card_name || '',
          is_reversed: !!card.is_reversed,
          meaning: card.meaning || '',
        })),
        base_interpretation: photoInterpretation || '',
      })
      if (photoReqSeqRef.current !== reqId) return
      const desc = String((out as any)?.description || '').trim()
      setPhotoFollowupAnswer(desc || buildPhotoFallbackText(photoDetectedCards, followText))
      setPhotoFollowupUsed(true)
      setPhotoFollowupQuestion('')
      void refreshBilling(token)
    } catch (err: any) {
      if (photoReqSeqRef.current !== reqId) return
      setPhotoFollowupError(mapPhotoError(String(err?.message || err || '')))
    } finally {
      if (photoReqSeqRef.current === reqId) setPhotoBusy(false)
    }
  }

  const onPhotoInputChange = (e: any) => {
    const file = (e?.target?.files?.[0] as File | undefined) || null
    if (e?.target) e.target.value = ''
    if (!file) return
    setPhotoFile(file)
    void runPhotoDetection(file)
  }

  const openPhotoActionSheet = () => {
    if (photoBusy) return
    galleryInputRef.current?.click()
  }

  const photoCardsLabel = photoDetectedCards
    .slice(0, 10)
    .map((card, idx) => String(card.card_name || card.title || card.position || `Карта ${idx + 1}`).trim())
    .filter(Boolean)
    .join(' • ')

  const startNewPhotoReading = () => {
    resetPhotoFlow()
    setPhotoStep('start')
  }

  const retryPhotoDetection = () => {
    setPhotoError('')
    if (photoFile) {
      void runPhotoDetection(photoFile)
      return
    }
    openPhotoActionSheet()
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

  const toForcedCards = (cards?: Array<{ idx: number; isReversed?: boolean }>) => {
    if (!cards || !cards.length) return undefined
    return cards.map((c) => ({
      card_index: clamp(Number(c.idx || 0), 0, 77),
      is_reversed: Boolean(c.isReversed),
    }))
  }

  const warmupCardImages = (cards?: Array<{ url?: string }>) => {
    if (!cards || !cards.length) return
    cards.forEach((c) => {
      const src = String(c?.url || '').trim()
      if (!src) return
      const im = new Image()
      im.decoding = 'async'
      im.src = src
      try {
        ;(im as any).decode?.().catch(() => {})
      } catch {}
    })
  }

  const buildThreeCardsPreview = (): ThreeCardResult[] => {
    const roles = ['Карта 1', 'Карта 2', 'Карта 3']
    const idxs = pickUniqueIndexes(3, FRONT_CARD_URLS.length || 78)
    return idxs.map((idx, i) => {
      const url = FRONT_CARD_URLS[idx] || backCardImg
      const isReversed = Math.random() < 0.5
      const baseName = cardNameFromUrl(url)
      return {
        idx,
        url,
        name: isReversed ? `${baseName} (перевёрнутая)` : baseName,
        role: roles[i] || `Карта ${i + 1}`,
        text: '',
        isReversed,
      }
    })
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

    const seededQuestion = String(question || '').trim()
    // подхватываем вопрос с главной и сразу открываем этап перемешивания
    setThreeQuestion(seededQuestion)
    setThreeScreen('shuffle')
    setView('three_cards_prep')

    try {
      hapticPulse(0.22)
    } catch {}

    void beginThreeShuffle(seededQuestion)
  }

  const beginThreeShuffle = async (questionOverride?: string) => {
    const effectiveQuestion = String(questionOverride ?? threeQuestion).trim()
    if (threeQuestion !== effectiveQuestion) setThreeQuestion(effectiveQuestion)

    if (needsMotionPermission) await requestMotion()

    threeLastAccelRef.current = null
    threeShakeCooldownRef.current = 0
    threeLastPulseRef.current = 0

    // Сразу показываем “выпавшие” карты локально, а текст дотягиваем с сервера.
    const previewCards = buildThreeCardsPreview()
    setThreeCards(previewCards)
    warmupCardImages(previewCards)
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
    buildThreeCardsReal(previewCards, effectiveQuestion).then((cards) => {
      if (threeRequestSeqRef.current !== requestSeq) return
      setThreeCards(cards)
      setThreeLoading(false)
    }).catch((err: any) => {
      if (threeRequestSeqRef.current !== requestSeq) return
      const raw = String(err?.message || err || '')
      if (isReadingLimitExceeded(raw)) {
        setThreeCards(previewCards)
        setThreeDesc('')
        setThreeLoading(false)
        setShowAccessPaywall(true)
        return
      }
      console.warn('[reading] three_cards failed:', err)
      setThreeCards(previewCards)
      setThreeDesc(mapReadingError(raw))
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
    const carryQuestion = String(threeQuestion || question || '').trim()
    resetThreeCardsState()
    setThreeQuestion(carryQuestion)
    setThreeScreen('shuffle')
    void beginThreeShuffle(carryQuestion)
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

  const buildPpfCardsPreview = (): PpfCardResult[] => {
    const roles = ['Прошлое', 'Настоящее', 'Будущее']
    const idxs = pickUniqueIndexes(3, FRONT_CARD_URLS.length || 78)
    return idxs.map((idx, i) => {
      const url = FRONT_CARD_URLS[idx] || backCardImg
      const isReversed = Math.random() < 0.5
      const baseName = cardNameFromUrl(url)
      return {
        idx,
        url,
        name: isReversed ? `${baseName} (перевёрнутая)` : baseName,
        role: roles[i] || '',
        text: '',
        isReversed,
      }
    })
  }

  const resetPpfState = () => {
    if (ppfDragReturnTRef.current) {
      window.clearTimeout(ppfDragReturnTRef.current)
      ppfDragReturnTRef.current = null
    }
    if (ppfAutoDealTRef.current) {
      window.clearTimeout(ppfAutoDealTRef.current)
      ppfAutoDealTRef.current = null
    }

    setPpfScreen('setup')
    setPpfShakeEnabled(false)
    setPpfShuffleProgress(0)
    setPpfReadyToOpen(false)

    setPpfCards([])
    setPpfDeckCards([])
    setPpfPlacedCards([null, null, null])
    setPpfRevealMap([false, false, false])
    setPpfPlacedCount(0)
    ppfPlacedCountRef.current = 0
    setPpfDragging(false)
    setPpfDragDelta(hiddenDragPoint())
    setPpfDragOverSlot(null)
    setPpfActiveFanIndex(3)
    ppfDragOriginRef.current = hiddenDragPoint()
    ppfDragPointerRef.current = null
    ppfDragTouchIdRef.current = null
    unbindGlobalPpfDrag()

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
    const seededQuestion = String(question || '').trim()
    setPpfQuestion(seededQuestion)
    setPpfScreen('shuffle')
    setView('past_present_future_prep')

    try {
      hapticPulse(0.22)
    } catch {}

    void beginPpfShuffle(seededQuestion)
  }

  const beginPpfShuffle = async (questionOverride?: string) => {
    if (ppfDragReturnTRef.current) {
      window.clearTimeout(ppfDragReturnTRef.current)
      ppfDragReturnTRef.current = null
    }
    const effectiveQuestion = String(questionOverride ?? ppfQuestion).trim()
    if (effectiveQuestion !== ppfQuestion) setPpfQuestion(effectiveQuestion)

    ppfLastAccelRef.current = null
    ppfShakeCooldownRef.current = 0
    ppfLastPulseRef.current = 0

    // Сразу показываем карты, чтобы не ждать сеть перед визуальным результатом.
    const previewCards = buildPpfCardsPreview()
    setPpfCards(previewCards)
    setPpfDeckCards(previewCards)
    warmupCardImages(previewCards)
    // Reset description and mark as loading while we fetch from the backend
    setPpfDesc('')
    setPpfLoading(true)
    setPpfDayKey(getVilniusDayKey())

    setPpfScreen('shuffle')
    setPpfShakeEnabled(false)
    setPpfReadyToOpen(false)
    setPpfShuffleProgress(0)
    setPpfPlacedCards([null, null, null])
    setPpfRevealMap([false, false, false])
    setPpfPlacedCount(0)
    ppfPlacedCountRef.current = 0
    setPpfDragging(false)
    setPpfDragDelta(hiddenDragPoint())
    setPpfDragOverSlot(null)
    setPpfActiveFanIndex(3)
    ppfDragOriginRef.current = hiddenDragPoint()
    ppfDragPointerRef.current = null
    ppfDragTouchIdRef.current = null
    unbindGlobalPpfDrag()

    ppfFinishingRef.current = false
    ppfLastSwapAtRef.current = 0

    try {
      hapticPulse(0.35)
    } catch {}

    // подгружаем реальные карты: fallback на мок при ошибках
    buildPpfCardsReal(previewCards, effectiveQuestion).then((cards) => {
      setPpfCards(cards)
      setPpfLoading(false)
    }).catch((err: any) => {
      const raw = String(err?.message || err || '')
      if (isReadingLimitExceeded(raw)) {
        setPpfCards(previewCards)
        setPpfDesc('')
        setPpfLoading(false)
        setShowAccessPaywall(true)
        return
      }
      console.warn('[reading] ppf failed:', err)
      setPpfCards(previewCards)
      setPpfDesc(mapReadingError(raw))
      setPpfLoading(false)
    })
  }

  const ppfReleaseDeckPointer = (target: EventTarget | null, pointerId: number) => {
    const el = target as HTMLElement | null
    if (!el) return
    try {
      el.releasePointerCapture(pointerId)
    } catch {}
  }

  const unbindGlobalPpfDrag = () => {
    if (!ppfGlobalDragCleanupRef.current) return
    ppfGlobalDragCleanupRef.current()
    ppfGlobalDragCleanupRef.current = null
  }

  const bindGlobalPpfDrag = () => {
    if (ppfGlobalDragCleanupRef.current) return

    const onPointerMove = (e: PointerEvent) => onPpfDeckPointerMove(e as any)
    const onPointerUp = (e: PointerEvent) => onPpfDeckPointerUp(e as any)
    const onPointerCancel = (e: PointerEvent) => onPpfDeckPointerCancel(e as any)

    const onTouchMove = (e: TouchEvent) => onPpfDeckTouchMove(e as any)
    const onTouchEnd = (e: TouchEvent) => onPpfDeckTouchEnd(e as any)
    const onTouchCancel = () => onPpfDeckTouchCancel()

    window.addEventListener('pointermove', onPointerMove, { passive: false })
    window.addEventListener('pointerup', onPointerUp, { passive: false })
    window.addEventListener('pointercancel', onPointerCancel, { passive: false })

    window.addEventListener('touchmove', onTouchMove, { passive: false })
    window.addEventListener('touchend', onTouchEnd, { passive: false })
    window.addEventListener('touchcancel', onTouchCancel, { passive: false })

    ppfGlobalDragCleanupRef.current = () => {
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', onPointerUp)
      window.removeEventListener('pointercancel', onPointerCancel)

      window.removeEventListener('touchmove', onTouchMove)
      window.removeEventListener('touchend', onTouchEnd)
      window.removeEventListener('touchcancel', onTouchCancel)
    }
  }

  const placePpfCardToNextSlot = (slotIndexArg?: number) => {
    const slotIndex = typeof slotIndexArg === 'number' ? slotIndexArg : ppfPlacedCountRef.current
    if (slotIndex < 0 || slotIndex > 2) return
    const card = ppfDeckCards[slotIndex]
    if (!card) return

    setPpfPlacedCards((cur) => {
      if (cur[slotIndex]) return cur
      const next = [...cur]
      next[slotIndex] = card
      return next
    })

    const nextCount = slotIndex + 1
    setPpfPlacedCount(nextCount)
    ppfPlacedCountRef.current = nextCount
    setPpfShuffleProgress(nextCount / 3)
    if (ppfDragReturnTRef.current) {
      window.clearTimeout(ppfDragReturnTRef.current)
      ppfDragReturnTRef.current = null
    }
    setPpfDragging(false)
    setPpfDragOverSlot(null)
    setPpfActiveFanIndex(3)
    ppfDragReturnTRef.current = window.setTimeout(() => {
      setPpfDragDelta(hiddenDragPoint())
      ppfDragReturnTRef.current = null
    }, 140)

    window.setTimeout(() => {
      setPpfRevealMap((cur) => {
        if (cur[slotIndex]) return cur
        const next = [...cur]
        next[slotIndex] = true
        return next
      })
    }, 36)

    try {
      hapticPulse(0.34)
    } catch {}

    if (nextCount >= 3) {
      if (ppfAutoDealTRef.current) {
        window.clearTimeout(ppfAutoDealTRef.current)
        ppfAutoDealTRef.current = null
      }
      window.setTimeout(() => finishPpfShuffle(), 320)
    }
  }

  const resolvePpfFanIndex = (target: EventTarget | null, clientX?: number) => {
    const node = target instanceof HTMLElement
      ? (target.closest('[data-ppf-fan-index]') as HTMLElement | null)
      : null

    if (node) {
      const parsed = Number(node.dataset.ppfFanIndex ?? '')
      if (Number.isFinite(parsed)) return clamp(Math.round(parsed), 0, 6)
    }

    const deckRect = ppfDeckRef.current?.getBoundingClientRect()
    if (!deckRect || typeof clientX !== 'number') return 3

    const rel = clamp((clientX - deckRect.left) / Math.max(1, deckRect.width), 0, 0.999)
    return clamp(Math.floor(rel * 7), 0, 6)
  }

  const getPpfFanAnchor = (fanIndex: number, fallbackX: number, fallbackY: number) => {
    const fanEl = ppfFanCardRefs.current[fanIndex]
    if (fanEl) {
      const r = fanEl.getBoundingClientRect()
      return {
        x: r.left + r.width / 2,
        y: r.top + r.height * 0.52,
      }
    }

    const deckRect = ppfDeckRef.current?.getBoundingClientRect()
    if (deckRect) {
      return {
        x: deckRect.left + ((fanIndex + 0.5) / 7) * deckRect.width,
        y: deckRect.top + deckRect.height * 0.38,
      }
    }

    return { x: fallbackX, y: fallbackY }
  }

  const getPpfFanRect = (fanIndex: number, fallbackX: number, fallbackY: number) => {
    const fanEl = ppfFanCardRefs.current[fanIndex]
    if (fanEl) return fanEl.getBoundingClientRect()

    const anchor = getPpfFanAnchor(fanIndex, fallbackX, fallbackY)
    const w = 98
    const h = 147
    return new DOMRect(anchor.x - w / 2, anchor.y - h / 2, w, h)
  }

  const onPpfDeckPointerDown = (e: any) => {
    if (ppfPlacedCountRef.current >= 3) return
    if (e.pointerType === 'touch') return
    if (typeof e.button === 'number' && e.button !== 0) return
    if (ppfDragTouchIdRef.current != null) return

    const fanIndex = resolvePpfFanIndex(e.target, e.clientX)
    setPpfActiveFanIndex(fanIndex)

    ppfDragPointerRef.current = e.pointerId
    const fanRect = getPpfFanRect(fanIndex, e.clientX, e.clientY)
    ppfDragOriginRef.current = { x: fanRect.left, y: fanRect.top }
    const grabX = clamp(e.clientX - fanRect.left, 0, fanRect.width)
    const grabY = clamp(e.clientY - fanRect.top, 0, fanRect.height)
    ppfDragStartRef.current = { x: e.clientX, y: e.clientY }
    ppfDragStartDeltaRef.current = { x: grabX, y: grabY }
    setPpfDragging(true)
    setPpfDragDelta({ x: fanRect.left, y: fanRect.top })
    setPpfDragOverSlot(null)
    bindGlobalPpfDrag()
    try {
      e.preventDefault()
    } catch {}
    try {
      e.stopPropagation()
    } catch {}
    try {
      ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    } catch {}
  }

  const updatePpfDragAt = (clientX: number, clientY: number) => {
    const x = clientX - ppfDragStartDeltaRef.current.x
    const y = clientY - ppfDragStartDeltaRef.current.y
    setPpfDragDelta({ x, y })

    const activeSlot = ppfPlacedCountRef.current
    const slotEl = ppfSlotRefs.current[activeSlot]
    if (!slotEl) {
      setPpfDragOverSlot(null)
      return
    }
    const r = slotEl.getBoundingClientRect()
    const inside = clientX >= r.left && clientX <= r.right && clientY >= r.top && clientY <= r.bottom
    setPpfDragOverSlot(inside ? activeSlot : null)
  }

  const finishPpfDragAt = (clientX: number, clientY: number) => {
    unbindGlobalPpfDrag()
    const activeSlot = ppfPlacedCountRef.current
    const slotEl = ppfSlotRefs.current[activeSlot]
    let inside = false
    if (slotEl) {
      const r = slotEl.getBoundingClientRect()
      inside = clientX >= r.left && clientX <= r.right && clientY >= r.top && clientY <= r.bottom
    }

    if (inside) {
      placePpfCardToNextSlot(activeSlot)
      return
    }

    setPpfDragOverSlot(null)
    if (ppfDragReturnTRef.current) {
      window.clearTimeout(ppfDragReturnTRef.current)
      ppfDragReturnTRef.current = null
    }
    setPpfDragDelta({ ...ppfDragOriginRef.current })
    ppfDragReturnTRef.current = window.setTimeout(() => {
      setPpfDragging(false)
      setPpfDragDelta(hiddenDragPoint())
      setPpfActiveFanIndex(3)
      ppfDragReturnTRef.current = null
    }, 210)
  }

  const onPpfDeckPointerMove = (e: any) => {
    if (ppfDragPointerRef.current !== e.pointerId) return
    try {
      e.preventDefault()
    } catch {}
    updatePpfDragAt(e.clientX, e.clientY)
  }

  const onPpfDeckPointerUp = (e: any) => {
    if (ppfDragPointerRef.current !== e.pointerId) return
    ppfReleaseDeckPointer(ppfDeckRef.current, e.pointerId)
    ppfDragPointerRef.current = null
    finishPpfDragAt(e.clientX, e.clientY)
  }

  const onPpfDeckPointerCancel = (e: any) => {
    if (ppfDragPointerRef.current !== e.pointerId) return
    ppfReleaseDeckPointer(ppfDeckRef.current, e.pointerId)
    ppfDragPointerRef.current = null
    unbindGlobalPpfDrag()
    if (ppfDragReturnTRef.current) {
      window.clearTimeout(ppfDragReturnTRef.current)
      ppfDragReturnTRef.current = null
    }
    setPpfDragging(false)
    setPpfDragDelta(hiddenDragPoint())
    setPpfDragOverSlot(null)
    setPpfActiveFanIndex(3)
  }

  const findTouchById = (touches: TouchList, id: number) => {
    for (let i = 0; i < touches.length; i += 1) {
      const t = touches.item(i)
      if (t && t.identifier === id) return t
    }
    return null
  }

  const onPpfDeckTouchStart = (e: any) => {
    if (ppfPlacedCountRef.current >= 3) return
    if (ppfDragPointerRef.current != null) return
    const t = e.changedTouches.item(0)
    if (!t) return

    const fanIndex = resolvePpfFanIndex(e.target, t.clientX)
    setPpfActiveFanIndex(fanIndex)

    ppfDragTouchIdRef.current = t.identifier
    const fanRect = getPpfFanRect(fanIndex, t.clientX, t.clientY)
    ppfDragOriginRef.current = { x: fanRect.left, y: fanRect.top }
    const grabX = clamp(t.clientX - fanRect.left, 0, fanRect.width)
    const grabY = clamp(t.clientY - fanRect.top, 0, fanRect.height)
    ppfDragStartRef.current = { x: t.clientX, y: t.clientY }
    ppfDragStartDeltaRef.current = { x: grabX, y: grabY }
    setPpfDragging(true)
    setPpfDragDelta({ x: fanRect.left, y: fanRect.top })
    setPpfDragOverSlot(null)
    bindGlobalPpfDrag()
    e.preventDefault()
    e.stopPropagation()
  }

  const onPpfDeckTouchMove = (e: any) => {
    const touchId = ppfDragTouchIdRef.current
    if (touchId == null) return
    const t = findTouchById(e.touches, touchId)
    if (!t) return
    updatePpfDragAt(t.clientX, t.clientY)
    e.preventDefault()
  }

  const onPpfDeckTouchEnd = (e: any) => {
    const touchId = ppfDragTouchIdRef.current
    if (touchId == null) return
    const t = findTouchById(e.changedTouches, touchId)
    if (!t) return
    ppfDragTouchIdRef.current = null
    finishPpfDragAt(t.clientX, t.clientY)
    e.preventDefault()
  }

  const onPpfDeckTouchCancel = () => {
    unbindGlobalPpfDrag()
    ppfDragTouchIdRef.current = null
    if (ppfDragReturnTRef.current) {
      window.clearTimeout(ppfDragReturnTRef.current)
      ppfDragReturnTRef.current = null
    }
    setPpfDragging(false)
    setPpfDragDelta(hiddenDragPoint())
    setPpfDragOverSlot(null)
    setPpfActiveFanIndex(3)
  }

  const finishPpfShuffle = () => {
    if (ppfFinishingRef.current) return
    ppfFinishingRef.current = true

    setPpfShakeEnabled(false)
    setPpfDragging(false)
    setPpfDragDelta(hiddenDragPoint())
    setPpfDragOverSlot(null)
    unbindGlobalPpfDrag()
    if (ppfDragReturnTRef.current) {
      window.clearTimeout(ppfDragReturnTRef.current)
      ppfDragReturnTRef.current = null
    }

    setPpfReadyToOpen(true)
    setPpfShuffleProgress(1)

    window.setTimeout(() => {
      setPpfScreen('result')
      try {
        hapticPulse(0.7)
      } catch {}
    }, 220)
  }

  const shufflePpfOnce = (power01: number) => {
    const p = clamp(power01, 0, 1)
    ppfLastSwapAtRef.current = Date.now()

    setPpfShuffleProgress((cur) => {
      const step = PPF_SHAKE_STEP_BASE + p * 0.11
      const next = clamp(cur + step, 0, 1)

      if (next >= 1) {
        requestAnimationFrame(() => finishPpfShuffle())
      }
      return next
    })
  }

  const autoShufflePpf = () => {
    if (ppfAutoDealTRef.current) {
      window.clearTimeout(ppfAutoDealTRef.current)
      ppfAutoDealTRef.current = null
    }

    const step = () => {
      const nextSlot = ppfPlacedCountRef.current
      if (nextSlot >= 3) {
        ppfAutoDealTRef.current = null
        return
      }
      placePpfCardToNextSlot(nextSlot)
      if (nextSlot < 2) {
        ppfAutoDealTRef.current = window.setTimeout(step, 260)
      } else {
        ppfAutoDealTRef.current = null
      }
    }

    step()
  }

  const restartPpf = () => {
    const carryQuestion = String(ppfQuestion || question || '').trim()
    resetPpfState()
    setPpfQuestion(carryQuestion)
    setPpfScreen('shuffle')
    void beginPpfShuffle(carryQuestion)
  }
  /* =============================================================================================
    [20.4] РАСКЛАД "ПРИНЯТИЕ РЕШЕНИЯ" (2 КАРТЫ): колода снизу + drag/drop + tap flip
  ============================================================================================= */

  type DecisionScreen = 'shuffle' | 'result'
  type DecisionCardResult = { idx: number; url: string; name: string; role: string; text: string; isReversed?: boolean }

  const DECISION_SLOT_LABELS = ['Вариант A', 'Вариант B'] as const
  const DECISION_FAN_CARDS = 9
  const DECISION_TOP_INDEX = DECISION_FAN_CARDS - 1

  const [decisionScreen, setDecisionScreen] = useState<DecisionScreen>('shuffle')
  const [decisionQuestion, setDecisionQuestion] = useState('')
  const [decisionCards, setDecisionCards] = useState<DecisionCardResult[]>([])
  const [decisionDeckCards, setDecisionDeckCards] = useState<DecisionCardResult[]>([])
  const [decisionDayKey, setDecisionDayKey] = useState<string>('')

  const [decisionPlacedCards, setDecisionPlacedCards] = useState<Array<DecisionCardResult | null>>([null, null])
  const [decisionRevealMap, setDecisionRevealMap] = useState<boolean[]>([false, false])
  const [decisionPlacedCount, setDecisionPlacedCount] = useState(0)

  const decisionPlacedCountRef = useRef(0)
  const decisionPlacedCardsRef = useRef<Array<DecisionCardResult | null>>([null, null])
  const decisionFinishQueuedRef = useRef(false)
  const decisionAutoDealTRef = useRef<number | null>(null)
  const decisionRequestSeqRef = useRef(0)

  const [decisionDragging, setDecisionDragging] = useState(false)
  const [decisionDragOverSlot, setDecisionDragOverSlot] = useState<number | null>(null)
  const [decisionActiveFanIndex, setDecisionActiveFanIndex] = useState(DECISION_TOP_INDEX)
  const decisionSlotRefs = useRef<Array<HTMLButtonElement | null>>([])
  const decisionDeckRef = useRef<HTMLDivElement | null>(null)
  const decisionDragCardRef = useRef<HTMLDivElement | null>(null)
  const decisionFanCardRefs = useRef<Array<HTMLSpanElement | null>>([])
  const decisionDragPointerRef = useRef<number | null>(null)
  const decisionDragTouchIdRef = useRef<number | null>(null)
  const decisionGlobalDragCleanupRef = useRef<null | (() => void)>(null)
  const decisionDragOriginRef = useRef(hiddenDragPoint())
  const decisionDragStartDeltaRef = useRef({ x: 0, y: 0 })
  const decisionDragReturnTRef = useRef<number | null>(null)
  const decisionDragPosRef = useRef(hiddenDragPoint())

  // держим актуальный прогресс в refs, чтобы listeners не пересоздавались на каждый тик
  const shuffleProgressRef = useRef(0)
  const threeShuffleProgressRef = useRef(0)
  const ppfShuffleProgressRef = useRef(0)

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
    decisionPlacedCountRef.current = decisionPlacedCount
  }, [decisionPlacedCount])

  useEffect(() => {
    decisionPlacedCardsRef.current = decisionPlacedCards
  }, [decisionPlacedCards])

  useEffect(() => {
    return () => {
      if (decisionAutoDealTRef.current) window.clearTimeout(decisionAutoDealTRef.current)
      if (decisionDragReturnTRef.current) window.clearTimeout(decisionDragReturnTRef.current)
      unbindGlobalDecisionDrag()
    }
  }, [])

  useEffect(() => {
    if (!decisionDragging) return

    const cancelDrag = () => {
      decisionDragPointerRef.current = null
      decisionDragTouchIdRef.current = null
      unbindGlobalDecisionDrag()
      if (decisionDragReturnTRef.current) {
        window.clearTimeout(decisionDragReturnTRef.current)
        decisionDragReturnTRef.current = null
      }
      setDecisionDragging(false)
      setDecisionDragPoint(hiddenDragPoint())
      setDecisionDragOverSlot(null)
      setDecisionActiveFanIndex(DECISION_TOP_INDEX)
    }

    const onVisibility = () => {
      if (document.visibilityState !== 'visible') cancelDrag()
    }

    window.addEventListener('blur', cancelDrag)
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      window.removeEventListener('blur', cancelDrag)
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [decisionDragging])

  const setDecisionDragPoint = (point: { x: number; y: number }) => {
    decisionDragPosRef.current = point
    const el = decisionDragCardRef.current
    if (el) {
      el.style.setProperty('--dx', `${point.x}px`)
      el.style.setProperty('--dy', `${point.y}px`)
    }
  }

  const buildDecisionCardsPreview = (): DecisionCardResult[] => {
    const idxs = pickUniqueIndexes(2, FRONT_CARD_URLS.length || 78)
    return idxs.map((idx, i) => {
      const url = FRONT_CARD_URLS[idx] || backCardImg
      const isReversed = Math.random() < 0.5
      const baseName = cardNameFromUrl(url)
      return {
        idx,
        url,
        name: isReversed ? `${baseName} (перевёрнутая)` : baseName,
        role: DECISION_SLOT_LABELS[i] || '',
        text: '',
        isReversed,
      }
    })
  }

  // =================================================================================================
  // [REAL READINGS] FUNCTIONS TO FETCH REAL LLM INTERPRETATIONS FOR SPREADS
  // These helpers call the backend API (createReading) to get actual card meanings.
  // They gracefully fallback to the existing mock builders when unauthenticated or on errors.
  // =================================================================================================

  // Build 3 cards reading with real LLM meanings
  const buildThreeCardsReal = async (
    previewCards?: ThreeCardResult[],
    questionText?: string,
  ): Promise<ThreeCardResult[]> => {
    if (!token) {
      return (previewCards && previewCards.length ? previewCards : buildThreeCardsMock())
    }
    const effectiveQuestion = String(questionText ?? threeQuestion).trim()
    const params = {
      spread_type: 'three_cards' as const,
      topic: topic,
      question: effectiveQuestion,
      consider_reversed: true,
      forced_cards: toForcedCards(previewCards),
    }
    const reading: any = await createReading(token, params)
    void refreshBilling(token)
    // Save the description from the backend so we can display it in the UI
    setThreeDesc(String(reading?.description ?? ''))
    const roles = ['Карта 1', 'Карта 2', 'Карта 3']
    const mapped = (reading.cards || []).slice(0, 3).map((c: any, i: number) => {
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
    if (mapped.length) return mapped
    return (previewCards && previewCards.length ? previewCards : buildThreeCardsMock())
  }

  // Build Past-Present-Future reading with real LLM meanings
  const buildPpfCardsReal = async (
    previewCards?: PpfCardResult[],
    questionText?: string
  ): Promise<PpfCardResult[]> => {
    if (!token) {
      return (previewCards && previewCards.length ? previewCards : buildPpfCardsMock())
    }
    const effectiveQuestion = String(questionText ?? ppfQuestion).trim()
    const params = {
      spread_type: 'ppf' as const,
      topic: topic,
      question: effectiveQuestion,
      consider_reversed: true,
      forced_cards: toForcedCards(previewCards),
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
    const mapped = (reading.cards || []).slice(0, 3).map((c: any, i: number) => {
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
    if (mapped.length) return mapped
    return (previewCards && previewCards.length ? previewCards : buildPpfCardsMock())
  }

  // Build Decision reading with real LLM meanings
  const buildDecisionCardsReal = async (
    previewCards?: DecisionCardResult[],
    questionText?: string
  ): Promise<DecisionCardResult[]> => {
    if (!token) return previewCards && previewCards.length ? previewCards : buildDecisionCardsPreview()

    const effectiveQuestion =
      String(questionText ?? decisionQuestion ?? question).trim() || 'Выбор между вариантом A и вариантом B'

    const params = {
      spread_type: 'decision' as const,
      topic: topic,
      question: effectiveQuestion,
      consider_reversed: true,
      forced_cards: toForcedCards(previewCards),
    }
    const reading: any = await createReading(token, params)
    void refreshBilling(token)
    // Save description returned from the backend
    setDecisionDesc(String(reading?.description ?? ''))
    const mapped = (reading.cards || []).slice(0, 2).map((c: any, i: number) => {
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
        role: DECISION_SLOT_LABELS[i] || '',
        text,
        isReversed,
      }
    })
    if (mapped.length) return mapped
    return previewCards && previewCards.length ? previewCards : buildDecisionCardsPreview()
  }

  const resetDecisionState = () => {
    setDecisionScreen('shuffle')
    setDecisionCards([])
    setDecisionDeckCards([])
    setDecisionPlacedCards([null, null])
    setDecisionRevealMap([false, false])
    setDecisionPlacedCount(0)
    decisionPlacedCountRef.current = 0
    decisionPlacedCardsRef.current = [null, null]

    // Reset description and loading state for decision reading
    setDecisionDesc('')
    setDecisionLoading(false)

    setDecisionDayKey('')

    decisionFinishQueuedRef.current = false
    decisionRequestSeqRef.current += 1

    if (decisionAutoDealTRef.current) {
      window.clearTimeout(decisionAutoDealTRef.current)
      decisionAutoDealTRef.current = null
    }
    if (decisionDragReturnTRef.current) {
      window.clearTimeout(decisionDragReturnTRef.current)
      decisionDragReturnTRef.current = null
    }
    unbindGlobalDecisionDrag()
    decisionDragPointerRef.current = null
    decisionDragTouchIdRef.current = null
    decisionDragOriginRef.current = hiddenDragPoint()
    decisionDragStartDeltaRef.current = { x: 0, y: 0 }
    setDecisionDragging(false)
    setDecisionDragPoint(hiddenDragPoint())
    setDecisionDragOverSlot(null)
    setDecisionActiveFanIndex(DECISION_TOP_INDEX)
  }

  const openDecision = () => {
    resetDecisionState()
    const q = String(question || '').trim()
    const preview = buildDecisionCardsPreview()
    setDecisionQuestion(q)
    setDecisionDeckCards(preview)
    setDecisionCards(preview)
    warmupCardImages(preview)
    setDecisionScreen('shuffle')
    setView('decision_prep')

    try {
      hapticPulse(0.22)
    } catch {}
  }

  const openDecisionResult = () => {
    setDecisionScreen('result')

    try {
      hapticPulse(0.7)
    } catch {}
  }

  const restartDecision = () => {
    resetDecisionState()
    const q = String(question || decisionQuestion || '').trim()
    const preview = buildDecisionCardsPreview()
    setDecisionQuestion(q)
    setDecisionDeckCards(preview)
    setDecisionCards(preview)
    setDecisionScreen('shuffle')
  }

  const startDecisionReading = (previewCards: DecisionCardResult[]) => {
    setDecisionLoading(true)
    setDecisionDesc('')
    setDecisionDayKey(getVilniusDayKey())
    const req = decisionRequestSeqRef.current + 1
    decisionRequestSeqRef.current = req

    buildDecisionCardsReal(previewCards, decisionQuestion)
      .then((cards) => {
        if (decisionRequestSeqRef.current !== req) return
        setDecisionCards(cards)
        setDecisionLoading(false)
      })
      .catch((err: any) => {
        if (decisionRequestSeqRef.current !== req) return
        const raw = String(err?.message || err || '')
        if (isReadingLimitExceeded(raw)) {
          setDecisionCards(previewCards)
          setDecisionDesc('')
          setDecisionLoading(false)
          setShowAccessPaywall(true)
          return
        }
        console.warn('[reading] decision failed:', err)
        setDecisionCards(previewCards)
        setDecisionDesc(mapReadingError(raw))
        setDecisionLoading(false)
      })
  }

  const queueDecisionFinishIfReady = (reveals?: boolean[]) => {
    const revealed = reveals || decisionRevealMap
    if (decisionPlacedCountRef.current < 2) return
    if (!revealed[0] || !revealed[1]) return
    if (decisionFinishQueuedRef.current) return

    decisionFinishQueuedRef.current = true
    window.setTimeout(() => {
      openDecisionResult()
      decisionFinishQueuedRef.current = false
    }, 220)
  }

  const onDecisionSlotTap = (slotIdx: number) => {
    const card = decisionPlacedCardsRef.current[slotIdx]
    if (!card) return

    setDecisionRevealMap((cur) => {
      if (cur[slotIdx]) return cur
      const next = [...cur]
      next[slotIdx] = true
      queueDecisionFinishIfReady(next)
      return next
    })

    try {
      hapticPulse(0.34)
    } catch {}
  }

  const unbindGlobalDecisionDrag = () => {
    if (!decisionGlobalDragCleanupRef.current) return
    decisionGlobalDragCleanupRef.current()
    decisionGlobalDragCleanupRef.current = null
  }

  const bindGlobalDecisionDrag = () => {
    if (decisionGlobalDragCleanupRef.current) return

    const onPointerMove = (e: PointerEvent) => onDecisionDeckPointerMove(e as any)
    const onPointerUp = (e: PointerEvent) => onDecisionDeckPointerUp(e as any)
    const onPointerCancel = (e: PointerEvent) => onDecisionDeckPointerCancel(e as any)

    const onTouchMove = (e: TouchEvent) => onDecisionDeckTouchMove(e as any)
    const onTouchEnd = (e: TouchEvent) => onDecisionDeckTouchEnd(e as any)
    const onTouchCancel = () => onDecisionDeckTouchCancel()

    window.addEventListener('pointermove', onPointerMove, { passive: false })
    window.addEventListener('pointerup', onPointerUp, { passive: false })
    window.addEventListener('pointercancel', onPointerCancel, { passive: false })

    window.addEventListener('touchmove', onTouchMove, { passive: false })
    window.addEventListener('touchend', onTouchEnd, { passive: false })
    window.addEventListener('touchcancel', onTouchCancel, { passive: false })

    decisionGlobalDragCleanupRef.current = () => {
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', onPointerUp)
      window.removeEventListener('pointercancel', onPointerCancel)
      window.removeEventListener('touchmove', onTouchMove)
      window.removeEventListener('touchend', onTouchEnd)
      window.removeEventListener('touchcancel', onTouchCancel)
    }
  }

  const resolveDecisionFanIndex = (target: EventTarget | null) => {
    const node = target instanceof HTMLElement
      ? (target.closest('[data-decision-fan-index]') as HTMLElement | null)
      : null

    if (node) {
      const parsed = Number(node.dataset.decisionFanIndex ?? '')
      if (Number.isFinite(parsed)) return clamp(Math.round(parsed), 0, DECISION_FAN_CARDS - 1)
    }

    return DECISION_TOP_INDEX
  }

  const getDecisionFanRect = (fanIndex: number, fallbackX: number, fallbackY: number) => {
    const safeIdx = clamp(fanIndex, 0, DECISION_FAN_CARDS - 1)
    const fanEl = decisionFanCardRefs.current[safeIdx]
    if (fanEl) return fanEl.getBoundingClientRect()

    const deckRect = decisionDeckRef.current?.getBoundingClientRect()
    if (deckRect) {
      const anchorX = deckRect.left + deckRect.width * 0.5
      const anchorY = deckRect.top + deckRect.height * 0.5
      const w = 98
      const h = 147
      return new DOMRect(anchorX - w / 2, anchorY - h / 2, w, h)
    }

    return new DOMRect(fallbackX - 49, fallbackY - 74, 98, 147)
  }

  const placeDecisionCardToNextSlot = (slotIndexArg?: number) => {
    const slotIndex = typeof slotIndexArg === 'number' ? slotIndexArg : decisionPlacedCountRef.current
    if (slotIndex < 0 || slotIndex > 1) return
    const card = decisionDeckCards[slotIndex]
    if (!card) return
    const current = decisionPlacedCardsRef.current
    if (current[slotIndex]) return

    const nextPlaced = [...current]
    nextPlaced[slotIndex] = card
    decisionPlacedCardsRef.current = nextPlaced
    setDecisionPlacedCards(nextPlaced)

    const nextCount = slotIndex + 1
    decisionPlacedCountRef.current = nextCount
    setDecisionPlacedCount(nextCount)

    if (decisionDragReturnTRef.current) {
      window.clearTimeout(decisionDragReturnTRef.current)
      decisionDragReturnTRef.current = null
    }
    setDecisionDragging(false)
    setDecisionDragOverSlot(null)
    setDecisionActiveFanIndex(DECISION_TOP_INDEX)
    setDecisionDragPoint(hiddenDragPoint())

    try {
      hapticPulse(0.34)
    } catch {}

    if (nextCount >= 2) {
      startDecisionReading(nextPlaced.filter(Boolean) as DecisionCardResult[])
    }
  }

  const updateDecisionDragAt = (clientX: number, clientY: number) => {
    const x = clientX - decisionDragStartDeltaRef.current.x
    const y = clientY - decisionDragStartDeltaRef.current.y
    setDecisionDragPoint({ x, y })

    const activeSlot = decisionPlacedCountRef.current
    const slotEl = decisionSlotRefs.current[activeSlot]
    if (!slotEl) {
      setDecisionDragOverSlot(null)
      return
    }
    const r = slotEl.getBoundingClientRect()
    const inside = clientX >= r.left && clientX <= r.right && clientY >= r.top && clientY <= r.bottom
    setDecisionDragOverSlot(inside ? activeSlot : null)
  }

  const finishDecisionDragAt = (clientX: number, clientY: number) => {
    unbindGlobalDecisionDrag()
    const activeSlot = decisionPlacedCountRef.current
    const slotEl = decisionSlotRefs.current[activeSlot]
    let inside = false
    if (slotEl) {
      const r = slotEl.getBoundingClientRect()
      inside = clientX >= r.left && clientX <= r.right && clientY >= r.top && clientY <= r.bottom
    }

    if (inside) {
      placeDecisionCardToNextSlot(activeSlot)
      return
    }

    setDecisionDragOverSlot(null)
    if (decisionDragReturnTRef.current) {
      window.clearTimeout(decisionDragReturnTRef.current)
      decisionDragReturnTRef.current = null
    }
    setDecisionDragPoint({ ...decisionDragOriginRef.current })
    decisionDragReturnTRef.current = window.setTimeout(() => {
      setDecisionDragging(false)
      setDecisionDragPoint(hiddenDragPoint())
      setDecisionActiveFanIndex(DECISION_TOP_INDEX)
      decisionDragReturnTRef.current = null
    }, 210)
  }

  const onDecisionDeckPointerDown = (e: any) => {
    if (decisionPlacedCountRef.current >= 2) return
    if (typeof e.button === 'number' && e.button !== 0) return
    if (decisionDragTouchIdRef.current != null) return

    const fanIndex = resolveDecisionFanIndex(e.target)
    setDecisionActiveFanIndex(fanIndex)
    decisionDragPointerRef.current = e.pointerId

    const fanRect = getDecisionFanRect(fanIndex, e.clientX, e.clientY)
    decisionDragOriginRef.current = { x: fanRect.left, y: fanRect.top }
    const grabX = clamp(e.clientX - fanRect.left, 0, fanRect.width)
    const grabY = clamp(e.clientY - fanRect.top, 0, fanRect.height)
    decisionDragStartDeltaRef.current = { x: grabX, y: grabY }

    setDecisionDragging(true)
    setDecisionDragPoint({ x: fanRect.left, y: fanRect.top })
    setDecisionDragOverSlot(null)
    bindGlobalDecisionDrag()

    try {
      e.preventDefault()
      e.stopPropagation()
      ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    } catch {}
  }

  const onDecisionDeckPointerMove = (e: any) => {
    if (decisionDragPointerRef.current !== e.pointerId) return
    try {
      e.preventDefault()
    } catch {}
    updateDecisionDragAt(e.clientX, e.clientY)
  }

  const onDecisionDeckPointerUp = (e: any) => {
    if (decisionDragPointerRef.current !== e.pointerId) return
    decisionDragPointerRef.current = null
    try {
      ;(decisionDeckRef.current as HTMLElement | null)?.releasePointerCapture(e.pointerId)
    } catch {}
    finishDecisionDragAt(e.clientX, e.clientY)
  }

  const onDecisionDeckPointerCancel = (e: any) => {
    if (decisionDragPointerRef.current !== e.pointerId) return
    decisionDragPointerRef.current = null
    unbindGlobalDecisionDrag()
    if (decisionDragReturnTRef.current) {
      window.clearTimeout(decisionDragReturnTRef.current)
      decisionDragReturnTRef.current = null
    }
    setDecisionDragging(false)
    setDecisionDragPoint(hiddenDragPoint())
    setDecisionDragOverSlot(null)
    setDecisionActiveFanIndex(DECISION_TOP_INDEX)
  }

  const findDecisionTouchById = (touches: TouchList, id: number) => {
    for (let i = 0; i < touches.length; i += 1) {
      const t = touches.item(i)
      if (t && t.identifier === id) return t
    }
    return null
  }

  const onDecisionDeckTouchStart = (e: any) => {
    if (decisionPlacedCountRef.current >= 2) return
    if (decisionDragPointerRef.current != null) return
    const t = e.changedTouches.item(0)
    if (!t) return

    const fanIndex = resolveDecisionFanIndex(e.target)
    setDecisionActiveFanIndex(fanIndex)
    decisionDragTouchIdRef.current = t.identifier

    const fanRect = getDecisionFanRect(fanIndex, t.clientX, t.clientY)
    decisionDragOriginRef.current = { x: fanRect.left, y: fanRect.top }
    const grabX = clamp(t.clientX - fanRect.left, 0, fanRect.width)
    const grabY = clamp(t.clientY - fanRect.top, 0, fanRect.height)
    decisionDragStartDeltaRef.current = { x: grabX, y: grabY }

    setDecisionDragging(true)
    setDecisionDragPoint({ x: fanRect.left, y: fanRect.top })
    setDecisionDragOverSlot(null)
    bindGlobalDecisionDrag()
    e.preventDefault()
    e.stopPropagation()
  }

  const onDecisionDeckTouchMove = (e: any) => {
    const touchId = decisionDragTouchIdRef.current
    if (touchId == null) return
    const t = findDecisionTouchById(e.touches, touchId)
    if (!t) return
    updateDecisionDragAt(t.clientX, t.clientY)
    e.preventDefault()
  }

  const onDecisionDeckTouchEnd = (e: any) => {
    const touchId = decisionDragTouchIdRef.current
    if (touchId == null) return
    const t = findDecisionTouchById(e.changedTouches, touchId)
    if (!t) return
    decisionDragTouchIdRef.current = null
    finishDecisionDragAt(t.clientX, t.clientY)
    e.preventDefault()
  }

  const onDecisionDeckTouchCancel = () => {
    unbindGlobalDecisionDrag()
    decisionDragTouchIdRef.current = null
    if (decisionDragReturnTRef.current) {
      window.clearTimeout(decisionDragReturnTRef.current)
      decisionDragReturnTRef.current = null
    }
    setDecisionDragging(false)
    setDecisionDragPoint(hiddenDragPoint())
    setDecisionDragOverSlot(null)
    setDecisionActiveFanIndex(DECISION_TOP_INDEX)
  }

  const autoShuffleDecision = () => {
    if (decisionAutoDealTRef.current) {
      window.clearTimeout(decisionAutoDealTRef.current)
      decisionAutoDealTRef.current = null
    }

    window.setTimeout(() => placeDecisionCardToNextSlot(0), 0)
    window.setTimeout(() => placeDecisionCardToNextSlot(1), 240)
    window.setTimeout(() => onDecisionSlotTap(0), 520)
    decisionAutoDealTRef.current = window.setTimeout(() => onDecisionSlotTap(1), 760)
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

  const revealCardAfterShake = async () => {
    if (!dailyFrontReady && dailyFrontUrl && dailyFrontUrl !== backCardImg) {
      const im = new Image()
      im.decoding = 'async'
      im.src = dailyFrontUrl
      try {
        await (im as any).decode?.()
      } catch {}
    }

    setCardRevealed(true)
    hapticPulse(0.6)
  }

  // ✅ когда карта доехала до рубашки — фиксируем результат шейка
  const onStoppedAtBack = () => {
    setStopRequested(false)
    setCardRevealed(false)
    setShakenOnce(true)
    markDailyOpened(dailyDayKey || getVilniusDayKey())

    // ✅ теперь фронт уже залочен в PremiumFlipCard на dailyFrontUrl,
    // но дополнительно сохраним в selectedFrontUrl, чтобы result точно совпадал
    setSelectedFrontUrl(dailyFrontUrl || backCardImg)

    setTimeout(() => {
      hapticPulse(1)
      try {
        navigator.vibrate?.([35, 18, 85])
      } catch {}
    }, 60)

    // После тряски карта открывается автоматически — без тапа по карте.
    window.setTimeout(() => {
      void revealCardAfterShake()
    }, 120)
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
    const PRE_REVEAL_GAP = 56
    const PRE_REVEAL_SCALE_BOOST = 1.06

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

      // В режиме перемешивания делаем карту чуть крупнее и ниже.
      s = Math.max(0.62, Math.min(1.08, s * PRE_REVEAL_SCALE_BOOST))
      const desiredTop = spreadBottom + PRE_REVEAL_GAP + (BASE_H * s) / 2

      const bottomLimit = panelTop - MARGIN
      const desiredCardBottom = desiredTop + (BASE_H * s) / 2

      if (desiredCardBottom > bottomLimit) {
        const availableH = Math.max(160, bottomLimit - (spreadBottom + PRE_REVEAL_GAP))
        const sH = availableH / BASE_H
        s = Math.max(0.62, Math.min(s, sH))

        const topPx = spreadBottom + PRE_REVEAL_GAP + (BASE_H * s) / 2
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

  const profileName =
    user?.first_name || user?.last_name
      ? `${user?.first_name || ''}${user?.last_name ? ` ${user.last_name}` : ''}`.trim()
      : 'Профиль'
  const profileUsername = user?.username ? `@${user.username}` : '@username'
  const freeLimit = Math.max(1, Number(billing?.free_limit ?? 5))
  const freeLeft = Math.max(0, Number(billing?.free_left ?? 0))
  const subActive = !!billing?.has_active_subscription
  const subLabel = subActive
    ? `Активна до ${formatRuDate(billing?.subscription_until)}`
    : 'Не активна'
  const currentLegalDoc = activeLegalDoc ? LEGAL_DOCS[activeLegalDoc] : null
  const canStartReading = !!spread
  const showHomePrimaryCta = view === 'home' && navTab === 'main'
  const isHomeTourActive = showHomeTour && showHomePrimaryCta
  const homeTourStep = isHomeTourActive ? HOME_TOUR_STEPS[Math.max(0, Math.min(homeTourIndex, HOME_TOUR_STEPS.length - 1))] : null
  const homeTourStepId = homeTourStep?.id || null
  const homeTourSpotlightStyle = homeTourSpotlight
    ? {
        top: `${homeTourSpotlight.top}px`,
        left: `${homeTourSpotlight.left}px`,
        width: `${homeTourSpotlight.width}px`,
        height: `${homeTourSpotlight.height}px`,
        borderRadius: `${homeTourSpotlight.radius}px`,
      }
    : undefined
  const homeTourCardStyle = homeTourSpotlight
    ? ({
        top: `${homeTourSpotlight.bubbleTop}px`,
        left: `${homeTourSpotlight.bubbleLeft}px`,
        width: `${homeTourSpotlight.bubbleWidth}px`,
        ['--tour-arrow-left' as any]: `${homeTourSpotlight.bubbleArrowLeft}px`,
      } as Record<string, string>)
    : undefined
  const homeKeyboardAwarePadding = showHomePrimaryCta
    ? {
        paddingBottom: `calc(${92 + Math.max(0, keyboardInset)}px + env(safe-area-inset-bottom, 0px))`,
      }
    : undefined

  return (
    <div className="app" ref={appRef}>
      <canvas className="stars-canvas" ref={starsCanvasRef} aria-hidden="true" />
      <canvas className="comets-canvas" ref={cometsCanvasRef} aria-hidden="true" />

{/* AUTH OVERLAY */}
{authStatus === 'loading' && (
  <div className="auth-overlay" role="status" aria-live="polite">
    <div className="auth-overlay__card">
      <div className="auth-overlay__ring" aria-hidden="true" />
      <div className="auth-overlay__title">Авторизация…</div>
      <div className="auth-overlay__sub">Проверяем Telegram и загружаем профиль</div>
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
            clearStoredJwt()
            setToken(null)
            setAuthStatus('loading')
            setAuthError('')
            setAuthRetryNonce((n) => n + 1)
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

{authStatus === 'ready' && showLegalConsent && (
  <div className="auth-overlay legal-consent" role="dialog" aria-modal="true" aria-label="Согласие с документами">
    <div className="auth-overlay__card legal-consent__card">
      <div className="legal-consent__title">Перед началом использования</div>
      <div className="legal-consent__text">
        Подтвердите согласие с документами, чтобы продолжить работу в AI Taro.
      </div>

      <div className="legal-consent__links">
        <button type="button" className="legal-consent__link" onClick={() => openLegalDoc('terms')}>
          📄 Пользовательское соглашение
        </button>
        <button type="button" className="legal-consent__link" onClick={() => openLegalDoc('privacy')}>
          🔐 Политика конфиденциальности
        </button>
      </div>

      <label className="legal-consent__check">
        <input
          type="checkbox"
          checked={legalConsentChecked}
          onChange={(e) => setLegalConsentChecked(e.target.checked)}
        />
        <span>Соглашаюсь с Пользовательским соглашением и Политикой конфиденциальности</span>
      </label>

      <button
        type="button"
        className="glass-cta legal-consent__accept"
        disabled={!legalConsentChecked}
        onClick={acceptLegalConsent}
      >
        <span className="glass-cta__inner">
          <span className="glass-cta__rim" aria-hidden="true" />
          <span className="glass-cta__text">Согласиться и продолжить</span>
          <span className="glass-cta__spark" aria-hidden="true" />
        </span>
      </button>
    </div>
  </div>
)}

{isHomeTourActive && homeTourStep && homeTourSpotlight && (
  <div
    className="home-tour-overlay"
    role="dialog"
    aria-modal="true"
    aria-label="Быстрый гид по приложению"
    onClick={nextHomeTourStep}
  >
    <div className="home-tour-overlay__veil" />
    <div
      className={`home-tour-overlay__spotlight is-${homeTourSpotlight.placement}`}
      style={homeTourSpotlightStyle}
      aria-hidden="true"
    />
    <div
      className={`home-tour-card is-${homeTourSpotlight.placement}`}
      style={homeTourCardStyle}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="home-tour-card__head">
        <div className="home-tour-card__title">{homeTourStep.title}</div>
        <button
          type="button"
          className="home-tour-card__close"
          onClick={closeHomeTour}
          aria-label="Пропустить инструкцию"
        >
          ×
        </button>
      </div>
      <div className="home-tour-card__text">{homeTourStep.text}</div>
      <div className="home-tour-card__foot">
        <div className="home-tour-card__step">
          {homeTourIndex + 1} из {HOME_TOUR_STEPS.length}
        </div>
        <div className="home-tour-card__actions">
          {homeTourIndex > 0 && (
            <button
              type="button"
              className="glassbtn home-tour-card__prev"
              onClick={prevHomeTourStep}
            >
              Назад
            </button>
          )}
          <button
            type="button"
            className="glassbtn home-tour-card__skip"
            onClick={closeHomeTour}
          >
            Пропустить
          </button>
          <button
            type="button"
            className="glassbtn home-tour-card__next"
            onClick={nextHomeTourStep}
          >
            {homeTourIndex >= HOME_TOUR_STEPS.length - 1 ? 'Готово' : 'Далее'}
          </button>
        </div>
      </div>
    </div>
  </div>
)}

      <div
        ref={contentRef}
        className={`content ${showHomePrimaryCta ? 'content--with-home-cta' : ''}`}
        style={homeKeyboardAwarePadding}
      >
        {view === 'home' && (
          <>
            <div className={`home-head ${navTab !== 'main' ? 'is-subtab' : ''}`}>
              {navTab === 'main' ? (
                <>
                  <div
                    className={`home-head__brand ${navTab !== 'main' ? 'is-clickable' : ''}`}
                    role={navTab !== 'main' ? 'button' : undefined}
                    tabIndex={navTab !== 'main' ? 0 : -1}
                    onClick={goToMainTab}
                    onKeyDown={(e) => {
                      if (navTab === 'main') return
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        goToMainTab()
                      }
                    }}
                    aria-label={navTab !== 'main' ? 'Вернуться на главную' : undefined}
                  >
                    <h1>AI Taro</h1>
                    <p>Мудрость карт и искусственного интеллекта</p>
                  </div>

                  <div className="home-head__actions" aria-label="Навигация">
                    <button
                      type="button"
                      className="home-head__action"
                      onClick={toggleHistoryTab}
                      aria-label="Открыть историю"
                      title="История"
                    >
                      <svg viewBox="0 0 24 24" aria-hidden="true">
                        <path d="M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z" fill="none" stroke="currentColor" strokeWidth="1.8" />
                        <path d="M12 6v6l4 2" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    </button>

                    <button
                      type="button"
                      className="home-head__action home-head__action--avatar"
                      onClick={toggleProfileTab}
                      aria-label="Открыть профиль"
                      title="Профиль"
                    >
                      {user?.photo_url ? (
                        <img src={user.photo_url} alt="" />
                      ) : (
                        <span>{(user?.first_name?.[0] || user?.username?.[0] || 'U').toUpperCase()}</span>
                      )}
                    </button>
                  </div>
                </>
              ) : (
                <div className="home-head__center-title" aria-live="polite">
                  {navTab === 'profile' ? 'Профиль' : 'История'}
                </div>
              )}
            </div>

            {/* PAGES: slide left/right */}
            <div className="nav-pages" data-dir={navDir} style={{ ['--pi' as any]: navActiveIndex }}>
              <div className="nav-track">
                <div className="nav-page" data-page="main">
                  <div
                    ref={homeCardDayRef}
                    className={`card-day card-day--sun ${isHomeTourActive && homeTourStepId === 'card_day' ? 'is-onboarding-focus' : ''}`}
                    role="button"
                    tabIndex={0}
                    onClick={openCardDay}
                    onKeyDown={(e) => e.key === 'Enter' && openCardDay()}
                  >
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

                  <div
                    ref={homePhotoRef}
                    className={`card-day card-day--photo ${isHomeTourActive && homeTourStepId === 'photo' ? 'is-onboarding-focus' : ''}`}
                    role="button"
                    tabIndex={0}
                    onClick={openPhotoAnalysis}
                    onKeyDown={(e) => e.key === 'Enter' && openPhotoAnalysis()}
                  >
                    <div className="card-day__rim" aria-hidden="true" />
                    <div className="card-day__spark" aria-hidden="true" />
                    <div className="card-day__text">
                    <div className="card-day__title">Фото расклада</div>
                    <div className="card-day__subtitle">
                      <span>Загрузите снимок расклада</span>
                      <span>и получите AI-разбор</span>
                    </div>
                  </div>
                    <div className="card-day__media" aria-hidden="true">
                      <img className="card-day__img" src={cameraIcon} alt="" />
                    </div>
                  </div>

                  <div
                    ref={homeQuestionZoneRef}
                    className={`home-guided-zone ${isHomeTourActive && homeTourStepId === 'question_zone' ? 'is-onboarding-focus' : ''}`}
                  >
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
                          enterKeyHint="search"
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
                      className={`seg seg--topics ${isBumping ? 'is-bump' : ''}`}
                      data-bump={bump}
                      style={{
                        ['--seg-cols' as any]: TOPICS.length,
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
                    <div className={`spread-soft-hint ${shouldAttnSpreads ? 'is-visible' : ''}`} aria-live="polite">
                      Сначала выберите тип расклада
                    </div>

                    <div
                      className={`spread-list ${shouldAttnSpreads ? 'is-attn' : ''}`}
                      data-attn={shouldAttnSpreads ? attnNonce : undefined}
                      ref={spreadListRef}
                    >
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
                  </div>

                </div>

                <div className="nav-page" data-page="history">
                  <div className="history-wrap">
                    <div className="subtab-toolbar">
                      <button
                        type="button"
                        className="subtab-back"
                        onClick={goToMainTab}
                        aria-label="Вернуться на главную"
                      >
                        <span className="subtab-back__arrow" aria-hidden="true">
                          <svg viewBox="0 0 24 24" fill="none">
                            <path
                              d="M14.8 5.5 8.2 12l6.6 6.5M8.6 12h11"
                              stroke="currentColor"
                              strokeWidth="2.8"
                              strokeLinecap="round"
                              strokeLinejoin="round"
                            />
                          </svg>
                        </span>
                        <span className="subtab-back__label">Назад</span>
                      </button>
                    </div>

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
                          const isReadingOpen = isCardDay ? false : openedReadingId === it.reading_id
                          const title = isCardDay
                            ? (it.card_name || 'Карта дня')
                            : (SPREAD_HISTORY_LABELS[it.spread_type] || 'Расклад')
                          const rawThemeCapsule = String(it.theme_capsule || '').trim()
                          const themeCapsule = isGenericThemeCapsule(rawThemeCapsule) ? '' : rawThemeCapsule
                          const subtitle = themeCapsule
                            ? `Тема: ${themeCapsule}`
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
                          const itemKey = isCardDay
                            ? `${it.kind}:${it.day_key || it.created_at}:${it.card_index}:${it.card_name}`
                            : `${it.kind}:${it.reading_id}:${it.created_at}`

                          const handleOpen = () => {
                            if (isCardDay) {
                              openCardDayFromHistory(it)
                              return
                            }
                            setOpenedReadingId((prev) => (prev === it.reading_id ? null : it.reading_id))
                          }

                          return (
                            <Fragment key={itemKey}>
                              <div
                                className={`spread-card spread-card--history ${!isCardDay ? 'is-openable' : ''} ${isReadingOpen ? 'is-open' : ''}`}
                                role="button"
                                tabIndex={0}
                                onClick={handleOpen}
                                onKeyDown={(e) => {
                                  if (e.key === 'Enter' || e.key === ' ') {
                                    e.preventDefault()
                                    handleOpen()
                                  }
                                }}
                                aria-expanded={!isCardDay ? isReadingOpen : undefined}
                                aria-label={isCardDay ? 'Открыть карту дня из истории' : 'Открыть детали расклада из истории'}
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

                                {!isCardDay && (
                                  <div className="history-open-indicator">
                                    {isReadingOpen ? 'Свернуть' : 'Открыть'}
                                  </div>
                                )}
                              </div>

                              {!isCardDay && isReadingOpen && (
                                <div className="history-reading-detail">
                                  <div className="history-reading-detail__head">
                                    <div className="history-reading-detail__title">{title}</div>
                                  <div className="history-reading-detail__meta">
                                    {themeCapsule ? `Тема: ${themeCapsule} • ` : ''}
                                    {topicLabel}
                                    {it.question ? ` • ${it.question}` : ''}
                                    {when ? ` • ${when}` : ''}
                                  </div>
                                  </div>

                                  <div className="history-reading-cards">
                                    {it.cards.map((card, cardIdx) => {
                                      const cardImage = FRONT_CARD_URLS[clamp(card.card_index ?? 0, 0, 77)] || backCardImg
                                      const cardLabel = card.title || card.position || `Карта ${cardIdx + 1}`
                                      return (
                                        <div key={`${itemKey}:card:${cardIdx}`} className="history-reading-card">
                                          <img
                                            className={`history-reading-card__img ${card.is_reversed ? 'is-reversed' : ''}`}
                                            src={cardImage}
                                            alt={card.card_name || cardLabel}
                                          />
                                          <div className="history-reading-card__label">{cardLabel}</div>
                                          <div className="history-reading-card__name">
                                            {card.card_name || `Карта ${cardIdx + 1}`}
                                            {card.is_reversed ? ' (перевёрнутая)' : ''}
                                          </div>
                                        </div>
                                      )
                                    })}
                                  </div>

                                  {!!it.description && (
                                    <MarkdownText text={it.description} className="history-reading-md" />
                                  )}
                                </div>
                              )}
                            </Fragment>
                          )
                        })}
                      </div>
                    )}
                  </div>
                </div>


                <div className="nav-page" data-page="profile">
                  <div className="subtab-toolbar">
                    <button
                      type="button"
                      className="subtab-back"
                      onClick={goToMainTab}
                      aria-label="Вернуться на главную"
                    >
                      <span className="subtab-back__arrow" aria-hidden="true">
                        <svg viewBox="0 0 24 24" fill="none">
                          <path
                            d="M14.8 5.5 8.2 12l6.6 6.5M8.6 12h11"
                            stroke="currentColor"
                            strokeWidth="2.8"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                        </svg>
                      </span>
                      <span className="subtab-back__label">Назад</span>
                    </button>
                  </div>

                  <div className="profile-shell">
                    <div className="profile-hero">
                      <div className="profile-hero__avatar" aria-hidden={!user?.photo_url}>
                        {user?.photo_url ? (
                          <img
                            src={user.photo_url}
                            alt=""
                            className="profile-hero__avatar-image"
                          />
                        ) : (
                          <div className="profile-hero__avatar-fallback">
                            {(user?.first_name?.[0] || user?.username?.[0] || 'U').toUpperCase()}
                          </div>
                        )}
                      </div>

                      <div className="profile-hero__name">{profileName}</div>
                      <div className="profile-hero__username">{profileUsername}</div>
                    </div>

                    <SubscriptionManageCard
                      active={subActive}
                      statusLabel={subLabel}
                      onOpenManage={() => openTelegramAndCloseMiniApp(BOT_SUB_MANAGE_URL)}
                    />

                    {!subActive && (
                      <section className="profile-piece profile-piece--stack" aria-label="Подключить безлимит">
                        <div className="profile-piece__info">
                          <div className="profile-piece__title">
                            <span className="profile-icon profile-icon--piece" aria-hidden="true">
                              <svg viewBox="0 0 24 24">
                                <circle cx="12" cy="12" r="7.2" fill="none" stroke="currentColor" strokeWidth="1.7" />
                                <path d="M12 4.8V3M12 21v-1.8M4.8 12H3M21 12h-1.8" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                              </svg>
                            </span>
                            <span>Подключить безлимит</span>
                          </div>
                          <div className="profile-piece__meta">Бесплатно в этом месяце: {freeLeft} из {freeLimit}</div>
                          <div className="profile-piece__submeta">Выберите удобный способ оплаты</div>
                        </div>

                        <div className="profile-piece__actions">
                          <button
                            type="button"
                            className="profile-piece__cta profile-piece__cta--sbp"
                            disabled={sbpBusyPlan === 'sub_2weeks'}
                            onClick={() => {
                              void startSbpPayment('sub_2weeks')
                            }}
                          >
                            {sbpBusyPlan === 'sub_2weeks' ? 'Создаю…' : 'СБП • 2 недели • 124 ₽'}
                          </button>

                          <button
                            type="button"
                            className="profile-piece__cta profile-piece__cta--sbp"
                            disabled={sbpBusyPlan === 'sub_month'}
                            onClick={() => {
                              void startSbpPayment('sub_month')
                            }}
                          >
                            {sbpBusyPlan === 'sub_month' ? 'Создаю…' : 'СБП • месяц • 224 ₽'}
                          </button>

                          <button
                            type="button"
                            className="profile-piece__cta profile-piece__cta--sbp"
                            disabled={sbpBusyPlan === 'sub_year'}
                            onClick={() => {
                              void startSbpPayment('sub_year')
                            }}
                          >
                            {sbpBusyPlan === 'sub_year' ? 'Создаю…' : 'СБП • год • 1 747 ₽'}
                          </button>

                          <a
                            href={BOT_CARD_URL}
                            target="_blank"
                            rel="noreferrer"
                            className="profile-piece__cta profile-piece__cta--card"
                            onClick={(e) => {
                              e.preventDefault()
                              openTelegramUrl(BOT_CARD_URL)
                            }}
                          >
                            По карте или SberPay
                          </a>

                          <a
                            href={BOT_CLICK_URL}
                            target="_blank"
                            rel="noreferrer"
                            className="profile-piece__cta profile-piece__cta--click"
                            onClick={(e) => {
                              e.preventDefault()
                              openTelegramUrl(BOT_CLICK_URL)
                            }}
                          >
                            CLICK (Узбекистан)
                          </a>

                          <a
                            href={BOT_CLICK_CARD_URL}
                            target="_blank"
                            rel="noreferrer"
                            className="profile-piece__cta profile-piece__cta--click"
                            onClick={(e) => {
                              e.preventDefault()
                              openTelegramUrl(BOT_CLICK_CARD_URL)
                            }}
                          >
                            Карта через CLICK (UZ)
                          </a>
                        </div>

                        {sbpOrderId && (
                          <button
                            type="button"
                            className="profile-piece__check"
                            disabled={sbpPolling}
                            onClick={() => {
                              void checkSbpStatus()
                            }}
                          >
                            {sbpPolling ? 'Проверяю…' : 'Проверить оплату СБП'}
                          </button>
                        )}

                        {!!sbpStatusText && <div className="profile-piece__status">{sbpStatusText}</div>}
                      </section>
                    )}

                    <section className="profile-panel" aria-label="Настройки">
                      <div className="profile-group-title">Настройки</div>

                      <button
                        type="button"
                        className="profile-link-row"
                        onClick={() => {
                          setPrefsError('')
                          setShowPersonalizationModal(true)
                        }}
                      >
                        <span className="profile-link-row__left">
                          <span className="profile-icon profile-icon--row" aria-hidden="true">
                            <svg viewBox="0 0 24 24">
                              <path
                                d="M12 4.2a3 3 0 1 0 0 6 3 3 0 0 0 0-6Zm0 9.6a5.2 5.2 0 1 0 0 10.4 5.2 5.2 0 0 0 0-10.4Zm-8.4-1.6a2.2 2.2 0 1 0 0 4.4 2.2 2.2 0 0 0 0-4.4Zm16.8 0a2.2 2.2 0 1 0 0 4.4 2.2 2.2 0 0 0 0-4.4Z"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="1.6"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                              />
                            </svg>
                          </span>
                          <span>Персонализация AI</span>
                        </span>
                        <span className="profile-link-row__chevron" aria-hidden="true">›</span>
                      </button>

                      <div className="profile-link-row profile-link-row--static">
                        <span className="profile-link-row__left">
                          <span className="profile-icon profile-icon--row" aria-hidden="true">
                            <svg viewBox="0 0 24 24">
                              <circle cx="12" cy="12" r="8.3" fill="none" stroke="currentColor" strokeWidth="1.7" />
                              <path d="M3.7 12h16.6M12 3.7c2.4 2.2 3.7 5.2 3.7 8.3s-1.3 6.1-3.7 8.3M12 3.7c-2.4 2.2-3.7 5.2-3.7 8.3s1.3 6.1 3.7 8.3" fill="none" stroke="currentColor" strokeWidth="1.35" />
                            </svg>
                          </span>
                          <span>Язык приложения (Draft)</span>
                        </span>
                        <span className="profile-link-row__right">Русский</span>
                      </div>

                      <div className="profile-link-row profile-link-row--static">
                        <span className="profile-link-row__left">
                          <span className="profile-icon profile-icon--row" aria-hidden="true">
                            <svg viewBox="0 0 24 24">
                              <path d="M21 3 10 13" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                              <path d="m21 3-7 18-3.4-7.2L3 10l18-7Z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
                            </svg>
                          </span>
                          <span>Привязка Telegram</span>
                        </span>
                        <span className={`profile-link-row__right ${token ? 'is-ok' : 'is-muted'}`}>
                          {token ? 'Подключен' : 'Не подключен'}
                        </span>
                      </div>
                    </section>

                    <section className="profile-panel" aria-label="Информация и поддержка">
                      <div className="profile-group-title">Информация и поддержка</div>

                      <a
                        href={SUPPORT_URL}
                        target="_blank"
                        rel="noreferrer"
                        className="profile-link-row"
                        onClick={(e) => {
                          e.preventDefault()
                          openTelegramAndCloseMiniApp(SUPPORT_URL)
                        }}
                      >
                        <span className="profile-link-row__left">
                          <span className="profile-icon profile-icon--row" aria-hidden="true">
                            <svg viewBox="0 0 24 24">
                              <path d="M6.2 18.4c-1.2 0-2.2-1-2.2-2.2V8.8c0-1.2 1-2.2 2.2-2.2h11.6c1.2 0 2.2 1 2.2 2.2v7.4c0 1.2-1 2.2-2.2 2.2H11l-4 2.7v-2.7H6.2Z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
                            </svg>
                          </span>
                          <span>Написать в поддержку</span>
                        </span>
                        <span className="profile-link-row__chevron" aria-hidden="true">›</span>
                      </a>

                      <a
                        href={TERMS_URL}
                        target="_blank"
                        rel="noreferrer"
                        className="profile-link-row"
                        onClick={(e) => {
                          e.preventDefault()
                          openLegalDoc('terms')
                        }}
                      >
                        <span className="profile-link-row__left">
                          <span className="profile-icon profile-icon--row" aria-hidden="true">
                            <svg viewBox="0 0 24 24">
                              <path d="M7 3.8h7l4 4v12.4H7z" fill="none" stroke="currentColor" strokeWidth="1.6" />
                              <path d="M14 3.8v4h4M9.5 12h6.8M9.5 15.3h6.8" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
                            </svg>
                          </span>
                          <span>Пользовательское соглашение</span>
                        </span>
                        <span className="profile-link-row__chevron" aria-hidden="true">›</span>
                      </a>

                      <a
                        href={PRIVACY_URL}
                        target="_blank"
                        rel="noreferrer"
                        className="profile-link-row"
                        onClick={(e) => {
                          e.preventDefault()
                          openLegalDoc('privacy')
                        }}
                      >
                        <span className="profile-link-row__left">
                          <span className="profile-icon profile-icon--row" aria-hidden="true">
                            <svg viewBox="0 0 24 24">
                              <rect x="6.2" y="10.2" width="11.6" height="9.2" rx="2.2" fill="none" stroke="currentColor" strokeWidth="1.6" />
                              <path d="M8.8 10.2V8a3.2 3.2 0 0 1 6.4 0v2.2" fill="none" stroke="currentColor" strokeWidth="1.6" />
                            </svg>
                          </span>
                          <span>Политика конфиденциальности</span>
                        </span>
                        <span className="profile-link-row__chevron" aria-hidden="true">›</span>
                      </a>
                    </section>

                    <button type="button" className="profile-logout-btn" onClick={handleProfileLogout}>
                      Выйти из аккаунта
                    </button>
                  </div>
                </div>
              </div>
            </div>
          </>
        )}


        {view === 'photo_analysis' && (
          <>
            <h1>AI Taro</h1>
            <p>AI анализ расклада по фото</p>

            <div className={`photo-flow photo-flow--${photoStep}`}>
              <input
                ref={galleryInputRef}
                className="photo-input"
                type="file"
                accept="image/*"
                onChange={onPhotoInputChange}
              />

              {(photoStep === 'start' || photoStep === 'error') && (
                <section className="photo-glass-card photo-glass-card--centered">
                  <h2 className="photo-flow-title">{photoStep === 'error' ? 'Карты не распознаны' : 'Анализ расклада по фото'}</h2>
                  <p className="photo-flow-text">
                    {photoStep === 'error'
                      ? 'Попробуйте сфотографировать расклад снова или выбрать другое фото.'
                      : 'Разложите карты и снимите расклад сверху при хорошем свете.'}
                  </p>

                  <button
                    type="button"
                    className={`photo-upload-block ${photoBusy ? 'is-busy' : ''}`}
                    onClick={openPhotoActionSheet}
                    disabled={photoBusy}
                  >
                    <span className="photo-upload-block__icon" aria-hidden="true">
                      <svg viewBox="0 0 24 24">
                        <path
                          d="M4.6 8.5h2.2l1.2-2h8l1.2 2h2.2a1.8 1.8 0 0 1 1.8 1.8v7.4a1.8 1.8 0 0 1-1.8 1.8H4.6a1.8 1.8 0 0 1-1.8-1.8v-7.4a1.8 1.8 0 0 1 1.8-1.8Z"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="1.8"
                          strokeLinejoin="round"
                        />
                        <circle cx="12" cy="13.1" r="3.2" fill="none" stroke="currentColor" strokeWidth="1.8" />
                      </svg>
                    </span>
                    <span className="photo-upload-block__body">
                      <span className="photo-upload-block__title">{photoStep === 'error' ? 'Сделать фото снова' : 'Сделать фото'}</span>
                      <span className="photo-upload-block__sub">или выбрать из галереи</span>
                    </span>
                    <span className="photo-upload-block__chevron" aria-hidden="true">›</span>
                  </button>

                  <div className="photo-tip-row" aria-hidden="true">
                    <span className="photo-tip-pill">Сверху</span>
                    <span className="photo-tip-pill">Без бликов</span>
                    <span className="photo-tip-pill">Весь расклад</span>
                  </div>

                  {photoStep === 'error' && photoFile && (
                    <button type="button" className="photo-ghost-btn" onClick={retryPhotoDetection} disabled={photoBusy}>
                      Повторить с этим фото
                    </button>
                  )}

                  {photoPreviewUrl && (
                    <div className="photo-preview-frame photo-preview-frame--soft">
                      <img src={photoPreviewUrl} alt="Фото расклада" />
                    </div>
                  )}

                  {photoError ? <div className="photo-stage-error">{photoError}</div> : null}
                </section>
              )}

              {photoStep === 'analyzing' && (
                <section className="photo-glass-card">
                  <h2 className="photo-flow-title">Анализируем расклад</h2>
                  <div className="photo-preview-frame photo-preview-frame--scanning">
                    {photoPreviewUrl ? (
                      <img src={photoPreviewUrl} alt="Фото расклада" />
                    ) : (
                      <div className="photo-preview-frame__empty">Подготовка фото…</div>
                    )}
                  </div>
                  <div className="photo-stage-status">Распознаем карты…</div>
                </section>
              )}

              {photoStep === 'detected' && (
                <section className="photo-glass-card">
                  <h2 className="photo-flow-title">Карты найдены</h2>
                  <div className="photo-cards-stage">
                    {renderPhotoCardsFan(photoDetectedCards, false)}
                  </div>
                  {photoCardsLabel ? <div className="photo-cards-label">{photoCardsLabel}</div> : null}

                  <div className="photo-field">
                    <label className="photo-field__label" htmlFor="photo-main-question">Ваш вопрос</label>
                    <textarea
                      id="photo-main-question"
                      className="photo-field__input"
                      value={photoMainQuestion}
                      onChange={(e) => setPhotoMainQuestion(e.target.value)}
                      placeholder="Что мне важно понять?"
                      rows={3}
                      enterKeyHint="send"
                    />
                  </div>

                  <button
                    type="button"
                    className={`glass-cta photo-main-cta ${photoBusy ? 'is-loading' : ''}`}
                    onClick={runPhotoInterpretation}
                    disabled={photoBusy}
                  >
                    <span className="glass-cta__inner">
                      <span className="glass-cta__rim" aria-hidden="true" />
                      <span className="glass-cta__text">{photoBusy ? 'Готовим ответ…' : 'Получить ответ'}</span>
                      <span className="glass-cta__spark" aria-hidden="true" />
                    </span>
                  </button>

                  <button type="button" className="photo-ghost-btn" onClick={openPhotoActionSheet} disabled={photoBusy}>
                    Выбрать другое фото
                  </button>
                  {photoError ? <div className="photo-stage-error">{photoError}</div> : null}
                </section>
              )}

              {photoStep === 'result' && (
                <section className="photo-glass-card photo-glass-card--result">
                  <h2 className="photo-flow-title">Ваш расклад</h2>
                  <div className="photo-cards-stage is-compact">
                    {renderPhotoCardsFan(photoDetectedCards, true)}
                  </div>

                  <div className="photo-reading-card">
                    <div className="photo-reading-card__title">Интерпретация</div>
                    {renderSafetyNotice(photoMainQuestion)}
                    {photoInterpretation ? (
                      <MarkdownText text={photoInterpretation} />
                    ) : (
                      <p style={{ margin: 0, opacity: 0.8 }}>Не удалось получить интерпретацию. Попробуйте ещё раз.</p>
                    )}
                  </div>

                  <div className="photo-followup-card">
                    <div className="photo-reading-card__title">Уточнить по раскладу</div>

                    {!photoFollowupUsed && (
                      <>
                        <textarea
                          className="photo-field__input photo-field__input--sm"
                          value={photoFollowupQuestion}
                          onChange={(e) => setPhotoFollowupQuestion(e.target.value)}
                          placeholder="Например: что это значит для отношений?"
                          rows={2}
                          enterKeyHint="send"
                        />
                        <button
                          type="button"
                          className={`glass-cta photo-main-cta photo-main-cta--small ${photoBusy ? 'is-loading' : ''}`}
                          onClick={runPhotoFollowup}
                          disabled={photoBusy || !String(photoFollowupQuestion || '').trim()}
                        >
                          <span className="glass-cta__inner">
                            <span className="glass-cta__rim" aria-hidden="true" />
                            <span className="glass-cta__text">{photoBusy ? 'Готовим ответ…' : 'Задать вопрос'}</span>
                            <span className="glass-cta__spark" aria-hidden="true" />
                          </span>
                        </button>
                      </>
                    )}

                    {photoFollowupError ? <div className="photo-stage-error">{photoFollowupError}</div> : null}

                    {photoFollowupAnswer && (
                      <div className="photo-followup-answer">
                        <MarkdownText text={photoFollowupAnswer} />
                      </div>
                    )}

                    {photoFollowupUsed && (
                      <div className="photo-followup-note">
                        Дополнительный вопрос уже использован. Чтобы получить новый разбор, начните новый расклад.
                      </div>
                    )}
                  </div>

                  <button
                    type="button"
                    className="glass-cta photo-main-cta"
                    onClick={startNewPhotoReading}
                    disabled={photoBusy}
                  >
                    <span className="glass-cta__inner">
                      <span className="glass-cta__rim" aria-hidden="true" />
                      <span className="glass-cta__text">Новый расклад</span>
                      <span className="glass-cta__spark" aria-hidden="true" />
                    </span>
                  </button>
                </section>
              )}
            </div>
          </>
        )}

        {view === 'card_day_prep' && (
          <>
            <h1>AI Taro</h1>
            <p ref={subtitleRef}>Мудрость карт и искусственного интеллекта</p>
            {cardDayLoading && (
              <div className="cardday-loader-stage" aria-live="polite">
                <div className="cardday-loader-orb" aria-hidden="true">
                  <div className="cardday-loader-spin cardday-loader-spin--lg" />
                </div>
                <div className="cardday-loader-caption">Подготавливаем расклад…</div>
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

            {!cardDayLoading && (
              <PremiumFlipCard
                key={pflipMountKey}
                frontUrls={SHUFFLE_FRONT_URLS}
                backUrl={backCardImg}
                active={!shakenOnce}
                durationMs={2600}
                intensity={shakeEnabled ? shuffleProgress : 0}
                clickable={false}
                stopAtBack={stopRequested}
                onStoppedAtBack={onStoppedAtBack}
                className={`${shakenOnce ? 'is-done' : ''} ${cardRevealed ? 'is-revealed' : ''} ${isResult ? 'is-top' : ''}`.trim()}
                scale={pflipScale}
                top={pflipTop}
                onFrontChange={setSelectedFrontUrl}
                // До остановки: рандомные карты в перемешивании. После остановки: фикс выпавшей карты.
                lockFront={shakenOnce}
                lockedFrontUrl={dailyFrontUrl || backCardImg}
                lockedFrontReversed={dailyIsReversed}
                previewExcludeUrl={dailyFrontUrl || ''}
              />
            )}

            {!isResult && !cardDayLoading && (
              <>
                <div
                  className={`cardday-shake-hint ${cardDayShuffleStarted ? 'is-hidden' : 'is-visible'}`}
                  aria-hidden={cardDayShuffleStarted ? 'true' : 'false'}
                >
                  <div className="cardday-shake-overlay__phone" />
                  <div className="cardday-shake-overlay__title">Встряхните телефон</div>
                  <div className="cardday-shake-overlay__sub">Или нажмите кнопку ниже для авто‑перемешивания</div>
                </div>

                {needsMotionPermission && !cardDayShuffleStarted && shuffleProgress < 1 && (
                  <div className="motion-permission-focus" aria-hidden="true" />
                )}

                <div
                  className={`bottom-panel bottom-panel--shake cardday-shake-panel ${
                    needsMotionPermission && !cardDayShuffleStarted && shuffleProgress < 1 ? 'is-permission-focus' : ''
                  }`.trim()}
                  ref={bottomPanelRef}
                >
                  {shuffleProgress < 1 ? (
                    <>
                      {needsMotionPermission && (
                        <button type="button" className="motion-permission-cta motion-permission-cta--tech" onClick={requestMotion}>
                          Разрешить встряхивание
                        </button>
                      )}

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
                        <div className="shake__title">Открываем карту…</div>
                        <div className="shake__sub">Сейчас покажем значение карты дня.</div>
                      </div>

                      <button type="button" className="glass-cta mini-cta" disabled>
                        <span className="glass-cta__inner">
                          <span className="glass-cta__rim" aria-hidden="true" />
                          <span className="glass-cta__text">Идёт раскрытие</span>
                          <span className="glass-cta__spark" aria-hidden="true" />
                        </span>
                      </button>
                    </>
                  )}
                </div>
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

                      {renderSafetyNotice(dailyQuestion)}

                      {dailyDesc ? (
                        <MarkdownText text={stripDailyQuestionContextSection(dailyDesc, dailyQuestion)} />
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
            <h1>AI Taro</h1>
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
                          enterKeyHint="search"
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

                    <button type="button" className="glass-cta" onClick={() => { void beginThreeShuffle() }}>
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
                    className={`three-mix-area three-mix-area--wide ${threeShuffleProgress < 1 ? 'is-shuffling' : 'is-done'}`}
                    aria-label="Перемешивание"
                  >
                    {[0, 1, 2].map((cardIdx) => {
                      const slot = Math.max(0, threeOrder.indexOf(cardIdx)) // 0..2
                      const p = THREE_SLOTS_WIDE[slot] || THREE_SLOTS_WIDE[0]

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

                  {threeShuffleProgress < 1 && (
                    <div
                      className={`cardday-shake-overlay ${threeShuffleStarted ? 'is-hidden' : 'is-visible'}`}
                      aria-hidden={threeShuffleStarted ? 'true' : 'false'}
                    >
                      <div className="cardday-shake-overlay__phone" />
                      <div className="cardday-shake-overlay__title">Встряхните телефон</div>
                      <div className="cardday-shake-overlay__sub">Или нажмите кнопку ниже для авто‑перемешивания</div>
                    </div>
                  )}

                  {needsMotionPermission && !threeShuffleStarted && threeShuffleProgress < 1 && (
                    <div className="motion-permission-focus" aria-hidden="true" />
                  )}

                  <div
                    className={`bottom-panel bottom-panel--shake ${
                      needsMotionPermission && !threeShuffleStarted && threeShuffleProgress < 1 ? 'is-permission-focus' : ''
                    }`.trim()}
                  >
                    {threeShuffleProgress < 1 ? (
                      <>
                        {needsMotionPermission && (
                          <button type="button" className="motion-permission-cta motion-permission-cta--tech" onClick={requestMotion}>
                            Разрешить встряхивание
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
                      <div className="threehint">Открываем карты…</div>
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
                  <div className={`three-result ${decisionLoading ? 'is-loading' : ''}`.trim()}>
                    {threeLoading ? (
                      <div className="result-loading-standalone">
                        <InterpretationLoader text="Получаем интерпретацию" />
                      </div>
                    ) : (
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
                            ) : (
                              <p style={{ opacity: 0.72, marginTop: 10, marginBottom: 0 }}>
                                Вопрос не задан. Показана общая интерпретация по картам.
                              </p>
                            )}

                            <p style={{ opacity: 0.82, marginTop: 10, marginBottom: 0 }}>
                              <b>Тип:</b>{' '}
                              {threeKind === 'yesno' ? 'Да / Нет' : threeKind === 'advice' ? 'Совет' : 'Открытый вопрос'}
                            </p>

                            <div style={{ height: 10 }} />

                            {!threeShowMeaning ? (
                              <p style={{ opacity: 0.8, marginTop: 10 }}>Карты раскрываются…</p>
                            ) : (
                              <>
                                {renderSafetyNotice(threeQuestion)}

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
                    )}

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
            <h1>AI Taro</h1>
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
                          enterKeyHint="search"
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

                    <button type="button" className="glass-cta" onClick={() => void beginPpfShuffle()}>
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
                  <div className="ppf-drag-board" aria-label="Расклад с перетаскиванием карт">
                    <div className="ppf-drag-board__slots">
                      {PPF_SLOT_LABELS.map((label, slotIdx) => {
                        const placed = ppfPlacedCards[slotIdx]
                        const isTarget = slotIdx === ppfPlacedCount
                        const isOver = ppfDragOverSlot === slotIdx
                        return (
                          <div
                            key={`${label}-${slotIdx}`}
                            ref={(el) => {
                              ppfSlotRefs.current[slotIdx] = el
                            }}
                            className={`ppf-drop-slot ${isTarget ? 'is-target' : ''} ${isOver ? 'is-over' : ''} ${
                              placed ? 'is-filled' : ''
                            }`}
                          >
                            {placed ? (
                              <div className={`ppf-drop-card ${ppfRevealMap[slotIdx] ? 'is-revealed' : ''}`}>
                                <div className="ppf-drop-card__face ppf-drop-card__face--back">
                                  <img src={backCardImg} alt="" />
                                </div>
                                <div className="ppf-drop-card__face ppf-drop-card__face--front">
                                  <img
                                    className={placed.isReversed ? 'is-reversed' : ''}
                                    src={placed.url || backCardImg}
                                    alt={placed.name}
                                  />
                                </div>
                              </div>
                            ) : null}

                            <div className="ppf-drop-slot__label">{label}</div>
                          </div>
                        )
                      })}
                    </div>

                    {ppfPlacedCount < 3 && (
                      <div
                        className={`ppf-drag-deck ${ppfDragging ? 'is-dragging' : ''}`}
                        aria-hidden="true"
                        ref={ppfDeckRef}
                        onPointerDown={onPpfDeckPointerDown}
                        onPointerMove={onPpfDeckPointerMove}
                        onPointerUp={onPpfDeckPointerUp}
                        onPointerCancel={onPpfDeckPointerCancel}
                        onTouchStart={onPpfDeckTouchStart}
                        onTouchMove={onPpfDeckTouchMove}
                        onTouchEnd={onPpfDeckTouchEnd}
                        onTouchCancel={onPpfDeckTouchCancel}
                      >
                        <div className="ppf-drag-deck__fan">
                          {Array.from({ length: 7 }).map((_, i) => (
                            <span
                              key={i}
                              data-ppf-fan-index={i}
                              ref={(el) => {
                                ppfFanCardRefs.current[i] = el
                              }}
                              className={ppfDragging && ppfActiveFanIndex === i ? 'is-picked' : ''}
                            >
                              <img src={backCardImg} alt="" />
                            </span>
                          ))}
                        </div>

                        <div
                          ref={ppfDragCardRef}
                          className={`ppf-drag-card-live ${ppfDragging ? 'is-dragging' : ''}`}
                          style={{
                            ['--dx' as any]: `${ppfDragDelta.x}px`,
                            ['--dy' as any]: `${ppfDragDelta.y}px`,
                          }}
                        >
                          <img src={backCardImg} alt="" />
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="bottom-panel bottom-panel--shake">
                    {ppfPlacedCount < 3 ? (
                      <>
                        <div className="shake__badge">
                          <div className="shake__title">Вытащите карты из колоды</div>
                          <div className="shake__sub">
                            Зажмите карту снизу и перетащите в подсвеченный пунктирный слот.
                          </div>
                        </div>

                        <div className="threehint">Выложено: {ppfPlacedCount} из 3</div>

                        <button type="button" className="glass-cta mini-cta" onClick={autoShufflePpf}>
                          <span className="glass-cta__inner">
                            <span className="glass-cta__rim" aria-hidden="true" />
                            <span className="glass-cta__text">Разложить автоматически</span>
                            <span className="glass-cta__spark" aria-hidden="true" />
                          </span>
                        </button>
                      </>
                    ) : (
                      <div className="shake__badge is-done">
                        <div className="shake__title">Открываем карты…</div>
                        <div className="shake__sub">Сейчас покажем 3 карты и интерпретацию.</div>
                      </div>
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
                    {ppfLoading ? (
                      <div className="result-loading-standalone">
                        <InterpretationLoader text="Получаем интерпретацию" />
                      </div>
                    ) : (
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
                            {!ppfQuestion && (
                              <p style={{ opacity: 0.72, marginTop: 10, marginBottom: 0 }}>
                                Вопрос не задан. Показана общая интерпретация по картам.
                              </p>
                            )}

                            <p style={{ opacity: 0.86, marginTop: 10 }}>
                              <b>Фокус:</b> {PPF_FOCUS.find((x) => x.id === ppfFocus)?.label || '—'}
                            </p>

                            {renderSafetyNotice(ppfQuestion)}

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
                    )}

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
            <h1>AI Taro</h1>
            <p>Принятие решения</p>

            <div className="threepage">
              {decisionScreen === 'shuffle' && (
                <>
                  <div className="decision-drag-board" aria-label="Расклад по вариантам A и B">
                    <div className="decision-drag-board__slots">
                      {DECISION_SLOT_LABELS.map((label, slotIdx) => {
                        const placed = decisionPlacedCards[slotIdx]
                        const isOver = decisionDragOverSlot === slotIdx
                        const canDrop = slotIdx === decisionPlacedCount
                        const isRevealed = Boolean(decisionRevealMap[slotIdx])
                        const waitingFlip = decisionPlacedCount >= 2 && !!placed && !isRevealed

                        return (
                          <button
                            key={`${label}-${slotIdx}`}
                            ref={(el) => {
                              decisionSlotRefs.current[slotIdx] = el
                            }}
                            type="button"
                            className={`decision-drop-slot ${canDrop ? 'is-target' : ''} ${isOver ? 'is-over' : ''} ${
                              placed ? 'is-filled' : ''
                            } ${waitingFlip ? 'is-awaiting-flip' : ''}`}
                            onClick={() => onDecisionSlotTap(slotIdx)}
                            disabled={!placed || isRevealed}
                          >
                            {placed ? (
                              <div className={`decision-drop-card ${isRevealed ? 'is-revealed' : ''} ${waitingFlip ? 'is-waiting-flip' : ''}`}>
                                <div className="decision-drop-card__face decision-drop-card__face--back">
                                  <img src={backCardImg} alt="" />
                                </div>
                                <div className="decision-drop-card__face decision-drop-card__face--front">
                                  <img
                                    className={placed.isReversed ? 'is-reversed' : ''}
                                    src={placed.url || backCardImg}
                                    alt={placed.name}
                                  />
                                </div>
                              </div>
                            ) : null}

                            {waitingFlip ? <div className="decision-finger-hint" aria-hidden="true" /> : null}
                            <div className="decision-drop-slot__label">{label}</div>
                          </button>
                        )
                      })}
                    </div>

                    {decisionPlacedCount < 2 && (
                      <div
                        className={`decision-drag-deck ${decisionDragging ? 'is-dragging' : ''}`}
                        ref={decisionDeckRef}
                        onPointerDown={onDecisionDeckPointerDown}
                        onPointerMove={onDecisionDeckPointerMove}
                        onPointerUp={onDecisionDeckPointerUp}
                        onPointerCancel={onDecisionDeckPointerCancel}
                        onTouchStart={onDecisionDeckTouchStart}
                        onTouchMove={onDecisionDeckTouchMove}
                        onTouchEnd={onDecisionDeckTouchEnd}
                        onTouchCancel={onDecisionDeckTouchCancel}
                      >
                        <div className="decision-drag-deck__fan">
                          {Array.from({ length: DECISION_FAN_CARDS }).map((_, i) => (
                            <span
                              key={i}
                              data-decision-fan-index={i}
                              ref={(el) => {
                                decisionFanCardRefs.current[i] = el
                              }}
                              className={`${i === DECISION_TOP_INDEX ? 'is-top' : ''} ${
                                decisionDragging && decisionActiveFanIndex === i ? 'is-picked' : ''
                              }`}
                            >
                              <img src={backCardImg} alt="" />
                            </span>
                          ))}
                        </div>

                        <div ref={decisionDragCardRef} className={`decision-drag-card-live ${decisionDragging ? 'is-dragging' : ''}`}>
                          <img src={backCardImg} alt="" />
                        </div>
                      </div>
                    )}
                  </div>

                  <div className="bottom-panel bottom-panel--shake">
                    {decisionPlacedCount < 2 || !decisionRevealMap.every(Boolean) ? (
                      <>
                        {decisionPlacedCount < 2 ? (
                          <div className="threehint">Выложено: {decisionPlacedCount} из 2</div>
                        ) : (
                          <div className="decision-flip-caption">Нажмите на карту, чтобы её перевернуть</div>
                        )}

                        {decisionPlacedCount < 2 ? (
                          <button type="button" className="glass-cta mini-cta" onClick={autoShuffleDecision}>
                            <span className="glass-cta__inner">
                              <span className="glass-cta__rim" aria-hidden="true" />
                              <span className="glass-cta__text">Разложить автоматически</span>
                              <span className="glass-cta__spark" aria-hidden="true" />
                            </span>
                          </button>
                        ) : null}
                      </>
                    ) : (
                      <div className="threehint">Открываем интерпретацию…</div>
                    )}
                  </div>
                </>
              )}

              {decisionScreen === 'result' && (
                <>
                  <div className="decision-result-row" aria-label="Две карты (результат)">
                    {(decisionCards.length ? decisionCards : []).map((c, i) => (
                      <div key={`${c.idx}-${i}`} className="threecard">
                        <img className={c.isReversed ? 'is-reversed' : ''} src={c.url || backCardImg} alt={c.name} />
                      </div>
                    ))}
                  </div>

                  <div className="three-result">
                    {decisionLoading ? (
                      <div className="result-loading-standalone">
                        <InterpretationLoader text="Получаем интерпретацию" />
                      </div>
                    ) : (
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

                            {renderSafetyNotice(decisionQuestion)}

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
                    )}

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

      {showHomePrimaryCta && (
        <div
          ref={homePrimaryFooterRef}
          className={`home-primary-footer ${isHomeTourActive && homeTourStepId === 'cta' ? 'is-onboarding-focus' : ''}`}
          style={{ bottom: `${Math.max(0, keyboardInset)}px` }}
        >
          <button
            ref={btnRef}
            type="button"
            className={`glass-cta glass-cta--primary-footer ${pressed ? 'pressed' : ''} ${ctaError ? 'is-error' : ''} ${canStartReading ? 'is-ready' : 'is-inactive'}`}
            onPointerDown={onGlassPointerDown}
            onPointerUp={onGlassPointerUp}
            onPointerCancel={onGlassPointerUp}
            onPointerLeave={onGlassPointerUp}
            onClick={onBeginReading}
            aria-disabled={!canStartReading}
          >
            <span className="glass-cta__inner">
              <span className="glass-cta__rim" aria-hidden="true" />
              <span className="glass-cta__icon" aria-hidden="true">
                <StartReadingIcon />
              </span>
              <span className="glass-cta__text">Начать расклад</span>
              <span className="glass-cta__spark" aria-hidden="true" />
            </span>
          </button>
        </div>
      )}

      {currentLegalDoc && (
        <div
          className="legal-doc-overlay"
          role="dialog"
          aria-modal="true"
          aria-label={currentLegalDoc.title}
          onClick={closeLegalDoc}
        >
          <div className="legal-doc-card" onClick={(e) => e.stopPropagation()}>
            <div className="legal-doc-card__head">
              <div className="legal-doc-card__title">{currentLegalDoc.title}</div>
              <button
                type="button"
                className="legal-doc-card__close"
                onClick={closeLegalDoc}
                aria-label="Закрыть документ"
              >
                ×
              </button>
            </div>

            <div className="legal-doc-card__embed">
              <iframe
                src={`${currentLegalDoc.pdf}#toolbar=0&navpanes=0&view=FitH`}
                title={currentLegalDoc.title}
                loading="lazy"
              />
            </div>

            <div className="legal-doc-card__fallback">
              <p>{currentLegalDoc.intro}</p>
              {currentLegalDoc.body.map((line, idx) => (
                <p key={`${activeLegalDoc}:line:${idx}`}>{line}</p>
              ))}
            </div>

            <div className="legal-doc-card__actions">
              <a
                href={currentLegalDoc.pdf}
                target="_blank"
                rel="noreferrer"
                className="legal-doc-card__action"
              >
                Открыть PDF
              </a>
              <button
                type="button"
                className="legal-doc-card__action legal-doc-card__action--ghost"
                onClick={() => openTelegramAndCloseMiniApp(LEGAL_DOC_BOT_DEEPLINK[activeLegalDoc || 'terms'])}
              >
                Открыть в боте
              </button>
            </div>
          </div>
        </div>
      )}

      {showPersonalizationModal && (
        <div
          className="prefs-modal-overlay"
          role="dialog"
          aria-modal="true"
          aria-label="Персонализация AI"
          onClick={() => {
            if (!prefsSaving) setShowPersonalizationModal(false)
          }}
        >
          <div className="prefs-modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="prefs-modal-card__head">
              <div className="prefs-modal-card__title">Персонализация AI</div>
              <button
                type="button"
                className="prefs-modal-card__close"
                onClick={() => setShowPersonalizationModal(false)}
                aria-label="Закрыть"
              >
                ×
              </button>
            </div>

            <p className="prefs-modal-card__text">
              Используем вашу историю раскладов для более точной интерпретации. Отдельную «вторую историю» не создаём.
            </p>

            <div className="prefs-modal-card__row">
              <div className="prefs-modal-card__row-text">
                <div className="prefs-modal-card__row-title">Память раскладов (90 дней)</div>
                <div className="prefs-modal-card__row-sub">Учитываем повторяющиеся темы и динамику по похожим вопросам.</div>
              </div>
              <label className="prefs-switch" aria-label="Память раскладов">
                <input
                  type="checkbox"
                  checked={memoryOptIn}
                  onChange={(e) => setMemoryOptIn(e.target.checked)}
                  disabled={prefsSaving}
                />
                <span />
              </label>
            </div>

            <p className="prefs-modal-card__hint">
              Для полного удаления персональных данных используйте команду <b>/forgetme</b> в боте.
            </p>

            {prefsError ? <div className="prefs-modal-card__error">{prefsError}</div> : null}

            <button
              type="button"
              className="glass-cta prefs-modal-card__save"
              disabled={prefsSaving}
              onClick={() => {
                void savePersonalization()
              }}
            >
              <span className="glass-cta__inner">
                <span className="glass-cta__rim" aria-hidden="true" />
                <span className="glass-cta__text">{prefsSaving ? 'Сохраняем…' : 'Сохранить'}</span>
                <span className="glass-cta__spark" aria-hidden="true" />
              </span>
            </button>
          </div>
        </div>
      )}

      {showAccessPaywall && (
        <div className="paywall-overlay" role="dialog" aria-modal="true" aria-label="Доступ к раскладам">
          <div className="paywall-card">
            <div className="paywall-card__title">Бесплатные расклады закончились</div>
            <div className="paywall-card__text">
              Подключите подписку, чтобы продолжить пользоваться приложением без ограничений.
            </div>
            <div className="paywall-card__meta">
              В этом месяце: {Math.max(0, Number(billing?.free_left ?? 0))} бесплатных из {Math.max(1, Number(billing?.free_limit ?? 5))}
            </div>

            <button
              type="button"
              className="glass-cta paywall-card__cta"
              onClick={() => {
                openTelegramUrl(BOT_PAYMENT_URL)
              }}
            >
              <span className="glass-cta__inner">
                <span className="glass-cta__rim" aria-hidden="true" />
                <span className="glass-cta__text">Купить подписку</span>
                <span className="glass-cta__spark" aria-hidden="true" />
              </span>
            </button>

            <button
              type="button"
              className="glass-cta paywall-card__cta paywall-card__cta--ghost"
              onClick={() => {
                setShowAccessPaywall(false)
                setView('home')
                setNavTab('profile')
              }}
            >
              <span className="glass-cta__inner">
                <span className="glass-cta__rim" aria-hidden="true" />
                <span className="glass-cta__text">Открыть профиль</span>
                <span className="glass-cta__spark" aria-hidden="true" />
              </span>
            </button>

            <button type="button" className="paywall-card__close" onClick={() => setShowAccessPaywall(false)}>
              Позже
            </button>
          </div>
        </div>
      )}

      {showKeyboardToolbar && (
        <div
          className="keyboard-toolbar"
          style={{ bottom: `calc(env(safe-area-inset-bottom, 0px) + ${Math.max(8, keyboardInset + 8)}px)` }}
        >
          <span className="keyboard-toolbar__spacer" aria-hidden="true" />

          <button
            type="button"
            className="keyboard-toolbar__btn keyboard-toolbar__btn--done"
            onClick={() => {
              const active = document.activeElement
              if (active instanceof HTMLElement) active.blur()
            }}
          >
            Готово
          </button>
        </div>
      )}
    </div>
  )
}
