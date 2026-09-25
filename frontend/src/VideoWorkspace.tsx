import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ArrowRight, Check, ChevronDown, Clock3, Columns2, Download, Film, Layers2, LoaderCircle, Pencil, Play, RefreshCw, Settings2, Shuffle, Sparkles, Star, Video, X } from "lucide-react";
import type { Project } from "./model";
import type { ContinuationRenderPreset } from "./quickPreview";
import { loadContinuationDraft, saveContinuationDraft } from "./continuationDraft";
import "./VideoWorkspace.css";
import LiveRenderProgress from "./LiveRenderProgress";
import UpscaleButton from "./UpscaleButton";
import { useUiLanguage } from "./i18n";

export type VideoJob = {
  id: string;
  request_id?: string;
  project_id: string;
  story_id?: string;
  status: "preparing" | "queued" | "running" | "succeeded" | "failed" | "uncertain";
  stage?: string;
  error?: string | null;
  warning?: string | null;
  seed?: number;
  duration?: number;
  new_seconds?: number | null;
  overlap_frames?: number | null;
  width?: number;
  height?: number;
  steps?: number | null;
  frames?: number | null;
  created_at?: number;
  elapsed_seconds?: number;
  video_url?: string | null;
  scene_video_url?: string | null;
  ending_image_url?: string | null;
  download_url?: string | null;
  continuation_source?: string | null;
  parent_run_id?: string | null;
  has_snapshot?: boolean;
  operation?: "generate" | "reroll" | "continue" | "combine";
  can_reroll?: boolean;
  can_continue?: boolean;
  can_combine?: boolean;
  continue_from_run_id?: string | null;
  title?: string;
  favorite?: boolean;
};

export type VideoWorkspaceProps = {
  project: Project;
  promptReady: boolean;
  busy: boolean | string;
  jobs: VideoJob[];
  currentJob?: VideoJob | null;
  storyId?: string;
  activeEndpointId?: string;
  storyClips?: VideoJob[];
  onBranch?: (job: VideoJob) => void | Promise<void>;
  onPlayGame?: (job: VideoJob) => void | Promise<void>;
  onSelectJob: (job: VideoJob) => void;
  onGenerate: (renderPreset?: ContinuationRenderPreset) => void | Promise<void>;
  onReroll: (job: VideoJob) => void | Promise<void>;
  onContinue: (job: VideoJob, nextIdea: string, duration: number, renderPreset?: ContinuationRenderPreset, options?: { planned?: boolean }) => void | Promise<void>;
  onUpdateTake?: (job: VideoJob, patch: { title?: string; favorite?: boolean }) => void | Promise<void>;
  onSuggest?: (job: VideoJob, duration: number, direction?: string) => Promise<ContinuationSuggestions>;
  onCombine?: (job: VideoJob) => void | Promise<void>;
  onResolve?: (job: VideoJob) => void | Promise<void>;
  advanced?: ReactNode;
};

export type ContinuationSuggestions = {
  suggestions: { title: string; idea: string }[];
  ending_image_url?: string;
  model?: string;
};

type SuggestionContext = { open: boolean; projectId: string; runId?: string; duration: number; direction: string };

/** A late suggestion must never replace another take's or an edited scene's ideas. */
export function continuationSuggestionIsCurrent(request: SuggestionContext, current: SuggestionContext) {
  return request.open && current.open && request.projectId === current.projectId && request.runId === current.runId &&
    request.duration === current.duration && request.direction === current.direction;
}

export function continuationIdeaChoices(result: ContinuationSuggestions) {
  const seen = new Set<string>();
  return (Array.isArray(result?.suggestions) ? result.suggestions : []).filter(item => {
    if (typeof item?.title !== "string" || typeof item?.idea !== "string" || !item.title.trim() || !item.idea.trim()) return false;
    const idea = item.idea.trim();
    if (seen.has(idea)) return false;
    seen.add(idea); return true;
  }).slice(0, 3).map(item => ({ title: item.title.trim(), idea: item.idea.trim() }));
}

export function videoJobIsPending(job?: VideoJob | null) {
  return !!job && ["preparing", "queued", "running", "uncertain"].includes(job.status);
}

export function elapsedVideoTime(seconds?: number) {
  if (!Number.isFinite(seconds) || seconds! < 0) return null;
  const total = Math.floor(seconds!);
  return total < 60 ? `${total}s` : `${Math.floor(total / 60)}m ${String(total % 60).padStart(2, "0")}s`;
}

export function videoTakeTitle(job: VideoJob, number?: number) {
  return job.title?.trim() || (number ? `Take ${number}` : "Selected take");
}

export function continuationSizeLabel(job?: VideoJob | null) {
  const width = job?.width, height = job?.height;
  const known = Number.isInteger(width) && Number.isInteger(height) && width! > 0 && height! > 0;
  const pixels = known ? width! * height! : 0;
  return {
    presetSize: known && pixels >= 260000 && pixels <= 350000 ? "0.3 MP" : "source size",
    explanation: known ? `Continuation keeps this video’s ${width} × ${height} size. Preview presets change steps.` : "Continuation keeps the saved video’s size. Preview presets change steps.",
  };
}

export function videoComparisonChoices(projectId: string, jobs: VideoJob[], selected?: VideoJob | null) {
  if (!selected || selected.project_id !== projectId || selected.status !== "succeeded" || !selected.video_url || selected.operation === "combine") return [];
  const seen = new Set<string>();
  return jobs.filter(job => job.project_id === projectId && job.id !== selected.id && job.status === "succeeded" && !!job.video_url &&
    job.operation !== "combine" && !seen.has(job.id) && !!seen.add(job.id));
}

export function sceneVideoUrl(job?: VideoJob | null) {
  return job?.scene_video_url || job?.video_url || '';
}

/** The caller supplies the accepted clips in story order; no join request is needed. */
export function storyPlaylist(clips: VideoJob[] = [], storyId?: string) {
  const seen = new Set<string>();
  return clips.filter(job => job.status === 'succeeded' && sceneVideoUrl(job) && job.operation !== 'combine' &&
    (!storyId || !job.story_id || job.story_id === storyId) && !seen.has(job.id) && !!seen.add(job.id));
}

