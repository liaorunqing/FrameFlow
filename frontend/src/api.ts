import type {
  AssetKind,
  Asset,
  CatalogItem,
  AudioOptions,
  ScriptCandidate,
  Project,
  SetupStatus,
  WorkflowRun,
  ProductionBudget,
  CreativePlan,
} from './types'

const jsonHeaders = { 'Content-Type': 'application/json' }

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: '请求失败' }))
    throw new Error(payload.detail ?? '请求失败')
  }
  if (response.status === 204) return undefined as T
  return response.json()
}

export const api = {
  setup: () => request<SetupStatus>('/api/setup'),
  saveSetup: (body: { dashscope_api_key: string; ai_provider: string; video_provider: string; minimax_api_key: string; seedance_api_key: string }) =>
    request<SetupStatus>('/api/setup', { method: 'POST', headers: jsonHeaders, body: JSON.stringify(body) }),
  projects: () => request<Project[]>('/api/projects'),
  audioOptions: () => request<AudioOptions>('/api/audio/options'),
  assetLibrary: () => request<Asset[]>('/api/assets/library'),
  catalog: () => request<CatalogItem[]>('/api/catalog'),
  selectCatalogItem: (id: string, itemId: string) => request<Project>(`/api/projects/${id}/catalog/${itemId}`, { method: 'POST' }),
  useLibraryAsset: (id: string, assetId: string) => request<Asset>(`/api/projects/${id}/assets/from-library`, { method: 'POST', headers: jsonHeaders, body: JSON.stringify({ asset_id: assetId }) }),
  deleteAsset: (id: string, assetId: string) => request<Project>(`/api/projects/${id}/assets/${assetId}`, { method: 'DELETE' }),
  deleteProject: (id: string) => request<void>(`/api/projects/${id}`, { method: 'DELETE' }),
  project: (id: string) => request<Project>(`/api/projects/${id}`),
  createProject: (body: Partial<Project> = {}) => request<Project>('/api/projects', {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({
      name: '新广告项目',
      product_name: '待定义商品',
      product_category: '其他',
      platform: '抖音',
      duration: 30,
      aspect_ratio: '9:16',
      style: '观察式生活微纪录片',
      audience: '重视真实体验与产品价值的消费者',
      selling_points: ['真实使用体验', '核心功能可视化'],
      brief: '',
      ...body,
    }),
  }),
  updateProject: (id: string, body: Partial<Project>) => request<Project>(`/api/projects/${id}`, {
    method: 'PATCH',
    headers: jsonHeaders,
    body: JSON.stringify(body),
  }),
  upload: async (id: string, kind: AssetKind, file: File) => {
    const body = new FormData()
    body.append('kind', kind)
    body.append('file', file)
    return request(`/api/projects/${id}/assets`, { method: 'POST', body })
  },
  scriptCandidates: (id: string) => request<ScriptCandidate[]>(`/api/projects/${id}/script-candidates`, { method: 'POST' }),
  selectScript: (id: string, candidate: ScriptCandidate) => request<Project>(`/api/projects/${id}/script-selection`, { method: 'POST', headers: jsonHeaders, body: JSON.stringify({ candidate }) }),
  unlockScript: (id: string) => request<Project>(`/api/projects/${id}/script-unlock`, { method: 'POST' }),
  saveScript: (id: string, plan: CreativePlan) => request<Project>(`/api/projects/${id}/script`, { method: 'PUT', headers: jsonHeaders, body: JSON.stringify(plan) }),
  produce: (id: string, budgetCny: number) => request<WorkflowRun>(`/api/projects/${id}/produce`, {
    method: 'POST', headers: jsonHeaders, body: JSON.stringify({ budget_cny: budgetCny }),
  }),
  resumeProduction: (id: string) => request<WorkflowRun>(`/api/projects/${id}/produce/resume`, { method: 'POST' }),
  workflow: (id: string) => request<WorkflowRun>(`/api/projects/${id}/workflow`),
  workflowEvents: (id: string) => `/api/projects/${id}/workflow/events`,
  retryNode: (id: string, nodeId: string) => request<WorkflowRun>(
    `/api/projects/${id}/workflow/nodes/${encodeURIComponent(nodeId)}/retry`,
    { method: 'POST' },
  ),
  selectVideoAttempt: (id: string, nodeId: string, attempt: number) => request<WorkflowRun>(
    `/api/projects/${id}/workflow/nodes/${encodeURIComponent(nodeId)}/select-video-attempt`,
    { method: 'POST', headers: jsonHeaders, body: JSON.stringify({ preferred_attempt: attempt }) },
  ),
  approveWorkflowBudget: (id: string, budgetCny: number) => request<ProductionBudget>(
    `/api/projects/${id}/workflow/budget/approve`,
    { method: 'POST', headers: jsonHeaders, body: JSON.stringify({ budget_cny: budgetCny }) },
  ),
  approveNode: (
    id: string,
    nodeId: string,
    approved: boolean,
    overrideFailedReview = false,
  ) => request<WorkflowRun>(
    `/api/projects/${id}/workflow/nodes/${encodeURIComponent(nodeId)}/approve`,
    {
      method: 'POST',
      headers: jsonHeaders,
      body: JSON.stringify({
        approved,
        override_failed_review: overrideFailedReview,
      }),
    },
  ),
}
