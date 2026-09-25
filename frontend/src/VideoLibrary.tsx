import React, { useEffect, useMemo, useState } from "react";
import { Check, Download, Film, FolderOpen, LoaderCircle, RefreshCw, Search, Video } from "lucide-react";
import { api } from "./api";
import { useUiLanguage } from "./i18n";
import "./VideoLibrary.css";

type Membership = {
  series_id:string; series_title:string; episode_index:number; episode_title:string; part_index:number;
};
type VideoTake = {
  id:string; status:string; seed?:number|null; duration?:number|null; new_seconds?:number|null;
  created_at?:number|null; width?:number|null; height?:number|null; video_url?:string|null;
  scene_video_url?:string|null; download_url?:string|null;
};
type VideoRow = {
  production_id:string; production_title:string; production_episode:number;
  segment_id:string; segment_index:number; segment_title:string; project_id?:string|null;
  project_status:string; selected?:VideoTake|null; ready:boolean; memberships:Membership[];
};
type ScriptSummary = {id:string;title:string;episodes:{index:number;title:string;production_ids:string[]}[]};
type Overview = {
  scripts:ScriptSummary[]; productions:{id:string;title:string;current_episode:number}[];
  videos:VideoRow[]; ready_count:number; video_count:number;
};
type SelectionFilm = {
  signature:string;run_ids:string[];final_ready:boolean;final_url?:string|null;download_url?:string|null;
  file_path?:string|null;folder_path?:string|null;
};

const PAGE_SIZE=18;
const durationLabel=(take?:VideoTake|null)=>{
  const value=take?.new_seconds??take?.duration;
  return typeof value==="number"&&Number.isFinite(value)?`${Number(value.toFixed(2))}s`:"—";
};

