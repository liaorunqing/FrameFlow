import type {
  AssetKind,
  Asset,
  CatalogItem,
  AudioOptions,
  ScriptCandidate,
  BrandBible,
  BenchmarkExperiment,
  BlindReviewItem,
  BenchmarkAssemblyResult,
  EnhancementCapability,
  IntegrationCapability,
  Project,
  ProviderCapability,
  QualityBenchmarkReport,
  RenderTask,
  RoutingDecision,
  SystemConfig,
  SetupStatus,
  WorkflowRun,
  ProductionBudget,
  ProductionPreflight,
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
  config: () => request<SystemConfig>('/api/config'),
  capabilities: () => request<ProviderCapability[]>('/api/providers/capabilities'),
  enhancements: () => request<EnhancementCapability[]>('/api/postprocess/capabilities'),
  integrations: () => request<IntegrationCapability[]>('/api/integrations'),
  projects: () => request<Project[]>('/api/projects'),
  audioOptions: () => request<AudioOptions>('/api/audio/options'),
  assetLibrary: () => request<Asset[]>('/api/assets/library'),
  catalog: () => request<CatalogItem[]>('/api/catalog'),
  selectCatalogItem: (id: string, itemId: string) => request<Project>(`/api/projects/${id}/catalog/${itemId}`, { method: 'POST' }),
  useLibraryAsset: (id: string, assetId: string) => request<Asset>(`/api/projects/${id}/assets/from-library`, { method: 'POST', headers: jsonHeaders, body: JSON.stringify({ asset_id: assetId }) }),
  deleteAsset: (id: string, assetId: string) => request<Project>(`/api/projects/${id}/assets/${assetId}`, { method: 'DELETE' }),
  deleteProject: (id: string) => request<void>(`/api/projects/${id}`, { method: 'DELETE' }),
  project: (id: string) => request<Project>(`/api/projects/${id}`),
  draftBrandBible: (id: string) => request<BrandBible>(`/api/projects/${id}/brand-bible/draft`, { method: 'POST' }),
  approveBrandBible: (id: string) => request<BrandBible>(`/api/projects/${id}/brand-bible/approve`, { method: 'POST' }),
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
  analyzeCreativeContext: (id: string) => request<Project>(`/api/projects/${id}/creative-context/analyze`, { method: 'POST' }),
  plan: (id: string) => request<Project>(`/api/projects/${id}/plan`, { method: 'POST' }),
  scriptCandidates: (id: string) => request<ScriptCandidate[]>(`/api/projects/${id}/script-candidates`, { method: 'POST' }),
  selectScript: (id: string, candidate: ScriptCandidate) => request<Project>(`/api/projects/${id}/script-selection`, { method: 'POST', headers: jsonHeaders, body: JSON.stringify({ candidate }) }),
  produce: (id: string, budgetCny: number) => request<WorkflowRun>(`/api/projects/${id}/produce`, {
    method: 'POST', headers: jsonHeaders, body: JSON.stringify({ budget_cny: budgetCny }),
  }),
  resumeProduction: (id: string) => request<WorkflowRun>(`/api/projects/${id}/produce/resume`, { method: 'POST' }),
  plushBearResearchPlan: (id: string) => request<Project>(`/api/projects/${id}/research/plush-bear-30s-plan`, { method: 'POST' }),
  productionBudget: (id: string) => request<ProductionBudget>(`/api/projects/${id}/production-budget`),
  productionPreflight: (id: string) => request<ProductionPreflight>(`/api/projects/${id}/production-preflight`),
  workflow: (id: string) => request<WorkflowRun>(`/api/projects/${id}/workflow`),
  workflowEvents: (id: string) => `/api/projects/${id}/workflow/events`,
  qualityReport: (id: string) => request<QualityBenchmarkReport>(`/api/projects/${id}/quality-report`),
  prepareBenchmark: (id: string) => request<BenchmarkExperiment>(`/api/projects/${id}/benchmarks`, {
    method: 'POST',
  }),
  latestBenchmark: (id: string) => request<BenchmarkExperiment | null>(`/api/projects/${id}/benchmarks/latest`),
  assembleBenchmark: (projectId: string, experimentId: string) => request<BenchmarkAssemblyResult>(
    `/api/projects/${projectId}/benchmarks/${experimentId}/assemble`,
    { method: 'POST' },
  ),
  nextBlindReview: (projectId: string, experimentId: string) => request<BlindReviewItem | null>(
    `/api/projects/${projectId}/benchmarks/${experimentId}/blind-review/next`,
  ),
  submitBlindReview: (projectId: string, experimentId: string, token: string, score: number, passed: boolean) => request<BenchmarkExperiment>(
    `/api/projects/${projectId}/benchmarks/${experimentId}/blind-review`,
    { method: 'POST', headers: jsonHeaders, body: JSON.stringify({ token, score, passed, failure_categories: [] }) },
  ),
  createWorkflow: (id: string, reset = false) => request<WorkflowRun>(`/api/projects/${id}/workflow`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ reset }),
  }),
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
  executeNode: (
    id: string,
    nodeId: string,
    confirmBillable: boolean,
    providerOverride = '',
    overrideBudget = false,
  ) => request<WorkflowRun>(
    `/api/projects/${id}/workflow/nodes/${encodeURIComponent(nodeId)}/execute`,
    {
      method: 'POST',
      headers: jsonHeaders,
      body: JSON.stringify({
        confirm_billable: confirmBillable,
        provider_override: providerOverride,
        override_budget: overrideBudget,
      }),
    },
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
  routing: (id: string) => request<RoutingDecision[]>(`/api/projects/${id}/routing`),
  render: (id: string) => request<RenderTask>(`/api/projects/${id}/render`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ provider: 'auto', quality: 'standard', variants: 1 }),
  }),
  task: (id: string) => request<RenderTask>(`/api/tasks/${id}`),
}
