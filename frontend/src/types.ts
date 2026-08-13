export type AssetKind = 'product' | 'character' | 'scene' | 'brand'

export interface Asset {
  id: string
  project_id: string
  kind: AssetKind
  name: string
  url: string
  content_type: string
  size: number
  created_at: string
}

export interface Shot {
  id: string
  index: number
  title: string
  duration: number
  purpose: string
  narrative_beat: string
  visual: string
  camera: string
  action: string
  voiceover: string
  on_screen_text: string
  continuity_anchor: string
  transition: string
  prompt: string
  model_hint: 'economy' | 'story' | 'premium'
  status: string
  quality_score?: number
  output_url?: string
  provider?: string
  estimated_cost?: number
}

export interface StorySequence {
  id: string
  index: number
  title: string
  narrative_goal: string
  turning_point: string
  continuity_scope: string
  shot_ids: string[]
  duration: number
}

export interface CreativePlan {
  director_source: 'ollama' | 'openai_compatible' | 'fallback'
  director_model: string
  director_note: string
  campaign_idea: string
  logline: string
  protagonist: string
  story_question: string
  hook: string
  emotional_arc: string
  continuity_bible: string[]
  narration_script: string
  visual_language: string
  music_direction: string
  call_to_action: string
  sequences: StorySequence[]
  shots: Shot[]
}

export interface RenderTask {
  id: string
  project_id: string
  status: string
  progress: number
  stage_label: string
  provider: string
  created_at: string
  updated_at: string
  output_url?: string
  estimated_cost: number
  error?: string
}

export interface SubjectBible {
  asset_ids: string[]
  immutable_features: string[]
  required_evidence: string[]
  prohibited_changes: string[]
}

export interface BrandBible {
  version: string
  status: 'draft' | 'conflict' | 'approved'
  product: SubjectBible
  character: SubjectBible
  scene: SubjectBible
  brand: SubjectBible
  mandatory_claims: string[]
  visual_tone: string[]
  global_prohibitions: string[]
  review_checklist: string[]
  fact_conflicts: string[]
  created_at: string
  updated_at: string
}

export interface ProductionBudget {
  ceiling_cny: number
  approved_cny: number
  approval_phrase: string
  approved_at?: string
  approved: boolean
}

export interface ProductionPreflight {
  project_id: string
  ready_for_paid_generation: boolean
  estimated_total_cny: number
  budget_ceiling_cny: number
  remaining_budget_cny: number
  approval_phrase: string
  checks: string[]
  blockers: string[]
}

export interface SystemConfig {
  mode: 'demo' | 'api'
  default_provider: string
  provider_configured: boolean
  director_mode: 'story_fallback' | 'llm'
  vision_mode: 'ollama' | 'dashscope' | 'disabled'
  vision_model: string
  pricing_note: string
  billing_currency: 'CNY' | 'USD'
}

export interface SetupStatus {
  ready: boolean
  director_configured: boolean
  vision_configured: boolean
  video_configured: boolean
  video_provider: string
  minimax_configured: boolean
  seedance_configured: boolean
}

export interface Project {
  id: string
  status: 'draft' | 'planned' | 'rendering' | 'completed'
  created_at: string
  updated_at: string
  assets: Asset[]
  creative_plan?: CreativePlan
  latest_task?: RenderTask
  output_url?: string
  brand_bible?: BrandBible
  production_budget?: ProductionBudget
  name: string
  product_name: string
  product_category: string
  platform: string
  duration: number
  aspect_ratio: string
  style: string
  audience: string
  selling_points: string[]
  brief: string
  script_template: string
  voice_id: string
  voice_speed: number
  bgm_track: string
  bgm_volume: number
  subtitles_enabled: boolean
  narrative_pace: string
  shot_count: number
  hook_style: string
  camera_style: string
  lighting_style: string
  emotion_curve: string
  narration_density: string
  product_exposure: string
  transition_style: string
  realism_level: string
  negative_constraints: string
}

export interface AudioOptions {
  bgm_tracks: { id: string; label: string }[]
  voices: { id: string; label: string }[]
}

export interface ScriptCandidate {
  id: string
  label: string
  template: string
  plan: CreativePlan
}

export type WorkflowNodeStatus =
  | 'blocked'
  | 'ready'
  | 'running'
  | 'review_required'
  | 'completed'
  | 'failed'
  | 'skipped'

export interface WorkflowAttempt {
  number: number
  status: 'running' | 'completed' | 'failed'
  started_at: string
  finished_at?: string
  provider: string
  model_id: string
  estimated_cost_cny: number
  actual_cost_cny?: number
  error?: string
  outputs: Record<string, string>
}

