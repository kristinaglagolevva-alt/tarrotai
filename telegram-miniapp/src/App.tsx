/* =================================================================================================
   [1] ИМПОРТЫ
================================================================================================= */

import { Fragment, useEffect, useMemo, useRef, useState } from 'react'
import './App.css'
import {
  telegramAuth,
  bootstrapBotChat,
  getMe,
  updateMePreferences,
  getBillingStatus,
  getBillingPlans,
  createSbpPayment,
  getSbpPaymentStatus,
  createClickPayment,
  getClickPaymentStatus,
  createCardOfDay,
  getUnifiedHistory,
  analyzeSpreadPhoto,
  askPhotoFollowup,
  createReading,
} from './api'
import type {
  CardOfDayDto,
  MeDto,
  SbpPlanCode,
  ClickPlanCode,
  AppLanguage,
} from './api'
import SubscriptionManageCard from './features/profile/SubscriptionManageCard'
import { metrikaHit, metrikaReachGoal } from './metrika'



import micIcon from './assets/icons/microphone.svg'
import cardDayIcon from './assets/icons/card_day_icon.png'
import cameraIcon from './assets/icons/camera.png'
import futureIcon from './assets/icons/future_icon.png'
import threeCardIcon from './assets/icons/three_card_icon.png'
import selectCardIcon from './assets/icons/select_icon.png'
import startSunUserWhiteIcon from './assets/icons/start-sun-user-white-outline.png'

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

