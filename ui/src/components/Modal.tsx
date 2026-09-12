import { useEffect, useId, useRef } from 'react'
import type { ReactNode } from 'react'
import { Button } from './Button'

export interface ModalProps {
  title: string
  description?: ReactNode
  onClose: () => void
  children: ReactNode
  footer?: ReactNode
  /** Stable hook for DOM smoke harnesses. */
  testId?: string
  size?: 'md' | 'lg'
}

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ')

/** Number of dialogs currently mounted; used to lock page scroll exactly once. */
let openDialogCount = 0

/**
 * Accessible modal dialog used for creation and detail flows.
 *
 * Implemented as an overlay with `role="dialog"` rather than the native
 * `<dialog>` element so the same behaviour (Escape to dismiss, labelled title,
 * initial focus, Tab trapping, focus restoration) is available in every
 * supported browser and in the jsdom verification harness.
 */
export function Modal({ title, description, onClose, children, footer, testId, size = 'md' }: ModalProps) {
  const titleId = useId()
  const descriptionId = useId()
  const panelRef = useRef<HTMLDivElement>(null)
  const restoreFocusRef = useRef<HTMLElement | null>(null)

  useEffect(() => {
    const previouslyFocused = document.activeElement
    restoreFocusRef.current = previouslyFocused instanceof HTMLElement ? previouslyFocused : null

    openDialogCount += 1
    document.body.classList.add('modal-open')
    return () => {
      openDialogCount = Math.max(0, openDialogCount - 1)
      if (openDialogCount === 0) {
        document.body.classList.remove('modal-open')
      }
      // Return keyboard focus to the control that opened the dialog.
      const target = restoreFocusRef.current
      if (target && target.isConnected) {
        target.focus()
      }
    }
  }, [])

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key === 'Escape') {
        event.stopPropagation()
        onClose()
        return
      }
      if (event.key !== 'Tab') {
        return
      }
      const panel = panelRef.current
      if (!panel) {
        return
      }
      const focusable = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
        (element) => !element.hasAttribute('hidden'),
      )
      if (focusable.length === 0) {
        event.preventDefault()
        panel.focus()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      const current = document.activeElement
      if (event.shiftKey && (current === first || !panel.contains(current))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (current === last || !panel.contains(current))) {
        event.preventDefault()
        first.focus()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  useEffect(() => {
    const panel = panelRef.current
    if (panel && !panel.contains(document.activeElement)) {
      panel.focus()
    }
  }, [])

  return (
    <div
      className="modal-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose()
        }
      }}
    >
      <div
        className={`modal modal--${size}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descriptionId : undefined}
        tabIndex={-1}
        ref={panelRef}
        data-testid={testId}
      >
        <header className="modal__header">
          <div>
            <h2 className="modal__title" id={titleId}>
              {title}
            </h2>
            {description ? (
              <p className="modal__description" id={descriptionId}>
                {description}
              </p>
            ) : null}
          </div>
          <Button size="sm" variant="ghost" onClick={onClose} aria-label="Close dialog">
            Close
          </Button>
        </header>
        <div className="modal__body">{children}</div>
        {footer ? <footer className="modal__footer">{footer}</footer> : null}
      </div>
    </div>
  )
}
