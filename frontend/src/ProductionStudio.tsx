import React, { useEffect, useMemo, useRef, useState } from "react";
import { ArrowDown, ArrowUp, BookOpen, Check, ChevronDown, ChevronLeft, ChevronRight, ChevronUp, Clapperboard, Clock3, Download, Film, FolderOpen, ImagePlus, LoaderCircle, Pause, Play, Plus, RefreshCw, Save, Search, Settings2, Sparkles, Square, Trash2, Upload, Video, X } from "lucide-react";
import { api, apiPatch, downloadText } from "./api";
import { localText, useUiLanguage, type UiLanguage } from "./i18n";
import type { Project } from "./model";
import SeriesWorkspace from "./SeriesWorkspace";
import VideoLibrary from "./VideoLibrary";
import "./ProductionStudio.css";

type Dialogue = { speaker:string; text:string; language:string; voiceover:boolean };
type CardKind = "characters"|"wardrobe"|"props"|"environments"|"voices"|"styles";
type SelectableCardKind = Exclude<CardKind,"styles">;
type OverviewCardKind = "characters"|"wardrobe"|"props"|"environments";
type ProductionCard = {
  id:string; name:string; description:string; notes:string; asset_ids:string[]; locked:boolean;
  subject_id?:string|null; owner_card_id?:string|null; character_card_id?:string|null;
  voice_card_id?:string|null; voice_id:string; language:string; pace:string; image_analysis:string; image_generation_prompt?:string;
};
type CardLibrary = Record<CardKind,ProductionCard[]>;
type Episode = { id:string; index:number; title:string; logline:string; story:string; character_card_ids:string[]; returning_character_card_ids:string[]; continuity_notes:string };
type CardCollection = { id:string; name:string; card_count:number; counts?:Partial<Record<CardKind,number>>; overview_count?:number; has_series_voice_style?:boolean; updated_at:number };
type CardCollectionDetail = { id:string; name:string; cards:CardLibrary; series_voice_style:string; overview_asset_ids:Record<OverviewCardKind,string|null>; updated_at:number };
type VideoWorkflow = {id:string;name:string;description:string;builtin:boolean;modes:string[];created_at:number};
type Segment = {
  id:string; index:number; title:string; story:string; setting:string; action:string;
  ending:string; duration:number; duration_reason:string; continue_previous?:boolean; dialogue:Dialogue[];
  image_prompt:string; project_id?:string|null; status:string; stale_reasons?:string[];
  image_run_id?:string|null; image_run_ids?:string[]; image_asset_id?:string|null; keyframe_asset_ids?:string[];
  prompt_direction?:string; video_prompt?:string; video_prompt_source?:"local_ai"|"compiled"|"";
  prompt_seconds?:number|null; prompt_updated_at?:number|null; prepare_seconds?:number|null;
  workflow_profile_id?:string;
  selected_video_run_id?:string|null; last_video_run_id?:string|null;
  card_selection?:Record<SelectableCardKind,string[]>; card_selection_source?:"local_ai"|"heuristic";
};
type Production = {
  id:string; title:string; language:"zh-CN"|"zh-TW"|"en"|"ja"; brief:string; style_bible:string; character_bible:string;
  prompt_version:"classic"|"continuity_director"|"storyboard_narrative";
  continuity_notes:string; series_voice_style:string; source_project_id:string; source_mode?:string; planner?:string|null; auto_merge:boolean; auto_continue_previous:boolean; task_state:"active"|"paused";
  planner_warning?:string|null; cards:CardLibrary; segments:Segment[];
  visual_style_preset:string; visual_style_custom:string; narrative_style:string; narrative_style_custom:string; narrative_notes:string;
  episode_count:number; episode_minutes:number; current_episode:number; episodes:Episode[];
  episode_planner?:string|null; episode_planner_warning?:string|null;
  card_planner?:string|null; card_planner_warning?:string|null; card_plan_source_hash?:string|null;
  card_collection_id?:string|null; card_collection_name:string;
  overview_asset_ids:Record<OverviewCardKind,string|null>;
  video_aspect_ratio:string; video_resolution:string; video_quality:"fast"|"detailed"|"lora8"; video_steps:"auto"|4|8|16;
  auto_keyframes_enabled:boolean; auto_keyframe_model:string;
  timings:{episode_plan_seconds?:number|null;storyboard_plan_seconds?:number|null;merge_seconds?:number|null};
};
type ProductionPage = "projects"|"series"|"videos"|"cardsets"|"script"|"assets"|"episodes"|"storyboard"|"output"|"settings";
type Summary = { id:string; title:string; segment_count:number; ready_count:number; updated_at:number; episode_count?:number;current_episode?:number };
type ProductionVideoJob = { id:string; status:string; stage?:string; error?:string|null; operation?:string; seed?:number|null; duration?:number|null; new_seconds?:number|null; created_at?:number; elapsed_seconds?:number|null;server_execution_seconds?:number|null;output_folder?:string|null;width?:number; height?:number; video_url?:string|null; scene_video_url?:string|null };
type ProductionOutputRow = { segment_id:string; index:number; title:string; project_id?:string|null; project_status:string; candidates:ProductionVideoJob[]; selected?:ProductionVideoJob|null; selection:"manual"|"latest" };
type ProductionOutputs = { production_id:string; auto_merge:boolean; segments:ProductionOutputRow[]; selected_run_ids:string[]; all_ready:boolean; ready_count:number; segment_count:number; signature?:string|null; estimated_seconds:number; active_jobs:number; timings:{episode_plan_seconds?:number|null;storyboard_plan_seconds?:number|null;prompt_generation_seconds?:number|null;video_generation_seconds?:number|null;merge_seconds?:number|null};final_ready:boolean; final_url?:string|null; download_url?:string|null; file_path?:string|null; folder_path?:string|null };

export const segmentNeedsVideoPrompt=(segment:Segment)=>
  !segment.video_prompt?.trim()||segment.status!=="ready"||!segment.project_id||!!segment.stale_reasons?.length;
export const segmentHasCompletedVideo=(segment:Segment,outputs:ProductionOutputs|null|undefined)=>{
  if(segment.status!=="ready"||!!segment.stale_reasons?.length)return false;
  const selected=outputs?.segments.find(row=>row.segment_id===segment.id)?.selected;
  if(!selected)return false;
  return !segment.prompt_updated_at||!selected.created_at||selected.created_at>=segment.prompt_updated_at;
};
export const videoPromptTargets=(segments:Segment[],force=false)=>
  force?segments:segments.filter(segmentNeedsVideoPrompt);
export const videoRenderTargets=(segments:Segment[],outputs:ProductionOutputs|null|undefined,force=false)=>
  force?segments:segments.filter(segment=>!segmentHasCompletedVideo(segment,outputs));

