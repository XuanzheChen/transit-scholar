import type { ReactNode } from 'react'

export type BadgeTone = 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'accent'

export interface BadgeProps {
  tone?: BadgeTone
  children: ReactNode
  title?: string
  /** Stable hook for DOM smoke harnesses. */
  testId?: string
}

export function Badge({ tone = 'neutral', children, title, testId }: BadgeProps) {
  return (
    <span className={`badge badge--${tone}`} title={title} data-testid={testId}>
      {children}
    </span>
  )
}
