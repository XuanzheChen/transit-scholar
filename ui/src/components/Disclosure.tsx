import { useState } from 'react'
import type { ReactNode, SyntheticEvent } from 'react'

export interface DisclosureProps {
  summary: string
  children: ReactNode
  defaultOpen?: boolean
  /**
   * Controlled open state.
   *
   * When provided, the disclosure follows the caller's state (used by the
   * research Timeline, which stays expanded while a run is active and collapses
   * once the run completes). Without it the disclosure manages its own state.
   */
  open?: boolean
  onOpenChange?: (open: boolean) => void
  className?: string
  /** Stable hook for DOM smoke harnesses. */
  testId?: string
}

/**
 * Accordion built on native `<details>` so collapsed content stays accessible
 * and keyboard-operable without extra component machinery.
 */
export function Disclosure({
  summary,
  children,
  defaultOpen = false,
  open,
  onOpenChange,
  className,
  testId,
}: DisclosureProps) {
  const [internalOpen, setInternalOpen] = useState(defaultOpen)
  const controlled = open !== undefined
  const expanded = controlled ? open : internalOpen

  function handleToggle(event: SyntheticEvent<HTMLDetailsElement>): void {
    const next = event.currentTarget.open
    if (!controlled) {
      setInternalOpen(next)
    }
    onOpenChange?.(next)
  }

  return (
    <details
      className={['disclosure', className].filter(Boolean).join(' ')}
      open={expanded}
      onToggle={handleToggle}
      data-testid={testId}
    >
      <summary className="disclosure__summary">{summary}</summary>
      <div className="disclosure__body">{children}</div>
    </details>
  )
}
