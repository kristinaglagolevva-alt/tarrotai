type Props = {
  text?: string | null
}

export default function InsightBanner({ text }: Props) {
  const clean = String(text || '').trim()
  if (!clean) return null

  return (
    <div className="insight-banner" role="status" aria-live="polite">
      <div className="insight-banner__icon" aria-hidden="true">✦</div>
      <div className="insight-banner__content">
        <div className="insight-banner__title">AI заметил повторяющийся паттерн</div>
        <div className="insight-banner__text">{clean}</div>
      </div>
    </div>
  )
}
