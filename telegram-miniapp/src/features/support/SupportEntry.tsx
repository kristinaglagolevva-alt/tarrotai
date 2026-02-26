import type { SupportTicketDto } from '../../api'

type Props = {
  tickets: SupportTicketDto[]
  loading: boolean
  error: string
  onRefresh: () => void
  onOpenSupport: () => void
}

function mapStatus(statusRaw: string): string {
  const status = String(statusRaw || '').toLowerCase()
  if (status === 'open') return 'Открыт'
  if (status === 'pending_support') return 'Ждёт поддержку'
  if (status === 'pending_user') return 'Ждёт ваш ответ'
  if (status === 'closed') return 'Закрыт'
  return 'В обработке'
}

export default function SupportEntry({ tickets, loading, error, onRefresh, onOpenSupport }: Props) {
  const latest = Array.isArray(tickets) && tickets.length > 0 ? tickets[0] : null

  return (
    <section className="profile-panel profile-panel--support" aria-label="Поддержка">
      <div className="profile-group-title">Поддержка</div>

      <button type="button" className="profile-link-row" onClick={onOpenSupport}>
        <span className="profile-link-row__left">Открыть чат поддержки</span>
        <span className="profile-link-row__chevron" aria-hidden="true">›</span>
      </button>

      {loading ? <div className="support-entry__meta">Проверяем последние обращения…</div> : null}
      {error ? <div className="support-entry__error">{error}</div> : null}

      {!loading && !error && latest ? (
        <div className="support-entry__meta">
          Последнее обращение: {latest.ticket_id} · {mapStatus(latest.status)}
        </div>
      ) : null}

      <button type="button" className="profile-link-row profile-link-row--action" onClick={onRefresh}>
        <span className="profile-link-row__left">Обновить статус обращений</span>
        <span className="profile-link-row__chevron" aria-hidden="true">›</span>
      </button>
    </section>
  )
}
