type Props = {
  text?: string | null
}

export default function ContextHint({ text }: Props) {
  const clean = String(text || '').trim()
  if (!clean) return null

  return (
    <div className="context-hint" role="status" aria-live="polite">
      <div className="context-hint__label">Контекст AI</div>
      <div className="context-hint__text">{clean}</div>
    </div>
  )
}
