import { request } from './api'
import type {
  ArtifactDetailOut,
  ArtifactListOut,
  CreateArtifactRequest,
} from './types'

/** Thin wrappers around `/artifacts/*` (`api/artifacts.py`) --
 * the same convention `lib/tenantAdminApi.ts`/`lib/onboardingApi.ts`
 * already establish.
 *
 * Note what `saveArtifact` does *not* send: no rows, no columns, no
 * result of any kind. The server reads all of that from the conversation
 * turn it already persisted, which is why a client cannot save an
 * artifact describing a result that never happened. */

export function listArtifacts(conversationId?: string): Promise<ArtifactListOut> {
  const query = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : ''
  return request<ArtifactListOut>(`/artifacts${query}`)
}

export function saveArtifact(payload: CreateArtifactRequest): Promise<ArtifactDetailOut> {
  return request<ArtifactDetailOut>('/artifacts', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function getArtifact(artifactId: string): Promise<ArtifactDetailOut> {
  return request<ArtifactDetailOut>(`/artifacts/${artifactId}`)
}

/** Re-runs the artifact's *recorded* SQL server-side. Deliberately takes
 * no SQL argument -- the route accepts none, so there is no client-side
 * way to turn a refresh into a less-governed `POST /execute`. */
export function refreshArtifact(artifactId: string): Promise<ArtifactDetailOut> {
  return request<ArtifactDetailOut>(`/artifacts/${artifactId}/refresh`, { method: 'POST' })
}

export function deleteArtifact(artifactId: string): Promise<void> {
  return request<void>(`/artifacts/${artifactId}`, { method: 'DELETE' })
}
