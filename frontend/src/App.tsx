import { useEffect, useMemo, useState } from 'react'
import {
  Aperture, Check, ChevronDown, CircleAlert, Clock3, Download, Eye, Film,
  FolderOpen, ImagePlus, KeyRound, Layers3, LoaderCircle, Menu, Plus,
  Settings, Sparkles, Upload, Video, WandSparkles, X,
} from 'lucide-react'
import { api } from './api'
import type { Asset, AssetKind, AudioOptions, Project, QualityDecision, ScriptCandidate, SetupStatus, WorkflowNode, WorkflowRun } from './types'

const assetCards: { kind: AssetKind; title: string; hint: string; required?: boolean }[] = [
  { kind: 'product', title: '商品', hint: '正面、侧面与功能细节', required: true },
  { kind: 'character', title: '人物', hint: '固定角色与服装' },
  { kind: 'scene', title: '场景', hint: '环境、光线与陈设' },
  { kind: 'brand', title: 'Logo', hint: '仅在后期清晰叠加' },
]

const stageNames: Record<string, string> = {
  asset_analysis: '理解素材', story_plan: '创作故事', keyframe_generation: '生成关键帧',
  keyframe_review: '审核关键帧', video_generation: '生成视频镜头', video_review: '检查视觉连续性',
  audio_timeline: '制作旁白与字幕', composition: '剪辑完整成片', upscale: '清晰度增强',
  frame_interpolation: '运动补帧',
}

const statusLabels: Record<string, string> = {
  draft: '草稿', planned: '已规划', rendering: '生成中', running: '生成中',
  review_required: '待处理', completed: '已完成', failed: '失败',
}

const qualityCategoryLabels: Record<string, { label: string; description: string }> = {
  product_identity: { label: '商品一致性', description: '商品外形、颜色、材质或关键部件与参考图存在偏差' },
  character_identity: { label: '人物一致性', description: '人物面部、发型、年龄或服装在镜头中发生变化' },
  scene_identity: { label: '场景一致性', description: '空间、光线或关键陈设与已批准场景不一致' },
  extra_people: { label: '多余人物', description: '画面出现脚本未指定的路人、重复人物或背景人脸' },
  camera_gaze: { label: '视线异常', description: '人物直视镜头或没有看向当前动作目标' },
  story_mismatch: { label: '故事不匹配', description: '镜头动作没有按脚本的起点、过程和终点推进' },
  hand_physics: { label: '手部与物理', description: '手指、接触关系、拿取动作或物体运动不自然' },
  motion_continuity: { label: '动作连续性', description: '前后动作、屏幕方向或镜头衔接出现跳变' },
  text_or_watermark: { label: '文字或水印', description: '生成画面中出现不应由模型制作的文字、标识或水印' },
  blank_screen: { label: '屏幕异常', description: '商品屏幕出现白屏、过曝或与真实参考不符的显示状态' },
  product_scale: { label: '商品尺寸', description: '商品相对人物或场景的比例明显偏离真实尺寸' },
}

const severityLabels: Record<string, string> = { none: '无问题', minor: '轻微', major: '明显', critical: '严重' }

function nodeError(node?: WorkflowNode) {
  if (!node) return ''
  return node.issues[0] || node.attempts.at(-1)?.error || ''
}

function providerAlertFor(node?: WorkflowNode) {
  const detail = nodeError(node)
  if (!detail) return null
  const balance = /1008|余额不足|insufficient\s*(balance|credit)|quota/i.test(detail)
  const auth = /1004|401|403|API.?Key|鉴权|认证|unauthorized/i.test(detail)
  if (!balance && !auth) return null
  return {
    type: balance ? 'balance' : 'auth',
    title: balance ? 'MiniMax API 余额不足' : '云端 API 鉴权失败',
    detail,
    action: balance ? '充值后重试' : '检查模型配置',
  }
}

