import React, { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Check,
  ChevronDown,
  Copy,
  Download,
  FolderOpen,
  ImagePlus,
  LoaderCircle,
  MessageSquare,
  Plus,
  Settings2,
  Sparkles,
  Trash2,
  Undo2,
  Upload,
  UserRound,
  Video,
  X,
} from "lucide-react";
import { Asset, Project, Subject, retime, uid } from "./model";
import {
  getPeople,
  getAssetPerson,
  setAssetType,
  setAssetPerson,
  renamePerson,
  removeSimpleAsset,
  setSimpleMode,
  setKeyframe,
  setSimpleSceneCount,
  setPersonAction as setAction,
} from "./simple";
import "./SimpleStudio.css";
import SceneDirector from './SceneDirector';
import TemplateShelf from './TemplateShelf';
import PhotoTools, { ReferenceInsert } from './PhotoTools';
import IdeaBuilder from './IdeaBuilder';
import { TimelinePlanner } from './TimelinePlanner';
import { ensurePromptTags } from './tags';
import { useUiLanguage } from './i18n';

export type SimpleStudioProps = {
  project: Project;
  update: (fn: (draft: Project) => void) => void;
  checkpointUpdate: (fn: (draft: Project) => void) => void;
  onRestore: (project:Project) => void;
  onReplacePhoto: (id:string,file:File) => Promise<void>;
  onAddFiles: (files: File[]) => Promise<void>;
  onGenerate: () => void;
  onBuild: () => void;
  busy: string;
  renderBusy?: boolean;
  progress: string;
  error: string;
  notice: string;
  result: any | null;
  currentPrompt: string;
  resultFresh: boolean;
  referenceMap: {asset_id:string;token:string;name:string;role:string}[];
  onCopy: () => void;
  onSave: () => void;
  onAdvanced: () => void;
  onProjects: () => void;
  onNew: () => void;
  onConnections: () => void;
  onFiles: () => void;
  onUndo: () => void;
  canUndo: boolean;
  connectionOnline: boolean;
  onSendToComfy: () => void;
  canReturn: boolean;
  onContinue: (next:Project)=>void;
  modelPicker?: React.ReactNode;
  comfyPanel?: React.ReactNode;
  settingsPanel?: React.ReactNode;
};

function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <label className="simple-field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}

