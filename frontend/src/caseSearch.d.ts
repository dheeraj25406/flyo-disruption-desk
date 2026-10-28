import type { CaseDetail, CaseListItem } from './types'

export function matchesCaseSearch(item: CaseListItem, detail: CaseDetail | undefined, query: string): boolean