function browserMediaUrl(value = '') {
  if (!value || value.startsWith('/') || /^https?:\/\//i.test(value)) return value
  const normalized = value.replaceAll('\\', '/')
  const marker = '/workflow-artifacts/'
  const index = normalized.indexOf(marker)
  return index >= 0 ? `/artifacts/${normalized.slice(index + marker.length)}` : ''
}

function App() {
  const [projects, setProjects] = useState<Project[]>([])
  const [project, setProject] = useState<Project | null>(null)
  const [setup, setSetup] = useState<SetupStatus | null>(null)
  const [workflow, setWorkflow] = useState<WorkflowRun | null>(null)
  const [showSetup, setShowSetup] = useState(false)
  const [showProjects, setShowProjects] = useState(false)
  const [mobileNav, setMobileNav] = useState(false)
  const [activePanel, setActivePanel] = useState<'assets' | 'story'>('assets')
  const [busy, setBusy] = useState('')
  const [message, setMessage] = useState('')
  const [budget, setBudget] = useState(30)
  const [audioOptions, setAudioOptions] = useState<AudioOptions>({ bgm_tracks: [], voices: [] })
  const [library, setLibrary] = useState<Asset[]>([])
  const [showLibrary, setShowLibrary] = useState(false)
  const [scriptCandidates, setScriptCandidates] = useState<ScriptCandidate[]>([])
  const [selectedCandidate, setSelectedCandidate] = useState('')

  const loadProject = async (id: string) => {
    const current = await api.project(id)
    setProject(current)
    // A freshly created project legitimately has no workflow yet. Avoid a
    // noisy 409 request in the browser console until generation starts.
    setWorkflow(current.creative_plan ? await api.workflow(id).catch(() => null) : null)
    setMessage('')
  }

  const refreshProjects = async (selectedId?: string) => {
    const list = await api.projects()
    setProjects(list)
    const current = list.find(item => item.id === selectedId) || list[0]
    if (current) await loadProject(current.id)
  }

  const createNewProject = async () => {
    setBusy('new-project')
    try {
      const created = await api.createProject({ name: `未命名视频 ${projects.length + 1}` })
      setProjects([created, ...projects])
      setProject(created)
      setWorkflow(null)
      setShowProjects(false)
      setActivePanel('assets')
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '无法新建项目')
    } finally { setBusy('') }
  }

  useEffect(() => {
    Promise.all([api.setup(), api.projects(), api.audioOptions(), api.assetLibrary()]).then(async ([status, list, audio, storedAssets]) => {
      setAudioOptions(audio); setLibrary(storedAssets)
      setSetup(status)
      setShowSetup(!status.ready)
      let current = list[0]
      if (!current) current = await api.createProject({ name: '我的第一条视频' })
      setProjects(current && !list.length ? [current] : list)
      setProject(current)
      setWorkflow(current.creative_plan ? await api.workflow(current.id).catch(() => null) : null)
    }).catch(error => setMessage(error instanceof Error ? error.message : '无法连接后端'))
  }, [])

  useEffect(() => {
    if (!project || !workflow || !['running', 'draft'].includes(workflow.status)) return
    const timer = window.setInterval(async () => {
      const flow = await api.workflow(project.id).catch(() => null)
      if (!flow) return
      setWorkflow(flow)
      if (['completed', 'failed', 'review_required'].includes(flow.status)) {
        window.clearInterval(timer)
        const updated = await api.project(project.id)
        setProject(updated)
        setProjects(items => items.map(item => item.id === updated.id ? updated : item))
        setBusy('')
      }
    }, 2500)
    return () => window.clearInterval(timer)
  }, [project?.id, workflow?.status])

  const update = <K extends keyof Project>(key: K, value: Project[K]) => {
    if (!project) return
    const scriptInputs = new Set<keyof Project>(['product_name', 'product_category', 'platform', 'duration', 'aspect_ratio', 'style', 'audience', 'selling_points', 'brief', 'script_template', 'narrative_pace', 'shot_count', 'hook_style', 'camera_style', 'lighting_style', 'emotion_curve', 'narration_density', 'product_exposure', 'transition_style', 'realism_level', 'negative_constraints'])
    setProject({ ...project, [key]: value, ...(scriptInputs.has(key) ? { creative_plan: undefined } : {}) })
    if (scriptInputs.has(key)) { setScriptCandidates([]); setSelectedCandidate('') }
  }

  const upload = async (kind: AssetKind, file?: File) => {
    if (!project || !file) return
    setBusy(`upload-${kind}`); setMessage('')
    try {
      await api.upload(project.id, kind, file)
      await refreshProjects(project.id)
    } catch (error) { setMessage(error instanceof Error ? error.message : '上传失败') }
    finally { setBusy('') }
  }

  const generate = async () => {
    if (!project) return
    setBusy('produce'); setMessage('')
    try {
      const updated = await api.updateProject(project.id, {
        name: project.name, product_name: project.product_name, product_category: project.product_category,
        platform: project.platform, duration: project.duration, aspect_ratio: project.aspect_ratio,
        style: project.style, audience: project.audience, selling_points: project.selling_points, brief: project.brief,
        script_template: project.script_template, voice_id: project.voice_id, voice_speed: project.voice_speed,
        bgm_track: project.bgm_track, bgm_volume: project.bgm_volume, subtitles_enabled: project.subtitles_enabled,
        narrative_pace: project.narrative_pace, shot_count: project.shot_count, hook_style: project.hook_style,
        camera_style: project.camera_style, lighting_style: project.lighting_style, emotion_curve: project.emotion_curve,
        narration_density: project.narration_density, product_exposure: project.product_exposure,
        transition_style: project.transition_style, realism_level: project.realism_level,
        negative_constraints: project.negative_constraints,
      })
      setProject(updated)
      if (!project.creative_plan) {
        setScriptCandidates(await api.scriptCandidates(project.id))
        setSelectedCandidate('')
        setBusy(''); setMessage('已生成 3 个候选脚本。请选择并锁定一个方案。')
        return
      }
      setWorkflow(await api.produce(project.id, budget))
    } catch (error) { setBusy(''); setMessage(error instanceof Error ? error.message : '生成启动失败') }
  }

  const lockScript = async () => {
    if (!project) return
    const candidate = scriptCandidates.find(item => item.id === selectedCandidate)
    if (!candidate) return
    setBusy('lock-script'); setMessage('')
    try {
      const updated = await api.selectScript(project.id, candidate)
      setProject(updated); setProjects(items => items.map(item => item.id === updated.id ? updated : item))
      setMessage('脚本已锁定。现在可以设置声音与画面并生成视频。')
    } catch (error) { setMessage(error instanceof Error ? error.message : '脚本锁定失败') }
    finally { setBusy('') }
  }

  const activeNode = workflow?.nodes.find(node => node.status === 'running')
    || workflow?.nodes.find(node => node.status === 'review_required' || node.status === 'failed')
  const providerAlert = providerAlertFor(activeNode)
  const output = browserMediaUrl(project?.output_url || workflow?.nodes.find(node => node.id === 'final-compose')?.attempts.at(-1)?.outputs.video_url)
  const productReady = !!project?.assets.some(asset => asset.kind === 'product')
  const progress = workflow?.progress || 0
  const videoNodes = workflow?.nodes.filter(node => ['video_generation', 'video_review'].includes(node.kind)) || []
  const completedShots = videoNodes.filter(node => node.kind === 'video_generation' && node.status === 'completed').length
  const totalShots = videoNodes.filter(node => node.kind === 'video_generation').length || project?.creative_plan?.shots.length || 0

  const continueAfterReview = async (mode: 'retry' | 'accept') => {
    if (!project || !activeNode) return
    setBusy(`review-${mode}`); setMessage('')
    try {
      if (mode === 'accept') await api.approveNode(project.id, activeNode.id, true, true)
      else {
        const id = activeNode.kind === 'keyframe_review' ? activeNode.id.replace('keyframe-review:', 'keyframe:')
          : activeNode.kind === 'video_review' ? activeNode.id.replace('video-review:', 'video:') : activeNode.id
        await api.retryNode(project.id, id)
      }
      setWorkflow(await api.resumeProduction(project.id))
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '无法继续生产')
      setBusy('')
    }
  }

  if (!project || !setup) return <div className="launch-screen"><Aperture/><LoaderCircle className="spin"/><span>正在打开 FrameFlow Studio</span></div>

  return <div className="studio-shell">
    <header className="studio-topbar">
      <button className="mobile-menu" onClick={() => setMobileNav(!mobileNav)}><Menu/></button>
      <div className="brand"><span><Aperture/></span><div><b>FrameFlow</b><small>STORY VIDEO STUDIO</small></div></div>
      <button className="project-switch" onClick={() => setShowProjects(!showProjects)}>
        <FolderOpen/><span><small>当前项目</small><b>{project.name}</b></span><ChevronDown/>
      </button>
      <div className="top-actions">
        <span className={`cloud-state ${setup.ready ? 'online' : ''}`}><i/>{setup.ready ? '云端模型在线' : '模型未配置'}</span>
        <button className="icon-button" onClick={() => setShowSetup(true)} title="模型配置"><Settings/></button>
        <button className="new-button" onClick={() => void createNewProject()} disabled={busy === 'new-project'}><Plus/>{busy === 'new-project' ? '创建中' : '新建视频'}</button>
      </div>
    </header>

    {showProjects && <div className="project-popover">
      <div><b>我的视频</b><button onClick={() => setShowProjects(false)}><X/></button></div>
      {projects.map(item => <button key={item.id} className={item.id === project.id ? 'selected' : ''} onClick={() => { void loadProject(item.id); setShowProjects(false) }}>
        <span><Film/></span><div><b>{item.name}</b><small>{item.product_name} · {item.duration} 秒</small></div><em>{statusLabels[item.status] || item.status}</em>
      </button>)}
      <button className="popover-new" onClick={() => void createNewProject()}><Plus/>创建新视频项目</button>
    </div>}

    <aside className={`studio-nav ${mobileNav ? 'open' : ''}`}>
      <button className={activePanel === 'assets' ? 'active' : ''} onClick={() => { setActivePanel('assets'); setMobileNav(false) }}><ImagePlus/><span>素材</span></button>
      <button className={activePanel === 'story' ? 'active' : ''} onClick={() => { setActivePanel('story'); setMobileNav(false) }}><WandSparkles/><span>创作</span></button>
      <button onClick={() => setShowProjects(true)}><Clock3/><span>历史</span></button>
      <i/>
      <button onClick={() => setShowSetup(true)}><Settings/><span>设置</span></button>
    </aside>

    <main className={`studio-main panel-${activePanel}`}>
      {providerAlert && <div className="provider-alert" role="alert">
        <span><CircleAlert/></span>
        <div><small>PAID SERVICE PAUSED</small><b>{providerAlert.title}</b><p>{providerAlert.detail}</p><em>任务已经暂停，不会继续扣费。充值或修正配置后可从当前失败镜头继续，无需重新生成已完成内容。</em></div>
        <div className="provider-alert-actions">
          {providerAlert.type === 'balance' && <a href="https://platform.minimaxi.com/console/recharge-records" target="_blank" rel="noreferrer">前往充值</a>}
          <button disabled={!!busy} onClick={() => providerAlert.type === 'balance' ? void continueAfterReview('retry') : setShowSetup(true)}>{busy === 'review-retry' ? <LoaderCircle className="spin"/> : null}{providerAlert.action}</button>
        </div>
      </div>}
      <section className="workbench">
        <nav className="business-steps" aria-label="制作步骤"><span className={productReady ? 'done' : 'active'}><em>1</em>准备素材</span><i/><span className={!productReady ? '' : project.creative_plan ? 'done' : 'active'}><em>2</em>选择脚本</span><i/><span className={project.creative_plan && !workflow ? 'active' : workflow ? 'done' : ''}><em>3</em>声画设置</span><i/><span className={workflow && workflow.status !== 'completed' ? 'active' : workflow?.status === 'completed' ? 'done' : ''}><em>4</em>生成质检</span></nav>
        <div className="workbench-heading">
          <div><small>{activePanel === 'assets' ? '01 / REFERENCE LIBRARY' : '02 / DIRECTOR BRIEF'}</small><h1>{activePanel === 'assets' ? '参考素材' : '创作设定'}</h1></div>
          <span>{project.aspect_ratio} · {project.duration}s · {project.platform}</span>
        </div>

        {activePanel === 'assets' ? <>
          <button className="library-button" onClick={() => setShowLibrary(!showLibrary)}><FolderOpen/>从素材库选择 <em>{library.length}</em></button>
          {showLibrary && <div className="library-grid">{library.map(asset => <button key={asset.id} onClick={async () => { await api.useLibraryAsset(project.id, asset.id); await refreshProjects(project.id); setShowLibrary(false) }}><img src={asset.url} alt={asset.name}/><span>{asset.name}<small>{asset.kind}</small></span></button>)}</div>}
          <p className="section-intro">上传真实参考图。商品图是必需项；人物与场景图能让跨镜头表现更稳定。</p>
          <div className="asset-board">{assetCards.map(card => {
            const asset = [...project.assets].reverse().find(item => item.kind === card.kind)
            return <label className={`asset-tile ${asset ? 'filled' : ''}`} key={card.kind}>
              <input type="file" accept="image/*" onChange={event => void upload(card.kind, event.target.files?.[0])}/>
              {asset ? <img src={asset.url} alt={card.title}/> : <div className="upload-mark"><ImagePlus/><span>添加图片</span></div>}
              <div className="asset-meta"><span><b>{card.title}</b>{card.required && <em>必需</em>}<small>{asset?.name || card.hint}</small></span>{busy === `upload-${card.kind}` ? <LoaderCircle className="spin"/> : <Upload/>}</div>
            </label>
          })}</div>
          <div className="asset-tip"><Eye/><span><b>一致性提示</b>商品建议上传正面、侧面和使用状态；同一类素材可多次上传，系统将以最新图片为主要参考。</span></div>
        </> : <>
          <p className="section-intro">只填写真实资料和故事意图。AI 导演会自动完成脚本、分镜与供应商提示词。</p>
          <div className="director-form">
            <label className="wide">广告脚本模板<select value={project.script_template || 'story'} onChange={e => { update('script_template', e.target.value); update('creative_plan', undefined) }}><option value="story">真实故事推进</option><option value="problem-solution">问题—尝试—解决</option><option value="feature-proof">功能实证演示</option><option value="documentary">生活微纪录</option><option value="product-film">质感产品短片</option></select></label>
            <details className="director-console wide" open><summary><span><b>导演参数台</b><small>STORY · CAMERA · PACING</small></span><em>10 项可调</em><ChevronDown/></summary><div className="control-matrix">
              <label>镜头数量<select value={project.shot_count || 5} onChange={e => update('shot_count', Number(e.target.value))}>{[3,4,5,6,7,8].map(n => <option key={n} value={n}>{n} 个镜头</option>)}</select></label>
              <label>叙事节奏<select value={project.narrative_pace || 'balanced'} onChange={e => update('narrative_pace', e.target.value)}><option value="slow">舒缓娓娓道来</option><option value="balanced">自然均衡</option><option value="fast">短促有冲击力</option></select></label>
              <label>前三秒钩子<select value={project.hook_style || 'action'} onChange={e => update('hook_style', e.target.value)}><option value="action">动作先行</option><option value="problem">问题悬念</option><option value="emotion">情绪瞬间</option><option value="product">产品奇观</option></select></label>
              <label>旁白密度<select value={project.narration_density || 'medium'} onChange={e => update('narration_density', e.target.value)}><option value="low">少旁白，多留白</option><option value="medium">均衡解说</option><option value="high">信息密集</option></select></label>
              <label>摄影方式<select value={project.camera_style || 'observational'} onChange={e => update('camera_style', e.target.value)}><option value="observational">观察式手持</option><option value="cinematic">电影感运镜</option><option value="tripod">稳定商业摄影</option><option value="macro">细节微距优先</option></select></label>
              <label>光线风格<select value={project.lighting_style || 'natural-warm'} onChange={e => update('lighting_style', e.target.value)}><option value="natural-warm">温暖自然窗光</option><option value="soft-studio">柔和棚拍光</option><option value="bright-playful">明亮童趣</option><option value="moody">克制低调</option></select></label>
              <label>情绪曲线<select value={project.emotion_curve || 'curiosity-growth-resolution'} onChange={e => update('emotion_curve', e.target.value)}><option value="curiosity-growth-resolution">好奇→投入→满足</option><option value="problem-relief-delight">困扰→缓解→惊喜</option><option value="calm-warmth-memory">平静→温暖→回味</option></select></label>
              <label>产品露出<select value={project.product_exposure || 'natural'} onChange={e => update('product_exposure', e.target.value)}><option value="natural">自然进入故事</option><option value="hero">产品主角优先</option><option value="demonstration">操作演示优先</option></select></label>
              <label>镜头衔接<select value={project.transition_style || 'match-action'} onChange={e => update('transition_style', e.target.value)}><option value="match-action">动作匹配</option><option value="sound-bridge">声音桥</option><option value="gentle-dissolve">柔和叠化</option><option value="straight-cut">克制直切</option></select></label>
              <label>写实程度<select value={project.realism_level || 'photoreal'} onChange={e => update('realism_level', e.target.value)}><option value="photoreal">真实摄影</option><option value="commercial">精致商业写实</option><option value="stylized">轻度风格化</option></select></label>
              <label className="wide">负面约束<textarea rows={3} value={project.negative_constraints || ''} onChange={e => update('negative_constraints', e.target.value)} placeholder="禁止产品变形、乱码、水印、额外人物……"/></label>
            </div></details>
            <label className="wide">项目名称<input value={project.name} onChange={e => update('name', e.target.value)}/></label>
            <label>商品名称<input value={project.product_name} onChange={e => update('product_name', e.target.value)}/></label>
            <label>商品类别<input value={project.product_category} onChange={e => update('product_category', e.target.value)}/></label>
            <label>目标平台<select value={project.platform} onChange={e => update('platform', e.target.value)}><option>抖音</option><option>TikTok</option><option>小红书</option><option>淘宝</option><option>视频号</option></select></label>
            <label>画面风格<select value={project.style} onChange={e => update('style', e.target.value)}><option>真实生活叙事广告</option><option>观察式生活微纪录片</option><option>温馨家庭故事</option><option>质感产品短片</option><option>轻快社交广告</option></select></label>
            <label className="wide">核心卖点 <small>每行一项</small><textarea rows={4} value={project.selling_points.join('\n')} onChange={e => update('selling_points', e.target.value.split('\n').filter(Boolean))}/></label>
            <label className="wide">故事要求<textarea rows={6} value={project.brief} onChange={e => update('brief', e.target.value)} placeholder="例如：一个孩子放学回家，在自然互动中发现并使用产品功能……"/></label>
            <label>视频时长<select value={project.duration} onChange={e => update('duration', Number(e.target.value))}><option value={15}>15 秒</option><option value={30}>30 秒</option><option value={45}>45 秒</option><option value={60}>60 秒</option></select></label>
            <label>画面比例<select value={project.aspect_ratio} onChange={e => update('aspect_ratio', e.target.value)}><option>9:16</option><option>16:9</option><option>1:1</option></select></label>
          </div>
          {scriptCandidates.length > 0 && !project.creative_plan && <div className="candidate-deck"><header><small>STEP 02 / SCRIPT SELECTION</small><h3>选择一个创作方向</h3><p>三个方案只生成脚本，不调用付费视频模型。</p></header><div>{scriptCandidates.map(candidate => <button key={candidate.id} className={selectedCandidate === candidate.id ? 'selected' : ''} onClick={() => setSelectedCandidate(candidate.id)}><span><em>{candidate.label}</em>{selectedCandidate === candidate.id && <Check/>}</span><b>{candidate.plan.campaign_idea}</b><p>{candidate.plan.logline}</p><small>{candidate.plan.shots.length} 镜头 · {candidate.plan.emotional_arc}</small></button>)}</div><button className="lock-script" disabled={!selectedCandidate || !!busy} onClick={() => void lockScript()}>{busy === 'lock-script' ? <LoaderCircle className="spin"/> : <Check/>}锁定所选脚本</button></div>}
          {project.creative_plan && <div className="script-preview"><div><small>LOCKED SCRIPT / 已锁定脚本</small><h3>{project.creative_plan.campaign_idea}</h3><p>{project.creative_plan.logline}</p></div>{project.creative_plan.shots.map((shot, index) => <article key={shot.id}><em>{String(index + 1).padStart(2, '0')}</em><span><b>{shot.title}</b><p>{shot.action}</p><q>{shot.voiceover || '此镜头无旁白'}</q></span><time>{shot.duration}s</time></article>)}<button onClick={() => { update('creative_plan', undefined); setScriptCandidates([]); setSelectedCandidate('') }} disabled={!!busy}><WandSparkles/>解除锁定并重新生成候选</button></div>}
        </>}
        <button className="panel-next" onClick={() => setActivePanel(activePanel === 'assets' ? 'story' : 'assets')}>{activePanel === 'assets' ? '下一步：创作设定' : '返回检查素材'}<ChevronDown/></button>
      </section>

      <PreviewPanel project={project} workflow={workflow} output={output} activeNode={activeNode} completedShots={completedShots} totalShots={totalShots}/>

      <aside className="render-panel">
        <div className="render-heading"><span><Sparkles/></span><div><small>AUDIO · CAPTION · OUTPUT</small><h2>声画与生成</h2></div></div>
        <div className="setting-row"><span>视频模型</span><b>{setup.video_provider === 'minimax-official' ? 'MiniMax 海螺' : 'Seedance'}</b></div>
        <div className="setting-grid"><label>时长<select value={project.duration} onChange={e => update('duration', Number(e.target.value))}><option value={15}>15s</option><option value={30}>30s</option><option value={45}>45s</option><option value={60}>60s</option></select></label><label>比例<select value={project.aspect_ratio} onChange={e => update('aspect_ratio', e.target.value)}><option>9:16</option><option>16:9</option><option>1:1</option></select></label></div>
        <div className={`media-controls ${!project.creative_plan ? 'locked' : ''}`}>
          {!project.creative_plan && <p className="control-lock"><KeyRound/>请先选择并锁定脚本</p>}
          <h3><span>01</span>配音</h3>
          <label>配音音色<select value={project.voice_id || 'male-qn-qingse'} onChange={e => update('voice_id', e.target.value)}>{audioOptions.voices.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
          <label>语速 <b>{(project.voice_speed || .92).toFixed(2)}×</b><input type="range" min="0.7" max="1.3" step="0.05" value={project.voice_speed || .92} onChange={e => update('voice_speed', Number(e.target.value))}/></label>
          <h3><span>02</span>背景音乐</h3>
          <label>背景音乐<select value={project.bgm_track || 'house-vibez.mp3'} onChange={e => update('bgm_track', e.target.value)}>{audioOptions.bgm_tracks.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
          <label>音乐音量 <b>{Math.round((project.bgm_volume || 0) * 100)}%</b><input type="range" min="0" max="0.35" step="0.01" value={project.bgm_volume || 0} onChange={e => update('bgm_volume', Number(e.target.value))}/></label>
          <h3><span>03</span>字幕</h3>
          <label className="switch-row"><span>黑体字幕<small>来自确认后的旁白并自动对齐</small></span><input type="checkbox" checked={project.subtitles_enabled !== false} onChange={e => update('subtitles_enabled', e.target.checked)}/></label>
        </div>
        <label className="budget-control"><span>预算上限 <b>¥{budget}</b></span><input type="range" min="10" max="100" step="5" value={budget} onChange={e => setBudget(Number(e.target.value))}/><small>只按实际调用计费，超过上限自动停止</small></label>
        <div className="readiness">
          <span className={productReady ? 'ready' : ''}>{productReady ? <Check/> : <CircleAlert/>}商品参考</span>
          <span className={setup.ready ? 'ready' : ''}>{setup.ready ? <Check/> : <KeyRound/>}云端模型</span>
          <span className="ready"><Check/>自动剪辑与字幕</span>
        </div>
        {message && <div className="error-note"><CircleAlert/>{message}</div>}
        {!providerAlert && activeNode && ['failed', 'review_required'].includes(activeNode.status) && <QualityReviewCard node={activeNode} busy={busy} onRetry={() => void continueAfterReview('retry')} onAccept={() => void continueAfterReview('accept')}/>} 
        <button className="generate-button" disabled={!productReady || !setup.ready || !!busy || ['running', 'review_required'].includes(workflow?.status || '') || !!providerAlert} onClick={() => void generate()}>
          {busy === 'produce' || workflow?.status === 'running' ? <LoaderCircle className="spin"/> : <Sparkles/>}
          <span><b>{providerAlert ? '请先处理 API 状态' : workflow?.status === 'review_required' ? '请先处理质量检查' : workflow?.status === 'running' ? '正在生成视频' : !project.creative_plan ? '先生成脚本预览' : output ? '重新生成完整视频' : '确认脚本并生成视频'}</b><small>{providerAlert ? '处理后从顶部“充值后重试”继续' : workflow?.status === 'review_required' ? '请在上方审核报告中选择重做或接受' : workflow?.status === 'running' ? `${stageNames[activeNode?.kind || ''] || '处理中'} · ${progress}%` : !project.creative_plan ? '本步骤不会调用付费视频模型' : `预计 ${project.duration} 秒成片`}</small></span>
        </button>
        <p className="generate-note">生成过程中可以离开页面，后端会继续完成任务。</p>
      </aside>
    </main>

    {showSetup && <SetupModal current={setup} onClose={() => setup.ready && setShowSetup(false)} onSaved={status => { setSetup(status); setShowSetup(false) }}/>} 
  </div>
}

function QualityReviewCard({ node, busy, onRetry, onAccept }: { node: WorkflowNode; busy: string; onRetry: () => void; onAccept: () => void }) {
  const decision = node.quality_decision as Partial<QualityDecision>
  const attempt = node.attempts.at(-1)
  const outputs = attempt?.outputs || {}
  const categories = decision.categories?.length ? decision.categories : (outputs.failure_categories || '').split(',').filter(Boolean)
  const score = decision.score ?? (outputs.score ? Number(outputs.score) : undefined)
  const severity = decision.severity || (categories.length > 2 ? 'major' : 'minor')
  const retries = decision.remaining_retries ?? Math.max(0, node.max_retries - node.retry_count)
  const nextCost = decision.projected_next_cost_cny ?? node.estimated_cost_cny
  const repairSteps = decision.repair_steps || []
  const summary = decision.summary || node.issues[0] || '当前画面未达到自动通过标准。'
  return <div className={`quality-review severity-${severity}`}>
    <div className="quality-review-head"><span><Eye/></span><div><small>AUTOMATIC QUALITY REVIEW</small><b>检测到 {categories.length || 1} 类问题</b></div>{score !== undefined && <strong>{score}<em>/100</em></strong>}</div>
    <p className="quality-summary">{summary}</p>
    <div className="quality-categories">{categories.map(category => {
      const item = qualityCategoryLabels[category] || { label: category.replaceAll('_', ' '), description: '视觉审核检测到此项与当前镜头要求存在偏差' }
      return <div key={category}><span><CircleAlert/></span><p><b>{item.label}</b><small>{item.description}</small></p></div>
    })}</div>
    <details className="quality-details" open>
      <summary>查看审核与修复详情 <ChevronDown/></summary>
      <div className="quality-metrics"><span>严重程度<b>{severityLabels[severity] || severity}</b></span><span>剩余重试<b>{retries} 次</b></span><span>预计重做费用<b>¥{Number(nextCost || 0).toFixed(2)}</b></span><span>已重试<b>{node.retry_count} 次</b></span></div>
      {(repairSteps.length > 0 || decision.prompt_patch || outputs.repair_prompt_patch) && <div className="repair-plan"><b>AI 建议的修复方式</b>{repairSteps.length > 0 ? repairSteps.map((step, index) => <p key={`${step.target_node_id}-${index}`}><em>{index + 1}</em><span>{step.label}<small>{step.prompt_patch}</small></span></p>) : <p><em>1</em><span>局部重做当前镜头<small>{decision.prompt_patch || outputs.repair_prompt_patch}</small></span></p>}</div>}
      {attempt?.error && <div className="technical-detail"><b>技术错误</b><code>{attempt.error}</code></div>}
    </details>
    <div className="quality-decision-note"><CircleAlert/><span><b>如何选择？</b>“局部重做”会再次调用模型并产生上方预计费用；“接受并继续”不会重做，但这些视觉问题会保留在最终成片中。</span></div>
    <div className="quality-actions"><button className="retry" disabled={!!busy || retries <= 0} onClick={onRetry}>{busy === 'review-retry' ? <LoaderCircle className="spin"/> : <Sparkles/>}局部重做当前镜头</button><button className="accept" disabled={!!busy} onClick={onAccept}><Check/>接受风险并继续</button></div>
  </div>
}

function PreviewPanel({ project, workflow, output, activeNode, completedShots, totalShots }: { project: Project; workflow: WorkflowRun | null; output: string; activeNode?: WorkflowNode; completedShots: number; totalShots: number }) {
  const progress = workflow?.progress || 0
  const timeline = useMemo(() => {
    const nodes = workflow?.nodes || []
    const order = ['asset_analysis', 'story_plan', 'keyframe_generation', 'video_generation', 'audio_timeline', 'composition']
    return order.map(kind => {
      const matching = nodes.filter(node => node.kind === kind)
      return { kind, done: matching.length > 0 && matching.every(node => ['completed', 'skipped'].includes(node.status)), active: matching.some(node => node.status === 'running') }
    })
  }, [workflow])
  return <section className="preview-stage">
    <div className="preview-toolbar"><div><Video/><span><b>成片预览</b><small>{output ? '已渲染最新版本' : '生成后在这里预览'}</small></span></div>{output && <a href={output} download><Download/>导出 MP4</a>}</div>
    <div className={`viewer ratio-${project.aspect_ratio.replace(':', '-')}`}>
      {output ? <video key={output} src={output} controls/> : <div className="empty-viewer"><span><Aperture/></span><h3>故事将在这里发生</h3><p>添加参考素材并完成创作设定，AI 会生成一条有叙事、有声音的完整视频。</p></div>}
      {workflow?.status === 'running' && <div className="render-overlay"><LoaderCircle className="spin"/><b>{stageNames[activeNode?.kind || ''] || '正在制作'}</b><span>{progress}%</span></div>}
    </div>
    <div className="production-status">
      <div><span><b>{workflow?.status === 'completed' ? '制作完成' : workflow?.status === 'running' ? '正在制作' : '等待生成'}</b><small>{totalShots ? `${completedShots}/${totalShots} 个视频镜头完成` : '准备素材与故事'}</small></span><strong>{progress}%</strong></div>
      <i><span style={{ width: `${progress}%` }}/></i>
      {workflow && <small>实际使用 ¥{workflow.actual_cost_cny.toFixed(2)} · 预算估算 ¥{workflow.estimated_cost_cny.toFixed(2)}</small>}
    </div>
    <div className="pipeline-strip">{timeline.map((item, index) => <div key={item.kind} className={`${item.done ? 'done' : ''} ${item.active ? 'active' : ''}`}><span>{item.done ? <Check/> : index + 1}</span><small>{stageNames[item.kind]}</small></div>)}</div>
  </section>
}

function SetupModal({ current, onClose, onSaved }: { current: SetupStatus; onClose: () => void; onSaved: (status: SetupStatus) => void }) {
  const [dashscope, setDashscope] = useState('')
  const [minimaxKey, setMinimaxKey] = useState('')
  const [seedanceKey, setSeedanceKey] = useState('')
  const [provider, setProvider] = useState(current.video_provider === 'seedance-official' ? 'seedance-official' : 'minimax-official')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const save = async () => {
    setBusy(true); setError('')
    try { onSaved(await api.saveSetup({ dashscope_api_key: dashscope, video_provider: provider, minimax_api_key: minimaxKey, seedance_api_key: seedanceKey })) }
    catch (reason) { setError(reason instanceof Error ? reason.message : '保存失败') }
    finally { setBusy(false) }
  }
  return <div className="modal"><section className="setup-modal">
    <div className="modal-title"><span><KeyRound/></span><div><small>CLOUD CONNECTIONS</small><h2>模型与 API 配置</h2></div>{current.ready && <button className="close" onClick={onClose}><X/></button>}</div>
    <p>密钥仅保存在这台电脑的后端配置文件中，不会写入网页代码或展示给其他用户。</p>
    <label>百炼 API Key <em>{current.director_configured ? '已配置 · 留空不修改' : '用于脚本和视觉审核'}</em><input type="password" value={dashscope} onChange={e => setDashscope(e.target.value)} placeholder="sk-..." autoComplete="off"/></label>
    <div className="provider-choice"><b>视频生成服务</b><div><button className={provider === 'minimax-official' ? 'selected' : ''} onClick={() => setProvider('minimax-official')} type="button"><Film/>MiniMax 海螺<small>中国区 · 当前推荐</small></button><button className={provider === 'seedance-official' ? 'selected' : ''} onClick={() => setProvider('seedance-official')} type="button"><Layers3/>Seedance<small>火山方舟接口</small></button></div></div>
    <label>MiniMax 海螺 API Key <em>{current.minimax_configured ? '已配置 · 留空不修改' : '在 MiniMax 开放平台创建'}</em><input type="password" value={minimaxKey} onChange={e => setMinimaxKey(e.target.value)} placeholder="MiniMax API Key" autoComplete="off"/></label>
    <label>Seedance / 火山方舟 API Key <em>{current.seedance_configured ? '已配置 · 留空不修改' : '以 ark- 开头的方舟 Key'}</em><input type="password" value={seedanceKey} onChange={e => setSeedanceKey(e.target.value)} placeholder="ark-..." autoComplete="off"/></label>
    {error && <div className="error-note"><CircleAlert/>{error}</div>}
    <button className="save-setup" disabled={busy || (!current.director_configured && !dashscope) || (provider === 'minimax-official' ? !current.minimax_configured && !minimaxKey : !current.seedance_configured && !seedanceKey)} onClick={() => void save()}>{busy ? <LoaderCircle className="spin"/> : <Check/>}保存配置</button>
  </section></div>
}

export default App
