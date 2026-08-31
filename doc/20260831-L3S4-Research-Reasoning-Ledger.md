# Layer3 Stage4: Research Reasoning Ledger

Layer3 Stage4 provides durable research-semantic state for one
`ResearchSession`. It records caller- or policy-created Queries, explicitly
admitted Evidence snapshots, explicitly created Claims, and structural
support/contradiction links between Claims and Evidence.

Stage4 is not an autonomous reasoning Agent. It does not choose or generate
Queries, infer Claims from Evidence, verify Claims semantically, calculate
confidence, or make final answers. It performs only deterministic structural
validation and persistence.

## Durable records

- `ResearchQueryRecord` stores a Query's session ownership, text, lifecycle
  status (`active`, `completed`, or `abandoned`), timestamps, and optional
  same-session parent Query.
- `EvidenceRecord` stores selected `ResearchEvidence` as an immutable
  `text_snapshot`, with its `EvidenceLocator`, source metadata, and available
  retrieval provenance. Retrieval alone never admits evidence to this ledger.
- `ClaimRecord` stores a caller-created statement, lifecycle status
  (`proposed`, `supported`, `conflicting`, or `rejected`), timestamps, and an
  optional rationale.
- `ClaimEvidenceLink` stores one `supports` or `contradicts` relationship.
  Claims and Evidence must have the same `ResearchSession` owner.

The records are relational database entities, independent of the Stage2
`ResearchState.payload` runtime checkpoint. An abandoned Query remains
available for inspection, and an Evidence snapshot remains unchanged if the
underlying paper is later reparsed.

## Framework-neutral API

`transit_scholar.layer3.ledger` exposes `QueryService`, `EvidenceService`,
`ClaimService`, and `ClaimEvidenceLinkService`. These APIs accept an existing
SQLAlchemy persistence session and require no LangGraph or other Agent runtime.

The intended lifecycle is:

1. A caller creates a Query for an existing `ResearchSession`.
2. The caller explicitly admits selected `ResearchEvidence` records for that
   Query.
3. The caller explicitly creates a Claim.
4. The caller links admitted Evidence as `supports` or `contradicts`.

No Stage4 operation modifies Base Wiki or Agentic Wiki state.
