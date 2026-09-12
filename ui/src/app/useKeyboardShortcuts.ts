import { useEffect, useRef } from 'react'
import { PRODUCT_NAV_ITEMS } from './navigation'

/**
 * Lightweight global keyboard shortcuts.
 *
 * The intent is everyday local research use: move between product sections and
 * jump straight to the active view's main input without reaching for the mouse.
 * Shortcuts stay out of the way of typing, ignore modified keystrokes, and never
 * carry backend meaning — they only drive navigation and focus.
 */
export interface KeyboardShortcutActions {
  /** Navigate to a product section path. */
  onNavigate: (path: string) => void
  /** Open the shortcut reference dialog. */
  onOpenHelp: () => void
  /** Focus the current view's main input; returns whether one was found. */
  onFocusPrimaryInput: () => boolean
}

/** How long a pending `g` chord stays armed. */
export const CHORD_TIMEOUT_MS = 1400

/** Section navigation chords, derived from the product navigation model. */
export const NAVIGATION_CHORDS: Record<string, string> = Object.fromEntries(
  PRODUCT_NAV_ITEMS.map((item) => [item.shortcut, item.path]),
)

/**
 * Inputs that the `/` shortcut may focus, in priority order.
 *
 * Views opt in explicitly with `data-primary-input`; the fallbacks keep the
 * shortcut useful on any view that has a single obvious text field.
 */
export const PRIMARY_INPUT_SELECTOR = [
  '[data-primary-input]',
  '.app__main textarea',
  '.app__main input[type="search"]',
  '.app__main input[type="text"]',
].join(', ')

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) {
    return false
  }
  const tag = target.tagName
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable
}

function hasModifier(event: KeyboardEvent): boolean {
  return event.ctrlKey || event.metaKey || event.altKey
}

/**
 * Focus the active view's main input.
 *
 * Returns whether an input was found, so the caller can leave the keystroke
 * unhandled when the current view has nothing to focus.
 */
export function focusPrimaryInput(): boolean {
  const element = document.querySelector<HTMLElement>(PRIMARY_INPUT_SELECTOR)
  if (!element) {
    return false
  }
  element.focus()
  if (element instanceof HTMLTextAreaElement && typeof element.setSelectionRange === 'function') {
    const end = element.value.length
    try {
      element.setSelectionRange(end, end)
    } catch {
      // Some input types reject selection ranges; focusing is enough.
    }
  }
  return true
}

/** Attach the global shortcut listener for the lifetime of the shell. */
export function useKeyboardShortcuts(actions: KeyboardShortcutActions): void {
  const actionsRef = useRef(actions)
  const chordRef = useRef<{ key: string; at: number } | null>(null)

  useEffect(() => {
    actionsRef.current = actions
  }, [actions])

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent): void {
      if (event.defaultPrevented || event.repeat || hasModifier(event)) {
        chordRef.current = null
        return
      }
      if (isTypingTarget(event.target)) {
        chordRef.current = null
        return
      }

      if (event.key === '?') {
        event.preventDefault()
        actionsRef.current.onOpenHelp()
        return
      }

      if (event.key === '/') {
        if (actionsRef.current.onFocusPrimaryInput()) {
          event.preventDefault()
        }
        return
      }

      const pending = chordRef.current
      chordRef.current = null
      if (pending && pending.key === 'g' && Date.now() - pending.at <= CHORD_TIMEOUT_MS) {
        const path = NAVIGATION_CHORDS[event.key]
        if (path) {
          event.preventDefault()
          actionsRef.current.onNavigate(path)
          return
        }
      }

      if (event.key === 'g') {
        chordRef.current = { key: 'g', at: Date.now() }
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [])
}
