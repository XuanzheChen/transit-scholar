/**
 * Presentation helpers for the registered local PDF.
 *
 * The product keeps the browser's native PDF viewer as the basic path: the
 * file-content endpoint URL is opened unchanged. A page fragment is a pure
 * viewer hint appended to that same URL, so a citation can point at the page the
 * evidence was recorded on without introducing a custom PDF reader (REQ-007).
 */

/** Human-readable page locator, or `null` when the API reported no pages. */
export function formatPageList(pages: number[] | null | undefined): string | null {
  const clean = (pages ?? []).filter((page) => Number.isFinite(page) && page >= 1)
  if (clean.length === 0) {
    return null
  }
  return clean.map((page) => `p. ${page}`).join(', ')
}

/** First API-reported page, or `null` when none is usable as a viewer hint. */
export function firstPage(pages: number[] | null | undefined): number | null {
  const page = (pages ?? []).find((candidate) => Number.isFinite(candidate) && candidate >= 1)
  return typeof page === 'number' ? Math.floor(page) : null
}

/**
 * Append a `#page=N` viewer fragment to a PDF URL.
 *
 * The base URL is returned untouched when no valid page is available, so the
 * plain file-content endpoint stays the default open target.
 */
export function pdfUrlForPage(url: string, page: number | null | undefined): string {
  if (typeof page !== 'number' || !Number.isFinite(page) || page < 1) {
    return url
  }
  const [base, existingFragment] = url.split('#', 2)
  const fragment = new URLSearchParams(existingFragment ?? '')
  fragment.set('page', String(Math.floor(page)))
  return `${base}#${fragment.toString()}`
}
