import type { ReactNode } from 'react'

type Props = {
  active: boolean
  statusLabel: string
  onOpenManage: () => void
  children?: ReactNode
}

export default function SubscriptionManageCard({ active, statusLabel, onOpenManage, children }: Props) {
  if (!active) return null
  return (
    <section className="profile-panel profile-panel--premium" aria-label="Подписка">
      <div className="profile-panel__title">
        <span className="profile-icon profile-icon--premium" aria-hidden="true">
          <svg viewBox="0 0 24 24">
            <path d="M4 9.5 7.4 5h9.2L20 9.5l-8 9-8-9Z" fill="none" stroke="currentColor" strokeWidth="1.8" />
            <path d="M7.4 5 12 18.5 16.6 5" fill="none" stroke="currentColor" strokeWidth="1.4" />
          </svg>
        </span>
        <span>Premium / Подписка AI Tarot</span>
      </div>

      <div className="profile-panel__status">
        Статус: <span className="profile-status is-active">{statusLabel}</span>
      </div>

      <button type="button" className="profile-link-row" onClick={onOpenManage}>
        <span className="profile-link-row__left">Управление подпиской</span>
        <span className="profile-link-row__chevron" aria-hidden="true">
          ›
        </span>
      </button>

      {children}
    </section>
  )
}