export default function VideoLibrary({onOpenProduction}:{onOpenProduction:(id:string)=>Promise<void>}){
  const {text:uiText}=useUiLanguage();
  const t=(zh:string,en:string,ja:string,tw=zh)=>uiText({"zh-CN":zh,"zh-TW":tw,en,ja});
  const [overview,setOverview]=useState<Overview|null>(null);
  const [scriptFilter,setScriptFilter]=useState("");
  const [episodeFilter,setEpisodeFilter]=useState("");
  const [productionFilter,setProductionFilter]=useState("");
  const [statusFilter,setStatusFilter]=useState<"all"|"ready"|"missing">("all");
  const [query,setQuery]=useState("");
  const [page,setPage]=useState(0);
  const [selectedIds,setSelectedIds]=useState<string[]>([]);
  const [selectionFilm,setSelectionFilm]=useState<SelectionFilm|null>(null);
  const [busy,setBusy]=useState("");
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");

  const refresh=async()=>{
    setBusy(t("刷新视频一览","Refreshing videos","映像一覧を更新中","重新整理影片一覽"));setError("");
    try{setOverview(await api("/video-library") as Overview);setSelectedIds([]);setSelectionFilm(null);}
    catch(e){setError((e as Error).message);}finally{setBusy("");}
  };
  useEffect(()=>{void refresh();},[]);

  const script=overview?.scripts.find(item=>item.id===scriptFilter);
  const episodeOptions=useMemo(()=>{
    if(script)return script.episodes.map(item=>({index:item.index,title:item.title}));
    const values=new Map<number,string>();
    for(const row of overview?.videos||[]){
      for(const membership of row.memberships)values.set(membership.episode_index,membership.episode_title);
      if(!row.memberships.length)values.set(row.production_episode,t("第 "+row.production_episode+" 集","Episode "+row.production_episode,"第"+row.production_episode+"話","第 "+row.production_episode+" 集"));
    }
    return [...values].sort((a,b)=>a[0]-b[0]).map(([index,title])=>({index,title}));
  },[overview,scriptFilter]);

  useEffect(()=>{
    if(episodeFilter&&!episodeOptions.some(item=>String(item.index)===episodeFilter))setEpisodeFilter("");
  },[episodeFilter,episodeOptions]);
  useEffect(()=>{setPage(0);setSelectedIds([]);setSelectionFilm(null);},[scriptFilter,episodeFilter,productionFilter,statusFilter]);

  const filtered=useMemo(()=>{
    const needle=query.trim().toLocaleLowerCase();
    const rows=(overview?.videos||[]).filter(row=>{
      if(scriptFilter==="__unassigned"&&row.memberships.length)return false;
      if(scriptFilter&&scriptFilter!=="__unassigned"&&!row.memberships.some(item=>item.series_id===scriptFilter))return false;
      if(episodeFilter){
        const episode=Number(episodeFilter);
        if(scriptFilter&&scriptFilter!=="__unassigned"){
          if(!row.memberships.some(item=>item.series_id===scriptFilter&&item.episode_index===episode))return false;
        }else if(row.memberships.length? !row.memberships.some(item=>item.episode_index===episode):row.production_episode!==episode)return false;
      }
      if(productionFilter&&row.production_id!==productionFilter)return false;
      if(statusFilter==="ready"&&!row.ready)return false;
      if(statusFilter==="missing"&&row.ready)return false;
      if(needle&&!`${row.production_title} ${row.segment_title} ${row.memberships.map(item=>`${item.series_title} ${item.episode_title}`).join(" ")}`.toLocaleLowerCase().includes(needle))return false;
      return true;
    });
    return rows.sort((a,b)=>{
      if(scriptFilter&&scriptFilter!=="__unassigned"){
        const left=a.memberships.find(item=>item.series_id===scriptFilter),right=b.memberships.find(item=>item.series_id===scriptFilter);
        if(left&&right)return left.episode_index-right.episode_index||left.part_index-right.part_index||a.segment_index-b.segment_index;
      }
      return a.production_title.localeCompare(b.production_title)||a.production_episode-b.production_episode||a.segment_index-b.segment_index;
    });
  },[overview,scriptFilter,episodeFilter,productionFilter,statusFilter,query]);

  const pages=Math.max(1,Math.ceil(filtered.length/PAGE_SIZE));
  useEffect(()=>{if(page>=pages)setPage(pages-1);},[page,pages]);
  const shown=filtered.slice(page*PAGE_SIZE,page*PAGE_SIZE+PAGE_SIZE);
  const filteredReadyCount=filtered.reduce((count,row)=>count+(row.ready?1:0),0);
  const readyOnPage=shown.flatMap(row=>row.selected?.id?[row.selected.id]:[]);
  const allPageSelected=!!readyOnPage.length&&readyOnPage.every(id=>selectedIds.includes(id));
  const selectedRows=filtered.filter(row=>row.selected?.id&&selectedIds.includes(row.selected.id));
  const toggle=(id:string)=>{setSelectedIds(values=>values.includes(id)?values.filter(value=>value!==id):[...values,id]);setSelectionFilm(null);};
  const togglePage=()=>{setSelectionFilm(null);setSelectedIds(values=>allPageSelected?values.filter(id=>!readyOnPage.includes(id)):[...new Set([...values,...readyOnPage])]);};
  const mergeSelection=async()=>{
    const runIds=selectedRows.map(row=>row.selected!.id);
    if(runIds.length<2)return;
    setBusy(t("正在合并选中视频…","Assembling selected videos…","選択映像を結合中…","正在合併選中影片…"));setError("");setNotice("");
    try{
      const result=await api("/video-library/film",{run_ids:runIds},undefined,"POST",{timeoutMs:1800000}) as SelectionFilm;
      setSelectionFilm(result);setNotice(t("选中视频已按当前列表顺序合并并保存在本机。","Selected videos were assembled in the current list order and saved locally.","選択映像を現在の一覧順で結合し、ローカルに保存しました。","選中影片已按目前列表順序合併並儲存在本機。"));
    }catch(e){setError((e as Error).message);}finally{setBusy("");}
  };
  const openSelectionFolder=async()=>{
    if(!selectionFilm?.signature)return;
    setBusy(t("打开文件位置","Opening file location","保存場所を開く","開啟檔案位置"));setError("");
    try{const value=await api(`/video-library/film/${selectionFilm.signature}/open`,{},undefined,"POST") as {path:string};setNotice(t("已打开：","Opened: ","開きました：","已開啟：")+value.path);}
    catch(e){setError((e as Error).message);}finally{setBusy("");}
  };

  return <section className="video-library card">
    <div className="section-title video-library-title"><div><span className="eyebrow">ALL PROJECT VIDEOS</span><h2><Video size={22}/>{t("视频一览","Video overview","映像一覧","影片一覽")}</h2><p>{t("集中查看所有剧集 / 片段的采用视频。可按剧本与剧集筛选、勾选任意成片进行合并，并直接进入所属项目。","Review adopted videos from every episode / clip part. Filter by script and episode, assemble any selected videos, or jump straight into the source project.","すべての話・パートの採用映像を一覧表示します。脚本と話で絞り込み、選択映像を結合し、元の作品を直接開けます。","集中查看所有劇集／片段的採用影片。可按劇本與劇集篩選、勾選任意成片進行合併，並直接進入所屬專案。")}</p></div><button onClick={()=>void refresh()} disabled={!!busy}><RefreshCw className={busy?"spin":""} size={17}/>{t("刷新视频","Refresh videos","映像を更新","重新整理影片")}</button></div>
    {(error||notice)&&<div className={error?"production-alert error":"production-alert"}>{error||notice}</div>}
    {busy&&<div className="production-progress"><LoaderCircle className="spin" size={18}/><span>{busy}</span></div>}
    <div className="video-library-filters">
      <label>{t("剧本","Script","脚本","劇本")}<select value={scriptFilter} onChange={e=>setScriptFilter(e.target.value)}><option value="">{t("全部剧本与独立项目","All scripts and standalone projects","全脚本・単独作品","全部劇本與獨立專案")}</option>{overview?.scripts.map(item=><option key={item.id} value={item.id}>{item.title}</option>)}<option value="__unassigned">{t("未归入剧本","Not assigned to a script","脚本未登録","未歸入劇本")}</option></select></label>
      <label>{t("剧集","Episode","話","劇集")}<select value={episodeFilter} onChange={e=>setEpisodeFilter(e.target.value)}><option value="">{t("全部剧集","All episodes","全話","全部劇集")}</option>{episodeOptions.map(item=><option key={item.index} value={item.index}>{String(item.index).padStart(2,"0")} · {item.title}</option>)}</select></label>
      <label>{t("项目","Project","作品","專案")}<select value={productionFilter} onChange={e=>setProductionFilter(e.target.value)}><option value="">{t("全部项目","All projects","全作品","全部專案")}</option>{overview?.productions.map(item=><option key={item.id} value={item.id}>{item.title} · E{item.current_episode}</option>)}</select></label>
      <label>{t("状态","Status","状態","狀態")}<select value={statusFilter} onChange={e=>setStatusFilter(e.target.value as typeof statusFilter)}><option value="all">{t("全部","All","すべて","全部")}</option><option value="ready">{t("已有视频","Video ready","映像あり","已有影片")}</option><option value="missing">{t("尚缺视频","Missing video","映像なし","尚缺影片")}</option></select></label>
      <label className="video-library-search">{t("搜索","Search","検索","搜尋")}<span><Search size={16}/><input value={query} onChange={e=>{setQuery(e.target.value);setPage(0)}} placeholder={t("剧本、项目或分镜名称…","Script, project or clip…","脚本・作品・クリップ名…","劇本、專案或分鏡名稱…")}/></span></label>
    </div>
    <div className="video-library-summary"><div><strong>{filteredReadyCount}</strong><span>{t("个视频就绪","videos ready","本の映像準備完了","個影片就緒")}</span></div><div><strong>{filtered.length}</strong><span>{t("条筛选结果","filtered items","件の絞り込み結果","條篩選結果")}</span></div><div><strong>{selectedRows.length}</strong><span>{t("个已选","selected","件選択中","個已選")}</span></div><button disabled={!readyOnPage.length} onClick={togglePage}>{allPageSelected?<Check size={16}/>:null}{allPageSelected?t("取消选择本页","Clear this page","このページを解除","取消選擇本頁"):t("选择本页视频","Select this page","このページを選択","選擇本頁影片")}</button><button className="primary" disabled={selectedRows.length<2||!!busy} onClick={()=>void mergeSelection()}><Film size={17}/>{t("合并选中视频","Assemble selected videos","選択映像を結合","合併選中影片")} · {selectedRows.length}</button></div>
    {!overview?<div className="production-output-empty">{t("正在读取全部视频…","Loading all videos…","全映像を読込中…","正在讀取全部影片…")}</div>:!shown.length?<div className="production-project-empty"><Video size={30}/><strong>{t("没有符合条件的视频","No matching videos","該当する映像はありません","沒有符合條件的影片")}</strong><span>{t("调整筛选条件，或先完成片段视频生成。","Change the filters or finish generating clip videos first.","条件を変更するか、先にクリップ映像を生成してください。","調整篩選條件，或先完成片段影片生成。")}</span></div>:
      <div className="video-library-grid">{shown.map(row=>{const take=row.selected;const membership=scriptFilter&&scriptFilter!=="__unassigned"?row.memberships.find(item=>item.series_id===scriptFilter):row.memberships[0];return <article className={take?"video-library-card ready":"video-library-card missing"} key={row.production_id+":"+row.segment_id}>
        <div className="video-library-media">{take?<video controls playsInline preload="metadata" src={take.scene_video_url||take.video_url||""}/>:<div><Video size={28}/><span>{t("尚无采用视频","No adopted video","採用映像なし","尚無採用影片")}</span></div>}{take&&<label className={selectedIds.includes(take.id)?"video-library-check selected":"video-library-check"}><input type="checkbox" checked={selectedIds.includes(take.id)} onChange={()=>toggle(take.id)}/><span><Check size={14}/>{t("选择","Select","選択","選擇")}</span></label>}</div>
        <div className="video-library-card-copy"><div className="video-library-card-path">{membership?<><span>{membership.series_title}</span><b>E{membership.episode_index}</b><em>{membership.episode_title}</em></>:<span>{t("独立项目","Standalone project","単独作品","獨立專案")}</span>}</div><h3>{String(row.segment_index).padStart(2,"0")} · {row.segment_title}</h3><p>{row.production_title}</p><div className="video-library-meta"><span>{durationLabel(take)}</span>{take?.seed!=null&&<span>Seed {take.seed}</span>}<span>{row.project_status}</span></div></div>
        <footer><button onClick={()=>void onOpenProduction(row.production_id)}><FolderOpen size={15}/>{t("进入项目","Open project","作品を開く","進入專案")}</button>{take?.download_url&&<a href={take.download_url}><Download size={15}/>{t("另存","Save copy","コピー保存","另存")}</a>}</footer>
      </article>})}</div>}
    <div className="video-library-paging"><span>{filtered.length} {t("条记录","items","件","條記錄")}</span><button disabled={page===0} onClick={()=>setPage(value=>Math.max(0,value-1))}>{t("上一页","Previous","前へ","上一頁")}</button><b>{page+1}/{pages}</b><button disabled={page>=pages-1} onClick={()=>setPage(value=>Math.min(pages-1,value+1))}>{t("下一页","Next","次へ","下一頁")}</button></div>
    {selectionFilm?.final_ready&&selectionFilm.final_url&&<div className="video-library-selection-film"><div><span className="eyebrow">SELECTED VIDEO ASSEMBLY</span><h3>{t("选中视频合片","Selected video assembly","選択映像の結合","選中影片合片")}</h3><p>{t("已按当前筛选列表顺序保存到本机。原视频与项目均未改动。","Saved locally in the current filtered-list order. Source videos and projects remain untouched.","現在の絞り込み一覧順でローカル保存しました。元映像と作品は変更していません。","已按目前篩選列表順序儲存到本機。原影片與專案均未改動。")}</p><code title={selectionFilm.file_path||""}>{selectionFilm.file_path}</code><div><button onClick={()=>void openSelectionFolder()}><FolderOpen size={16}/>{t("打开文件位置","Open file location","保存場所を開く","開啟檔案位置")}</button>{selectionFilm.download_url&&<a href={selectionFilm.download_url}><Download size={16}/>{t("另存副本","Download a copy","コピーを保存","另存副本")}</a>}</div></div><video controls playsInline preload="metadata" src={selectionFilm.final_url}/></div>}
  </section>;
}