const blank = (project:Project) => ({
  // A new episode/part is deliberately empty. The current Scene Studio
  // project is still the technical render source, but its creative content
  // must never look like user-authored production data unless explicitly
  // imported below.
  title: "",
  language: (project.production_language||"zh-CN") as Production["language"],
  prompt_version:"classic" as Production["prompt_version"],
  brief: "",
  style_bible: "",
  character_bible: "",
  continuity_notes: "",series_voice_style:"",
  visual_style_preset:"cinematic_realism",visual_style_custom:"",narrative_style:"cinematic",narrative_style_custom:"",narrative_notes:"",
  episode_count:1,episode_minutes:8,card_collection_name:"",
  video_aspect_ratio:project.aspect_ratio||"16:9",video_resolution:String(project.comfy_render?.resolution||"0.7"),
  video_quality:project.comfy_render?.quality==="lora8"?"lora8":project.comfy_render?.quality==="detailed"?"detailed":"fast",video_steps:[4,8,16].includes(project.comfy_render?.steps)?project.comfy_render.steps:"auto",
  overview_asset_ids:{characters:null,wardrobe:null,props:null,environments:null},
});
const sleep = (ms:number) => new Promise(resolve=>setTimeout(resolve,ms));
export const productionKeyframeSize=(aspect:string)=>({
  "16:9":{width:768,height:432},"9:16":{width:432,height:768},
  "1:1":{width:768,height:768},"4:3":{width:768,height:576},
  "3:4":{width:576,height:768},
}[aspect]||{width:768,height:432});
const formatSeconds=(value?:number|null)=>value==null?"—":value<60?`${value.toFixed(value<10?1:0)}s`:`${Math.floor(value/60)}m ${Math.round(value%60)}s`;
const formatProjectDate=(value:number,language:UiLanguage)=>new Intl.DateTimeFormat(language,{month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"}).format(new Date(value*1000));
const safeDownloadName=(value:string)=>value.replace(/[\\/:*?"<>|]+/g,"-").trim().slice(0,80)||"H3-production";
const episodeTiming=(minutes:number)=>{
  const targetSeconds=Math.max(5,Math.round(minutes*60));
  const minimum=Math.max(1,Math.ceil(targetSeconds/15));
  const maximum=Math.max(minimum,Math.floor(targetSeconds/5));
  const preferred=Math.max(1,Math.floor(targetSeconds/10+0.5));
  return {targetSeconds,recommendedClips:Math.min(64,Math.max(minimum,Math.min(maximum,preferred)))};
};
const projectText=(language:UiLanguage,zh:string,en:string,ja:string,tw=zh)=>localText(language,{"zh-CN":zh,"zh-TW":tw,en,ja});
export function productionStoryExport(value:Production){
  const tx=(zh:string,en:string,ja:string,tw=zh)=>projectText(value.language,zh,en,ja,tw);
  const empty=tx("（尚未规划）","(not planned)","（未計画）","（尚未規劃）");
  const episodes=value.episodes.map(ep=>`## ${String(ep.index).padStart(2,"0")} · ${ep.title}\n\n${ep.logline}\n\n${ep.story}\n\n**${tx("连续性","Continuity","連続性","連續性")}：** ${ep.continuity_notes}`).join("\n\n");
  const clips=value.segments.map(s=>`### ${String(s.index).padStart(2,"0")} · ${s.title} · ${s.duration}s\n\n${s.story}\n\n- ${tx("场景","Setting","場所","場景")}：${s.setting}\n- ${tx("可见动作","Visible action","画面内の動作","可見動作")}：${s.action}\n- ${tx("结束状态","Ending","終了状態","結束狀態")}：${s.ending}`).join("\n\n");
  return `# ${value.title}\n\n## ${tx("故事原文 / 剧本","Source story / screenplay","物語原文・脚本","故事原文／劇本")}\n\n${value.brief}\n\n## ${tx("角色圣经","Character bible","キャラクター設定","角色聖經")}\n\n${value.character_bible}\n\n## ${tx("视觉风格圣经","Visual style bible","視覚スタイル設定","視覺風格聖經")}\n\n${value.style_bible}\n\n## ${tx("全剧声线风格","Series voice style","シリーズ音声スタイル","全劇聲線風格")}\n\n${value.series_voice_style||empty}\n\n## ${tx("全片连续性","Film-wide continuity","全編の連続性","全片連續性")}\n\n${value.continuity_notes}\n\n# ${tx("剧集规划","Episode plan","エピソード計画","劇集規劃")}\n\n${episodes||empty}\n\n# ${tx("当前集分镜","Current episode clips","現在話のクリップ","目前集分鏡")}\n\n${clips||empty}\n`;
}
export function productionDialogueExport(value:Production){
  const tx=(zh:string,en:string,ja:string,tw=zh)=>projectText(value.language,zh,en,ja,tw);
  const languageName=tx("简体中文","English","日本語","繁體中文");
  const lines=value.segments.flatMap(s=>s.dialogue.map((d,i)=>`[${String(s.index).padStart(2,"0")}.${i+1}] ${d.speaker}${d.voiceover?tx("（画外音）"," (V.O.)","（画外音）","（畫外音）"):""}：${d.text}`));
  return `${value.title}\n${tx("项目语言","Project language","プロジェクト言語","專案語言")}：${languageName}\n\n${lines.join("\n")||tx("（无对白）","(no dialogue)","（台詞なし）","（無對白）")}\n`;
}
const CARD_KINDS:CardKind[]=["characters","wardrobe","props","environments","voices","styles"];
const OVERVIEW_CARD_KINDS:OverviewCardKind[]=["characters","wardrobe","props","environments"];
const CARD_META:Record<CardKind,{title:Record<UiLanguage,string>;hint:Record<UiLanguage,string>;upload:Record<UiLanguage,string>}>= {
  characters:{
    title:{"zh-CN":"角色卡","zh-TW":"角色卡",en:"Character cards",ja:"キャラクターカード"},
    hint:{"zh-CN":"上传图时，图片主导脸、发型、体型与可见标志；文字负责姓名、身份与连续性绑定。建议每个角色至少一张清晰独立图。","zh-TW":"上傳圖片時，圖片主導臉、髮型、體型與可見標誌；文字負責姓名、身分與連續性綁定。建議每個角色至少一張清晰獨立圖。",en:"When an image is uploaded, it leads visible face, hair, build and distinctive marks; text controls identity and continuity binding. Keep at least one clear solo image per character.",ja:"画像がある場合、顔・髪型・体格・目に見える特徴は画像を優先し、文章は名前・身元・連続性の紐付けを管理します。各人物に鮮明な単独画像を推奨します。"},
    upload:{"zh-CN":"上传角色图","zh-TW":"上傳角色圖",en:"Upload character image",ja:"人物画像を追加"}},
  wardrobe:{
    title:{"zh-CN":"服装卡","zh-TW":"服裝卡",en:"Wardrobe cards",ja:"衣装カード"},
    hint:{"zh-CN":"与角色分开管理，换装时不会污染角色身份。","zh-TW":"與角色分開管理，換裝時不會污染角色身分。",en:"Keep clothing separate from identity so outfit changes do not alter the character.",ja:"人物の識別と衣装を分け、着替えても人物が変化しないようにします。"},
    upload:{"zh-CN":"上传服装图","zh-TW":"上傳服裝圖",en:"Upload wardrobe image",ja:"衣装画像を追加"}},
  props:{
    title:{"zh-CN":"道具卡","zh-TW":"道具卡",en:"Prop cards",ja:"小道具カード"},
    hint:{"zh-CN":"关键或反复出现的道具；普通物件只写文字即可。","zh-TW":"關鍵或反覆出現的道具；普通物件只寫文字即可。",en:"Use for important or recurring props; ordinary objects can remain text-only.",ja:"重要または繰り返し登場する小道具用。一般的な物は文字だけで十分です。"},
    upload:{"zh-CN":"上传道具图","zh-TW":"上傳道具圖",en:"Upload prop image",ja:"小道具画像を追加"}},
  environments:{
    title:{"zh-CN":"环境卡","zh-TW":"環境卡",en:"Environment cards",ja:"環境カード"},
    hint:{"zh-CN":"固定空间、布局与光线；推荐一张能看清全局的广角图。","zh-TW":"固定空間、配置與光線；建議一張能看清全局的廣角圖。",en:"Lock the space, layout and light. A clear wide establishing image works best.",ja:"空間・配置・光を固定します。全体が分かる広角画像がおすすめです。"},
    upload:{"zh-CN":"上传环境图","zh-TW":"上傳環境圖",en:"Upload environment image",ja:"環境画像を追加"}},
  voices:{
    title:{"zh-CN":"角色声线卡","zh-TW":"角色聲線卡",en:"Voice cards",ja:"ボイスカード"},
    hint:{"zh-CN":"绑定角色并填写文字声线。音频可选；一旦上传，2–15 秒单人干声将作为音色与节奏的第一依据，文字负责补充表演和禁忌。","zh-TW":"綁定角色並填寫文字聲線。音訊可選；一旦上傳，2–15 秒單人乾聲將作為音色與節奏的第一依據，文字負責補充表演和禁忌。",en:"Bind a character and write voice direction. Audio is optional; once supplied, the clean 2–15 second solo sample becomes the primary authority for vocal identity and cadence, with text supplementing performance and exclusions.",ja:"人物に紐付けて音声指示を記入します。音声は任意ですが、追加した2〜15秒の単独ドライ音声を声質とリズムの最優先資料とし、文章で演技と禁止事項を補います。"},
    upload:{"zh-CN":"上传声线音频（可选）","zh-TW":"上傳聲線音訊（可選）",en:"Upload voice sample (optional)",ja:"音声サンプルを追加（任意）"}},
  styles:{
    title:{"zh-CN":"风格卡","zh-TW":"風格卡",en:"Style cards",ja:"スタイルカード"},
    hint:{"zh-CN":"直接输入全片画风、色彩、材质和禁忌即可；风格图完全可选。","zh-TW":"直接輸入全片畫風、色彩、材質和禁忌即可；風格圖完全可選。",en:"Text alone can define the film's art direction, color, material and exclusions; an image is optional.",ja:"作品全体の画風・色・質感・禁止事項は文字だけで設定できます。画像は任意です。"},
    upload:{"zh-CN":"上传风格图（可选）","zh-TW":"上傳風格圖（可選）",en:"Upload style image (optional)",ja:"スタイル画像を追加（任意）"}},
};
const LANGUAGE_OPTIONS:[Production["language"],string][]=[["en","English"],["ja","日本語"],["zh-CN","简体中文"],["zh-TW","繁體中文"]];
const LANGUAGE_NAMES:Record<Production["language"],string>={"zh-CN":"Simplified Chinese","zh-TW":"Traditional Chinese",en:"English",ja:"Japanese"};
const CARD_DEFAULT_NAMES:Record<Production["language"],Record<CardKind,string>>={
  "zh-CN":{characters:"角色卡",wardrobe:"服装卡",props:"道具卡",environments:"环境卡",voices:"角色声线卡",styles:"风格卡"},
  "zh-TW":{characters:"角色卡",wardrobe:"服裝卡",props:"道具卡",environments:"環境卡",voices:"角色聲線卡",styles:"風格卡"},
  en:{characters:"Character card",wardrobe:"Wardrobe card",props:"Prop card",environments:"Environment card",voices:"Voice card",styles:"Style card"},
  ja:{characters:"キャラクターカード",wardrobe:"衣装カード",props:"小道具カード",environments:"環境カード",voices:"ボイスカード",styles:"スタイルカード"},
};
type Localized4 = [string,string,string,string];
type StyleOption = {id:string;group:string;label:Localized4;description:Localized4};
type StyleGroup = {id:string;label:Localized4};
const VISUAL_STYLE_GROUPS:StyleGroup[]=[
  {
    "id": "live",
    "label": [
      "实拍与电影",
      "Live action & cinema",
      "実写・映画",
      "實拍與電影"
    ]
  },
  {
    "id": "animation",
    "label": [
      "动画与插画",
      "Animation & illustration",
      "アニメ・イラスト",
      "動畫與插畫"
    ]
  },
  {
    "id": "art",
    "label": [
      "艺术与实验",
      "Art & experimental",
      "芸術・実験",
      "藝術與實驗"
    ]
  },
  {
    "id": "personal",
    "label": [
      "个性化",
      "Personal",
      "カスタム",
      "個人化"
    ]
  }
];
const VISUAL_STYLES:StyleOption[]=[
  {
    "id": "cinematic_realism",
    "group": "live",
    "label": [
      "电影写实",
      "Cinematic realism",
      "映画的リアリズム",
      "電影寫實"
    ],
    "description": [
      "自然肤质与材质、合理光源、克制的镜头语言。",
      "Natural skin and materials, motivated light and restrained lens language.",
      "自然な肌と質感、必然性のある光、抑制されたレンズ表現。",
      "自然膚質與材質、合理光源、克制的鏡頭語言。"
    ]
  },
  {
    "id": "hollywood_blockbuster",
    "group": "live",
    "label": [
      "商业大片",
      "Studio blockbuster",
      "スタジオ大作",
      "商業大片"
    ],
    "description": [
      "宏大调度、清晰主光、强轮廓与高完成度视效。",
      "Large-scale staging, clear key light, strong silhouettes and polished effects.",
      "大規模な演出、明快な主光、強い輪郭、高品質なVFX。",
      "宏大調度、清晰主光、強輪廓與高完成度視效。"
    ]
  },
  {
    "id": "commercial_clean",
    "group": "live",
    "label": [
      "高端广告",
      "Premium commercial",
      "高級CM",
      "高端廣告"
    ],
    "description": [
      "干净材质、精准产品光、明亮层次与受控运动。",
      "Clean materials, precise product lighting, bright separation and controlled motion.",
      "清潔な質感、精密な商品照明、明るい分離感と制御された動き。",
      "乾淨材質、精準產品光、明亮層次與受控運動。"
    ]
  },
  {
    "id": "documentary",
    "group": "live",
    "label": [
      "自然纪录片",
      "Documentary naturalism",
      "ドキュメンタリー自然主義",
      "自然紀錄片"
    ],
    "description": [
      "自然光、观察式摄影、可信场景与不刻意表演。",
      "Available light, observational camera, credible locations and unforced performance.",
      "自然光、観察的カメラ、信頼できる場所と作りすぎない演技。",
      "自然光、觀察式攝影、可信場景與不刻意表演。"
    ]
  },
  {
    "id": "neorealism",
    "group": "live",
    "label": [
      "新现实主义",
      "Neorealism",
      "ネオリアリズム",
      "新現實主義"
    ],
    "description": [
      "真实地点、生活质感、长镜头与非戏剧化观察。",
      "Real locations, lived-in texture, long takes and de-dramatized observation.",
      "実在の場所、生活感、長回し、非劇的な観察。",
      "真實地點、生活質感、長鏡頭與非戲劇化觀察。"
    ]
  },
  {
    "id": "handheld_indie",
    "group": "live",
    "label": [
      "手持独立电影",
      "Handheld indie",
      "手持ちインディー",
      "手持獨立電影"
    ],
    "description": [
      "贴近人物的手持机位、自然噪点、亲密表演与有限光源。",
      "Character-close handheld framing, natural grain, intimate acting and limited light.",
      "人物に寄る手持ち、自然な粒子、親密な演技、限られた光源。",
      "貼近人物的手持機位、自然噪點、親密表演與有限光源。"
    ]
  },
  {
    "id": "historical_epic",
    "group": "live",
    "label": [
      "历史史诗",
      "Historical epic",
      "歴史叙事詩",
      "歷史史詩"
    ],
    "description": [
      "大尺度场景、层次群像、时代材质与庄重镜头运动。",
      "Grand scale, layered crowds, period materials and stately camera movement.",
      "大規模な場面、重層的な群像、時代素材、荘重なカメラ。",
      "大尺度場景、層次群像、時代材質與莊重鏡頭運動。"
    ]
  },
  {
    "id": "fashion_editorial",
    "group": "live",
    "label": [
      "时尚大片",
      "Fashion editorial",
      "ファッション・エディトリアル",
      "時尚大片"
    ],
    "description": [
      "造型优先、雕塑光、图形构图与精确姿态。",
      "Styling-first design, sculpted light, graphic composition and precise posing.",
      "スタイリング重視、彫刻的な光、図形的構図、精密なポーズ。",
      "造型優先、雕塑光、圖形構圖與精確姿態。"
    ]
  },
  {
    "id": "music_video",
    "group": "live",
    "label": [
      "音乐影像",
      "Music video",
      "ミュージックビデオ",
      "音樂影像"
    ],
    "description": [
      "节奏化剪辑、大胆色彩、表现性镜头与视觉转场。",
      "Rhythmic cutting, bold color, expressive camera and visual transitions.",
      "リズム編集、大胆な色、表現的カメラ、視覚的転換。",
      "節奏化剪輯、大膽色彩、表現性鏡頭與視覺轉場。"
    ]
  },
  {
    "id": "romantic_soft",
    "group": "live",
    "label": [
      "柔光浪漫",
      "Soft romantic",
      "ソフト・ロマンティック",
      "柔光浪漫"
    ],
    "description": [
      "柔和逆光、低反差肤色、浅景深与温柔色调。",
      "Soft backlight, low-contrast skin, shallow depth and gentle color.",
      "柔らかな逆光、低コントラストの肌、浅い被写界深度、穏やかな色。",
      "柔和逆光、低反差膚色、淺景深與溫柔色調。"
    ]
  },
  {
    "id": "film_noir",
    "group": "live",
    "label": [
      "经典黑色电影",
      "Classic film noir",
      "クラシック・ノワール",
      "經典黑色電影"
    ],
    "description": [
      "低调高反差、深阴影、斜线构图与压迫城市氛围。",
      "Low-key contrast, deep shadows, diagonal framing and oppressive urban mood.",
      "ローキー高コントラスト、深い影、斜線構図、圧迫的都市感。",
      "低調高反差、深陰影、斜線構圖與壓迫城市氛圍。"
    ]
  },
  {
    "id": "neo_noir",
    "group": "live",
    "label": [
      "现代新黑色",
      "Neo-noir",
      "ネオ・ノワール",
      "現代新黑色"
    ],
    "description": [
      "冷暖霓虹、主观机位、现代都市孤独与道德暧昧。",
      "Cool-warm neon, subjective framing, urban isolation and moral ambiguity.",
      "寒暖ネオン、主観的構図、都市の孤独、道徳的曖昧さ。",
      "冷暖霓虹、主觀機位、現代都市孤獨與道德曖昧。"
    ]
  },
  {
    "id": "horror_expressionist",
    "group": "live",
    "label": [
      "表现主义恐怖",
      "Expressionist horror",
      "表現主義ホラー",
      "表現主義恐怖"
    ],
    "description": [
      "畸变阴影、倾斜空间、局部光源与心理压迫。",
      "Distorted shadows, canted space, isolated light and psychological pressure.",
      "歪んだ影、傾いた空間、局所光、心理的圧迫。",
      "畸變陰影、傾斜空間、局部光源與心理壓迫。"
    ]
  },
  {
    "id": "cyberpunk_neon",
    "group": "live",
    "label": [
      "赛博霓虹",
      "Cyberpunk neon",
      "サイバーパンク・ネオン",
      "賽博霓虹"
    ],
    "description": [
      "湿润夜景、密集霓虹、技术层叠与高色彩分离。",
      "Wet night streets, dense neon, layered technology and strong color separation.",
      "濡れた夜景、密集ネオン、技術の重層、強い色分離。",
      "濕潤夜景、密集霓虹、技術層疊與高色彩分離。"
    ]
  },
  {
    "id": "retro_scifi",
    "group": "live",
    "label": [
      "复古科幻",
      "Retro science fiction",
      "レトロSF",
      "復古科幻"
    ],
    "description": [
      "实用主义未来感、模拟界面、大胆几何与年代色彩。",
      "Practical futurism, analog interfaces, bold geometry and period color.",
      "実用的未来像、アナログUI、大胆な幾何学、時代色。",
      "實用主義未來感、模擬介面、大膽幾何與年代色彩。"
    ]
  },
  {
    "id": "anime_2d",
    "group": "animation",
    "label": [
      "二维日系动画",
      "2D anime",
      "2Dアニメ",
      "二維日系動畫"
    ],
    "description": [
      "清晰线稿、表现性演技、赛璐璐明暗与易读轮廓。",
      "Clean line art, expressive acting, cel shading and readable silhouettes.",
      "明快な線、表情豊かな演技、セル塗り、読みやすい輪郭。",
      "清晰線稿、表現性演技、賽璐璐明暗與易讀輪廓。"
    ]
  },
  {
    "id": "anime_cinematic",
    "group": "animation",
    "label": [
      "电影级动画",
      "Cinematic anime",
      "劇場アニメ",
      "電影級動畫"
    ],
    "description": [
      "动画造型结合真实镜头、体积光、精细背景与电影调色。",
      "Anime design with grounded lenses, volumetric light, detailed backgrounds and cinematic grading.",
      "アニメ造形に実写的レンズ、体積光、精密背景、映画調色を融合。",
      "動畫造型結合真實鏡頭、體積光、精細背景與電影調色。"
    ]
  },
  {
    "id": "stylized_3d",
    "group": "animation",
    "label": [
      "风格化三维动画",
      "Stylized 3D",
      "スタイライズ3D",
      "風格化三維動畫"
    ],
    "description": [
      "雕塑感造型、统一材质、图形化灯光与动画友好细节。",
      "Sculpted forms, coherent materials, graphic lighting and animation-friendly detail.",
      "彫刻的造形、統一材質、図形的照明、動かしやすい細部。",
      "雕塑感造型、統一材質、圖形化燈光與動畫友好細節。"
    ]
  },
  {
    "id": "photoreal_cgi",
    "group": "animation",
    "label": [
      "照片级 CG",
      "Photoreal CGI",
      "フォトリアルCG",
      "照片級 CG"
    ],
    "description": [
      "物理材质、可信全局光照、精细表面与真实镜头缺陷。",
      "Physical materials, believable global light, fine surfaces and realistic lens imperfections.",
      "物理材質、自然な大域照明、精密表面、実写的レンズの癖。",
      "物理材質、可信全局光照、精細表面與真實鏡頭缺陷。"
    ]
  },
  {
    "id": "stop_motion",
    "group": "animation",
    "label": [
      "定格黏土动画",
      "Stop-motion / clay",
      "ストップモーション",
      "定格黏土動畫"
    ],
    "description": [
      "手工模型、可见触感、逐帧节奏与微小材质变化。",
      "Hand-built models, tactile surfaces, frame-by-frame rhythm and subtle material variation.",
      "手作り模型、触感、コマ撮りのリズム、微細な素材変化。",
      "手工模型、可見觸感、逐幀節奏與微小材質變化。"
    ]
  },
  {
    "id": "storybook",
    "group": "animation",
    "label": [
      "绘本插画",
      "Storybook illustration",
      "絵本イラスト",
      "繪本插畫"
    ],
    "description": [
      "绘画化形状、可触肌理、温暖构图与柔和节奏。",
      "Painterly shapes, tactile texture, warm composition and gentle rhythm.",
      "絵画的な形、触感ある質感、温かな構図、穏やかなリズム。",
      "繪畫化形狀、可觸肌理、溫暖構圖與柔和節奏。"
    ]
  },
  {
    "id": "ink_animation",
    "group": "animation",
    "label": [
      "东方水墨动画",
      "Ink-wash animation",
      "水墨アニメ",
      "東方水墨動畫"
    ],
    "description": [
      "墨色晕染、纸张肌理、诗意留白与优雅运动。",
      "Ink diffusion, paper texture, poetic negative space and elegant motion.",
      "墨のにじみ、紙の質感、詩的余白、優雅な動き。",
      "墨色暈染、紙張肌理、詩意留白與優雅運動。"
    ]
  },
  {
    "id": "watercolor",
    "group": "animation",
    "label": [
      "水彩动画",
      "Watercolor animation",
      "水彩アニメ",
      "水彩動畫"
    ],
    "description": [
      "透明叠色、湿边纹理、柔和轮廓与流动色块。",
      "Transparent washes, wet edges, soft contours and flowing color fields.",
      "透明な重ね色、湿った縁、柔らかな輪郭、流れる色面。",
      "透明疊色、濕邊紋理、柔和輪廓與流動色塊。"
    ]
  },
  {
    "id": "graphic_novel",
    "group": "animation",
    "label": [
      "漫画图像小说",
      "Graphic novel",
      "グラフィックノベル",
      "漫畫圖像小說"
    ],
    "description": [
      "强墨线、网点或块面阴影、面板式构图与冲击动作。",
      "Bold inks, halftone or blocked shadows, panel-like framing and punchy action.",
      "太いインク線、網点・面影、コマ的構図、強い動作。",
      "強墨線、網點或塊面陰影、面板式構圖與衝擊動作。"
    ]
  },
  {
    "id": "surreal_dream",
    "group": "art",
    "label": [
      "超现实梦境",
      "Surreal dream",
      "シュルレアルな夢",
      "超現實夢境"
    ],
    "description": [
      "不可能空间、象征转化、梦境光线与有意的不连续。",
      "Impossible space, symbolic transformation, dream light and intentional discontinuity.",
      "不可能空間、象徴的変容、夢の光、意図的な不連続。",
      "不可能空間、象徵轉化、夢境光線與有意的不連續。"
    ]
  },
  {
    "id": "poetic_minimal",
    "group": "art",
    "label": [
      "诗意极简",
      "Poetic minimalism",
      "詩的ミニマリズム",
      "詩意極簡"
    ],
    "description": [
      "少量元素、精确留白、长静观与克制色彩。",
      "Few elements, precise negative space, long observation and restrained color.",
      "少ない要素、精密な余白、長い観察、抑制された色。",
      "少量元素、精確留白、長靜觀與克制色彩。"
    ]
  },
  {
    "id": "custom",
    "group": "personal",
    "label": [
      "自定义视觉风格",
      "Custom visual style",
      "カスタム視覚スタイル",
      "自訂視覺風格"
    ],
    "description": [
      "用自己的文字或风格参考图定义视觉；有图时图片分析优先。",
      "Define the look with your own text or style image; image analysis takes priority when present.",
      "文章またはスタイル画像で定義し、画像があれば分析結果を優先します。",
      "用自己的文字或風格參考圖定義視覺；有圖時圖片分析優先。"
    ]
  }
];
const NARRATIVE_STYLE_GROUPS:StyleGroup[]=[
  {
    "id": "adapt",
    "label": [
      "改编与结构",
      "Adaptation & structure",
      "脚色・構成",
      "改編與結構"
    ]
  },
  {
    "id": "pace",
    "label": [
      "节奏与视角",
      "Pacing & viewpoint",
      "テンポ・視点",
      "節奏與視角"
    ]
  },
  {
    "id": "format",
    "label": [
      "系列与类型",
      "Series & genre form",
      "シリーズ・形式",
      "系列與類型"
    ]
  },
  {
    "id": "personal",
    "label": [
      "个性化",
      "Personal",
      "カスタム",
      "個人化"
    ]
  }
];
const NARRATIVE_STYLES:StyleOption[]=[
  {
    "id": "faithful",
    "group": "adapt",
    "label": [
      "忠于原作",
      "Faithful adaptation",
      "原作忠実",
      "忠於原作"
    ],
    "description": [
      "保持剧情、人物动机与事件顺序，只做必要影视化压缩。",
      "Preserve plot, motivation and order; compress only where screen storytelling requires it.",
      "物語・動機・順序を保ち、映像化に必要な圧縮だけを行います。",
      "保持劇情、人物動機與事件順序，只做必要影視化壓縮。"
    ]
  },
  {
    "id": "three_act",
    "group": "adapt",
    "label": [
      "三幕式",
      "Three-act structure",
      "三幕構成",
      "三幕式"
    ],
    "description": [
      "建立—对抗—解决，转折点清晰，因果持续推进。",
      "Setup, confrontation and resolution with clear causal turning points.",
      "設定・対立・解決を明確な転換点と因果で進めます。",
      "建立—對抗—解決，轉折點清晰，因果持續推進。"
    ]
  },
  {
    "id": "five_act",
    "group": "adapt",
    "label": [
      "五幕式长篇",
      "Five-act long form",
      "五幕長編",
      "五幕式長篇"
    ],
    "description": [
      "用五个阶段容纳更长铺垫、升级、危机、高潮与余韵。",
      "Use five phases for setup, escalation, crisis, climax and aftermath.",
      "導入・上昇・危機・頂点・余韻を五段階で描きます。",
      "用五個階段容納更長鋪墊、升級、危機、高潮與餘韻。"
    ]
  },
  {
    "id": "eight_sequence",
    "group": "adapt",
    "label": [
      "八序列结构",
      "Eight-sequence structure",
      "8シークエンス構成",
      "八序列結構"
    ],
    "description": [
      "把长片切成八个有目标、转折和阶段回报的连续单元。",
      "Organize a feature into eight goal-driven sequences with turns and payoffs.",
      "長編を目標・転換・回収を持つ8つの連続単位に構成します。",
      "把長片切成八個有目標、轉折和階段回報的連續單元。"
    ]
  },
  {
    "id": "hero_journey",
    "group": "adapt",
    "label": [
      "英雄旅程",
      "Hero's journey",
      "英雄の旅",
      "英雄旅程"
    ],
    "description": [
      "围绕召唤、跨越门槛、试炼、转变与归返塑造角色弧。",
      "Shape the arc through call, threshold, trials, transformation and return.",
      "召命・境界・試練・変容・帰還で人物弧を作ります。",
      "圍繞召喚、跨越門檻、試煉、轉變與歸返塑造角色弧。"
    ]
  },
  {
    "id": "kishotenketsu",
    "group": "adapt",
    "label": [
      "起承转合",
      "Kishōtenketsu",
      "起承転結",
      "起承轉合"
    ],
    "description": [
      "以建立、发展、意外转折与重新理解完成非对抗式推进。",
      "Build through introduction, development, surprising turn and recontextualization.",
      "起・承・意外な転・再理解としての結で進めます。",
      "以建立、發展、意外轉折與重新理解完成非對抗式推進。"
    ]
  },
  {
    "id": "character_driven",
    "group": "pace",
    "label": [
      "人物驱动",
      "Character-driven",
      "人物主導",
      "人物驅動"
    ],
    "description": [
      "外部事件服务于选择、关系与内在变化。",
      "Let external events serve choices, relationships and inner change.",
      "外的事件を選択・関係・内面変化に従属させます。",
      "外部事件服務於選擇、關係與內在變化。"
    ]
  },
  {
    "id": "ensemble",
    "group": "pace",
    "label": [
      "群像交织",
      "Ensemble interweave",
      "群像交差",
      "群像交織"
    ],
    "description": [
      "平衡多名主角的目标、交叉点和共同高潮。",
      "Balance multiple protagonists, intersections and a shared climax.",
      "複数主人公の目標・交差点・共通の頂点を均衡させます。",
      "平衡多名主角的目標、交叉點和共同高潮。"
    ]
  },
  {
    "id": "slow_burn",
    "group": "pace",
    "label": [
      "慢热沉浸",
      "Slow burn",
      "スローバーン",
      "慢熱沉浸"
    ],
    "description": [
      "让关系、潜台词和压力逐层累积，延后回报。",
      "Accumulate relationships, subtext and pressure before delayed payoff.",
      "関係・行間・圧力を積み、回収を遅らせます。",
      "讓關係、潛台詞和壓力逐層累積，延後回報。"
    ]
  },
  {
    "id": "fast_paced",
    "group": "pace",
    "label": [
      "快节奏推进",
      "Fast-paced",
      "高速展開",
      "快節奏推進"
    ],
    "description": [
      "高信息密度、短场景、快速转场与连续行动目标。",
      "Use dense information, short scenes, quick transitions and consecutive goals.",
      "高密度情報、短い場面、速い転換、連続目標で進めます。",
      "高資訊密度、短場景、快速轉場與連續行動目標。"
    ]
  },
  {
    "id": "suspense",
    "group": "pace",
    "label": [
      "悬念递进",
      "Suspense escalation",
      "サスペンス上昇",
      "懸念遞進"
    ],
    "description": [
      "控制观众信息，用风险升级与阶段揭示持续加压。",
      "Control audience knowledge through escalating risk and staged reveals.",
      "観客情報を制御し、危険増幅と段階開示で圧力を高めます。",
      "控制觀眾資訊，用風險升級與階段揭示持續加壓。"
    ]
  },
  {
    "id": "mystery_reveal",
    "group": "pace",
    "label": [
      "谜题揭示",
      "Mystery reveal",
      "謎解き",
      "謎題揭示"
    ],
    "description": [
      "按线索、误导、验证与真相重构安排信息。",
      "Arrange information through clues, misdirection, verification and reconstruction.",
      "手掛かり・ミスリード・検証・真相再構築で情報を配置します。",
      "按線索、誤導、驗證與真相重構安排資訊。"
    ]
  },
  {
    "id": "nonlinear",
    "group": "pace",
    "label": [
      "非线性时间",
      "Nonlinear time",
      "非線形時間",
      "非線性時間"
    ],
    "description": [
      "用闪回、闪前或碎片重排时间，但保持因果可追踪。",
      "Reorder time with flashbacks, flashforwards or fragments while keeping causality traceable.",
      "回想・予示・断片で時間を組み替え、因果は追跡可能に保ちます。",
      "用閃回、閃前或碎片重排時間，但保持因果可追蹤。"
    ]
  },
  {
    "id": "parallel",
    "group": "pace",
    "label": [
      "平行线交叉",
      "Parallel storylines",
      "並行物語",
      "平行線交叉"
    ],
    "description": [
      "多条时空或人物线交替推进，在主题或事件节点汇合。",
      "Alternate character or timeline threads that converge thematically or causally.",
      "人物・時間線を交互に進め、主題または事件で収束させます。",
      "多條時空或人物線交替推進，在主題或事件節點匯合。"
    ]
  },
  {
    "id": "episodic",
    "group": "format",
    "label": [
      "单元剧结构",
      "Episodic series",
      "エピソード型",
      "單元劇結構"
    ],
    "description": [
      "每集有独立目标与回报，同时持续推进季级主线。",
      "Give each episode a goal and payoff while advancing the season arc.",
      "各話に目標と決着を置きつつシーズン軸を進めます。",
      "每集有獨立目標與回報，同時持續推進季級主線。"
    ]
  },
  {
    "id": "procedural",
    "group": "format",
    "label": [
      "程序剧",
      "Procedural",
      "プロシージャル",
      "程序劇"
    ],
    "description": [
      "以稳定任务流程承载每集案件，并维护持续人物关系。",
      "Use a repeatable case process while maintaining continuing relationships.",
      "反復可能な事件手順を軸に継続する人物関係を保ちます。",
      "以穩定任務流程承載每集案件，並維護持續人物關係。"
    ]
  },
  {
    "id": "slice_of_life",
    "group": "format",
    "label": [
      "生活流",
      "Slice of life",
      "日常系",
      "生活流"
    ],
    "description": [
      "用日常细节、自然对白和微小变化承载成长。",
      "Carry growth through everyday detail, natural dialogue and subtle change.",
      "日常の細部、自然な会話、小さな変化で成長を描きます。",
      "用日常細節、自然對白和微小變化承載成長。"
    ]
  },
  {
    "id": "documentary",
    "group": "format",
    "label": [
      "纪实叙事",
      "Documentary",
      "ドキュメンタリー",
      "紀實敘事"
    ],
    "description": [
      "以事实感、观察视角、时间地点信息和克制旁白组织。",
      "Use factual texture, observation, time/place context and restrained narration.",
      "事実感、観察視点、時刻・場所、抑制された語りで構成します。",
      "以事實感、觀察視角、時間地點資訊和克制旁白組織。"
    ]
  },
  {
    "id": "anthology",
    "group": "format",
    "label": [
      "选集式",
      "Anthology",
      "アンソロジー",
      "選集式"
    ],
    "description": [
      "各集故事相对独立，以主题、世界观或母题保持统一。",
      "Keep episodes independent while unifying them through theme, world or motif.",
      "各話を独立させ、主題・世界・モチーフで統一します。",
      "各集故事相對獨立，以主題、世界觀或母題保持統一。"
    ]
  },
  {
    "id": "circular",
    "group": "format",
    "label": [
      "环形回响",
      "Circular / bookend",
      "円環・ブックエンド",
      "環形回響"
    ],
    "description": [
      "结尾回到开场意象或状态，以变化后的意义完成闭环。",
      "Return to an opening image or state, now transformed in meaning.",
      "冒頭の像や状態に戻り、変化した意味で円環を閉じます。",
      "結尾回到開場意象或狀態，以變化後的意義完成閉環。"
    ]
  },
  {
    "id": "custom",
    "group": "personal",
    "label": [
      "自定义叙事方式",
      "Custom narrative",
      "カスタム語り",
      "自訂敘事方式"
    ],
    "description": [
      "输入自己的结构、节奏、视角与禁忌。",
      "Define your own structure, pacing, viewpoint and exclusions.",
      "独自の構成・テンポ・視点・禁止事項を入力します。",
      "輸入自己的結構、節奏、視角與禁忌。"
    ]
  }
];
const newCard=(kind:CardKind,language:Production["language"]):ProductionCard=>({id:crypto.randomUUID(),name:CARD_DEFAULT_NAMES[language][kind]+" ",description:"",notes:"",asset_ids:[],locked:true,
  subject_id:null,owner_card_id:null,character_card_id:null,voice_card_id:null,voice_id:"",language,pace:"",image_analysis:"",image_generation_prompt:""});

export default function ProductionStudio({project,onOpenProject,onStudio}:{
  project:Project; onOpenProject:(id:string)=>Promise<void>; onStudio:(target?:"connections")=>void;
}) {
  const {language:uiLanguage,text:uiText}=useUiLanguage();
  const t=(zh:string,en:string,ja:string,tw=zh)=>uiText({"zh-CN":zh,"zh-TW":tw,en,ja});
  const l4=(value:Localized4)=>localText(uiLanguage,{"zh-CN":value[0],en:value[1],ja:value[2],"zh-TW":value[3]});
  const visualOptions=()=>VISUAL_STYLE_GROUPS.map(group=><optgroup key={group.id} label={l4(group.label)}>{VISUAL_STYLES.filter(item=>item.group===group.id).map(item=><option key={item.id} value={item.id}>{l4(item.label)}</option>)}</optgroup>);
  const narrativeOptions=()=>NARRATIVE_STYLE_GROUPS.map(group=><optgroup key={group.id} label={l4(group.label)}>{NARRATIVE_STYLES.filter(item=>item.group===group.id).map(item=><option key={item.id} value={item.id}>{l4(item.label)}</option>)}</optgroup>);
  const visualDescription=(id:string)=>l4(VISUAL_STYLES.find(item=>item.id===id)?.description||VISUAL_STYLES[0].description);
  const narrativeDescription=(id:string)=>l4(NARRATIVE_STYLES.find(item=>item.id===id)?.description||NARRATIVE_STYLES[0].description);
  const cardMeta=useMemo(()=>Object.fromEntries(CARD_KINDS.map(kind=>[kind,{
    title:localText(uiLanguage,CARD_META[kind].title),hint:localText(uiLanguage,CARD_META[kind].hint),upload:localText(uiLanguage,CARD_META[kind].upload),
  }])) as Record<CardKind,{title:string;hint:string;upload:string}>,[uiLanguage]);
  const selectionLabels=useMemo<Record<SelectableCardKind,string>>(()=>({
    characters:t("角色","Characters","人物","角色"),wardrobe:t("服装","Wardrobe","衣装","服裝"),props:t("道具","Props","小道具","道具"),
    environments:t("环境","Environment","環境","環境"),voices:t("声线","Voices","声","聲線"),
  }),[uiLanguage]);
  const [items,setItems]=useState<Summary[]>([]);
  const [projectQuery,setProjectQuery]=useState("");
  const [projectPage,setProjectPage]=useState(0);
  const [productionPage,setProductionPage]=useState<ProductionPage>("projects");
  const [production,setProduction]=useState<Production|null>(null);
  const [draft,setDraft]=useState(()=>blank(project));
  const [busy,setBusy]=useState("");
  const busyRef=useRef(false);
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [models,setModels]=useState<string[]>([]);
  const [imageInventory,setImageInventory]=useState<any>(null);
  const [videoWorkflows,setVideoWorkflows]=useState<VideoWorkflow[]>([]);
  const [imageModel,setImageModel]=useState(()=>localStorage.getItem("h3.production.imageModel")||"z_image_turbo_bf16.safetensors");
  const [keyframeSuggestions,setKeyframeSuggestions]=useState<{segment_id:string;index:number;reason:string;prompt:string}[]>([]);
  const [autoKeyframeSuggestions,setAutoKeyframeSuggestions]=useState<{segment_id:string;index:number;reason:string;prompt:string}[]>([]);
  const [cardKind,setCardKind]=useState<CardKind>("characters");
  const [openCardId,setOpenCardId]=useState<string|null>(null);
  const [collections,setCollections]=useState<CardCollection[]>([]);
  const [selectedCollection,setSelectedCollection]=useState("");
  const [cardSetQuery,setCardSetQuery]=useState("");
  const [openCardSetId,setOpenCardSetId]=useState<string|null>(null);
  const [cardSetDetails,setCardSetDetails]=useState<Record<string,CardCollectionDetail>>({});
  const [openEpisodeId,setOpenEpisodeId]=useState<string|null>(null);
  const [openSegmentId,setOpenSegmentId]=useState<string|null>(null);
  const [outputs,setOutputs]=useState<ProductionOutputs|null>(null);
  const [outputsLoading,setOutputsLoading]=useState(false);
  const [merging,setMerging]=useState(false);
  const [bulkAction,setBulkAction]=useState<""|"prompts"|"redo-prompts"|"videos"|"redo-videos"|"all"|"full">("");
  const autoMergeAttempt=useRef("");
  const totalSeconds=useMemo(()=>production?.segments.reduce((n,s)=>n+s.duration,0)||0,[production?.segments]);
  const currentEpisodeTiming=useMemo(()=>episodeTiming(production?.episode_minutes||draft.episode_minutes),[production?.episode_minutes,draft.episode_minutes]);
  const currentEpisodeForPlan=production?.episodes.find(episode=>episode.index===production.current_episode)||production?.episodes[0];
  const ready=production?.segments.filter(s=>s.status==="ready").length||0;
  const stale=production?.segments.filter(s=>s.status==="stale").length||0;
  const overviewKind=OVERVIEW_CARD_KINDS.includes(cardKind as OverviewCardKind)?cardKind as OverviewCardKind:null;
  const filteredItems=useMemo(()=>{const query=projectQuery.trim().toLocaleLowerCase();return query?items.filter(item=>item.title.toLocaleLowerCase().includes(query)):items;},[items,projectQuery]);
  const filteredCollections=useMemo(()=>{const query=cardSetQuery.trim().toLocaleLowerCase();return query?collections.filter(item=>item.name.toLocaleLowerCase().includes(query)):collections;},[collections,cardSetQuery]);
  const projectPages=Math.max(1,Math.ceil(filteredItems.length/12));
  const shownItems=useMemo(()=>{
    const page=filteredItems.slice(projectPage*12,projectPage*12+12);
    const current=items.find(item=>item.id===production?.id);
    return current&&!page.some(item=>item.id===current.id)?[current,...page]:page;
  },[filteredItems,items,production?.id,projectPage]);
  useEffect(()=>setProjectPage(0),[projectQuery]);
  useEffect(()=>{if(projectPage>=projectPages)setProjectPage(projectPages-1);},[projectPage,projectPages]);

  const refreshList=async()=>setItems(await api("/productions"));
  const refreshCollections=async()=>setCollections(await api("/card-collections"));
  const refreshVideoWorkflows=async()=>setVideoWorkflows(await api("/video-workflows"));
  const refreshImageModels=async()=>{const x=await api("/assets/generators") as any;setImageInventory(x);setModels(x.models||[]);
    setImageModel(current=>x.models?.includes(current)?current:(x.default_model||current));};
  const chooseImageModel=(value:string)=>{setImageModel(value);localStorage.setItem("h3.production.imageModel",value);};
  useEffect(()=>{void refreshList();void refreshCollections();void refreshImageModels().catch(()=>{});void refreshVideoWorkflows();},[]);
  useEffect(()=>{if(!production)setDraft(blank(project));},[project.id]);

  const run=async(label:string,fn:()=>Promise<void>)=>{
    if(busyRef.current)return;busyRef.current=true;setBusy(label);setError("");setNotice("");
    try{await fn();}catch(e){setError((e as Error).message);}finally{busyRef.current=false;setBusy("");}
  };
  const focusCardLibrary=()=>requestAnimationFrame(()=>requestAnimationFrame(()=>
    document.getElementById("production-card-library")?.scrollIntoView({behavior:"smooth",block:"start"})));
  const create=async(focusCards=false)=>{
    const value=await api("/productions",{...draft,source_project:project});
    setProduction(value);setDraft(value);setKeyframeSuggestions([]);setAutoKeyframeSuggestions([]);await refreshList();
    setNotice(focusCards?t("制作项目已建立。请先完善资产卡，再进行智能拆分。","Production created. Complete the asset cards, then plan with AI.","制作プロジェクトを作成しました。素材カードを整えてからAIで分割してください。","製作專案已建立。請先完善資產卡，再進行智慧拆分。"):t("制作项目已建立。现在可以智能拆分。","Production created. It is ready for AI planning.","制作プロジェクトを作成しました。AIで分割できます。","製作專案已建立。現在可以智慧拆分。"));
    setProductionPage(focusCards?"assets":"script");
    if(focusCards)focusCardLibrary();
    return value as Production;
  };
  const save=async(value=production)=>{
    if(!value)return create();
    const next=await apiPatch("/productions/"+value.id,{
      title:value.title,brief:value.brief,style_bible:value.style_bible,
       language:value.language,prompt_version:value.prompt_version,auto_merge:value.auto_merge,auto_continue_previous:value.auto_continue_previous,task_state:value.task_state,
      video_aspect_ratio:value.video_aspect_ratio,video_resolution:value.video_resolution,video_quality:value.video_quality,video_steps:value.video_steps,
      auto_keyframes_enabled:value.auto_keyframes_enabled,auto_keyframe_model:value.auto_keyframe_model,
      character_bible:value.character_bible,continuity_notes:value.continuity_notes,
      series_voice_style:value.series_voice_style,
      visual_style_preset:value.visual_style_preset,visual_style_custom:value.visual_style_custom,narrative_style:value.narrative_style,narrative_style_custom:value.narrative_style_custom,narrative_notes:value.narrative_notes,
      episode_count:value.episode_count,episode_minutes:value.episode_minutes,current_episode:value.current_episode,episodes:value.episodes,
      card_collection_id:value.card_collection_id,card_collection_name:value.card_collection_name,
      cards:value.cards,overview_asset_ids:value.overview_asset_ids,segments:value.segments});
    setProduction(next);setDraft(next);await refreshList();return next as Production;
  };
  const plan=(useAI:boolean)=>run(useAI?t("分析剧情并动态拆段","Analyse story and plan dynamic clips","物語を分析して動的に分割","分析劇情並動態拆段"):t("按本地规则动态拆段","Plan dynamic clips with local rules","ローカル規則で動的に分割","按本地規則動態拆段"),async()=>{
    const current=production?await save():await create();
    const next=await api("/productions/"+current.id+"/plan",{use_ai:useAI},undefined,undefined,{timeoutMs:240000});
    setProduction(next);setDraft(next);setKeyframeSuggestions([]);await refreshList();
    setNotice(next.planner==="local_ai"?t("本地 AI 已按本集目标总时长拆分，并为每段选择 5–15 秒时长。","Local AI matched the episode target and selected 5–15 seconds for each clip.","ローカルAIが話の目標尺に合わせ、各クリップを5〜15秒に設定しました。","本地 AI 已按本集目標總時長拆分，並為每段選擇 5–15 秒時長。"):t("已用本地动态时长规则完成拆分。","Dynamic local timing is complete.","ローカル動的時間推定が完了しました。","已用本地動態時長規則完成拆分。"));
  });
  const planCards=()=>run(t("AI 分析剧本并补全文字卡","AI analyse script and complete text cards","AIで脚本を分析して文章カードを補完","AI 分析劇本並補全文字卡"),async()=>{
    const saved=await save();
    const next=await api("/productions/"+saved.id+"/cards/plan",{force:true},undefined,undefined,{timeoutMs:900000}) as Production;
    setProduction(next);setDraft(next);await refreshList();
    if(next.card_planner_warning)setNotice(next.card_planner_warning);
    else setNotice(t("AI 已从剧本补全缺少的文字卡。已有文字、图片和声音均已保留。","AI completed missing text cards from the script. Existing text, images and audio were preserved.","AIが脚本から不足する文章カードを補完しました。既存の文章・画像・音声は保持されています。","AI 已從劇本補全缺少的文字卡。已有文字、圖片和聲音均已保留。"));
  });
  const planEpisodes=(useAI:boolean)=>run(useAI?t("规划全部剧集","Plan all episodes","全エピソードを計画","規劃全部劇集"):t("本地规则划分剧集","Split episodes locally","ローカル規則で分割","本地規則劃分劇集"),async()=>{
    const saved=await save();
    const next=await api("/productions/"+saved.id+"/episodes/plan",{use_ai:useAI},undefined,undefined,{timeoutMs:900000}) as Production;
    setProduction(next);setDraft(next);setOpenEpisodeId(next.episodes[0]?.id||null);await refreshList();
    setNotice(next.episode_planner==="local_ai"?t("本地 AI 已规划全部剧集并建立每集出场/回归角色表。","Local AI planned every episode with cast and returning-character tracking.","ローカルAIが全話と登場・再登場人物を計画しました。","本地 AI 已規劃全部劇集並建立每集出場／回歸角色表。"):t("已按原文顺序建立剧集草案，可继续手动调整。","An ordered offline episode draft is ready for editing.","原文順のエピソード案を作成しました。","已按原文順序建立劇集草案，可繼續手動調整。"));
  });
  const planEpisodeClips=(episode:Episode)=>run(t("细分当前剧集","Plan current episode clips","現在話をクリップ化","細分目前劇集"),async()=>{
    if(!production)return;
    const saved=await save({...production,current_episode:episode.index});
    const next=await api("/productions/"+saved.id+"/plan",{use_ai:true},undefined,undefined,{timeoutMs:480000}) as Production;
    setProduction(next);setDraft(next);setKeyframeSuggestions([]);await refreshList();
    setProductionPage("storyboard");
    window.setTimeout(()=>document.getElementById("production-storyboards")?.scrollIntoView({behavior:"smooth",block:"start"}),100);
    const generated=next.segments.reduce((sum,item)=>sum+item.duration,0),target=Math.round(next.episode_minutes*60);
    setNotice(t("第 "+episode.index+" 集已按剧情拆成 "+next.segments.length+" 段，每段 5–15 秒；生成时长 "+generated+"/"+target+" 秒。","Episode "+episode.index+" is now "+next.segments.length+" story-driven clips of 5–15s; generated duration "+generated+"/"+target+"s.","第"+episode.index+"話を物語に沿って5〜15秒の"+next.segments.length+"クリップに分割しました。生成尺 "+generated+"/"+target+"秒。","第 "+episode.index+" 集已按劇情拆成 "+next.segments.length+" 段，每段 5–15 秒；生成時長 "+generated+"/"+target+" 秒。"));
  });
  const load=async(id:string,page:ProductionPage="script")=>{
    if(!id){setProduction(null);setDraft(blank(project));setKeyframeSuggestions([]);setAutoKeyframeSuggestions([]);setProductionPage("script");return;}
    const value=await api("/productions/"+id);setProduction(value);setDraft(value);setKeyframeSuggestions([]);setAutoKeyframeSuggestions([]);setError("");setNotice("");setProductionPage(page);
  };
  const startNewProduction=()=>{setProduction(null);setDraft(blank(project));setKeyframeSuggestions([]);setAutoKeyframeSuggestions([]);setError("");setNotice("");setProductionPage("script");};
  const importCurrentStudioContent=()=>setDraft(current=>({
    ...current,
    title:project.title==="Untitled film"?"":project.title,
    brief:project.story.text||"",
    style_bible:Object.values(project.style||{}).filter(Boolean).join("；"),
    character_bible:project.subjects.map(subject=>subject.name+": "+(subject.description||"")).join("\n"),
  }));
  const updateField=(key:keyof Production,value:any)=>setProduction(p=>p?{...p,[key]:value}:p);
  const updateQuality=(value:Production["video_quality"])=>setProduction(p=>p?{...p,video_quality:value,video_steps:value==="lora8"?"auto":p.video_steps}:p);
  const updateEpisode=(id:string,key:keyof Episode,value:any)=>setProduction(p=>p?{...p,episodes:p.episodes.map(ep=>ep.id===id?{...ep,[key]:value}:ep)}:p);
  const toggleEpisodeCharacter=(episode:Episode,cardId:string)=>{
    const ids=episode.character_card_ids.includes(cardId)?episode.character_card_ids.filter(id=>id!==cardId):[...episode.character_card_ids,cardId];
    updateEpisode(episode.id,"character_card_ids",ids);
  };
  const updateSegment=(id:string,key:keyof Segment,value:any)=>setProduction(p=>p?{
    ...p,segments:p.segments.map(s=>s.id===id?{...s,[key]:value}:s)}:p);
  const reindexSegments=(segments:Segment[])=>segments.map((segment,index)=>({...segment,index:index+1}));
  const addSegment=(afterIndex?:number)=>setProduction(p=>{
    if(!p||p.segments.length>=64)return p;
    const at=afterIndex==null?p.segments.length:Math.min(p.segments.length,afterIndex+1);
    const segment:Segment={
      id:crypto.randomUUID(),index:at+1,title:t("新片段","New clip","新しいクリップ","新片段"),
      story:"",setting:"",action:"",ending:"",duration:5,
      duration_reason:t("人工新增；请按动作和对白需要调整为 5–15 秒。","Added manually; set 5–15 seconds to fit the action and dialogue.","手動追加。動作と台詞に合わせて5〜15秒に調整してください。","人工新增；請按動作和對白需要調整為 5–15 秒。"),
      dialogue:[],image_prompt:"",status:"unprepared",stale_reasons:[],keyframe_asset_ids:[],image_run_ids:[],
      prompt_direction:"",video_prompt:"",video_prompt_source:"",workflow_profile_id:"builtin",
      card_selection:{characters:[],wardrobe:[],props:[],environments:[],voices:[]},card_selection_source:"heuristic",
      selected_video_run_id:null,last_video_run_id:null,project_id:null,
    };
    const segments=[...p.segments];segments.splice(at,0,segment);
    setOpenSegmentId(segment.id);
    return {...p,segments:reindexSegments(segments)};
  });
  const moveSegment=(id:string,direction:-1|1)=>setProduction(p=>{
    if(!p)return p;
    const index=p.segments.findIndex(segment=>segment.id===id),target=index+direction;
    if(index<0||target<0||target>=p.segments.length)return p;
    const segments=[...p.segments];[segments[index],segments[target]]=[segments[target],segments[index]];
    return {...p,segments:reindexSegments(segments)};
  });
  const deleteSegment=(segment:Segment)=>{
    if(!window.confirm(t(
      "从本集删除第 "+segment.index+" 段“"+segment.title+"”？已生成的视频文件会保留，但不再进入本项目合片。",
      "Remove clip "+segment.index+" “"+segment.title+"”? Existing video files are kept but will no longer be included in this production.",
      "クリップ "+segment.index+"「"+segment.title+"」を削除しますか？生成済み映像ファイルは保持されますが、この作品の結合対象から外れます。",
      "從本集刪除第 "+segment.index+" 段「"+segment.title+"」？已生成的影片檔案會保留，但不再進入本專案合片。")))return;
    setProduction(p=>p?{...p,segments:reindexSegments(p.segments.filter(item=>item.id!==segment.id))}:p);
    if(openSegmentId===segment.id)setOpenSegmentId(null);
  };
  const updateDialogue=(segmentId:string,lineIndex:number,key:keyof Dialogue,value:any)=>setProduction(p=>p?{
    ...p,segments:p.segments.map(s=>s.id===segmentId?{...s,dialogue:s.dialogue.map((d,i)=>i===lineIndex?{...d,[key]:value}:d)}:s)}:p);
  const addDialogue=(segmentId:string)=>setProduction(p=>p?{...p,segments:p.segments.map(s=>s.id===segmentId?{
    ...s,dialogue:[...s.dialogue,{speaker:"",text:"",language:LANGUAGE_NAMES[p.language],voiceover:false}]}:s)}:p);
  const removeDialogue=(segmentId:string,lineIndex:number)=>setProduction(p=>p?{...p,segments:p.segments.map(s=>s.id===segmentId?{
    ...s,dialogue:s.dialogue.filter((_,i)=>i!==lineIndex)}:s)}:p);
  const updateCard=(kind:CardKind,id:string,key:keyof ProductionCard,value:any)=>setProduction(p=>p?{
    ...p,cards:{...p.cards,[kind]:p.cards[kind].map(card=>card.id===id?{...card,[key]:value}:card)}}:p);
  const addCard=(kind:CardKind)=>{const card=newCard(kind,production?.language||"zh-CN");setProduction(p=>p?{
    ...p,cards:{...p.cards,[kind]:[...p.cards[kind],card]}}:p);setOpenCardId(card.id);};
  const removeCard=(kind:CardKind,id:string)=>setProduction(p=>p?{
    ...p,cards:{...p.cards,[kind]:p.cards[kind].filter(card=>card.id!==id)}}:p);
  const unlinkCardAsset=(kind:CardKind,id:string,assetId:string)=>setProduction(p=>p?{
    ...p,cards:{...p.cards,[kind]:p.cards[kind].map(card=>card.id===id?{
      ...card,asset_ids:card.asset_ids.filter(value=>value!==assetId)}:card)}}:p);
  const unlinkOverviewAsset=(kind:OverviewCardKind)=>setProduction(p=>p?{
    ...p,overview_asset_ids:{...p.overview_asset_ids,[kind]:null}}:p);
  const uploadOverviewAsset=(kind:OverviewCardKind,file:File)=>run(t("上传总图","Upload overview","総覧画像を追加","上傳總圖"),async()=>{
    const saved=await save();
    const form=new FormData();form.append("file",file);
    const result=await api("/productions/"+saved.id+"/overviews/"+kind+"/asset",undefined,form,"POST",{timeoutMs:90000}) as {production:Production;asset:any};
    setProduction(result.production);setDraft(result.production);await refreshList();
    setNotice(t("总图已保存；当独立图缺失、同类项目较多或 9 张参考位不足时会自动优先调用。","Overview saved. It is used automatically when individual images are missing, the category is large, or the nine-reference budget is tight.","総覧画像を保存しました。個別画像不足・同種多数・9枚上限時に自動使用します。","總圖已儲存；獨立圖缺失、同類項目較多或 9 張參考位不足時會自動優先調用。"));
  });
  const uploadCardAsset=(kind:CardKind,card:ProductionCard,file:File)=>run(t("上传并绑定","Upload and bind ","アップロードして紐付け")+cardMeta[kind].title,async()=>{
    const saved=await save();
    const form=new FormData();form.append("file",file);
    const result=await api("/productions/"+saved.id+"/cards/"+kind+"/"+card.id+"/assets",undefined,form,"POST",{timeoutMs:90000}) as {production:Production;asset:any};
    let next=result.production;
    let styleAnalysisFailed="";
    if(kind==="styles"){
      try{
        const analysed=await api("/ai/analyse",{asset:{...result.asset,semantic_role:"style",role:"context",description:card.description}},undefined,undefined,{timeoutMs:300000}) as any;
        const observation=String(analysed?.observation?.observation||analysed?.observation||"").trim();
        if(observation){
          next={...next,cards:{...next.cards,styles:next.cards.styles.map(item=>item.id===card.id?{...item,image_analysis:[item.image_analysis,observation].filter(Boolean).join("\n").slice(0,6000)}:item)}};
          next=await apiPatch("/productions/"+next.id,{cards:next.cards}) as Production;
        }
      }catch(e){styleAnalysisFailed=(e as Error).message;}
    }
    setProduction(next);setDraft(next);await refreshList();
    if(kind==="styles")setNotice(styleAnalysisFailed?t("风格图已保存；当前本地视觉模型未完成分析。生成提示词时仍会把该图作为最高优先级风格上下文。","Style image saved. The local vision model could not analyse it now; prompt generation will still treat it as the highest-priority style context.","スタイル画像を保存しました。現在は視覚モデルで分析できませんでしたが、生成時には最優先のスタイル文脈として扱います。","風格圖已儲存；目前本地視覺模型未完成分析。生成提示詞時仍會把該圖作為最高優先級風格上下文。"):t("风格图已由本地视觉模型分析并保存；画面处理以图片分析为第一优先，文字预设只作补充。","The local vision model analysed and saved the style image. Its visual treatment now outranks text presets, which remain supporting guidance.","ローカル視覚モデルがスタイル画像を分析・保存しました。画像分析を最優先し、文章プリセットは補助として使います。","風格圖已由本地視覺模型分析並儲存；畫面處理以圖片分析為第一優先，文字預設只作補充。"));
    else setNotice(kind==="voices"?t("声线样本已绑定到角色卡；该角色说话时，干声主导音色与节奏，文字卡补充表演和禁忌。","The voice sample is bound to its character. When that character speaks, the clean sample leads vocal identity and cadence while the text card supplies acting and exclusions.","音声サンプルを人物に紐付けました。発話時はドライ音声を声質とリズムの基準にし、文章カードで演技と禁止事項を補います。","聲線樣本已綁定到角色卡；該角色說話時，乾聲主導音色與節奏，文字卡補充表演和禁忌。"):t("参考图已加入卡库；准备分镜时会按内容与 9 图上限自动选择。","The image is in the card library. Clip preparation selects it by content within the nine-image limit.","参照画像をカード庫に追加しました。クリップ準備時に内容と9画像上限に従って選択されます。","參考圖已加入卡庫；準備分鏡時會按內容與 9 圖上限自動選擇。"));
  });
  const cardImagePrompt=(current:Production,kind:CardKind,card:ProductionCard)=>{
    const subject={characters:"one character, exactly one individual",wardrobe:"the exact costume or garment",props:"one exact story prop",environments:"an empty location with no people"}[kind]||"one visual reference";
    const style=[current.style_bible,current.visual_style_custom].filter(Boolean).join(". ").slice(0,1200);
    return ["Production reference image for a consistent H3 film.","Depict "+subject+": "+card.name+".",
      card.description,card.notes,card.image_generation_prompt&&"Additional image-generation direction: "+card.image_generation_prompt,style&&"Visual style: "+style,
      "Preserve this card's identity and design. One coherent composition. No labels, lettering, collage, duplicates or extra characters."].filter(Boolean).join(" ").slice(0,6000);
  };
  const produceCardImage=async(current:Production,kind:CardKind,card:ProductionCard):Promise<Production>=>{
    if(!models.includes(imageModel))throw new Error(t("选定的本地生图模型不可用。请在设置刷新模型。","The selected image model is unavailable. Refresh models in Settings.","選択した画像モデルが使えません。設定で更新してください。","選定的本地生圖模型不可用。請在設定重新整理模型。"));
    const role={characters:"character",wardrobe:"wardrobe",props:"object",environments:"background"}[kind];
    if(!role)throw new Error("Only visual cards support image generation.");
    const runId=crypto.randomUUID();
    await api("/asset-runs",{request_id:runId,spec:{prompt:cardImagePrompt(current,kind,card),
      name:(current.title+" · "+card.name).slice(0,100),semantic_role:role,person_id:null,
      prompt_tag:"card-"+current.id.slice(0,8)+"-"+card.id.slice(0,8),model:imageModel,
      ...(kind==="environments"?productionKeyframeSize(current.video_aspect_ratio):{width:768,height:768}),
      seed:Math.floor(Math.random()*4294967296)}},undefined,"POST",{timeoutMs:90000});
    for(let attempt=0;attempt<600;attempt++){
      const state=await api("/asset-runs/"+runId) as any;
      if(state.status==="succeeded"){
        const result=await api("/productions/"+current.id+"/cards/"+kind+"/"+card.id+"/image/attach",{run_id:runId}) as {production:Production};
        setProduction(result.production);setDraft(result.production);return result.production;
      }
      if(["failed","cancelled","paused"].includes(state.status))throw new Error(state.error||"Local image generation stopped.");
      await sleep(1500);
    }
    throw new Error(t("图片仍在 ComfyUI 运行，请稍后刷新。","The image is still running in ComfyUI; refresh later.","画像はComfyUIで処理中です。後で更新してください。","圖片仍在 ComfyUI 執行，請稍後重新整理。"));
  };
  const generateCardImage=(kind:CardKind,card:ProductionCard)=>run(t("生成参考图","Generate reference image","参照画像を生成","生成參考圖"),async()=>{
    const current=await save();await produceCardImage(current,kind,card);
    setNotice(t("新图已绑定；旧图仍保留，可逐张移除。","New image attached; previous images remain available for removal.","新しい画像を追加しました。以前の画像は個別に外せます。","新圖已綁定；舊圖仍保留，可逐張移除。"));
  });
  const generateMissingCardImages=()=>run(t("批量生成缺少的资产图","Generate missing card images","不足カード画像を一括生成","批量生成缺少的資產圖"),async()=>{
    let current=await save();const kinds:CardKind[]=["characters","wardrobe","props","environments"];
    const missing=kinds.flatMap(kind=>current.cards[kind].filter(card=>!card.asset_ids.length).map(card=>({kind,card})));
    if(!missing.length){setNotice(t("所有视觉卡已有参考图。","All visual cards already have images.","すべての視覚カードに画像があります。","所有視覺卡已有參考圖。"));return;}
    for(let index=0;index<Math.min(missing.length,24);index++){
      const {kind,card}=missing[index];setBusy(t("资产生图 "+(index+1)+"/"+Math.min(missing.length,24),"Card images "+(index+1)+"/"+Math.min(missing.length,24),"カード画像 "+(index+1)+"/"+Math.min(missing.length,24),"資產生圖 "+(index+1)+"/"+Math.min(missing.length,24)));
      current=await produceCardImage(current,kind,card);
    }
    setNotice(missing.length>24?t("本次完成前 24 张；其余可再次点击批量补图。","The first 24 images are complete. Run the batch again for the remainder.","先頭24枚を生成しました。残りは再度一括生成してください。","本次完成前 24 張；其餘可再次點擊批量補圖。"):t("缺图卡片已逐张生成；可检查、删除或重做。","Missing card images were generated sequentially; review, remove or regenerate each one.","不足画像を順に生成しました。確認・削除・再生成できます。","缺圖卡片已逐張生成；可檢查、刪除或重做。"));
  });
  const planCardsAndImages=()=>run(t("从剧情规划卡片并生图","Plan cards and images from story","物語からカードと画像を生成","從劇情規劃卡片並生圖"),async()=>{
    if(!models.includes(imageModel))throw new Error(t("请先到设置选择可用的本地生图配方。","Choose an available local image recipe in Settings first.","先に設定で利用可能な画像レシピを選択してください。","請先到設定選擇可用的本地生圖配方。"));
    let current=await save();current=await api("/productions/"+current.id+"/cards/plan",{force:true},undefined,"POST",{timeoutMs:900000}) as Production;
    setProduction(current);setDraft(current);
    if(current.card_planner_warning)throw new Error(current.card_planner_warning);
    const kinds:CardKind[]=["characters","wardrobe","props","environments"];
    const missing=kinds.flatMap(kind=>current.cards[kind].filter(card=>!card.asset_ids.length).map(card=>({kind,card})));
    for(let index=0;index<Math.min(missing.length,24);index++){
      setBusy(t("剧情资产生图 "+(index+1)+"/"+Math.min(missing.length,24),"Story assets "+(index+1)+"/"+Math.min(missing.length,24),"物語素材 "+(index+1)+"/"+Math.min(missing.length,24),"劇情資產生圖 "+(index+1)+"/"+Math.min(missing.length,24)));
      current=await produceCardImage(current,missing[index].kind,missing[index].card);
    }
    setNotice(missing.length>24?t("已按剧情建卡并生成前 24 张缺图；可再次点击批量补图。","Story cards and the first 24 missing images are ready; run missing-image batch for the rest.","物語カードと先頭24枚の画像を作成しました。残りは再度一括生成してください。","已按劇情建卡並生成前 24 張缺圖；可再次點擊批量補圖。"):t("已按剧情建卡并补图。请逐张审核，不满意可删除或再生成。","Story cards and missing images are ready. Review each; remove or regenerate as needed.","物語カードと画像を生成しました。確認し、必要なら削除・再生成してください。","已按劇情建卡並補圖。請逐張審核，不滿意可刪除或再生成。"));
  });
  const saveCollection=()=>run(t("保存命名卡组","Save named card set","名前付きカードセットを保存","儲存命名卡組"),async()=>{
    const saved=await save();
    const result=await api("/productions/"+saved.id+"/card-collection",{name:saved.card_collection_name}) as any;
    setProduction({...saved,card_collection_id:result.id,card_collection_name:result.name});setSelectedCollection(result.id);await refreshCollections();
    setNotice(t("已增量同步到独立卡组；其他项目已有的卡和图片不会被本项目的空白卡覆盖。以后其他剧本可合并载入。","Synced to the independent set without deleting cards or images supplied by other projects. Other scripts can merge it in.","独立カードセットへ追加同期しました。別作品のカードや画像は削除されず、他の脚本にも統合できます。","已增量同步到獨立卡組；其他專案已有的卡和圖片不會被本專案的空白卡覆蓋。"));
  });
  const applyCollection=()=>run(t("载入卡组","Load card set","カードセットを読込","載入卡組"),async()=>{
    if(!production||!selectedCollection)return;
    const next=await api("/productions/"+production.id+"/card-collection/"+selectedCollection+"/apply",{}) as Production;
    setProduction(next);setDraft(next);setNotice(t("已按名称合并卡片；本项目原有卡片保留。","Cards were merged by name; this production's existing cards were kept.","名前でカードを統合し、現在のカードは保持しました。","已按名稱合併卡片；本專案原有卡片保留。"));
  });
  const toggleCardSetDetails=(item:CardCollection)=>{
    if(openCardSetId===item.id){setOpenCardSetId(null);return;}
    if(cardSetDetails[item.id]){setOpenCardSetId(item.id);return;}
    void run(t("读取卡组","Open card set","カードセットを開く","讀取卡組"),async()=>{
      const detail=await api("/card-collections/"+item.id) as CardCollectionDetail;
      setCardSetDetails(current=>({...current,[item.id]:detail}));setOpenCardSetId(item.id);
    });
  };
  const applyManagedCardSet=(item:CardCollection)=>{
    if(!production){setNotice(t("请先在“剧集 / 片段”打开或创建一个片段，再套用资料库。","Open or create an episode/clip part before applying the library.","先に「エピソード／クリップ」でパートを開くか作成してから資料庫を適用してください。","請先在「劇集／片段」開啟或建立一個片段，再套用資料庫。"));return;}
    void run(t("套用卡组","Apply card set","カードセットを適用","套用卡組"),async()=>{
      const saved=await save();
      const next=await api("/productions/"+saved.id+"/card-collection/"+item.id+"/apply",{}) as Production;
      setProduction(next);setDraft(next);setSelectedCollection(item.id);await refreshList();
      setNotice(t("卡组已合并到当前项目。同名项目卡优先，角色关联已自动重绑，声线语言已跟随项目语言。","The set was merged into the current project. Existing same-name cards take priority, links were rebound, and voice language now follows the project.","カードセットを現在の作品に統合しました。同名カードを優先し、関連付けを再接続して、音声言語を作品言語に合わせました。","卡組已合併到目前專案。同名專案卡優先，角色關聯已自動重綁，聲線語言已跟隨專案語言。"));
    });
  };
  const renameManagedCardSet=(item:CardCollection)=>{
    const name=window.prompt(t("输入新的卡组名称","Enter a new card-set name","新しいカードセット名を入力","輸入新的卡組名稱"),item.name)?.trim();
    if(!name||name===item.name)return;
    void run(t("重命名卡组","Rename card set","カードセット名を変更","重新命名卡組"),async()=>{
      const detail=await apiPatch("/card-collections/"+item.id,{name}) as CardCollectionDetail;
      setCardSetDetails(current=>({...current,[item.id]:detail}));
      if(production?.card_collection_id===item.id)setProduction({...production,card_collection_name:detail.name});
      await refreshCollections();
      setNotice(t("卡组已重命名；已关联项目中的卡片内容没有变化。","The card set was renamed; card contents in linked projects were unchanged.","カードセット名を変更しました。関連作品内のカード内容は変わりません。","卡組已重新命名；已關聯專案中的卡片內容沒有變化。"));
    });
  };
  const duplicateManagedCardSet=(item:CardCollection)=>{
    const defaultName=item.name+" "+t("副本","copy","コピー","副本");
    const name=window.prompt(t("复制为新卡组","Name the duplicated card set","複製するカードセット名","複製為新卡組"),defaultName)?.trim();
    if(!name)return;
    void run(t("复制卡组","Duplicate card set","カードセットを複製","複製卡組"),async()=>{
      const detail=await api("/card-collections/"+item.id+"/duplicate",{name}) as CardCollectionDetail;
      setCardSetDetails(current=>({...current,[detail.id]:detail}));await refreshCollections();setOpenCardSetId(detail.id);
      setNotice(t("已创建独立副本；修改副本不会改变原卡组。","An independent copy was created; editing it will not change the original set.","独立したコピーを作成しました。コピーを変更しても元のセットは変わりません。","已建立獨立副本；修改副本不會改變原卡組。"));
    });
  };
  const exportManagedCardSet=(item:CardCollection)=>void run(t("导出卡组","Export card set","カードセットを書き出す","匯出卡組"),async()=>{
    const detail=cardSetDetails[item.id]||await api("/card-collections/"+item.id) as CardCollectionDetail;
    setCardSetDetails(current=>({...current,[item.id]:detail}));
    downloadText(safeDownloadName(item.name)+"-card-set.json",JSON.stringify(detail,null,2),"application/json");
    setNotice(t("卡组 JSON 已导出。素材文件仍保存在本机卡库中。","The card-set JSON was exported. Media files remain in the local asset library.","カードセットJSONを書き出しました。素材ファイルはローカル素材庫に残ります。","卡組 JSON 已匯出。素材檔案仍保存在本機卡庫中。"));
  });
  const deleteManagedCardSet=(item:CardCollection)=>{
    if(!window.confirm(t("确定归档卡组“"+item.name+"”吗？项目中已有的卡片、图片和音频都不会删除。","Archive card set “"+item.name+"”? Cards, images and audio already copied into projects will not be deleted.","カードセット「"+item.name+"」をアーカイブしますか？作品内のカード・画像・音声は削除されません。","確定封存卡組「"+item.name+"」嗎？專案中已有的卡片、圖片和音訊都不會刪除。")))return;
    void run(t("归档卡组","Archive card set","カードセットをアーカイブ","封存卡組"),async()=>{
      await api("/card-collections/"+item.id,undefined,undefined,"DELETE");
      setCardSetDetails(current=>{const next={...current};delete next[item.id];return next;});
      if(openCardSetId===item.id)setOpenCardSetId(null);if(selectedCollection===item.id)setSelectedCollection("");
      if(production?.card_collection_id===item.id)setProduction({...production,card_collection_id:null});
      await refreshCollections();
      setNotice(t("卡组已安全归档。项目卡片和全部素材均已保留。","The card set was safely archived. Project cards and all media were preserved.","カードセットを安全にアーカイブしました。作品カードと素材はすべて保持されています。","卡組已安全封存。專案卡片和全部素材均已保留。"));
    });
  };

  const prepareOne=(segment:Segment,open=false)=>run(t("准备 H3 分镜工程","Prepare H3 clip project","H3クリッププロジェクトを準備","準備 H3 分鏡專案"),async()=>{
    if(open&&production?.task_state==="paused"&&segment.project_id){await onOpenProject(segment.project_id);onStudio();return;}
    const saved=await save();
    const result=await api("/productions/"+saved.id+"/segments/"+segment.id+"/materialize",{},undefined,undefined,{timeoutMs:60000});
    setProduction(result.production);await refreshList();
    if(open){await onOpenProject(result.project.id);onStudio();}
    else setNotice(t("第 "+segment.index+" 段已成为独立 H3 工程。","Clip "+segment.index+" is now an independent H3 project.","クリップ "+segment.index+" を独立したH3プロジェクトにしました。","第 "+segment.index+" 段已成為獨立 H3 專案。"));
  });
  const prepareMissing=()=>run(t("只补齐缺失或已过期的 H3 工程","Prepare missing or stale H3 projects only","不足または期限切れのH3プロジェクトのみ準備","只補齊缺失或已過期的 H3 專案"),async()=>{
    const saved=await save();
    const result=await api("/productions/"+saved.id+"/materialize",{only_missing:true},undefined,undefined,{timeoutMs:180000});
    setProduction(result.production);await refreshList();
    setNotice(t("已准备 "+result.prepared.length+" 段，保留 "+result.skipped.length+" 段现有成果。","Prepared "+result.prepared.length+" clips and retained "+result.skipped.length+" existing results.",result.prepared.length+" 件を準備し、既存の "+result.skipped.length+" 件を保持しました。","已準備 "+result.prepared.length+" 段，保留 "+result.skipped.length+" 段現有成果。"));
  });
  const refreshOutputs=async(id=production?.id,silent=false)=>{
    if(!id){setOutputs(null);return null;}
    if(!silent)setOutputsLoading(true);
    try{const value=await api("/productions/"+id+"/outputs") as ProductionOutputs;setOutputs(value);return value;}
    catch(e){if(!silent)setError((e as Error).message);return null;}
    finally{if(!silent)setOutputsLoading(false);}
  };
  const selectVideo=async(row:ProductionOutputRow,runId:string)=>run(t("选择采用版本","Select adopted take","採用テイクを選択","選擇採用版本"),async()=>{
    if(!production)return;
    const value=await apiPatch("/productions/"+production.id+"/segments/"+row.segment_id+"/video",{run_id:runId||null}) as ProductionOutputs;
    autoMergeAttempt.current="";setOutputs(value);
  });
  const setAutoMerge=async(enabled:boolean)=>{
    if(!production)return;
    setProduction({...production,auto_merge:enabled});
    try{const value=await apiPatch("/productions/"+production.id,{auto_merge:enabled}) as Production;setProduction(value);autoMergeAttempt.current="";}
    catch(e){setError((e as Error).message);}
  };
  const setTaskState=(state:"active"|"paused")=>run(state==="paused"?t("暂停后续任务","Pause queued work","後続タスクを一時停止","暫停後續任務"):t("继续制作任务","Resume production","制作タスクを再開","繼續製作任務"),async()=>{
    if(!production)return;
    const value=await apiPatch("/productions/"+production.id,{task_state:state}) as Production;
    setProduction(value);setDraft(value);
    setNotice(state==="paused"?t("已暂停新的规划、提示词、生成和合片任务。已经进入 ComfyUI 的当前渲染不会被强行中断。","New planning, prompting, renders and assembly are paused. A render already inside ComfyUI is not interrupted.","新しい計画・プロンプト・生成・結合を一時停止しました。ComfyUIで実行中のレンダーは中断しません。","已暫停新的規劃、提示詞、生成和合片任務。已進入 ComfyUI 的目前渲染不會被強制中斷。"):t("制作任务已继续，可以提交下一项工作。","Production resumed; new work can be submitted.","制作タスクを再開しました。新しい処理を送信できます。","製作任務已繼續，可以提交下一項工作。"));
  });
  const exportStory=()=>{if(!production)return;downloadText(safeDownloadName(production.title)+"-story.md",productionStoryExport(production),"text/markdown;charset=utf-8");};
  const exportDialogue=()=>{if(!production)return;downloadText(safeDownloadName(production.title)+"-dialogue.txt",productionDialogueExport(production),"text/plain;charset=utf-8");};
  const deleteProduction=()=>run(t("删除片段","Delete part","パートを削除","刪除片段"),async()=>{
    if(!production)return;
    if(!window.confirm(t("确定从“剧集 / 片段”列表删除“"+production.title+"”吗？当前卡库会自动保存为独立命名卡组；H3 分镜工程和视频也会保留。","Remove “"+production.title+"” from the Episodes / Clips list? Its current cards will be saved automatically as an independent named card set; H3 clip projects and videos are also kept.","「"+production.title+"」をエピソード／クリップ一覧から削除しますか？現在のカード庫は独立した名前付きカードセットとして自動保存され、H3クリップと映像も保持されます。","確定從「劇集／片段」列表刪除「"+production.title+"」嗎？目前卡庫會自動儲存為獨立命名卡組；H3 分鏡專案和影片也會保留。")))return;
    const deleted=await api("/productions/"+production.id,undefined,undefined,"DELETE") as {card_collection_name?:string|null};
    setProduction(null);setOutputs(null);setDraft(blank(project));await refreshList();await refreshCollections();
    setNotice(t("片段已移除；卡库已保留为“"+(deleted.card_collection_name||production.card_collection_name)+"”，关联 H3 工程和视频结果也仍保留。","Part removed. Its cards remain as “"+(deleted.card_collection_name||production.card_collection_name)+"”; linked H3 projects and video results were also preserved.","パートを外しました。カードは「"+(deleted.card_collection_name||production.card_collection_name)+"」として残り、関連H3プロジェクトと映像も保持されています。","片段已移除；卡庫已保留為「"+(deleted.card_collection_name||production.card_collection_name)+"」，關聯 H3 專案和影片結果也仍保留。"));
  });
  const importVideoWorkflow=(file:File)=>run(t("导入 ComfyUI API 工作流","Import ComfyUI API workflow","ComfyUI APIワークフローを読込","匯入 ComfyUI API 工作流"),async()=>{
    if(file.size>2_000_000)throw new Error(t("工作流 JSON 不能超过 2 MB。","Workflow JSON must be 2 MB or smaller.","ワークフローJSONは2MB以下にしてください。","工作流 JSON 不能超過 2 MB。"));
    let graph:any;try{graph=JSON.parse(await file.text());}catch{throw new Error(t("请选择 ComfyUI 导出的 API 格式 JSON，不是界面截图或普通 workflow。","Choose a ComfyUI API-format JSON export, not an image or regular UI workflow.","画像や通常のUIワークフローではなく、ComfyUIのAPI形式JSONを選択してください。","請選擇 ComfyUI 匯出的 API 格式 JSON，不是介面截圖或普通 workflow。"));}
    const result=await api("/video-workflows",{name:file.name.replace(/\.json$/i,""),description:"Imported from "+file.name,graph}) as VideoWorkflow;
    await refreshVideoWorkflows();
    setNotice(t("已导入“"+result.name+"”。它不会替换内置流程；可在每个片段单独选择。","Imported “"+result.name+"”. It does not replace the built-in workflow and can be selected per clip.","「"+result.name+"」を読み込みました。内蔵ワークフローは置き換えず、クリップごとに選べます。","已匯入「"+result.name+"」。它不會取代內置流程；可在每個片段單獨選擇。"));
  });
  const deleteVideoWorkflow=(workflow:VideoWorkflow)=>run(t("删除视频工作流","Delete video workflow","映像ワークフローを削除","刪除影片工作流"),async()=>{
    if(workflow.builtin)return;
    if(!window.confirm(t("删除工作流“"+workflow.name+"”？已生成的视频不会删除。","Delete workflow “"+workflow.name+"”? Existing videos are kept.","ワークフロー「"+workflow.name+"」を削除しますか？生成済み映像は保持されます。","刪除工作流「"+workflow.name+"」？已生成的影片不會刪除。")))return;
    await api("/video-workflows/"+workflow.id,undefined,undefined,"DELETE");await refreshVideoWorkflows();
  });
  const buildFilm=async(automatic=false)=>{
    if(!production||merging)return;
    setMerging(true);setError("");
    try{
      const value=await api("/productions/"+production.id+"/film",{},undefined,"POST",{timeoutMs:1800000}) as ProductionOutputs;
      setOutputs(value);setNotice(automatic?t("全部分镜完成，最终成片已自动合并。","All clips are ready and the final film was assembled automatically.","全クリップが完成し、最終映像を自動結合しました。","全部分鏡完成，最終成片已自動合併。"):t("最终成片已按分镜顺序合并。","The final film was assembled in storyboard order.","絵コンテ順に最終映像を結合しました。","最終成片已按分鏡順序合併。"));
    }catch(e){setError((e as Error).message);}finally{setMerging(false);}
  };
  const openProductionFilmFolder=()=>run(t("打开文件位置","Open film location","映像の保存場所を開く","開啟檔案位置"),async()=>{
    if(!production)throw new Error("No production is open.");
    const result=await api("/productions/"+production.id+"/film/open",{},undefined,"POST") as {path:string};
    setNotice(t("已在资源管理器打开：","Opened in Explorer: ","エクスプローラーで開きました：","已在檔案總管開啟：")+result.path);
  });
  useEffect(()=>{
    if(!production?.id){setOutputs(null);return;}
    let active=true;
    const poll=async()=>{try{const value=await api("/productions/"+production.id+"/outputs") as ProductionOutputs;if(active)setOutputs(value);}catch{/* Keep editing while ComfyUI or a result is unavailable. */}};
    void poll();const timer=window.setInterval(()=>void poll(),5000);
    return()=>{active=false;window.clearInterval(timer);};
  },[production?.id]);
  useEffect(()=>{
    if(production?.task_state==="paused"||bulkAction||!production?.auto_merge||!outputs?.all_ready||outputs.active_jobs>0||outputs.final_ready||!outputs.signature||merging)return;
    if(autoMergeAttempt.current===outputs.signature)return;
    autoMergeAttempt.current=outputs.signature;void buildFilm(true);
  },[production?.task_state,production?.auto_merge,outputs?.all_ready,outputs?.active_jobs,outputs?.final_ready,outputs?.signature,merging,bulkAction]);

  const rebuildPrompt=(segment:Segment,useAI=true)=>run(useAI?t("用本地 LLM 重做视频提示词","Rebuild video prompt with local LLM","ローカルLLMで映像プロンプトを再作成","用本地 LLM 重做影片提示詞"):t("按当前结构重建视频提示词","Recompile current video prompt","現在の構造から映像プロンプトを再構築","按目前結構重建影片提示詞"),async()=>{
    const saved=await save();
    const result=await api("/productions/"+saved.id+"/segments/"+segment.id+"/prompt",{use_ai:useAI},undefined,"POST",{timeoutMs:900000}) as any;
    setProduction(result.production);setDraft(result.production);await refreshList();
    setNotice(t("第 "+segment.index+" 段视频提示词已更新，用时 "+formatSeconds(result.seconds)+"。","Clip "+segment.index+" video prompt updated in "+formatSeconds(result.seconds)+".","クリップ "+segment.index+" の映像プロンプトを "+formatSeconds(result.seconds)+" で更新しました。","第 "+segment.index+" 段影片提示詞已更新，用時 "+formatSeconds(result.seconds)+"。"));
  });
  const submitVideoWithPromptRepair=async(current:Production,target:Segment)=>{
    const submit=()=>api("/productions/"+current.id+"/segments/"+target.id+"/video",{request_id:crypto.randomUUID(),new_seed:true},undefined,"POST",{timeoutMs:180000}) as Promise<any>;
    try{return await submit();}
    catch(e){
      const reason=(e as Error).message;
      if(!reason.includes("directions inherited from an older clip"))throw e;
      setBusy(t("第 "+target.index+" 段安全检查：自动重建提示词","Clip "+target.index+" safety check: rebuilding its prompt","クリップ "+target.index+" の安全確認：プロンプトを再作成","第 "+target.index+" 段安全檢查：自動重建提示詞"));
      const repaired=await api("/productions/"+current.id+"/segments/"+target.id+"/prompt",{use_ai:true},undefined,"POST",{timeoutMs:900000}) as {production:Production};
      current=repaired.production;setProduction(current);setDraft(current);
      return await api("/productions/"+current.id+"/segments/"+target.id+"/video",{request_id:crypto.randomUUID(),new_seed:true},undefined,"POST",{timeoutMs:180000}) as any;
    }
  };
  const generateVideo=(segment:Segment)=>run(t("提交第 "+segment.index+" 段视频","Submit clip "+segment.index+" video","クリップ "+segment.index+" の映像を送信","提交第 "+segment.index+" 段影片"),async()=>{
    let saved=await save();
    const target=saved.segments.find(item=>item.id===segment.id)||segment;
    if(segmentNeedsVideoPrompt(target)){
      setBusy(t("先更新本段视频提示词","Updating this clip's prompt first","先にこのクリップのプロンプトを更新","先更新本段影片提示詞"));
      const prompted=await api("/productions/"+saved.id+"/segments/"+segment.id+"/prompt",{use_ai:true},undefined,"POST",{timeoutMs:900000}) as {production:Production};
      saved=prompted.production;setProduction(saved);setDraft(saved);
    }
    setBusy(t("提交第 "+segment.index+" 段视频","Submit clip "+segment.index+" video","クリップ "+segment.index+" の映像を送信","提交第 "+segment.index+" 段影片"));
    const result=await submitVideoWithPromptRepair(saved,target);
    setProduction(result.production);setDraft(result.production);await refreshOutputs(saved.id,true);await refreshList();
    setNotice(t("第 "+segment.index+" 段已提交到 ComfyUI；可以在下方结果区查看进度或停止。","Clip "+segment.index+" was submitted to ComfyUI. Track or stop it in the results below.","クリップ "+segment.index+" をComfyUIへ送信しました。下の結果で進捗確認・停止ができます。","第 "+segment.index+" 段已提交到 ComfyUI；可在下方結果區查看進度或停止。"));
  });
  const generateAllPrompts=async(force=false)=>{
    if(busyRef.current||!production?.segments.length)return;
    busyRef.current=true;setBulkAction(force?"redo-prompts":"prompts");setError("");setNotice("");
    try{
      let current=await save();
      const targets=videoPromptTargets(current.segments,force);
      if(!targets.length){
        setNotice(t("全部片段已有与当前分镜同步的视频提示词，无需重复生成。","Every clip already has a video prompt synced to the current storyboard.","全クリップの映像プロンプトは現在の絵コンテと同期済みです。","全部片段已有與目前分鏡同步的影片提示詞，無需重複生成。"));
        return;
      }
      for(let i=0;i<targets.length;i++){
        const target=current.segments.find(item=>item.id===targets[i].id)||targets[i];
        setBusy(t("批量生成视频提示词：第 "+(i+1)+"/"+targets.length+" 段","Generating video prompts: "+(i+1)+"/"+targets.length,"映像プロンプトを一括生成："+(i+1)+"/"+targets.length,"批量生成影片提示詞：第 "+(i+1)+"/"+targets.length+" 段"));
        const result=await api("/productions/"+current.id+"/segments/"+target.id+"/prompt",{use_ai:true},undefined,"POST",{timeoutMs:900000}) as any;
        current=result.production;setProduction(current);setDraft(current);
      }
      await refreshList();
      setNotice(force?t("已重新生成全部 "+targets.length+" 段视频提示词；旧视频记录保留，可按需重做视频。","Regenerated all "+targets.length+" prompts. Existing video history is retained; regenerate videos as needed.","全"+targets.length+"件のプロンプトを再生成しました。以前の映像履歴は保持されます。","已重新生成全部 "+targets.length+" 段影片提示詞；舊影片紀錄保留，可按需重做影片。"):
        t("已生成 "+targets.length+" 段视频提示词；已有有效提示词保持不变。","Generated "+targets.length+" video prompts; valid existing prompts were kept.",targets.length+"件の映像プロンプトを生成しました。既存の有効なプロンプトは保持しました。","已生成 "+targets.length+" 段影片提示詞；已有有效提示詞保持不變。"));
    }catch(e){setError((e as Error).message);}finally{busyRef.current=false;setBulkAction("");setBusy("");}
  };
  const generateAllVideos=async(force=false)=>{
    if(busyRef.current||!production?.segments.length)return;
    if(force&&!window.confirm(t("将顺序重新渲染本集全部 "+production.segments.length+" 段视频，可能耗时较长；旧视频版本会保留。继续吗？","Regenerate all "+production.segments.length+" clips in order? This may take a while; old takes will be kept.","全"+production.segments.length+"件の映像を順番に再生成します。時間がかかる場合があります。旧テイクは保持されます。続けますか？","將依序重新渲染本集全部 "+production.segments.length+" 段影片，可能耗時較長；舊影片版本會保留。繼續嗎？")))return;
    busyRef.current=true;setBulkAction(force?"redo-videos":"videos");setError("");setNotice("");
    try{
      let current=await save();
      let overview=await api("/productions/"+current.id+"/outputs") as ProductionOutputs;
      setOutputs(overview);
      if(overview.active_jobs>0)throw new Error(t("已有视频任务正在运行，请等待或停止后再批量重做。","A video job is already running; wait or stop it before batch rendering.","映像処理中です。完了または停止後に再実行してください。","已有影片任務正在執行，請等待或停止後再批量重做。"));
      const targets=videoRenderTargets(current.segments,overview,force);
      if(!targets.length){
        setNotice(t("全部片段已有采用的视频版本，无需重复生成。","Every clip already has an adopted video take.","全クリップに採用済みの映像テイクがあります。","全部片段已有採用的影片版本，無需重複生成。"));
        return;
      }
      for(let i=0;i<targets.length;i++){
        let target=current.segments.find(item=>item.id===targets[i].id)||targets[i];
        if(!force&&segmentHasCompletedVideo(target,overview))continue;
        if(segmentNeedsVideoPrompt(target)){
          setBusy(t("批量生成视频：先准备第 "+(i+1)+"/"+targets.length+" 段提示词","Generate all videos: preparing prompt "+(i+1)+"/"+targets.length,"映像一括生成：プロンプト準備 "+(i+1)+"/"+targets.length,"批量生成影片：先準備第 "+(i+1)+"/"+targets.length+" 段提示詞"));
          const promptResult=await api("/productions/"+current.id+"/segments/"+target.id+"/prompt",{use_ai:true},undefined,"POST",{timeoutMs:900000}) as any;
          current=promptResult.production;setProduction(current);setDraft(current);
          target=current.segments.find(item=>item.id===target.id)||target;
        }
        setBusy(t("批量生成视频：提交第 "+(i+1)+"/"+targets.length+" 段","Generate all videos: submitting "+(i+1)+"/"+targets.length,"映像一括生成：送信 "+(i+1)+"/"+targets.length,"批量生成影片：提交第 "+(i+1)+"/"+targets.length+" 段"));
        const submitted=await submitVideoWithPromptRepair(current,target);
        current=submitted.production;setProduction(current);setDraft(current);
        let job=submitted.run as ProductionVideoJob;
        while(["preparing","queued","running","uncertain"].includes(job.status)){
          setBusy(t("批量生成视频：第 "+(i+1)+"/"+targets.length+" 段正在渲染 · "+(job.stage||job.status),"Generate all videos: rendering "+(i+1)+"/"+targets.length+" · "+(job.stage||job.status),"映像一括生成："+(i+1)+"/"+targets.length+" を処理中・"+(job.stage||job.status),"批量生成影片：第 "+(i+1)+"/"+targets.length+" 段正在渲染 · "+(job.stage||job.status)));
          await sleep(5000);
          job=await api("/video/runs/"+job.id) as ProductionVideoJob;
          overview=await api("/productions/"+current.id+"/outputs") as ProductionOutputs;setOutputs(overview);
        }
        if(job.status!=="succeeded")throw new Error(t("批量生成停在第 "+target.index+" 段：","Batch generation stopped at clip "+target.index+": ","一括生成はクリップ "+target.index+" で停止しました：","批量生成停在第 "+target.index+" 段：")+(job.error||job.stage||job.status));
        overview=await api("/productions/"+current.id+"/outputs") as ProductionOutputs;setOutputs(overview);
      }
      await refreshList();
      setNotice(force?t("已按顺序重新生成全部 "+targets.length+" 段视频；旧版本仍在生成记录中。","Regenerated all "+targets.length+" clips in order; old takes remain in the history.","全"+targets.length+"件の映像を順番に再生成しました。以前のテイクは履歴に残ります。","已依序重新生成全部 "+targets.length+" 段影片；舊版本仍保留在紀錄中。"):
        t("缺失的 "+targets.length+" 段视频已全部完成；原有完成版本未重复生成。","All "+targets.length+" missing videos are complete; existing takes were not regenerated.","不足していた "+targets.length+" 件の映像が完了しました。既存テイクは再生成していません。","缺失的 "+targets.length+" 段影片已全部完成；原有完成版本未重複生成。"));
    }catch(e){setError((e as Error).message);}finally{busyRef.current=false;setBulkAction("");setBusy("");await refreshOutputs(production?.id,true);}
  };
  const generateEpisodeFilm=async(options:{full?:boolean}={})=>{
    const full=!!options.full;
    if(busyRef.current||!production||(!full&&!production.segments.length))return;
    if(full&&!production.brief.trim()){setError(t("请先填写并保存故事原文 / 剧本。","Add and save the source story or screenplay first.","先に原作ストーリー／脚本を入力して保存してください。","請先填寫並儲存故事原文／劇本。"));return;}
    if(full&&!window.confirm(t("将为当前集依次补全文字卡、规划缺失分镜、生成缺失提示词和视频，并合并成片。已有成果不会重复覆盖；视频生成可能耗时较长。继续吗？","Run the current episode from text-card completion through missing storyboard, prompts, videos and final assembly? Existing work is kept; video rendering can take a long time.","現在話について文章カード補完、未作成の絵コンテ、プロンプト、映像、最終結合を順番に実行します。既存成果は保持され、映像生成には時間がかかります。続けますか？","將為目前集依序補全文字卡、規劃缺失分鏡、生成缺失提示詞和影片，並合併成片。已有成果不會重複覆蓋；影片生成可能耗時較長。繼續嗎？")))return;
    busyRef.current=true;setBulkAction(full?"full":"all");setError("");setNotice("");
    let currentId=production.id;
    try{
      if(full)setBusy(t("全流程 · 第 1/6 步：保存并检查项目","Full run · Step 1/6: save and check project","全工程・ステップ1/6：保存と確認","全流程 · 第 1/6 步：儲存並檢查專案"));
      let current=await save();currentId=current.id;
      if(full){
        if(current.task_state==="paused")throw new Error(t("当前任务已暂停，请先恢复后再运行全流程。","This task is paused. Resume it before starting the full run.","このタスクは一時停止中です。再開してから全工程を実行してください。","目前任務已暫停，請先恢復後再執行全流程。"));

        setBusy(t("全流程 · 第 2/6 步：补全文字卡","Full run · Step 2/6: complete text cards","全工程・ステップ2/6：文章カードを補完","全流程 · 第 2/6 步：補全文字卡"));
        current=await api("/productions/"+current.id+"/cards/plan",{force:false},undefined,"POST",{timeoutMs:900000}) as Production;
        setProduction(current);setDraft(current);

        if(!current.episodes.length){
          setBusy(t("全流程 · 第 3/6 步：规划剧集","Full run · Step 3/6: plan episode","全工程・ステップ3/6：エピソード計画","全流程 · 第 3/6 步：規劃劇集"));
          current=await api("/productions/"+current.id+"/episodes/plan",{use_ai:true},undefined,"POST",{timeoutMs:900000}) as Production;
          setProduction(current);setDraft(current);setOpenEpisodeId(current.episodes[0]?.id||null);
        }
        if(!current.segments.length){
          setBusy(t("全流程 · 第 3/6 步：拆分当前集分镜","Full run · Step 3/6: plan current episode clips","全工程・ステップ3/6：現在話をクリップ化","全流程 · 第 3/6 步：拆分目前集分鏡"));
          current=await api("/productions/"+current.id+"/plan",{use_ai:true},undefined,"POST",{timeoutMs:480000}) as Production;
          setProduction(current);setDraft(current);setKeyframeSuggestions([]);
        }
        if(!current.segments.length)throw new Error(t("没有生成可制作的分镜，请先检查剧本内容和剧集规划。","No producible storyboard clips were created. Check the screenplay and episode plan.","制作可能なクリップが作成されませんでした。脚本とエピソード計画を確認してください。","沒有生成可製作的分鏡，請先檢查劇本內容和劇集規劃。"));
        setProductionPage("storyboard");await refreshList();
      }
      let overview=await api("/productions/"+current.id+"/outputs") as ProductionOutputs;
      setOutputs(overview);
      if(overview.active_jobs>0)throw new Error(t("已有视频任务正在运行。请等待它完成或先停止，再启动一键生成。","A video job is already running. Wait for it to finish or stop it before starting one-click production.","映像タスクが実行中です。完了を待つか停止してから一括制作を開始してください。","已有影片任務正在執行。請等待完成或先停止，再啟動一鍵生成。"));

      if(current.auto_keyframes_enabled){
        if(!models.includes(current.auto_keyframe_model))throw new Error(t("已开启自动补关键帧，但选定的本地生图模型当前不可用。请先在设置检查 ComfyUI 和模型。","Automatic keyframes are on, but the selected local image model is unavailable. Check ComfyUI and the model in Settings.","自動キーフレームが有効ですが、選択した画像モデルを利用できません。設定でComfyUIとモデルを確認してください。","已開啟自動補關鍵影格，但選定的本地生圖模型目前不可用。請先在設定檢查 ComfyUI 和模型。"));
        const review=await api("/productions/"+current.id+"/keyframe-suggestions") as {suggestions:{segment_id:string;index:number;prompt:string}[]};
        for(let i=0;i<review.suggestions.length;i++){
          const suggestion=review.suggestions[i];
          setBusy(full?t("全流程 · 第 4/6 步：补环境关键帧 "+(i+1)+"/"+review.suggestions.length,"Full run · Step 4/6: environment keyframe "+(i+1)+"/"+review.suggestions.length,"全工程・ステップ4/6：背景キーフレーム "+(i+1)+"/"+review.suggestions.length,"全流程 · 第 4/6 步：補環境關鍵影格 "+(i+1)+"/"+review.suggestions.length):t("一键生成 · 补环境关键帧 "+(i+1)+"/"+review.suggestions.length,"One-click · environment keyframe "+(i+1)+"/"+review.suggestions.length,"一括制作・背景キーフレーム "+(i+1)+"/"+review.suggestions.length,"一鍵生成 · 補環境關鍵影格 "+(i+1)+"/"+review.suggestions.length));
          await api("/productions/"+current.id+"/segments/"+suggestion.segment_id+"/image",{
            request_id:crypto.randomUUID(),prompt:suggestion.prompt,model:current.auto_keyframe_model,
            ...productionKeyframeSize(current.video_aspect_ratio),seed:Math.floor(Math.random()*4294967296)},undefined,"POST",{timeoutMs:90000});
          let completed=false;
          for(let attempt=0;attempt<600;attempt++){
            const state=await api("/productions/"+current.id+"/segments/"+suggestion.segment_id+"/image/sync",{}) as {run:{status:string;error?:string};production:Production};
            current=state.production;setProduction(current);setDraft(current);
            if(state.run.status==="succeeded"){completed=true;break;}
            if(["failed","cancelled","paused"].includes(state.run.status))throw new Error(state.run.error||t("自动关键帧生成失败，视频尚未提交。","Automatic keyframe failed; no video has been submitted yet.","自動キーフレームが失敗しました。映像はまだ送信されていません。","自動關鍵影格失敗，影片尚未提交。"));
            await sleep(1500);
          }
          if(!completed)throw new Error(t("自动关键帧仍在运行；已暂停一键流程，请稍后刷新项目。","The keyframe is still running. One-click production stopped; refresh the project later.","キーフレームは処理中です。一括制作を停止したので後で更新してください。","自動關鍵影格仍在執行；已暫停一鍵流程，請稍後重新整理專案。"));
        }
      }

      const promptTargets=current.segments.filter(segmentNeedsVideoPrompt);
      for(let i=0;i<promptTargets.length;i++){
        const target=current.segments.find(item=>item.id===promptTargets[i].id)||promptTargets[i];
        setBusy(full?t("全流程 · 第 4/6 步：视频提示词 "+(i+1)+"/"+promptTargets.length,"Full run · Step 4/6: video prompts "+(i+1)+"/"+promptTargets.length,"全工程・ステップ4/6：映像プロンプト "+(i+1)+"/"+promptTargets.length,"全流程 · 第 4/6 步：影片提示詞 "+(i+1)+"/"+promptTargets.length):t("一键生成 · 第 1/3 步：提示词 "+(i+1)+"/"+promptTargets.length,"One-click production · Step 1/3: prompts "+(i+1)+"/"+promptTargets.length,"一括制作・ステップ1/3：プロンプト "+(i+1)+"/"+promptTargets.length,"一鍵生成 · 第 1/3 步：提示詞 "+(i+1)+"/"+promptTargets.length));
        const result=await api("/productions/"+current.id+"/segments/"+target.id+"/prompt",{use_ai:true},undefined,"POST",{timeoutMs:900000}) as any;
        current=result.production;setProduction(current);setDraft(current);
      }

      overview=await api("/productions/"+current.id+"/outputs") as ProductionOutputs;setOutputs(overview);
      const videoTargets=current.segments.filter(segment=>!segmentHasCompletedVideo(segment,overview));
      for(let i=0;i<videoTargets.length;i++){
        const target=current.segments.find(item=>item.id===videoTargets[i].id)||videoTargets[i];
        if(segmentHasCompletedVideo(target,overview))continue;
        setBusy(full?t("全流程 · 第 5/6 步：提交视频 "+(i+1)+"/"+videoTargets.length,"Full run · Step 5/6: submit video "+(i+1)+"/"+videoTargets.length,"全工程・ステップ5/6：映像を送信 "+(i+1)+"/"+videoTargets.length,"全流程 · 第 5/6 步：提交影片 "+(i+1)+"/"+videoTargets.length):t("一键生成 · 第 2/3 步：提交视频 "+(i+1)+"/"+videoTargets.length,"One-click production · Step 2/3: submit video "+(i+1)+"/"+videoTargets.length,"一括制作・ステップ2/3：映像を送信 "+(i+1)+"/"+videoTargets.length,"一鍵生成 · 第 2/3 步：提交影片 "+(i+1)+"/"+videoTargets.length));
        const submitted=await submitVideoWithPromptRepair(current,target);
        current=submitted.production;setProduction(current);setDraft(current);
        let job=submitted.run as ProductionVideoJob;
        while(["preparing","queued","running","uncertain"].includes(job.status)){
          setBusy(full?t("全流程 · 第 5/6 步：渲染 "+(i+1)+"/"+videoTargets.length+" · "+(job.stage||job.status),"Full run · Step 5/6: rendering "+(i+1)+"/"+videoTargets.length+" · "+(job.stage||job.status),"全工程・ステップ5/6：レンダー "+(i+1)+"/"+videoTargets.length+"・"+(job.stage||job.status),"全流程 · 第 5/6 步：渲染 "+(i+1)+"/"+videoTargets.length+" · "+(job.stage||job.status)):t("一键生成 · 第 2/3 步：渲染 "+(i+1)+"/"+videoTargets.length+" · "+(job.stage||job.status),"One-click production · Step 2/3: rendering "+(i+1)+"/"+videoTargets.length+" · "+(job.stage||job.status),"一括制作・ステップ2/3：レンダー "+(i+1)+"/"+videoTargets.length+"・"+(job.stage||job.status),"一鍵生成 · 第 2/3 步：渲染 "+(i+1)+"/"+videoTargets.length+" · "+(job.stage||job.status)));
          await sleep(5000);
          job=await api("/video/runs/"+job.id) as ProductionVideoJob;
          overview=await api("/productions/"+current.id+"/outputs") as ProductionOutputs;setOutputs(overview);
        }
        if(job.status!=="succeeded")throw new Error(t("一键生成停在第 "+target.index+" 段：","One-click production stopped at clip "+target.index+": ","一括制作はクリップ "+target.index+" で停止しました：","一鍵生成停在第 "+target.index+" 段：")+(job.error||job.stage||job.status));
        overview=await api("/productions/"+current.id+"/outputs") as ProductionOutputs;setOutputs(overview);
      }

      overview=await api("/productions/"+current.id+"/outputs") as ProductionOutputs;setOutputs(overview);
      if(!overview.all_ready)throw new Error(t("仍有片段没有可采用的视频，一键生成已在合片前停止。","Some clips still have no adoptable video. One-click production stopped before assembly.","採用できる映像がないクリップが残っているため、結合前に一括制作を停止しました。","仍有片段沒有可採用的影片，一鍵生成已在合片前停止。"));
      setBusy(full?t("全流程 · 第 6/6 步：合并最终成片","Full run · Step 6/6: assembling final film","全工程・ステップ6/6：最終映像を結合","全流程 · 第 6/6 步：合併最終成片"):t("一键生成 · 第 3/3 步：合并最终成片","One-click production · Step 3/3: assembling final film","一括制作・ステップ3/3：最終映像を結合","一鍵生成 · 第 3/3 步：合併最終成片"));
      setMerging(true);
      overview=await api("/productions/"+current.id+"/film",{},undefined,"POST",{timeoutMs:1800000}) as ProductionOutputs;
      setOutputs(overview);await refreshList();
      setProductionPage("output");
      setNotice(full?t("全流程完成：文字卡、剧集规划、分镜、提示词、视频和最终成片均已处理。","Full run complete: text cards, episode plan, storyboard, prompts, videos and final assembly are ready.","全工程が完了しました。文章カード、エピソード計画、絵コンテ、プロンプト、映像、最終結合を処理しました。","全流程完成：文字卡、劇集規劃、分鏡、提示詞、影片和最終成片均已處理。"):t("整集已完成：提示词、视频和最终成片均已按分镜顺序生成。","The episode is complete: prompts, videos and the final film were produced in storyboard order.","全話が完成しました。プロンプト・映像・最終映像を絵コンテ順に生成しました。","整集已完成：提示詞、影片和最終成片均已按分鏡順序生成。"));
    }catch(e){
      const reason=(e as Error).message;
      setError(t(
        "一键流程已暂停，已经完成的提示词和视频均已保存。请确认 H3 Studio 与 ComfyUI 正在运行，再点“继续完成本集”即可从缺失处接着做。原因：",
        "One-click production paused. Completed prompts and videos are saved. Make sure H3 Studio and ComfyUI are running, then choose Resume episode to continue only the missing work. Reason: ",
        "一括制作を中断しました。完了済みのプロンプトと映像は保存されています。H3 Studio と ComfyUI を起動し、「この話を続行」で不足分から再開してください。理由：",
        "一鍵流程已暫停，已完成的提示詞和影片均已儲存。請確認 H3 Studio 與 ComfyUI 正在執行，再點「繼續完成本集」即可從缺失處接著做。原因："
      )+reason);
    }finally{setMerging(false);busyRef.current=false;setBulkAction("");setBusy("");await refreshOutputs(currentId,true);}
  };
  const stopVideo=async(job:ProductionVideoJob)=>{
    const action=async()=>{
    await api("/video/runs/"+job.id+"/cancel",{},undefined,"POST",{timeoutMs:30000});
    await refreshOutputs(production?.id,true);
    setNotice(t("当前视频任务已停止。ComfyUI 视频无法从中点恢复；可回到该片段重新生成。","The current video task was stopped. ComfyUI cannot resume mid-render; regenerate that clip when ready.","現在の映像タスクを停止しました。途中再開はできないため、必要ならクリップを再生成してください。","目前影片任務已停止。ComfyUI 無法從中點恢復；可回到該片段重新生成。"));
    };
    if(bulkAction==="videos"||bulkAction==="redo-videos"||bulkAction==="all"||bulkAction==="full"){
      try{await action();}catch(e){setError((e as Error).message);}
      return;
    }
    await run(t("停止当前视频任务","Stop current video task","現在の映像タスクを停止","停止目前影片任務"),action);
  };
  const uploadSegmentKeyframe=(segment:Segment,file:File)=>run(t("上传片段专属关键帧","Upload clip-only keyframe","クリップ専用キーフレームを追加","上傳片段專屬關鍵影格"),async()=>{
    const saved=await save();const form=new FormData();form.append("file",file);
    const result=await api("/productions/"+saved.id+"/segments/"+segment.id+"/assets",undefined,form,"POST",{timeoutMs:90000}) as any;
    setProduction(result.production);setDraft(result.production);await refreshList();
    setNotice(t("关键帧只绑定到第 "+segment.index+" 段，不会进入全项目卡库，也不会占用其它片段。","The keyframe belongs only to clip "+segment.index+"; it is not added to the shared card library or other clips.","キーフレームはクリップ "+segment.index+" 専用です。共有カード庫や他のクリップには入りません。","關鍵影格只綁定到第 "+segment.index+" 段，不會進入全專案卡庫，也不會佔用其他片段。"));
  });
  const removeSegmentKeyframe=(segment:Segment,assetId:string)=>run(t("移除片段关键帧","Remove clip keyframe","クリップのキーフレームを外す","移除片段關鍵影格"),async()=>{
    if(!production)return;
    const value=await apiPatch("/productions/"+production.id+"/segments/"+segment.id+"/assets/"+assetId,{attached:false}) as Production;
    setProduction(value);setDraft(value);await refreshList();
  });

  const generateImage=(segment:Segment)=>run(t("生成第 "+segment.index+" 段关键帧","Generate keyframe for clip "+segment.index,"クリップ "+segment.index+" のキーフレームを生成","生成第 "+segment.index+" 段關鍵影格"),async()=>{
    if(!models.length)throw new Error(t("没有检测到可用的本地生图工作流。请先启动 ComfyUI，并安装所需节点/模型。","No local image workflow was detected. Start ComfyUI and install the required nodes and models.","ローカル画像ワークフローが見つかりません。ComfyUIを起動し、必要なノードとモデルをインストールしてください。","沒有偵測到可用的本地生圖工作流。請先啟動 ComfyUI，並安裝所需節點/模型。"));
    const saved=await save();
    await api("/productions/"+saved.id+"/segments/"+segment.id+"/image",{
      request_id:crypto.randomUUID(),prompt:segment.image_prompt,model:imageModel,
      ...productionKeyframeSize(saved.video_aspect_ratio),seed:Math.floor(Math.random()*4294967296)});
    for(let i=0;i<600;i++){
      const state=await api("/productions/"+saved.id+"/segments/"+segment.id+"/image/sync",{});
      setProduction(state.production);
      if(state.run.status==="succeeded"){setNotice(t("第 "+segment.index+" 段关键帧已生成并绑定。","Keyframe for clip "+segment.index+" was generated and attached.","クリップ "+segment.index+" のキーフレームを生成して紐付けました。","第 "+segment.index+" 段關鍵影格已生成並綁定。"));return;}
      if(["failed","cancelled","paused"].includes(state.run.status))throw new Error(state.run.error||t("本地关键帧生成没有完成。","Local keyframe generation did not finish.","ローカルキーフレーム生成が完了しませんでした。","本地關鍵影格生成沒有完成。"));
      await sleep(1500);
    }
    throw new Error(t("关键帧仍在 ComfyUI 中运行，请稍后刷新制作项目。","The keyframe is still running in ComfyUI. Refresh the production later.","キーフレームはComfyUIで処理中です。後で制作プロジェクトを更新してください。","關鍵影格仍在 ComfyUI 中執行，請稍後重新整理製作專案。"));
  });
  const analyseKeyframes=()=>run(t("本地 AI 分析关键帧","Local AI keyframe review","ローカルAIでキーフレームを分析","本地 AI 分析關鍵影格"),async()=>{
    const current=await save();const result=await api("/productions/"+current.id+"/keyframe-suggestions/analyse",{limit:32},undefined,"POST",{timeoutMs:900000}) as any;
    setKeyframeSuggestions(result.suggestions||[]);
    setNotice(result.warning||t("分析完成。请先检查并修改建议，再批量生图；不会自动占用显存。","Analysis complete. Review and edit the suggestions before batch generation; no GPU image job was started.","分析完了。候補を確認・編集してから一括生成してください。まだ画像処理は開始していません。","分析完成。請先檢查並修改建議，再批量生圖；不會自動佔用顯示記憶體。"));
  });
  const generateSuggestedKeyframes=()=>run(t("批量生成分镜关键帧","Batch storyboard keyframes","絵コンテ画像を一括生成","批量生成分鏡關鍵影格"),async()=>{
    if(!models.includes(imageModel))throw new Error(t("请先在设置里选择可用的本地生图配方。","Select an available local image recipe in Settings first.","先に設定で利用可能な画像レシピを選択してください。","請先在設定選擇可用的本地生圖配方。"));
    let current=await save();let remaining=[...keyframeSuggestions];
    for(let index=0;index<keyframeSuggestions.length;index++){
      const suggestion=keyframeSuggestions[index];
      const segment=current.segments.find(item=>item.id===suggestion.segment_id);
      if(!segment||segment.keyframe_asset_ids?.length||!suggestion.prompt.trim()){
        remaining=remaining.filter(item=>item.segment_id!==suggestion.segment_id);setKeyframeSuggestions(remaining);continue;
      }
      setBusy(t("生成关键帧 "+(index+1)+"/"+keyframeSuggestions.length,"Generating keyframe "+(index+1)+"/"+keyframeSuggestions.length,"画像生成 "+(index+1)+"/"+keyframeSuggestions.length,"生成關鍵影格 "+(index+1)+"/"+keyframeSuggestions.length));
      await api("/productions/"+current.id+"/segments/"+segment.id+"/image",{request_id:crypto.randomUUID(),prompt:suggestion.prompt,
        model:imageModel,...productionKeyframeSize(current.video_aspect_ratio),seed:Math.floor(Math.random()*4294967296)},undefined,"POST",{timeoutMs:90000});
      let finished=false;
      for(let attempt=0;attempt<600;attempt++){
        const state=await api("/productions/"+current.id+"/segments/"+segment.id+"/image/sync",{}) as {run:{status:string;error?:string};production:Production};
        current=state.production;setProduction(current);setDraft(current);
        if(state.run.status==="succeeded"){finished=true;break;}
        if(["failed","cancelled","paused"].includes(state.run.status))throw new Error(state.run.error||"Local keyframe generation stopped.");
        await sleep(1500);
      }
      if(!finished)throw new Error(t("关键帧仍在 ComfyUI 运行，请稍后刷新。","Keyframe is still running in ComfyUI; refresh later.","画像はComfyUIで処理中です。後で更新してください。","關鍵影格仍在 ComfyUI 執行，請稍後重新整理。"));
      remaining=remaining.filter(item=>item.segment_id!==segment.id);setKeyframeSuggestions(remaining);
    }
    setNotice(t("建议中的关键帧已完成；每段仍可单独修改、移除或再次生成。","Suggested keyframes are complete. Each clip can still be edited, removed or regenerated.","候補画像を生成しました。各クリップで編集・削除・再生成できます。","建議中的關鍵影格已完成；每段仍可單獨修改、移除或再次生成。"));
  });

  return <main className="production-studio" lang={uiLanguage}>
    <header className="production-hero">
      <div><span className="eyebrow">H3 STORY PRODUCTION · LOCAL</span><h1>{production?.title||t("剧本制作中心","Story production","脚本制作センター","劇本製作中心")}</h1>
        <p>{production?t("剧本、资产卡、分镜和视频各自分区；统一设置不会占用创作页面。","Script, cards, storyboards and video each have their own workspace. Shared settings stay out of the creative pages.","脚本・素材カード・絵コンテ・映像を分け、共通設定は制作画面を圧迫しません。","劇本、資產卡、分鏡和影片各自分區；統一設定不會佔用創作頁面。"):t("先建立剧集 / 片段，再配置卡组、规划剧集和制作视频。每段按剧情动态控制在 5–15 秒。","Create an episode / clip part first, then configure cards, plan episodes and produce video. Each clip is timed dynamically from 5–15 seconds.","エピソード／クリップを作成し、カード、エピソード、映像の順に進めます。各クリップは5〜15秒で動的に設計されます。","先建立劇集／片段，再設定卡組、規劃劇集和製作影片。每段依劇情動態控制在 5–15 秒。")}</p></div>
      <div className="production-hero-actions"><button className={productionPage==="projects"?"active":""} onClick={()=>setProductionPage("projects")}><FolderOpen size={17}/>{t("剧集 / 片段","Episodes / Clips","エピソード／クリップ","劇集／片段")}</button><button className={productionPage==="series"?"active":""} onClick={()=>setProductionPage("series")}><Film size={17}/>{t("剧本管理","Script management","脚本管理","劇本管理")}</button><button className={productionPage==="videos"?"active":""} onClick={()=>setProductionPage("videos")}><Video size={17}/>{t("视频一览","Video overview","映像一覧","影片一覽")}</button><button className={productionPage==="cardsets"?"active":""} onClick={()=>setProductionPage("cardsets")}><BookOpen size={17}/>{t("角色资料库","Character library","キャラクター資料庫","角色資料庫")}</button><button className={productionPage==="settings"?"active":""} onClick={()=>setProductionPage("settings")}><Settings2 size={17}/>{t("设置","Settings","設定","設定")}</button></div>
    </header>
    {production&&<nav className="production-steps" aria-label={t("制作流程","Production workflow","制作フロー","製作流程")}>
      <button className={productionPage==="script"?"active":""} onClick={()=>setProductionPage("script")}><b>1</b><span>{t("片段与剧本","Clip & script","クリップ・脚本","片段與劇本")}</span></button>
      <button className={productionPage==="assets"?"active":""} onClick={()=>setProductionPage("assets")}><b>2</b><span>{t("卡组与角色","Cards & cast","カードと人物","卡組與角色")}</span></button>
      <button className={productionPage==="episodes"?"active":""} onClick={()=>setProductionPage("episodes")}><b>3</b><span>{t("剧集规划","Episode plan","エピソード計画","劇集規劃")}</span></button>
      <button className={productionPage==="storyboard"?"active":""} onClick={()=>setProductionPage("storyboard")}><b>4</b><span>{t("分镜制作","Storyboard clips","絵コンテ制作","分鏡製作")}</span></button>
      <button className={productionPage==="output"?"active":""} onClick={()=>setProductionPage("output")}><b>5</b><span>{t("视频与成片","Videos & film","映像と完成版","影片與成片")}</span></button>
    </nav>}
    {production&&!(["projects","series","videos","cardsets","settings"] as ProductionPage[]).includes(productionPage)&&<section className="production-full-runner card">
      <div className="production-full-runner-copy">
        <span className="eyebrow">FULL EPISODE PIPELINE</span>
        <strong>{t("一键跑完当前集全流程","Run the current episode end to end","現在話を全工程で一括制作","一鍵跑完目前集全流程")}</strong>
        <p>{t("按现有设置依次保存项目、补齐缺失文字卡、规划剧集与分镜、生成缺失提示词和视频，最后自动合片。已完成内容和手工修改不会被覆盖。","Using the current settings, save the project, fill missing text cards, plan any missing episode/storyboard work, generate missing prompts and videos, then assemble the final film. Completed and manually edited work is preserved.","現在の設定で保存、未作成の文章カード・話数計画・絵コンテ・プロンプト・映像を順に補完し、最後に結合します。完成済み内容や手動編集は上書きしません。","依目前設定依序儲存專案、補齊缺失文字卡、規劃劇集與分鏡、生成缺失提示詞和影片，最後自動合片。已完成內容和手動修改不會被覆蓋。")}</p>
      </div>
      <div className="production-full-runner-actions">
        <small>{t("仅处理当前集；自动环境关键帧遵循“设置”中的开关。","Current episode only; automatic environment keyframes follow the Settings toggle.","現在話のみ。背景キーフレームの自動生成は設定の切替に従います。","只處理目前集；自動環境關鍵影格依照「設定」中的開關。")}</small>
        <button className="primary" disabled={production.task_state==="paused"||!!busy||(outputs?.active_jobs||0)>0} onClick={()=>void generateEpisodeFilm({full:true})}>
          {bulkAction==="full"?<LoaderCircle className="spin" size={18}/>:<Sparkles size={18}/>}<span>{bulkAction==="full"?t("全流程运行中…","Full run in progress…","全工程を実行中…","全流程執行中…"):t("一键跑完全流程","Run full pipeline","全工程を一括実行","一鍵跑完全流程")}</span>
        </button>
      </div>
    </section>}
    {(error||notice)&&<div className={error?"production-alert error":"production-alert"}>{error||notice}</div>}
    {busy&&<div className="production-progress"><LoaderCircle className="spin" size={18}/><span>{busy}</span></div>}

    {productionPage==="projects"&&<section className="production-projects card">
      <div className="section-title production-projects-title"><div><span className="eyebrow">EPISODES · CLIPS</span><h2><FolderOpen size={21}/>{t("剧集 / 片段管理","Episodes & clips","エピソード／クリップ管理","劇集／片段管理")}</h2><p>{t("这里管理每集拆分出的制作片段；整部剧的分集编排与最终合片请到“剧本管理”。普通单镜头工程仍在“镜头工作室 → 已保存项目”。","Manage production parts split from episodes here. Use Script management for the whole story and final assembly. Single-scene projects remain in Scene Studio → Saved projects.","各話から分けた制作パートを管理します。全編の話数構成と結合は「脚本管理」で行います。単一シーンはシーンスタジオの保存済みプロジェクトにあります。","這裡管理每集拆分出的製作片段；整部劇的分集編排與最終合片請到「劇本管理」。普通單鏡頭工程仍在「鏡頭工作室 → 已儲存專案」。")}</p></div><button className="primary" onClick={startNewProduction}><Plus size={18}/>{t("新建剧集 / 片段","New episode / clip part","エピソード／クリップを作成","新建劇集／片段")}</button></div>
      <label className="production-project-search">{t("搜索剧集 / 片段","Search episodes / clips","エピソード／クリップを検索","搜尋劇集／片段")}<span><Search size={16}/><input value={projectQuery} onChange={e=>setProjectQuery(e.target.value)} placeholder={t("输入片名、剧名…","Title or series name…","作品名・シリーズ名…","輸入片名、劇名…")}/></span></label>
      <div className="production-project-grid">{shownItems.map(item=><button className={production?.id===item.id?"production-project-card current":"production-project-card"} key={item.id} onClick={()=>void load(item.id)}><span className="production-project-card-icon"><BookOpen size={20}/></span><span className="production-project-card-copy"><strong>{item.title}</strong><small>{t("第 "+(item.current_episode||1)+" 集","Episode "+(item.current_episode||1),"第"+(item.current_episode||1)+"話","第 "+(item.current_episode||1)+" 集")} · {item.ready_count}/{item.segment_count} {t("段已准备","clips ready","クリップ準備済み","段已準備")}</small><em>{formatProjectDate(item.updated_at,uiLanguage)}</em></span><ChevronRight size={18}/></button>)}</div>
      {!shownItems.length&&<div className="production-project-empty"><Film size={28}/><strong>{t("还没有剧集 / 片段","No episodes or clips yet","エピソード／クリップはまだありません","還沒有劇集／片段")}</strong><span>{t("从一个故事梗概、完整剧本或当前 Studio 项目开始。","Start from a story idea, a full screenplay or the current Studio project.","物語案、完成脚本、または現在のStudioプロジェクトから始められます。","從故事梗概、完整劇本或目前 Studio 專案開始。")}</span></div>}
      <div className="production-project-paging"><span>{filteredItems.length} {t("个项目","projects","件","個專案")}</span><button disabled={projectPage<=0} onClick={()=>setProjectPage(page=>Math.max(0,page-1))}><ChevronLeft size={17}/>{t("上一页","Previous","前へ","上一頁")}</button><b>{projectPage+1}/{projectPages}</b><button disabled={projectPage>=projectPages-1} onClick={()=>setProjectPage(page=>Math.min(projectPages-1,page+1))}>{t("下一页","Next","次へ","下一頁")}<ChevronRight size={17}/></button><button onClick={()=>void refreshList()}><RefreshCw size={17}/>{t("刷新","Refresh","更新","重新整理")}</button></div>
    </section>}

    {productionPage==="series"&&<SeriesWorkspace projects={items} collections={collections} currentProductionId={production?.id} onOpenProduction={load}/>}

    {productionPage==="videos"&&<VideoLibrary onOpenProduction={id=>load(id,"output")}/>}

    {productionPage==="cardsets"&&<section className="production-cardset-manager card">
      <div className="section-title production-projects-title"><div><span className="eyebrow">CHARACTER & PRODUCTION LIBRARY</span><h2><BookOpen size={21}/>{t("角色资料库","Character library","キャラクター資料庫","角色資料庫")}</h2><p>{t("角色资料库与剧集片段分开保存，可共用角色、服装、道具、环境、声线和风格；同名片段卡优先，声线目标语言跟随当前片段。","This library is separate from episode parts and shares characters, wardrobe, props, environments, voices and styles. Same-name local cards take priority; voice language follows the current part.","資料庫は各話のパートとは別に保存され、人物・衣装・小道具・環境・音声・スタイルを共有できます。同名のローカルカードを優先し、音声言語は現在のパートに合わせます。","角色資料庫與劇集片段分開儲存，可共用角色、服裝、道具、環境、聲線和風格；同名片段卡優先，聲線目標語言跟隨目前片段。")}</p></div><button onClick={()=>void refreshCollections()}><RefreshCw size={17}/>{t("刷新资料库","Refresh library","資料庫を更新","重新整理資料庫")}</button></div>
      <div className="production-cardset-toolbar"><label className="production-project-search">{t("搜索卡组","Search card sets","カードセットを検索","搜尋卡組")}<span><Search size={16}/><input value={cardSetQuery} onChange={e=>setCardSetQuery(e.target.value)} placeholder={t("输入卡组名称…","Card-set name…","カードセット名…","輸入卡組名稱…")}/></span></label><div><strong>{filteredCollections.length}</strong><span>{t("个可复用卡组"," reusable sets"," 件の再利用セット"," 個可重用卡組")}</span></div></div>
      {!production&&<div className="production-cardset-context"><BookOpen size={18}/><span>{t("可以先整理、复制或导出角色资料；要套用到片段，请先从“剧集 / 片段”打开一个片段。","You can organise, duplicate or export character records now. Open a part from Episodes / Clips before applying them.","人物資料は整理・複製・書き出しできます。適用するには「エピソード／クリップ」でパートを開いてください。","可以先整理、複製或匯出角色資料；要套用到片段，請先從「劇集／片段」開啟一個片段。")}</span></div>}
      <div className="production-cardset-grid">{filteredCollections.map(item=>{const detail=cardSetDetails[item.id];const expanded=openCardSetId===item.id;return <article className={expanded?"production-cardset-item expanded":"production-cardset-item"} key={item.id}>
        <header><span className="production-cardset-icon"><BookOpen size={21}/></span><div><strong>{item.name}</strong><small>{item.card_count} {t("张卡","cards","枚のカード","張卡")} · {item.overview_count||0} {t("张总图","overviews","総覧画像","張總圖")} · {formatProjectDate(item.updated_at,uiLanguage)}</small></div><button className="icon" onClick={()=>toggleCardSetDetails(item)} title={t("查看内容","View contents","内容を見る","查看內容")}>{expanded?<ChevronUp size={18}/>:<ChevronDown size={18}/>}</button></header>
        <div className="production-cardset-counts">{CARD_KINDS.map(kind=><span key={kind}><b>{item.counts?.[kind]||0}</b>{cardMeta[kind].title}</span>)}{item.has_series_voice_style&&<span className="voice-style-ready"><Check size={13}/>{t("全剧声线","Series voice style","全編音声スタイル","全劇聲線")}</span>}</div>
        {expanded&&<div className="production-cardset-detail">{detail?CARD_KINDS.map(kind=><div key={kind}><strong>{cardMeta[kind].title}</strong><p>{detail.cards[kind].length?detail.cards[kind].map(card=>card.name).join(" · "):t("无","None","なし","無")}</p></div>):<span>{t("正在读取…","Loading…","読み込み中…","正在讀取…")}</span>}{detail?.series_voice_style&&<details><summary>{t("查看全剧声线设定","View series voice style","全編音声スタイルを見る","查看全劇聲線設定")}</summary><p>{detail.series_voice_style}</p></details>}</div>}
        <footer><button className="primary" onClick={()=>applyManagedCardSet(item)}><Plus size={16}/>{production?t("套用到当前片段","Apply to current part","現在のパートに適用","套用到目前片段"):t("打开片段后套用","Open a part to apply","パートを開いて適用","開啟片段後套用")}</button><button onClick={()=>renameManagedCardSet(item)}>{t("重命名","Rename","名前変更","重新命名")}</button><button onClick={()=>duplicateManagedCardSet(item)}>{t("复制","Duplicate","複製","複製")}</button><button onClick={()=>exportManagedCardSet(item)}><Download size={16}/>{t("导出 JSON","Export JSON","JSON書き出し","匯出 JSON")}</button><button className="danger" onClick={()=>deleteManagedCardSet(item)}><Trash2 size={16}/>{t("归档","Archive","アーカイブ","封存")}</button></footer>
      </article>})}</div>
      {!filteredCollections.length&&<div className="production-project-empty"><BookOpen size={28}/><strong>{t("角色资料库还是空的","Character library is empty","キャラクター資料庫は空です","角色資料庫還是空的")}</strong><span>{t("在片段的“卡组与角色”页点击“保存为可复用卡组”，角色资料就会出现在这里。","In a part's Cards & cast page, choose Save reusable set to add records here.","パートの「カードと人物」で「再利用セットとして保存」を押すとここに表示されます。","在片段的「卡組與角色」頁點擊「儲存為可重用卡組」，角色資料就會出現在這裡。")}</span></div>}
    </section>}

    {productionPage==="settings"&&!production&&<section className="production-settings card">
      <div className="section-title"><div><span className="eyebrow">SETTINGS</span><h2><Settings2 size={21}/>{t("设置","Settings","設定","設定")}</h2><p>{t("无需先创建项目，即可检查本地生图流程。视频尺寸等项目专属设置在打开项目后显示。","Check local image workflows before creating a project. Project-specific video settings appear after opening a project.","作品を作る前にローカル画像ワークフローを確認できます。映像の設定は作品を開いた後に表示します。","無需先建立專案，即可檢查本地生圖流程。影片尺寸等專案專屬設定在開啟專案後顯示。")}</p></div></div>
      <div className="production-auto-keyframes">
        <div className="production-auto-keyframes-head"><div><strong>{t("打开项目专属设置","Open project settings","作品別設定を開く","開啟專案專屬設定")}</strong><p>{t("画幅、分辨率、质量配方、提示词规则、自动续接、自动关键帧和合片选项属于具体剧集 / 片段。请在这里直接选择一个已有项目。","Aspect ratio, resolution, quality, prompt rules, continuation, keyframes and assembly belong to a specific episode or clip. Select an existing project here.","画面比・解像度・品質・プロンプト・継続・キーフレーム・結合は作品別です。既存作品をここで選択してください。","畫幅、解析度、品質、提示詞、續接、關鍵影格和合片屬於具體劇集／片段。請在這裡選擇現有專案。")}</p></div></div>
        {items.length?<label>{t("选择剧集 / 片段","Choose episode / clip","エピソード／クリップを選択","選擇劇集／片段")}<select value="" onChange={e=>{const id=e.target.value;if(id)void load(id,"settings")}}><option value="">{t("请选择…","Select…","選択…","請選擇…")}</option>{items.map(item=><option value={item.id} key={item.id}>{item.title} · {t("第 "+(item.current_episode||1)+" 集","Episode "+(item.current_episode||1),"第"+(item.current_episode||1)+"話","第 "+(item.current_episode||1)+" 集")}</option>)}</select></label>:<button className="primary" onClick={startNewProduction}><Plus size={17}/>{t("先创建剧集 / 片段","Create an episode / clip first","先にエピソード／クリップを作成","先建立劇集／片段")}</button>}
      </div>
      <label>{t("卡图与手动关键帧生图配方","Card and manual keyframe image recipe","カード・手動キーフレーム画像レシピ","卡圖與手動關鍵影格生圖配方")}<select value={imageModel} onChange={e=>chooseImageModel(e.target.value)}>{!models.includes(imageModel)&&<option value={imageModel}>{imageModel} · {t("当前不可用","unavailable","利用不可","目前不可用")}</option>}{models.map(model=><option key={model} value={model}>{model}</option>)}</select></label>
      <div className="production-actions"><button disabled={!!busy} onClick={()=>void run(t("刷新 ComfyUI 模型","Refresh ComfyUI models","ComfyUIモデルを更新","重新整理 ComfyUI 模型"),async()=>{await refreshImageModels()})}><RefreshCw size={16}/>{t("刷新模型检测","Refresh model check","モデルを再確認","重新整理模型檢查")}</button><button onClick={()=>onStudio("connections")}>{t("打开连接与显存设置","Open connections & GPU","接続・GPU設定を開く","開啟連線與顯示記憶體設定")}</button></div>
      <p className={imageInventory?.inventory_available?"production-alert":"planner-warning"}>{imageInventory?.inventory_available?t("ComfyUI 已连接，模型清单读取成功。","ComfyUI is connected and its model inventory is available.","ComfyUIに接続し、モデル一覧を取得しました。","ComfyUI 已連線，模型清單讀取成功。"):t("ComfyUI 未连接，或模型清单仍在读取。它只影响本地生图，不会锁住剧本、卡库和其他设置。","ComfyUI is disconnected or its inventory is still loading. This only affects local image generation; script, library and other settings remain available.","ComfyUI未接続、またはモデル一覧を読込中です。影響はローカル画像生成だけで、脚本や資料庫などは利用できます。","ComfyUI 未連線，或模型清單仍在讀取。只影響本地生圖，不會鎖住劇本、資料庫和其他設定。")}</p>
      <p>{t("附件 Krea 2 作为可选 8 步无 LoRA 配方；原文件虽写“无 LoRA”，内部实际有三个风格 LoRA。这里保留基础模型、采样器和尺寸输入，去掉风格 LoRA，并补上 SaveImage。不会替换原生图或视频流程。","The supplied Krea 2 canvas is adapted as an optional 8-step no-LoRA recipe. Despite its filename, the file contains three style LoRAs; this recipe omits them and adds SaveImage. Existing image and video recipes remain unchanged.","添付のKrea 2を任意の8ステップ・LoRAなしレシピにしました。元ファイルには実際には3つのLoRAがあるため省き、SaveImageを追加しました。既存のレシピは変更しません。","附件 Krea 2 作為可選 8 步無 LoRA 配方；原檔內其實有三個風格 LoRA。這裡保留基礎模型、取樣器和尺寸輸入，去掉 LoRA 並補上 SaveImage。原流程不變。")}</p>
      {imageInventory?.errors?.map((message:string,index:number)=><p className="planner-warning" key={index}>{message}</p>)}
      {imageInventory?.servers?.map((server:any)=><p key={server.comfy_url}>{server.comfy_url} · Krea 2: {server.models?.includes("krea2_turbo_bf16.safetensors")?t("可用","ready","使用可能","可用"):t("缺少","missing","不足","缺少")}{!server.models?.includes("krea2_turbo_bf16.safetensors")&&" · "+(server.krea_missing||[]).join(", ")}</p>)}
    </section>}
    {productionPage==="script"&&!production&&<section className="production-setup card" id="production-bible">
      <div className="production-page-heading"><span className="eyebrow">NEW EPISODE · CLIP PART</span><h2><BookOpen size={20}/> {t("创建剧集 / 片段","Create episode / clip part","エピソード／クリップを作成","建立劇集／片段")}</h2><p>{t("先建立一个制作片段，再进入资产卡库。完整剧本的多集、多片段顺序到“剧本管理”编排；跨片段共用资料请保存到“角色资料库”。","Create one production part, then add its assets. Arrange a full script's episodes and parts in Script management; save reusable records in Character library.","1つの制作パートを作成して素材を追加します。全編の話数・順序は「脚本管理」で編成し、共有資料は「キャラクター資料庫」に保存します。","先建立一個製作片段，再進入資產卡庫。完整劇本的多集、多片段順序到「劇本管理」編排；跨片段共用資料請存到「角色資料庫」。")}</p></div>
      <div className="production-grid"><label>{t("剧集 / 片段标题","Episode / part title","エピソード／パート名","劇集／片段標題")}<input autoFocus value={draft.title} onChange={e=>setDraft({...draft,title:e.target.value})} placeholder={t("例如：星图寻宝队 · 第 1 集（上）","Example: Star Map Seekers · Episode 1A","例：星図探検隊・第1話（前編）","例如：星圖尋寶隊 · 第 1 集（上）")}/><small>{t("灰色文字只是案例，不会作为标题保存。请输入便于在剧本管理中查找的名称。","Grey text is only an example and is never saved. Enter a name that will be easy to find in Script management.","灰色の文字は例であり、タイトルとして保存されません。脚本管理で見つけやすい名前を入力してください。","灰色文字只是案例，不會作為標題儲存。請輸入方便在劇本管理中查找的名稱。")}</small></label>
        <label>{t("项目输出语言","Project output language","プロジェクト出力言語","專案輸出語言")}<select value={draft.language} onChange={e=>setDraft({...draft,language:e.target.value as Production["language"]})}>{LANGUAGE_OPTIONS.map(([code,label])=><option key={code} value={code}>{label}</option>)}</select></label></div>
      <div className="production-grid production-series-fields"><label>{t("剧集数","Episodes","話数","劇集數")}<input type="number" min={1} max={100} value={draft.episode_count} onChange={e=>setDraft({...draft,episode_count:Math.max(1,Math.min(100,Number(e.target.value)||1))})}/></label>
        <label>{t("单集目标时长（分钟）","Target minutes per episode","1話の目標時間（分）","單集目標時長（分鐘）")}<input type="number" min={0.5} max={180} step={0.5} value={draft.episode_minutes} onChange={e=>{const minutes=e.currentTarget.valueAsNumber;if(Number.isFinite(minutes))setDraft({...draft,episode_minutes:Math.max(0.5,Math.min(180,minutes))})}}/></label></div>
      <label>{t("本剧集 / 片段内容","Episode / part content","エピソード／パート内容","本劇集／片段內容")}<textarea rows={10} value={draft.brief} onChange={e=>setDraft({...draft,brief:e.target.value})} placeholder={t("示例（仅作输入提示）：\n场景：雨夜的旧车站\n出场：林夏、周宁\n剧情：林夏发现储物柜里的旧录音机……\n对白：林夏：“这不是我父亲的声音。”\n\n也可以直接粘贴小说原文、剧本或分镜稿；输入语言不限。","Example (input guide only):\nSetting: an old station on a rainy night\nCast: Lina, Noah\nStory: Lina finds an old recorder inside a locker…\nDialogue — Lina: “That is not my father's voice.”\n\nYou may also paste prose, a screenplay or a storyboard in any language.","例（入力ガイドのみ）：\n場所：雨の夜の古い駅\n登場人物：リナ、ノア\n物語：リナはロッカーの中で古い録音機を見つける……\n台詞・リナ：「これは父の声じゃない。」\n\n小説本文、脚本、絵コンテをそのまま貼り付けても構いません。入力言語は自由です。","範例（僅作輸入提示）：\n場景：雨夜的舊車站\n出場：林夏、周寧\n劇情：林夏發現置物櫃裡的舊錄音機……\n對白：林夏：「這不是我父親的聲音。」\n\n也可以直接貼上小說原文、劇本或分鏡稿；輸入語言不限。")}/><small>{draft.brief.length.toLocaleString()} / 30,000 {t("字；输入框内的灰色案例不会提交。长剧请按集/段拆开，避免一次规划截断。","characters. Grey example text is never submitted. Split long stories by episode or part to avoid truncated plans.","文字。灰色の例文は送信されません。長編は話・パートごとに分けて出力切れを防いでください。","字；輸入框內的灰色案例不會提交。長劇請依集／段拆開，避免一次規劃截斷。")}</small></label>
      <label>{t("视频提示词规则","Video prompt rules","映像プロンプト規則","影片提示詞規則")}<select value={draft.prompt_version==="storyboard_narrative"?"continuity_director":draft.prompt_version} onChange={e=>setDraft({...draft,prompt_version:e.target.value as Production["prompt_version"]})}><option value="classic">{t("原版 H3 · 已加强防重复与风格锁定","Classic H3 · improved cast and style locks","従来版H3・重複と画風を改善","原版 H3 · 已加強防重複與風格鎖定")}</option><option value="continuity_director">{t("导演连续性 · 叙事导演稿","Director continuity · narrative storyboard","監督連続性・絵コンテ叙述","導演連續性 · 敘事導演稿")}</option></select><small>{t("导演版按参考图职责、镜头、对白和稳定性约束组织；原版也已加强角色名单和防重复规则。","Director mode groups picture roles, scene, dialogue and stability rules. Classic mode also has stronger cast and duplicate safeguards.","監督版は参照画像・場面・台詞・安定性を整理。従来版も出演者一覧と重複防止を強化しました。","導演版按參考圖職責、鏡頭、對白和穩定性約束組織；原版也已加強角色名單和防重複規則。")}</small></label>
      <div className="production-grid production-style-grid"><label>{t("视觉风格预设","Visual style preset","ビジュアルスタイル","視覺風格預設")}<select value={draft.visual_style_preset} onChange={e=>setDraft({...draft,visual_style_preset:e.target.value})}>{visualOptions()}</select><small>{visualDescription(draft.visual_style_preset)}</small>{draft.visual_style_preset==="custom"&&<textarea className="production-custom-style" rows={3} value={draft.visual_style_custom} onChange={e=>setDraft({...draft,visual_style_custom:e.target.value})} placeholder={t("输入画风、媒介、色彩、光线、镜头质感和明确禁忌。","Describe medium, palette, lighting, lens texture and explicit exclusions.","画材・色・光・レンズ質感・禁止事項を入力。","輸入畫風、媒介、色彩、光線、鏡頭質感和明確禁忌。")}/>}</label>
        <label>{t("长片叙事风格","Long-form narrative style","長編の語り口","長片敘事風格")}<select value={draft.narrative_style} onChange={e=>setDraft({...draft,narrative_style:e.target.value})}>{narrativeOptions()}</select><small>{narrativeDescription(draft.narrative_style)}</small>{draft.narrative_style==="custom"&&<textarea className="production-custom-style" rows={3} value={draft.narrative_style_custom} onChange={e=>setDraft({...draft,narrative_style_custom:e.target.value})} placeholder={t("输入结构、节奏、叙述视角、时间方式与禁忌。","Describe structure, pacing, viewpoint, time design and exclusions.","構成・テンポ・視点・時間設計・禁止事項を入力。","輸入結構、節奏、敘述視角、時間方式與禁忌。")}/>}</label></div>
      <div className="production-grid"><label>{t("视觉风格圣经","Visual style bible","ビジュアルスタイル設定","視覺風格聖經")}<textarea rows={5} value={draft.style_bible} onChange={e=>setDraft({...draft,style_bible:e.target.value})}/></label>
        <label>{t("角色圣经","Character bible","キャラクター設定","角色聖經")}<textarea rows={5} value={draft.character_bible} onChange={e=>setDraft({...draft,character_bible:e.target.value})} placeholder={t("每个角色的身份、脸、服装、不可改变项","Identity, face, clothing and invariants for each character","各人物の身元、顔、衣装、変更禁止事項","每個角色的身分、臉、服裝、不可改變項")}/></label></div>
      <details className="production-optional"><summary>{t("连续性规则（可选高级项）","Continuity rules · optional","連続性ルール・任意","連續性規則（可選進階項）")}</summary><p>{t("不填写也可以。仅当你有必须跨镜头保持的特殊规则时填写，例如某把钥匙始终由谁持有、人物不能越过哪条轴线、某段必须保持夜景。角色外貌和服装请优先写在对应资产卡。","You may leave this blank. Use it only for special facts that must persist between shots, such as who holds a key, screen-direction limits or a sequence that must remain at night. Put appearance and wardrobe facts in their cards instead.","空欄でも問題ありません。鍵の所持者、画面方向、夜景維持など、ショット間で必ず守る特別な規則だけを書きます。外見や衣装は各カードを優先してください。","可以不填。只在有必須跨鏡頭保持的特殊規則時填寫，例如鑰匙由誰持有、人物不可越過哪條軸線、某段必須維持夜景。角色外貌和服裝請優先寫在對應資產卡。")}</p><textarea rows={4} value={draft.continuity_notes} onChange={e=>setDraft({...draft,continuity_notes:e.target.value})} placeholder={t("可留空：只写跨镜头必须保持的特殊规则","Optional: only special rules that must persist between shots","任意：ショット間で維持する特別ルールのみ","可留空：只寫跨鏡頭必須保持的特殊規則")}/></details>
      <p className="setup-flow"><strong>{t("推荐顺序：","Recommended order:","推奨手順：","建議順序：")}</strong>{t("先创建项目并上传资产卡，让 AI 拆分时就能知道固定角色、服装、道具、环境和声线。","Create the production and add asset cards first, so AI knows the fixed characters, wardrobe, props, environments and voices when planning.","先に制作プロジェクトと素材カードを作成すると、AIは人物・衣装・小道具・環境・声を理解して分割できます。","先建立專案並上傳資產卡，讓 AI 拆分時就能知道固定角色、服裝、道具、環境和聲線。")}</p>
      <div className="production-actions"><button className="primary" disabled={!!busy||!draft.title.trim()||!draft.brief.trim()} onClick={()=>void run(t("创建剧集 / 片段","Create episode / clip part","エピソード／クリップを作成","建立劇集／片段"),async()=>{await create(true)})}><Plus size={18}/> {t("创建片段并配置角色资料","Create part and set up character records","パートを作成して人物資料を設定","建立片段並設定角色資料")}</button>
        <button disabled={!!busy||(!project.story.text.trim()&&project.title==="Untitled film"&&!project.subjects.length)} onClick={importCurrentStudioContent}>{t("从当前镜头带入内容","Import current scene content","現在のシーン内容を取り込む","從目前鏡頭帶入內容")}</button>
        <span className="setup-action-note">{t("标题和内容为必填；其余资料可以创建后再完善。","Title and content are required; everything else can be completed after creation.","タイトルと内容は必須です。その他の資料は作成後に追加できます。","標題和內容為必填；其餘資料可在建立後再完善。")}</span>
        </div>
    </section>}{production&&<>
      <section className="production-bible card" id="production-bible" hidden={productionPage!=="script"}>
        <div className="section-title"><div><span className="eyebrow">PRODUCTION BIBLE</span><h2>{t("全片约束","Film-wide constraints","作品全体の制約","全片約束")}</h2></div>
          <div className="production-title-actions"><button className={production.task_state==="paused"?"primary":""} onClick={()=>void setTaskState(production.task_state==="paused"?"active":"paused")}>{production.task_state==="paused"?<><Play size={17}/>{t("继续任务","Resume","再開","繼續任務")}</>:<><Pause size={17}/>{t("暂停任务","Pause","一時停止","暫停任務")}</>}</button><button onClick={exportStory}><Download size={17}/>{t("导出整体剧情","Export story","物語を書き出す","匯出整體劇情")}</button><button onClick={exportDialogue}><Download size={17}/>{t("导出对白","Export dialogue","台詞を書き出す","匯出對白")}</button><button onClick={()=>void run(t("保存片段","Save part","パートを保存","儲存片段"),async()=>{await save();setNotice(t("片段已保存。","Part saved.","パートを保存しました。","片段已儲存。"))})}><Save size={17}/> {t("保存片段","Save part","パートを保存","儲存片段")}</button><button className="danger" onClick={()=>void deleteProduction()}><Trash2 size={17}/>{t("删除片段","Delete part","パートを削除","刪除片段")}</button></div></div>
        {production.task_state==="paused"&&<div className="production-paused"><Pause size={18}/><div><strong>{t("项目已暂停后续调度","Production scheduling is paused","制作スケジュールは一時停止中","專案已暫停後續調度")}</strong><span>{t("仍可编辑和导出。新的规划、提示词、关键帧、视频和合片任务暂不提交；已经进入 ComfyUI 的渲染继续运行。","Editing and export remain available. New planning, prompt, keyframe, video and assembly jobs are held; an existing ComfyUI render keeps running.","編集と書き出しは可能です。新しい計画・プロンプト・キーフレーム・映像・結合は保留され、ComfyUIで実行中のレンダーは継続します。","仍可編輯和匯出。新的規劃、提示詞、關鍵影格、影片和合片任務暫不提交；已進入 ComfyUI 的渲染繼續執行。")}</span></div></div>}
        <div className="production-grid"><label>{t("片名","Title","作品名","片名")}<input value={production.title} onChange={e=>updateField("title",e.target.value)}/></label>
          <label>{t("项目输出语言","Project output language","プロジェクト出力言語","專案輸出語言")}<select value={production.language} onChange={e=>updateField("language",e.target.value)}>{LANGUAGE_OPTIONS.map(([code,label])=><option key={code} value={code}>{label}</option>)}</select><small>{t("原剧本可用任意语言；本地 LLM 会把分镜、对白和提示词统一输出为这里选择的语言。","The source may use any language. The local LLM writes storyboards, dialogue and prompts in the selected language.","入力言語は自由です。ローカルLLMは絵コンテ・台詞・プロンプトを選択した言語で出力します。","原劇本可用任意語言；本地 LLM 會把分鏡、對白和提示詞統一輸出為這裡選擇的語言。")}</small></label></div>
        <div className="production-grid production-series-fields"><label>{t("剧集数","Episodes","話数","劇集數")}<input type="number" min={1} max={100} value={production.episode_count} onChange={e=>updateField("episode_count",Math.max(1,Math.min(100,Number(e.target.value)||1)))}/></label>
          <label>{t("单集目标时长（分钟）","Target minutes per episode","1話の目標時間（分）","單集目標時長（分鐘）")}<input type="number" min={0.5} max={180} step={0.5} value={production.episode_minutes} onChange={e=>{const minutes=e.currentTarget.valueAsNumber;if(Number.isFinite(minutes))updateField("episode_minutes",Math.max(0.5,Math.min(180,minutes)))}}/></label>
          <label>{t("当前制作集","Current episode","現在の話","目前製作集")}<select value={production.current_episode} onChange={e=>updateField("current_episode",Number(e.target.value))}>{Array.from({length:production.episode_count},(_,i)=><option key={i+1} value={i+1}>{t("第 "+(i+1)+" 集","Episode "+(i+1),"第"+(i+1)+"話","第 "+(i+1)+" 集")}</option>)}</select></label></div>
        <label>{t("故事原文 / 剧本","Source story / screenplay","原作ストーリー / 脚本","故事原文 / 劇本")}<textarea rows={7} value={production.brief} onChange={e=>updateField("brief",e.target.value)}/><small>{production.brief.length.toLocaleString()} / 30,000 {t("字；同一集的后续内容可建新项目，并在“剧本管理”接续。","characters. Put later parts of the same episode into new projects and order them in Script management.","文字。同じ話の続きは新しい作品に分け、脚本管理で順に並べられます。","字；同一集的後續內容可建新專案，並在「劇本管理」接續。")}</small></label>
        <div className="production-grid production-style-grid"><label>{t("视觉风格预设","Visual style preset","ビジュアルスタイル","視覺風格預設")}<select value={production.visual_style_preset} onChange={e=>updateField("visual_style_preset",e.target.value)}>{visualOptions()}</select><small>{visualDescription(production.visual_style_preset)}</small>{production.visual_style_preset==="custom"&&<textarea className="production-custom-style" rows={3} value={production.visual_style_custom} onChange={e=>updateField("visual_style_custom",e.target.value)} placeholder={t("输入画风、媒介、色彩、光线、镜头质感和明确禁忌。","Describe medium, palette, lighting, lens texture and explicit exclusions.","画材・色・光・レンズ質感・禁止事項を入力。","輸入畫風、媒介、色彩、光線、鏡頭質感和明確禁忌。")}/>}</label>
          <label>{t("长片叙事风格","Long-form narrative style","長編の語り口","長片敘事風格")}<select value={production.narrative_style} onChange={e=>updateField("narrative_style",e.target.value)}>{narrativeOptions()}</select><small>{narrativeDescription(production.narrative_style)}</small>{production.narrative_style==="custom"&&<textarea className="production-custom-style" rows={3} value={production.narrative_style_custom} onChange={e=>updateField("narrative_style_custom",e.target.value)} placeholder={t("输入结构、节奏、叙述视角、时间方式与禁忌。","Describe structure, pacing, viewpoint, time design and exclusions.","構成・テンポ・視点・時間設計・禁止事項を入力。","輸入結構、節奏、敘述視角、時間方式與禁忌。")}/>}</label></div>
        <label>{t("叙事节奏补充","Narrative direction notes","語り口の補足","敘事節奏補充")}<textarea rows={3} value={production.narrative_notes} onChange={e=>updateField("narrative_notes",e.target.value)} placeholder={t("例如：前两集慢热建立人物，第 3 集开始加快冲突；每集结尾保留悬念。","Example: establish characters slowly for two episodes, accelerate conflict in episode 3, and end each episode on a hook.","例：最初の2話は人物を丁寧に描き、第3話から対立を加速。各話末にフックを残す。","例如：前兩集慢熱建立人物，第 3 集開始加快衝突；每集結尾保留懸念。")}/></label>
        <div className="production-grid"><label>{t("视觉风格详细设定","Detailed visual style notes","ビジュアル詳細設定","視覺風格詳細設定")}<textarea rows={4} value={production.style_bible} onChange={e=>updateField("style_bible",e.target.value)}/></label>
          <label>{t("角色圣经","Character bible","キャラクター設定","角色聖經")}<textarea rows={4} value={production.character_bible} onChange={e=>updateField("character_bible",e.target.value)}/></label></div>
        <details className="production-optional"><summary>{t("连续性规则（可选高级项）","Continuity rules · optional","連続性ルール・任意","連續性規則（可選進階項）")}</summary><p>{t("可以不填。这里只放跨镜头必须保持、但无法归入角色卡、服装卡、道具卡或环境卡的特殊约束。","This may be left blank. Use it only for cross-shot constraints that do not belong in a character, wardrobe, prop or environment card.","空欄でも問題ありません。人物・衣装・小道具・環境カードに入らない、ショット間の特別な制約だけを書きます。","可以不填。這裡只放跨鏡頭必須保持、但無法歸入角色卡、服裝卡、道具卡或環境卡的特殊約束。")}</p><textarea rows={3} value={production.continuity_notes} onChange={e=>updateField("continuity_notes",e.target.value)} placeholder={t("可留空","Optional","任意","可留空")}/></details>
        <div className="production-actions production-section-next"><button onClick={()=>void run(t("保存剧本设定","Save script settings","脚本設定を保存","儲存劇本設定"),async()=>{await save();setNotice(t("剧本设定已保存。","Script settings saved.","脚本設定を保存しました。","劇本設定已儲存。"))})}><Save size={17}/>{t("保存剧本","Save script","脚本を保存","儲存劇本")}</button><button className="primary" onClick={()=>setProductionPage("assets")}><span>{t("下一步：配置资产卡","Next: configure cards","次へ：素材カード","下一步：設定資產卡")}</span><ChevronRight size={17}/></button></div>
        {production.planner_warning&&<p className="planner-warning">{production.planner_warning}</p>}
      </section>
      <section className="production-cards card" id="production-card-library" hidden={productionPage!=="assets"}>
        <div className="section-title"><div><span className="eyebrow">PROJECT-SCOPED REFERENCE LIBRARY</span><h2>{t("本项目专属资产卡库","This production's asset library","この制作専用の素材カード","本專案專屬資產卡庫")}</h2></div>
          <div className="production-actions"><button className="primary" disabled={production.task_state==="paused"} onClick={()=>void planCards()}><Sparkles size={17}/>{t("AI 自动补全文字卡","AI complete text cards","AIで文章カードを補完","AI 自動補全文字卡")}</button><button disabled={production.task_state==="paused"||!!busy||!models.includes(imageModel)} onClick={()=>void planCardsAndImages()}><Sparkles size={17}/>{t("从剧情建卡并补图","Plan cards and images","物語からカードと画像を生成","從劇情建卡並補圖")}</button><button disabled={production.task_state==="paused"||!!busy||!models.includes(imageModel)} onClick={()=>void generateMissingCardImages()}><ImagePlus size={17}/>{t("批量生成缺图卡","Generate missing card images","不足カード画像を一括生成","批量生成缺圖卡")}</button><button onClick={()=>void run(t("保存资产卡","Save asset cards","素材カードを保存","儲存資產卡"),async()=>{await save();setNotice(t("资产卡已保存；相关旧分镜会自动标记为需重建。","Asset cards saved. Affected older clips are marked stale automatically.","素材カードを保存しました。影響する既存クリップは自動的に再構築対象になります。","資產卡已儲存；相關舊分鏡會自動標記為需重建。"))})}><Save size={17}/> {t("保存卡库","Save library","カード庫を保存","儲存卡庫")}</button></div></div>
        {production.card_planner_warning&&<p className="planner-warning">{production.card_planner_warning}</p>}
        <p className="card-strategy">{t("每段由本地 LLM 按剧情只选本镜头实际出现的卡。角色独立图优先；角色、服装、道具、环境的图片合计超过 9 张，或所选卡缺少独立图时，才启用总图。角色必须压缩时会从本镜头选中的角色图自动生成专属编号拼图，不再直接套用可能含有额外人物的全员总图。声线走独立音频位，风格图只作分析上下文。","The local LLM selects only cards actually present in each clip. Individual character images have priority. An overview is used only when the selected character, wardrobe, prop and environment images exceed nine slots or a selected card lacks its own image. If the cast must be compressed, Studio builds a numbered clip-specific sheet from the selected characters instead of reusing a full-cast sheet that may contain extras. Voices use separate audio slots and style images remain analysis context.","ローカルLLMは各クリップに実際に登場するカードだけを選びます。人物の個別画像を優先し、選択した人物・衣装・小道具・環境が合計9枚を超える場合、または個別画像がない場合のみ総覧画像を使います。人物を圧縮する必要がある場合は、全員画像を流用せず、そのクリップで選んだ人物だけの番号付き画像を自動作成します。音声は別枠、スタイル画像は分析用です。","每段由本地 LLM 按劇情只選本鏡頭實際出現的卡。角色獨立圖優先；角色、服裝、道具、環境的圖片合計超過 9 張，或所選卡缺少獨立圖時，才啟用總圖。角色必須壓縮時會從本鏡頭選中的角色圖自動生成專屬編號拼圖，不再直接套用可能含有額外人物的全員總圖。聲線走獨立音訊位，風格圖只作分析上下文。")}</p>
        <div className="card-collection-bar"><label>{t("当前卡组分类名","Current card-set name","現在のカードセット名","目前卡組分類名")}<input value={production.card_collection_name} onChange={e=>updateField("card_collection_name",e.target.value)} placeholder={t("例如：图书馆篇主角组","e.g. Library arc main cast","例：図書館編メインキャスト","例如：圖書館篇主角組")}/></label>
          <button onClick={()=>void saveCollection()}><Save size={16}/>{production.card_collection_id?t("同步到共享卡组","Sync shared card set","共有カードを同期","同步到共享卡組"):t("保存为可复用卡组","Save reusable set","再利用セットとして保存","儲存為可重用卡組")}</button>
          <label>{t("载入其他卡组","Load another set","別セットを読込","載入其他卡組")}<select value={selectedCollection} onChange={e=>setSelectedCollection(e.target.value)}><option value="">{t("请选择命名卡组","Choose a named set","名前付きセットを選択","請選擇命名卡組")}</option>{collections.map(x=><option key={x.id} value={x.id}>{x.name} · {x.card_count}{x.overview_count?" + "+x.overview_count+" "+t("张总图","overviews","総覧","張總圖"):""}</option>)}</select></label>
          <button disabled={!selectedCollection} onClick={()=>void applyCollection()}>{t("合并载入","Merge into project","統合して読込","合併載入")}</button></div>
        <p className="card-strategy">{t("项目卡与共享卡组是两份数据：保存卡库只保存当前项目；点“同步到共享卡组”才把新增卡、参考图和声线样本增量共享。系列页绑定卡组也不会自动覆盖各项目。","Project cards and shared cards are separate. Save library only saves this project; Sync shared card set explicitly shares new cards, images and voice samples. Linking a set to a series never overwrites projects.","作品内カードと共有カードは別です。作品の保存だけでは共有されません。「共有カードを同期」で追加分を共有します。シリーズへの関連付けで作品は上書きされません。","專案卡與共享卡組是兩份資料：儲存卡庫只儲存目前專案；點「同步到共享卡組」才增量共享新增卡、圖片與聲線樣本。")}</p>
        {production.card_collection_id&&collections.find(item=>item.id===production.card_collection_id)&&<p className="production-card-sync-status"><BookOpen size={16}/>{t("本项目 "+Object.values(production.cards).reduce((sum,rows)=>sum+rows.length,0)+" 张卡 · 共享卡组 "+collections.find(item=>item.id===production.card_collection_id)!.card_count+" 张卡。数量不同可能正常；新增内容要点上方“同步到共享卡组”。","This project has "+Object.values(production.cards).reduce((sum,rows)=>sum+rows.length,0)+" cards; the shared set has "+collections.find(item=>item.id===production.card_collection_id)!.card_count+". Different counts can be normal; use Sync shared card set to share additions.","この作品のカード "+Object.values(production.cards).reduce((sum,rows)=>sum+rows.length,0)+" 枚・共有セット "+collections.find(item=>item.id===production.card_collection_id)!.card_count+" 枚。追加を共有するには上の同期ボタンを押してください。","本專案 "+Object.values(production.cards).reduce((sum,rows)=>sum+rows.length,0)+" 張卡 · 共享卡組 "+collections.find(item=>item.id===production.card_collection_id)!.card_count+" 張卡。新增內容請點上方同步。")}</p>}
        <div className="production-card-tabs">{CARD_KINDS.map(kind=><button key={kind} className={cardKind===kind?"active":""} onClick={()=>{setCardKind(kind);setOpenCardId(null)}}>
          {cardMeta[kind].title}<span>{production.cards[kind].length}</span></button>)}</div>
        <div className="production-card-kind-head"><div><h3>{cardMeta[cardKind].title}</h3><p>{cardMeta[cardKind].hint}</p></div>
          <button className="primary" onClick={()=>addCard(cardKind)}><Plus size={17}/> {t("新建","New","新規")} {cardMeta[cardKind].title}</button></div>
        {cardKind==="voices"&&<div className="production-series-voice-style"><div><span className="eyebrow">SERIES VOICE STYLE</span><h3>{t("全剧声线风格","Series voice style","シリーズ音声スタイル","全劇聲線風格")}</h3><p>{t("写所有角色共用的语言、表演、录音质感和禁忌。本段只要有对白就会使用；英文内容会原文锁定，非英文会转成 H3 所需的英文。","Define the shared language, acting, recording treatment and exclusions. It is used whenever a clip has dialogue. English text is preserved verbatim; other languages are converted to H3-ready English.","全人物共通の言語・演技・録音質感・禁止事項を記入します。台詞のあるクリップで使用され、英語は原文固定、その他の言語はH3用英語に変換されます。","填寫所有角色共用的語言、表演、錄音質感和禁忌。有對白的片段都會使用；英文原文鎖定，其他語言轉為 H3 所需英文。")}</p></div><textarea rows={9} value={production.series_voice_style||""} onChange={e=>updateField("series_voice_style",e.target.value)} placeholder={t("例如：Neutral American English；动画表演自然有层次；清晰近讲棚录；禁止幼儿化、广告腔、机器人感……","Example: Neutral American English; expressive but grounded animation acting; clean close studio dialogue; no babyish, commercial or robotic delivery...","例：ニュートラルな米国英語、自然で表情豊かなアニメ演技、明瞭な近接スタジオ収録、幼児的・広告的・機械的な話し方は禁止…","例如：Neutral American English；動畫表演自然有層次；清晰近講棚錄；禁止幼兒化、廣告腔、機器人感……")}/></div>}
        {overviewKind&&<div className="production-overview-card">
          <div className="production-overview-copy"><span>{t("智能兜底","Smart fallback","スマート補助","智慧兜底")}</span><strong>{cardMeta[overviewKind].title} · {t("总图","Overview","総覧画像","總圖")}</strong>
            <p>{t("建议使用带清晰分区、名称标注且不重复的总览拼图。它不会一直占位，只在独立参考不足或 9 图额度紧张时自动调用。","Use a clearly divided, labelled overview without duplicates. It only occupies a slot when individual references are incomplete or the nine-image budget is tight.","明確に区切り、名前を付けた重複のない総覧画像がおすすめです。個別参照が不足する時や9枚枠が厳しい時だけ自動使用します。","建議使用帶清晰分區、名稱標註且不重複的總覽拼圖。它不會一直佔位，只在獨立參考不足或 9 圖額度緊張時自動調用。")}</p></div>
          <div className="production-overview-media">{production.overview_asset_ids?.[overviewKind]?<div className="production-overview-image"><img src={"/api/assets/"+production.overview_asset_ids[overviewKind]+"/thumbnail"} alt={cardMeta[overviewKind].title+" overview"}/><button className="icon" onClick={()=>unlinkOverviewAsset(overviewKind)} title={t("移除总图","Remove overview","総覧画像を外す","移除總圖")}><X size={15}/></button></div>:<div className="production-overview-empty"><ImagePlus size={22}/><small>{t("尚未上传","Not uploaded","未設定","尚未上傳")}</small></div>}
            <label className="production-upload-button"><Upload size={16}/>{production.overview_asset_ids?.[overviewKind]?t("替换总图","Replace overview","総覧画像を交換","替換總圖"):t("上传总图","Upload overview","総覧画像を追加","上傳總圖")}<input type="file" hidden accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp" onChange={e=>{const file=e.target.files?.[0];if(file)void uploadOverviewAsset(overviewKind,file);e.currentTarget.value=""}}/></label></div>
        </div>}
        {!production.cards[cardKind].length&&<div className="production-empty-cards">{t("这里还没有卡片。可点“AI 自动补全文字卡”；直接规划剧集或分镜时，系统也会先自动分析。之后再按需补图或声音。","No cards yet. Choose “AI complete text cards”, or start AI episode/clip planning and the analysis will run automatically. Add images or voices later as needed.","カードはまだありません。「AIで文章カードを補完」を押すか、AIエピソード／クリップ計画を始めると自動分析されます。画像や音声は後から追加できます。","這裡還沒有卡片。可點「AI 自動補全文字卡」；直接規劃劇集或分鏡時，系統也會先自動分析。之後再按需補圖或聲音。")}</div>}
        <div className="production-card-editor-grid">{production.cards[cardKind].map(card=><article className={"production-asset-card "+(openCardId===card.id?"expanded":"collapsed")} key={card.id}>
          <header><input value={card.name} onChange={e=>updateCard(cardKind,card.id,"name",e.target.value)} placeholder={t("卡片名称","Card name","カード名","卡片名稱")}/>
            <span className="production-card-summary">{card.asset_ids.length} {t("个参考文件","reference files","個の参照ファイル","個參考檔案")}</span>
            <label className="production-inline-lock"><input type="checkbox" checked={card.locked} onChange={e=>updateCard(cardKind,card.id,"locked",e.target.checked)}/>{t("锁定","Lock","固定","鎖定")}</label>
            <button className="icon" onClick={()=>setOpenCardId(openCardId===card.id?null:card.id)} title={openCardId===card.id?t("收起卡片","Collapse card","カードを閉じる","收起卡片"):t("编辑卡片","Edit card","カードを編集","編輯卡片")}>{openCardId===card.id?<ChevronUp size={17}/>:<ChevronDown size={17}/>}</button>
            <button className="icon danger" onClick={()=>removeCard(cardKind,card.id)} title={t("删除卡片","Delete card","カードを削除","刪除卡片")}><Trash2 size={16}/></button></header>
          <div className="production-card-body">
          <label>{cardKind==="styles"?t("风格文字设定","Style text","スタイル文章設定","風格文字設定"):cardKind==="voices"?t("角色声线原文","Character voice direction","人物別音声指示","角色聲線原文"):t("稳定特征 / 描述","Stable traits / description","固定特徴 / 說明","穩定特徵 / 描述")}<textarea rows={cardKind==="voices"?7:3} value={card.description} onChange={e=>updateCard(cardKind,card.id,"description",e.target.value)}
            placeholder={cardKind==="voices"?t("完整写入年龄感、音高、音色、口吻、情绪变化、节奏、与其他角色的区分及禁止项。无需上传音频。","Write the complete age impression, pitch, timbre, manner, emotional range, rhythm, contrast with other voices and exclusions. No audio upload is required.","年齢感、音高、声色、口調、感情幅、リズム、他人物との差、禁止事項を完全に記入します。音声アップロードは不要です。","完整填寫年齡感、音高、音色、口吻、情緒變化、節奏、與其他角色的區分及禁止項。無需上傳音訊。"):cardKind==="styles"?t("例如：二维日系动画，柔和低饱和，细线条，电影光影；禁止 3D 塑料感","Example: 2D anime, soft low saturation, fine lines, cinematic light; no plastic 3D look","例：2Dアニメ、柔らかな低彩度、細い線、映画的照明。3Dのプラスチック感は禁止","例如：二維日系動畫，柔和低飽和，細線條，電影光影；禁止 3D 塑膠感"):t("只写必须保持一致、能从画面判断的特征","Only describe visible traits that must remain consistent","画面で判断でき、必ず維持すべき特徴だけを記述","只寫必須保持一致、能從畫面判斷的特徵")}/></label>
          {cardKind==="styles"&&!!card.image_analysis&&<div className="production-style-analysis"><strong><Sparkles size={15}/>{t("风格图分析（最高优先）","Style-image analysis · highest priority","スタイル画像分析・最優先","風格圖分析（最高優先）")}</strong><p>{card.image_analysis}</p></div>}
          {(["characters","wardrobe","props","environments"] as CardKind[]).includes(cardKind)&&<label>{t("仅用于生图的要求（可选）","Image-generation direction (optional)","画像生成専用の指示（任意）","僅用於生圖的要求（可選）")}<textarea rows={2} value={card.image_generation_prompt||""} onChange={e=>updateCard(cardKind,card.id,"image_generation_prompt",e.target.value)} placeholder={t("例如：全身正面，纯色背景，不要文字；修改后点“再生成一张”","For example: full body, front view, plain background, no text. Edit and regenerate.","例：全身正面、無地の背景、文字なし。編集後に再生成。","例如：全身正面、純色背景、不要文字；修改後再生成一張")}/><small>{t("只影响下次卡图生成，不改角色身份或已上传参考图。","Only affects the next generated card image, not the card identity or existing references.","次のカード画像生成だけに適用し、人物設定や既存の参照画像は変えません。","只影響下次卡圖生成，不修改角色身份或已有參考圖。")}</small></label>}
          {(cardKind==="wardrobe"||cardKind==="props")&&<label>{t("归属角色","Owner character","所有キャラクター","歸屬角色")}<select value={card.owner_card_id||""} onChange={e=>updateCard(cardKind,card.id,"owner_card_id",e.target.value||null)}>
            <option value="">{t("无固定归属 / 公共","No fixed owner / shared","固定所有者なし / 共用","無固定歸屬 / 公共")}</option>{production.cards.characters.map(x=><option key={x.id} value={x.id}>{x.name}</option>)}</select></label>}
          {cardKind==="voices"&&<>
            <label>{t("绑定角色","Bind character","人物に紐付け","綁定角色")}<select value={card.character_card_id||""} onChange={e=>updateCard(cardKind,card.id,"character_card_id",e.target.value||null)}>
              <option value="">{t("请选择角色","Choose a character","人物を選択","請選擇角色")}</option>{production.cards.characters.map(x=><option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
            <div className="production-card-fields"><label>{t("声线 ID","Voice ID","ボイスID","聲線 ID")}<input value={card.voice_id} onChange={e=>updateCard(cardKind,card.id,"voice_id",e.target.value)} placeholder={t("例如 MIMI_V1","e.g. MIMI_V1","例：MIMI_V1","例如 MIMI_V1")}/></label>
              <label>{t("目标语言","Target language","対象言語","目標語言")}<select value={production.language} disabled>{LANGUAGE_OPTIONS.map(([code,label])=><option key={code} value={code}>{label}</option>)}</select><small>{t("自动跟随项目输出语言；音色、节奏和表演设定保持不变。","Follows the project output language automatically; vocal identity, rhythm and acting remain unchanged.","プロジェクト出力言語に自動追従し、声質・リズム・演技設定は維持します。","自動跟隨專案輸出語言；音色、節奏和表演設定保持不變。")}</small></label>
              <label>{t("语速 / 节奏","Pace / rhythm","速度 / リズム","語速 / 節奏")}<input value={card.pace} onChange={e=>updateCard(cardKind,card.id,"pace",e.target.value)} placeholder={t("自然、偏慢、句尾短停顿","Natural, slightly slow, brief pauses at sentence ends","自然、やや遅め、文末に短い間","自然、偏慢、句尾短停頓")}/></label></div></>}
          <label>{t("连续性备注","Continuity notes","連続性メモ","連續性備註")}<textarea rows={2} value={card.notes} onChange={e=>updateCard(cardKind,card.id,"notes",e.target.value)} placeholder={t("不可替换项、使用限制或镜头间连续性","Non-replaceable traits, usage limits or continuity between shots","変更不可事項、使用制限、ショット間の連続性","不可替換項、使用限制或鏡頭間連續性")}/></label>
          {!!card.asset_ids.length&&<div className={cardKind==="voices"?"production-voice-assets":"production-card-assets"}>{card.asset_ids.map((assetId,index)=>
            cardKind==="voices"?<div className="production-voice-asset" key={assetId}><audio controls preload="metadata" src={"/api/assets/"+assetId+"/file"}/><button className="icon" onClick={()=>unlinkCardAsset(cardKind,card.id,assetId)}><X size={15}/></button></div>:
            <div className="production-card-image" key={assetId}><img src={"/api/assets/"+assetId+"/thumbnail"} alt={card.name+" 参考图 "+(index+1)}/><button className="icon" onClick={()=>unlinkCardAsset(cardKind,card.id,assetId)}><X size={14}/></button></div>)}</div>}
          <footer><label className="production-upload-button"><Upload size={16}/>{cardMeta[cardKind].upload}<input type="file" hidden
            accept={cardKind==="voices"?"audio/wav,audio/mpeg,audio/mp4,audio/flac,audio/ogg,.wav,.mp3,.m4a,.flac,.ogg":"image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp"}
            onChange={e=>{const file=e.target.files?.[0];if(file)void uploadCardAsset(cardKind,card,file);e.currentTarget.value=""}}/></label>
            {(["characters","wardrobe","props","environments"] as CardKind[]).includes(cardKind)&&<button disabled={!!busy||production.task_state==="paused"||!models.includes(imageModel)||card.asset_ids.length>=24} onClick={()=>void generateCardImage(cardKind,card)}><ImagePlus size={16}/>{card.asset_ids.length?t("再生成一张","Generate another","別の画像を生成","再生成一張"):t("本地生成参考图","Generate image locally","ローカルで画像生成","本地生成參考圖")}</button>}
            <span>{cardKind==="voices"?(!card.asset_ids.length?t("未上传音频：使用文字声线卡","No audio: using text voice card","音声なし：文章カードを使用","未上傳音訊：使用文字聲線卡"):t("已上传干声：音频主导，文字补充","Voice audio leads; text supplements","音声を最優先・文章で補足","已上傳乾聲：音訊主導，文字補充")):card.asset_ids.length+" "+t("个参考文件","reference files","個の参照ファイル","個參考檔案")}</span></footer>
          </div>
        </article>)}</div>
        <div className="production-actions production-section-next"><button onClick={()=>void run(t("保存资产卡","Save asset cards","素材カードを保存","儲存資產卡"),async()=>{await save();setNotice(t("资产卡已保存。","Asset library saved.","素材カードを保存しました。","資產卡已儲存。"))})}><Save size={17}/>{t("保存卡库","Save library","カード庫を保存","儲存卡庫")}</button><button className="primary" onClick={()=>setProductionPage("episodes")}>{t("下一步：规划剧集","Next: plan episodes","次へ：エピソード計画","下一步：規劃劇集")}<ChevronRight size={17}/></button></div>
      </section>
      <section className="production-episodes card" id="production-episodes" hidden={productionPage!=="episodes"}>
        <div className="section-title"><div><span className="eyebrow">SERIES & EPISODE MAP</span><h2>{t("剧集规划与每集角色表","Episode plan & cast continuity","エピソード計画と登場人物","劇集規劃與每集角色表")}</h2>
          <p>{t("先规划整季，再选择当前集细分镜头。系统按约 10 秒估算段数，再让每段根据剧情在 5–15 秒内变化，并对齐本集目标总时长。","Plan the season first, then select an episode for clip planning. The system estimates count at roughly 10s per clip, lets each story beat vary within 5–15s, and aligns their sum to the episode target.","先に全話を計画し、対象話をクリップ化します。約10秒を基準に本数を見積もり、物語に応じて各クリップを5〜15秒で変化させ、合計を話の目標尺に合わせます。","先規劃整季，再選擇目前集細分鏡頭。系統按約 10 秒估算段數，再讓每段根據劇情在 5–15 秒內變化，並對齊本集目標總時長。")}</p></div>
          <div className="production-actions"><button className="primary" disabled={production.task_state==="paused"} onClick={()=>void planEpisodes(true)}><Sparkles size={17}/>{t("AI 规划全部剧集","AI plan all episodes","AIで全話を計画","AI 規劃全部劇集")}</button><button disabled={production.task_state==="paused"} onClick={()=>void planEpisodes(false)}>{t("不用 AI 先划分","Split without AI","AIなしで分割","不用 AI 先劃分")}</button></div></div>
        {production.episode_planner_warning&&<p className="planner-warning">{production.episode_planner_warning}</p>}
        {!production.episodes.length?<div className="production-episode-empty">{t("还没有剧集表。设置剧集数和单集时长后，点击“AI 规划全部剧集”。","No episode map yet. Set episode count and target length, then choose “AI plan all episodes”.","エピソード表はまだありません。話数と目標時間を設定して「AIで全話を計画」を押してください。","還沒有劇集表。設定劇集數和單集時長後，點擊「AI 規劃全部劇集」。")}</div>:<>
        <div className="episode-flow-guide" aria-label={t("从剧集规划进入分镜制作","Episode-to-storyboard flow","エピソードから絵コンテへの流れ","從劇集規劃進入分鏡製作")}><div className="episode-flow-copy"><span className="done"><Check size={15}/>{t("剧集规划已完成","Episode plan ready","エピソード計画完了","劇集規劃已完成")}</span><ChevronRight size={17}/><strong>{t("生成当前集分镜","Create the current episode storyboard","現在話の絵コンテを作成","生成目前集分鏡")}</strong><ChevronRight size={17}/><span>{t("自动进入第 4 步","Open Step 4 automatically","自動でステップ4へ","自動進入第 4 步")}</span></div>{currentEpisodeForPlan&&<button className="primary episode-flow-cta" disabled={production.task_state==="paused"||!!busy} onClick={()=>void planEpisodeClips(currentEpisodeForPlan)}>{busy?<LoaderCircle className="spin" size={18}/>:<Clapperboard size={18}/>}<span><b>{t("下一步：生成当前集分镜","Next: create current storyboard","次へ：現在話の絵コンテ作成","下一步：生成目前集分鏡")}</b><small>{t("第 "+currentEpisodeForPlan.index+" 集 · 约 "+currentEpisodeTiming.recommendedClips+" 段","Episode "+currentEpisodeForPlan.index+" · about "+currentEpisodeTiming.recommendedClips+" clips","第"+currentEpisodeForPlan.index+"話・約"+currentEpisodeTiming.recommendedClips+"本","第 "+currentEpisodeForPlan.index+" 集 · 約 "+currentEpisodeTiming.recommendedClips+" 段")}</small></span><ChevronRight size={18}/></button>}</div>
        <div className="production-episode-list">{production.episodes.map(episode=>{const earlier=new Set(production.episodes.filter(x=>x.index<episode.index).flatMap(x=>x.character_card_ids));const returning=episode.character_card_ids.filter(id=>earlier.has(id));const first=episode.character_card_ids.filter(id=>!earlier.has(id));const isOpen=openEpisodeId===episode.id;return <article className={production.current_episode===episode.index?"production-episode current":"production-episode"} key={episode.id}>
          <header onClick={()=>setOpenEpisodeId(isOpen?null:episode.id)}><b>{String(episode.index).padStart(2,"0")}</b><div><input value={episode.title} onClick={e=>e.stopPropagation()} onChange={e=>updateEpisode(episode.id,"title",e.target.value)}/><span>{episode.logline||t("待补充本集梗概","Add episode logline","ログラインを追加","待補充本集梗概")}</span></div><div className="episode-cast-count"><span className="episode-timing">≈ {currentEpisodeTiming.recommendedClips} {t("段","clips","本","段")} · {currentEpisodeTiming.targetSeconds}s</span><span>{episode.character_card_ids.length} {t("名角色","cast","人","名角色")}</span>{returning.length>0&&<em>{returning.length} {t("名回归","returning","人再登場","名回歸")}</em>}</div>{isOpen?<ChevronUp size={18}/>:<ChevronDown size={18}/>}</header>
          {isOpen&&<div className="production-episode-body"><label>{t("本集一句话梗概","Episode logline","話のログライン","本集一句話梗概")}<textarea rows={2} value={episode.logline} onChange={e=>updateEpisode(episode.id,"logline",e.target.value)}/></label>
            <label>{t("本集完整剧情 / 剧本","Episode story / screenplay","話のストーリー / 脚本","本集完整劇情 / 劇本")}<textarea rows={7} value={episode.story} onChange={e=>updateEpisode(episode.id,"story",e.target.value)}/></label>
            <div className="episode-cast"><strong>{t("本集出场角色表","Episode cast","登場人物","本集出場角色表")}</strong><div>{production.cards.characters.map(card=><label key={card.id} className={episode.character_card_ids.includes(card.id)?"selected":""}><input type="checkbox" checked={episode.character_card_ids.includes(card.id)} onChange={()=>toggleEpisodeCharacter(episode,card.id)}/>{card.name}{returning.includes(card.id)?<em>{t("回归","Returning","再登場","回歸")}</em>:first.includes(card.id)?<em>{t("首次出场","First appearance","初登場","首次出場")}</em>:null}</label>)}</div>{!production.cards.characters.length&&<small>{t("先在卡库建立角色卡，AI 才能生成可追踪的每集角色表。","Create character cards first so AI can build a trackable episode cast.","追跡可能な登場人物表には、先に人物カードを作成してください。","先在卡庫建立角色卡，AI 才能生成可追蹤的每集角色表。")}</small>}</div>
            <label>{t("本集连续性与下集交接","Episode continuity handoff","連続性と次話への引継ぎ","本集連續性與下集交接")}<textarea rows={3} value={episode.continuity_notes} onChange={e=>updateEpisode(episode.id,"continuity_notes",e.target.value)}/></label>
            <div className="episode-next-step"><div className="episode-next-step-copy"><span><Check size={14}/>{t("本集剧情与角色表已准备","Episode story and cast are ready","物語と登場人物の準備完了","本集劇情與角色表已準備")}</span><strong>{t("下一步：生成第 "+episode.index+" 集分镜","Next: create Episode "+episode.index+" storyboard","次へ：第"+episode.index+"話の絵コンテを作成","下一步：生成第 "+episode.index+" 集分鏡")}</strong><small>{t("预计约 "+currentEpisodeTiming.recommendedClips+" 个 5–15 秒片段；完成后自动进入“分镜制作”。","About "+currentEpisodeTiming.recommendedClips+" clips of 5–15 seconds; Storyboard opens automatically when ready.","約"+currentEpisodeTiming.recommendedClips+"本（5〜15秒）。完了後、自動で絵コンテ制作を開きます。","預計約 "+currentEpisodeTiming.recommendedClips+" 個 5–15 秒片段；完成後自動進入「分鏡製作」。")}</small></div><button className="primary episode-plan-cta" disabled={production.task_state==="paused"||!!busy} onClick={()=>void planEpisodeClips(episode)}>{busy?<LoaderCircle className="spin" size={21}/>:<Clapperboard size={21}/>}<span><b>{t("生成本集分镜并进入下一步","Create storyboard & continue","絵コンテを作成して次へ","生成本集分鏡並進入下一步")}</b><small>{t("第 "+episode.index+" 集 · 约 "+currentEpisodeTiming.recommendedClips+" 段","Episode "+episode.index+" · about "+currentEpisodeTiming.recommendedClips+" clips","第"+episode.index+"話・約"+currentEpisodeTiming.recommendedClips+"本","第 "+episode.index+" 集 · 約 "+currentEpisodeTiming.recommendedClips+" 段")}</small></span><ChevronRight size={21}/></button></div></div>}
        </article>})}</div></>}
      </section>
      <section className="production-overview" hidden={productionPage!=="storyboard"}>
        <div><strong>{production.segments.length}</strong><span>{t("个动态片段","dynamic clips","個の動的クリップ","個動態片段")}</span></div>
        <div><strong>{totalSeconds}s / {currentEpisodeTiming.targetSeconds}s</strong><span>{t("生成时长 / 本集目标（续写重叠未扣除）","generated / episode target (before continuation overlap)","生成尺 / 話の目標（継続重複を除く前）","生成時長／本集目標（續寫重疊未扣除）")}</span></div>
        <div><strong>{ready}</strong><span>{t("已准备 H3 工程","H3 projects ready","準備済みH3プロジェクト","已準備 H3 專案")}</span></div>
        <div><strong>{stale}</strong><span>{t("修改后待重建","stale after changes","変更後・再構築待ち","修改後待重建")}</span></div>
      </section>
      <section className="production-timings card" hidden={productionPage!=="output"} aria-label={t("制作耗时","Production timings","制作所要時間","製作耗時")}>
        <div className="production-timings-title"><Clock3 size={19}/><div><strong>{t("制作耗时记录","Production timing","制作時間の記録","製作耗時記錄")}</strong><span>{t("显示本机实际完成用时；视频为当前采用版本累计，等待和模型装载也计入总耗时。","Actual local elapsed time. Video is the sum of adopted takes; total elapsed time includes waiting and model loading.","このPCでの実測時間です。映像は採用テイクの合計で、待機とモデル読込も総時間に含みます。","顯示本機實際完成用時；影片為目前採用版本累計，等待和模型載入也計入總耗時。")}</span></div></div>
        <div className="production-timing-grid"><div><span>{t("剧集规划","Episode planning","エピソード計画","劇集規劃")}</span><strong>{formatSeconds(outputs?.timings.episode_plan_seconds??production.timings?.episode_plan_seconds)}</strong></div><div><span>{t("分镜 / 剧本规划","Storyboard planning","絵コンテ計画","分鏡／劇本規劃")}</span><strong>{formatSeconds(outputs?.timings.storyboard_plan_seconds??production.timings?.storyboard_plan_seconds)}</strong></div><div><span>{t("视频提示词","Video prompts","映像プロンプト","影片提示詞")}</span><strong>{formatSeconds(outputs?.timings.prompt_generation_seconds)}</strong></div><div><span>{t("视频生成（采用版本）","Video renders (adopted)","映像生成（採用分）","影片生成（採用版本）")}</span><strong>{formatSeconds(outputs?.timings.video_generation_seconds)}</strong></div><div><span>{t("最终合并","Final assembly","最終結合","最終合併")}</span><strong>{formatSeconds(outputs?.timings.merge_seconds??production.timings?.merge_seconds)}</strong></div></div>
      </section>
      <section className="production-settings card" hidden={productionPage!=="settings"}>
        <div className="section-title"><div><span className="eyebrow">SETTINGS</span><h2><Settings2 size={21}/>{t("设置","Settings","設定","設定")}</h2><p>{t("这里的参数应用到本项目之后创建或重建的每个 H3 分镜。修改尺寸会把已准备片段标记为需要重建。","These settings apply to every H3 clip created or rebuilt for this project. Changing the output shape marks prepared clips for rebuild.","この設定は今後作成・再構築する全H3クリップに適用されます。画面サイズを変えると準備済みクリップは再構築対象になります。","這裡的參數套用到本專案之後建立或重建的每個 H3 分鏡。修改尺寸會把已準備片段標記為需要重建。")}</p></div><button className="primary" onClick={()=>void run(t("保存设置","Save settings","設定を保存","儲存設定"),async()=>{await save();setNotice(t("统一制作设置已保存。","Production settings saved.","共通制作設定を保存しました。","統一製作設定已儲存。"))})}><Save size={17}/>{t("保存设置","Save settings","設定を保存","儲存設定")}</button></div>
        <div className="production-auto-keyframes-controls"><label>{t("卡图与手动关键帧生图配方","Card and manual keyframe image recipe","カード・手動キーフレーム画像レシピ","卡圖與手動關鍵影格生圖配方")}<select value={imageModel} onChange={e=>chooseImageModel(e.target.value)}>{!models.includes(imageModel)&&<option value={imageModel}>{imageModel} · {t("当前不可用","unavailable","利用不可","目前不可用")}</option>}{models.map(model=><option key={model} value={model}>{model}</option>)}</select></label><button onClick={()=>void run(t("刷新 ComfyUI 模型","Refresh ComfyUI models","ComfyUIモデルを更新","重新整理 ComfyUI 模型"),async()=>{await refreshImageModels()})}><RefreshCw size={16}/>{t("刷新模型检测","Refresh model check","モデルを再確認","重新整理模型檢查")}</button></div>
        <small>{t("Krea 2 为附件改造的可选 8 步无 LoRA 生图配方；只在模型与节点齐全时出现，不影响原工作流。","Krea 2 is the optional adapted eight-step no-LoRA image recipe. It appears only when all required models and nodes are present; original workflows are unchanged.","Krea 2は添付から改造した任意の8ステップ・LoRAなし画像レシピです。必要モデルとノードが揃う時だけ表示され、元のフローは変えません。","Krea 2 是附件改造的可選 8 步無 LoRA 生圖配方；僅在模型與節點齊全時出現，不影響原工作流。")}</small>
        <label>{t("视频提示词规则","Video prompt rules","映像プロンプト規則","影片提示詞規則")}<select value={production.prompt_version==="storyboard_narrative"?"continuity_director":production.prompt_version||"classic"} onChange={e=>updateField("prompt_version",e.target.value as Production["prompt_version"])}><option value="classic">{t("原版 H3 · 已加强防重复与风格锁定","Classic H3 · improved cast and style locks","従来版H3・重複と画風を改善","原版 H3 · 已加強防重複與風格鎖定")}</option><option value="continuity_director">{t("导演连续性 · 叙事导演稿","Director continuity · narrative storyboard","監督連続性・絵コンテ叙述","導演連續性 · 敘事導演稿")}</option></select><small>{t("两个版本都已加强角色绑定；导演版更精简。保存后重做提示词才作用于新视频，已有视频不变。","Both versions have stronger identity safeguards; Director is more concise. Save and regenerate prompts for new videos. Existing videos remain unchanged.","両版とも人物の対応付けを強化。監督版はより簡潔です。保存後にプロンプトを再生成すると新しい映像に反映され、既存映像は変わりません。","兩個版本都已加強角色綁定；導演版更精簡。儲存後重做提示詞才作用於新影片，已有影片不變。")}</small></label>
        <div className="production-setting-grid">
          <label>{t("视频画幅","Video aspect ratio","映像アスペクト比","影片畫幅")}<select value={production.video_aspect_ratio} onChange={e=>updateField("video_aspect_ratio",e.target.value)}>{["16:9","9:16","1:1","4:3","3:4"].map(value=><option key={value}>{value}</option>)}</select><small>{t("全项目统一；续写已有 MMH3 时仍以源片尺寸为准。","Shared by the project; MMH3 continuation still preserves its source size.","プロジェクト共通。MMH3継続時は元映像サイズを維持します。","全專案統一；續寫既有 MMH3 時仍以來源片尺寸為準。")}</small></label>
          <label>{t("视频分辨率","Video resolution","映像解像度","影片解析度")}<select value={production.video_resolution} onChange={e=>updateField("video_resolution",e.target.value)}>{["0.3","0.5","0.7","1.0"].map(value=><option key={value} value={value}>{value} MP{value==="0.3"?t(" · 快速预览"," · quick preview","・高速プレビュー"," · 快速預覽"):""}</option>)}</select><small>{t("分辨率越高，显存占用和生成时间越大。","Higher resolution uses more VRAM and render time.","高解像度ほどVRAMと生成時間が増えます。","解析度越高，顯示記憶體占用和生成時間越大。")}</small></label>
          <label>{t("质量配方","Quality recipe","品質レシピ","品質配方")}<select value={production.video_quality} onChange={e=>updateQuality(e.target.value as Production["video_quality"])}><option value="fast">{t("已验证快速配方","Fast tested recipe","検証済み高速レシピ","已驗證快速配方")}</option><option value="detailed">{t("双倍步数对比","Double-step comparison","倍ステップ比較","雙倍步數對比")}</option><option value="lora8" disabled={production.source_mode!=="ref2va"}>{t("8步 LoRA 加速","8-step LoRA acceleration","8ステップLoRA加速","8步 LoRA 加速")}</option></select><small>{production.video_quality==="lora8"?t("仅参考图模式；使用附件流程的 8 步、LoRA 0.5 和声音输出。原流程不变。","Reference mode only. Uses the supplied 8-step workflow, LoRA at 0.5 and audio output. The original workflow stays unchanged.","参照画像モード専用。添付の8ステップ、LoRA 0.5、音声出力を使用。元のフローは変更しません。","僅參考圖模式；使用附件流程的 8 步、LoRA 0.5 和聲音輸出。原流程不變。"):t("详细配方更慢，不保证每个镜头都更好。","Detailed is slower and is not guaranteed to improve every shot.","詳細設定は遅く、すべてのショットで品質向上を保証しません。","詳細配方較慢，不保證每個鏡頭都更好。")}</small></label>
          <label>{t("采样步数","Sampling steps","サンプリングステップ","採樣步數")}<select value={production.video_quality==="lora8"?"auto":production.video_steps} disabled={production.video_quality==="lora8"} onChange={e=>updateField("video_steps",e.target.value==="auto"?"auto":Number(e.target.value))}><option value="auto">{production.video_quality==="lora8"?t("配方固定 8 步","Fixed at 8 steps","8ステップ固定","配方固定 8 步"):t("使用配方默认值","Recipe default","レシピ既定値","使用配方預設值")}</option>{[4,8,16].map(value=><option key={value} value={value}>{value} {t("步","steps","ステップ","步")}</option>)}</select><small>{t("通常保持自动；只在对比测试时手动指定。","Keep Auto normally; set this only for controlled comparisons.","通常は自動のままにし、比較テスト時だけ指定します。","通常保持自動；只在對比測試時手動指定。")}</small></label>
        </div>
        <div className="production-auto-keyframes">
          <div className="production-auto-keyframes-head"><div><strong>{t("自动接续上一段结尾","Continue from the preceding clip automatically","前のクリップの結末から自動継続","自動接續上一段結尾")}</strong><p>{t("默认关闭。开启后按分镜顺序生成时，下一段会使用上一段已采用视频保存的 .mmh3 动作与声音状态；首段会自动保存续接状态。前段未完成或没有有效状态时会停下提示，不会偷偷改成普通新镜头。只支持内置 H3 参考图流程；续接段最多 13 秒新画面，合片会自动去掉重叠开头。","Off by default. In storyboard order, each following clip uses the adopted preceding take's verified .mmh3 motion and audio state. The first clip saves that state. Missing state stops with an explanation rather than silently starting a new shot. Built-in H3 reference workflows only; continued clips support up to 13 seconds of new footage. Assembly removes the repeated opening.","初期設定はオフ。順番に生成すると前クリップの採用テイクに保存された.mmH3の動きと音声を継承します。元データがなければ停止します。内蔵H3参照フロー専用、継続部分の新規映像は最大13秒です。結合時に重複部分を除きます。","預設關閉。按分鏡順序生成時，下一段使用上一段採用影片儲存的 .mmh3 動作與聲音狀態；首段會儲存續接狀態。前段未完成或無有效狀態時會停止提示，不會暗中改為新鏡頭。僅支援內置 H3 參考圖流程；續接段最多 13 秒新畫面，合片會去掉重疊開頭。")}</p></div><label className="auto-merge"><input type="checkbox" checked={!!production.auto_continue_previous} onChange={e=>updateField("auto_continue_previous",e.target.checked)}/><span>{production.auto_continue_previous?t("已开启","On","オン","已開啟"):t("关闭","Off","オフ","關閉")}</span></label></div>
        </div>
        <div className="production-auto-keyframes">
          <div className="production-auto-keyframes-head"><div><strong>{t("自动补环境关键帧","Automatic environment keyframes","背景キーフレームの自動補完","自動補環境關鍵影格")}</strong><p>{t("默认关闭。开启后，一键生成会在提示词和视频之前，为缺少环境参考的新场景最多生成 3 张背景图；已有参考图或片段关键帧时跳过。不会占用 H3 的 9 个参考图位。","Off by default. When enabled, one-click production first makes up to three background images for new locations missing environment references. Existing references and keyframes are skipped. These do not use H3's nine image slots.","初期設定はオフ。オンにすると、一括制作時に背景参照のない新しい場所へ最大3枚の背景画像を先に生成します。既存の参照やキーフレームは省略し、H3の9画像枠は使用しません。","預設關閉。開啟後，一鍵生成會先為缺少環境參考的新場景補最多 3 張背景圖；已有參考圖或關鍵影格則跳過，不佔 H3 的 9 個參考圖位。")}</p></div><label className="auto-merge"><input type="checkbox" checked={!!production.auto_keyframes_enabled} onChange={e=>updateField("auto_keyframes_enabled",e.target.checked)}/><span>{production.auto_keyframes_enabled?t("已开启","On","オン","已開啟"):t("关闭","Off","オフ","關閉")}</span></label></div>
          {production.auto_keyframes_enabled&&<div className="production-auto-keyframes-controls"><label>{t("本地生图模型","Local image model","ローカル画像モデル","本地生圖模型")}<select value={production.auto_keyframe_model} onChange={e=>updateField("auto_keyframe_model",e.target.value)}>{Array.from(new Set([production.auto_keyframe_model,...models])).filter(Boolean).map(model=><option key={model} value={model}>{model}{!models.includes(model)?t(" · 当前未检测到"," · not detected","・未検出"," · 目前未偵測到"):""}</option>)}</select></label><button onClick={()=>void run(t("检查自动关键帧建议","Preview automatic keyframes","自動キーフレーム案を確認","檢查自動關鍵影格建議"),async()=>{const saved=await save();const result=await api("/productions/"+saved.id+"/keyframe-suggestions") as {suggestions:{segment_id:string;index:number;reason:string;prompt:string}[]};setAutoKeyframeSuggestions(result.suggestions);})}>{t("预览建议","Preview suggestions","候補を確認","預覽建議")}</button></div>}
          {production.auto_keyframes_enabled&&autoKeyframeSuggestions.length>0&&<div className="production-auto-keyframes-preview"><strong>{t("本次预计补图","Suggested images","生成候補","本次預計補圖")}</strong>{autoKeyframeSuggestions.map(item=><div key={item.segment_id}><b>{t("第 "+item.index+" 段","Clip "+item.index,"クリップ "+item.index,"第 "+item.index+" 段")}</b><span>{t("新场景缺少环境参考图","New location lacks an environment reference","新しい場所に背景参照がありません","新場景缺少環境參考圖")}</span></div>)}</div>}
          {production.auto_keyframes_enabled&&<small>{t("请先保存设置。生图仍由本机 ComfyUI 执行，可能占用显存；失败时一键流程会停在视频生成之前。","Save settings first. Local ComfyUI performs image generation and may use VRAM; a failure stops one-click production before video submission.","先に設定を保存してください。画像生成はローカルComfyUIで実行され、VRAMを使います。失敗時は映像送信前に停止します。","請先儲存設定。生圖由本機 ComfyUI 執行，可能占用顯示記憶體；失敗時一鍵流程會在提交影片前停止。")}</small>}
        </div>
        <div className="production-settings-footer"><label className="auto-merge"><input type="checkbox" checked={production.auto_merge!==false} onChange={e=>void setAutoMerge(e.target.checked)}/><span>{t("所有片段完成后自动合并成片","Automatically assemble when every clip is ready","全クリップ完了後に自動結合","所有片段完成後自動合併成片")}</span></label><button onClick={()=>onStudio()}>{t("打开工作室母版设置","Open Studio master project","Studioの元プロジェクト設定を開く","開啟工作室母版設定")}</button></div>
      </section>
      <section className="production-workflows card" hidden={productionPage!=="settings"}>
        <div className="section-title"><div><span className="eyebrow">COMFYUI VIDEO WORKFLOWS</span><h2><Video size={20}/>{t("视频工作流库","Video workflow library","映像ワークフロー庫","影片工作流庫")}</h2><p>{t("内置 H3/MMH3 流程保持原样并永远作为默认。可追加导入可信的 ComfyUI API JSON；系统会确认 H3 条件节点与单一 SaveVideo 输出，再统一注入本段提示词、参考图、Seed 和项目目录。自定义节点仍由本机 ComfyUI 执行，请只导入你信任的工作流。","The built-in H3/MMH3 workflow remains unchanged and default. Import trusted ComfyUI API JSON; Studio verifies an H3 conditioning node and one SaveVideo output, then injects this clip's prompt, references, seed and project folder. Custom nodes still run inside your local ComfyUI, so import only workflows you trust.","内蔵H3/MMH3フローは変更せず既定のままです。信頼できるComfyUI API JSONを追加すると、H3条件ノードと単一SaveVideo出力を確認し、クリップのプロンプト・参照・Seed・プロジェクト保存先を注入します。カスタムノードはローカルComfyUIで実行されるため、信頼できるフローだけを読み込んでください。","內置 H3/MMH3 流程保持原樣並永遠作為預設。可追加匯入可信的 ComfyUI API JSON；系統會確認 H3 條件節點與單一 SaveVideo 輸出，再統一注入本段提示詞、參考圖、Seed 和專案目錄。自訂節點仍由本機 ComfyUI 執行，請只匯入你信任的工作流。")}</p></div><label className="production-upload-button"><Upload size={16}/>{t("导入 API workflow JSON","Import API workflow JSON","API workflow JSONを読込","匯入 API workflow JSON")}<input type="file" hidden accept="application/json,.json" onChange={e=>{const file=e.target.files?.[0];if(file)void importVideoWorkflow(file);e.currentTarget.value=""}}/></label></div>
        <div className="production-workflow-list">{videoWorkflows.map(workflow=><article key={workflow.id} className={workflow.builtin?"builtin":""}><div><strong>{workflow.id==="builtin"?t("H3 Studio · 内置已验证流程","H3 Studio · built-in tested workflow","H3 Studio・内蔵検証済みフロー","H3 Studio · 內置已驗證流程"):workflow.id==="h3_ref8_lora_accel"?t("8步 LoRA 加速","8-step LoRA acceleration","8ステップLoRA加速","8步 LoRA 加速"):workflow.name}</strong>{workflow.builtin&&<em>{workflow.id==="builtin"?t("默认 / 不替换","Default · unchanged","既定・変更なし","預設／不取代"):t("可选 / 不替换","Optional · unchanged original","任意・元のフローは維持","可選／不取代")}</em>}<span>{workflow.id==="builtin"?t("随 Prompt Studio 提供、保持不变的 H3/MMH3 稳定流程。","The unchanged, measured H3/MMH3 workflow shipped with Prompt Studio.","Prompt Studio付属の、変更されない検証済みH3/MMH3フローです。","Prompt Studio 隨附、保持不變的 H3/MMH3 穩定流程。"):workflow.id==="h3_ref8_lora_accel"?t("附件改造：8 步采样、LoRA 强度 0.5；提示词、参考图、声音、尺寸与输出目录仍由软件注入。","Adapted from the supplied workflow: 8 steps and LoRA 0.5; Studio still supplies prompt, references, audio, size and output folder.","添付フローを適用：8ステップ、LoRA 0.5。プロンプト・参照・音声・サイズ・出力先はStudioが設定します。","附件改造：8 步採樣、LoRA 強度 0.5；提示詞、參考圖、聲音、尺寸與輸出目錄仍由軟體注入。"):workflow.description}</span></div><div>{workflow.modes.map(mode=><code key={mode}>{mode}</code>)}{!workflow.builtin&&<button className="icon danger" onClick={()=>void deleteVideoWorkflow(workflow)} title={t("删除工作流","Delete workflow","ワークフローを削除","刪除工作流")}><Trash2 size={15}/></button>}</div></article>)}</div>
      </section>
      <section className="segment-list" id="production-storyboards" hidden={productionPage!=="storyboard"}>
        <div className="production-storyboard-head card"><div><span className="eyebrow">STORYBOARD CLIPS</span><h2><Clapperboard size={20}/>{t("当前集分镜制作","Current episode storyboard","現在話の絵コンテ制作","目前集分鏡製作")}</h2><p>{t("先按约 10 秒估算段数，再由剧情把每段调整到 5–15 秒，并让总生成时长贴合本集目标。AI 拆段后仍可人工增删、排序和编辑。","Count is estimated at roughly 10s per clip, then story needs vary each clip within 5–15s while total generated duration follows the episode target. You can still add, remove, reorder and edit after AI planning.","約10秒を基準に本数を見積もり、物語に応じて各クリップを5〜15秒で調整し、合計生成尺を話の目標に合わせます。AI計画後も追加・削除・並べ替え・編集が可能です。","先按約 10 秒估算段數，再由劇情把每段調整到 5–15 秒，並讓總生成時長貼合本集目標。AI 拆段後仍可人工增刪、排序和編輯。")}</p></div><div className="storyboard-head-actions"><span>{production.segments.length} {t("段","clips","件","段")} · {totalSeconds}/{currentEpisodeTiming.targetSeconds}s</span><button disabled={production.task_state==="paused"||!!busy||production.segments.length>=64} onClick={()=>addSegment()}><Plus size={16}/>{t("新增片段","Add clip","クリップを追加","新增片段")}</button></div></div>
        {!!production.segments.length&&<section className="storyboard-keyframe-planner card"><div className="section-title"><div><span className="eyebrow">AI KEYFRAME REVIEW</span><h3>{t("分镜关键帧建议与批量生图","Keyframe review & batch images","キーフレーム候補と一括生成","分鏡關鍵影格建議與批量生圖")}</h3><p>{t("本地 LLM 按剧情、动作、出场角色和画风判断哪些段需要补图。先审核提示词，确认后再逐张提交 ComfyUI；已有关键帧自动跳过。","The local LLM reviews story, action, cast and visual style. Edit its prompts before submitting images sequentially to ComfyUI. Clips with existing keyframes are skipped.","ローカルLLMが物語・動作・出演者・画風を確認します。プロンプトを編集後、ComfyUIへ順番に送信します。既存画像のあるクリップは省略します。","本地 LLM 按劇情、動作、出場角色和畫風判斷哪些段需要補圖。先審核提示詞，再逐張提交 ComfyUI；已有關鍵影格會跳過。")}</p></div><div className="production-actions"><button disabled={production.task_state==="paused"||!!busy} onClick={()=>void analyseKeyframes()}><Sparkles size={16}/>{t("AI 分析需要的关键帧","AI review keyframes","AIで画像候補を分析","AI 分析需要的關鍵影格")}</button><button className="primary" disabled={production.task_state==="paused"||!!busy||!keyframeSuggestions.length||!models.includes(imageModel)} onClick={()=>void generateSuggestedKeyframes()}><ImagePlus size={16}/>{t("批量生成建议图","Generate suggested images","候補画像を一括生成","批量生成建議圖")}</button></div></div>
          {keyframeSuggestions.map(item=><div className="storyboard-keyframe-suggestion" key={item.segment_id}><div><strong>{t("第 "+item.index+" 段","Clip "+item.index,"クリップ "+item.index,"第 "+item.index+" 段")}</strong><small>{item.reason}</small><button className="icon" title={t("删除此建议","Remove suggestion","候補を削除","刪除此建議")} onClick={()=>setKeyframeSuggestions(rows=>rows.filter(row=>row.segment_id!==item.segment_id))}><X size={16}/></button></div><textarea rows={3} value={item.prompt} onChange={e=>setKeyframeSuggestions(rows=>rows.map(row=>row.segment_id===item.segment_id?{...row,prompt:e.target.value}:row))}/></div>)}
        </section>}
        {!!production.segments.length&&<div className="storyboard-batch-bar card"><div className="storyboard-batch-copy"><span className="eyebrow">EPISODE AUTOMATION</span><strong>{t("整集制作控制台","Episode production console","エピソード制作コンソール","整集製作控制台")}</strong><span>{t("中断后可从缺失处续跑；“全部重做”才会建立新版本，旧提示词与视频仍然保留。","Interrupted runs resume from missing work. Only Rebuild all creates new versions; earlier prompts and video takes remain available.","中断後は不足分から再開できます。「すべて再作成」のみ新しい版を作り、以前のプロンプトと映像を保持します。","中斷後可從缺失處續跑；「全部重做」才會建立新版本，舊提示詞與影片仍然保留。")}</span><div className="storyboard-batch-progress"><span className={ready===production.segments.length?"complete":""}>{t("H3 工程","H3 projects","H3プロジェクト","H3 專案")} <b>{ready}/{production.segments.length}</b></span><span className={(outputs?.ready_count||0)===production.segments.length?"complete":""}>{t("视频","Videos","映像","影片")} <b>{outputs?.ready_count||0}/{production.segments.length}</b></span><span className={outputs?.final_ready?"complete":""}>{t("最终成片","Final film","最終映像","最終成片")} <b>{outputs?.final_ready?t("已完成","Ready","完成","已完成"):t("待合并","Pending","未結合","待合併")}</b></span></div></div>
          <button className="primary batch-all" disabled={production.task_state==="paused"||!!busy||(outputs?.active_jobs||0)>0} onClick={()=>void generateEpisodeFilm()}><Film size={18}/>{bulkAction==="all"?t("正在续跑本集…","Resuming this episode…","この話を再開中…","正在續跑本集…"):outputs?.final_ready?t("检查并补齐本集","Check episode","この話を確認","檢查並補齊本集"):(outputs?.ready_count||0)>=production.segments.length?t("合并本集成片","Assemble episode","この話を結合","合併本集成片"):(outputs?.ready_count||0)>0?t("继续完成本集 · 还差 "+Math.max(0,production.segments.length-(outputs?.ready_count||0))+" 段","Resume episode · "+Math.max(0,production.segments.length-(outputs?.ready_count||0))+" clips left","この話を続行・残り "+Math.max(0,production.segments.length-(outputs?.ready_count||0))+" 件","繼續完成本集 · 尚差 "+Math.max(0,production.segments.length-(outputs?.ready_count||0))+" 段"):t("一键完成本集","Complete this episode","この話を一括完成","一鍵完成本集")}</button>
          <div className="storyboard-batch-actions">
            <div className="batch-action-group batch-action-primary"><span>{t("补齐缺失","Fill missing","不足分を補完","補齊缺失")}</span><div>
              <button disabled={production.task_state==="paused"||!!busy||(outputs?.active_jobs||0)>0} onClick={()=>void generateAllPrompts()}><Sparkles size={16}/>{bulkAction==="prompts"?t("生成中…","Generating…","生成中…","生成中…"):t("提示词","Prompts","プロンプト","提示詞")}</button>
              <button disabled={production.task_state==="paused"||!!busy||(outputs?.active_jobs||0)>0} onClick={()=>void generateAllVideos()}><Play size={16}/>{bulkAction==="videos"?t("生成中…","Generating…","生成中…","生成中…"):t("视频","Videos","映像","影片")}</button>
              <button disabled={production.task_state==="paused"||!!busy} onClick={()=>void prepareMissing()}><Check size={16}/>{t("H3 工程","H3 projects","H3プロジェクト","H3 專案")}</button>
            </div></div>
            <div className="batch-action-group batch-action-maintenance"><span>{t("全部重做","Rebuild all","すべて再作成","全部重做")}</span><div>
              <button disabled={production.task_state==="paused"||!!busy||(outputs?.active_jobs||0)>0} onClick={()=>void generateAllPrompts(true)}><RefreshCw size={16}/>{bulkAction==="redo-prompts"?t("重做中…","Rebuilding…","再作成中…","重做中…"):t("提示词","Prompts","プロンプト","提示詞")}</button>
              <button disabled={production.task_state==="paused"||!!busy||(outputs?.active_jobs||0)>0} onClick={()=>void generateAllVideos(true)}><RefreshCw size={16}/>{bulkAction==="redo-videos"?t("重做中…","Rebuilding…","再作成中…","重做中…"):t("视频","Videos","映像","影片")}</button>
            </div></div>
          </div>
        </div>}
        {production.segments.map((segment,segmentPosition)=>{const isOpen=openSegmentId===segment.id;const outputRow=outputs?.segments.find(row=>row.segment_id===segment.id);const hasRender=!!outputRow?.candidates.length;const keyframes=segment.keyframe_asset_ids||[segment.image_asset_id].filter(Boolean) as string[];const effectiveWorkflowId=production.video_quality==="lora8"&&(segment.workflow_profile_id||"builtin")==="builtin"?"h3_ref8_lora_accel":segment.workflow_profile_id||"builtin";return <article className={"segment-card "+segment.status+(isOpen?" expanded":" collapsed")} key={segment.id}>
          <header><div className="segment-number">{String(segment.index).padStart(2,"0")}</div>
            <input className="segment-title" value={segment.title} onChange={e=>updateSegment(segment.id,"title",e.target.value)}/>
            <label className="duration"><input type="number" min={5} max={15} step={1} value={segment.duration}
              onChange={e=>updateSegment(segment.id,"duration",Math.max(5,Math.min(15,Number(e.target.value)||5)))}/><span>{t("秒","sec","秒","秒")}</span></label>
            <span className={"status "+(segment.status==="ready"&&!segment.video_prompt?.trim()?"missing-prompt":segment.status)}>{segment.status==="ready"?(segment.video_prompt?.trim()?t("已准备","Ready","準備済み","已準備"):t("缺提示词","Prompt missing","プロンプト未生成","缺提示詞")):segment.status==="stale"?t("需重建","Rebuild","再構築","需重建"):t("未准备","Not ready","未準備","未準備")}</span><div className="segment-row-tools">{!isOpen&&<div className="segment-quick-actions"><button disabled={production.task_state==="paused"||!!busy||(outputs?.active_jobs||0)>0} onClick={()=>void rebuildPrompt(segment,true)} title={t("重做本段提示词","Regenerate clip prompt","このプロンプトを再生成","重做本段提示詞")}><RefreshCw size={14}/>{t("提示词","Prompt","プロンプト","提示詞")}</button><button disabled={production.task_state==="paused"||!!busy||(outputs?.active_jobs||0)>0} onClick={()=>void generateVideo(segment)} title={t("重做本段视频","Regenerate clip video","この映像を再生成","重做本段影片")}><Play size={14}/>{t("视频","Video","映像","影片")}</button></div>}<div className="segment-structure-actions"><button className="icon" disabled={segmentPosition===0||production.task_state==="paused"} onClick={()=>moveSegment(segment.id,-1)} title={t("上移片段","Move clip up","上へ移動","上移片段")}><ArrowUp size={15}/></button><button className="icon" disabled={segmentPosition===production.segments.length-1||production.task_state==="paused"} onClick={()=>moveSegment(segment.id,1)} title={t("下移片段","Move clip down","下へ移動","下移片段")}><ArrowDown size={15}/></button><button className="icon danger" disabled={production.task_state==="paused"} onClick={()=>deleteSegment(segment)} title={t("删除片段","Delete clip","クリップを削除","刪除片段")}><Trash2 size={15}/></button><button className="segment-toggle icon" onClick={()=>setOpenSegmentId(isOpen?null:segment.id)} title={isOpen?t("收起分镜","Collapse clip","クリップを閉じる","收起分鏡"):t("编辑分镜","Edit clip","クリップを編集","編輯分鏡")}>{isOpen?<ChevronUp size={18}/>:<ChevronDown size={18}/>}</button></div></div></header>
          {isOpen&&<><p className="duration-reason">{segment.duration_reason}</p>
          {production.auto_continue_previous&&segment.index>1&&<div className="segment-card-selection"><strong>{t("上一段衔接","Previous clip link","前クリップとの接続","上一段銜接")}</strong><label><input type="checkbox" checked={segment.continue_previous!==false} onChange={e=>updateSegment(segment.id,"continue_previous",e.target.checked)}/> {t("使用上一段结尾的动作与声音","Carry preceding motion and audio","前の動きと音声を引き継ぐ","使用上一段結尾的動作與聲音")}</label><em>{t("换场或时间跳转时取消；此段将重新开镜。更改后需重做本段视频。","Uncheck for a location or time cut. This clip starts fresh; regenerate its video after changing.","場所や時間が飛ぶ場合はオフにします。このクリップは新しいショットになり、変更後は映像の再生成が必要です。","換場或時間跳轉時取消；此段會重新開鏡。更改後需重做本段影片。")}</em></div>}
          <div className={segment.card_selection_source==="local_ai"?"segment-card-selection ai":"segment-card-selection"}>
            <strong>{segment.card_selection_source==="local_ai"?t("本地 LLM 已选卡","Local LLM card selection","ローカルLLMのカード選択","本地 LLM 已選卡"):t("本地规则选卡","Local rule selection","ローカル規則による選択","本地規則選卡")}</strong>
            {segment.card_selection&&Object.entries(segment.card_selection).some(([,names])=>names.length)?
              <div>{(Object.entries(segment.card_selection) as [SelectableCardKind,string[]][]).flatMap(([kind,names])=>names.map(name=><span key={kind+name}>{selectionLabels[kind]} · {name}</span>))}</div>:
              <em>{t("本段没有指定额外资产卡；准备工程时会按分镜内容安全匹配。","No extra card is specified; safe matching is applied when the project is prepared.","追加カードの指定はありません。プロジェクト準備時に安全な照合を行います。","本段沒有指定額外資產卡；準備專案時會按分鏡內容安全匹配。")}</em>}
          </div>
          <div className="production-grid"><label>{t("场景地点","Setting","場所","場景地點")}<input value={segment.setting} onChange={e=>updateSegment(segment.id,"setting",e.target.value)}/></label>
            <label>{t("结束画面 / 衔接状态","Ending frame / continuity state","終了画面 / 継続状態","結束畫面 / 銜接狀態")}<input value={segment.ending} onChange={e=>updateSegment(segment.id,"ending",e.target.value)}/></label></div>
          <label>{t("本段故事事实","Story facts for this clip","このクリップの物語上の事実","本段故事事實")}<textarea rows={3} value={segment.story} onChange={e=>updateSegment(segment.id,"story",e.target.value)}/></label>
          <label>{t("可见动作","Visible action","画面に見える動作","可見動作")}<textarea rows={4} value={segment.action} onChange={e=>updateSegment(segment.id,"action",e.target.value)}/></label>
          <div className="dialogue-head"><strong>{t("精确对白","Exact dialogue","正確な台詞","精確對白")}</strong><button onClick={()=>addDialogue(segment.id)}><Plus size={15}/> {t("添加","Add","追加","新增")}</button></div>
          {segment.dialogue.map((line,i)=><div className="dialogue-row" key={i}>
            <input value={line.speaker} onChange={e=>updateDialogue(segment.id,i,"speaker",e.target.value)} placeholder={t("说话人","Speaker","話者","說話人")}/>
            <input value={line.text} onChange={e=>updateDialogue(segment.id,i,"text",e.target.value)} placeholder={t("逐字对白","Exact words","台詞原文","逐字對白")}/>
            <input value={line.language} onChange={e=>updateDialogue(segment.id,i,"language",e.target.value)} placeholder={t("语言","Language","言語","語言")}/>
            <label className="check"><input type="checkbox" checked={line.voiceover} onChange={e=>updateDialogue(segment.id,i,"voiceover",e.target.checked)}/>{t("画外音","Voiceover","画外音","畫外音")}</label>
            <button className="icon" onClick={()=>removeDialogue(segment.id,i)} aria-label={t("删除对白","Delete dialogue","台詞を削除","刪除對白")}><Trash2 size={16}/></button>
          </div>)}
          <section className="segment-keyframes"><div><strong><ImagePlus size={17}/>{t("本段专属关键帧","Clip-only keyframes","クリップ専用キーフレーム","本段專屬關鍵影格")}</strong><span>{keyframes.length}/12 · {t("只供本段规划，不进入共享卡库","this clip only; not the shared library","このクリップ専用・共有カード庫には追加しません","只供本段規劃，不進入共享卡庫")}</span></div>
            <label>{t("本地关键帧提示词","Local keyframe prompt","ローカルキーフレーム用プロンプト","本地關鍵影格提示詞")}<textarea rows={3} value={segment.image_prompt} onChange={e=>updateSegment(segment.id,"image_prompt",e.target.value)}/></label>
            {!!keyframes.length&&<div className="segment-keyframe-grid">{keyframes.map((assetId,index)=><div key={assetId}><img src={"/api/assets/"+assetId+"/thumbnail"} alt={t("片段关键帧 ","Clip keyframe ","クリップのキーフレーム ","片段關鍵影格 ")+(index+1)}/><button className="icon" onClick={()=>void removeSegmentKeyframe(segment,assetId)} title={t("从本段移除","Remove from this clip","このクリップから外す","從本段移除")}><X size={14}/></button></div>)}</div>}
            <div className="segment-keyframe-actions"><label className="production-upload-button"><Upload size={16}/>{t("上传本段其它关键帧","Upload another clip keyframe","別の専用キーフレームを追加","上傳本段其他關鍵影格")}<input type="file" hidden disabled={production.task_state==="paused"||keyframes.length>=12} accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp" onChange={e=>{const file=e.target.files?.[0];if(file)void uploadSegmentKeyframe(segment,file);e.currentTarget.value=""}}/></label><select value={imageModel} onChange={e=>setImageModel(e.target.value)} disabled={!models.length}>
              {!models.length&&<option>{t("未检测到本地生图模型","No local image model detected","ローカル画像モデルが見つかりません","未偵測到本地生圖模型")}</option>}{models.map(m=><option key={m}>{m}</option>)}</select>
              <button disabled={production.task_state==="paused"||keyframes.length>=12||!models.length||!segment.image_prompt.trim()} onClick={()=>void generateImage(segment)}><ImagePlus size={17}/> {t("本地生成关键帧","Generate keyframe locally","ローカルでキーフレーム生成","本地生成關鍵影格")}</button></div>
          </section>
          <section className="segment-prompt-workspace"><div className="segment-workspace-head"><div><strong><Sparkles size={17}/>{t("本段视频提示词","Clip video prompt","クリップ映像プロンプト","本段影片提示詞")}</strong><span>{t("先修改要求，再由本地 LLM 或编译器安全重建；最终文本与 H3 工程始终保持一致。","Edit the revision request, then safely rebuild with the local LLM or compiler. The final text stays in sync with the H3 project.","修正指示を編集し、ローカルLLMまたはコンパイラで安全に再構築します。最終テキストはH3プロジェクトと常に同期します。","先修改要求，再由本地 LLM 或編譯器安全重建；最終文字與 H3 專案始終保持一致。")}</span></div><em>{segment.video_prompt_source==="local_ai"?t("本地 LLM 生成","Local LLM","ローカルLLM","本地 LLM 生成"):segment.video_prompt?t("结构化编译","Structured compile","構造化コンパイル","結構化編譯"):t("尚未生成","Not built","未生成","尚未生成")}</em></div>
            <label>{t("本段 ComfyUI 视频工作流","ComfyUI video workflow for this clip","このクリップのComfyUI映像ワークフロー","本段 ComfyUI 影片工作流")}<select value={effectiveWorkflowId} onChange={e=>updateSegment(segment.id,"workflow_profile_id",e.target.value)}>
              {!videoWorkflows.some(workflow=>workflow.id===effectiveWorkflowId)&&<option value={effectiveWorkflowId}>{t("原工作流已被删除，请重新选择","Previous workflow is missing; choose another","以前のワークフローがありません。選び直してください","原工作流已被刪除，請重新選擇")}</option>}
              {videoWorkflows.map(workflow=><option key={workflow.id} value={workflow.id} disabled={!workflow.modes.includes(production.source_mode||"ref2va")||(production.video_quality==="lora8"&&workflow.id==="builtin")}>{workflow.id==="h3_ref8_lora_accel"?t("8步 LoRA 加速","8-step LoRA acceleration","8ステップLoRA加速","8步 LoRA 加速"):workflow.name}{workflow.modes.includes(production.source_mode||"ref2va")?"":t(" · 不支持当前输入模式"," · incompatible mode","・現在の入力モード非対応"," · 不支援目前輸入模式")}</option>)}</select><small>{t("每段可独立选择。更换后会标记工程需重建；内置流程始终保留。","Choose independently per clip. Changing it marks the H3 project for rebuild; the built-in workflow is always retained.","クリップごとに選択できます。変更後はH3プロジェクトの再構築が必要になり、内蔵フローは常に残ります。","每段可獨立選擇。更換後會標記專案需重建；內置流程始終保留。")}</small></label>
            <label>{t("视频提示词修改要求","Video prompt revision request","映像プロンプトの修正指示","影片提示詞修改要求")}<textarea rows={3} value={segment.prompt_direction||""} onChange={e=>updateSegment(segment.id,"prompt_direction",e.target.value)} placeholder={t("例如：镜头保持稳定，先给女生反应，再让男生把书递过去；不要新增对白。","Example: keep the camera steady, show her reaction first, then let him pass the book; add no dialogue.","例：カメラを安定させ、先に彼女の反応、その後で彼が本を渡す。台詞は追加しない。","例如：鏡頭保持穩定，先給女生反應，再讓男生把書遞過去；不要新增對白。")}/></label>
            {segment.video_prompt?<label>{t("最终 H3 视频提示词（同步预览）","Final H3 video prompt · synced preview","最終H3映像プロンプト・同期プレビュー","最終 H3 影片提示詞（同步預覽）")}<textarea className="compiled-prompt-preview" rows={7} readOnly value={segment.video_prompt}/></label>:<p className="segment-prompt-empty">{t("H3 工程已经建立，但本段尚未生成视频提示词。点击下方“生成视频提示词”即可补齐。","The H3 project exists, but this clip has no video prompt yet. Use Generate video prompt below to build it.","H3プロジェクトはありますが、このクリップの映像プロンプトは未生成です。下の「映像プロンプトを生成」で作成できます。","H3 專案已建立，但本段尚未生成影片提示詞。點擊下方「生成影片提示詞」即可補齊。")}</p>}
            <div className="segment-prompt-actions"><button className="primary" disabled={production.task_state==="paused"||!!busy||(outputs?.active_jobs||0)>0} onClick={()=>void rebuildPrompt(segment,true)}><Sparkles size={16}/>{segment.video_prompt?t("重新生成本段提示词","Regenerate this clip prompt","このプロンプトを再生成","重新生成本段提示詞"):t("生成视频提示词","Generate video prompt","映像プロンプトを生成","生成影片提示詞")}</button><button disabled={production.task_state==="paused"||!!busy||(outputs?.active_jobs||0)>0} onClick={()=>void rebuildPrompt(segment,false)}><RefreshCw size={16}/>{t("不用 AI 重建","Rebuild without AI","AIなしで再構築","不用 AI 重建")}</button><span><Clock3 size={14}/>{t("提示词","Prompt","プロンプト","提示詞")} {formatSeconds(segment.prompt_seconds)} · H3 {formatSeconds(segment.prepare_seconds)}</span></div>
          </section>
          <footer><button disabled={production.task_state==="paused"} onClick={()=>addSegment(segmentPosition)}><Plus size={17}/>{t("在后面插入片段","Insert clip after","後ろにクリップを挿入","在後面插入片段")}</button><button disabled={production.task_state==="paused"&&!segment.project_id} onClick={()=>void prepareOne(segment,true)}><Clapperboard size={17}/>{segment.project_id?t("更新并打开 H3","Update and open H3","H3を更新して開く","更新並開啟 H3"):t("创建并打开 H3","Create and open H3","H3を作成して開く","建立並開啟 H3")}</button>
            <button disabled={production.task_state==="paused"} onClick={()=>void prepareOne(segment,false)}><Save size={17}/> {t("只准备工程","Prepare only","準備のみ","只準備專案")}</button>
            <button className="primary" disabled={production.task_state==="paused"||!!busy||(outputs?.active_jobs||0)>0} onClick={()=>void generateVideo(segment)}><Play size={17}/>{hasRender?t("重新生成本段视频","Regenerate this clip","このクリップを再生成","重新生成本段影片"):t("生成本段视频","Generate this clip","このクリップを生成","生成本段影片")}</button></footer>
          {!!segment.stale_reasons?.length&&<p className="stale-note">{segment.stale_reasons.join("；")}</p>}</>}
        </article>})}
      </section>
      <section className="production-results card" id="production-video-overview" hidden={productionPage!=="output"}>
        <div className="section-title production-results-head"><div><span className="eyebrow">PROJECT VIDEO OVERVIEW</span><h2><Video size={20}/> {t("项目视频一览与输出","Project videos and output","プロジェクト映像と出力","專案影片一覽與輸出")}</h2>
          <p>{t("每个分镜默认采用最新成功版本，也可手动指定。只有采用版本齐全后才会按分镜顺序合成最终影片。","Each clip adopts its latest successful take by default, or you can choose one manually. The final film is assembled in storyboard order only after every adopted take is ready.","各クリップは最新の成功テイクを既定で採用し、手動選択も可能です。全採用テイクが揃うと絵コンテ順に最終映像を結合します。","每個分鏡預設採用最新成功版本，也可手動指定。只有採用版本齊全後才會按分鏡順序合成最終影片。")}</p></div>
          <div className="production-result-actions"><span className="auto-merge-status">{production.auto_merge!==false?t("自动合片已开启","Auto-assembly on","自動結合オン","自動合片已開啟"):t("自动合片已关闭","Auto-assembly off","自動結合オフ","自動合片已關閉")}</span>
            <button onClick={()=>void refreshOutputs()} disabled={outputsLoading}><RefreshCw className={outputsLoading?"spin":""} size={16}/>{t("刷新结果","Refresh results","結果を更新","重新整理結果")}</button>
            <button className="primary" disabled={production.task_state==="paused"||!outputs?.all_ready||merging} onClick={()=>void buildFilm(false)}><Film size={17}/>{merging?t("正在合片…","Assembling…","結合中…","正在合片…"):t("立即合并最终成片","Assemble final film","最終映像を結合","立即合併最終成片")}</button></div></div>
        {!outputs?<div className="production-output-empty">{t("正在读取本项目的视频结果…","Reading this production's video results…","この制作の映像結果を読み込み中…","正在讀取本專案的影片結果…")}</div>:<>
          <div className="production-output-summary"><strong>{outputs.ready_count}/{outputs.segment_count}</strong><span>{t("个分镜已有采用版本","clips have adopted takes","クリップに採用テイクあり","個分鏡已有採用版本")}</span><strong>{outputs.estimated_seconds}s</strong><span>{t("预计成片时长（已扣续写重叠）","estimated film length after continuation overlap","継続重複を除いた推定尺","預計成片時長（已扣續寫重疊）")}</span>{outputs.active_jobs>0&&<><strong>{outputs.active_jobs}</strong><span>{t("个视频任务正在处理","video job in progress","件の映像処理中","個影片任務正在處理")}</span></>}</div>
          <div className="production-output-list">{outputs.segments.map(row=>{const readyTakes=row.candidates.filter(job=>job.status==="succeeded"&&job.video_url);const selected=row.selected;const activeJob=row.candidates.find(job=>["preparing","queued","running","uncertain"].includes(job.status));const segment=production.segments.find(item=>item.id===row.segment_id);return <article className={selected?"production-output-row ready":"production-output-row"} key={row.segment_id}>
            <div className="production-output-index">{String(row.index).padStart(2,"0")}</div>
            <div className="production-output-media">{selected?<video controls playsInline preload="metadata" src={selected.scene_video_url||selected.video_url||""}/>:<div className="production-video-placeholder"><Video size={24}/><span>{row.project_id?t("尚无完成的视频","No completed video yet","完成した映像はまだありません","尚無完成的影片"):t("先创建 H3 工程","Create the H3 project first","先にH3プロジェクトを作成","先建立 H3 專案")}</span></div>}</div>
            <div className="production-output-info"><strong>{row.title}</strong><span>{selected?t("采用版本","Adopted take","採用テイク","採用版本")+" · "+(selected.seed!=null?"Seed "+selected.seed:t("已完成","Ready","完成","已完成"))+" · "+formatSeconds(selected.elapsed_seconds):t("等待本段生成完成","Waiting for this clip","このクリップの生成待ち","等待本段生成完成")}</span>
              <div className="production-output-dialogue"><b>{t("本段对白 · 对照视频听","Clip dialogue · check against the video","このクリップの台詞・映像と照合","本段對白 · 對照影片聽")}</b>{segment?.dialogue?.length?segment.dialogue.map((line,index)=><p key={index}><strong>{line.speaker||t("未署名","Unknown speaker","話者未設定","未署名")}{line.voiceover?" · "+t("画外音","off-screen","画面外","畫外音"):""}</strong><span>{line.text}</span></p>):<small>{t("本段无对白；检查动作、音效和画面连续性。","No dialogue in this clip; check action, sound and visual continuity.","このクリップに台詞はありません。動作・音・映像の連続性を確認してください。","本段無對白；檢查動作、音效和畫面連續性。")}</small>}</div>
              {(activeJob?.output_folder||selected?.output_folder)&&<code className="production-output-folder">ComfyUI/output/{activeJob?.output_folder||selected?.output_folder}</code>}
              {activeJob&&<div className="production-active-job"><LoaderCircle className="spin" size={15}/><span><b>{activeJob.stage||activeJob.status}</b> · {formatSeconds(activeJob.elapsed_seconds)}</span><button onClick={()=>void stopVideo(activeJob)}><Square size={13}/>{t("停止","Stop","停止","停止")}</button></div>}
              <label>{t("本段采用版本","Adopted take for this clip","このクリップの採用テイク","本段採用版本")}<select value={selected?.id||""} disabled={!readyTakes.length} onChange={e=>void selectVideo(row,e.target.value)}>
                {!readyTakes.length&&<option value="">{t("暂无成功版本","No successful take","成功テイクなし","暫無成功版本")}</option>}{readyTakes.map((job,i)=><option key={job.id} value={job.id}>{t("版本","Take","テイク","版本")} {readyTakes.length-i}{job.seed!=null?" · Seed "+job.seed:""}{row.selection==="latest"&&job.id===selected?.id?t(" · 最新自动采用"," · latest auto choice"," · 最新を自動採用"," · 最新自動採用"):""}</option>)}</select></label>
              <div><span>{row.candidates.length} {t("个生成记录","render records","件の生成履歴","個生成記錄")}</span><span className="production-row-actions">{segment&&<><button disabled={production.task_state==="paused"||!!busy||outputs.active_jobs>0} onClick={()=>void rebuildPrompt(segment,true)}><Sparkles size={14}/>{t("重做提示词","Regenerate prompt","プロンプトを再生成","重做提示詞")}</button><button disabled={production.task_state==="paused"||!!busy||outputs.active_jobs>0} onClick={()=>void generateVideo(segment)}><RefreshCw size={14}/>{t("重做视频","Regenerate video","映像を再生成","重做影片")}</button></>}{row.project_id&&<button onClick={async()=>{await onOpenProject(row.project_id!);onStudio();}}>{t("打开本段 H3","Open clip in H3","このクリップをH3で開く","開啟本段 H3")}</button>}</span></div></div>
          </article>})}</div>
          <div className={outputs.final_ready?"production-final ready":"production-final"}><div><span className="eyebrow">FINAL FILM</span><h3>{t("项目最终成片","Final production film","プロジェクト最終映像","專案最終成片")}</h3>
            <p>{outputs.final_ready?t("已按分镜顺序完成；若更换任一采用版本，会生成新的成片。","Assembled in storyboard order. Changing any adopted take creates a new film.","絵コンテ順に完成しました。採用テイクを変更すると新しい映像を作成します。","已按分鏡順序完成；若更換任一採用版本，會生成新的成片。"):outputs.all_ready?t("采用版本已齐全，可以开始合片。","All adopted takes are ready for assembly.","採用テイクが揃い、結合できます。","採用版本已齊全，可以開始合片。"):t("缺少的分镜会保留为空，不会拿错误项目的视频补位。","Missing clips stay empty; videos from another project are never substituted.","不足クリップは空欄のままにし、別プロジェクトの映像で代用しません。","缺少的分鏡會保留為空，不會拿錯誤專案的影片補位。")}</p></div>
            {outputs.final_ready&&outputs.final_url&&<div className="production-final-player"><video controls playsInline preload="metadata" src={outputs.final_url}/><div className="production-film-location"><strong>{t("已自动保存在本机，无需再下载","Saved locally; no download required","ローカルに保存済み・ダウンロード不要","已自動儲存在本機，無需再下載")}</strong><code title={outputs.file_path||""}>{outputs.file_path}</code><div className="production-actions"><button onClick={()=>void openProductionFilmFolder()} disabled={!!busy}><FolderOpen size={16}/>{t("打开文件位置","Open file location","保存場所を開く","開啟檔案位置")}</button>{outputs.download_url&&<a href={outputs.download_url}><Download size={16}/>{t("另存副本","Download a copy","コピーを保存","另存副本")}</a>}</div></div></div>}</div>
        </>}
      </section>
    </>}
  </main>;
}
