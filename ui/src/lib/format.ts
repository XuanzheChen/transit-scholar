/** Presentation-only formatting helpers. No backend state is derived here. */

export function formatTimestamp(value: string | null | undefined): string {
  if (!value) {
    return 'not reported'
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleString()
}

export function shortHash(value: string | null | undefined, length = 10): string {
  if (!value) {
    return 'unknown'
  }
  return value.length > length ? `${value.slice(0, length)}…` : value
}

export function pluralize(count: number, singular: string, plural?: string): string {
  return count === 1 ? `${count} ${singular}` : `${count} ${plural ?? `${singular}s`}`
}

/** Human-readable byte size for upload limits and registered PDF files. */
export function formatFileSize(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes <= 0) {
    return 'not reported'
  }
  const kib = bytes / 1024
  if (kib < 1024) {
    return `${Math.round(kib)} KiB`
  }
  const mib = kib / 1024
  if (mib < 1024) {
    return `${Math.round(mib)} MiB`
  }
  return `${(mib / 1024).toFixed(1)} GiB`
}
