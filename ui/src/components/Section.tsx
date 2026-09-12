import type { ReactNode } from 'react'

export interface SectionProps {
  title: string
  description?: ReactNode
  actions?: ReactNode
  children: ReactNode
}

/** Standard content section used by every product area. */
export function Section({ title, description, actions, children }: SectionProps) {
  return (
    <section className="section">
      <header className="section__header">
        <div>
          <h2 className="section__title">{title}</h2>
          {description ? <p className="section__description">{description}</p> : null}
        </div>
        {actions ? <div className="section__actions">{actions}</div> : null}
      </header>
      <div className="section__body">{children}</div>
    </section>
  )
}

/** Muted note used for iteration/roadmap or advanced-detail explanations. */
export function Note({ children, tone = 'neutral' }: { children: ReactNode; tone?: 'neutral' | 'warning' }) {
  return <p className={`note note--${tone}`}>{children}</p>
}