export default function SimpleStudio(props: SimpleStudioProps) {
  const { project: p, update } = props;
  const {text:uiText}=useUiLanguage();
  const t=(zh:string,en:string,ja:string,tw=zh)=>uiText({"zh-CN":zh,"zh-TW":tw,en,ja});
  const photoTypes = [
    ["other", t("选择类型…","Choose a type…","種類を選択…","選擇類型…")],
    ["face", t("人物 · 面部 / 身份","Person · face / identity","人物・顔 / 識別","人物 · 臉部 / 身分")],
    ["character", t("人物 · 整体造型","Person · whole look","人物・全身ルック","人物 · 整體造型")],
    ["wardrobe", t("服装","Clothes","衣装","服裝")],
    ["object", t("道具","Object","小道具","道具")],
    ["background", t("环境","Place","場所","環境")],
    ["style", t("风格","Style","スタイル","風格")],
    ["palette", t("色彩","Colors","色彩","色彩")],
    ["pose", t("姿势 / 构图","Pose / composition","ポーズ / 構図","姿勢 / 構圖")],
  ];
  const inputRef = useRef<HTMLInputElement>(null);
  const storyRef = useRef<HTMLTextAreaElement>(null);
  const actionRefs = useRef<Record<string,HTMLTextAreaElement|null>>({});
  const [dragging, setDragging] = useState(false);
  const [preview, setPreview] = useState<Asset | null>(null);
  const [uploadError, setUploadError] = useState("");
  const [editorTab, setEditorTab] = useState<'photos' | 'story' | 'settings'>(p.assets.length ? 'story' : 'photos');
  const [showScenes, setShowScenes] = useState(
    p.shots.length > 1 || p.shots.some((s) => s.dialogue.length > 0),
  );
  useEffect(() => {
    setShowScenes(p.shots.length > 1 || p.shots.some((s) => s.dialogue.length > 0));
    setPreview(null);
  }, [p.id]);
  useEffect(()=>{if(p.shots.length>1)setShowScenes(true);},[p.shots.length]);
  const images = p.assets.filter((a) => a.media_type === "image");
  const activeImages = images.filter((a) => a.enabled);
  const people = getPeople(p);
  const videoImages = activeImages.filter((a) => a.role !== "context");
  const inspirationImages = activeImages.filter((a) => a.role === "context");
  const unavailable = !!props.busy;
  const editAsset = (id: string, values: Partial<Asset>) =>
    update((d) => {
      const a = d.assets.find((a) => a.id === id);
      if (a) Object.assign(a, values);
    });
  const editShot = (id: string, fn: (s: Project["shots"][number]) => void) =>
    update((d) => {
      const shot = d.shots.find((s) => s.id === id);
      if (shot) fn(shot);
    });
  const addFiles = async (files: File[]) => {
    setUploadError("");
    if (!files.length) return;
    try {
      await props.onAddFiles(files);
    } catch (e: any) {
      setUploadError(
        e.message || "Those photos could not be added. Please try again.",
      );
    }
  };

  const ownerOf = (a: Asset) => getAssetPerson(p, a.id);
  const typeChanged = (a: Asset, role: string) =>
    update((d) => setAssetType(d, a.id, role));
  const assignTo = (a: Asset, personId: string) =>
    update((d) => setAssetPerson(d, a.id, personId));
  const personAction = (person: Subject) =>
    p.simple?.person_actions?.[person.id] || "";
  const setPersonAction = (person: Subject, text: string) =>
    update((d) => setAction(d, person.id, text));
  const insertReference = (tag:string, shotId?:string) => {
    const input=shotId?actionRefs.current[shotId]:storyRef.current;
    const source=shotId?p.shots.find(s=>s.id===shotId)?.action||'':p.story.text;
    const start=input?.selectionStart ?? source.length,end=input?.selectionEnd ?? start;
    const added=(start>0&&!/\s/.test(source[start-1])?' ':'')+tag+' ';
    props.checkpointUpdate(d=>{ensurePromptTags(d);const value=source.slice(0,start)+added+source.slice(end);if(shotId){const s=d.shots.find(s=>s.id===shotId);if(s)s.action=value;}else d.story.text=value;});
    requestAnimationFrame(()=>{input?.focus();input?.setSelectionRange(start+added.length,start+added.length);});
  };

  return (
    <div className="simple-studio">
      <header className="simple-header">
        <div className="simple-brand">
          <span className="simple-brand-mark">
            <Video size={19} />
          </span>
          <div>
            <strong>H3 Prompt Studio</strong>
            <span>{t("简易模式","Simple mode","シンプルモード","簡易模式")}</span>
          </div>
        </div>
        <nav aria-label={t("项目工具","Project tools","プロジェクトツール","專案工具")}>
          {props.canUndo&&<button onClick={props.onUndo} disabled={unavailable}><Undo2 size={15}/> {t("撤销上次修改","Undo last change","直前の変更を元に戻す","復原上次修改")}</button>}
          <button onClick={props.onNew} disabled={unavailable}>
            <Plus size={15} /> {t("新建","New","新規","新建")}
          </button>
          <button onClick={props.onProjects} disabled={unavailable}>
            <FolderOpen size={15} /> {t("已保存项目","Saved projects","保存済みプロジェクト","已儲存專案")}
          </button>
          <button onClick={props.onFiles}>
            <Download size={15} /> {t("输出","Outputs","出力","輸出")}
          </button>
          <button onClick={() => document.querySelector('.video-workspace')?.scrollIntoView({behavior:'smooth',block:'start'})}>
            <Video size={15} /> {t("视频","Video","動画","影片")}
          </button>
          <button className="simple-quiet" onClick={props.onAdvanced}>
            <Settings2 size={15} /> {t("高级","Advanced","詳細","進階")}
          </button>
        </nav>
      </header>

      <main className="simple-main">
        <div className="simple-intro">
          <div>
            <p className="simple-eyebrow">{t("你的图片，你的创意","YOUR IMAGES. YOUR IDEA.","あなたの画像、あなたのアイデア","你的圖片，你的創意")}</p>
            <h1>{t("制作你的镜头。","Make your scene.","シーンを作ろう。","製作你的鏡頭。")}</h1>
            <p>
              {t("添加照片，说明谁做什么，然后在这里生成视频。","Add photos, say who does what, and generate your video here.","写真を追加し、誰が何をするかを指定して、ここで動画を生成します。","加入照片，說明誰做什麼，然後在這裡生成影片。")}
            </p>
          </div>
          <button
            className={
              "simple-connection " + (props.connectionOnline ? "is-online" : "")
            }
            onClick={props.onConnections}
          >
            <span />
            {props.connectionOnline ? t("本地 AI 已连接","Local AI connected","ローカルAI接続済み","本地 AI 已連線") : t("连接本地 AI","Connect local AI","ローカルAIに接続","連線本地 AI")}
            <ChevronDown size={14} />
          </button>
        </div>

        <div className="simple-project-line">
          <label htmlFor="simple-project-title">{t("项目","Project","プロジェクト","專案")}</label>
          <input
            id="simple-project-title"
            aria-label={t("项目名称","Project name","プロジェクト名","專案名稱")}
            value={p.title}
            onChange={(e) =>
              update((d) => {
                d.title = e.target.value;
              })
            }
            disabled={unavailable}
          />
          <span>{t("自动保存","Saved automatically","自動保存","自動儲存")}</span>
        </div>

        <div className="simple-workspace-layout">
        <div className="simple-editor">
        <div className="simple-editor-tabs" role="tablist" aria-label={t("镜头编辑器","Scene editor","シーンエディター","鏡頭編輯器")}>
          {([['photos', t("照片","Photos","写真","照片")], ['story', t("故事与对白","Story & Dialogue","物語と台詞","故事與對白")], ['settings', t("设置","Settings","設定","設定")]] as const).map(([tab, label], index) =>
            <button key={tab} id={`simple-tab-${tab}`} type="button" role="tab" aria-selected={editorTab === tab}
              aria-controls={`simple-panel-${tab}`} tabIndex={editorTab === tab ? 0 : -1}
              onClick={() => setEditorTab(tab)} onKeyDown={event => {
                const offset = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
                if (!offset && event.key !== 'Home' && event.key !== 'End') return;
                event.preventDefault();
                const next = (['photos', 'story', 'settings'] as const)[event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (index + offset + 3) % 3];
                setEditorTab(next); document.getElementById(`simple-tab-${next}`)?.focus();
              }}>{label}</button>)}
        </div>
        <section role="tabpanel" id="simple-panel-photos" hidden={editorTab !== 'photos'} aria-labelledby="simple-tab-photos"
          className="simple-section"
        >
          <div className="simple-section-heading">
            <span className="simple-step">1</span>
            <div>
              <h2 id="simple-photos-title">{t("添加照片","Add your photos","写真を追加","加入照片")}</h2>
              <p>
                {t("说明每张照片的用途，并把服装和道具分配给正确的角色。","Tell us what each photo is. Assign clothes and objects to the right person.","各写真の用途を指定し、衣装や小道具を正しい人物に割り当てます。","說明每張照片的用途，並把服裝和道具分配給正確的角色。")}
              </p>
            </div>
            <span className="simple-count">{inspirationImages.length
              ? t(`${videoImages.length} 张视频参考 · ${inspirationImages.length} 张灵感图`,`${videoImages.length} video reference${videoImages.length===1?'':'s'} · ${inspirationImages.length} inspiration`,`${videoImages.length}枚の映像参照・${inspirationImages.length}枚の参考画像`,`${videoImages.length} 張影片參考 · ${inspirationImages.length} 張靈感圖`)
              : t(`正在使用 ${videoImages.length} 张图片`,`${videoImages.length} photo${videoImages.length===1?'':'s'} in use`,`${videoImages.length}枚の画像を使用中`,`正在使用 ${videoImages.length} 張圖片`)}</span>
          </div>
          <div
            className={"simple-dropzone " + (dragging ? "is-dragging" : "")}
            onDragOver={(e) => {
              e.preventDefault();
              if (!unavailable) setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDragging(false);
              if (!unavailable) void addFiles(Array.from(e.dataTransfer.files));
            }}
          >
            <div>
              <ImagePlus size={25} />
              <span>
                <strong>{t("添加角色、服装、道具或环境","Add people, clothes, objects or a place","人物・衣装・小道具・場所を追加","新增角色、服裝、道具或環境")}</strong>
                <small>{t("可一次选择多张图片，也可以拖到这里。","Choose several photos at once, or drop them here.","複数の画像を選ぶか、ここへドロップできます。","可一次選擇多張圖片，也可以拖到這裡。")}</small>
              </span>
            </div>
            <button
              className="simple-secondary"
              disabled={unavailable}
              onClick={() => inputRef.current?.click()}
            >
              <Upload size={16} /> {t("添加图片","Add photos","画像を追加","新增圖片")}
            </button>
            <input
              ref={inputRef}
              type="file"
              accept="image/*"
              multiple
              hidden
              aria-label={t("上传参考图","Upload reference photos","参照画像を追加","上傳參考圖")}
              onChange={(e) => {
                void addFiles(Array.from(e.target.files || []));
                e.target.value = "";
              }}
            />
          </div>
          {uploadError && (
            <p className="simple-error" role="alert">
              {uploadError}
            </p>
          )}

          {p.mode === "ref2va" &&
            activeImages.some((a) =>
              ["first_frame", "last_frame"].includes(a.role),
            ) && (
              <div className="simple-mode-choice">
                <p>{t("选择这些图片在视频中的用途。","Choose how to use these photos.","画像を映像でどう使うか選択します。","選擇這些圖片在影片中的用途。")}</p>
                <div>
                  <button
                    disabled={unavailable}
                    onClick={() => update((d) => setSimpleMode(d, "ref2va"))}
                  >
                    {t("全部作为参考图","Use all as reference photos","すべて参照画像として使う","全部作為參考圖")}
                  </button>
                  {activeImages.some((a) => a.role === "first_frame") && (
                    <button
                      disabled={unavailable}
                      onClick={() => update((d) => setSimpleMode(d, "i2va"))}
                    >
                      {t("只使用首帧","Use first frame only","開始フレームだけ使う","只使用首幀")}
                    </button>
                  )}
                </div>
              </div>
            )}
          {!!images.length && (
            <div className="simple-photo-grid">
              {images.map((a, index) => {
                const owner = ownerOf(a);
                const isPerson = ["face", "character"].includes(
                  a.semantic_role,
                );
                const isClothes = a.semantic_role === "wardrobe";
                const isObject = a.semantic_role === "object";
                return (
                  <article
                    className={
                      "simple-photo " + (!a.enabled ? "is-excluded" : "")
                    }
                    key={a.id}
                  >
                    <button
                      className="simple-photo-preview"
                      aria-label={t(`查看图片 ${index+1}：${a.name}`,`View photo ${index+1}: ${a.name}`,`画像${index+1}を表示：${a.name}`,`查看圖片 ${index+1}：${a.name}`)}
                      onClick={() => setPreview(a)}
                    >
                      <img
                        src={`/api/assets/${a.id}/thumbnail`}
                        alt={a.name}
                        loading="lazy"
                      />
                      <span>{t("图片","Photo","画像","圖片")} {index + 1}</span>
                    </button>
                    <fieldset
                      className="simple-photo-fields"
                      disabled={unavailable}
                    >
                      <Field label={t("这是什么图片？","What is this photo?","この画像は何ですか？","這是什麼圖片？")}>
                        <select
                          aria-label={`Photo ${index + 1} type`}
                          value={a.semantic_role}
                          onChange={(e) => typeChanged(a, e.target.value)}
                        >
                          {photoTypes.map(([value, label]) => (
                            <option key={value} value={value}>
                              {label}
                            </option>
                          ))}
                        </select>
                      </Field>
                      {isPerson && (
                        <>
                          <Field label={t("这是哪个角色？","Which person?","どの人物ですか？","這是哪個角色？")}>
                            <select
                              aria-label={`Photo ${index + 1} person`}
                              value={owner?.id || ""}
                              onChange={(e) => assignTo(a, e.target.value)}
                            >
                              <option value="" disabled>
                                {t("选择角色…","Choose a person…","人物を選択…","選擇角色…")}
                              </option>
                              {people.map((person) => (
                                <option key={person.id} value={person.id}>
                                  {person.name || t("未命名角色","Unnamed person","名前のない人物","未命名角色")}
                                </option>
                              ))}
                              <option value="new">{t("+ 新角色","+ A different person","+ 別の人物","+ 新角色")}</option>
                            </select>
                          </Field>
                          <Field label={t("角色名称","Person's name","人物名","角色名稱")}>
                            <input
                              aria-label={`Photo ${index + 1} person name`}
                              value={owner?.name || ""}
                              placeholder={t("例如：米拉","e.g. Mira","例：ミラ","例如：米拉")}
                              onChange={(e) => {
                                const name = e.target.value;
                                update((d) => {
                                  if (!getAssetPerson(d, a.id))
                                    setAssetPerson(d, a.id, "new");
                                  const person = getAssetPerson(d, a.id);
                                  if (person) renamePerson(d, person.id, name);
                                });
                              }}
                            />
                          </Field>
                        </>
                      )}
                      {(isClothes ||
                        isObject ||
                        a.semantic_role === "pose") && (
                        <Field
                          label={
                            isClothes
                              ? t("穿着者","Worn by","着用する人物","穿著者")
                              : isObject
                                ? t("起始持有者","Starts with","最初の所有者","起始持有者")
                                : t("姿势适用于","Pose for","ポーズ対象","姿勢適用於")
                          }
                          hint={
                            isClothes && !people.length
                              ? t("请先添加人物图片并为角色命名。","Add a person photo and name them first.","先に人物画像を追加して名前を付けてください。","請先新增人物圖片並為角色命名。")
                              : undefined
                          }
                        >
                          <select
                            aria-label={`Photo ${index + 1} ${isClothes ? "worn by" : isObject ? "starts with" : "pose for"}`}
                            value={owner?.id || ""}
                            onChange={(e) => assignTo(a, e.target.value)}
                          >
                            <option value="">
                              {isClothes
                                ? t("选择角色…","Choose a person…","人物を選択…","選擇角色…")
                                : isObject
                                  ? t("场景中 · 无持有者","In the scene · no owner","シーン内・所有者なし","場景中 · 無持有者")
                                  : t("整个场景","Whole scene","シーン全体","整個場景")}
                            </option>
                            {people.map((person) => (
                              <option key={person.id} value={person.id}>
                                {person.name || t("未命名角色","Unnamed person","名前のない人物","未命名角色")}
                              </option>
                            ))}
                          </select>
                        </Field>
                      )}
                      {a.semantic_role === "background" && (
                        <p className="simple-photo-hint">
                          {t("用于确定地点，不会额外添加人物。","Sets the place. It does not add extra people.","場所を設定します。人物は追加しません。","用於確定地點，不會額外新增人物。")}
                        </p>
                      )}
                      {["style", "palette"].includes(a.semantic_role) && (
                        <p className="simple-photo-hint">
                          {t("只借用画风和色彩，不会借用其中的人物或物体。","Borrows the look and colors, not the people or objects.","画風と色だけを参照し、人物や物体は借用しません。","只借用畫風和色彩，不會借用其中的人物或物體。")}
                        </p>
                      )}
                      {isPerson && (
                        <p className="simple-photo-hint">
                          {a.semantic_role === "face"
                            ? t("保留这张脸；服装请单独指定。","Keeps this face. Assign an outfit separately below.","この顔を維持し、衣装は別に指定します。","保留這張臉；服裝請單獨指定。")
                            : t("保留这个角色及其整体造型。","Keeps this person and their overall look.","この人物と全体のルックを維持します。","保留這個角色及其整體造型。")}
                        </p>
                      )}
                      <PhotoTools project={p} asset={a} index={index} update={update} onReplace={props.onReplacePhoto}/>
                      <details className="simple-photo-notes">
                        <summary>{t("图片备注","Photo notes","画像メモ","圖片備註")}</summary>
                        <Field label={t("需要保留或忽略什么？","Anything to keep or ignore?","維持または無視する要素","需要保留或忽略什麼？")}>
                          <textarea
                            rows={2}
                            value={a.description || ""}
                            placeholder={t("例如：只使用红裙，忽略人体模特","e.g. use the red dress only, ignore the mannequin","例：赤いドレスだけを使い、マネキンは無視","例如：只使用紅裙，忽略人體模特")}
                            onChange={(e) =>
                              editAsset(a.id, { description: e.target.value })
                            }
                          />
                        </Field>
                        {(a.approved_observation || a.observation) && (
                          <Field label={t("提示词使用的图片描述","Description used in the prompt","プロンプトで使う画像説明","提示詞使用的圖片描述")}>
                            <textarea
                              rows={3}
                              value={a.approved_observation || ""}
                              placeholder={t("AI 将描述这张图片。","AI will describe this photo.","AIがこの画像を説明します。","AI 將描述這張圖片。")} 
                              onChange={(e) =>
                                editAsset(a.id, {
                                  approved_observation: e.target.value,
                                })
                              }
                            />
                          </Field>
                        )}
                      </details>
                      {a.enabled && (
                        <span
                          className={
                            "simple-photo-use " +
                            (a.role === "context" ? "is-context" : "")
                          }
                        >
                          {a.role === "context"
                            ? t("只作提示词灵感","Prompt inspiration only","プロンプト参考のみ","只作提示詞靈感")
                            : a.role === "first_frame"
                              ? t("起始图片","Starting image","開始画像","起始圖片")
                              : a.role === "last_frame"
                                ? t("结束图片","Ending image","終了画像","結束圖片")
                                : t(`视频参考 ${videoImages.findIndex(item=>item.id===a.id)+1}`,`Video reference ${videoImages.findIndex(item=>item.id===a.id)+1}`,`映像参照 ${videoImages.findIndex(item=>item.id===a.id)+1}`,`影片參考 ${videoImages.findIndex(item=>item.id===a.id)+1}`)}
                        </span>
                      )}
                      <div className="simple-photo-bottom">
                        <label className="simple-checkbox">
                          <input
                            type="checkbox"
                            checked={a.enabled}
                            onChange={(e) =>
                              update((d) => {
                                const item = d.assets.find(
                                  (item) => item.id === a.id,
                                );
                                if (item) item.enabled = e.target.checked;
                                setSimpleMode(d, d.mode);
                              })
                            }
                          />
                        <span>{t("使用这张图","Use this photo","この画像を使う","使用這張圖")}</span>
                        </label>
                        <button
                          className="simple-icon"
                          title={t("移除图片","Remove photo","画像を削除","移除圖片")}
                          aria-label={t(`移除图片 ${index+1}`,`Remove photo ${index+1}`,`画像${index+1}を削除`,`移除圖片 ${index+1}`)}
                          onClick={() =>
                            update((d) => removeSimpleAsset(d, a.id))
                          }
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                    </fieldset>
                  </article>
                );
              })}
            </div>
          )}
          {!images.length && (
            <p className="simple-tip">
              For two people with different outfits: add two face photos and two
              clothes photos. Choose who wears each outfit.
            </p>
          )}
          {people
            .filter(
              (person) =>
                !person.asset_ids.some((id) =>
                  images.some(
                    (a) =>
                      a.id === id &&
                      ["face", "character"].includes(a.semantic_role),
                  ),
                ),
            )
            .map((person) => (
              <div className="simple-person-without-photo" key={person.id}>
                <UserRound size={17} />
                <Field label="Person without a photo">
                  <input
                    aria-label={`Name for ${person.name || "person"}`}
                    value={person.name}
                    placeholder="Person's name"
                    disabled={unavailable}
                    onChange={(e) =>
                      update((d) => renamePerson(d, person.id, e.target.value))
                    }
                  />
                </Field>
              </div>
            ))}
          <button
            className="simple-add-line"
            disabled={unavailable}
            onClick={() =>
              update((d) => {
                const n = getPeople(d).length + 1;
                d.subjects.push({
                  id: uid(),
                  name: `Person ${n}`,
                  asset_ids: [],
                  description: "",
                  simple_person: true,
                } as Subject);
              })
            }
          >
            <Plus size={13} /> {t("添加暂无图片的角色","Add a person without a photo","画像なしの人物を追加","新增暫無圖片的角色")}
          </button>
        </section>

        <section role="tabpanel" id="simple-panel-story" hidden={editorTab !== 'story'} aria-labelledby="simple-tab-story"
          className="simple-section"
        >
          <div className="simple-section-heading">
            <span className="simple-step">2</span>
            <div>
              <h2 id="simple-idea-title">{t("讲述你的故事","Tell your story","物語を伝える","講述你的故事")}</h2>
              <p>
                 {t("简单描述即可。角色和对白由你决定，AI 会完善镜头。","Simple words are enough. You choose the people and dialogue; AI improves the scene.","簡単な説明で十分です。人物と台詞はあなたが決め、AIがシーンを整えます。","簡單描述即可。角色和對白由你決定，AI 會完善鏡頭。")}
              </p>
            </div>
          </div>
          <fieldset className="simple-story-fields" disabled={unavailable}>
            <IdeaBuilder project={p} update={props.checkpointUpdate}/>
            <Field label={t("画面里发生什么？","What should happen?","何が起きますか？","畫面裡發生什麼？")}>
              <textarea
                ref={storyRef}
                className="simple-idea-input"
                rows={3}
                value={p.story.text}
                placeholder="e.g. Mira shows Nora a gift, gives it to her, and they smile together in the room."
                onChange={(e) =>
                  update((d) => {
                    d.story.text = e.target.value;
                  })
                }
              />
            </Field>
            {!!activeImages.length&&<ReferenceInsert project={p} onInsert={tag=>insertReference(tag)}/>}
            {!!people.length && (
              <details className="simple-people-actions">
                <summary>
                  {t("谁做什么？","Who does what?","誰が何をしますか？","誰做什麼？")} <span>{t("可选","optional","任意","可選")}</span>
                </summary>
                <p className="simple-detail-help">
                  Your main idea can cover this. Add individual instructions
                  only if you want more control.
                </p>
                {people.map((person) => (
                  <div className="simple-person-action" key={person.id}>
                    <span className="simple-person-icon">
                      <UserRound size={17} />
                    </span>
                    <Field label={`${person.name || "This person"}'s action`}>
                      <input
                        value={personAction(person)}
                        placeholder={`e.g. ${person.name || "They"} holds the box, then hands it to the other person`}
                        onChange={(e) =>
                          setPersonAction(person, e.target.value)
                        }
                      />
                    </Field>
                  </div>
                ))}
              </details>
            )}

            <details
              className="simple-scenes"
              open={showScenes}
              onToggle={(e) => setShowScenes(e.currentTarget.open)}
            >
              <summary>
                  {t("分镜、摄影机与对白","Scenes, camera & spoken words","シーン・カメラ・台詞","分鏡、攝影機與對白")} <span>{t("可选","optional","任意","可選")}</span>
              </summary>
              <div className="simple-subheading">
                <div>
                  <p>
                    Each card can use a different shot size and camera move. Choose “Cut” to change the shot. Add exact words under their speaker.
                  </p>
                </div>
                <Field label={t("分镜数量","Number of scenes","シーン数","分鏡數量")}>
                  <select
                    value={p.shots.length}
                    onChange={(e) =>
                      update((d) =>
                        setSimpleSceneCount(d, Number(e.target.value)),
                      )
                    }
                  >
                    {[1, 2, 3, 4, 5, 6].map((n) => (
                      <option key={n} value={n}>
                        {n} scene{n === 1 ? "" : "s"}
                      </option>
                    ))}
                  </select>
                </Field>
              </div>
              {p.shots.map((shot, i) => (
                <article className="simple-scene" key={shot.id}>
                  <div className="simple-scene-heading">
                    <strong>
                      <span>{i + 1}</span>Scene {i + 1}
                    </strong>
                    <span>{Number(shot.duration.toFixed(2))} seconds</span>
                    {p.shots.length > 1 && (
                      <button
                        className="simple-icon"
                        aria-label={`Remove scene ${i + 1}`}
                        title="Remove this scene and its dialogue"
                        onClick={() =>
                          props.checkpointUpdate((d) => {
                            d.shots = retime(
                              d.shots.filter((s) => s.id !== shot.id),
                              d.duration,
                            );
                          })
                        }
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </div>
                  <Field label={`What happens in scene ${i + 1}?`}>
                    <textarea
                      ref={el=>{actionRefs.current[shot.id]=el;}}
                      rows={2}
                      value={shot.action}
                      placeholder="A short instruction, or leave blank for AI to plan this scene."
                      onChange={(e) =>
                        editShot(shot.id, (s) => {
                          s.action = e.target.value;
                        })
                      }
                    />
                  </Field>
                  {!!activeImages.length&&<ReferenceInsert project={p} label={`Scene ${i+1} insert photo reference`} onInsert={tag=>insertReference(tag,shot.id)}/>}
                  <SceneDirector project={p} shot={shot} index={i} update={props.checkpointUpdate}/>
                  <div className="simple-dialogue-list">
                    {shot.dialogue.map((line, lineIndex) => (
                      <div
                        className="simple-dialogue"
                        key={line.id || lineIndex}
                      >
                        <div className="simple-dialogue-top">
                          <MessageSquare size={15} />
                          <select
                            aria-label={`Scene ${i + 1} dialogue ${lineIndex + 1} speaker`}
                            value={line.speaker_id || ""}
                            onChange={(e) =>
                              editShot(shot.id, (s) => {
                                s.dialogue[lineIndex].speaker_id =
                                  e.target.value;
                                if (
                                  e.target.value &&
                                  !s.visible_subject_ids.includes(
                                    e.target.value,
                                  ) &&
                                  !s.offscreen_subject_ids.includes(
                                    e.target.value,
                                  )
                                )
                                  s.visible_subject_ids.push(e.target.value);
                              })
                            }
                          >
                            <option value="">{t("谁说这句？","Who says this?","誰の台詞ですか？","誰說這句？")}</option>
                            {people.map((person) => (
                              <option key={person.id} value={person.id}>
                                  {person.name || t("未命名角色","Unnamed person","名前のない人物","未命名角色")}
                              </option>
                            ))}
                          </select>
                          <span>{t("说","says","話す","說")}</span>
                          <button
                            className="simple-icon"
                            aria-label={`Remove dialogue ${lineIndex + 1} from scene ${i + 1}`}
                            onClick={() =>
                              editShot(shot.id, (s) => {
                                s.dialogue.splice(lineIndex, 1);
                              })
                            }
                          >
                            <X size={14} />
                          </button>
                        </div>
                        <textarea
                          aria-label={`Scene ${i + 1} dialogue ${lineIndex + 1} exact words`}
                          rows={2}
                          value={line.text || ""}
                          placeholder="Type their exact words, in any language…"
                          onChange={(e) =>
                            editShot(shot.id, (s) => {
                              s.dialogue[lineIndex].text = e.target.value;
                              s.dialogue[lineIndex].locked = true;
                            })
                          }
                        />
                        <div className="simple-dialogue-options">
                          <Field label="Language">
                            <input
                              value={line.language || ""}
                              placeholder="e.g. Albanian"
                              onChange={(e) =>
                                editShot(shot.id, (s) => {
                                  s.dialogue[lineIndex].language =
                                    e.target.value;
                                })
                              }
                            />
                          </Field>
                          <Field label="How they say it">
                            <input
                              value={line.delivery || ""}
                              placeholder="e.g. softly, with a smile"
                              onChange={(e) =>
                                editShot(shot.id, (s) => {
                                  s.dialogue[lineIndex].delivery =
                                    e.target.value;
                                })
                              }
                            />
                          </Field>
                        </div>
                      </div>
                    ))}
                  </div>
                  <button
                    className="simple-add-line"
                    onClick={() =>
                      editShot(shot.id, (s) => {
                        const speaker = people[0]?.id || "";
                        s.dialogue.push({
                          id: uid(),
                          speaker_id: speaker,
                          text: "",
                          language: "",
                          delivery: "",
                          locked: true,
                        });
                        if (
                          speaker &&
                          !s.visible_subject_ids.includes(speaker) &&
                          !s.offscreen_subject_ids.includes(speaker)
                        )
                          s.visible_subject_ids.push(speaker);
                      })
                    }
                  >
                    <Plus size={14} /> {t("添加对白","Add spoken line","台詞を追加","新增對白")}
                  </button>
                </article>
              ))}
            </details>
            <details className="simple-extra">
              <summary>
                {t("补充导演要求","A little more direction","演出指示を追加","補充導演要求")} <span>{t("可选","optional","任意","可選")}</span>
              </summary>
              <div className="simple-extra-grid">
                <Field label={t("氛围或风格","Mood or style","雰囲気・スタイル","氛圍或風格")}>
                  <input
                    value={p.style.vibe || ""}
                    placeholder="e.g. warm, playful, elegant"
                    onChange={(e) =>
                      update((d) => {
                        d.style.vibe = e.target.value;
                      })
                    }
                  />
                </Field>
                <Field label="Music or background sounds">
                  <input
                    value={p.soundscape || ""}
                    placeholder="e.g. quiet room, soft piano"
                    onChange={(e) =>
                      update((d) => {
                        d.soundscape = e.target.value;
                      })
                    }
                  />
                </Field>
                <Field label="Anything else for the AI?">
                  <textarea
                    rows={2}
                    value={p.assistant_instructions || ""}
                    placeholder="e.g. keep both women visible; only Nora holds the box at the end"
                    onChange={(e) =>
                      update((d) => {
                        d.assistant_instructions = e.target.value;
                      })
                    }
                  />
                </Field>
              </div>
            </details>
          </fieldset>
          <TimelinePlanner project={p} update={update} checkpointUpdate={props.checkpointUpdate}/>
        </section>
        <section role="tabpanel" id="simple-panel-settings" hidden={editorTab !== 'settings'} aria-labelledby="simple-tab-settings" className="simple-section simple-settings-panel">
          <h2>{t("场景设置","Scene settings","シーン設定","場景設定")}</h2>
          <fieldset disabled={unavailable}>
            <div className="simple-options">
              <Field label={t("视频时长","Video length","映像の長さ","影片時長")}>
                <select
                  value={p.duration}
                  onChange={(e) =>
                    update((d) => {
                      d.duration = Number(e.target.value);
                      d.shots = retime(d.shots, d.duration);
                    })
                  }
                >
                  {Array.from(new Set([5, 7, 10, 15, p.duration]))
                    .sort((a, b) => a - b)
                    .map((n) => (
                      <option key={n} value={n}>
                        {n} {t("秒","seconds","秒","秒")}
                      </option>
                    ))}
                </select>
              </Field>
              <Field label={t("画面比例","Shape","画面比率","畫面比例")}>
                <select
                  value={p.aspect_ratio}
                  onChange={(e) =>
                    update((d) => {
                      d.aspect_ratio = e.target.value;
                    })
                  }
                >
                  <option value="16:9">{t("宽屏","Wide","ワイド","寬螢幕")} · 16:9</option>
                  <option value="9:16">{t("竖屏","Vertical","縦長","直式")} · 9:16</option>
                  <option value="1:1">{t("方形","Square","正方形","方形")} · 1:1</option>
                  <option value="4:3">{t("经典","Classic","クラシック","經典")} · 4:3</option>
                  <option value="3:4">{t("人像","Portrait","ポートレート","人像")} · 3:4</option>
                </select>
              </Field>
              <Field label={t("视频如何使用图片？","How should the video use your photos?","画像を映像でどう使いますか？","影片如何使用圖片？")}>
                <select
                  value={p.mode}
                  onChange={(e) =>
                    update((d) => setSimpleMode(d, e.target.value))
                  }
                >
                  <option value="ref2va">{t("参考图","Reference photos","参照画像","參考圖")}</option>
                  <option value="i2va">{t("仅首帧","First frame only","開始フレームのみ","僅首幀")}</option>
                  <option value="fl2va">{t("首帧 + 尾帧","First + last frame","開始 + 終了フレーム","首幀 + 尾幀")}</option>
                  <option value="l2va">{t("仅尾帧","Last frame only","終了フレームのみ","僅尾幀")}</option>
                  <option value="t2va">{t("仅文字","Text only","テキストのみ","僅文字")}</option>
                </select>
              </Field>
            </div>
            {p.mode !== "ref2va" && (
              <div className="simple-keyframes">
                {["i2va", "fl2va"].includes(p.mode) && (
                  <Field label="Starting image">
                    <select
                      value={
                        activeImages.find((a) => a.role === "first_frame")
                          ?.id || ""
                      }
                      onChange={(e) =>
                        update((d) =>
                          setKeyframe(d, e.target.value, "first_frame"),
                        )
                      }
                    >
                      <option value="" disabled>
                        Choose the first frame…
                      </option>
                      {images.map((a, i) => (
                        <option key={a.id} value={a.id}>
                          Photo {i + 1} · {a.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                )}
                {["l2va", "fl2va"].includes(p.mode) && (
                  <Field label="Ending image">
                    <select
                      value={
                        activeImages.find((a) => a.role === "last_frame")?.id ||
                        ""
                      }
                      onChange={(e) =>
                        update((d) =>
                          setKeyframe(d, e.target.value, "last_frame"),
                        )
                      }
                    >
                      <option value="" disabled>
                        Choose the last frame…
                      </option>
                      {images.map((a, i) => (
                        <option key={a.id} value={a.id}>
                          Photo {i + 1} · {a.name}
                        </option>
                      ))}
                    </select>
                  </Field>
                )}
                <p>
                  {p.mode === "i2va"
                    ? "The video starts from one image. No ending image is needed."
                    : p.mode === "l2va"
                      ? "The video ends at your chosen image. No starting image is needed."
                      : p.mode === "fl2va"
                        ? "Choose the image where the video starts and the image where it ends."
                        : "AI can use your photos for ideas; the video receives text only."}
                  {!!inspirationImages.length &&
                    " Other photos help AI describe the scene; they are not sent to H3 as separate image references."}
                </p>
              </div>
            )}
          </fieldset>

          <TemplateShelf project={p} update={props.checkpointUpdate} onRestore={props.onRestore} currentPrompt={props.currentPrompt} resultFresh={props.resultFresh} busy={unavailable}/>
          {props.modelPicker}
          {props.settingsPanel}
        </section>
        <div className="simple-editor-footer">
          <div className="simple-generate-area">
            <button
              className="simple-generate"
              disabled={
                unavailable || props.renderBusy || (!p.story.text.trim() && !activeImages.length)
              }
              onClick={props.onGenerate}
            >
              {unavailable ? (
                <LoaderCircle size={19} className="simple-spinning" />
              ) : (
                <Sparkles size={19} />
              )}
              {unavailable ? t("正在生成提示词…","Working on your prompt…","プロンプトを作成中…","正在生成提示詞…") : props.renderBusy ? t("视频任务进行中","Video request in progress","映像処理中","影片任務進行中") : t("生成我的提示词","Make my prompt","プロンプトを作成","生成我的提示詞")}
              {!unavailable && <ArrowRight size={18} />}
            </button>
            <button className="simple-build" disabled={unavailable||props.renderBusy||!p.story.text.trim()} onClick={props.onBuild}>{t("不用 AI 直接生成","Build without AI","AIなしで作成","不用 AI 直接生成")}</button>
            <p>
              {unavailable
                ? props.progress || props.busy
                : props.renderBusy ? t("当前视频生成时仍可编辑下一段内容，请在视频区查看进度。","You can edit your next idea while the current request finishes. Check its status in Video.","映像生成中も次の内容を編集できます。進捗は映像欄で確認してください。","目前影片生成時仍可編輯下一段內容，請在影片區查看進度。") : t("AI 会读取你的图片，把想法整理成可直接使用的 H3 提示词。","AI looks at your photos and turns your idea into a ready-to-use H3 prompt.","AIが画像を読み、アイデアをすぐ使えるH3プロンプトに整えます。","AI 會讀取你的圖片，把想法整理成可直接使用的 H3 提示詞。")}
            </p>
          </div>
          {props.error && (
            <div className="simple-error" role="alert">
              {props.error}
            </div>
          )}
          {props.notice && (
            <div className="simple-notice" role="status">
              {props.notice}
            </div>
          )}
        </div>
        </div>

        <aside className="simple-video-column" aria-label="Video and continuation">{props.comfyPanel}</aside>
        </div>
        <section
          className="simple-section simple-result"
          id="simple-result"
          aria-labelledby="simple-result-title"
        >
          <div className="simple-section-heading">
            <span className="simple-step">3</span>
            <div>
              <h2 id="simple-result-title">{t("你的视频提示词","Your prompt","映像プロンプト","你的影片提示詞")}</h2>
              <p>
                {props.resultFresh
                  ? t("已可生成视频，图片会自动连接。","Ready for Generate video. Your photos are connected automatically.","映像を生成できます。画像は自動で接続されます。","已可生成影片，圖片會自動連接。")
                  : props.currentPrompt
                    ? t("这是当前草稿；点击“生成我的提示词”可让 AI 完善。","Your current draft. Use Make my prompt to improve it with AI.","現在の下書きです。「プロンプトを作成」でAIが改善します。","這是目前草稿；點擊「生成我的提示詞」可讓 AI 完善。")
                    : t("完成后的提示词会显示在这里。","Your finished prompt will appear here.","完成したプロンプトがここに表示されます。","完成後的提示詞會顯示在這裡。")}
              </p>
            </div>
            {props.resultFresh && (
              <span className="simple-ready">
                  <Check size={14} /> {t("已就绪","Ready","準備完了","已就緒")}
              </span>
            )}
          </div>
          {props.currentPrompt ? (
            <>
              <div className="simple-result-summary">
                <span>
                  <Video size={15} />
                  {p.duration}s · {p.aspect_ratio}
                </span>
                <span>
                  <ImagePlus size={15} />
                  {t(`${videoImages.length} 张视频参考图`,`${videoImages.length} video image${videoImages.length===1?'':'s'}`,`${videoImages.length}枚の映像参照画像`,`${videoImages.length} 張影片參考圖`)}
                </span>
                {!!inspirationImages.length && (
                  <span>
                    {t(`${inspirationImages.length} 张灵感图`,`${inspirationImages.length} inspiration photo${inspirationImages.length===1?'':'s'}`,`${inspirationImages.length}枚の参考画像`,`${inspirationImages.length} 張靈感圖`)}
                  </span>
                )}
                <span>
                  <MessageSquare size={15} />
                  {p.shots.reduce(
                    (n, s) =>
                      n + s.dialogue.filter((l) => l.text?.trim()).length,
                    0,
                  )} {t("句对白","spoken lines","行の台詞","句對白")}
                </span>
                {p.simple_generation?.method === 'manual' ? <span>{t("按你的选择生成 · 未使用 AI","Built from your choices · no AI","選択内容から作成・AI未使用","按你的選擇生成 · 未使用 AI")}</span> : props.result?.seconds != null && (
                  <span>{t("生成耗时 ","Written in ","作成時間 ","生成耗時 ")}{Math.round(props.result.seconds)}s</span>
                )}
              </div>
              {props.resultFresh && (
                <div className="simple-plan-preview">
                  {p.shots.map((s, i) => (
                    <div key={s.id}>
                      <span>{t("分镜","SCENE","シーン","分鏡")} {i + 1}</span>
                      <p>
                        {s.action || t("场景导演说明已写入提示词。","Scene direction is included in the prompt.","シーン演出はプロンプトに含まれています。","場景導演說明已寫入提示詞。")}
                      </p>
                      {s.dialogue
                        .filter((l) => l.text?.trim())
                        .map((line, j) => (
                          <blockquote key={line.id || j}>
                            <b>
                              {people.find(
                                (person) => person.id === line.speaker_id,
                              )?.name || t("说话人","Speaker","話者","說話人")}
                              :
                            </b>{" "}
                            “{line.text}”
                          </blockquote>
                        ))}
                    </div>
                  ))}
                </div>
              )}
              {!!props.referenceMap.length&&<details className="result-reference-map">
                <summary>{t("已绑定的图片参考","Your connected photo references","接続済み参照画像","已綁定的圖片參考")}</summary>
                <p>{t("生成视频时会按正确顺序传入这些图片；点击图片可检查。","Generate video connects these photos in the correct order. Select a photo to review it.","映像生成時に正しい順序で画像を渡します。画像を選ぶと確認できます。","生成影片時會按正確順序傳入這些圖片；點擊圖片可檢查。")}</p>
                <div>{props.referenceMap.map(r=>{
                  const asset=p.assets.find(a=>a.id===r.asset_id);
                  return <button key={r.asset_id} type="button" onClick={()=>asset?.media_type==='image'&&setPreview(asset)}>
                    {asset?.media_type==='image'&&<img src={`/api/assets/${r.asset_id}/thumbnail`} alt=""/>}
                    <span><b>{r.token}</b><small>{asset?.prompt_tag?'@'+asset.prompt_tag:r.name}</small><small>{r.name}</small></span>
                  </button>;
                })}</div>
              </details>}
              <div className="simple-result-buttons">
                <button
                  className="simple-copy"
                  onClick={props.onCopy}
                  disabled={!props.resultFresh}
                >
                  <Copy size={17} /> {t("复制提示词","Copy prompt","プロンプトをコピー","複製提示詞")}
                </button>
                <button onClick={props.onSave} disabled={!props.resultFresh}>
                  <Download size={16} /> {t("保存提示词","Save prompt","プロンプトを保存","儲存提示詞")}
                </button>
                {props.canReturn && (
                  <button
                    onClick={props.onSendToComfy}
                    disabled={unavailable || !props.resultFresh}
                  >
                    <ArrowRight size={16} /> Update connected prompt only
                  </button>
                )}
                {props.canUndo && (
                  <button
                    className="simple-quiet"
                    onClick={props.onUndo}
                    disabled={unavailable}
                  >
                    <Undo2 size={15} /> Undo AI changes
                  </button>
                )}
              </div>
              <details className="simple-full-prompt">
                <summary>{t("查看完整 H3 提示词","See the full H3 prompt","完全なH3プロンプトを見る","查看完整 H3 提示詞")}</summary>
                <textarea
                  aria-label="Finished H3 prompt"
                  readOnly
                  value={props.currentPrompt}
                  rows={14}
                />
                <p>
                  Generate video sends this prompt with its matching photos.
                  Inspiration photos help the AI plan; they are not extra video references.
                </p>
              </details>
            </>
          ) : (
            <div className="simple-result-empty">
              <Sparkles size={26} />
              <p>
                Add your idea above, then choose <strong>Make my prompt</strong>
                .
              </p>
            </div>
          )}
        </section>
        <footer className="simple-footer">
          <span>{t("由你选择的 Ollama 或 LM Studio 模型在本机完成。","Made locally with your Ollama or LM Studio model.","選択したOllamaまたはLM Studioモデルでローカル処理します。","由你選擇的 Ollama 或 LM Studio 模型在本機完成。")}</span>
          <button onClick={props.onAdvanced}>
            {t("需要运镜控制或完整编辑器？打开高级模式","Need camera controls or the full editor? Open Advanced","カメラ制御や完全版エディターが必要ですか？詳細モードを開く","需要運鏡控制或完整編輯器？開啟進階模式")}{" "}
            <ArrowRight size={13} />
          </button>
        </footer>
      </main>
      {preview && (
        <div
          className="simple-lightbox"
          role="dialog"
          aria-modal="true"
          aria-label={`Photo preview: ${preview.name}`}
          onClick={() => setPreview(null)}
        >
          <button
            aria-label="Close photo preview"
            onClick={() => setPreview(null)}
          >
            <X size={21} />
          </button>
          <img
            src={`/api/assets/${preview.id}/thumbnail`}
            alt={preview.name}
            onClick={(e) => e.stopPropagation()}
          />
          <p>{preview.name}</p>
        </div>
      )}
    </div>
  );
}