export interface WorkflowNode {
  id: string
  kind: string
  label: string
  shot_id?: string
  dependencies: string[]
  status: WorkflowNodeStatus
  progress: number
  cache_key: string
  cache_hit: boolean
  billable: boolean
  max_retries: number
  attempts: WorkflowAttempt[]
  estimated_cost_cny: number
  actual_cost_cny: number
  quality_decision: QualityDecision | Record<string, never>
  issues: string[]
  retry_count: number
  updated_at: string
}

export interface RepairStep {
  action: string
  label: string
  target_node_id: string
  prompt_patch: string
}

export interface QualityDecision {
  passed: boolean
  score: number
  severity: 'none' | 'minor' | 'major' | 'critical'
  categories: string[]
  summary: string
  repair_steps: RepairStep[]
  prompt_patch: string
  suggested_provider: string
  retry_recommended: boolean
  remaining_retries: number
  spent_cny: number
  retry_budget_cny: number
  projected_next_cost_cny: number
  within_budget: boolean
}

export interface WorkflowRun {
  id: string
  project_id: string
  version: string
  status: 'draft' | 'running' | 'review_required' | 'completed' | 'failed'
  nodes: WorkflowNode[]
  created_at: string
  updated_at: string
  estimated_cost_cny: number
  actual_cost_cny: number
  progress: number
}

export interface ProviderCapability {
  id: string
  label: string
  configured: boolean
  region: string
  modes: string[]
  strengths: string[]
  limitations: string[]
}

export interface RoutingDecision {
  shot_id: string
  provider: string
  model_hint: 'economy' | 'story' | 'premium'
  requested_capabilities: string[]
  reasons: string[]
  fallback_provider: string
}

export interface EnhancementCapability {
  id: string
  label: string
  available: boolean
  executable: string
  purpose: string
  warning: string
}

export interface IntegrationCapability {
  id: string
  label: string
  category: 'director' | 'quality' | 'editor' | 'audio'
  available: boolean
  mode: string
  detail: string
  license_note: string
  missing: string[]
}

export interface ProviderBenchmark {
  provider: string
  node_kind: 'keyframe' | 'video'
  generated_attempts: number
  accepted_outputs: number
  reviewed_outputs: number
  automatic_pass_rate: number
  actual_cost_cny: number
  average_cost_per_attempt_cny: number
}

export interface QualityBenchmarkReport {
  project_id: string
  total_generation_attempts: number
  total_actual_cost_cny: number
  providers: ProviderBenchmark[]
  failure_categories: Record<string, number>
}

export interface BenchmarkCase {
  id: 'product_showcase' | 'character_emotion' | 'hand_interaction'
  label: string
  purpose: string
  duration: number
  prompt: string
  reference_asset_ids: string[]
  first_frame_asset_id: string
  requires_composite_keyframe: boolean
  eligible: boolean
  blocking_reasons: string[]
  review_dimensions: string[]
}

export interface BenchmarkJob {
  id: string
  case_id: string
  provider: 'seedance-official' | 'minimax-official'
  model_id: string
  duration: number
  resolution: string
  estimated_cost_cny: number
  status: string
  latency_seconds?: number
  actual_cost_cny?: number
  output_url: string
  automatic_score?: number
  automatic_passed?: boolean
  operator_review_score?: number
  operator_review_passed?: boolean
  operator_review_notes: string
  error: string
}

export interface BenchmarkExperiment {
  id: string
  project_id: string
  version: string
  status: 'prepared' | 'blocked' | 'approved' | 'running' | 'review_required' | 'completed' | 'cancelled'
  cases: BenchmarkCase[]
  jobs: BenchmarkJob[]
  keyframe_cost_cny: number
  contingency_rate: number
  hard_budget_cny: number
  estimated_cost_cny: number
  committed_cost_cny: number
  approval_phrase: string
  approved_budget_cny?: number
  report_path: string
  assembly_status: 'not_started' | 'running' | 'completed' | 'failed'
  assembly_output_url: string
  assembly_error: string
  can_execute: boolean
}

export interface BlindReviewItem {
  token: string
  sample_label: string
  case_label: string
  review_dimensions: string[]
  output_url: string
}

export interface BenchmarkAssemblyResult {
  experiment_id: string
  status: string
  output_url: string
  output_path: string
  selected_job_ids: string[]
  duration_seconds: number
  narration_path: string
  subtitle_path: string
  note: string
}
