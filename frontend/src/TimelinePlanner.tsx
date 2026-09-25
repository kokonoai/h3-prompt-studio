import { useEffect, useState } from "react";
import type { Project } from "./model";
import { setDirectorValue } from "./shotDirections";
import {
  continuationEnding, createContinuation, formatTime, setContinuousTake,
  setTimelineBoundary, splitSceneAt, splitTimelineAt, timelineRanges,
  twoScenesInFirstFiveSeconds,
} from "./timelineHelpers";
import "./TimelinePlanner.css";
import { useUiLanguage } from "./i18n";

type Update = (fn: (draft: Project) => void) => void;
type TimelineProps = { project: Project; update: Update; checkpointUpdate?: Update };

function BoundaryInput({ value, min, max, label, onApply }: {
  value: number; min: number; max: number; label: string; onApply: (value: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  const apply = () => {
    if (draft !== "" && Number(draft) !== value) onApply(Number(draft));
    setDraft(String(value));
  };
  return <input type="number" min={min} max={max} step={0.25} aria-label={label}
    value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={apply}
    onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); e.currentTarget.blur(); } }} />;
}

export function TimelinePlanner({ project: p, update, checkpointUpdate }: TimelineProps) {
  const {text:uiText}=useUiLanguage();
  const t=(zh:string,en:string,ja:string,tw=zh)=>uiText({'zh-CN':zh,'zh-TW':tw,en,ja});
  const change = checkpointUpdate || update;
  const ranges = timelineRanges(p);
  const [splitAt, setSplitAt] = useState("2.5");
  const [transition, setTransition] = useState<"cut" | "continuous">("cut");
  const [error, setError] = useState("");
  useEffect(() => { setError(""); setSplitAt(String(Math.min(2.5, p.duration / 2))); }, [p.id]);
  const apply = (fn: (draft: Project) => void) => {
    try {
      // Validate before the React state updater so an invalid entry stays inline.
      fn(structuredClone(p));
      change(fn);
      setError("");
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
  };
  const continuous = p.shots.every((shot, i) => i === 0 || shot.transition === "continuous");
  return <details className="timeline-planner">
    <summary>{t("时序与连续拍摄","Timing & continuous filming","タイミング・連続撮影","時序與連續拍攝")} <span>0–{formatTime(p.duration)} {t("秒","seconds","秒","秒")}</span></summary>
    <p className="timeline-help">{t("设置每个分镜的起止时间。前 5 秒内也可以安排两个分镜；一个连续镜头可包含多个定时动作而不切镜。","Choose when each scene starts and ends. Two scenes can fit inside the first five seconds. A continuous take can have several timed actions without a cut.","各シーンの開始・終了時間を設定します。最初の5秒に2シーンを入れることもでき、連続テイクならカットせず複数の時間指定アクションを配置できます。","設定每個分鏡的起止時間。前 5 秒內也可以安排兩個分鏡；一個連續鏡頭可包含多個定時動作而不切鏡。")}</p>
    <div className="timeline-track" aria-label={t("分镜时间线","Scene timeline","シーンタイムライン","分鏡時間軸")}>
      {ranges.map((range) => <div key={range.id} className="timeline-segment" style={{ flexGrow: range.duration }}>
        <strong>{t("分镜","Scene","シーン","分鏡")} {range.index + 1}</strong><span>{formatTime(range.start)}–{formatTime(range.end)}s</span>
      </div>)}
    </div>
    <div className="timeline-shortcuts">
      <button type="button" onClick={() => apply(setContinuousTake)}>{t("一个连续镜头 · 不切镜","One continuous take · no cuts","1つの連続テイク・カットなし","一個連續鏡頭 · 不切鏡")}</button>
      {p.shots.length === 1 && <button type="button" onClick={() => apply(twoScenesInFirstFiveSeconds)}>{t(`前 ${Math.min(5,p.duration)} 秒内安排 2 个分镜`,`2 scenes in the first ${Math.min(5,p.duration)} seconds`,`最初の${Math.min(5,p.duration)}秒に2シーン`,`前 ${Math.min(5,p.duration)} 秒內安排 2 個分鏡`)}</button>}
    </div>
    {continuous && p.shots.length > 1 && <p className="timeline-state">{t("摄影机会跨越这些剧情节拍持续拍摄；对白和运镜选择保持不变。","The camera keeps filming across these scene beats. Your dialogue and camera choices stay in place.","カメラはこれらのビートを通して撮影を続け、台詞とカメラ選択は維持されます。","攝影機會跨越這些劇情節拍持續拍攝；對白和運鏡選擇保持不變。")}</p>}
    <div className="timeline-rows">
      {ranges.map((range, i) => <div className="timeline-row" key={range.id}>
        <strong>{t("分镜","Scene","シーン","分鏡")} {i + 1}</strong>
        <label><span>{t("开始于","Starts at","開始","開始於")}</span><output>{formatTime(range.start)}s</output></label>
        <label><span>{t("结束于","Ends at","終了","結束於")}</span>{i < ranges.length - 1
          ? <BoundaryInput label={t(`分镜 ${i+1} 结束于`,`Scene ${i+1} ends at`,`シーン${i+1}の終了`,`分鏡 ${i+1} 結束於`)} value={range.end} min={range.start + 0.25} max={ranges[i + 1].end - 0.25}
            onApply={(value) => apply((d) => setTimelineBoundary(d, ranges[i + 1].id, value))} />
          : <output>{formatTime(range.end)}s</output>}</label>
        {i > 0 ? <label className="timeline-cut"><span>{t("与上一分镜的衔接","From previous scene","前シーンから","與上一分鏡的銜接")}</span><select aria-label={t(`分镜 ${i+1} 转场`,`Timeline scene ${i+1} change`,`シーン${i+1}の切替`,`分鏡 ${i+1} 轉場`)}
          value={p.shots[i].transition === "continuous" ? "continuous" : "cut"}
          onChange={(e) => apply((d) => { setDirectorValue(d, range.id, "transition", e.target.value); if (e.target.value === "cut") (d.simple ??= {}).continuous_take = false; })}>
          <option value="cut">{t("切到这个分镜","Cut to this scene","このシーンへカット","切到這個分鏡")}</option><option value="continuous">{t("继续拍摄 · 不切镜","Keep filming · no cut","撮影継続・カットなし","繼續拍攝 · 不切鏡")}</option>
        </select></label> : <span className="timeline-first">{t("视频从这里开始","The video starts here","映像はここから開始","影片從這裡開始")}</span>}
        <button type="button" disabled={p.shots.length >= 6 || range.duration < 0.5} aria-label={t(`把分镜 ${i+1} 一分为二`,`Split scene ${i+1} in two`,`シーン${i+1}を2分割`,`把分鏡 ${i+1} 一分為二`)}
          onClick={() => apply((d) => splitSceneAt(d, range.id, (range.start + range.end) / 2, transition))}>{t("一分为二","Split in two","2分割","一分為二")}</button>
      </div>)}
    </div>
    <div className="timeline-split">
      <label><span>{t("在第几秒新增分镜","Add a scene at (seconds)","シーン追加位置（秒）","在第幾秒新增分鏡")}</span><input type="number" aria-label={t("新增分镜时间","Add scene at seconds","シーン追加時間","新增分鏡時間")} min={0.25} max={p.duration - 0.25} step={0.25} value={splitAt} onChange={(e) => setSplitAt(e.target.value)} /></label>
      <label><span>{t("新分镜如何开始","How it starts","開始方法","新分鏡如何開始")}</span><select aria-label={t("新分镜转场","New scene transition","新シーンの切替","新分鏡轉場")} value={transition} onChange={(e) => setTransition(e.target.value as "cut" | "continuous")}>
        <option value="cut">{t("切到新镜头","Cut to a new shot","新しいショットへカット","切到新鏡頭")}</option><option value="continuous">{t("继续拍摄 · 新动作","Keep filming · new action","撮影継続・新しい動作","繼續拍攝 · 新動作")}</option>
      </select></label>
      <button type="button" disabled={!splitAt || p.shots.length >= 6} onClick={() => apply((d) => splitTimelineAt(d, Number(splitAt), transition))}>{t("在这里新增分镜","Add scene here","ここにシーンを追加","在這裡新增分鏡")}</button>
    </div>
    <p className="timeline-help">{t("修改结束时间只会调整下一分镜。拆分后对白留在原分镜，请在新卡片中补写后续动作；每段最长 15 秒。","Changing an end time adjusts only the next scene. Splitting keeps spoken lines in the original scene; add the next action in its new card below. Each clip is up to 15 seconds.","終了時間の変更は次のシーンだけを調整します。分割後も台詞は元のシーンに残るため、新カードに次の動作を追加してください。各クリップは最長15秒です。","修改結束時間只會調整下一分鏡。拆分後對白留在原分鏡，請在新卡片中補寫後續動作；每段最長 15 秒。")}</p>
    {error && <p className="timeline-error" role="alert">{error}</p>}
  </details>;
}

type ContinuationProps = { project: Project; update: Update; onContinue: (next: Project) => void; busy?: boolean };
export function ContinuationPlanner({ project: p, update, onContinue, busy = false }: ContinuationProps) {
  const {text:uiText}=useUiLanguage();
  const t=(zh:string,en:string,ja:string,tw=zh)=>uiText({'zh-CN':zh,'zh-TW':tw,en,ja});
  const saved = p.simple?.next_clip_draft || {};
  const request = saved.request || "";
  const ending = saved.ending ?? continuationEnding(p);
  const duration = Number(saved.duration || 15);
  const firstFrameAssetId = saved.first_frame_asset_id || "";
  const [error, setError] = useState("");
  const priorStart = Number(p.simple?.continuation?.sequence_start || 0);
  const nextStart = priorStart + p.duration;
  const images = p.assets.filter((asset) => asset.media_type === "image");
  const set = (key: string, value: string | number) => update((d) => { ((d.simple ??= {}).next_clip_draft ??= {})[key] = value; });
  useEffect(() => setError(""), [p.id]);
  return <details className="timeline-planner continuation-planner">
    <summary>{t("规划独立的下一幕","Plan a separate scene","次の独立シーンを計画","規劃獨立的下一幕")} <span>{t(`接下来 ${duration} 秒`,`next ${duration} seconds`,`次の${duration}秒`,`接下來 ${duration} 秒`)}</span></summary>
    {p.simple?.continuation && <p className="timeline-state">{t(`这是长故事的第 ${p.simple.continuation.segment_index} 段，覆盖 ${formatTime(priorStart)}–${formatTime(priorStart+p.duration)} 秒。`,`This is clip ${p.simple.continuation.segment_index}, covering ${formatTime(priorStart)}–${formatTime(priorStart+p.duration)}s of your longer story.`,`長編ストーリーのクリップ${p.simple.continuation.segment_index}で、${formatTime(priorStart)}〜${formatTime(priorStart+p.duration)}秒を扱います。`,`這是長故事的第 ${p.simple.continuation.segment_index} 段，涵蓋 ${formatTime(priorStart)}–${formatTime(priorStart+p.duration)} 秒。`)}</p>}
    <p className="timeline-help">{t("保留当前项目，使用相同角色、服装、图片标签和风格创建独立下一段，再用“生成我的提示词”展开后续。","Keep this project and start a separate next clip with the same people, clothes, photo tags and style. Then use “Make my prompt” to develop what happens next.","現在のプロジェクトを残し、同じ人物・衣装・画像タグ・スタイルで独立した次クリップを作成し、「プロンプトを作成」で続きを展開します。","保留目前專案，使用相同角色、服裝、圖片標籤和風格建立獨立下一段，再用「生成我的提示詞」展開後續。")}</p>
    <div className="continuation-fields">
      <label><span>{t("这一段在哪里结束？","Where does this clip finish?","このクリップの終了状態","這一段在哪裡結束？")}</span><textarea aria-label={t("上一段结束状态","Previous clip ending","前クリップの終了状態","上一段結束狀態")} rows={2} value={ending} placeholder={t("例如：诺拉拿着盒子；两人站在门边","e.g. Nora now holds the box; both women stand by the door","例：ノラが箱を持ち、二人は扉のそばに立つ","例如：諾拉拿著盒子；兩人站在門邊")} onChange={(e) => set("ending", e.target.value)} /></label>
      <label><span>{t("接下来发生什么？","What happens next?","次に何が起こる？","接下來發生什麼？")}</span><textarea aria-label={t("下一段创意","Next clip idea","次クリップのアイデア","下一段創意")} rows={2} value={request} placeholder={t("例如：诺拉打开盒子，米拉微笑，然后两人走向窗边","e.g. Nora opens the box, Mira smiles, then they walk toward the window","例：ノラが箱を開け、ミラが微笑み、二人で窓へ歩く","例如：諾拉打開盒子，米拉微笑，然後兩人走向窗邊")} onChange={(e) => set("request", e.target.value)} /></label>
      <div className="continuation-settings">
        <label><span>{t("下一段时长","Next clip length","次クリップの長さ","下一段時長")}</span><select aria-label={t("下一段时长","Next clip length","次クリップの長さ","下一段時長")} value={duration} onChange={(e) => set("duration", Number(e.target.value))}>
          {[5, 7, 10, 15].map((n) => <option value={n} key={n}>{n} {t("秒","seconds","秒","秒")}</option>)}
        </select></label>
        <label><span>{t("上一视频的最后一帧 · 可选","Previous video's last frame · optional","前映像の最終フレーム・任意","上一影片的最後一幀 · 可選")}</span><select aria-label={t("下一段起始帧","Next clip starting frame","次クリップの開始フレーム","下一段起始幀")} value={firstFrameAssetId} onChange={(e) => set("first_frame_asset_id", e.target.value)}>
          <option value="">{t("使用参考图和连续性备注","Use references and continuity notes","参照画像と連続性メモを使用","使用參考圖和連續性備註")}</option>
          {images.map((a) => <option value={a.id} key={a.id}>{a.name}</option>)}
        </select></label>
      </div>
    </div>
    <p className="timeline-help">{t("要提高视觉衔接度，请把成片的实际最后一帧加入“照片”并在这里选择。否则只会继承故事和参考图，不能保证无缝拼接。","For a closer visual match, add the actual last frame of your finished video to Photos and select it here. Otherwise this carries the story and references; it does not guarantee a seamless join.","見た目をより合わせるには、完成映像の実際の最終フレームを「写真」に追加して選択します。それ以外は物語と参照だけを引き継ぎ、継ぎ目のない結合は保証しません。","要提高視覺銜接度，請把成片的實際最後一幀加入「照片」並在這裡選擇。否則只會繼承故事和參考圖，不能保證無縫拼接。")}</p>
    <p className="timeline-help">{t("下一段会从空对白开始；请检查每件道具的持有者并添加新对白。当前片段会单独保留。","The next clip starts with empty dialogue. Check who holds each object and add new spoken words. Your current clip remains saved separately.","次クリップの台詞は空から始まります。各小道具の所有者を確認し、新しい台詞を追加してください。現在のクリップは別に保存されます。","下一段會從空對白開始；請檢查每件道具的持有者並新增新對白。目前片段會單獨保留。")}</p>
    <button type="button" disabled={busy} onClick={() => {
      try { const next = createContinuation(p, { request, duration, ending, firstFrameAssetId: firstFrameAssetId || undefined }); setError(""); onContinue(next); }
      catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    }}>{t(`创建独立 ${duration} 秒场景`,`Create separate ${duration}-second scene`,`独立した${duration}秒シーンを作成`,`建立獨立 ${duration} 秒場景`)} · {formatTime(nextStart)}–{formatTime(nextStart + duration)}s</button>
    {error && <p className="timeline-error" role="alert">{error}</p>}
  </details>;
}
