export interface ProseProps {
  /** Text exactly as the API returned it. */
  text: string
  className?: string
  /** Stable hook for DOM smoke harnesses. */
  testId?: string
}

/**
 * Readable rendering of API-provided prose.
 *
 * The text is only split into paragraphs: the UI never rewrites, summarises, or
 * reinterprets knowledge content that came from the backend (C-002).
 */
export function Prose({ text, className, testId }: ProseProps) {
  const paragraphs = text
    .split(/\n\s*\n/)
    .map((paragraph) => paragraph.trim())
    .filter((paragraph) => paragraph.length > 0)

  if (paragraphs.length === 0) {
    return null
  }

  return (
    <div className={['prose', className].filter(Boolean).join(' ')} data-testid={testId}>
      {paragraphs.map((paragraph, index) => (
        <p className="prose__paragraph" key={index}>
          {paragraph}
        </p>
      ))}
    </div>
  )
}
