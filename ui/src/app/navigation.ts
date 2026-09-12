/**
 * Product navigation model.
 *
 * The navigation speaks in user-facing product concepts only. Internal
 * engineering layers (Layer1/2/3, Ledger, RoleRuntime, ResearchSession) must
 * never appear as primary navigation or required user terminology.
 */
export type ProductSectionId = 'workspaces' | 'research' | 'library' | 'wiki' | 'schemas'

export interface ProductNavItem {
  id: ProductSectionId
  label: string
  path: string
  summary: string
  /** True when the section needs an open Workspace to show content. */
  requiresWorkspace: boolean
  /** Single letter used by the lightweight `g` + key navigation chord. */
  shortcut: string
}

export const PRODUCT_NAV_ITEMS: ProductNavItem[] = [
  {
    id: 'workspaces',
    label: 'Workspaces',
    path: '/workspaces',
    summary: 'Create and open research workspaces',
    requiresWorkspace: false,
    shortcut: 'w',
  },
  {
    id: 'research',
    label: 'Research',
    path: '/research',
    summary: 'Conversations, agent progress, and answers',
    requiresWorkspace: true,
    shortcut: 'r',
  },
  {
    id: 'library',
    label: 'Library',
    path: '/library',
    summary: 'Local papers and PDFs',
    requiresWorkspace: false,
    shortcut: 'l',
  },
  {
    id: 'wiki',
    label: 'Wiki',
    path: '/wiki',
    summary: 'Schema Wiki and Agent Learned knowledge',
    requiresWorkspace: true,
    shortcut: 'k',
  },
  {
    id: 'schemas',
    label: 'Schemas',
    path: '/schemas',
    summary: 'Schema definitions and versions',
    requiresWorkspace: false,
    shortcut: 's',
  },
]

/**
 * Terms that belong to backend implementation and must not become primary
 * product navigation. Exported so the UI contract can be tested directly.
 */
export const INTERNAL_TERMS_NOT_IN_NAVIGATION: string[] = [
  'Layer1',
  'Layer2',
  'Layer3',
  'Ledger',
  'RoleRuntime',
  'ResearchSession',
]

/** Resolve the active navigation item for a pathname (segment aware). */
export function navItemForPath(pathname: string): ProductNavItem | undefined {
  const normalized = pathname === '/' ? '/workspaces' : pathname
  return PRODUCT_NAV_ITEMS.find(
    (item) => normalized === item.path || normalized.startsWith(`${item.path}/`),
  )
}
