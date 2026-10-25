import { describe, expect, it } from 'vitest'
import type { Citation, SourceAnswer } from './types'
import {
  insightEvidenceSummary,
  sourceAnswerEvidenceStatus,
} from './evidence'

function citation(overrides: Partial<Citation> = {}): Citation {
  return {
    filename: 'handbook.pdf',
    chunk_index: 0,
    page_number: 4,
    document_id: 'doc-1',
    has_pdf_bytes: true,
    ...overrides,
  }
}

function sourceAnswer(citations: Citation[], answer = 'Policy states 20 days.'): SourceAnswer {
  return { answer, citations, status: 'ok' }
}

describe('insightEvidenceSummary -- what was actually verified', () => {
  it('reports how many figures the server checked, and against what', () => {
    expect(insightEvidenceSummary(2, 42)).toBe(
      '2 figures in this summary were checked against the 42 returned rows.',
    )
  })

  it('reads correctly for a single figure', () => {
    expect(insightEvidenceSummary(1, 42)).toContain('1 figure in this summary')
  })

  it('says a summary with no figures states none, rather than implying failure', () => {
    // A real, ordinary case: "sales are concentrated in the top category"
    // states nothing numeric. "0 figures verified" would read as a problem.
    expect(insightEvidenceSummary(0, 42)).toBe(
      'This summary states no figures of its own; it describes the 42 returned rows.',
    )
  })

  it('claims no verification at all when the server reported no count', () => {
    // An older turn, or one reloaded from saved history. Saying nothing is
    // correct; implying a check happened would be inventing evidence.
    expect(insightEvidenceSummary(null, 42)).toBeUndefined()
  })

  it('falls back to naming the result when the row count is unknown', () => {
    expect(insightEvidenceSummary(3, null)).toContain('the returned result')
    expect(insightEvidenceSummary(3, null)).not.toContain('null')
  })
})

describe('sourceAnswerEvidenceStatus -- source-backed requires a source', () => {
  it('is source-backed when something is actually cited', () => {
    expect(sourceAnswerEvidenceStatus(sourceAnswer([citation()]))).toBe('source_backed')
  })

  it('is not available when nothing is cited', () => {
    // rag/graph.py's insufficient-information fallback answers without
    // citing anything.
    expect(
      sourceAnswerEvidenceStatus(
        sourceAnswer([], 'I could not find enough information to answer that.'),
      ),
    ).toBe('unavailable')
  })

  it('never claims a source for an answer whose material was withheld', () => {
    // A policy chunk carrying a sensitivity_category, or a document whose
    // restricted_roles exclude this caller, is never summarized into an
    // answer and yields citations == []. Labelling that "Source-backed"
    // would turn an access control into a false endorsement.
    const restricted = sourceAnswer([], 'That information is not available to your role.')
    expect(sourceAnswerEvidenceStatus(restricted)).toBe('unavailable')
    expect(sourceAnswerEvidenceStatus(restricted)).not.toBe('source_backed')
  })
})
