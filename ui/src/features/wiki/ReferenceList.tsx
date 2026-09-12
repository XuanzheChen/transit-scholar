/**
 * Inline list of API-provided reference identifiers.
 *
 * Used by the Wiki detail views to render claim, evidence, and provenance
 * references as individually readable values instead of a comma-joined string.
 */
export function ReferenceList({
  values,
  emptyLabel,
  testId,
}: {
  values: string[]
  emptyLabel: string
  testId?: string
}) {
  if (values.length === 0) {
    return <p className="card__meta">{emptyLabel}</p>
  }
  return (
    <ul className="ref-list" data-testid={testId}>
      {values.map((value) => (
        <li className="ref-list__item" key={value}>
          <code>{value}</code>
        </li>
      ))}
    </ul>
  )
}