export function continuationIsPlanned(idea: string, selectedIdea: string, suggestions?: ContinuationSuggestions | null) {
  return !!selectedIdea && idea === selectedIdea && !!suggestions?.suggestions.some(choice => choice.idea === selectedIdea);
}

/** Every visible result and action belongs to the current project. */
export function videoWorkspaceState(projectId: string, jobs: VideoJob[] = [], selected?: VideoJob | null, busy: boolean | string = false,
  story: {storyId?: string; activeEndpointId?: string} = {}) {
  const seen = new Set<string>();
  const inScope = (job: VideoJob) => story.storyId ? !job.story_id || job.story_id === story.storyId : job.project_id === projectId;
  const takes = jobs.filter(job => job && inScope(job) && job.id && !seen.has(job.id) && !!seen.add(job.id));
  const current = selected && inScope(selected) ? (takes.find(job => job.id === selected.id) || (!story.storyId ? selected : null)) : null;
  // Never silently continue a history preview, or substitute another take for a missing endpoint.
  const source = story.activeEndpointId !== undefined ? takes.find(job => job.id === story.activeEndpointId) || null : story.storyId ? null : current;
  const pending = takes.find(videoJobIsPending) || (videoJobIsPending(current) ? current : null);
  const working = Boolean(busy || pending);
  const playable = current?.status === "succeeded" && !!current.video_url;
  const continuationChain = !!current?.parent_run_id && !!current?.continuation_source &&
    (current.can_combine === true || (current.can_combine === undefined && current.operation === "continue"));
  const verifiedCombinedEnding=source?.operation==='combine'&&source.can_continue===true&&
    typeof source.continue_from_run_id==='string'&&!!source.continue_from_run_id.trim()&&source.continue_from_run_id!==source.id;
  return { takes, current, source, pending, working, playable,
    showCombine: current?.operation === "combine" || continuationChain,
    canReroll: !!playable && !working && current?.has_snapshot !== false && current?.operation !== "combine" && current?.can_reroll !== false,
    canContinue: source?.status === 'succeeded' && !!source.video_url && !working && source.can_continue !== false && (verifiedCombinedEnding ||
      (!!source.continuation_source && source.has_snapshot !== false && source.operation !== "combine")),
    canCombine: !!playable && !working && continuationChain && current?.operation !== "combine" };
}

