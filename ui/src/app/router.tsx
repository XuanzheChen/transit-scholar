import { useSyncExternalStore } from 'react'
import type { AnchorHTMLAttributes, MouseEvent, ReactNode } from 'react'

const listeners = new Set<() => void>()

function emit(): void {
  for (const listener of listeners) {
    listener()
  }
}

function subscribe(onStoreChange: () => void): () => void {
  listeners.add(onStoreChange)
  window.addEventListener('popstate', onStoreChange)
  return () => {
    listeners.delete(onStoreChange)
    window.removeEventListener('popstate', onStoreChange)
  }
}

function getSnapshot(): string {
  return `${window.location.pathname}${window.location.search}`
}

/**
 * Minimal history-based router.
 *
 * The production build is served by FastAPI with an SPA fallback, and the Vite
 * dev server provides history fallback too, so plain `history.pushState`
 * navigation works in both models without a routing dependency.
 */
export function useLocation(): string {
  return useSyncExternalStore(subscribe, getSnapshot, () => '/')
}

export function navigate(to: string, options: { replace?: boolean } = {}): void {
  if (to === getSnapshot()) {
    return
  }
  if (options.replace) {
    window.history.replaceState(null, '', to)
  } else {
    window.history.pushState(null, '', to)
  }
  emit()
}

export interface LinkProps extends Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> {
  to: string
  children?: ReactNode
}

/** Client-side link that keeps normal browser navigation affordances. */
export function Link({ to, children, onClick, ...rest }: LinkProps) {
  function handleClick(event: MouseEvent<HTMLAnchorElement>): void {
    onClick?.(event)
    if (
      event.defaultPrevented ||
      event.button !== 0 ||
      event.metaKey ||
      event.ctrlKey ||
      event.shiftKey ||
      event.altKey
    ) {
      return
    }
    event.preventDefault()
    navigate(to)
  }

  return (
    <a {...rest} href={to} onClick={handleClick}>
      {children}
    </a>
  )
}
