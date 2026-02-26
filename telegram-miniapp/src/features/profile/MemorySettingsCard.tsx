import type { MemorySummaryDto } from '../../api'

type Props = {
  memoryOptIn: boolean
  nudgesOptIn: boolean
  nudgeHour: number | null
  nudgeTz: string | null
  saveBusy: boolean
  saveError: string
  summaryLoading: boolean
  summary: MemorySummaryDto | null
  onToggleMemory: (next: boolean) => void
  onToggleNudges: (next: boolean) => void
  onChangeHour: (next: number | null) => void
  onSave: () => void
  onRefreshSummary: () => void
}

const HOURS = Array.from({ length: 24 }).map((_, h) => h)

function fmtHour(h: number) {
  return `${String(h).padStart(2, '0')}:00`
}

export default function MemorySettingsCard(props: Props) {
  const {
    memoryOptIn,
    nudgesOptIn,
    nudgeHour,
    nudgeTz,
    saveBusy,
    saveError,
    summary,
    onToggleMemory,
    onToggleNudges,
    onChangeHour,
    onSave,
  } = props

  const summaryHint = String(summary?.cycle_hints?.[0] || '').trim()

  return (
    <section className="profile-panel profile-panel--memory" aria-label="Персонализация AI">
      <div className="profile-group-title">Персонализация AI</div>

      <div className="memory-settings__description">
        Используем вашу существующую историю раскладов для контекста. Отдельную «вторую историю» не создаём.
      </div>

      <div className="memory-settings__row">
        <div className="memory-settings__text">
          <div className="memory-settings__title">Память раскладов (90 дней)</div>
          <div className="memory-settings__sub">AI учитывает повторяющиеся темы в новых интерпретациях.</div>
        </div>
        <label className="memory-switch" aria-label="Память раскладов">
          <input
            type="checkbox"
            checked={memoryOptIn}
            onChange={(e) => onToggleMemory(e.target.checked)}
          />
          <span className="memory-switch__slider" />
        </label>
      </div>

      <div className={`memory-settings__row ${!memoryOptIn ? 'is-disabled' : ''}`}>
        <div className="memory-settings__text">
          <div className="memory-settings__title">AI-напоминания</div>
          <div className="memory-settings__sub">Мягкое сообщение в боте по выбранному часу (только по согласию).</div>
        </div>
        <label className="memory-switch" aria-label="AI-напоминания">
          <input
            type="checkbox"
            checked={memoryOptIn && nudgesOptIn}
            disabled={!memoryOptIn}
            onChange={(e) => onToggleNudges(e.target.checked)}
          />
          <span className="memory-switch__slider" />
        </label>
      </div>

      <div className={`memory-settings__grid ${!memoryOptIn || !nudgesOptIn ? 'is-disabled' : ''}`}>
        <label className="memory-settings__field">
          <span>Час напоминания</span>
          <select
            value={nudgeHour == null ? '' : String(nudgeHour)}
            disabled={!memoryOptIn || !nudgesOptIn}
            onChange={(e) => {
              const v = String(e.target.value)
              onChangeHour(v === '' ? null : Number(v))
            }}
          >
            <option value="">Не выбрано</option>
            {HOURS.map((h) => (
              <option key={h} value={h}>
                {fmtHour(h)}
              </option>
            ))}
          </select>
        </label>

        <div className="memory-settings__field memory-settings__tz">
          <span>Часовой пояс</span>
          <div>{nudgeTz || 'UTC'}</div>
        </div>
      </div>

      <div className="memory-settings__actions">
        <button
          type="button"
          className="profile-link-row profile-link-row--action"
          disabled={saveBusy}
          onClick={onSave}
        >
          <span className="profile-link-row__left">{saveBusy ? 'Сохраняем…' : 'Сохранить настройки'}</span>
          <span className="profile-link-row__chevron" aria-hidden="true">›</span>
        </button>
      </div>

      {saveError ? <div className="memory-settings__error">{saveError}</div> : null}
      {summaryHint ? <div className="memory-settings__hint">{summaryHint}</div> : null}
    </section>
  )
}