function CardDayMysticLoader({
  caption,
  compact = false,
  className = '',
}: {
  caption: string
  compact?: boolean
  className?: string
}) {
  return (
    <div className={`cardday-loader-stage ${compact ? 'cardday-loader-stage--compact' : ''} ${className}`.trim()} aria-live="polite">
      <div className="cardday-loader-orb" aria-hidden="true">
        <span className="cardday-loader-halo" />
        <span className="cardday-loader-ring cardday-loader-ring--a" />
        <span className="cardday-loader-ring cardday-loader-ring--b" />
        <span className="cardday-loader-core">✦</span>
      </div>
      <div className="cardday-loader-caption">{caption}</div>
      <div className="cardday-loader-dots" aria-hidden="true">
        <span />
        <span />
        <span />
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

const TOPIC_LABELS: Record<AppLanguage, Record<Topic, string>> = {
  ru: {
    other: 'Другое',
    relations: 'Отношения',
    career: 'Карьера',
    finance: 'Финансы',
  },
  en: {
    other: 'Other',
    relations: 'Relationships',
    career: 'Career',
    finance: 'Finance',
  },
  uz: {
    other: 'Boshqa',
    relations: 'Munosabatlar',
    career: 'Karyera',
    finance: 'Moliya',
  },
}

const TOPIC_ORDER: Topic[] = ['other', 'relations', 'career', 'finance']

const buildTopics = (lang: AppLanguage): { id: Topic; label: string }[] =>
  TOPIC_ORDER.map((id) => ({ id, label: TOPIC_LABELS[lang]?.[id] || TOPIC_LABELS.ru[id] }))

type SafetyKind = 'medical' | 'crisis'

type SafetyNoticeData = {
  kind: SafetyKind
  title: string
  message: string
  contacts: string[]
}

const SELF_HARM_RE = /суицид|самоубий|поконч(ить|у)\s+с\s+собой|не\s+хочу\s+жить|убить\s+себя|самоповреж|self[-\s]?harm|suicid|kill\s+myself|end\s+my\s+life/i
const MEDICAL_RE = /болезн|заболев|симптом|диагноз|лечени|лекарств|температур|боль|депресси|тревог|паник|врач|doctor|symptom|diagnos|disease|illness|medicine|panic|anxiety/i

const US_TZ_RE = /America\/(New_York|Chicago|Denver|Los_Angeles|Anchorage|Phoenix|Detroit|Indiana|Adak|Boise|Juneau|Sitka|Metlakatla|Yakutat|Nome)/i

const normalizeAppLanguage = (raw: string | null | undefined): AppLanguage => {
  const val = String(raw || '').trim().toLowerCase().replace('_', '-')
  if (!val) return 'ru'
  if (val === 'ru' || val.startsWith('ru')) return 'ru'
  if (val === 'uz' || val.startsWith('uz')) return 'uz'
  if (val === 'en' || val.startsWith('en')) return 'en'
  return 'ru'
}

const getRuntimeLocale = () => {
  const tgLang = String((window as any)?.Telegram?.WebApp?.initDataUnsafe?.user?.language_code || '').trim().toLowerCase()
  if (tgLang) return tgLang
  return String(navigator.language || '').trim().toLowerCase()
}

const detectRuntimeAppLanguage = (): AppLanguage => normalizeAppLanguage(getRuntimeLocale())

const APP_LANG_STORAGE_KEY = 'ai_taro_app_language'
const APP_LANG_MANUAL_KEY = 'ai_taro_app_language_manual'

const readStoredAppLanguage = (): AppLanguage | null => {
  try {
    const raw = localStorage.getItem(APP_LANG_STORAGE_KEY)
    return raw ? normalizeAppLanguage(raw) : null
  } catch {
    return null
  }
}

const readLanguageManualFlag = (): boolean => {
  try {
    return localStorage.getItem(APP_LANG_MANUAL_KEY) === '1'
  } catch {
    return false
  }
}

const UI_TEXT: Record<AppLanguage, Record<string, string>> = {
  ru: {
    appSubtitle: 'Мудрость карт и искусственного интеллекта',
    cardOfDay: 'Карта дня',
    dailyGuideLine1: 'Ежедневное руководство',
    dailyGuideLine2: 'от Вселенной',
    photoSpread: 'Фото расклада',
    photoSpreadLine1: 'Загрузите снимок расклада',
    photoSpreadLine2: 'и получите AI-разбор',
    askQuestion: 'Задайте ваш вопрос',
    questionPlaceholder: 'Что вас беспокоит? О чем хотели бы узнать?',
    chooseTopic: 'Выберите категорию вопроса',
    chooseSpread: 'Выберите тип расклада',
    pickSpreadFirst: 'Сначала выберите тип расклада',
    startReading: 'Начать расклад',
    profile: 'Профиль',
    history: 'История',
    settings: 'Настройки',
    personalization: 'Персонализация AI',
    appLanguage: 'Язык приложения',
    telegramBinding: 'Привязка Telegram',
    connected: 'Подключен',
    disconnected: 'Не подключен',
    infoSupport: 'Информация и поддержка',
    writeSupport: 'Написать в поддержку',
    terms: 'Пользовательское соглашение',
    privacy: 'Политика конфиденциальности',
    saveFailed: 'Не удалось сохранить. Попробуйте ещё раз.',
    authRequired: 'Нужна авторизация',
    authErrorDefault: 'Ошибка авторизации.',
    retry: 'Повторить',
    close: 'Закрыть',
    beforeStart: 'Перед началом использования',
    acceptDocsToContinue: 'Подтвердите согласие с документами, чтобы продолжить работу в AI Taro.',
    consentLine: 'Соглашаюсь с Пользовательским соглашением и Политикой конфиденциальности',
    continue: 'Продолжить',
    agreeContinue: 'Согласиться и продолжить',
    loadingApp: 'Загрузка приложения…',
    waitSeconds: 'Пожалуйста, подождите пару секунд',
  },
  en: {
    appSubtitle: 'Wisdom of cards and artificial intelligence',
    cardOfDay: 'Card of the Day',
    dailyGuideLine1: 'Daily guidance',
    dailyGuideLine2: 'from the Universe',
    photoSpread: 'Photo spread',
    photoSpreadLine1: 'Upload a photo of your spread',
    photoSpreadLine2: 'and get an AI interpretation',
    askQuestion: 'Ask your question',
    questionPlaceholder: 'What worries you? What would you like to know?',
    chooseTopic: 'Choose question category',
    chooseSpread: 'Choose spread type',
    pickSpreadFirst: 'Select a spread type first',
    startReading: 'Start reading',
    profile: 'Profile',
    history: 'History',
    settings: 'Settings',
    personalization: 'AI personalization',
    appLanguage: 'App language',
    telegramBinding: 'Telegram binding',
    connected: 'Connected',
    disconnected: 'Not connected',
    infoSupport: 'Info and support',
    writeSupport: 'Contact support',
    terms: 'Terms of service',
    privacy: 'Privacy policy',
    saveFailed: 'Could not save. Please try again.',
    authRequired: 'Authorization required',
    authErrorDefault: 'Authorization error.',
    retry: 'Retry',
    close: 'Close',
    beforeStart: 'Before you start',
    acceptDocsToContinue: 'Please accept the documents to continue using AI Taro.',
    consentLine: 'I agree with the Terms of service and Privacy policy',
    continue: 'Continue',
    agreeContinue: 'Agree and continue',
    loadingApp: 'Loading app…',
    waitSeconds: 'Please wait a few seconds',
  },
  uz: {
    appSubtitle: "Kartalar donoligi va sun'iy intellekt",
    cardOfDay: 'Kun kartasi',
    dailyGuideLine1: "Har kunlik yo'riqnoma",
    dailyGuideLine2: 'Koinotdan',
    photoSpread: 'Foto yoyilma',
    photoSpreadLine1: 'Yoyilma rasmini yuklang',
    photoSpreadLine2: 'va AI tahlilini oling',
    askQuestion: 'Savolingizni kiriting',
    questionPlaceholder: "Sizni nima bezovta qiladi? Nima bilmoqchisiz?",
    chooseTopic: 'Savol toifasini tanlang',
    chooseSpread: 'Yoyilma turini tanlang',
    pickSpreadFirst: 'Avval yoyilma turini tanlang',
    startReading: 'Yoyilmani boshlash',
    profile: 'Profil',
    history: 'Tarix',
    settings: 'Sozlamalar',
    personalization: 'AI shaxsiylashtirish',
    appLanguage: 'Ilova tili',
    telegramBinding: 'Telegram bog‘lanishi',
    connected: 'Ulangan',
    disconnected: 'Ulanmagan',
    infoSupport: "Ma'lumot va yordam",
    writeSupport: 'Yordamga yozish',
    terms: 'Foydalanuvchi kelishuvi',
    privacy: 'Maxfiylik siyosati',
    saveFailed: "Saqlab bo'lmadi. Qayta urinib ko'ring.",
    authRequired: 'Avtorizatsiya kerak',
    authErrorDefault: 'Avtorizatsiya xatosi.',
    retry: 'Qayta urinish',
    close: 'Yopish',
    beforeStart: 'Ishni boshlashdan oldin',
    acceptDocsToContinue: "AI Taro ishlashini davom ettirish uchun hujjatlarga rozilik bering.",
    consentLine: "Foydalanuvchi kelishuvi va Maxfiylik siyosatiga roziman",
    continue: 'Davom etish',
    agreeContinue: 'Roziman va davom etish',
    loadingApp: 'Ilova yuklanmoqda…',
    waitSeconds: 'Iltimos, bir necha soniya kuting',
  },
}

const languageDisplayName = (lang: AppLanguage, locale: AppLanguage): string => {
  const labels: Record<AppLanguage, Record<AppLanguage, string>> = {
    ru: { ru: 'Русский', en: 'English', uz: 'Oʻzbekcha' },
    en: { ru: 'Russian', en: 'English', uz: 'Uzbek' },
    uz: { ru: 'Ruscha', en: 'Inglizcha', uz: "O'zbekcha" },
  }
  return labels[locale]?.[lang] || labels.ru[lang]
}

const languageAutoLabel = (locale: AppLanguage): string => {
  if (locale === 'en') return 'Automatic (Telegram language)'
  if (locale === 'uz') return "Avtomatik (Telegram tili)"
  return 'Автоматически (язык Telegram)'
}

const languageCurrentLabel = (locale: AppLanguage, runtime: AppLanguage): string => {
  if (locale === 'en') return `Current: ${languageDisplayName(runtime, locale)}`
  if (locale === 'uz') return `Hozir: ${languageDisplayName(runtime, locale)}`
  return `Сейчас: ${languageDisplayName(runtime, locale)}`
}

const getRuntimeTimeZone = () => {
  try {
    return String(Intl.DateTimeFormat().resolvedOptions().timeZone || '')
  } catch {
    return ''
  }
}

const buildSafetyNotice = (questionRaw: string, language: AppLanguage): SafetyNoticeData | null => {
  const question = String(questionRaw || '').trim()
  if (!question) return null

  const isCrisis = SELF_HARM_RE.test(question)
  const isMedical = isCrisis || MEDICAL_RE.test(question)
  if (!isMedical) return null

  const lang = normalizeAppLanguage(language)
  const locale = getRuntimeLocale()
  const tz = getRuntimeTimeZone()
  const isUsRegion = locale.startsWith('en-us') || US_TZ_RE.test(tz)

  if (isCrisis) {
    if (isUsRegion) {
      return {
        kind: 'crisis',
        title: 'Safety first',
        message: 'This looks like a crisis topic. Please contact emergency help right now and talk to a live specialist.',
        contacts: ['US/Canada Suicide & Crisis Lifeline: 988', 'Emergency services: 911'],
      }
    }
    if (lang === 'uz') {
      return {
        kind: 'crisis',
        title: "Muhim",
        message: "Bu inqirozga o'xshash savol. Iltimos, zudlik bilan tirik mutaxassis yordamiga murojaat qiling.",
        contacts: isUsRegion ? ['US/Canada Suicide & Crisis Lifeline: 988', 'Emergency services: 911'] : ["Shoshilinch xizmatlar: 112", "Tez tibbiy yordam: 103"],
      }
    }
    if (lang === 'ru') {
      return {
        kind: 'crisis',
        title: 'Важно',
        message: 'Похоже на кризисный вопрос. Пожалуйста, срочно обратитесь за живой помощью к специалисту.',
        contacts: isUsRegion ? ['US/Canada Suicide & Crisis Lifeline: 988', 'Emergency services: 911'] : ['Экстренные службы: 112', 'Скорая медицинская помощь: 103'],
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
  if (lang === 'uz') {
    return {
      kind: 'medical',
      title: "Tibbiy eslatma",
      message: "Savol tibbiy baholashni talab qilishi mumkin. Aniqlashtirish uchun shifokorga murojaat qiling.",
      contacts: isUsRegion ? ['Urgent emergency: 911'] : ["Tez tibbiy yordam: 103", "Shoshilinch xizmatlar: 112"],
    }
  }
  if (lang === 'ru') {
    return {
      kind: 'medical',
      title: 'Медицинская памятка',
      message: 'Вопрос может требовать медицинской оценки. Пожалуйста, обратитесь к врачу для точной диагностики.',
      contacts: isUsRegion ? ['Urgent emergency: 911'] : ['Скорая медицинская помощь: 103', 'Экстренные службы: 112'],
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

type SpreadUi = {
  id: SpreadId
  title: string
  subtitle: string
  cards: string
  icon: 'sun' | 'clock' | 'cards' | 'branch'
}

const SPREADS_BY_LANG: Record<AppLanguage, SpreadUi[]> = {
  ru: [
    { id: 'card_of_day', title: 'Карта Дня', subtitle: 'Ежедневное руководство', cards: '1 карта', icon: 'sun' },
    {
      id: 'past_present_future',
      title: 'Прошлое • Настоящее • Будущее',
      subtitle: 'Временная линия событий',
      cards: '3 карты',
      icon: 'clock',
    },
    { id: 'three_cards', title: 'Расклад по 3 картам', subtitle: 'Универсальный расклад', cards: '3 карты', icon: 'cards' },
    { id: 'decision', title: 'Принятие решения', subtitle: 'Выбор между вариантами', cards: '2 карты', icon: 'branch' },
  ],
  en: [
    { id: 'card_of_day', title: 'Card of the Day', subtitle: 'Daily guidance', cards: '1 card', icon: 'sun' },
    {
      id: 'past_present_future',
      title: 'Past • Present • Future',
      subtitle: 'Timeline of events',
      cards: '3 cards',
      icon: 'clock',
    },
    { id: 'three_cards', title: '3-card spread', subtitle: 'Universal spread', cards: '3 cards', icon: 'cards' },
    { id: 'decision', title: 'Decision spread', subtitle: 'Choice between options', cards: '2 cards', icon: 'branch' },
  ],
  uz: [
    { id: 'card_of_day', title: 'Kun kartasi', subtitle: 'Har kunlik yo‘riqnoma', cards: '1 karta', icon: 'sun' },
    {
      id: 'past_present_future',
      title: "O'tmish • Hozir • Kelajak",
      subtitle: 'Vaqt chizig‘i',
      cards: '3 karta',
      icon: 'clock',
    },
    { id: 'three_cards', title: '3 kartalik yoyilma', subtitle: 'Universal yoyilma', cards: '3 karta', icon: 'cards' },
    { id: 'decision', title: 'Qaror yoyilmasi', subtitle: 'Variantlar orasida tanlov', cards: '2 karta', icon: 'branch' },
  ],
}

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
  return <img src={startSunUserWhiteIcon} alt="" aria-hidden="true" />
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
type VoiceInputTarget = 'home' | 'photo_main'
type BillingStatus = {
  free_limit: number
  month_used: number
  free_left: number
  paid_readings_balance: number
  subscription_until?: string | null
  has_active_subscription?: boolean
  can_create_reading?: boolean
}

type BillingCountryCode = 'ru' | 'uz'
type BillingPlanKey = ClickPlanCode
type BillingFlowStep = 'country' | 'plan' | 'method'
type BillingFlowSource = 'paywall' | 'profile'
type BillingPlanPriceInfo = { amount: number; currency: string }
type BillingPlanPricesByProvider = {
  sbp: Partial<Record<BillingPlanKey, BillingPlanPriceInfo>>
  click: Partial<Record<BillingPlanKey, BillingPlanPriceInfo>>
}

const BILLING_PLAN_PRICES_FALLBACK: BillingPlanPricesByProvider = {
  sbp: {
    sub_week: { amount: 7900, currency: 'RUB' },
    sub_2weeks: { amount: 12400, currency: 'RUB' },
    sub_month: { amount: 22400, currency: 'RUB' },
    sub_year: { amount: 174700, currency: 'RUB' },
  },
  click: {
    sub_week: { amount: 12000, currency: 'UZS' },
    sub_2weeks: { amount: 18600, currency: 'UZS' },
    sub_month: { amount: 34900, currency: 'UZS' },
    sub_year: { amount: 219000, currency: 'UZS' },
  },
}

const BILLING_COUNTRIES_BY_LANG: Record<AppLanguage, Array<{ code: BillingCountryCode; label: string; methodsHint: string }>> = {
  ru: [
    { code: 'ru', label: '🇷🇺 Россия', methodsHint: 'Оплата через СБП' },
    { code: 'uz', label: '🇺🇿 Узбекистан', methodsHint: 'Оплата через CLICK' },
  ],
  en: [
    { code: 'ru', label: '🇷🇺 Russia', methodsHint: 'Pay via SBP' },
    { code: 'uz', label: '🇺🇿 Uzbekistan', methodsHint: 'Pay via CLICK' },
  ],
  uz: [
    { code: 'ru', label: '🇷🇺 Rossiya', methodsHint: "SBP orqali to'lov" },
    { code: 'uz', label: "🇺🇿 O'zbekiston", methodsHint: "CLICK orqali to'lov" },
  ],
}

const BILLING_PLAN_META_BY_LANG: Record<AppLanguage, Record<BillingPlanKey, { title: string; caption: string }>> = {
  ru: {
    sub_week: { title: 'Пробная неделя', caption: '7 дней' },
    sub_2weeks: { title: 'Безлимит на 2 недели', caption: '14 дней' },
    sub_month: { title: 'Безлимит на месяц', caption: '30 дней' },
    sub_year: { title: 'Безлимит на год', caption: '365 дней' },
  },
  en: {
    sub_week: { title: 'Trial week', caption: '7 days' },
    sub_2weeks: { title: 'Unlimited for 2 weeks', caption: '14 days' },
    sub_month: { title: 'Unlimited for a month', caption: '30 days' },
    sub_year: { title: 'Unlimited for a year', caption: '365 days' },
  },
  uz: {
    sub_week: { title: 'Sinov haftasi', caption: '7 kun' },
    sub_2weeks: { title: '2 haftaga cheksiz', caption: '14 kun' },
    sub_month: { title: 'Bir oyga cheksiz', caption: '30 kun' },
    sub_year: { title: 'Bir yilga cheksiz', caption: '365 kun' },
  },
}

const BILLING_PLAN_KEYS_BY_COUNTRY: Record<BillingCountryCode, BillingPlanKey[]> = {
  ru: ['sub_week', 'sub_2weeks', 'sub_month', 'sub_year'],
  uz: ['sub_week', 'sub_2weeks', 'sub_month', 'sub_year'],
}

const BOT_USERNAME = 'Ttaarrroobot'
const BOT_PAYMENT_URL = `https://t.me/${BOT_USERNAME}?start=menu`
const BOT_CARD_URL = `https://t.me/${BOT_USERNAME}?start=card`
const BOT_CLICK_URL = `https://t.me/${BOT_USERNAME}?start=click`
const BOT_CLICK_CARD_URL = `https://t.me/${BOT_USERNAME}?start=click_card`
const BOT_SUB_MANAGE_URL = `https://t.me/${BOT_USERNAME}?start=sub_manage`
const SUPPORT_URL = `https://t.me/${BOT_USERNAME}?start=support`
const TERMS_URL = `https://t.me/${BOT_USERNAME}?start=terms`
const PRIVACY_URL = `https://t.me/${BOT_USERNAME}?start=privacy`

const METRIKA_GOALS = {
  appOpen: 'app_open',
  openCardDay: 'open_card_day',
  openPhotoAnalysis: 'open_photo_analysis',
  readingStart: 'reading_start',
  photoDetectSuccess: 'photo_detect_success',
  photoInterpretationDone: 'photo_interpretation_done',
  photoFollowupDone: 'photo_followup_done',
  paywallShown: 'paywall_shown',
  paymentStart: 'payment_start',
  paymentSuccess: 'payment_success',
} as const

const CARD_DAY_LOADING_STEPS_BY_LANG: Record<AppLanguage, readonly string[]> = {
  ru: ['Ищем вашу карту дня…', 'Собираем интерпретацию…', 'Почти готово…'],
  en: ['Looking for your card of the day…', 'Building interpretation…', 'Almost done…'],
  uz: ["Kun kartangiz topilmoqda…", 'Talqin tayyorlanmoqda…', 'Deyarli tayyor…'],
}
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

const HOME_TOUR_STEPS_BY_LANG: Record<AppLanguage, Array<{ id: HomeTourStepId; title: string; text: string }>> = {
  ru: [
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
  ],
  en: [
    {
      id: 'card_day',
      title: 'Card of the day',
      text: "Tap here to open your card of the day and get a short AI interpretation for today.",
    },
    {
      id: 'photo',
      title: 'Photo spread',
      text: 'Lay out real Tarot cards, take a top-down photo (or pick one from gallery), and the app will recognize those exact cards.',
    },
    {
      id: 'question_zone',
      title: 'Question and spread type',
      text: 'First enter what worries you, then choose a category and spread format below.',
    },
    {
      id: 'cta',
      title: 'Start reading',
      text: 'After selecting a spread type, tap “Start reading” at the bottom.',
    },
  ],
  uz: [
    {
      id: 'card_day',
      title: 'Kun kartasi',
      text: "Bu yerga bosib bugungi kun kartasini oching va qisqa AI talqinini oling.",
    },
    {
      id: 'photo',
      title: 'Foto yoyilma',
      text: "Tarot kartalarini yoying, yuqoridan suratga oling (yoki galereyadan tanlang) — ilova aynan shu kartalarni aniqlab beradi.",
    },
    {
      id: 'question_zone',
      title: "Savol va yoyilma turi",
      text: "Avval sizni nima bezovta qilayotganini yozing, so'ng pastdan toifa va yoyilma turini tanlang.",
    },
    {
      id: 'cta',
      title: 'Yoyilmani boshlash',
      text: 'Yoyilma turi tanlangach, pastdagi “Yoyilmani boshlash” tugmasini bosing.',
    },
  ],
}

type LegalDocKind = 'terms' | 'privacy'

const LEGAL_DOCS_BY_LANG: Record<AppLanguage, Record<LegalDocKind, { title: string; intro: string; body: string[]; pdf: string }>> = {
  ru: {
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
  },
  en: {
    terms: {
      title: 'Terms of service',
      intro: 'Draft version. Final legal wording will be updated later without changing the user flow.',
      body: [
        '1. AI Taro provides informational interpretations and is not medical, legal, or financial advice.',
        '2. Users make their own decisions based on the received information.',
        '3. At first launch, users confirm consent to these documents and to personal reading memory (up to 90 days).',
        '4. Memory is used only to personalize interpretations. Use /forgetme to delete personal data.',
        '5. You can request full data deletion via /forgetme in @Ttaarrroobot.',
        '6. For support, use “Contact support” in profile or /support in the bot.',
      ],
      pdf: TERMS_PDF_URL,
    },
    privacy: {
      title: 'Privacy policy',
      intro: 'Draft version. Final wording will be updated later without changing the user path.',
      body: [
        '1. The service processes Telegram profile data (id, username, name) and user prompts.',
        '2. Data is used only for auth, limits, payments, and response generation.',
        '3. When personal memory is enabled, structured reading data is stored for up to 90 days.',
        '4. The service does not sell personal data to third parties.',
        '5. Use /forgetme in @Ttaarrroobot for full deletion.',
        '6. For medical/crisis topics the service shows a soft warning and local emergency contacts, without diagnoses.',
      ],
      pdf: PRIVACY_PDF_URL,
    },
  },
  uz: {
    terms: {
      title: 'Foydalanuvchi kelishuvi',
      intro: "Hujjatning sinov versiyasi. Yakuniy yuridik matn keyinroq, ekran tuzilmasi o'zgarmagan holda yangilanadi.",
      body: [
        "1. AI Taro faqat ma'lumot beruvchi talqinlarni taqdim etadi va tibbiy, yuridik yoki moliyaviy maslahat o'rnini bosa olmaydi.",
        "2. Foydalanuvchi olingan ma'lumot asosida qarorni mustaqil qabul qiladi.",
        "3. Birinchi kirishda foydalanuvchi hujjatlarga va shaxsiy xotira funksiyasiga (90 kungacha) rozilik beradi.",
        "4. Xotira faqat talqinlarni shaxsiylashtirish uchun ishlatiladi. Ma'lumotlarni o'chirish uchun /forgetme dan foydalaning.",
        "5. To'liq o'chirish so'rovi @Ttaarrroobot botida /forgetme orqali yuboriladi.",
        "6. Yordam uchun profildagi “Yordamga yozish” yoki botdagi /support tugmasidan foydalaning.",
      ],
      pdf: TERMS_PDF_URL,
    },
    privacy: {
      title: 'Maxfiylik siyosati',
      intro: "Hujjatning sinov versiyasi. Yakuniy tahrir keyinroq foydalanuvchi yo'li o'zgarmasdan yangilanadi.",
      body: [
        "1. Xizmat Telegram profil ma'lumotlarini (id, username, ism) va foydalanuvchi so'rovlarini qayta ishlaydi.",
        "2. Ma'lumotlar faqat avtorizatsiya, limitlar, to'lov va javob generatsiyasi uchun ishlatiladi.",
        "3. Shaxsiy xotira yoqilganida, yoyilmalar bo'yicha tuzilgan ma'lumotlar 90 kungacha saqlanadi.",
        "4. Xizmat shaxsiy ma'lumotlarni uchinchi tomonga sotmaydi.",
        "5. To'liq o'chirish uchun @Ttaarrroobot botida /forgetme buyrug'idan foydalaning.",
        "6. Tibbiy/inqiroz mavzularida xizmat tashxis qo'ymasdan yumshoq ogohlantirish va mahalliy yordam kontaktlarini ko'rsatadi.",
      ],
      pdf: PRIVACY_PDF_URL,
    },
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

const isTransientAuthError = (raw: string): boolean => {
  const text = String(raw || '').toLowerCase()
  if (!text) return false
  return (
    text.includes('network timeout') ||
    text.includes('failed to fetch') ||
    text.includes('load failed') ||
    text.includes('networkerror') ||
    text.includes('gateway timeout') ||
    text.includes('502') ||
    text.includes('503') ||
    text.includes('504') ||
    text.includes('timeout')
  )
}

const requestTelegramWriteAccess = async (): Promise<boolean> => {
  const tg = (window as any)?.Telegram?.WebApp
  if (!tg || typeof tg?.requestWriteAccess !== 'function') return false
  try {
    return await new Promise<boolean>((resolve) => {
      let settled = false
      const done = (value: boolean) => {
        if (settled) return
        settled = true
        resolve(Boolean(value))
      }
      try {
        tg.requestWriteAccess((granted: boolean) => done(Boolean(granted)))
      } catch {
        done(false)
        return
      }
      window.setTimeout(() => done(false), 10000)
    })
  } catch {
    return false
  }
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

const [isLanguageManual, setIsLanguageManual] = useState<boolean>(() => readLanguageManualFlag())

const [appLanguage, setAppLanguage] = useState<AppLanguage>(() => {
  const saved = readStoredAppLanguage()
  const manual = readLanguageManualFlag()
  if (manual && saved) return saved
  return detectRuntimeAppLanguage()
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
const [clickOrderId, setClickOrderId] = useState<string | null>(() => {
  try {
    const v = localStorage.getItem('click_pending_order_id')
    return v && v.trim() ? v.trim() : null
  } catch {
    return null
  }
})
const [clickBusyPlan, setClickBusyPlan] = useState<ClickPlanCode | null>(null)
const [clickStatusText, setClickStatusText] = useState('')
const [clickPolling, setClickPolling] = useState(false)
const [showBillingFlow, setShowBillingFlow] = useState(false)
const [billingFlowSource, setBillingFlowSource] = useState<BillingFlowSource>('profile')
const [billingFlowStep, setBillingFlowStep] = useState<BillingFlowStep>('country')
const [billingFlowCountry, setBillingFlowCountry] = useState<BillingCountryCode>('uz')
const [billingFlowPlan, setBillingFlowPlan] = useState<BillingPlanKey | null>(null)
const [billingPlanPrices, setBillingPlanPrices] = useState<BillingPlanPricesByProvider>(BILLING_PLAN_PRICES_FALLBACK)
const [showProfilePaymentHelp, setShowProfilePaymentHelp] = useState(false)
const [showBillingPaymentHelp, setShowBillingPaymentHelp] = useState(false)
const [showPaymentSuccessModal, setShowPaymentSuccessModal] = useState(false)
const [paymentSuccessText, setPaymentSuccessText] = useState(() => {
  if (appLanguage === 'en') return 'Payment successful. Subscription activated.'
  if (appLanguage === 'uz') return "To'lov muvaffaqiyatli. Obuna faollashtirildi."
  return 'Оплата прошла успешно. Подписка активирована.'
})
const [showAccessPaywall, setShowAccessPaywall] = useState(false)
const [activeLegalDoc, setActiveLegalDoc] = useState<LegalDocKind | null>(null)
const [showPersonalizationModal, setShowPersonalizationModal] = useState(false)
const [showLanguageModal, setShowLanguageModal] = useState(false)
const [memoryOptIn, setMemoryOptIn] = useState<boolean>(true)
const [prefsSaving, setPrefsSaving] = useState(false)
const [languageSaving, setLanguageSaving] = useState(false)
const [prefsError, setPrefsError] = useState('')

const [question, setQuestion] = useState('')
const topics = useMemo(() => buildTopics(appLanguage), [appLanguage])
const spreads = useMemo(() => SPREADS_BY_LANG[appLanguage] || SPREADS_BY_LANG.ru, [appLanguage])
const t = (key: keyof (typeof UI_TEXT)['ru']) => UI_TEXT[appLanguage]?.[key] || UI_TEXT.ru[key] || key
const lx = (ru: string, en: string, uz: string) => (appLanguage === 'en' ? en : appLanguage === 'uz' ? uz : ru)
const homeTourSteps = useMemo(() => HOME_TOUR_STEPS_BY_LANG[appLanguage] || HOME_TOUR_STEPS_BY_LANG.ru, [appLanguage])
const cardDayLoadingSteps = useMemo(
  () => CARD_DAY_LOADING_STEPS_BY_LANG[appLanguage] || CARD_DAY_LOADING_STEPS_BY_LANG.ru,
  [appLanguage],
)
const billingCountries = useMemo(
  () => BILLING_COUNTRIES_BY_LANG[appLanguage] || BILLING_COUNTRIES_BY_LANG.ru,
  [appLanguage],
)
const billingPlanMeta = useMemo(
  () => BILLING_PLAN_META_BY_LANG[appLanguage] || BILLING_PLAN_META_BY_LANG.ru,
  [appLanguage],
)
const legalDocs = useMemo(() => LEGAL_DOCS_BY_LANG[appLanguage] || LEGAL_DOCS_BY_LANG.ru, [appLanguage])

useEffect(() => {
  try {
    localStorage.setItem(APP_LANG_STORAGE_KEY, appLanguage)
  } catch {}
  try {
    document.documentElement.setAttribute('lang', appLanguage)
  } catch {}
}, [appLanguage])

useEffect(() => {
  try {
    localStorage.setItem(APP_LANG_MANUAL_KEY, isLanguageManual ? '1' : '0')
  } catch {}
}, [isLanguageManual])

useEffect(() => {
  if (isLanguageManual) return
  const syncLanguage = () => {
    setAppLanguage((prev) => {
      const runtime = detectRuntimeAppLanguage()
      return prev === runtime ? prev : runtime
    })
  }
  const onVisibility = () => {
    if (!document.hidden) syncLanguage()
  }
  syncLanguage()
  window.addEventListener('focus', syncLanguage)
  document.addEventListener('visibilitychange', onVisibility)
  return () => {
    window.removeEventListener('focus', syncLanguage)
    document.removeEventListener('visibilitychange', onVisibility)
  }
}, [isLanguageManual])

useEffect(() => {
  if (showPaymentSuccessModal) return
  setPaymentSuccessText(
    lx(
      'Оплата прошла успешно. Подписка активирована.',
      'Payment successful. Subscription activated.',
      "To'lov muvaffaqiyatli. Obuna faollashtirildi.",
    ),
  )
}, [appLanguage, showPaymentSuccessModal])

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
      tg?.expand?.()
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
          const runtimeLang = detectRuntimeAppLanguage()
          const serverLang = normalizeAppLanguage(me?.app_language || runtimeLang)
          setUser(me)
          setAppLanguage(isLanguageManual ? serverLang : runtimeLang)
          setBilling(billingOut)
          setAuthStatus('ready')
        })
        return
      } catch (meErr: any) {
        const raw = String(meErr?.message || meErr || '')
        const isAuthRejected = /(?:^|\\b)(401|403)(?:\\b|$)|unauthorized|forbidden/i.test(raw)
        if (isAuthRejected) {
          // jwt действительно невалиден — очищаем и продолжаем Telegram auth
          clearStoredJwt()
          safe(() => {
            setToken(null)
            setUser(null)
            setBilling(null)
          })
        } else {
          // Временный сетевой сбой на /me: пробуем Telegram auth вместо немедленного фейла.
          console.warn('getMe failed, fallback to Telegram auth', meErr)
        }
      }
    }

    // 2) Телеграм‑авторизация
    try {
      let initData = await resolveTelegramInitDataWithRetry(24, 250)
      if (!initData) {
        safe(() => {
          setAuthStatus('error')
          if (appLanguage === 'en') {
            setAuthError('Open the mini app from the Telegram button (initData is missing).')
          } else if (appLanguage === 'uz') {
            setAuthError("Mini ilovani Telegram tugmasi orqali oching (initData yuborilmadi).")
          } else {
            setAuthError('Откройте мини‑приложение из кнопки в Telegram (initData не передан).')
          }
        })
        return
      }

      let res: Awaited<ReturnType<typeof telegramAuth>> | null = null
      let lastAuthErr: any = null
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          res = await telegramAuth(initData)
          lastAuthErr = null
          break
        } catch (authErr: any) {
          lastAuthErr = authErr
          const msg = String(authErr?.message || authErr || '')
          const retryableInitData = /Invalid Telegram initData|initData expired|Invalid initData timestamp|Telegram initData is empty/i.test(msg)
          const retryableNetwork = isTransientAuthError(msg)
          if (attempt >= 2 || (!retryableInitData && !retryableNetwork)) {
            break
          }
          await delayMs(350 + attempt * 300)
          const refreshed = await resolveTelegramInitDataWithRetry(12, 250)
          if (refreshed) {
            initData = refreshed
          }
        }
      }
      if (lastAuthErr || !res) {
        throw lastAuthErr || new Error('Telegram auth failed')
      }
      let billingOut: BillingStatus | null = null
      try {
        billingOut = await getBillingStatus(res.token)
      } catch {}

      writeStoredJwt(res.token)

      safe(() => {
        const runtimeLang = detectRuntimeAppLanguage()
        const serverLang = normalizeAppLanguage(res.user?.app_language || runtimeLang)
        setToken(res.token)
        setUser(res.user)
        setAppLanguage(isLanguageManual ? serverLang : runtimeLang)
        setBilling(billingOut)
        setAuthStatus('ready')
      })
    } catch (e: any) {
      console.error('Auth error', e)
      clearStoredJwt()
      const raw = String(e?.message || e || '')
      let uiError =
        appLanguage === 'en'
          ? 'Authorization failed. Reopen the mini app in Telegram.'
          : appLanguage === 'uz'
            ? "Avtorizatsiya bajarilmadi. Telegram ichida mini ilovani qayta oching."
            : 'Не удалось авторизоваться. Перезапустите мини‑приложение в Telegram.'
      if (/initData is empty|нет initData|initData не передан/i.test(raw)) {
        uiError =
          appLanguage === 'en'
            ? 'Telegram did not pass login data. Open the app only via the bot button.'
            : appLanguage === 'uz'
              ? "Telegram kirish ma'lumotlarini uzatmadi. Ilovani faqat bot tugmasi orqali oching."
              : 'Telegram не передал данные входа. Откройте приложение только через кнопку в боте.'
      } else if (/Invalid Telegram initData|initData expired|Invalid initData timestamp/i.test(raw)) {
        uiError =
          appLanguage === 'en'
            ? 'Telegram data expired. Close the mini app and open it again from the bot.'
            : appLanguage === 'uz'
              ? "Telegram ma'lumotlari eskirgan. Mini ilovani yoping va botdan qayta oching."
              : 'Данные Telegram устарели. Закройте мини‑приложение и откройте заново из бота.'
      } else if (/Network timeout/i.test(raw)) {
        uiError =
          appLanguage === 'en'
            ? 'Network timeout. Check your connection and tap Retry.'
            : appLanguage === 'uz'
              ? "Tarmoq javobi uzoq. Internetni tekshirib, qayta urinib ko'ring."
              : 'Сеть отвечает слишком долго. Проверьте интернет и нажмите «Повторить».'
      } else if (/401|403/.test(raw)) {
        uiError =
          appLanguage === 'en'
            ? 'Telegram authorization error. Close the mini app and open it again.'
            : appLanguage === 'uz'
              ? "Telegram avtorizatsiya xatosi. Mini ilovani yoping va qayta oching."
              : 'Ошибка авторизации Telegram. Закройте мини‑приложение и откройте снова.'
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
}, [token, authRetryNonce, isLanguageManual])/* =============================================================================================
     [9] БАЗОВОЕ СОСТОЯНИЕ UI
  ============================================================================================= */

  useEffect(() => {
    if (authStatus !== 'ready' || !token) return
    const tgId = Number(user?.telegram_id || 0)
    if (!tgId) return

    let cancelled = false
    ;(async () => {
      try {
        const result = await bootstrapBotChat(token)
        if (cancelled) return

        const status = String(result?.status || '').toLowerCase()
        if (status === 'sent') {
          return
        }

        if (status === 'already_sent') return

        if (status === 'forbidden') {
          // Просим системное разрешение Telegram, когда бот реально не может писать в ЛС.
          const canWrite = await requestTelegramWriteAccess()
          if (!canWrite) return

          const forceResult = await bootstrapBotChat(token, { force: true })
          const forceStatus = String(forceResult?.status || '').toLowerCase()
          if (forceStatus === 'sent' || forceStatus === 'already_sent') return
          return
        }
      } catch {}
    })()

    return () => {
      cancelled = true
    }
  }, [authStatus, token, user?.telegram_id])

  useEffect(() => {
    setMemoryOptIn(Boolean(user?.memory_opt_in ?? true))
  }, [user?.memory_opt_in])

useEffect(() => {
  if (!isLanguageManual) return
  if (!user?.app_language) return
  const next = normalizeAppLanguage(user.app_language)
  setAppLanguage(next)
}, [user?.app_language, isLanguageManual])

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
        app_language: appLanguage,
      })
      setMemoryOptIn(Boolean(out.memory_opt_in ?? true))
      const nextLang = normalizeAppLanguage(out.app_language || appLanguage)
      setAppLanguage(nextLang)
      setUser((prev) => (prev ? { ...prev, memory_opt_in: Boolean(out.memory_opt_in ?? true), app_language: nextLang } : prev))
      setShowPersonalizationModal(false)
    } catch {
      setPrefsError(t('saveFailed'))
    } finally {
      setPrefsSaving(false)
    }
  }

  const saveAppLanguage = async (nextLanguage: AppLanguage) => {
    const normalized = normalizeAppLanguage(nextLanguage)
    setIsLanguageManual(true)
    setAppLanguage(normalized)
    setUser((prev) => (prev ? { ...prev, app_language: normalized } : prev))
    setShowLanguageModal(false)

    if (!token) {
      return
    }

    setLanguageSaving(true)
    try {
      const out = await updateMePreferences(token, {
        memory_opt_in: memoryOptIn,
        retention_nudges_opt_in: false,
        retention_nudge_hour_local: null,
        retention_nudge_tz: null,
        app_language: normalized,
      })
      const serverLang = normalizeAppLanguage(out.app_language || normalized)
      setAppLanguage(serverLang)
      setUser((prev) =>
        prev
          ? { ...prev, memory_opt_in: Boolean(out.memory_opt_in ?? true), app_language: serverLang }
          : prev
      )
    } catch {
      // Keep local language even if backend persistence fails.
    } finally {
      setLanguageSaving(false)
    }
  }

  const saveAutoLanguage = async () => {
    const runtime = detectRuntimeAppLanguage()
    setIsLanguageManual(false)
    setAppLanguage(runtime)
    setUser((prev) => (prev ? { ...prev, app_language: runtime } : prev))
    setShowLanguageModal(false)

    if (!token) {
      return
    }

    setLanguageSaving(true)
    try {
      const out = await updateMePreferences(token, {
        memory_opt_in: memoryOptIn,
        retention_nudges_opt_in: false,
        retention_nudge_hour_local: null,
        retention_nudge_tz: null,
        app_language: runtime,
      })
      const serverLang = normalizeAppLanguage(out.app_language || runtime)
      setAppLanguage(serverLang)
      setUser((prev) =>
        prev
          ? { ...prev, memory_opt_in: Boolean(out.memory_opt_in ?? true), app_language: serverLang }
          : prev
      )
    } catch {
      // Keep local language even if backend persistence fails.
    } finally {
      setLanguageSaving(false)
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

  const setPendingClickOrder = (orderId: string | null) => {
    const clean = String(orderId || '').trim()
    const next = clean || null
    setClickOrderId(next)
    try {
      if (next) localStorage.setItem('click_pending_order_id', next)
      else localStorage.removeItem('click_pending_order_id')
    } catch {}
  }

  const openBillingFlow = (source: BillingFlowSource) => {
    setBillingFlowSource(source)
    setBillingFlowStep('country')
    setBillingFlowPlan(null)
    setShowBillingPaymentHelp(false)
    setShowBillingFlow(true)
  }

  const closeBillingFlow = () => {
    setShowBillingPaymentHelp(false)
    setShowBillingFlow(false)
  }

  const backBillingFlowStep = () => {
    if (billingFlowStep === 'method') {
      setBillingFlowStep('plan')
      return
    }
    if (billingFlowStep === 'plan') {
      setBillingFlowPlan(null)
      setBillingFlowStep('country')
    }
  }

  const onPaymentSucceeded = (orderId: string, provider: 'sbp' | 'click', planCode: string) => {
    const cleanOrderId = String(orderId || '').trim()
    if (!cleanOrderId) return

    if (!paidOrdersTrackedRef.current.has(cleanOrderId)) {
      paidOrdersTrackedRef.current.add(cleanOrderId)
      metrikaReachGoal(METRIKA_GOALS.paymentSuccess, {
        provider,
        plan: String(planCode || ''),
      })
    }

    if (!paymentSuccessNotifiedRef.current.has(cleanOrderId)) {
      paymentSuccessNotifiedRef.current.add(cleanOrderId)
      setPaymentSuccessText(
        provider === 'click'
          ? lx(
              'Оплата через CLICK подтверждена. Подписка активирована.',
              'CLICK payment confirmed. Subscription activated.',
              "CLICK to'lovi tasdiqlandi. Obuna faollashtirildi.",
            )
          : lx(
              'Оплата через СБП подтверждена. Подписка активирована.',
              'SBP payment confirmed. Subscription activated.',
              "SBP to'lovi tasdiqlandi. Obuna faollashtirildi.",
            )
      )
      setShowPaymentSuccessModal(true)
    }

    setShowAccessPaywall(false)
    setShowBillingPaymentHelp(false)
    setShowProfilePaymentHelp(false)
    setShowBillingFlow(false)
  }

  const mapPlanToSbpPlan = (planKey: BillingPlanKey): SbpPlanCode | null => {
    if (planKey === 'sub_week' || planKey === 'sub_2weeks' || planKey === 'sub_month' || planKey === 'sub_year') return planKey
    return null
  }

  useEffect(() => {
    let cancelled = false

    const run = async () => {
      try {
        const out = await getBillingPlans()
        if (cancelled) return

        const next: BillingPlanPricesByProvider = {
          sbp: { ...(BILLING_PLAN_PRICES_FALLBACK.sbp || {}) },
          click: { ...(BILLING_PLAN_PRICES_FALLBACK.click || {}) },
        }
        for (const item of out.sbp || []) {
          const code = String(item?.code || '').trim() as BillingPlanKey
          if (!code) continue
          next.sbp[code] = {
            amount: Math.max(0, Number(item?.amount || 0)),
            currency: String(item?.currency || 'RUB').trim().toUpperCase(),
          }
        }
        for (const item of out.click || []) {
          const code = String(item?.code || '').trim() as BillingPlanKey
          if (!code) continue
          next.click[code] = {
            amount: Math.max(0, Number(item?.amount || 0)),
            currency: String(item?.currency || 'UZS').trim().toUpperCase(),
          }
        }
        setBillingPlanPrices(next)
      } catch {
        // Keep fallback prices from local defaults.
      }
    }

    void run()
    return () => {
      cancelled = true
    }
  }, [])

  const formatPlanAmount = (amount: number, currency: string) => {
    const value = Math.max(0, Number(amount || 0))
    const cur = String(currency || '').toUpperCase()
    const numLocale = appLanguage === 'en' ? 'en-US' : appLanguage === 'uz' ? 'uz-UZ' : 'ru-RU'
    if (cur === 'RUB') {
      const rub = Math.round(value / 100)
      return `₽ ${rub.toLocaleString(numLocale)}`
    }
    if (cur === 'UZS') {
      return `${Math.round(value).toLocaleString(numLocale)} UZS`
    }
    return `${Math.round(value).toLocaleString(numLocale)} ${cur || ''}`.trim()
  }

  const getPlanPriceLabel = (country: BillingCountryCode, planKey: BillingPlanKey) => {
    const provider = country === 'uz' ? 'click' : 'sbp'
    const priceInfo = provider === 'click' ? billingPlanPrices.click[planKey] : billingPlanPrices.sbp[planKey]
    if (!priceInfo) return ''
    return formatPlanAmount(priceInfo.amount, priceInfo.currency)
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
      const fromClickQuery = String(qs.get('click_order_id') || '').trim()
      if (fromQuery) {
        setPendingSbpOrder(fromQuery)
        qs.delete('sbp_order_id')
      }
      if (fromClickQuery) {
        setPendingClickOrder(fromClickQuery)
        qs.delete('click_order_id')
      }
      if (fromQuery || fromClickQuery) {
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
        onPaymentSucceeded(orderId, 'sbp', String(out?.plan_code || ''))
        setPendingSbpOrder(null)
        void refreshBilling(jwt)
      } else if (status === 'canceled' || status === 'cancelled') {
        setPendingSbpOrder(null)
      }

      if (!silent) {
        setSbpStatusText(
          msg ||
            lx(
              'Статус платежа обновлён.',
              'Payment status updated.',
              "To'lov holati yangilandi.",
            ),
        )
      }
    } catch (err: any) {
      if (!silent) {
        const text = String(err?.message || '')
        const parsed = readBackendErrorDetail(text)
        const detail = parsed?.detail
        if (detail?.code === 'SBP_ORDER_NOT_FOUND') {
          setPendingSbpOrder(null)
          setSbpStatusText(
            lx(
              'Счёт не найден или уже закрыт. Создайте новый платёж.',
              'Invoice not found or already closed. Create a new payment.',
              "Hisob topilmadi yoki yopilgan. Yangi to'lov yarating.",
            ),
          )
        } else {
          setSbpStatusText(
            lx(
              'Не удалось проверить статус оплаты. Попробуйте ещё раз.',
              'Could not verify payment status. Please try again.',
              "To'lov holatini tekshirib bo'lmadi. Qayta urinib ko'ring.",
            ),
          )
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
      metrikaReachGoal(METRIKA_GOALS.paymentStart, { provider: 'sbp', plan: planCode })
      setSbpBusyPlan(planCode)
      setSbpStatusText('')
      setShowBillingPaymentHelp(false)
      setShowProfilePaymentHelp(false)

      const out = await createSbpPayment(jwt, planCode)
      const orderId = String(out?.order_id || '').trim()
      const link = String(out?.confirmation_url || '').trim()

      if (!orderId || !link) {
        throw new Error(
          lx(
            'Провайдер не вернул ссылку оплаты.',
            'Payment provider did not return a payment link.',
            "To'lov provayderi to'lov havolasini qaytarmadi.",
          ),
        )
      }

      setPendingSbpOrder(orderId)
      if (showBillingFlow) {
        setBillingFlowStep('country')
      }
      setSbpStatusText(
        lx(
          'Счёт СБП открыт. Завершите оплату и вернитесь в приложение.',
          'SBP invoice created. Complete payment and return to the app.',
          "SBP hisobi yaratildi. To'lovni tugatib, ilovaga qayting.",
        ),
      )
      openTelegramUrl(link)
      window.setTimeout(() => {
        void checkSbpStatus(orderId, true)
      }, 3500)
    } catch (err: any) {
      const text = String(err?.message || '')
      const parsed = readBackendErrorDetail(text)
      const detail = parsed?.detail

      if (detail?.code === 'SBP_NOT_CONFIGURED') {
        setSbpStatusText(
          lx(
            'СБП ещё не настроен на сервере. Нужны shop_id и secret_key ЮKassa.',
            'SBP is not configured on server yet. shop_id and secret_key are required.',
            "SBP hali serverda sozlanmagan. shop_id va secret_key kerak.",
          ),
        )
      } else if (detail?.message) {
        setSbpStatusText(String(detail.message))
      } else {
        setSbpStatusText(
          lx(
            'Не удалось создать счёт СБП. Попробуйте ещё раз.',
            'Could not create SBP invoice. Please try again.',
            "SBP hisobi yaratilmadi. Qayta urinib ko'ring.",
          ),
        )
      }
    } finally {
      setSbpBusyPlan(null)
    }
  }

  const checkClickStatus = async (orderIdArg?: string | null, silent = false) => {
    const jwt = token
    const orderId = String(orderIdArg ?? clickOrderId ?? '').trim()
    if (!jwt || !orderId) return

    try {
      if (!silent) setClickPolling(true)
      const out = await getClickPaymentStatus(jwt, orderId)
      const status = String(out?.status || '').toLowerCase()
      const msg = String(out?.message || '').trim()

      if (status === 'succeeded') {
        onPaymentSucceeded(orderId, 'click', String(out?.plan_code || ''))
        setPendingClickOrder(null)
        void refreshBilling(jwt)
      } else if (status === 'canceled' || status === 'cancelled') {
        setPendingClickOrder(null)
      }

      if (!silent) {
        setClickStatusText(
          msg ||
            lx(
              'Статус оплаты CLICK обновлён.',
              'CLICK payment status updated.',
              "CLICK to'lovi holati yangilandi.",
            ),
        )
      }
    } catch (err: any) {
      if (!silent) {
        const text = String(err?.message || '')
        const parsed = readBackendErrorDetail(text)
        const detail = parsed?.detail
        if (detail?.code === 'CLICK_ORDER_NOT_FOUND') {
          setPendingClickOrder(null)
          setClickStatusText(
            lx(
              'Счёт CLICK не найден или уже закрыт. Создайте новый платёж.',
              'CLICK invoice not found or already closed. Create a new payment.',
              "CLICK hisobi topilmadi yoki yopilgan. Yangi to'lov yarating.",
            ),
          )
        } else {
          setClickStatusText(
            lx(
              'Не удалось проверить статус CLICK. Попробуйте ещё раз.',
              'Could not verify CLICK payment status. Please try again.',
              "CLICK to'lovi holatini tekshirib bo'lmadi. Qayta urinib ko'ring.",
            ),
          )
        }
      }
    } finally {
      if (!silent) setClickPolling(false)
    }
  }

  const startClickPayment = async (planCode: ClickPlanCode) => {
    const jwt = token
    if (!jwt) return

    try {
      metrikaReachGoal(METRIKA_GOALS.paymentStart, { provider: 'click', plan: planCode })
      setClickBusyPlan(planCode)
      setClickStatusText('')
      setShowBillingPaymentHelp(false)
      setShowProfilePaymentHelp(false)

      const out = await createClickPayment(jwt, planCode)
      const orderId = String(out?.order_id || '').trim()
      const link = String(out?.payment_url || '').trim()

      if (!orderId || !link) {
        throw new Error(
          lx(
            'Провайдер CLICK не вернул ссылку оплаты.',
            'CLICK provider did not return a payment link.',
            "CLICK provayderi to'lov havolasini qaytarmadi.",
          ),
        )
      }

      setPendingClickOrder(orderId)
      if (showBillingFlow) {
        setBillingFlowStep('country')
      }
      setClickStatusText(
        lx(
          'Счёт CLICK открыт. Завершите оплату и вернитесь в приложение.',
          'CLICK invoice created. Complete payment and return to the app.',
          "CLICK hisobi yaratildi. To'lovni tugatib, ilovaga qayting.",
        ),
      )
      openTelegramUrl(link)
      window.setTimeout(() => {
        void checkClickStatus(orderId, true)
      }, 3500)
    } catch (err: any) {
      const text = String(err?.message || '')
      const parsed = readBackendErrorDetail(text)
      const detail = parsed?.detail

      if (detail?.code === 'CLICK_NOT_CONFIGURED') {
        setClickStatusText(
          lx(
            'CLICK ещё не настроен на сервере. Проверьте интеграцию и повторите.',
            'CLICK is not configured on server yet. Check integration and retry.',
            "CLICK hali serverda sozlanmagan. Integratsiyani tekshirib qayta urinib ko'ring.",
          ),
        )
      } else if (detail?.code === 'CLICK_INTRO_ALREADY_USED') {
        setClickStatusText(
          lx(
            'Пробная неделя уже была использована. Выберите другой тариф.',
            'Trial week has already been used. Select another plan.',
            "Sinov haftasi allaqachon ishlatilgan. Boshqa tarifni tanlang.",
          ),
        )
      } else if (detail?.message) {
        setClickStatusText(String(detail.message))
      } else {
        setClickStatusText(
          lx(
            'Не удалось создать счёт CLICK. Попробуйте ещё раз.',
            'Could not create CLICK invoice. Please try again.',
            "CLICK hisobi yaratilmadi. Qayta urinib ko'ring.",
          ),
        )
      }
    } finally {
      setClickBusyPlan(null)
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

  useEffect(() => {
    if (!token || !clickOrderId) return

    let stopped = false
    const tick = async () => {
      if (stopped) return
      await checkClickStatus(clickOrderId, true)
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
  }, [token, clickOrderId])

  const readingLimitMessage =
    appLanguage === 'en'
      ? `Your monthly free reading limit is exhausted.\n\nOpen payment in the app (profile or paywall) or via bot: ${BOT_PAYMENT_URL}`
      : appLanguage === 'uz'
        ? `Oy bo‘yicha bepul yoyilmalar limiti tugadi.\n\nTo‘lovni ilova ichida (profil yoki paywall) yoki bot orqali oching: ${BOT_PAYMENT_URL}`
        : `Бесплатный лимит раскладов за месяц исчерпан.\n\nОткройте оплату в приложении (профиль или paywall) или через бота: ${BOT_PAYMENT_URL}`

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
    const locale = appLanguage === 'en' ? 'en-US' : appLanguage === 'uz' ? 'uz-UZ' : 'ru-RU'
    return d.toLocaleDateString(locale)
  }

  const mapReadingError = (raw: string) => {
    const msg = String(raw || '').trim()
    const parsed = readBackendErrorDetail(msg)
    const detail = parsed?.detail

    if (isReadingLimitExceeded(msg)) {
      return readingLimitMessage
    }
    if (/401|403/i.test(msg)) {
      if (appLanguage === 'en') return 'Session expired. Reopen the mini app and try again.'
      if (appLanguage === 'uz') return "Sessiya tugagan. Mini ilovani qayta ochib yana urinib ko'ring."
      return 'Сессия устарела. Перезапустите мини-приложение и попробуйте снова.'
    }
    if (/503|service unavailable/i.test(msg)) {
      if (appLanguage === 'en') return 'AI service is temporarily unavailable. Please retry in a minute.'
      if (appLanguage === 'uz') return "AI xizmati vaqtincha ishlamayapti. Bir daqiqadan keyin qayta urinib ko'ring."
      return 'AI-сервис временно недоступен. Повторите через минуту.'
    }
    if (typeof detail === 'string' && detail.trim() && appLanguage === 'ru') return detail.trim()
    if (msg && appLanguage === 'ru') return msg
    return lx(
      'Не удалось получить ответ от сервера.',
      'Could not get a server response.',
      "Serverdan javob olib bo'lmadi.",
    )
  }

  const [needsMotionPermission, setNeedsMotionPermission] = useState(false)
  const [motionParallaxEnabled, setMotionParallaxEnabled] = useState(false)
  const [pressed, setPressed] = useState(false)

  const [askInputFocused, setAskInputFocused] = useState(false)
  const [keyboardInset, setKeyboardInset] = useState(0)
  const focusSyncTRef = useRef<number | null>(null)

  /* =============================================================================================
     [10] ЗАПИСЬ ГОЛОСА
  ============================================================================================= */

  const [isRecording, setIsRecording] = useState(false)
  const [activeVoiceTarget, setActiveVoiceTarget] = useState<VoiceInputTarget | null>(null)
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
    topics.forEach((t, i) => map.set(t.id, i))
    return map
  }, [topics])
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

  const blockIfNoReadingAccess = () => {
    const hasSub = Boolean(billing?.has_active_subscription)
    const freeLeft = Math.max(0, Number(billing?.free_left ?? 0))
    const paidBalance = Math.max(0, Number(billing?.paid_readings_balance ?? 0))
    const noAccessBySnapshot = !!billing && !hasSub && freeLeft <= 0 && paidBalance <= 0
    if (billing?.can_create_reading === false || noAccessBySnapshot) {
      setShowAccessPaywall(true)
      return true
    }
    return false
  }

  const onBeginReading = () => {
    const spreadOk = !!spread

    if (!spreadOk) {
      pulseCtaRed()
      return flashStageBorder('spread')
    }

    if (spread !== 'card_of_day' && blockIfNoReadingAccess()) {
      return
    }

    metrikaReachGoal(METRIKA_GOALS.readingStart, {
      spread,
      topic,
      has_question: String(question || '').trim().length > 0 ? 1 : 0,
    })

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
    const useGyroParallax = motionParallaxEnabled && !reducedMotion
    const gyroMix = useGyroParallax ? (lowPowerDevice ? 0.22 : 0.35) : 0
    const pointerMix = 1 - gyroMix
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

    const pickCometEdge = (introTopBias: boolean) => {
      const r = Math.random()
      if (introTopBias) {
        if (r < 0.84) return 0 // top
        if (r < 0.92) return 3 // left
        if (r < 0.98) return 1 // right
        return 2 // bottom
      }

      if (r < 0.62) return 0 // top
      if (r < 0.79) return 3 // left
      if (r < 0.94) return 1 // right
      return 2 // bottom
    }

    const spawnComet = (introTopBias = false) => {
      const pad = 120
      const edge = pickCometEdge(introTopBias) // 0=top,1=right,2=bottom,3=left

      let x = 0
      let y = 0

      if (edge === 0) {
        x = rand(-pad, width + pad)
        y = -pad
      } else if (edge === 1) {
        x = width + pad
        y = rand(-pad, height + pad)
      } else if (edge === 2) {
        x = rand(-pad, width + pad)
        y = height + pad
      } else {
        x = -pad
        y = rand(-pad, height + pad)
      }

      const targetX = rand(width * 0.14, width * 0.86)
      const targetY = introTopBias
        ? rand(height * 0.16, height * 0.58)
        : rand(height * 0.12, height * 0.88)
      const dirX = targetX - x
      const dirY = targetY - y
      const len = Math.hypot(dirX, dirY) || 1

      const speed = rand(lowPowerDevice ? 430 : 520, lowPowerDevice ? 720 : 900)
      const vx = (dirX / len) * speed
      const vy = (dirY / len) * speed

      comets.push({
        x,
        y,
        vx,
        vy,
        life: 0,
        maxLife: rand(lowPowerDevice ? 0.6 : 0.72, lowPowerDevice ? 1.1 : 1.32),
        tail: rand(lowPowerDevice ? 72 : 94, lowPowerDevice ? 118 : 148),
        width: rand(lowPowerDevice ? 1.1 : 1.35, lowPowerDevice ? 2.1 : 2.65),
      })
    }

    let last = performance.now()
    let sceneAge = 0
    let cometTimer = 0
    const cometInterval = lowPowerDevice ? 1.75 : 1.08
    const cometChance = lowPowerDevice ? 0.56 : 0.86
    const maxComets = lowPowerDevice ? 3 : 5
    let rafId = 0

    const introBurstCount = lowPowerDevice ? 2 : 3
    for (let i = 0; i < introBurstCount; i++) spawnComet(true)
    cometTimer = cometInterval * 0.35

    const draw = (now: number) => {
      const frameGap = now - last
      if (frameGap < minFrameMs) {
        rafId = requestAnimationFrame(draw)
        return
      }

      const dt = clamp(frameGap / 1000, 0, 0.05)
      last = now
      sceneAge += dt

      if (document.hidden) {
        rafId = requestAnimationFrame(draw)
        return
      }

      pX += (targetPX - pX) * PARALLAX.follow
      pY += (targetPY - pY) * PARALLAX.follow

      gx += (tgtGX - gx) * 0.06
      gy += (tgtGY - gy) * 0.06

      const mixX = pX * pointerMix + gx * gyroMix
      const mixY = pY * pointerMix + gy * gyroMix

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
        const introTopBias = sceneAge < 6.5
        const chance = introTopBias ? Math.min(0.98, cometChance + 0.1) : cometChance
        if (comets.length < maxComets && Math.random() < chance) spawnComet(introTopBias)
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
  }, [motionParallaxEnabled])

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
        const granted = res === 'granted'
        setNeedsMotionPermission(!granted)
        if (granted) setMotionParallaxEnabled(true)
      } else {
        setNeedsMotionPermission(false)
        setMotionParallaxEnabled(true)
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
      setActiveVoiceTarget(null)
    }
  }

  const toggleRecording = async (target: VoiceInputTarget = 'home') => {
    const seedValue = target === 'photo_main' ? photoMainQuestion : question
    const setTargetValue = target === 'photo_main' ? setPhotoMainQuestion : setQuestion

    if (isRecording) {
      if (activeVoiceTarget === target) {
        await stopRecording()
        return
      }
      await stopRecording()
    }

    const SpeechRecognitionCtor = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition
    if (typeof SpeechRecognitionCtor === 'function') {
      try {
        speechSeedRef.current = seedValue.trim()
        speechFinalRef.current = ''

        const recognition = new SpeechRecognitionCtor()
        speechRecognitionRef.current = recognition
        recognition.lang = appLanguage === 'en' ? 'en-US' : appLanguage === 'uz' ? 'uz-UZ' : 'ru-RU'
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
          if (full) setTargetValue(full)
        }

        recognition.onerror = async () => {
          await stopRecording()
        }

        recognition.onend = () => {
          speechRecognitionRef.current = null
          const full = [speechSeedRef.current, speechFinalRef.current].filter(Boolean).join(' ').trim()
          if (full) setTargetValue(full)
          setIsRecording(false)
          setActiveVoiceTarget(null)
        }

        recognition.start()
        setIsRecording(true)
        setActiveVoiceTarget(target)
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
      setActiveVoiceTarget(target)
    } catch (e) {
      console.error(e)
      setIsRecording(false)
      setActiveVoiceTarget(null)
      await stopRecording()
    }
  }

  const isHomeRecording = isRecording && activeVoiceTarget === 'home'
  const isPhotoMainRecording = isRecording && activeVoiceTarget === 'photo_main'

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
    const note = buildSafetyNotice(sourceQuestion, appLanguage)
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
  const lastMetrikaPathRef = useRef('')
  const paywallShownRef = useRef(false)
  const paidOrdersTrackedRef = useRef<Set<string>>(new Set())
  const paymentSuccessNotifiedRef = useRef<Set<string>>(new Set())

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
              const spreadTypeKey = String(p.spread_type || '')
              const fallbackLabel =
                SPREAD_HISTORY_LABELS_BY_LANG[appLanguage]?.[spreadTypeKey] ||
                SPREAD_HISTORY_LABELS_BY_LANG.ru[spreadTypeKey] ||
                lx('Расклад', 'Spread', 'Yoyilma')
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
        setHistoryError(
          lx(
            'Не удалось загрузить историю',
            'Could not load history.',
            "Tarixni yuklab bo'lmadi.",
          ),
        )
      } finally {
        if (!mounted) return
        setHistoryLoading(false)
      }
    }

    load()

    return () => {
      mounted = false
    }
  }, [navTab, token, appLanguage])

  useEffect(() => {
    if (navTab !== 'history') setOpenedReadingId(null)
  }, [navTab])

  /* =============================================================================================
    [20.2] РАСКЛАД "3 КАРТЫ": 3 ЭКРАНА (SETUP → SHUFFLE → RESULT)
  ============================================================================================= */

  type ThreeScreen = 'setup' | 'shuffle' | 'result'
  type ThreeQuestionKind = 'open' | 'yesno' | 'advice'

  const THREE_QKINDS: { id: ThreeQuestionKind; label: string}[] = useMemo(() => [
    { id: 'open', label: lx('Открытый вопрос', 'Open question', 'Ochiq savol') },
    { id: 'yesno', label: lx('Да / Нет', 'Yes / No', "Ha / Yo'q") },
    { id: 'advice', label: lx('Совет', 'Advice', 'Maslahat') },
  ], [appLanguage])

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
  }, [THREE_QKINDS])

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

  const PPF_FOCUS: { id: PpfFocus; label: string }[] = useMemo(() => [
    { id: 'past', label: lx('Прошлое', 'Past', "O'tmish") },
    { id: 'present', label: lx('Настоящее', 'Present', 'Hozir') },
    { id: 'future', label: lx('Будущее', 'Future', 'Kelajak') },
  ], [appLanguage])
  const PPF_SLOT_LABELS = useMemo(
    () => [lx('Прошлое', 'Past', "O'tmish"), lx('Настоящее', 'Present', 'Hozir'), lx('Будущее', 'Future', 'Kelajak')] as const,
    [appLanguage],
  )

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
  }, [PPF_FOCUS])

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

  const SPREAD_HISTORY_LABELS_BY_LANG: Record<AppLanguage, Record<string, string>> = {
    ru: {
      three_cards: 'Расклад по 3 картам',
      ppf: 'Прошлое • Настоящее • Будущее',
      decision: 'Принятие решения',
      custom: 'Пользовательский расклад',
      photo_analysis: 'Анализ фото',
    },
    en: {
      three_cards: '3-card spread',
      ppf: 'Past • Present • Future',
      decision: 'Decision spread',
      custom: 'Custom spread',
      photo_analysis: 'Photo analysis',
    },
    uz: {
      three_cards: '3 kartalik yoyilma',
      ppf: "O'tmish • Hozir • Kelajak",
      decision: 'Qaror yoyilmasi',
      custom: 'Shaxsiy yoyilma',
      photo_analysis: 'Foto tahlili',
    },
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
  const [cardDayLoaderStep, setCardDayLoaderStep] = useState(0)
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

  useEffect(() => {
    if (!cardDayLoading) {
      setCardDayLoaderStep(0)
      return
    }
    const timer = window.setInterval(() => {
      setCardDayLoaderStep((idx) => (idx + 1) % cardDayLoadingSteps.length)
    }, 1200)
    return () => window.clearInterval(timer)
  }, [cardDayLoading, cardDayLoadingSteps.length])

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
    if (homeTourIndex >= homeTourSteps.length - 1) {
      closeHomeTour()
      return
    }
    setHomeTourIndex((i) => Math.min(homeTourSteps.length - 1, i + 1))
  }

  const prevHomeTourStep = () => {
    setHomeTourIndex((i) => Math.max(0, i - 1))
  }

  useEffect(() => {
    if (authStatus !== 'ready') {
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
  }, [authStatus, view, navTab, user?.telegram_id])

  useEffect(() => {
    if (!showHomeTour || view !== 'home' || navTab !== 'main') return
    const step = homeTourSteps[homeTourIndex]
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
    const step = homeTourSteps[homeTourIndex]
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
    metrikaReachGoal(METRIKA_GOALS.openCardDay, { source: 'home' })
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
    setCardDayLoaderStep(0)
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
        const dto = await createCardOfDay(token, {
          question: '',
          topic: 'other',
          deck_size: 78,
          consider_reversed: true,
          app_language: appLanguage,
        })
        applyDailyDto(dto)
        // create_or_get: если карта уже была — сразу результат; если новая — оставляем шаг перемешивания.
        shouldOpenReadyResult = !Boolean((dto as CardOfDayDto).is_new)
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
        } else {
          setMotionParallaxEnabled(true)
        }
      }
      setCardDayLoading(false)
    }
  }

  const openCardDayFromHistory = (it: CardHistoryItem) => {
    metrikaReachGoal(METRIKA_GOALS.openCardDay, { source: 'history' })
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
    if (blockIfNoReadingAccess()) return
    metrikaReachGoal(METRIKA_GOALS.openPhotoAnalysis, { source: 'home' })
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
        reject(
          new Error(
            lx(
              'Не удалось прочитать изображение.',
              'Could not read image.',
              "Rasmni o'qib bo'lmadi.",
            ),
          ),
        )
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
    if (!msg) {
      return lx(
        'Не удалось получить ответ от AI.',
        'Could not get a response from AI.',
        "AI'dan javob olib bo'lmadi.",
      )
    }
    const parsed = readBackendErrorDetail(msg)
    const detail = parsed?.detail
    if (isReadingLimitExceeded(msg)) {
      return readingLimitMessage
    }
    if (/401|403/i.test(msg)) {
      return lx(
        'Сессия устарела. Перезапустите мини-приложение и попробуйте снова.',
        'Session expired. Reopen the mini app and try again.',
        "Sessiya tugagan. Mini ilovani qayta ochib yana urinib ko'ring.",
      )
    }
    if (/413|too large/i.test(msg)) {
      return lx(
        'Фото слишком большое. Выберите более лёгкое изображение.',
        'Image is too large. Choose a lighter file.',
        "Rasm juda katta. Kichikroq faylni tanlang.",
      )
    }
    if (/415|unsupported/i.test(msg)) {
      return lx(
        'Формат фото не поддерживается. Лучше использовать JPG или PNG.',
        'Image format is not supported. Use JPG or PNG.',
        "Rasm formati qo'llab-quvvatlanmaydi. JPG yoki PNG ishlating.",
      )
    }
    if (/503|service unavailable/i.test(msg)) {
      return lx(
        'AI-сервис временно недоступен. Повторите через минуту.',
        'AI service is temporarily unavailable. Retry in a minute.',
        "AI xizmati vaqtincha ishlamayapti. Bir daqiqadan so'ng urinib ko'ring.",
      )
    }
    if (/load failed|failed to fetch|networkerror/i.test(msg)) {
      return lx(
        'Не удалось отправить фото на сервер. Проверьте интернет и попробуйте ещё раз.',
        'Could not send image to server. Check connection and try again.',
        "Rasm serverga yuborilmadi. Internetni tekshirib, qayta urinib ko'ring.",
      )
    }
    if (typeof detail === 'string' && detail.trim() && appLanguage === 'ru') return detail.trim()
    if (msg && appLanguage === 'ru') return msg
    return lx(
      'Не удалось получить ответ от AI.',
      'Could not get a response from AI.',
      "AI'dan javob olib bo'lmadi.",
    )
  }

  const normalizePhotoCards = (raw: any): PhotoCardItem[] => {
    if (!Array.isArray(raw)) return []
    return raw.slice(0, 10).map((card: any, idx: number) => {
      const cardIndex = Number(card?.card_index)
      const confRaw = Number(card?.confidence)
      const fallbackTitle = String(
        card?.title ||
          card?.position ||
          (appLanguage === 'en' ? `Card ${idx + 1}` : appLanguage === 'uz' ? `Karta ${idx + 1}` : `Карта ${idx + 1}`)
      ).trim()
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
      return {
        ok: false as const,
        reason:
          appLanguage === 'en'
            ? 'Cards were not recognized. Try taking a top-down photo in good light.'
            : appLanguage === 'uz'
              ? "Kartalar aniqlanmadi. Yaxshi yorug'likda yuqoridan suratga olib ko'ring."
              : 'Карты не распознаны. Попробуйте сделать фото сверху при хорошем свете.',
      }
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
      .map((card) =>
        String(card.card_name || card.title || card.position || (appLanguage === 'en' ? 'Card' : appLanguage === 'uz' ? 'Karta' : 'Карта')).trim()
      )
      .filter(Boolean)
      .join(', ')
    if (appLanguage === 'en') {
      const main = String(questionText || '').trim()
        ? `For your question, the card combination (${cardsLine || 'this spread'}) suggests choosing a steadier line of action.`
        : `The card combination (${cardsLine || 'this spread'}) shows a period of reviewing priorities and inner alignment.`
      return [
        '## General direction',
        main,
        '',
        '## Recommendations',
        '- Separate facts from anxious scenarios and avoid rushed decisions.',
        '- Pick one concrete step you can realistically do today.',
        '- Keep a calm and consistent pace for the next 24 hours.',
      ].join('\n')
    }
    if (appLanguage === 'uz') {
      const main = String(questionText || '').trim()
        ? `Savolingiz bo'yicha kartalar uyg'unligi (${cardsLine || 'shu yoyilma'}) yanada barqaror harakat yo'lini tanlash kerakligini ko'rsatmoqda.`
        : `Kartalar uyg'unligi (${cardsLine || 'shu yoyilma'}) ustuvorliklarni qayta ko'rib chiqish va ichki moslashuv davrini ko'rsatadi.`
      return [
        "## Umumiy yo'nalish",
        main,
        '',
        '## Tavsiyalar',
        "- Faktlarni xavotirli taxminlardan ajrating va shoshma-shosharlik bilan qaror qabul qilmang.",
        "- Bugun amalga oshirish mumkin bo'lgan bitta aniq qadamni tanlang.",
        '- Keyingi 24 soatda xotirjam va izchil sur’atni saqlang.',
      ].join('\n')
    }
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
      <div
        className={`photo-cards-fan ${compact ? 'is-compact' : ''} ${sizeClass}`.trim()}
        aria-label={appLanguage === 'en' ? 'Recognized cards' : appLanguage === 'uz' ? 'Aniqlangan kartalar' : 'Распознанные карты'}
      >
        {source.map((card, idx) => {
          const name = String(
            card.card_name ||
              card.title ||
              card.position ||
              (appLanguage === 'en' ? `Card ${idx + 1}` : appLanguage === 'uz' ? `Karta ${idx + 1}` : `Карта ${idx + 1}`)
          ).trim()
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
      setPhotoError(
        lx(
          'Нужен вход через Telegram, чтобы отправить фото на анализ.',
          'Telegram sign-in is required to analyze photos.',
          "Rasmni tahlil qilish uchun Telegram orqali kirish kerak.",
        ),
      )
      return
    }

    const sourceFile = incomingFile || photoFile
    if (!sourceFile) {
      setPhotoStep('error')
      setPhotoError(
        lx(
          'Выберите фото расклада (из галереи или сделайте снимок).',
          'Choose a spread photo (from gallery or camera).',
          "Yoyilma rasmini tanlang (galereyadan yoki kameradan).",
        ),
      )
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
            app_language: appLanguage,
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
      metrikaReachGoal(METRIKA_GOALS.photoDetectSuccess, {
        cards_count: Array.isArray(detection.cards) ? detection.cards.length : 0,
      })
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
        app_language: appLanguage,
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
      metrikaReachGoal(METRIKA_GOALS.photoInterpretationDone, {
        cards_count: (detection.ok ? (detection.cards || photoDetectedCards) : photoDetectedCards).length || 0,
        has_question: String(photoMainQuestion || '').trim().length > 0 ? 1 : 0,
      })
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
      setPhotoFollowupError(
        lx(
          'Введите уточняющий вопрос.',
          'Enter a follow-up question.',
          "Aniqlashtiruvchi savol kiriting.",
        ),
      )
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
        app_language: appLanguage,
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
      metrikaReachGoal(METRIKA_GOALS.photoFollowupDone, {
        has_main_question: String(mainQ || '').trim().length > 0 ? 1 : 0,
      })
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
    .map((card, idx) =>
      String(
        card.card_name ||
          card.title ||
          card.position ||
          lx(`Карта ${idx + 1}`, `Card ${idx + 1}`, `Karta ${idx + 1}`),
      ).trim(),
    )
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
    const decoded = (() => {
      try {
        return decodeURIComponent(raw)
      } catch {
        return raw
      }
    })()
    return decoded
      .replace(/-[A-Za-z0-9_-]{6,}$/, '')
      .replace(/_/g, ' ')
      .replace(/-/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
  }

  const withReversedLabel = (name: string, isReversed: boolean) =>
    isReversed ? `${name}${lx(' (перевёрнутая)', ' (reversed)', ' (teskari)')}` : name

  const threeRoleLabels = useMemo(
    () =>
      [lx('Карта 1', 'Card 1', '1-karta'), lx('Карта 2', 'Card 2', '2-karta'), lx('Карта 3', 'Card 3', '3-karta')] as const,
    [appLanguage],
  )
  const ppfRoleLabels = PPF_SLOT_LABELS

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
    const idxs = pickUniqueIndexes(3, FRONT_CARD_URLS.length || 78)
    return idxs.map((idx, i) => {
      const url = FRONT_CARD_URLS[idx] || backCardImg
      const isReversed = Math.random() < 0.5
      const baseName = cardNameFromUrl(url)
      return {
        idx,
        url,
        name: withReversedLabel(baseName, isReversed),
        role: threeRoleLabels[i] || lx(`Карта ${i + 1}`, `Card ${i + 1}`, `${i + 1}-karta`),
        text: '',
        isReversed,
      }
    })
  }

  const buildThreeCardsMock = (): ThreeCardResult[] => {
    const idxs = pickUniqueIndexes(3, FRONT_CARD_URLS.length || 78)

    return idxs.map((idx, i) => {
      const url = FRONT_CARD_URLS[idx] || backCardImg
      const name = cardNameFromUrl(url)

      const text =
        threeKind === 'yesno'
          ? lx(
              'Ответ будет подтянут с бэкенда. Сейчас это mock.\n\nСфокусируйтесь на ощущениях от карты и переформулируйте вопрос, если нужно.',
              'Answer will be loaded from backend. This is a mock now.\n\nFocus on your first feeling from the card and rephrase the question if needed.',
              "Javob backenddan olinadi. Hozir bu mock.\n\nKartadan olgan birinchi hisingizga e'tibor bering va kerak bo'lsa savolni qayta yozing.",
            )
          : threeKind === 'advice'
          ? lx(
              'Совет будет подтянут с бэкенда. Сейчас это mock.\n\nПодумайте: какой маленький шаг вы можете сделать уже сегодня?',
              'Advice will be loaded from backend. This is a mock now.\n\nThink: what small step can you take today?',
              "Maslahat backenddan olinadi. Hozir bu mock.\n\nO'ylab ko'ring: bugun qaysi kichik qadamni qo'ya olasiz?",
            )
          : lx(
              'Интерпретация будет подтянута с бэкенда. Сейчас это mock.\n\nЗаметьте эмоции и ассоциации — это уже часть ответа.',
              'Interpretation will be loaded from backend. This is a mock now.\n\nNotice your emotions and associations, this is already part of the answer.',
              "Talqin backenddan olinadi. Hozir bu mock.\n\nHissiyot va assotsiatsiyalarni payqang, bu ham javobning bir qismi.",
            )

      return { idx, url, name, role: threeRoleLabels[i], text, isReversed: false }
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
    if (blockIfNoReadingAccess()) return
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
    else setMotionParallaxEnabled(true)

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
    else setMotionParallaxEnabled(true)

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
    const idxs = pickUniqueIndexes(3, FRONT_CARD_URLS.length || 78)

    return idxs.map((idx, i) => {
      const url = FRONT_CARD_URLS[idx] || backCardImg
      const name = cardNameFromUrl(url)

      const focusLine =
        ppfFocus === 'past'
          ? lx('Фокус: прошлое — что привело к ситуации?', 'Focus: past - what led to this situation?', "Fokus: o'tmish - bu holatga nima olib keldi?")
          : ppfFocus === 'present'
          ? lx('Фокус: настоящее — что происходит прямо сейчас?', 'Focus: present - what is happening right now?', 'Fokus: hozir - ayni paytda nima bo‘lyapti?')
          : lx('Фокус: будущее — куда ведёт текущая динамика?', 'Focus: future - where is current dynamics leading?', 'Fokus: kelajak - hozirgi yo‘nalish qayerga olib boryapti?')

      const text =
        `${focusLine}\n\n` +
        lx(
          'Интерпретация будет подтянута с бэкенда. Сейчас это mock.\n\nСмотрите на символы, ощущения и первую ассоциацию — это часто самый точный ответ.',
          'Interpretation will be loaded from backend. This is a mock now.\n\nWatch symbols, feelings and first association - that is often the most precise answer.',
          "Talqin backenddan olinadi. Hozir bu mock.\n\nBelgilar, hislar va birinchi assotsiatsiyaga e'tibor bering - ko'pincha eng aniq javob shu bo'ladi.",
        )

      return { idx, url, name, role: ppfRoleLabels[i], text, isReversed: false }
    })
  }

  const buildPpfCardsPreview = (): PpfCardResult[] => {
    const idxs = pickUniqueIndexes(3, FRONT_CARD_URLS.length || 78)
    return idxs.map((idx, i) => {
      const url = FRONT_CARD_URLS[idx] || backCardImg
      const isReversed = Math.random() < 0.5
      const baseName = cardNameFromUrl(url)
      return {
        idx,
        url,
        name: withReversedLabel(baseName, isReversed),
        role: ppfRoleLabels[i] || '',
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
    if (blockIfNoReadingAccess()) return
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

  const DECISION_SLOT_LABELS = useMemo(
    () => [lx('Вариант A', 'Option A', 'A varianti'), lx('Вариант B', 'Option B', 'B varianti')] as const,
    [appLanguage],
  )
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
        name: withReversedLabel(baseName, isReversed),
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
      app_language: appLanguage,
    }
    const reading: any = await createReading(token, params)
    void refreshBilling(token)
    // Save the description from the backend so we can display it in the UI
    setThreeDesc(String(reading?.description ?? ''))
    const mapped = (reading.cards || []).slice(0, 3).map((c: any, i: number) => {
      const idx = Number(c.card_index ?? 0)
      const url = FRONT_CARD_URLS[idx] || backCardImg
      const isReversed = Boolean(c?.is_reversed)
      const baseName = String(c.card_name || cardNameFromUrl(url))
      const name = withReversedLabel(baseName, isReversed)
      const text = String(c.meaning || '').trim() || ''
      return {
        idx,
        url,
        name,
        role: threeRoleLabels[i] || lx(`Карта ${i + 1}`, `Card ${i + 1}`, `${i + 1}-karta`),
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
      app_language: appLanguage,
    }
    const reading: any = await createReading(token, params)
    void refreshBilling(token)
    // Save description returned from the backend
    setPpfDesc(String(reading?.description ?? ''))
    const focusLine =
      ppfFocus === 'past'
        ? lx('Фокус: прошлое — что привело к ситуации?', 'Focus: past - what led to this situation?', "Fokus: o'tmish - bu holatga nima olib keldi?")
        : ppfFocus === 'present'
        ? lx('Фокус: настоящее — что происходит прямо сейчас?', 'Focus: present - what is happening right now?', 'Fokus: hozir - ayni paytda nima bo‘lyapti?')
        : lx('Фокус: будущее — куда ведёт текущая динамика?', 'Focus: future - where is current dynamics leading?', 'Fokus: kelajak - hozirgi yo‘nalish qayerga olib boryapti?')
    const mapped = (reading.cards || []).slice(0, 3).map((c: any, i: number) => {
      const idx = Number(c.card_index ?? 0)
      const url = FRONT_CARD_URLS[idx] || backCardImg
      const isReversed = Boolean(c?.is_reversed)
      const baseName = String(c.card_name || cardNameFromUrl(url))
      const name = withReversedLabel(baseName, isReversed)
      const meaning = String(c.meaning || '').trim() || ''
      const text = `${focusLine}\n\n${meaning}`
      return {
        idx,
        url,
        name,
        role: ppfRoleLabels[i] || '',
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
      String(questionText ?? decisionQuestion ?? question).trim() ||
      lx('Выбор между вариантом A и вариантом B', 'Choice between option A and option B', 'A varianti va B varianti o‘rtasidagi tanlov')

    const params = {
      spread_type: 'decision' as const,
      topic: topic,
      question: effectiveQuestion,
      consider_reversed: true,
      forced_cards: toForcedCards(previewCards),
      app_language: appLanguage,
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
      const name = withReversedLabel(baseName, isReversed)
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
    if (blockIfNoReadingAccess()) return
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
    else setMotionParallaxEnabled(true)

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

  useEffect(() => {
    metrikaReachGoal(METRIKA_GOALS.appOpen)
  }, [])

  useEffect(() => {
    const virtualPath = (() => {
      if (showAccessPaywall) return '/paywall'
      if (view === 'home') return `/home/${navTab}`
      if (view === 'photo_analysis') return `/photo/${photoStep}`
      if (view === 'three_cards_prep') return `/reading/three_cards/${threeScreen}`
      if (view === 'past_present_future_prep') return `/reading/past_present_future/${ppfScreen}`
      if (view === 'decision_prep') return `/reading/decision/${decisionScreen}`
      if (view === 'card_day_prep') return '/reading/card_day'
      return '/home/main'
    })()

    if (lastMetrikaPathRef.current === virtualPath) return

    const prev = lastMetrikaPathRef.current
    metrikaHit(virtualPath, {
      title: `AI Taro ${virtualPath}`,
      referer: prev ? `${window.location.origin}${prev}` : undefined,
      params: {
        view,
        nav_tab: navTab,
        photo_step: photoStep,
      },
    })
    lastMetrikaPathRef.current = virtualPath
  }, [view, navTab, photoStep, threeScreen, ppfScreen, decisionScreen, showAccessPaywall])

  useEffect(() => {
    if (showAccessPaywall && !paywallShownRef.current) {
      metrikaReachGoal(METRIKA_GOALS.paywallShown, {
        view,
        nav_tab: navTab,
      })
      paywallShownRef.current = true
      return
    }
    if (!showAccessPaywall) {
      paywallShownRef.current = false
    }
  }, [showAccessPaywall, view, navTab])

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
      : t('profile')
  const profileUsername = user?.username ? `@${user.username}` : '@username'
  const freeLimit = Math.max(1, Number(billing?.free_limit ?? 5))
  const freeLeft = Math.max(0, Number(billing?.free_left ?? 0))
  const subActive = !!billing?.has_active_subscription
  const subLabel = subActive
    ? lx(`Активна до ${formatRuDate(billing?.subscription_until)}`, `Active until ${formatRuDate(billing?.subscription_until)}`, `${formatRuDate(billing?.subscription_until)} gacha faol`)
    : lx('Не активна', 'Inactive', 'Faol emas')
  const currentLegalDoc = activeLegalDoc ? legalDocs[activeLegalDoc] : null
  const canStartReading = !!spread
  const showHomePrimaryCta = view === 'home' && navTab === 'main'
  const isHomeTourActive = showHomeTour && showHomePrimaryCta
  const homeTourStep = isHomeTourActive ? homeTourSteps[Math.max(0, Math.min(homeTourIndex, homeTourSteps.length - 1))] : null
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
  const billingCountryLabel = billingCountries.find((item) => item.code === billingFlowCountry)?.label || '—'
  const billingPlans = BILLING_PLAN_KEYS_BY_COUNTRY[billingFlowCountry] || BILLING_PLAN_KEYS_BY_COUNTRY.ru
  const billingSelectedPlan = billingFlowPlan ? billingPlanMeta[billingFlowPlan] : null
  const billingStepNumber = billingFlowStep === 'country' ? 1 : billingFlowStep === 'plan' ? 2 : 3
  const inAppBillingEnabled = true
  const combinedPaymentStatus = clickStatusText || sbpStatusText
  const hasSbpMethod = !!billingFlowPlan && billingFlowCountry === 'ru' && !!mapPlanToSbpPlan(billingFlowPlan)
  const hasClickMethod = !!billingFlowPlan && billingFlowCountry === 'uz'
  const hasPendingPayment = !!(sbpOrderId || clickOrderId)
  const showBillingStatusArea = billingFlowStep === 'country' && (hasPendingPayment || !!combinedPaymentStatus)

  return (
    <div className="app" ref={appRef}>
      <canvas className="stars-canvas" ref={starsCanvasRef} aria-hidden="true" />
      <canvas className="comets-canvas" ref={cometsCanvasRef} aria-hidden="true" />

{/* AUTH OVERLAY */}
{authStatus === 'loading' && (
  <div className="auth-overlay" role="status" aria-live="polite">
    <div className="auth-overlay__card">
      <div className="auth-overlay__ring" aria-hidden="true" />
      <div className="auth-overlay__title">{t('loadingApp')}</div>
      <div className="auth-overlay__sub">{t('waitSeconds')}</div>
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
      <div style={{ fontWeight: 700, marginBottom: 8 }}>{t('authRequired')}</div>
      <div style={{ fontSize: 13, opacity: 0.85, lineHeight: 1.35 }}>{authError || t('authErrorDefault')}</div>

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
          {t('retry')}
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
          {t('close')}
        </button>
      </div>
    </div>
  </div>
)}

{isHomeTourActive && homeTourStep && homeTourSpotlight && (
  <div
    className="home-tour-overlay"
    role="dialog"
    aria-modal="true"
    aria-label={lx('Быстрый гид по приложению', 'Quick app guide', "Ilova bo'yicha tezkor yo'riqnoma")}
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
          aria-label={lx('Пропустить инструкцию', 'Skip tutorial', "Yo'riqnomani o'tkazib yuborish")}
        >
          ×
        </button>
      </div>
      <div className="home-tour-card__text">{homeTourStep.text}</div>
      <div className="home-tour-card__foot">
        <div className="home-tour-card__step">
          {homeTourIndex + 1} / {homeTourSteps.length}
        </div>
        <div className="home-tour-card__actions">
          {homeTourIndex > 0 && (
            <button
              type="button"
              className="glassbtn home-tour-card__prev"
              onClick={prevHomeTourStep}
            >
              {lx('Назад', 'Back', 'Orqaga')}
            </button>
          )}
          <button
            type="button"
            className="glassbtn home-tour-card__skip"
            onClick={closeHomeTour}
          >
            {lx('Пропустить', 'Skip', "O'tkazib yuborish")}
          </button>
          <button
            type="button"
            className="glassbtn home-tour-card__next"
            onClick={nextHomeTourStep}
          >
            {homeTourIndex >= homeTourSteps.length - 1 ? lx('Готово', 'Done', 'Tayyor') : lx('Далее', 'Next', 'Keyingi')}
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
                    aria-label={navTab !== 'main' ? lx('Вернуться на главную', 'Back to home', 'Bosh sahifaga qaytish') : undefined}
                  >
                    <h1>AI Taro</h1>
                    <p>{t('appSubtitle')}</p>
                  </div>

                  <div className="home-head__actions" aria-label={lx('Навигация', 'Navigation', 'Navigatsiya')}>
                    <button
                      type="button"
                      className="home-head__action"
                      onClick={toggleHistoryTab}
                      aria-label={t('history')}
                      title={t('history')}
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
                      aria-label={t('profile')}
                      title={t('profile')}
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
                  {navTab === 'profile' ? t('profile') : t('history')}
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
                      <div className="card-day__title">{t('cardOfDay')}</div>
                      <div className="card-day__subtitle">
                        <span>{t('dailyGuideLine1')}</span>
                        <span>{t('dailyGuideLine2')}</span>
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
                    <div className="card-day__title">{t('photoSpread')}</div>
                    <div className="card-day__subtitle">
                      <span>{t('photoSpreadLine1')}</span>
                      <span>{t('photoSpreadLine2')}</span>
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
                    <h2 className="home-section-title">{t('askQuestion')}</h2>

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
                          placeholder={t('questionPlaceholder')}
                          enterKeyHint="search"
                          rows={2}
                        />
                        <button
                          type="button"
                          className={`ask-mic ${isHomeRecording ? 'recording' : ''}`}
                          onClick={() => void toggleRecording('home')}
                          aria-label={isHomeRecording ? lx('Остановить запись', 'Stop recording', "Yozuvni to'xtatish") : lx('Начать запись', 'Start recording', 'Yozishni boshlash')}
                          title={isHomeRecording ? lx('Остановить запись', 'Stop recording', "Yozuvni to'xtatish") : lx('Записать голосом', 'Record voice', 'Ovoz yozish')}
                        >
                          <img className="ask-mic__icon" src={micIcon} alt="" aria-hidden="true" />
                        </button>
                      </div>

                      <div className={`ask-hint ${isHomeRecording ? 'is-visible' : ''}`}>
                        {lx('Идёт запись… нажмите ещё раз, чтобы остановить', 'Recording… tap again to stop', "Yozilmoqda… to'xtatish uchun yana bosing")}
                      </div>
                    </div>

                    <h2 className="home-section-title">{t('chooseTopic')}</h2>

                    <div
                      className={`seg seg--topics ${isBumping ? 'is-bump' : ''}`}
                      data-bump={bump}
                      style={{
                        ['--seg-cols' as any]: topics.length,
                        ['--i' as any]: activeIndex,
                        ['--from' as any]: prevIndex,
                      }}
                      role="tablist"
                      aria-label={lx('Выбор темы', 'Topic selection', 'Mavzu tanlash')}
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

                      {topics.map((t) => (
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

                    <h2 className="home-section-title">{t('chooseSpread')}</h2>
                    <div className={`spread-soft-hint ${shouldAttnSpreads ? 'is-visible' : ''}`} aria-live="polite">
                      {t('pickSpreadFirst')}
                    </div>

                    <div
                      className={`spread-list ${shouldAttnSpreads ? 'is-attn' : ''}`}
                      data-attn={shouldAttnSpreads ? attnNonce : undefined}
                      ref={spreadListRef}
                    >
                      {spreads.map((s) => {
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
                        aria-label={lx('Вернуться на главную', 'Back to home', 'Bosh sahifaga qaytish')}
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
                        <span className="subtab-back__label">{lx('Назад', 'Back', 'Orqaga')}</span>
                      </button>
                    </div>

                    {!token && (
                      <div className="history-empty">
                        {appLanguage === 'en'
                          ? 'History is available after Telegram sign-in.'
                          : appLanguage === 'uz'
                            ? "Tarix Telegram orqali kirgandan keyin ko'rinadi."
                            : 'История доступна после входа через Telegram.'}
                      </div>
                    )}

                    {token && historyLoading && <div className="history-loading">{lx('Загружаем историю...', 'Loading history...', 'Tarix yuklanmoqda...')}</div>}

                    {token && !!historyError && <div className="history-error">{historyError}</div>}

                    {token && !historyLoading && !historyError && history.length === 0 && (
                      <div className="history-empty">
                        {lx('Ваша история только начинается...', 'Your history is just beginning...', "Sizning tarixingiz endi boshlanmoqda...")}
                      </div>
                    )}

                    {token && history.length > 0 && (
                      <div className="spread-list">
                        {history.map((it) => {
                          const idx = clamp(it.card_index ?? 0, 0, 77)
                          const img = FRONT_CARD_URLS[idx] || backCardImg
                          const topicLabel = topics.find((t) => t.id === (it.topic as any))?.label || it.topic
                          const isCardDay = it.kind === 'card_of_day'
                          const isReadingOpen = isCardDay ? false : openedReadingId === it.reading_id
                          const title = isCardDay
                            ? (it.card_name || lx('Карта дня', 'Card of the day', 'Kun kartasi'))
                            : (SPREAD_HISTORY_LABELS_BY_LANG[appLanguage]?.[it.spread_type] || SPREAD_HISTORY_LABELS_BY_LANG.ru[it.spread_type] || lx('Расклад', 'Spread', 'Yoyilma'))
                          const rawThemeCapsule = String(it.theme_capsule || '').trim()
                          const themeCapsule = isGenericThemeCapsule(rawThemeCapsule) ? '' : rawThemeCapsule
                          const subtitle = themeCapsule
                            ? `${lx('Тема', 'Theme', 'Mavzu')}: ${themeCapsule}`
                            : `${topicLabel}${it.question ? ` • ${it.question}` : ''}`
                          const when = (() => {
                            const d = new Date(it.created_at)
                            if (!isFinite(d.getTime())) return isCardDay ? it.day_key : ''
                            const locale = appLanguage === 'en' ? 'en-US' : appLanguage === 'uz' ? 'uz-UZ' : 'ru-RU'
                            return d.toLocaleString(locale, {
                              day: '2-digit',
                              month: '2-digit',
                              year: 'numeric',
                              hour: '2-digit',
                              minute: '2-digit',
                            })
                          })()
                          const metaPrefix = isCardDay
                            ? lx('Карта дня', 'Card of the day', 'Kun kartasi')
                            : lx(`${it.cards_count || 0} карт`, `${it.cards_count || 0} cards`, `${it.cards_count || 0} karta`)
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
                                aria-label={
                                  isCardDay
                                    ? lx('Открыть карту дня из истории', 'Open card of the day from history', 'Tarixdan kun kartasini ochish')
                                    : lx('Открыть детали расклада из истории', 'Open spread details from history', 'Tarixdan yoyilma tafsilotlarini ochish')
                                }
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
                                    {isReadingOpen ? lx('Свернуть', 'Collapse', "Yig'ish") : lx('Открыть', 'Open', 'Ochish')}
                                  </div>
                                )}
                              </div>

                              {!isCardDay && isReadingOpen && (
                                <div className="history-reading-detail">
                                  <div className="history-reading-detail__head">
                                    <div className="history-reading-detail__title">{title}</div>
                                  <div className="history-reading-detail__meta">
                                    {themeCapsule ? `${lx('Тема', 'Theme', 'Mavzu')}: ${themeCapsule} • ` : ''}
                                    {topicLabel}
                                    {it.question ? ` • ${it.question}` : ''}
                                    {when ? ` • ${when}` : ''}
                                  </div>
                                  </div>

                                  <div className="history-reading-cards">
                                    {it.cards.map((card, cardIdx) => {
                                      const cardImage = FRONT_CARD_URLS[clamp(card.card_index ?? 0, 0, 77)] || backCardImg
                                      const cardLabel = card.title || card.position || lx(`Карта ${cardIdx + 1}`, `Card ${cardIdx + 1}`, `Karta ${cardIdx + 1}`)
                                      return (
                                        <div key={`${itemKey}:card:${cardIdx}`} className="history-reading-card">
                                          <img
                                            className={`history-reading-card__img ${card.is_reversed ? 'is-reversed' : ''}`}
                                            src={cardImage}
                                            alt={card.card_name || cardLabel}
                                          />
                                          <div className="history-reading-card__label">{cardLabel}</div>
                                          <div className="history-reading-card__name">
                                            {card.card_name || lx(`Карта ${cardIdx + 1}`, `Card ${cardIdx + 1}`, `Karta ${cardIdx + 1}`)}
                                            {card.is_reversed ? lx(' (перевёрнутая)', ' (reversed)', ' (teskari)') : ''}
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
                      aria-label={lx('Вернуться на главную', 'Back to home', 'Bosh sahifaga qaytish')}
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
                      <span className="subtab-back__label">{lx('Назад', 'Back', 'Orqaga')}</span>
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
                      <section className="profile-piece profile-piece--stack" aria-label={lx('Купить подписку', 'Buy subscription', "Obuna sotib olish")}>
                        <div className="profile-piece__info">
                          <div className="profile-piece__title">
                            <span className="profile-icon profile-icon--piece" aria-hidden="true">
                              <svg viewBox="0 0 24 24">
                                <circle cx="12" cy="12" r="7.2" fill="none" stroke="currentColor" strokeWidth="1.7" />
                                <path d="M12 4.8V3M12 21v-1.8M4.8 12H3M21 12h-1.8" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                              </svg>
                            </span>
                            <span>{lx('Купить подписку', 'Buy subscription', "Obuna sotib olish")}</span>
                          </div>
                          <div className="profile-piece__meta">{lx(`Бесплатно в этом месяце: ${freeLeft} из ${freeLimit}`, `Free this month: ${freeLeft} of ${freeLimit}`, `Bu oy bepul: ${freeLeft} / ${freeLimit}`)}</div>
                          <div className="profile-piece__submeta">{lx('Выберите страну и удобный способ оплаты', 'Choose your country and payment method', "Mamlakat va to'lov usulini tanlang")}</div>
                        </div>

                        {inAppBillingEnabled ? (
                          <>
                            <div className="profile-piece__actions profile-piece__actions--single">
                              <button
                                type="button"
                                className="profile-piece__cta profile-piece__cta--click profile-piece__cta--wide"
                                onClick={() => {
                                  metrikaReachGoal(METRIKA_GOALS.paymentStart, { provider: 'profile_inapp' })
                                  openBillingFlow('profile')
                                }}
                              >
                                {lx('Купить подписку', 'Buy subscription', "Obuna sotib olish")}
                              </button>
                            </div>

                            {(sbpOrderId || clickOrderId) && (
                              <div className="profile-piece__checks">
                                <button
                                  type="button"
                                  className="profile-piece__check profile-piece__check--center"
                                  onClick={() => setShowProfilePaymentHelp((prev) => !prev)}
                                >
                                  {showProfilePaymentHelp ? lx('Скрыть', 'Hide', 'Yashirish') : lx('Оплатили, но ошибка?', 'Paid but got an error?', "To'lov qildingiz, lekin xato chiqyaptimi?")}
                                </button>
                                {showProfilePaymentHelp && (
                                  <>
                                    {sbpOrderId && (
                                      <button
                                        type="button"
                                        className="profile-piece__check profile-piece__check--center"
                                        disabled={sbpPolling}
                                        onClick={() => {
                                          void checkSbpStatus()
                                        }}
                                      >
                                        {sbpPolling ? lx('Проверяю…', 'Checking…', 'Tekshirilmoqda…') : lx('Проверить оплату СБП', 'Check SBP payment', "SBP to'lovini tekshirish")}
                                      </button>
                                    )}
                                    {clickOrderId && (
                                      <button
                                        type="button"
                                        className="profile-piece__check profile-piece__check--center"
                                        disabled={clickPolling}
                                        onClick={() => {
                                          void checkClickStatus()
                                        }}
                                      >
                                        {clickPolling ? lx('Проверяю…', 'Checking…', 'Tekshirilmoqda…') : lx('Проверить оплату CLICK', 'Check CLICK payment', "CLICK to'lovini tekshirish")}
                                      </button>
                                    )}
                                    <button
                                      type="button"
                                      className="profile-piece__check profile-piece__check--center profile-piece__check--support"
                                      onClick={() => openTelegramAndCloseMiniApp(SUPPORT_URL)}
                                    >
                                      {lx('Ошибка не уходит? Напишите в поддержку', 'Still not fixed? Contact support', "Muammo ketmayaptimi? Yordamga yozing")}
                                    </button>
                                  </>
                                )}
                              </div>
                            )}

                            {!!combinedPaymentStatus && <div className="profile-piece__status">{combinedPaymentStatus}</div>}
                          </>
                        ) : (
                          <>
                            <div className="profile-piece__actions">
                              <button
                                type="button"
                                className="profile-piece__cta profile-piece__cta--sbp"
                                disabled={sbpBusyPlan === 'sub_2weeks'}
                                onClick={() => {
                                  void startSbpPayment('sub_2weeks')
                                }}
                              >
                                {sbpBusyPlan === 'sub_2weeks'
                                  ? lx('Создаю…', 'Creating…', 'Yaratilmoqda…')
                                  : `${lx('СБП • 2 недели •', 'SBP • 2 weeks •', 'SBP • 2 hafta •')} ${getPlanPriceLabel('ru', 'sub_2weeks')}`}
                              </button>

                              <button
                                type="button"
                                className="profile-piece__cta profile-piece__cta--sbp"
                                disabled={sbpBusyPlan === 'sub_month'}
                                onClick={() => {
                                  void startSbpPayment('sub_month')
                                }}
                              >
                                {sbpBusyPlan === 'sub_month'
                                  ? lx('Создаю…', 'Creating…', 'Yaratilmoqda…')
                                  : `${lx('СБП • месяц •', 'SBP • month •', 'SBP • oy •')} ${getPlanPriceLabel('ru', 'sub_month')}`}
                              </button>

                              <button
                                type="button"
                                className="profile-piece__cta profile-piece__cta--sbp"
                                disabled={sbpBusyPlan === 'sub_year'}
                                onClick={() => {
                                  void startSbpPayment('sub_year')
                                }}
                              >
                                {sbpBusyPlan === 'sub_year'
                                  ? lx('Создаю…', 'Creating…', 'Yaratilmoqda…')
                                  : `${lx('СБП • год •', 'SBP • year •', 'SBP • yil •')} ${getPlanPriceLabel('ru', 'sub_year')}`}
                              </button>

                              <a
                                href={BOT_CARD_URL}
                                target="_blank"
                                rel="noreferrer"
                                className="profile-piece__cta profile-piece__cta--card"
                                onClick={(e) => {
                                  e.preventDefault()
                                  metrikaReachGoal(METRIKA_GOALS.paymentStart, { provider: 'bot_card' })
                                  openTelegramUrl(BOT_CARD_URL)
                                }}
                              >
                                {lx('По карте или SberPay', 'Card or SberPay', 'Karta yoki SberPay')}
                              </a>

                              <a
                                href={BOT_CLICK_URL}
                                target="_blank"
                                rel="noreferrer"
                                className="profile-piece__cta profile-piece__cta--click"
                                onClick={(e) => {
                                  e.preventDefault()
                                  metrikaReachGoal(METRIKA_GOALS.paymentStart, { provider: 'click_app' })
                                  openTelegramUrl(BOT_CLICK_URL)
                                }}
                              >
                                {lx('CLICK (Узбекистан)', 'CLICK (Uzbekistan)', "CLICK (O'zbekiston)")}
                              </a>

                              <a
                                href={BOT_CLICK_CARD_URL}
                                target="_blank"
                                rel="noreferrer"
                                className="profile-piece__cta profile-piece__cta--click"
                                onClick={(e) => {
                                  e.preventDefault()
                                  metrikaReachGoal(METRIKA_GOALS.paymentStart, { provider: 'click_card' })
                                  openTelegramUrl(BOT_CLICK_CARD_URL)
                                }}
                              >
                                {lx('Карта через CLICK (UZ)', 'Card via CLICK (UZ)', "CLICK orqali karta (UZ)")}
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
                                {sbpPolling ? lx('Проверяю…', 'Checking…', 'Tekshirilmoqda…') : lx('Проверить оплату СБП', 'Check SBP payment', "SBP to'lovini tekshirish")}
                              </button>
                            )}

                            {!!sbpStatusText && <div className="profile-piece__status">{sbpStatusText}</div>}
                          </>
                        )}
                      </section>
                    )}

                    <section className="profile-panel" aria-label={t('settings')}>
                      <div className="profile-group-title">{t('settings')}</div>

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
                          <span>{t('personalization')}</span>
                        </span>
                        <span className="profile-link-row__chevron" aria-hidden="true">›</span>
                      </button>

                      <button
                        type="button"
                        className="profile-link-row"
                        onClick={() => {
                          setPrefsError('')
                          setShowLanguageModal(true)
                        }}
                      >
                        <span className="profile-link-row__left">
                          <span className="profile-icon profile-icon--row" aria-hidden="true">
                            <svg viewBox="0 0 24 24">
                              <circle cx="12" cy="12" r="8.3" fill="none" stroke="currentColor" strokeWidth="1.7" />
                              <path d="M3.7 12h16.6M12 3.7c2.4 2.2 3.7 5.2 3.7 8.3s-1.3 6.1-3.7 8.3M12 3.7c-2.4 2.2-3.7 5.2-3.7 8.3s1.3 6.1 3.7 8.3" fill="none" stroke="currentColor" strokeWidth="1.35" />
                            </svg>
                          </span>
                          <span>{t('appLanguage')}</span>
                        </span>
                        <span className="profile-link-row__right">{languageDisplayName(appLanguage, appLanguage)}</span>
                        <span className="profile-link-row__chevron" aria-hidden="true">›</span>
                      </button>

                      <div className="profile-link-row profile-link-row--static">
                        <span className="profile-link-row__left">
                          <span className="profile-icon profile-icon--row" aria-hidden="true">
                            <svg viewBox="0 0 24 24">
                              <path d="M21 3 10 13" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                              <path d="m21 3-7 18-3.4-7.2L3 10l18-7Z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
                            </svg>
                          </span>
                          <span>{t('telegramBinding')}</span>
                        </span>
                        <span className={`profile-link-row__right ${token ? 'is-ok' : 'is-muted'}`}>
                          {token ? t('connected') : t('disconnected')}
                        </span>
                      </div>
                    </section>

                    <section className="profile-panel" aria-label={t('infoSupport')}>
                      <div className="profile-group-title">{t('infoSupport')}</div>

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
                          <span>{t('writeSupport')}</span>
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
                          <span>{t('terms')}</span>
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
                          <span>{t('privacy')}</span>
                        </span>
                        <span className="profile-link-row__chevron" aria-hidden="true">›</span>
                      </a>
                    </section>

                    <button type="button" className="profile-logout-btn" onClick={handleProfileLogout}>
                      {appLanguage === 'en' ? 'Log out' : appLanguage === 'uz' ? 'Hisobdan chiqish' : 'Выйти из аккаунта'}
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
            <p>{appLanguage === 'en' ? 'AI photo spread analysis' : appLanguage === 'uz' ? "AI foto yoyilma tahlili" : 'AI анализ расклада по фото'}</p>

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
                  <h2 className="photo-flow-title">
                    {photoStep === 'error'
                      ? appLanguage === 'en'
                        ? 'Cards not recognized'
                        : appLanguage === 'uz'
                          ? 'Kartalar aniqlanmadi'
                          : 'Карты не распознаны'
                      : appLanguage === 'en'
                        ? 'Photo spread analysis'
                        : appLanguage === 'uz'
                          ? 'Foto yoyilma tahlili'
                          : 'Анализ расклада по фото'}
                  </h2>
                  <p className="photo-flow-text">
                    {photoStep === 'error'
                      ? appLanguage === 'en'
                        ? 'Try taking the spread photo again or choose another photo.'
                        : appLanguage === 'uz'
                          ? "Yoyilmani qayta suratga oling yoki boshqa rasm tanlang."
                          : 'Попробуйте сфотографировать расклад снова или выбрать другое фото.'
                      : appLanguage === 'en'
                        ? 'Lay out the cards and shoot the spread from above in good light.'
                        : appLanguage === 'uz'
                          ? "Kartalarni yoyib, yaxshi yorug'likda yuqoridan suratga oling."
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
                      <span className="photo-upload-block__title">
                        {photoStep === 'error'
                          ? appLanguage === 'en'
                            ? 'Take photo again'
                            : appLanguage === 'uz'
                              ? 'Qayta suratga olish'
                              : 'Сделать фото снова'
                          : appLanguage === 'en'
                            ? 'Take a photo'
                            : appLanguage === 'uz'
                              ? 'Suratga olish'
                              : 'Сделать фото'}
                      </span>
                      <span className="photo-upload-block__sub">
                        {appLanguage === 'en' ? 'or choose from gallery' : appLanguage === 'uz' ? 'yoki galereyadan tanlang' : 'или выбрать из галереи'}
                      </span>
                    </span>
                    <span className="photo-upload-block__chevron" aria-hidden="true">›</span>
                  </button>

                  <div className="photo-tip-row" aria-hidden="true">
                    <span className="photo-tip-pill">{appLanguage === 'en' ? 'From above' : appLanguage === 'uz' ? 'Yuqoridan' : 'Сверху'}</span>
                    <span className="photo-tip-pill">{appLanguage === 'en' ? 'No glare' : appLanguage === 'uz' ? "Yaltiroqsiz" : 'Без бликов'}</span>
                    <span className="photo-tip-pill">{appLanguage === 'en' ? 'Full spread' : appLanguage === 'uz' ? "To'liq yoyilma" : 'Весь расклад'}</span>
                  </div>

                  {photoStep === 'error' && photoFile && (
                    <button type="button" className="photo-ghost-btn" onClick={retryPhotoDetection} disabled={photoBusy}>
                      {appLanguage === 'en' ? 'Retry with this photo' : appLanguage === 'uz' ? 'Shu rasm bilan qayta urinish' : 'Повторить с этим фото'}
                    </button>
                  )}

                  {photoPreviewUrl && (
                    <div className="photo-preview-frame photo-preview-frame--soft">
                      <img src={photoPreviewUrl} alt={appLanguage === 'en' ? 'Spread photo' : appLanguage === 'uz' ? 'Yoyilma rasmi' : 'Фото расклада'} />
                    </div>
                  )}

                  {photoError ? <div className="photo-stage-error">{photoError}</div> : null}
                </section>
              )}

              {photoStep === 'analyzing' && (
                <section className="photo-glass-card">
                  <h2 className="photo-flow-title">{appLanguage === 'en' ? 'Analyzing spread' : appLanguage === 'uz' ? 'Yoyilma tahlil qilinmoqda' : 'Анализируем расклад'}</h2>
                  <div className="photo-preview-frame photo-preview-frame--scanning">
                    {photoPreviewUrl ? (
                      <img src={photoPreviewUrl} alt={appLanguage === 'en' ? 'Spread photo' : appLanguage === 'uz' ? 'Yoyilma rasmi' : 'Фото расклада'} />
                    ) : (
                      <div className="photo-preview-frame__empty">{appLanguage === 'en' ? 'Preparing photo…' : appLanguage === 'uz' ? 'Rasm tayyorlanmoqda…' : 'Подготовка фото…'}</div>
                    )}
                  </div>
                  <div className="photo-stage-status">{appLanguage === 'en' ? 'Recognizing cards…' : appLanguage === 'uz' ? 'Kartalar aniqlanmoqda…' : 'Распознаем карты…'}</div>
                </section>
              )}

              {photoStep === 'detected' && (
                <section className="photo-glass-card">
                  <h2 className="photo-flow-title">{appLanguage === 'en' ? 'Cards found' : appLanguage === 'uz' ? 'Kartalar topildi' : 'Карты найдены'}</h2>
                  <div className="photo-cards-stage">
                    {renderPhotoCardsFan(photoDetectedCards, false)}
                  </div>
                  {photoCardsLabel ? <div className="photo-cards-label">{photoCardsLabel}</div> : null}

                  <div className="photo-field">
                    <label className="photo-field__label" htmlFor="photo-main-question">
                      {appLanguage === 'en' ? 'Your question' : appLanguage === 'uz' ? 'Savolingiz' : 'Ваш вопрос'}
                    </label>
                    <div className="ask-wrap photo-question-ask">
                      <div className="ask-glass">
                        <textarea
                          id="photo-main-question"
                          className="ask-input photo-question-ask__input"
                          value={photoMainQuestion}
                          onChange={(e) => setPhotoMainQuestion(e.target.value)}
                          placeholder={
                            appLanguage === 'en'
                              ? 'What is important for me to understand?'
                              : appLanguage === 'uz'
                                ? "Men uchun nimani tushunish muhim?"
                                : 'Что мне важно понять?'
                          }
                          rows={2}
                          enterKeyHint="send"
                        />
                        <button
                          type="button"
                          className={`ask-mic ${isPhotoMainRecording ? 'recording' : ''}`}
                          onClick={() => void toggleRecording('photo_main')}
                          aria-label={
                            isPhotoMainRecording
                              ? appLanguage === 'en'
                                ? 'Stop recording'
                                : appLanguage === 'uz'
                                  ? "Yozuvni to'xtatish"
                                  : 'Остановить запись'
                              : appLanguage === 'en'
                                ? 'Start recording'
                                : appLanguage === 'uz'
                                  ? 'Yozishni boshlash'
                                  : 'Начать запись'
                          }
                          title={
                            isPhotoMainRecording
                              ? appLanguage === 'en'
                                ? 'Stop recording'
                                : appLanguage === 'uz'
                                  ? "Yozuvni to'xtatish"
                                  : 'Остановить запись'
                              : appLanguage === 'en'
                                ? 'Record voice'
                                : appLanguage === 'uz'
                                  ? 'Ovoz yozish'
                                  : 'Записать голосом'
                          }
                        >
                          <img className="ask-mic__icon" src={micIcon} alt="" aria-hidden="true" />
                        </button>
                      </div>
                    </div>
                    <div className={`ask-hint ${isPhotoMainRecording ? 'is-visible' : ''}`}>
                      {appLanguage === 'en'
                        ? 'Recording… tap again to stop'
                        : appLanguage === 'uz'
                          ? "Yozilmoqda… to'xtatish uchun yana bosing"
                          : 'Идёт запись… нажмите ещё раз, чтобы остановить'}
                    </div>
                  </div>

                  <button
                    type="button"
                    className={`glass-cta photo-main-cta ${photoBusy ? 'is-loading' : ''}`}
                    onClick={runPhotoInterpretation}
                    disabled={photoBusy}
                  >
                    <span className="glass-cta__inner">
                      <span className="glass-cta__rim" aria-hidden="true" />
                      <span className="glass-cta__text">
                        {photoBusy
                          ? appLanguage === 'en'
                            ? 'Preparing answer…'
                            : appLanguage === 'uz'
                              ? 'Javob tayyorlanmoqda…'
                              : 'Готовим ответ…'
                          : appLanguage === 'en'
                            ? 'Get answer'
                            : appLanguage === 'uz'
                              ? 'Javob olish'
                              : 'Получить ответ'}
                      </span>
                      <span className="glass-cta__spark" aria-hidden="true" />
                    </span>
                  </button>

                  <button type="button" className="photo-ghost-btn" onClick={openPhotoActionSheet} disabled={photoBusy}>
                    {appLanguage === 'en' ? 'Choose another photo' : appLanguage === 'uz' ? 'Boshqa rasm tanlash' : 'Выбрать другое фото'}
                  </button>
                  {photoError ? <div className="photo-stage-error">{photoError}</div> : null}
                </section>
              )}

              {photoStep === 'result' && (
                <section className="photo-glass-card photo-glass-card--result">
                  <h2 className="photo-flow-title">{appLanguage === 'en' ? 'Your spread' : appLanguage === 'uz' ? 'Sizning yoyilmangiz' : 'Ваш расклад'}</h2>
                  <div className="photo-cards-stage is-compact">
                    {renderPhotoCardsFan(photoDetectedCards, true)}
                  </div>

                  <div className="photo-reading-card">
                    <div className="photo-reading-card__title">{appLanguage === 'en' ? 'Interpretation' : appLanguage === 'uz' ? 'Talqin' : 'Интерпретация'}</div>
                    {renderSafetyNotice(photoMainQuestion)}
                    {photoInterpretation ? (
                      <MarkdownText text={photoInterpretation} />
                    ) : (
                      <p style={{ margin: 0, opacity: 0.8 }}>
                        {appLanguage === 'en'
                          ? 'Could not get interpretation. Please try again.'
                          : appLanguage === 'uz'
                            ? "Talqinni olib bo'lmadi. Qayta urinib ko'ring."
                            : 'Не удалось получить интерпретацию. Попробуйте ещё раз.'}
                      </p>
                    )}
                  </div>

                  <div className="photo-followup-card">
                    <div className="photo-reading-card__title">
                      {appLanguage === 'en' ? 'Ask a follow-up' : appLanguage === 'uz' ? "Yoyilma bo'yicha aniqlashtirish" : 'Уточнить по раскладу'}
                    </div>

                    {!photoFollowupUsed && (
                      <>
                        <textarea
                          className="photo-field__input photo-field__input--sm"
                          value={photoFollowupQuestion}
                          onChange={(e) => setPhotoFollowupQuestion(e.target.value)}
                          placeholder={
                            appLanguage === 'en'
                              ? 'For example: what does this mean for relationships?'
                              : appLanguage === 'uz'
                                ? "Masalan: bu munosabatlar uchun nimani anglatadi?"
                                : 'Например: что это значит для отношений?'
                          }
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
                            <span className="glass-cta__text">
                              {photoBusy
                                ? appLanguage === 'en'
                                  ? 'Preparing answer…'
                                  : appLanguage === 'uz'
                                    ? 'Javob tayyorlanmoqda…'
                                    : 'Готовим ответ…'
                                : appLanguage === 'en'
                                  ? 'Ask question'
                                  : appLanguage === 'uz'
                                    ? 'Savol berish'
                                    : 'Задать вопрос'}
                            </span>
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
                        {appLanguage === 'en'
                          ? 'Follow-up question already used. Start a new spread for a new analysis.'
                          : appLanguage === 'uz'
                            ? "Qo'shimcha savoldan foydalanib bo'lindi. Yangi tahlil uchun yangi yoyilma boshlang."
                            : 'Дополнительный вопрос уже использован. Чтобы получить новый разбор, начните новый расклад.'}
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
                      <span className="glass-cta__text">{appLanguage === 'en' ? 'New spread' : appLanguage === 'uz' ? 'Yangi yoyilma' : 'Новый расклад'}</span>
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
            <p ref={subtitleRef}>{t('appSubtitle')}</p>
            {cardDayLoading && (
              <CardDayMysticLoader caption={cardDayLoadingSteps[cardDayLoaderStep]} />
            )}
            {/* активный spread-card */}
            {!isResult && !cardDayLoading && (
              <div className="spread-list">
                <div ref={spreadActiveRef} className="spread-card spread-card--sun is-active" role="button" tabIndex={0}>
                  <div className="spread-icon__svg" aria-hidden="true">
                    <img src={cardDayIcon} alt="" />
                  </div>

                  <div className="spread-body">
                    <div className="spread-title">{t('cardOfDay')}</div>
                    <div className="spread-subtitle">{t('dailyGuideLine1')}</div>
                    <div className="spread-meta">{spreads.find((s) => s.id === 'card_of_day')?.cards || '1'}</div>
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
                  <div className="cardday-shake-overlay__title">{lx('Встряхните телефон', 'Shake your phone', "Telefonni silkitib ko'ring")}</div>
                  <div className="cardday-shake-overlay__sub">
                    {lx(
                      'Или нажмите кнопку ниже для авто‑перемешивания',
                      'Or use the button below for auto shuffle',
                      "Yoki pastdagi tugma bilan avtomatik aralashtiring",
                    )}
                  </div>
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
                          {lx('Разрешить', 'Allow motion', "Silkitishga ruxsat berish")}
                        </button>
                      )}

                      <button type="button" className="glass-cta mini-cta" onClick={autoShuffle}>
                        <span className="glass-cta__inner">
                          <span className="glass-cta__rim" aria-hidden="true" />
                          <span className="glass-cta__text">{lx('Перемешать автоматически', 'Shuffle automatically', 'Avtomatik aralashtirish')}</span>
                          <span className="glass-cta__spark" aria-hidden="true" />
                        </span>
                      </button>
                    </>
                  ) : (
                    <div className="cardday-inline-loader-wrap">
                      <CardDayMysticLoader
                        compact
                        caption={lx(
                          'Загружаем интерпретацию карты дня…',
                          'Loading card interpretation…',
                          "Kun kartasi talqini yuklanmoqda…",
                        )}
                      />
                    </div>
                  )}
                </div>
              </>
            )}

            {isResult && (
              <div className="result-layout">
                <div className="result-layout__desc">
                  <div className="result-card">
                    <div className="result-card__title">{lx('Значение карты', 'Card meaning', 'Karta maʼnosi')}</div>
                    <div className="result-card__name">
                      {dailyCardName
                        ? dailyCardName
                        : (selectedFrontUrl.split('/').pop() || lx('Карта', 'Card', 'Karta')).replace(/\.(png|jpg|jpeg|webp)$/i, '')}
                    </div>

                    <div className="result-card__scroll">
                      {dailyDayKey ? (
                        <p style={{ opacity: 0.72, marginTop: 0 }}>{lx('Карта дня', 'Card of the day', 'Kun kartasi')}: {dailyDayKey}</p>
                      ) : null}

                      {renderSafetyNotice(dailyQuestion)}

                      {dailyDesc ? (
                        <MarkdownText text={stripDailyQuestionContextSection(dailyDesc, dailyQuestion)} />
                      ) : (
                        <p>{lx('Описание пока недоступно.', 'Description is not available yet.', "Izoh hozircha mavjud emas.")}</p>
                      )}
                    </div>

                    <div className="result-card__scroll">
                    </div>
                  </div>
                </div>

                <button type="button" className="glass-cta result-back" onClick={backHome}>
                  <span className="glass-cta__inner">
                    <span className="glass-cta__rim" aria-hidden="true" />
                    <span className="glass-cta__text">{lx('Вернуться в меню', 'Back to menu', 'Menyuga qaytish')}</span>
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
            <p>{lx('Расклад по 3 картам', '3-card spread', '3 kartalik yoyilma')}</p>

            <div className="threepage">

              {/* 1) SETUP */}
              {threeScreen === 'setup' && (
                <>
                  <div className="threecards-row" aria-label={lx('Три карты (рубашка)', 'Three cards (back side)', 'Uchta karta (orqa tomoni)')}>
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
                          placeholder={appLanguage === 'en' ? 'Your question…' : appLanguage === 'uz' ? 'Savolingiz…' : 'Ваш вопрос…'}
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
                      aria-label={lx('Тип вопроса', 'Question type', 'Savol turi')}
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
                        <span className="glass-cta__text">{t('continue')}</span>
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
                    aria-label={lx('Перемешивание', 'Shuffling', 'Aralashtirish')}
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
                      <div className="cardday-shake-overlay__title">{lx('Встряхните телефон', 'Shake your phone', "Telefonni silkitib ko'ring")}</div>
                      <div className="cardday-shake-overlay__sub">
                        {lx(
                          'Или нажмите кнопку ниже для авто‑перемешивания',
                          'Or use the button below for auto shuffle',
                          "Yoki pastdagi tugma bilan avtomatik aralashtiring",
                        )}
                      </div>
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
                            {lx('Разрешить встряхивание', 'Allow motion', "Silkitishga ruxsat berish")}
                          </button>
                        )}

                        <div className="threehint">{lx('Прогресс', 'Progress', 'Jarayon')}: {Math.round(threeShuffleProgress * 100)}%</div>

                        <button type="button" className="glass-cta mini-cta" onClick={autoShuffleThree}>
                          <span className="glass-cta__inner">
                            <span className="glass-cta__rim" aria-hidden="true" />
                            <span className="glass-cta__text">{lx('Перемешать автоматически', 'Shuffle automatically', 'Avtomatik aralashtirish')}</span>
                            <span className="glass-cta__spark" aria-hidden="true" />
                          </span>
                        </button>
                      </>
                    ) : (
                      <div className="threehint">{lx('Открываем карты…', 'Opening cards…', 'Kartalar ochilmoqda…')}</div>
                    )}
                  </div>
                </>
              )}

              {/* 3) RESULT */}
              {threeScreen === 'result' && (
                <>
                  <div className="threecards-row" aria-label={lx('Три карты (результат)', 'Three cards (result)', 'Uchta karta (natija)')}>
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
                        <InterpretationLoader text={lx('Получаем интерпретацию', 'Preparing interpretation', 'Talqin tayyorlanmoqda')} />
                      </div>
                    ) : (
                      <div className="result-layout__desc">
                        <div className="result-card">
                          <div className="result-card__title">{lx('Значение расклада', 'Spread meaning', 'Yoyilma maʼnosi')}</div>
                          <div className="result-card__name">{lx('Расклад по 3 картам', '3-card spread', '3 kartalik yoyilma')}</div>

                          <div className="result-card__scroll">
                            {threeDayKey ? (
                              <p style={{ opacity: 0.72, marginTop: 0 }}>{lx('Дата расклада', 'Spread date', 'Yoyilma sanasi')}: {threeDayKey}</p>
                            ) : null}

                            {threeQuestion ? (
                              <p style={{ opacity: 0.85, marginTop: 10, marginBottom: 0 }}>
                                <b>{lx('Вопрос', 'Question', 'Savol')}:</b> {threeQuestion}
                              </p>
                            ) : (
                              <p style={{ opacity: 0.72, marginTop: 10, marginBottom: 0 }}>
                                {lx(
                                  'Вопрос не задан. Показана общая интерпретация по картам.',
                                  'No question provided. Showing a general interpretation by cards.',
                                  "Savol berilmagan. Kartalar bo'yicha umumiy talqin ko'rsatilmoqda.",
                                )}
                              </p>
                            )}

                            <p style={{ opacity: 0.82, marginTop: 10, marginBottom: 0 }}>
                              <b>{lx('Тип', 'Type', 'Turi')}:</b>{' '}
                              {threeKind === 'yesno'
                                ? lx('Да / Нет', 'Yes / No', "Ha / Yo'q")
                                : threeKind === 'advice'
                                  ? lx('Совет', 'Advice', 'Maslahat')
                                  : lx('Открытый вопрос', 'Open question', 'Ochiq savol')}
                            </p>

                            <div style={{ height: 10 }} />

                            {!threeShowMeaning ? (
                              <p style={{ opacity: 0.8, marginTop: 10 }}>{lx('Карты раскрываются…', 'Cards are revealing…', 'Kartalar ochilmoqda…')}</p>
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
                        <span className="glass-cta__text">{lx('Новый расклад', 'New spread', 'Yangi yoyilma')}</span>
                        <span className="glass-cta__spark" aria-hidden="true" />
                      </span>
                    </button>

                    <button type="button" className="glass-cta result-back" onClick={backHome}>
                      <span className="glass-cta__inner">
                        <span className="glass-cta__rim" aria-hidden="true" />
                        <span className="glass-cta__text">{lx('Вернуться в меню', 'Back to menu', 'Menyuga qaytish')}</span>
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
            <p>{lx('Прошлое • Настоящее • Будущее', 'Past • Present • Future', "O'tmish • Hozir • Kelajak")}</p>

            <div className="threepage">
              {/* 1) SETUP */}
              {ppfScreen === 'setup' && (
                <>
                  <div className="threecards-row" aria-label={lx('Три карты (рубашка)', 'Three cards (back side)', 'Uchta karta (orqa tomoni)')}>
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
                          placeholder={appLanguage === 'en' ? 'Your question…' : appLanguage === 'uz' ? 'Savolingiz…' : 'Ваш вопрос…'}
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
                      aria-label={lx('Фокус', 'Focus', 'Fokus')}
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
                        <span className="glass-cta__text">{t('continue')}</span>
                        <span className="glass-cta__spark" aria-hidden="true" />
                      </span>
                    </button>
                  </div>
                </>
              )}



              {/* 2) SHUFFLE */}
              {ppfScreen === 'shuffle' && (
                <>
                  <div className="ppf-drag-board" aria-label={lx('Расклад с перетаскиванием карт', 'Drag-and-drop spread', "Kartalarni sudrab joylash yoyilmasi")}>
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
                          <div className="shake__title">{lx('Вытащите карты из колоды', 'Pull cards from the deck', "Kartalarni kolodadan oling")}</div>
                          <div className="shake__sub">
                            {lx(
                              'Зажмите карту снизу и перетащите в подсвеченный пунктирный слот.',
                              'Hold a bottom card and drag it into the highlighted slot.',
                              "Pastdagi kartani bosib ushlab, yoritilgan joyga sudrab o'tkazing.",
                            )}
                          </div>
                        </div>

                        <div className="threehint">{lx('Выложено', 'Placed', 'Joylashtirildi')}: {ppfPlacedCount} / 3</div>

                        <button type="button" className="glass-cta mini-cta" onClick={autoShufflePpf}>
                          <span className="glass-cta__inner">
                            <span className="glass-cta__rim" aria-hidden="true" />
                            <span className="glass-cta__text">{lx('Разложить автоматически', 'Auto place cards', 'Avtomatik joylashtirish')}</span>
                            <span className="glass-cta__spark" aria-hidden="true" />
                          </span>
                        </button>
                      </>
                    ) : (
                      <div className="shake__badge is-done">
                        <div className="shake__title">{lx('Открываем карты…', 'Opening cards…', 'Kartalar ochilmoqda…')}</div>
                        <div className="shake__sub">{lx('Сейчас покажем 3 карты и интерпретацию.', 'Now showing 3 cards and interpretation.', "Hozir 3 ta karta va talqin ko'rsatiladi.")}</div>
                      </div>
                    )}
                  </div>
                </>
              )}

              {/* 3) RESULT */}
              {ppfScreen === 'result' && (
                <>
                  <div className="threecards-row" aria-label={lx('Три карты (результат)', 'Three cards (result)', 'Uchta karta (natija)')}>
                    {(ppfCards.length ? ppfCards : []).map((c, i) => (
                      <div key={`${c.idx}-${i}`} className="threecard">
                        <img className={c.isReversed ? 'is-reversed' : ''} src={c.url || backCardImg} alt={c.name} />
                      </div>
                    ))}
                  </div>

                  <div className="three-result">
                    {ppfLoading ? (
                      <div className="result-loading-standalone">
                        <InterpretationLoader text={lx('Получаем интерпретацию', 'Preparing interpretation', 'Talqin tayyorlanmoqda')} />
                      </div>
                    ) : (
                      <div className="result-layout__desc">
                        <div className="result-card">
                          <div className="result-card__title">{lx('Значение расклада', 'Spread meaning', 'Yoyilma maʼnosi')}</div>

                          <div className="result-card__scroll">
                            {ppfDayKey ? <p style={{ opacity: 0.72, marginTop: 0 }}>{lx('Дата расклада', 'Spread date', 'Yoyilma sanasi')}: {ppfDayKey}</p> : null}

                            {!!ppfQuestion && (
                              <p style={{ opacity: 0.86, marginTop: 10 }}>
                                <b>{lx('Вопрос', 'Question', 'Savol')}:</b> {ppfQuestion}
                              </p>
                            )}
                            {!ppfQuestion && (
                              <p style={{ opacity: 0.72, marginTop: 10, marginBottom: 0 }}>
                                {lx(
                                  'Вопрос не задан. Показана общая интерпретация по картам.',
                                  'No question provided. Showing a general interpretation by cards.',
                                  "Savol berilmagan. Kartalar bo'yicha umumiy talqin ko'rsatilmoqda.",
                                )}
                              </p>
                            )}

                            <p style={{ opacity: 0.86, marginTop: 10 }}>
                              <b>{lx('Фокус', 'Focus', 'Fokus')}:</b> {PPF_FOCUS.find((x) => x.id === ppfFocus)?.label || '—'}
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
                        <span className="glass-cta__text">{lx('Новый расклад', 'New spread', 'Yangi yoyilma')}</span>
                        <span className="glass-cta__spark" aria-hidden="true" />
                      </span>
                    </button>

                    <button type="button" className="glass-cta result-back" onClick={backHome}>
                      <span className="glass-cta__inner">
                        <span className="glass-cta__rim" aria-hidden="true" />
                        <span className="glass-cta__text">{lx('Вернуться в меню', 'Back to menu', 'Menyuga qaytish')}</span>
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
            <p>{lx('Принятие решения', 'Decision spread', 'Qaror yoyilmasi')}</p>

            <div className="threepage">
              {decisionScreen === 'shuffle' && (
                <>
                  <div className="decision-drag-board" aria-label={lx('Расклад по вариантам A и B', 'Spread by options A and B', "A va B variantlari bo'yicha yoyilma")}>
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
                          <div className="threehint">{lx('Выложено', 'Placed', 'Joylashtirildi')}: {decisionPlacedCount} / 2</div>
                        ) : (
                          <div className="decision-flip-caption">{lx('Нажмите на карту, чтобы её перевернуть', 'Tap a card to flip it', "Kartani ochish uchun ustiga bosing")}</div>
                        )}

                        {decisionPlacedCount < 2 ? (
                          <button type="button" className="glass-cta mini-cta" onClick={autoShuffleDecision}>
                            <span className="glass-cta__inner">
                              <span className="glass-cta__rim" aria-hidden="true" />
                              <span className="glass-cta__text">{lx('Разложить автоматически', 'Auto place cards', 'Avtomatik joylashtirish')}</span>
                              <span className="glass-cta__spark" aria-hidden="true" />
                            </span>
                          </button>
                        ) : null}
                      </>
                    ) : (
                      <div className="threehint">{lx('Открываем интерпретацию…', 'Opening interpretation…', 'Talqin ochilmoqda…')}</div>
                    )}
                  </div>
                </>
              )}

              {decisionScreen === 'result' && (
                <>
                  <div className="decision-result-row" aria-label={lx('Две карты (результат)', 'Two cards (result)', 'Ikki karta (natija)')}>
                    {(decisionCards.length ? decisionCards : []).map((c, i) => (
                      <div key={`${c.idx}-${i}`} className="threecard">
                        <img className={c.isReversed ? 'is-reversed' : ''} src={c.url || backCardImg} alt={c.name} />
                      </div>
                    ))}
                  </div>

                  <div className="three-result">
                    {decisionLoading ? (
                      <div className="result-loading-standalone">
                        <InterpretationLoader text={lx('Получаем интерпретацию', 'Preparing interpretation', 'Talqin tayyorlanmoqda')} />
                      </div>
                    ) : (
                      <div className="result-layout__desc">
                        <div className="result-card">
                          <div className="result-card__title">{lx('Значение расклада', 'Spread meaning', 'Yoyilma maʼnosi')}</div>

                          <div className="result-card__scroll">
                            {decisionDayKey ? <p style={{ opacity: 0.72, marginTop: 0 }}>{lx('Расклад', 'Spread', 'Yoyilma')}: {decisionDayKey}</p> : null}

                            {decisionQuestion ? (
                              <p style={{ marginTop: 10, opacity: 0.86 }}>
                                <b>{lx('Вопрос', 'Question', 'Savol')}:</b> {decisionQuestion}
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
                        <span className="glass-cta__text">{lx('Новый расклад', 'New spread', 'Yangi yoyilma')}</span>
                        <span className="glass-cta__spark" aria-hidden="true" />
                      </span>
                    </button>

                    <button type="button" className="glass-cta result-back" onClick={backHome}>
                      <span className="glass-cta__inner">
                        <span className="glass-cta__rim" aria-hidden="true" />
                        <span className="glass-cta__text">{lx('Вернуться в меню', 'Back to menu', 'Menyuga qaytish')}</span>
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
              <span className="glass-cta__text">{t('startReading')}</span>
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
                aria-label={lx('Закрыть документ', 'Close document', 'Hujjatni yopish')}
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
                {lx('Открыть PDF', 'Open PDF', 'PDF ochish')}
              </a>
              <button
                type="button"
                className="legal-doc-card__action legal-doc-card__action--ghost"
                onClick={() => openTelegramAndCloseMiniApp(LEGAL_DOC_BOT_DEEPLINK[activeLegalDoc || 'terms'])}
              >
                {lx('Открыть в боте', 'Open in bot', 'Botda ochish')}
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
          aria-label={t('personalization')}
          onClick={() => {
            if (!prefsSaving) setShowPersonalizationModal(false)
          }}
        >
          <div className="prefs-modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="prefs-modal-card__head">
              <div className="prefs-modal-card__title">{t('personalization')}</div>
              <button
                type="button"
                className="prefs-modal-card__close"
                onClick={() => setShowPersonalizationModal(false)}
                aria-label={t('close')}
              >
                ×
              </button>
            </div>

            <p className="prefs-modal-card__text">
              {lx(
                'Используем вашу историю раскладов для более точной интерпретации. Отдельную «вторую историю» не создаём.',
                'We use your reading history for more accurate interpretations. No separate “second history” is created.',
                "Aniqroq talqin uchun yoyilmalar tarixingizdan foydalanamiz. Alohida “ikkinchi tarix” yaratilmaydi.",
              )}
            </p>

            <div className="prefs-modal-card__row">
              <div className="prefs-modal-card__row-text">
                <div className="prefs-modal-card__row-title">{lx('Память раскладов (90 дней)', 'Reading memory (90 days)', "Yoyilma xotirasi (90 kun)")}</div>
                <div className="prefs-modal-card__row-sub">
                  {lx(
                    'Учитываем повторяющиеся темы и динамику по похожим вопросам.',
                    'Tracks recurring topics and dynamics across similar questions.',
                    "O'xshash savollar bo'yicha takrorlanadigan mavzu va dinamikani hisobga oladi.",
                  )}
                </div>
              </div>
              <label className="prefs-switch" aria-label={lx('Память раскладов', 'Reading memory', "Yoyilma xotirasi")}>
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
              {lx(
                'Для полного удаления персональных данных используйте команду',
                'To fully delete personal data, use command',
                "Shaxsiy ma'lumotlarni to'liq o'chirish uchun",
              )}{' '}
              <b>/forgetme</b>{' '}
              {lx('в боте.', 'in the bot.', 'buyrug‘idan botda foydalaning.')}
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
                <span className="glass-cta__text">
                  {prefsSaving ? lx('Сохраняем…', 'Saving…', 'Saqlanmoqda…') : lx('Сохранить', 'Save', 'Saqlash')}
                </span>
                <span className="glass-cta__spark" aria-hidden="true" />
              </span>
            </button>
          </div>
        </div>
      )}

      {showLanguageModal && (
        <div
          className="prefs-modal-overlay"
          role="dialog"
          aria-modal="true"
          aria-label={t('appLanguage')}
          onClick={() => {
            if (!languageSaving) setShowLanguageModal(false)
          }}
        >
          <div className="prefs-modal-card prefs-lang-card" onClick={(e) => e.stopPropagation()}>
            <div className="prefs-modal-card__head">
              <div className="prefs-modal-card__title">{t('appLanguage')}</div>
              <button
                type="button"
                className="prefs-modal-card__close"
                onClick={() => setShowLanguageModal(false)}
                aria-label={appLanguage === 'en' ? 'Close' : appLanguage === 'uz' ? 'Yopish' : 'Закрыть'}
              >
                ×
              </button>
            </div>

            <div className="prefs-lang-list">
              {(() => {
                const autoActive = !isLanguageManual
                const runtimeLang = detectRuntimeAppLanguage()
                return (
                  <button
                    type="button"
                    className={`prefs-lang-option ${autoActive ? 'is-active' : ''}`}
                    disabled={languageSaving}
                    onClick={() => {
                      if (autoActive) {
                        setShowLanguageModal(false)
                        return
                      }
                      void saveAutoLanguage()
                    }}
                  >
                    <span className="prefs-lang-option__body">
                      <span className="prefs-lang-option__title">{languageAutoLabel(appLanguage)}</span>
                      <span className="prefs-lang-option__meta">{languageCurrentLabel(appLanguage, runtimeLang)}</span>
                    </span>
                    {autoActive ? <span className="prefs-lang-option__mark">✓</span> : null}
                  </button>
                )
              })()}
              {(['ru', 'uz', 'en'] as AppLanguage[]).map((langCode) => {
                const active = isLanguageManual && appLanguage === langCode
                return (
                  <button
                    key={langCode}
                    type="button"
                    className={`prefs-lang-option ${active ? 'is-active' : ''}`}
                    disabled={languageSaving}
                    onClick={() => {
                      if (active) {
                        setShowLanguageModal(false)
                        return
                      }
                      void saveAppLanguage(langCode)
                    }}
                  >
                    <span>{languageDisplayName(langCode, appLanguage)}</span>
                    {active ? <span className="prefs-lang-option__mark">✓</span> : null}
                  </button>
                )
              })}
            </div>

            {prefsError ? <div className="prefs-modal-card__error">{prefsError}</div> : null}
          </div>
        </div>
      )}

      {inAppBillingEnabled && showBillingFlow && (
        <div className="billing-flow-overlay" role="dialog" aria-modal="true" aria-label={lx('Оплата подписки', 'Subscription payment', "Obuna to'lovi")}>
          <div className="billing-flow-card" data-source={billingFlowSource}>
            <div className="billing-flow-card__head">
              <div className="billing-flow-card__step">{lx('Шаг', 'Step', 'Qadam')} {billingStepNumber} {lx('из', 'of', 'dan')} 3</div>
              <button type="button" className="billing-flow-card__close" onClick={closeBillingFlow} aria-label={t('close')}>
                ×
              </button>
            </div>

            {billingFlowStep === 'country' && (
              <>
                <div className="billing-flow-card__title">{lx('Из какой вы страны?', 'What country are you from?', "Siz qaysi mamlakatdansiz?")}</div>
                <div className="billing-flow-card__text">
                  {lx('Покажем только подходящие способы оплаты.', 'We will show only suitable payment methods.', "Faqat mos to'lov usullari ko'rsatiladi.")}
                </div>
                <div className="billing-flow-list">
                  {billingCountries.map((country) => (
                    <button
                      key={country.code}
                      type="button"
                      className="billing-flow-option"
                        onClick={() => {
                          setBillingFlowCountry(country.code)
                          setBillingFlowPlan(null)
                          setShowBillingPaymentHelp(false)
                          setBillingFlowStep('plan')
                        }}
                    >
                      <span className="billing-flow-option__title">{country.label}</span>
                      <span className="billing-flow-option__meta">{country.methodsHint}</span>
                    </button>
                  ))}
                </div>
              </>
            )}

            {billingFlowStep === 'plan' && (
              <>
                <div className="billing-flow-card__title">{lx('Выберите тариф', 'Select a plan', 'Tarifni tanlang')}</div>
                <div className="billing-flow-card__text">{lx('Страна', 'Country', 'Mamlakat')}: {billingCountryLabel}</div>
                <div className="billing-flow-list">
                  {billingPlans.map((planKey) => {
                    const plan = billingPlanMeta[planKey]
                    const planPriceLabel = getPlanPriceLabel(billingFlowCountry, planKey)
                    return (
                      <button
                        key={planKey}
                        type="button"
                        className="billing-flow-option billing-flow-option--plan"
                        onClick={() => {
                          setBillingFlowPlan(planKey)
                          setBillingFlowStep('method')
                          setShowBillingPaymentHelp(false)
                          setSbpStatusText('')
                          setClickStatusText('')
                        }}
                      >
                        <span className="billing-flow-option__line">
                          <span className="billing-flow-option__title">{plan.title}</span>
                          <span className="billing-flow-option__price">{planPriceLabel || '—'}</span>
                        </span>
                        <span className="billing-flow-option__meta">{plan.caption}</span>
                      </button>
                    )
                  })}
                </div>
              </>
            )}

            {billingFlowStep === 'method' && billingFlowPlan && (
              <>
                <div className="billing-flow-card__title">{lx('Выберите способ оплаты', 'Select payment method', "To'lov usulini tanlang")}</div>
                <div className="billing-flow-card__text">
                  {billingSelectedPlan?.title || lx('Подписка', 'Subscription', 'Obuna')} • {billingCountryLabel}
                </div>
                <div className="billing-flow-list">
                  {hasSbpMethod && (
                    <button
                      type="button"
                      className="billing-flow-option billing-flow-option--sbp"
                      disabled={sbpBusyPlan === mapPlanToSbpPlan(billingFlowPlan)}
                      onClick={() => {
                        const sbpPlan = mapPlanToSbpPlan(billingFlowPlan)
                        if (!sbpPlan) return
                        void startSbpPayment(sbpPlan)
                      }}
                    >
                      <span className="billing-flow-option__title">
                        {sbpBusyPlan === mapPlanToSbpPlan(billingFlowPlan)
                          ? lx('Создаю счёт СБП…', 'Creating SBP invoice…', 'SBP hisobi yaratilmoqda…')
                          : lx('СБП (внутри приложения)', 'SBP (inside app)', 'SBP (ilova ichida)')}
                      </span>
                      <span className="billing-flow-option__meta">{lx('Оплатите и вернитесь в mini app', 'Pay and return to mini app', "To'lab, mini appga qayting")}</span>
                    </button>
                  )}

                  {hasClickMethod && (
                    <button
                      type="button"
                      className="billing-flow-option billing-flow-option--click"
                      disabled={clickBusyPlan === billingFlowPlan}
                      onClick={() => {
                        void startClickPayment(billingFlowPlan)
                      }}
                    >
                      <span className="billing-flow-option__title">
                        {clickBusyPlan === billingFlowPlan
                          ? lx('Создаю счёт CLICK…', 'Creating CLICK invoice…', 'CLICK hisobi yaratilmoqda…')
                          : lx('CLICK / карта через CLICK', 'CLICK / card via CLICK', 'CLICK / karta orqali CLICK')}
                      </span>
                      <span className="billing-flow-option__meta">{lx('Откроется страница оплаты CLICK', 'CLICK payment page will open', "CLICK to'lov sahifasi ochiladi")}</span>
                    </button>
                  )}
                </div>
              </>
            )}

            {showBillingStatusArea && (
              <div className="billing-flow-status">
                {hasPendingPayment && (
                  <button
                    type="button"
                    className="billing-flow-status__link billing-flow-status__link--center"
                    onClick={() => setShowBillingPaymentHelp((prev) => !prev)}
                  >
                    {showBillingPaymentHelp
                      ? lx('Скрыть', 'Hide', 'Yopish')
                      : lx('Оплатили, но ошибка?', 'Paid but got an error?', "To'lov qilindi, lekin xatomi?")}
                  </button>
                )}
                {showBillingPaymentHelp && (
                  <div className="billing-flow-status__actions">
                    {sbpOrderId && (
                      <button
                        type="button"
                        className="billing-flow-status__link billing-flow-status__link--action billing-flow-status__link--center"
                        disabled={sbpPolling}
                        onClick={() => {
                          void checkSbpStatus()
                        }}
                      >
                        {sbpPolling
                          ? lx('Проверяю СБП…', 'Checking SBP…', 'SBP tekshirilmoqda…')
                          : lx('Проверить оплату СБП', 'Check SBP payment', "SBP to'lovini tekshirish")}
                      </button>
                    )}
                    {clickOrderId && (
                      <button
                        type="button"
                        className="billing-flow-status__link billing-flow-status__link--action billing-flow-status__link--center"
                        disabled={clickPolling}
                        onClick={() => {
                          void checkClickStatus()
                        }}
                      >
                        {clickPolling
                          ? lx('Проверяю CLICK…', 'Checking CLICK…', 'CLICK tekshirilmoqda…')
                          : lx('Проверить оплату CLICK', 'Check CLICK payment', "CLICK to'lovini tekshirish")}
                      </button>
                    )}
                    <button
                      type="button"
                      className="billing-flow-status__link billing-flow-status__link--support billing-flow-status__link--center"
                      onClick={() => openTelegramAndCloseMiniApp(SUPPORT_URL)}
                    >
                      {lx('Ошибка не уходит? Напишите в поддержку', 'Still an error? Contact support', "Xato saqlanib qoldimi? Qo'llab-quvvatlashga yozing")}
                    </button>
                  </div>
                )}
                {!!combinedPaymentStatus && <div className="billing-flow-status__text">{combinedPaymentStatus}</div>}
              </div>
            )}

            <div className="billing-flow-card__foot">
              {billingFlowStep !== 'country' ? (
                <button type="button" className="billing-flow-back" onClick={backBillingFlowStep}>
                  ← {lx('Назад', 'Back', 'Orqaga')}
                </button>
              ) : (
                <span />
              )}
              <button type="button" className="billing-flow-back" onClick={closeBillingFlow}>
                {t('close')}
              </button>
            </div>
          </div>
        </div>
      )}

      {showAccessPaywall && (
        <div className="paywall-overlay" role="dialog" aria-modal="true" aria-label={lx('Доступ к раскладам', 'Reading access', 'Yoyilma kirishi')}>
          <div className="paywall-card">
            <div className="paywall-card__title">{lx('Бесплатные расклады закончились', 'Free readings are over', 'Bepul yoyilmalar tugadi')}</div>
            <div className="paywall-card__text">
              {lx(
                'Извините, мы не можем показать новый расклад: бесплатные попытки закончились и подписка не активна. Подключите безлимит, чтобы продолжить пользоваться сервисом.',
                'Sorry, we cannot show a new spread: free attempts are over and subscription is inactive. Activate unlimited access to continue.',
                "Kechirasiz, yangi yoyilma ko'rsatib bo'lmaydi: bepul urinishlar tugagan va obuna faol emas. Davom etish uchun cheksiz tarifni yoqing.",
              )}
            </div>
            <div className="paywall-card__meta">
              {lx('Bu oyda', 'This month', 'Bu oyda')}: {Math.max(0, Number(billing?.free_left ?? 0))} {lx('bepul', 'free', 'bepul')} {lx('из', 'of', 'dan')} {Math.max(1, Number(billing?.free_limit ?? 5))}
            </div>

            <div className="paywall-card__actions">
              <button
                type="button"
                className="glass-cta paywall-card__cta"
                onClick={() => {
                  metrikaReachGoal(METRIKA_GOALS.paymentStart, { provider: 'paywall_inapp' })
                  setShowAccessPaywall(false)
                  openBillingFlow('paywall')
                }}
              >
                <span className="glass-cta__inner">
                  <span className="glass-cta__rim" aria-hidden="true" />
                  <span className="glass-cta__text">{lx('Подключить безлимит', 'Activate unlimited', 'Cheksizni yoqish')}</span>
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
                  <span className="glass-cta__text">{lx('Открыть профиль', 'Open profile', 'Profilni ochish')}</span>
                  <span className="glass-cta__spark" aria-hidden="true" />
                </span>
              </button>
            </div>

            <button type="button" className="paywall-card__close" onClick={() => setShowAccessPaywall(false)}>
              {lx('Позже', 'Later', 'Keyinroq')}
            </button>
          </div>
        </div>
      )}

      {showPaymentSuccessModal && (
        <div className="paywall-overlay" role="dialog" aria-modal="true" aria-label={lx('Оплата успешна', 'Payment successful', "To'lov muvaffaqiyatli")}>
          <div className="paywall-card">
            <div className="paywall-card__title">{lx('Оплата успешна', 'Payment successful', "To'lov muvaffaqiyatli")}</div>
            <div className="paywall-card__text">{paymentSuccessText}</div>
            <div className="paywall-card__actions">
              <button
                type="button"
                className="glass-cta paywall-card__cta"
                onClick={() => setShowPaymentSuccessModal(false)}
              >
                <span className="glass-cta__inner">
                  <span className="glass-cta__rim" aria-hidden="true" />
                  <span className="glass-cta__text">{t('close')}</span>
                  <span className="glass-cta__spark" aria-hidden="true" />
                </span>
              </button>
            </div>
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
            {lx('Готово', 'Done', 'Tayyor')}
          </button>
        </div>
      )}
    </div>
  )
}
