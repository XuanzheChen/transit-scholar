import { useMemo } from 'react'
import { api } from '../../api'
import { useApiResource } from '../../hooks/useApiResource'

/**
 * Library paper titles by paper id, read from the Papers API.
 *
 * Wiki knowledge records reference papers by id. Showing the Library title (when
 * the Papers API reports one) makes those references readable without inventing
 * any paper identity in the frontend: an unknown or deleted paper simply keeps
 * displaying its id.
 */
export function usePaperTitles(): {
  titles: Map<string, string>
  loading: boolean
} {
  const library = useApiResource((signal) => api.papers.list({ signal, limit: 500 }), [])

  const titles = useMemo(() => {
    const map = new Map<string, string>()
    for (const paper of library.data?.items ?? []) {
      map.set(paper.paper_id, paper.title ?? 'Untitled paper')
    }
    return map
  }, [library.data])

  return { titles, loading: library.status === 'loading' }
}