export default function VideoWorkspace({ project, promptReady, busy, jobs, currentJob, storyId, activeEndpointId, storyClips, onBranch, onPlayGame, onSelectJob, onGenerate, onReroll, onContinue, onSuggest, onCombine, onResolve, onUpdateTake, advanced }: VideoWorkspaceProps) {
  const {text:uiText}=useUiLanguage();
  const t=(zh:string,en:string,ja:string,tw=zh)=>uiText({'zh-CN':zh,'zh-TW':tw,en,ja});
  const statusLabels:Record<VideoJob["status"],string>={
    preparing:t("正在准备视频","Preparing your video","映像を準備中","正在準備影片"),queued:t("等待 ComfyUI","Waiting for ComfyUI","ComfyUI待機中","等待 ComfyUI"),running:t("正在生成视频","Rendering your video","映像を生成中","正在生成影片"),
    succeeded:t("可以观看","Ready to watch","視聴できます","可以觀看"),failed:t("生成已停止","Generation stopped","生成停止","生成已停止"),uncertain:t("正在核对上次请求","Checking the previous request","前回リクエストを確認中","正在核對上次請求"),
  };
  const id = useId();
  const [continuing, setContinuing] = useState(false), [idea, setIdea] = useState(""), [length, setLength] = useState(5);
  const [renderPreset, setRenderPreset] = useState<ContinuationRenderPreset>("inherit");
  const [generatePreset, setGeneratePreset] = useState<ContinuationRenderPreset>("inherit");
  const [draftScope, setDraftScope] = useState(""), [restoredDraft, setRestoredDraft] = useState(false);
  const [renaming, setRenaming] = useState(false), [takeTitle, setTakeTitle] = useState("");
  const [metadataBusy, setMetadataBusy] = useState(false), [metadataError, setMetadataError] = useState("");
  const metadataInFlight = useRef(false), metadataScope = useRef("");
  const [comparing, setComparing] = useState(false), [compareId, setCompareId] = useState("");
  const [submitting, setSubmitting] = useState(false), [actionError, setActionError] = useState("");
  const [suggested, setSuggested] = useState<ContinuationSuggestions | null>(null);
  const [suggesting, setSuggesting] = useState(false), [suggestionError, setSuggestionError] = useState("");
  const [selectedIdea, setSelectedIdea] = useState('');
  const [playback, setPlayback] = useState<'scene' | 'story'>('scene'), [playlistIndex, setPlaylistIndex] = useState(0);
  const [mediaLength, setMediaLength] = useState<{id:string;seconds:number}|null>(null);
  const autoplayNext = useRef(false), directionRef = useRef<HTMLTextAreaElement>(null);
  const suggestionRevision = useRef(0);
  const suggestionInFlight = useRef<number | null>(null);
  const state = videoWorkspaceState(project.id, jobs, currentJob, busy || submitting || suggesting, {storyId, activeEndpointId});
  const { current, source, pending, takes, working, playable, canReroll, canContinue, canCombine, showCombine } = state;
  const hasResult = takes.some(job => job.status === 'succeeded' && !!job.video_url);
  const playlist = storyPlaylist(storyClips, storyId);
  const playing = playback === 'story' ? playlist[Math.min(playlistIndex, Math.max(0, playlist.length - 1))] : current;
  const playingUrl = sceneVideoUrl(playing);
  const endingUrl = source?.ending_image_url || (draftScope === `${source?.project_id}:${source?.id}` ? suggested?.ending_image_url : undefined);
  const sourceProjectId = source?.project_id || project.id;
  const status = pending || current;
  const measuredTime = elapsedVideoTime(status?.elapsed_seconds);
  const runDuration = Number((mediaLength && mediaLength.id===current?.id ? mediaLength.seconds : current?.new_seconds ?? current?.duration ?? project.duration).toFixed(2));
  const dimensions = current?.width && current?.height ? `${current.width} × ${current.height}` : `${project.comfy_render?.resolution || "0.3"} MP`;
  const seed = current?.seed;
  const continuationSize = continuationSizeLabel(source);
  const continuationSizeText=source?.width&&source?.height?t(
    `续写保持当前视频的 ${source.width} × ${source.height} 尺寸；预览预设只改变步数。`,
    `Continuation keeps this video’s ${source.width} × ${source.height} size. Preview presets change steps.`,
    `継続映像は現在の ${source.width} × ${source.height} サイズを維持し、プレビュープリセットはステップ数だけを変更します。`,
    `續寫保持目前影片的 ${source.width} × ${source.height} 尺寸；預覽預設只改變步數。`):t(
    "续写保持已保存视频的尺寸；预览预设只改变步数。","Continuation keeps the saved video’s size. Preview presets change steps.","継続映像は保存済み映像のサイズを維持し、プレビュープリセットはステップ数だけを変更します。","續寫保持已儲存影片的尺寸；預覽預設只改變步數。");
  const generationPresetSize = project.comfy_render?.continuation_source ? "source size" : "0.3 MP";
  metadataScope.current = `${project.id}:${current?.id || ""}`;
  const takeNumber = (job: VideoJob) => Math.max(1, takes.length - takes.findIndex(take => take.id === job.id));
  const compareChoices = videoComparisonChoices(project.id, takes, current);
  const compareTake = compareChoices.find(job => job.id === compareId) || compareChoices[0];
  const updateTake = async (patch: { title?: string; favorite?: boolean }) => {
    if (!onUpdateTake || !current || metadataInFlight.current) return;
    const scope = metadataScope.current;
    metadataInFlight.current = true; setMetadataBusy(true); setMetadataError("");
    try { await onUpdateTake(current, patch); if (scope === metadataScope.current && patch.title !== undefined) setRenaming(false); }
    catch (error) { if (scope === metadataScope.current) setMetadataError(error instanceof Error ? error.message : "Could not save this take. Try again."); }
    finally { metadataInFlight.current = false; setMetadataBusy(false); }
  };
  const suggestionContext = useRef<SuggestionContext>({ open: continuing, projectId: sourceProjectId, runId: source?.id, duration: length, direction: idea });
  suggestionContext.current = { open: continuing, projectId: sourceProjectId, runId: source?.id, duration: length, direction: idea };
  const invalidateSuggestions = () => { suggestionRevision.current += 1; };
  const requestSuggestions = async (duration = length, direction = idea) => {
    if (!onSuggest || !source || working || suggestionInFlight.current !== null) return;
    const revision = ++suggestionRevision.current;
    suggestionInFlight.current = revision;
    const context = { open: true, projectId: sourceProjectId, runId: source.id, duration, direction };
    suggestionContext.current = context;
    setSuggesting(true); setSuggestionError("");
    try {
      const result = await onSuggest(source, duration, direction.trim() || undefined);
      if (revision !== suggestionRevision.current || !continuationSuggestionIsCurrent(context, suggestionContext.current)) return;
      const suggestions = continuationIdeaChoices(result);
      setSuggested({ ...result, suggestions });
      setSelectedIdea('');
      setRestoredDraft(false);
      if (!suggestions.length) setSuggestionError("No scene ideas came back. Refresh ideas or write what happens next below.");
    } catch (error) {
      if (revision === suggestionRevision.current && continuationSuggestionIsCurrent(context, suggestionContext.current))
        setSuggestionError(error instanceof Error ? error.message : "Scene suggestions are unavailable. You can still write your own idea below.");
    } finally {
      if (suggestionInFlight.current === revision) { suggestionInFlight.current = null; setSuggesting(false); }
    }
  };
  const changeIdea = (value: string) => {
    invalidateSuggestions(); suggestionContext.current = { ...suggestionContext.current, direction: value };
    setIdea(value); setSuggestionError("");
  };
  const runAction = async (action: () => void | Promise<void>) => {
    if (working) return;
    setActionError(""); setSubmitting(true);
    try { await action(); } catch (error) { setActionError(error instanceof Error ? error.message : "This action did not finish. Check the connection and try again."); }
    finally { setSubmitting(false); }
  };
  useEffect(() => {
    suggestionRevision.current += 1;
    const restored = source?.id ? loadContinuationDraft(sourceProjectId, source.id) : null;
    setContinuing(false); setIdea(restored?.idea || ""); setLength(Math.min(13, restored?.duration || 5)); setRenderPreset(restored?.renderPreset || "inherit"); setActionError("");
    setSelectedIdea('');
    setSuggested(restored?.suggestions || null); setSuggestionError(""); setRestoredDraft(!!restored);
    setDraftScope(`${sourceProjectId}:${source?.id || ""}`);
  }, [sourceProjectId, source?.id]);
  useEffect(() => {
    if (!source?.id || draftScope !== `${sourceProjectId}:${source.id}` || (!continuing && !idea && !suggested)) return;
    saveContinuationDraft(sourceProjectId, source.id, { idea, duration: length, renderPreset, ...(suggested ? { suggestions: suggested } : {}) });
  }, [draftScope, sourceProjectId, source?.id, continuing, idea, length, renderPreset, suggested]);
  useEffect(() => {
    setRenaming(false); setMetadataError(''); setComparing(false); setCompareId(''); setPlayback('scene'); autoplayNext.current = false;
  }, [current?.id]);
  useEffect(() => { setPlaylistIndex(0); autoplayNext.current = false; }, [storyId]);
  useEffect(() => {
    if (!continuing) return;
    directionRef.current?.focus({preventScroll:true});
    directionRef.current?.closest('.video-workspace-continue')?.scrollIntoView({behavior:'smooth',block:'nearest'});
  }, [continuing]);
  useEffect(() => { setGeneratePreset("inherit"); }, [project.id]);
  useEffect(() => () => { suggestionRevision.current += 1; }, []);

  return <section className="video-workspace" aria-label={t("视频工作区","Video workspace","映像ワークスペース","影片工作區")}>
    <header className="video-workspace-heading">
      <div><span className="video-workspace-eyebrow"><Video size={14} aria-hidden="true" /> {t("你的视频","YOUR VIDEO","あなたの映像","你的影片")}</span><h3>{t("让画面动起来。","Make it move.","映像を動かす。","讓畫面動起來。")}</h3></div>
      <span className="video-workspace-mode">{project.mode?.toUpperCase()}</span>
    </header>

    {current && <div className="video-workspace-take-toolbar">
      <strong className="video-workspace-current-title">{videoTakeTitle(current, takeNumber(current))}</strong>
      <div>{onUpdateTake && <><button type="button" aria-label="Rename selected take" disabled={metadataBusy} onClick={() => { setTakeTitle(current.title || ""); setRenaming(true); setMetadataError(""); }}><Pencil size={14} aria-hidden="true" /></button><button type="button" aria-label={current.favorite ? "Remove take from favorites" : "Favorite this take"} aria-pressed={!!current.favorite} disabled={metadataBusy} onClick={() => void updateTake({ favorite: !current.favorite })}><Star size={15} fill={current.favorite ? "currentColor" : "none"} aria-hidden="true" /></button></>}
      {!!compareChoices.length && <button type="button" aria-expanded={comparing} onClick={() => setComparing(value => !value)}><Columns2 size={14} aria-hidden="true" /> {t("对比版本","Compare takes","テイクを比較","對比版本")}</button>}</div>
    </div>}
    {renaming && current && <form className="video-workspace-rename" onSubmit={event => { event.preventDefault(); void updateTake({ title: takeTitle.trim() }); }}><label htmlFor={`${id}-take-title`}>{t("版本名称","Take name","テイク名","版本名稱")}</label><input id={`${id}-take-title`} value={takeTitle} maxLength={80} placeholder={`${t("版本","Take","テイク","版本")} ${takeNumber(current)}`} onChange={event => setTakeTitle(event.target.value)} disabled={metadataBusy} autoFocus /><button type="submit" disabled={metadataBusy}>{t("保存名称","Save name","名前を保存","儲存名稱")}</button><button type="button" aria-label={t("取消重命名","Cancel rename","名前変更を取消","取消重新命名")} disabled={metadataBusy} onClick={() => setRenaming(false)}><X size={15} aria-hidden="true" /></button></form>}
    {metadataError && <p className="video-workspace-error" role="alert">{metadataError}</p>}

    {!!playlist.length && <div className="video-workspace-playback-tabs" role="group" aria-label={t("播放视图","Playback view","再生表示","播放檢視")}>
      <button type="button" aria-pressed={playback === 'scene' && (!source || current?.id === source.id)} onClick={() => {setPlayback('scene'); autoplayNext.current = false; if(source)onSelectJob(source);}}>{t("最新片段","Latest scene","最新シーン","最新片段")}</button>
      <button type="button" aria-pressed={playback === 'story'} onClick={() => {setPlayback('story'); setPlaylistIndex(0); autoplayNext.current = false;}}>{t("完整故事","Whole story","物語全体","完整故事")} · {playlist.length} {t("段","scenes","シーン","段")}</button>
      {storyId && <a href={`/api/stories/${storyId}/video`} download>{t("保存完整故事","Save whole story","物語全体を保存","儲存完整故事")}</a>}
    </div>}
    {playback === 'story' && playing && <p className="video-workspace-help" role="status">{t(`第 ${Math.min(playlistIndex+1,playlist.length)} / ${playlist.length} 段`,`Scene ${Math.min(playlistIndex+1,playlist.length)} of ${playlist.length}`,`${Math.min(playlistIndex+1,playlist.length)} / ${playlist.length} シーン`,`第 ${Math.min(playlistIndex+1,playlist.length)} / ${playlist.length} 段`)} · {videoTakeTitle(playing)}。{t("按采用片段的顺序连续播放。","Plays the accepted clips in order.","採用したクリップを順番に再生します。","按採用片段的順序連續播放。")}</p>}
    <div className={`video-workspace-preview ${playing?.status === 'succeeded' && playingUrl ? "has-video" : ""}`}>
      {playing?.status === 'succeeded' && playingUrl ? <video key={`${playing.id}:${playingUrl}`} src={playingUrl} controls playsInline preload="metadata" aria-label={t("选中的视频","Selected video","選択中の映像","選取的影片")}
        onEnded={() => {if(playback === 'story' && playlistIndex < playlist.length - 1) {autoplayNext.current = true; setPlaylistIndex(index => index + 1);} else autoplayNext.current = false;}}
        onLoadedMetadata={event => {if(Number.isFinite(event.currentTarget.duration))setMediaLength({id:playing.id,seconds:event.currentTarget.duration});if(autoplayNext.current) {autoplayNext.current = false; void event.currentTarget.play().catch(() => {});}}} /> :
        <div className="video-workspace-empty">
          <div className="video-workspace-preview-icon">{pending && pending.status !== "uncertain" ? <LoaderCircle className="video-workspace-spin" size={30} aria-hidden="true" /> : <Film size={30} aria-hidden="true" />}</div>
          <strong>{status ? statusLabels[status.status] : t("下一段视频从这里开始","Your next video starts here","次の映像はここから始まります","下一段影片從這裡開始")}</strong>
          <p>{pending ? t("可以保持页面打开，完成后结果会显示在这里。","You can keep this page open. Your result will appear here when it is ready.","このページを開いたままにすると、完成後ここに結果が表示されます。","可以保持頁面開啟，完成後結果會顯示在這裡。") : current?.status === "failed" ? t("故事和参考图已保存，请查看下方信息后重新生成。","Your story and references are saved. Review the message below before generating again.","物語と参照画像は保存済みです。下のメッセージを確認して再生成してください。","故事和參考圖已儲存，請查看下方資訊後重新生成。") : t("写下想法、添加图片，然后生成一个版本。","Write your idea, add your photos, then generate a take.","アイデアと画像を用意してテイクを生成します。","寫下想法、新增圖片，然後生成一個版本。")}</p>
        </div>}
    </div>

    <div className="video-workspace-actions">
      {hasResult && <><button className="video-workspace-generate" type="button" disabled={!canContinue} onClick={() => {
        setContinuing(true); setActionError('');
        if (!continuing && !suggested?.suggestions.length) void requestSuggestions();
      }} title={t("从当前故事结尾继续；预览历史版本不会改变续写起点。","Continue the active story ending. Previewing history does not change this source.","現在の物語の終わりから続けます。履歴プレビューで起点は変わりません。","從目前故事結尾繼續；預覽歷史版本不會改變續寫起點。")}><ArrowRight size={17} aria-hidden="true" /><span>{t("从这个结尾继续","Continue from this ending","この終わりから続ける","從這個結尾繼續")}</span></button>
      <button type="button" disabled={!canReroll} onClick={() => current && void runAction(() => onReroll(current))} title={t("保留本版本的提示词、图片和设置，用新种子重新生成。","Keep this take's prompt, photos and settings, and render with a new seed.","このテイクのプロンプト・画像・設定を維持し、新しいSeedで生成します。","保留本版本的提示詞、圖片和設定，用新種子重新生成。")}><Shuffle size={17} aria-hidden="true" /><span>{t("再试一个版本","Try another take","別テイクを試す","再試一個版本")}</span></button></>}
      <button className={!hasResult ? 'video-workspace-generate' : ''} type="button" disabled={working} onClick={() => void runAction(() => onGenerate(generatePreset))}><Play size={17} fill="currentColor" aria-hidden="true" /><span>{t("生成视频","Generate video","映像を生成","生成影片")}</span></button>
    </div>
    {current && ["queued", "running"].includes(current.status) && <LiveRenderProgress runId={current.id}/>}
    {source && <div className="video-workspace-source" aria-label={t("续写起点","Continuation source","継続元","續寫起點")}>
      {endingUrl && <img src={endingUrl} alt={t("用于续写的结束画面","Ending frame used for continuation","継続に使う終了フレーム","用於續寫的結束畫面")} />}
      <div><strong>{t("续写于 ","Continuing after ","次から継続：","續寫於 ")}{videoTakeTitle(source, takeNumber(source))}</strong><span>{source.id !== current?.id ? t('你正在预览历史版本，故事仍从这个结尾继续。','You are previewing history. Your story still continues from this ending.','履歴をプレビュー中です。物語は引き続きこの終わりから続きます。','你正在預覽歷史版本，故事仍從這個結尾繼續。') : t('保存的结束画面和运动会自动带入下一段。','The saved ending and motion carry into the next scene automatically.','保存した終了画面とモーションを次のシーンへ自動で引き継ぎます。','儲存的結束畫面和運動會自動帶入下一段。')}</span></div>
      {onPlayGame && <button type="button" disabled={!canContinue} onClick={() => void runAction(() => onPlayGame(source))}>{t("从这里开始互动","Play from here","ここから再生","從這裡開始互動")}</button>}
    </div>}
    {storyId && !source && <p className="video-workspace-help">{activeEndpointId ? t('正在读取保存的故事结尾，读取完成后即可续写。','Loading the saved story ending. Continuation will be ready when it is available.','保存済みの物語の終わりを読み込み中です。完了後に継続できます。','正在讀取儲存的故事結尾，讀取完成後即可續寫。') : t('先生成开场片段来开始这个故事。','Generate your opening scene to start this story.','冒頭シーンを生成して物語を始めます。','先生成開場片段來開始這個故事。')}</p>}
    {onBranch && current && current.id !== source?.id && <button className="video-workspace-branch" type="button"
      disabled={working || !videoWorkspaceState(current.project_id, [current], current).canContinue}
      onClick={() => void runAction(() => onBranch(current))}>{t("从此预览创建分支","Branch from this preview","このプレビューから分岐","從此預覽建立分支")}</button>}

    {continuing && source && <div className="video-workspace-continue" role="region" aria-label={t("续写选中视频","Continue selected video","選択映像を継続","續寫選取影片")}>
      <div className="video-workspace-continue-heading"><div><span className="video-workspace-eyebrow"><Sparkles size={13} aria-hidden="true" /> {t("互动故事","INTERACTIVE STORY","インタラクティブストーリー","互動故事")}</span><h4>{t("这个结尾之后会发生什么？","What happens after this ending?","この終わりの後、何が起こりますか？","這個結尾之後會發生什麼？")}</h4></div><button type="button" aria-label={t("取消续写","Cancel continuation","継続をキャンセル","取消續寫")} className="video-workspace-close" onClick={() => {
        invalidateSuggestions(); suggestionContext.current = { ...suggestionContext.current, open: false }; setContinuing(false);
      }} disabled={submitting}><X size={17} aria-hidden="true" /></button></div>
      <p>{t("选择一个下一幕，也可以自行修改，然后让故事接着演。上一段的结尾画面和故事会自动带入，原版本会一直保留。","Pick a next scene, edit it if you want, then watch the story continue. The last frame and story come from this video automatically. Your original take stays saved.","次のシーンを選び、必要なら編集して物語を続けます。直前の終了画面と物語は自動で引き継がれ、元のテイクも保存されます。","選擇一個下一幕，也可以自行修改，然後讓故事接著演。上一段的結尾畫面和故事會自動帶入，原版本會一直保留。")}</p>
      {restoredDraft && <p className="video-workspace-settings-note" role="status">{t("已恢复保存的场景草稿；刷新建议可获得新选项。","Saved scene draft restored. Refresh ideas for new choices.","保存済みシーン下書きを復元しました。新しい候補は提案を更新してください。","已恢復儲存的場景草稿；重新整理建議可取得新選項。")}</p>}
      {endingUrl && <figure className="video-workspace-ending"><img src={endingUrl} alt={t("选中视频的实际最后一帧","Actual last frame of the selected video","選択映像の実際の最終フレーム","選取影片的實際最後一幀")} /><figcaption><strong>{t("续写起点","Your starting point","継続の起点","續寫起點")}</strong><span>{t("规划下一段时会自动加入这张结束画面。","This ending frame is included automatically when planning the next clip.","次のクリップを計画する際、この終了フレームが自動で含まれます。","規劃下一段時會自動加入這張結束畫面。")}</span></figcaption></figure>}
      {onSuggest && <div className="video-workspace-suggestions" aria-label={t("下一幕建议","Suggested next scenes","次シーンの提案","下一幕建議")}>
        <div className="video-workspace-suggestions-heading"><strong>{t(`接下来 ${length} 秒的构思`,`Ideas for the next ${length} seconds`,`次の${length}秒のアイデア`,`接下來 ${length} 秒的構思`)}</strong><button type="button" disabled={working || suggesting} onClick={() => void requestSuggestions()}><RefreshCw size={13} aria-hidden="true" /> {t("刷新建议","Refresh ideas","提案を更新","重新整理建議")}</button></div>
        {suggesting && <p className="video-workspace-suggestion-status" role="status"><LoaderCircle size={14} className="video-workspace-spin" aria-hidden="true" /> {t("正在分析结尾并构思下一幕…","Looking at the ending and thinking of next scenes…","終了画面を見て次シーンを考えています…","正在分析結尾並構思下一幕…")}</p>}
        {suggestionError && <p className="video-workspace-suggestion-error" role="alert">{suggestionError}</p>}
        {!!suggested?.suggestions.length && <div className="video-workspace-idea-grid">{suggested.suggestions.map((choice, index) => <button type="button" key={`${index}:${choice.idea}`} disabled={working} aria-pressed={idea === choice.idea && selectedIdea === choice.idea} onClick={() => { setSelectedIdea(choice.idea); changeIdea(choice.idea); }}><span className="video-workspace-idea-number">{index + 1}</span><strong>{choice.title}</strong><span>{choice.idea}</span></button>)}</div>}
        {suggested?.model && <small className="video-workspace-suggestion-model">{t("建议模型：","Ideas by ","提案モデル：","建議模型：")}{suggested.model}</small>}
      </div>}
      <label htmlFor={`${id}-idea`}>{t("接下来发生什么？","What happens next?","次に何が起こりますか？","接下來發生什麼？")}</label>
      <textarea ref={directionRef} id={`${id}-idea`} value={idea} onChange={event => changeIdea(event.target.value)} rows={3} maxLength={1000} disabled={working && !suggesting} placeholder={t("她打开盒子，微笑着转向窗边。保持同一房间和一个连续镜头。","She opens the box, smiles, and turns toward the window. Keep the same room and one continuous shot.","彼女は箱を開け、微笑んで窓の方を向く。同じ部屋と連続したワンショットを維持する。","她打開盒子，微笑著轉向窗邊。保持同一房間和一個連續鏡頭。")} />
      <label className="video-workspace-preset" htmlFor={`${id}-continue-preset`}><span>{t("续写质量","Continuation quality","継続品質","續寫品質")}<small>{t("快速草稿用于检查运动，质量预览会增加细节。","Quick draft checks motion. Quality preview adds detail.","高速下書きでモーションを確認し、品質プレビューで詳細を増やします。","快速草稿用於檢查運動，品質預覽會增加細節。")}</small></span><select id={`${id}-continue-preset`} aria-label={t("续写质量","Continuation quality","継続品質","續寫品質")} value={renderPreset} onChange={event => setRenderPreset(event.target.value as ContinuationRenderPreset)} disabled={working}><option value="inherit">{t("沿用本版本设置","This take’s settings","このテイクの設定","沿用本版本設定")}</option><option value="draft">{t("快速草稿","Quick draft","高速下書き","快速草稿")} · {continuationSize.presetSize} / 4 {t("步","steps","ステップ","步")}</option><option value="quality">{t("质量预览","Quality preview","品質プレビュー","品質預覽")} · {continuationSize.presetSize} / 8 {t("步","steps","ステップ","步")}</option></select></label>
      <p className="video-workspace-settings-note">{continuationSizeText}</p>
      <p className="video-workspace-settings-note">{t("除非选择预览预设，否则会沿用本版本已保存的视频设置，并保持你选择的片段时长。","Uses this take’s saved video settings unless you choose a preview preset. Keeps the clip length you select.","プレビュープリセットを選ばない限り、このテイクの保存済み映像設定と選択したクリップ長を使います。","除非選擇預覽預設，否則會沿用本版本已儲存的影片設定，並保持你選擇的片段時長。")}</p>
      <div className="video-workspace-continue-footer"><label htmlFor={`${id}-length`}><span>{t("新增动作时长","New action length","新しいアクションの長さ","新增動作時長")}</span><select id={`${id}-length`} aria-label={t("下一段时长","Next clip length","次クリップの長さ","下一段時長")} value={length} onChange={event => {
        const duration = Number(event.target.value); invalidateSuggestions(); setLength(duration); setSelectedIdea('');
        suggestionContext.current = { ...suggestionContext.current, duration };
        const restored = loadContinuationDraft(sourceProjectId, source.id, { duration });
        if (restored) {
          setIdea(restored.idea); setRenderPreset(restored.renderPreset); setSuggested(restored.suggestions || null); setRestoredDraft(true);
          suggestionContext.current = { ...suggestionContext.current, direction: restored.idea };
          return;
        }
        setSuggested(value => value ? { ...value, suggestions: [] } : null);
        setRestoredDraft(false);
        void requestSuggestions(duration);
      }} disabled={working}>{[4, 5, 7, 10, 13].map(seconds => <option key={seconds} value={seconds}>{seconds===4?t("4 秒 · 快速测试","4 seconds · quick test","4秒・高速テスト","4 秒 · 快速測試"):t(`${seconds} 秒新动作`,`${seconds} seconds of new action`,`${seconds}秒の新しいアクション`,`${seconds} 秒新動作`)}</option>)}</select></label><button className="video-workspace-generate" type="button" disabled={!canContinue || !idea.trim()} onClick={() => {
        invalidateSuggestions(); void runAction(() => onContinue(source, idea.trim(), length, renderPreset, {planned:continuationIsPlanned(idea, selectedIdea, suggested)}));
      }}><ArrowRight size={16} aria-hidden="true" /> {t(`生成接下来的 ${length} 秒`,`Generate next ${length} seconds`,`次の${length}秒を生成`,`生成接下來的 ${length} 秒`)}</button></div>
      <small>{continuationIsPlanned(idea, selectedIdea, suggested) ? t("选中的建议已可生成。","Your selected suggestion is ready to render.","選択した提案を生成できます。","選取的建議已可生成。") : t("生成前，AI 会把你的方向扩展为动作和对白。","AI develops your direction into actions and dialogue before rendering.","生成前にAIが指示を動作と台詞へ展開します。","生成前，AI 會把你的方向擴展為動作和對白。")} {t("该时长是保存片头之后新增的动作；H3 会取整到支持的帧数，“最新片段”播放会跳过复制的片头。","This length adds new action after the saved opening. H3 rounds to supported frames; Latest scene skips the copied opening.","この長さは保存済み冒頭の後に追加する動作です。H3は対応フレーム数に丸め、「最新シーン」では複製された冒頭を飛ばします。","該時長是儲存片頭之後新增的動作；H3 會取整到支援的影格數，「最新片段」播放會跳過複製的片頭。")}</small>
    </div>}


    {comparing && current && compareTake && <section className="video-workspace-compare" aria-label={t("对比选中版本","Compare selected takes","選択テイクを比較","對比選取版本")}>
      <div className="video-workspace-compare-heading"><label htmlFor={`${id}-compare`}>{t("对比对象","Compare with","比較対象","對比對象")}<select id={`${id}-compare`} aria-label={t("对比对象","Compare with","比較対象","對比對象")} value={compareTake.id} onChange={event => setCompareId(event.target.value)}>{compareChoices.map(job => <option key={job.id} value={job.id}>{videoTakeTitle(job, takeNumber(job))}</option>)}</select></label><button type="button" aria-label={t("关闭对比","Close comparison","比較を閉じる","關閉對比")} onClick={() => setComparing(false)}><X size={16} aria-hidden="true" /></button></div>
      <div className="video-workspace-compare-grid">{[current, compareTake].map((job, index) => <figure key={`${index}:${job.id}`}><video src={job.video_url!} controls playsInline preload="metadata" aria-label={index === 0 ? "Selected take comparison" : "Other take comparison"} /><figcaption><strong>{videoTakeTitle(job, takeNumber(job))}</strong><span>{job.steps && job.steps > 0 ? `${job.steps} steps · ` : ""}{Number.isSafeInteger(job.seed) ? `Seed ${job.seed}` : ""}</span></figcaption></figure>)}</div>
      <p>{t("分别播放两个版本来比较动作和细节；当前采用版本不会改变。","Play either take to compare the action and detail. Your selected take stays the same.","両方のテイクを再生して動作と細部を比較できます。選択中のテイクは変わりません。","分別播放兩個版本來比較動作和細節；目前採用版本不會改變。")}</p>
    </section>}

    <div className="video-workspace-result-info">
      <div className="video-workspace-badges" aria-label={t("视频详情","Video details","映像詳細","影片詳情")}>{current?.operation === "combine" && <span>{t("合并成片","Combined film","結合映像","合併成片")}</span>}<span>{runDuration}s</span><span>{dimensions}</span>{current?.steps != null && current.steps > 0 && <span>{current.steps} {t("步","steps","ステップ","步")}</span>}{Number.isSafeInteger(seed) && <span>Seed {seed}</span>}</div>
      {playable && current?.download_url && <a className="video-workspace-download" href={current.scene_video_url || current.download_url} download><Download size={14} aria-hidden="true" /> {t("保存场景","Save scene","シーンを保存","儲存場景")}</a>}
    </div>

    <UpscaleButton runId={playable ? current?.id : undefined}/>
    {(status || busy || submitting) && <div className={`video-workspace-status ${status?.status === "failed" || status?.status === "uncertain" ? "needs-attention" : ""}`} role="status" aria-live="polite">
      {pending || busy || submitting ? <LoaderCircle size={15} className={status?.status === "uncertain" ? "" : "video-workspace-spin"} aria-hidden="true" /> : status?.status === "succeeded" ? <Check size={15} aria-hidden="true" /> : <RefreshCw size={15} aria-hidden="true" />}
      <div><strong>{busy && !pending ? (typeof busy === "string" ? busy : t("正在准备视频","Preparing your video","映像を準備中","正在準備影片")) : submitting && !pending ? t("正在提交请求","Starting your request","リクエストを開始中","正在提交請求") : status ? statusLabels[status.status] : t("正在准备视频","Preparing your video","映像を準備中","正在準備影片")}</strong>
        {status?.stage && <span>{status.stage}</span>}
        {status?.status === "uncertain" && <span>{t("Studio 正在确认 ComfyUI 是否收到上次请求，确认前不会重复提交。","Studio is checking whether ComfyUI received the request before allowing another generation.","StudioはComfyUIがリクエストを受信したか確認中です。確認までは再生成できません。","Studio 正在確認 ComfyUI 是否收到上次請求，確認前不會重複提交。")}</span>}
      </div>
      {measuredTime && <span className="video-workspace-elapsed"><Clock3 size={12} aria-hidden="true" /> {measuredTime}</span>}
    </div>}
    {(actionError || current?.error || pending?.error) && <p className="video-workspace-error" role="alert">{actionError || pending?.error || current?.error}</p>}
    {current?.warning && <p className="video-workspace-help" role="status">{current.warning}</p>}
    {status?.status==='uncertain' && onResolve && <div className="video-workspace-combine"><button type="button" disabled={!!busy||submitting} onClick={async()=>{
      setSubmitting(true);setActionError('');
      try{await onResolve(status);}catch(error){setActionError((error as Error).message);}finally{setSubmitting(false);}
    }}><RefreshCw size={15}/> {t("核对并解锁","Check & unlock","確認して解除","核對並解鎖")}</button><span>{t("如有结果会尝试恢复；若任务已不再运行，则允许重新生成。已有视频会保留。","Recover its result if available. If this request is no longer running, unlock new generations. Existing videos are kept.","結果があれば復元し、実行中でなければ新しい生成を許可します。既存映像は保持されます。","如有結果會嘗試恢復；若任務已不再執行，則允許重新生成。已有影片會保留。")}</span></div>}

    <label className="video-workspace-preset" htmlFor={`${id}-generate-preset`}><span>{t("视频质量","Video quality","映像品質","影片品質")}<small>{project.comfy_render?.continuation_source ? t("沿用保存片段尺寸；预览档只改变步数。","Uses the saved clip size; preview presets change steps.","保存クリップのサイズを使用し、プレビュー設定はステップ数だけを変えます。","沿用儲存片段尺寸；預覽檔只改變步數。") : t("用于“生成视频”。","Applies to Generate video.","「映像を生成」に適用します。","用於「生成影片」。")}</small></span><select id={`${id}-generate-preset`} aria-label="Video quality" value={generatePreset} onChange={event => setGeneratePreset(event.target.value as ContinuationRenderPreset)} disabled={working}><option value="inherit">{t("当前视频设置","Current video settings","現在の映像設定","目前影片設定")}</option><option value="draft">{t("快速草稿","Quick draft","高速ドラフト","快速草稿")} · {generationPresetSize} / 4 {t("步","steps","ステップ","步")}</option><option value="quality">{t("质量预览","Quality preview","品質プレビュー","品質預覽")} · {generationPresetSize} / 8 {t("步","steps","ステップ","步")}</option></select></label>
    {onCombine && showCombine && <div className="video-workspace-combine"><button type="button" disabled={!canCombine} onClick={() => current && void runAction(() => onCombine(current))}><Layers2 size={15} aria-hidden="true" /> {t("合并片段","Combine clips","クリップを結合","合併片段")}</button><span>{current?.operation === "combine" ? current.can_continue===true&&current.continue_from_run_id ? t("合片已在播放器中就绪；“从这个结尾继续”会自动从最后一段接续。","Your joined film is ready in the player. Continue from this ending picks up from the final clip automatically.","結合映像の準備ができました。「この終わりから続ける」は最後のクリップから自動で続きます。","合片已在播放器中就緒；「從這個結尾繼續」會自動從最後一段接續。") : t("合片已在播放器中就绪；请选择单独版本来续写它的故事。","Your joined film is ready in the player. Select an individual take to continue its story.","結合映像の準備ができました。物語を続けるには個別テイクを選んでください。","合片已在播放器中就緒；請選擇單獨版本來續寫它的故事。") : t("把本次续写和前面的片段合并为一个视频。","Join this continuation with its earlier clips into one video.","今回の継続と前のクリップを1本の映像に結合します。","把本次續寫和前面的片段合併為一個影片。")}</span></div>}
    <p className="video-workspace-help">{!promptReady ? t("生成时会先完成提示词，再渲染视频。","Generate makes your prompt first, then renders your video.","生成時にまずプロンプトを作り、その後映像をレンダリングします。","生成時會先完成提示詞，再渲染影片。") : t("生成会使用当前提示词与设置。","Generate uses your current prompt and settings.","現在のプロンプトと設定で生成します。","生成會使用目前提示詞與設定。")} {playable ? t("“再试一个版本”会保留已选版本的提示词、图片和设置。","Try another take keeps the selected take’s prompt, photos and settings.","別テイクでは選択中テイクのプロンプト・画像・設定を維持します。","「再試一個版本」會保留已選版本的提示詞、圖片和設定。") : t("完成后可换种子对比，或继续故事。","After a take finishes, compare a new seed or continue its story.","完成後は別Seedと比較するか、物語を続けられます。","完成後可換種子對比，或繼續故事。")}</p>
    {playable && !current?.continuation_source && !current?.warning && current?.operation !== "combine" && <p className="video-workspace-help">{t("这个版本没有保存运动状态。想续写的视频需要在生成设置中开启“保存续写状态”。","This take has no saved motion state. Enable Save continuation state in generation settings for videos you want to extend.","このテイクにはモーション状態が保存されていません。継続したい映像は生成設定で「継続状態を保存」を有効にしてください。","這個版本沒有儲存運動狀態。想續寫的影片需要在生成設定中開啟「儲存續寫狀態」。")}</p>}

    {takes.length > 0 && <div className="video-workspace-takes"><div className="video-workspace-takes-heading"><h4>{t("近期版本","Recent takes","最近のテイク","近期版本")}</h4><span>{takes.length} {t("个生成记录","saved runs","件の保存済み生成","個生成記錄")}</span></div><div className="video-workspace-takes-strip" aria-label="Recent video takes">
      {takes.map((job, index) => <button type="button" key={job.id} className={`video-workspace-take ${current?.id === job.id ? "selected" : ""}`} aria-label={`Select take ${takes.length - index}`} aria-pressed={current?.id === job.id} onClick={() => onSelectJob(job)}>
        <span className={`video-workspace-take-icon ${job.status}`}><span>{job.favorite ? <Star size={16} fill="currentColor" aria-label="Favorite take" /> : job.status === "succeeded" ? <Play size={16} aria-hidden="true" /> : videoJobIsPending(job) ? <Clock3 size={16} aria-hidden="true" /> : <RefreshCw size={16} aria-hidden="true" />}</span><strong>{videoTakeTitle(job, takes.length - index)}</strong>{current?.id === job.id && <Check size={13} aria-hidden="true" />}</span>
        <span className="video-workspace-take-status">{job.status === "succeeded" ? job.operation === "combine" ? t("已合并成片","Combined film","結合済み映像","已合併成片") : t("已完成","Ready","完了","已完成") : statusLabels[job.status]}</span><small>{job.operation === "combine" ? t("合并视频","Joined video","結合映像","合併影片") : Number.isSafeInteger(job.seed) ? `Seed ${job.seed}` : t("等待 Seed","Seed pending","Seed待機中","等待 Seed")}{job.duration ? ` · ${Number(job.duration.toFixed(2))}s` : ""}</small>
      </button>)}
    </div></div>}

    {playable && current?.video_url && <details className="video-workspace-raw-details"><summary>{t("片段详情与原始输出","Clip details & original output","クリップ詳細・元出力","片段詳情與原始輸出")}</summary>
      <p>{current.scene_video_url ? 'Scene playback uses the new footage. The original output can include preserved motion from the previous clip.' : 'This is the full generated clip. Saved-state continuations can include a short preserved opening from the previous clip.'}</p>
      <a href={current.video_url} target="_blank" rel="noreferrer">{t("查看原始生成片段","View original generated clip","元の生成クリップを見る","查看原始生成片段")}</a>
      {current.parent_run_id && <p>The parent take is saved with this clip. Branches keep their own source ending.</p>}
    </details>}
    {advanced && <details className="video-workspace-advanced"><summary><Settings2 size={15} aria-hidden="true" /><span>{t("生成设置","Generation settings","生成設定","生成設定")}</span><ChevronDown size={15} aria-hidden="true" /></summary><div>{advanced}</div></details>}
  </section>;
}
