import React, { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, BookOpen, ChevronDown, ChevronRight, ChevronUp, Download, Film, FolderOpen, Plus, RefreshCw, Save, Trash2 } from "lucide-react";
import { api, apiPatch } from "./api";
import { useUiLanguage } from "./i18n";
import "./SeriesWorkspace.css";

type Summary = {id:string;title:string};
type CardSet = {id:string;name:string;card_count:number};
type Episode = {index:number;title:string;production_ids:string[]};
type Series = {id:string;title:string;card_collection_id:string|null;episodes:Episode[];updated_at:number};
type SeriesSummary = {id:string;title:string;episode_count:number;part_count:number;updated_at:number};
type PartOutput = {production_id:string;title:string;ready_count:number;segment_count:number;all_ready:boolean;final_ready:boolean;missing?:boolean};
type EpisodeOutput = {index:number;title:string;parts:PartOutput[];part_count:number;ready_count:number;all_ready:boolean;film_ready:boolean;film_url:string|null;file_path:string|null;folder_path:string|null};
type SeriesOutputs = {all_ready:boolean;final_ready:boolean;episodes:EpisodeOutput[];final_url:string|null;download_url:string|null;file_path:string|null;folder_path:string|null};
type SelectionOutput = {episode_indices:number[];all_ready:boolean;final_ready:boolean;final_url:string|null;download_url:string|null;file_path:string|null;folder_path:string|null};

export default function SeriesWorkspace({projects,collections,currentProductionId,onOpenProduction}:{
  projects:Summary[];collections:CardSet[];currentProductionId?:string;onOpenProduction:(id:string)=>Promise<void>;
}){
  const {text:uiText}=useUiLanguage();
  const t=(zh:string,en:string,ja:string,tw=zh)=>uiText({"zh-CN":zh,"zh-TW":tw,en,ja});
  const [items,setItems]=useState<SeriesSummary[]>([]);
  const [series,setSeries]=useState<Series|null>(null);
  const [outputs,setOutputs]=useState<SeriesOutputs|null>(null);
  const [selectedEpisodes,setSelectedEpisodes]=useState<number[]>([]);
  const [selection,setSelection]=useState<SelectionOutput|null>(null);
  const [expandedEpisodes,setExpandedEpisodes]=useState<number[]>([]);
  const [newTitle,setNewTitle]=useState("");
  const [newCount,setNewCount]=useState(10);
  const [newCollection,setNewCollection]=useState("");
  const [dirty,setDirty]=useState(false);
  const [busy,setBusy]=useState("");
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const assigned=useMemo(()=>new Set(series?.episodes.flatMap(ep=>ep.production_ids)||[]),[series]);
  const selectedKey=selectedEpisodes.join(",");
  const refreshList=async()=>setItems(await api("/series") as SeriesSummary[]);
  const refreshOutputs=async(id:string)=>setOutputs(await api("/series/"+id+"/outputs") as SeriesOutputs);
  useEffect(()=>{void refreshList().catch(e=>setError((e as Error).message));},[]);
  useEffect(()=>{
    if(!series?.id||!selectedKey||dirty){setSelection(null);return;}
    let active=true;
    void api("/series/"+series.id+"/film/selection/status?episodes="+selectedKey).then(value=>{if(active)setSelection(value as SelectionOutput)}).catch(()=>{if(active)setSelection(null)});
    return ()=>{active=false};
  },[series?.id,selectedKey,dirty,outputs]);
  const run=async(label:string,action:()=>Promise<void>)=>{
    if(busy)return;setBusy(label);setError("");setNotice("");
    try{await action();}catch(e){setError((e as Error).message);}finally{setBusy("");}
  };
  const open=(id:string)=>void run(t("打开剧本","Open script","脚本を開く","開啟劇本"),async()=>{
    if(!id){setSeries(null);setOutputs(null);setSelection(null);setSelectedEpisodes([]);setExpandedEpisodes([]);setDirty(false);return;}
    const value=await api("/series/"+id) as Series;setSeries(value);setSelection(null);setSelectedEpisodes([]);setExpandedEpisodes(value.episodes.filter(ep=>ep.production_ids.length>0).map(ep=>ep.index));setDirty(false);await refreshOutputs(id);
  });
  const create=()=>void run(t("创建剧本","Create script","脚本を作成","建立劇本"),async()=>{
    const value=await api("/series",{title:newTitle.trim(),episode_count:newCount,card_collection_id:newCollection||null}) as Series;
    setSeries(value);setSelection(null);setSelectedEpisodes([]);setExpandedEpisodes([1]);setDirty(false);setNewTitle("");await refreshList();await refreshOutputs(value.id);
    setNotice(t("剧本已建立。把各集对应的片段按顺序加入，再保存编排。","Script created. Add each episode's parts in order, then save.","脚本を作成しました。各話のパートを順番に追加して保存してください。","劇本已建立。把各集對應的片段依序加入，再儲存編排。"));
  });
  const change=(edit:(value:Series)=>Series)=>{setSeries(value=>value?edit(value):value);setDirty(true);};
  const updateEpisode=(index:number,edit:(value:Episode)=>Episode)=>change(value=>({...value,episodes:value.episodes.map(ep=>ep.index===index?edit(ep):ep)}));
  const addPart=(index:number,id:string)=>{
    if(!id||assigned.has(id))return;
    updateEpisode(index,ep=>({...ep,production_ids:[...ep.production_ids,id]}));
  };
  const movePart=(index:number,position:number,offset:number)=>updateEpisode(index,ep=>{
    const ids=[...ep.production_ids],next=position+offset;
    if(next<0||next>=ids.length)return ep;
    [ids[position],ids[next]]=[ids[next],ids[position]];
    return {...ep,production_ids:ids};
  });
  const save=()=>{if(!series)return;void run(t("保存剧本编排","Save script order","脚本構成を保存","儲存劇本編排"),async()=>{
    const value=await apiPatch("/series/"+series.id,{title:series.title,card_collection_id:series.card_collection_id,episodes:series.episodes}) as Series;
    setSeries(value);setDirty(false);await refreshList();await refreshOutputs(value.id);
    setNotice(t("集数、顺序和共享卡组已保存；项目和原视频均未改动。","Episode order and shared set saved; projects and original videos were not changed.","話数・順序・共有カードを保存しました。元の作品と映像は変更していません。","集數、順序和共享卡組已儲存；專案和原影片均未改動。"));
  });};
  const assemble=()=>{if(!series)return;void run(t("合并全剧","Assembling full script","全編を結合中","合併全劇"),async()=>{
    const value=await api("/series/"+series.id+"/film",{},undefined,"POST",{timeoutMs:1800000}) as SeriesOutputs;
    setOutputs(value);setNotice(t("全剧成片已保存在本机；下方可直接打开文件位置。","The full film is saved locally. Open its folder below.","全編映像をローカルに保存しました。下から保存場所を開けます。","全劇成片已儲存在本機；下方可直接開啟檔案位置。"));
  });};
  const assembleEpisode=(index:number)=>{if(!series)return;void run(t("合并单集","Assembling episode","この話を結合中","合併單集"),async()=>{
    const value=await api("/series/"+series.id+"/film/episode/"+index,{},undefined,"POST",{timeoutMs:1800000}) as SeriesOutputs;
    setOutputs(value);setNotice(t("第 "+index+" 集已保存在本机。","Episode "+index+" is saved locally.","第"+index+"話をローカルに保存しました。","第 "+index+" 集已儲存在本機。"));
  });};
  const assembleSelection=()=>{if(!series||selectedEpisodes.length<2)return;void run(t("合并选中剧集","Assembling selected episodes","選択した話を結合中","合併選中劇集"),async()=>{
    const value=await api("/series/"+series.id+"/film/selection",{episode_indices:selectedEpisodes},undefined,"POST",{timeoutMs:1800000}) as SelectionOutput;
    setSelection(value);setNotice(t("选中的集数已按剧本顺序合并并保存在本机。","Selected episodes were assembled in script order and saved locally.","選択した話を脚本順に結合し、ローカルへ保存しました。","選中的集數已按劇本順序合併並儲存在本機。"));
  });};
  const toggleEpisode=(index:number)=>setSelectedEpisodes(values=>values.includes(index)?values.filter(value=>value!==index):[...values,index].sort((a,b)=>a-b));
  const toggleExpandedEpisode=(index:number)=>setExpandedEpisodes(values=>values.includes(index)?values.filter(value=>value!==index):[...values,index]);
  const openFilmFolder=(kind:"full"|"episode"|"selection",index?:number)=>{if(!series)return;void run(t("打开文件位置","Opening film folder","保存場所を開く","開啟檔案位置"),async()=>{
    const path="/series/"+series.id+"/film"+(kind==="episode"?"/episode/"+index:kind==="selection"?"/selection":"")+"/open";
    const result=await api(path,kind==="selection"?{episode_indices:selectedEpisodes}:{},undefined,"POST") as {path:string};
    setNotice(t("已在资源管理器打开：","Opened in Explorer: ","エクスプローラーで開きました：","已在檔案總管開啟：")+result.path);
  });};
  const archive=()=>{if(!series||!window.confirm(t("归档这部剧本的编排？关联片段、角色资料和视频都会保留。","Archive this script arrangement? Parts, character records and videos remain untouched.","この脚本構成をアーカイブしますか？パート・人物資料・映像は残ります。","封存這部劇本的編排？關聯片段、角色資料與影片都會保留。")))return;
    void run(t("归档剧本","Archive script","脚本をアーカイブ","封存劇本"),async()=>{
      await api("/series/"+series.id,undefined,undefined,"DELETE");setSeries(null);setOutputs(null);setSelection(null);setSelectedEpisodes([]);setExpandedEpisodes([]);setDirty(false);await refreshList();
    });};

  return <section className="production-series-manager card">
    <div className="section-title production-projects-title"><div><span className="eyebrow">SCRIPT · EPISODES · FINAL FILM</span><h2><Film size={21}/>{t("剧本管理","Script management","脚本管理","劇本管理")}</h2><p>{t("先按集编排项目，再按需合并单集、选中集或全剧。成片保存在本机，直接打开文件位置即可。","Arrange parts by episode, then assemble one episode, selected episodes or the full script. Films are saved locally; open their folders directly.","作品を話ごとに並べ、単話・選択話・全編を必要に応じて結合します。映像はローカルに保存され、保存場所を直接開けます。","先按集編排專案，再按需合併單集、選中集或全劇。成片儲存在本機，可直接開啟檔案位置。")}</p></div><button onClick={()=>void refreshList()}><RefreshCw size={16}/>{t("刷新剧本","Refresh scripts","脚本を更新","重新整理劇本")}</button></div>
    {(error||notice)&&<div className={error?"production-alert error":"production-alert"}>{error||notice}</div>}
    <div className="series-toolbar"><label>{t("打开剧本","Open script","脚本を開く","開啟劇本")}<select value={series?.id||""} onChange={e=>open(e.target.value)}><option value="">{t("选择剧本…","Select a script…","脚本を選択…","選擇劇本…")}</option>{items.map(item=><option key={item.id} value={item.id}>{item.title} · {item.episode_count} {t("集","episodes","話","集")} · {item.part_count} {t("个片段","parts","パート","個片段")}</option>)}</select></label><span>{items.length} {t("部剧本","scripts","本の脚本","部劇本")}</span></div>
    {!series&&<div className="series-create"><h3>{t("新建剧本","Create a script","脚本を作成","新建劇本")}</h3><div className="series-create-grid"><label>{t("剧本名称","Script title","脚本名","劇本名稱")}<input value={newTitle} onChange={e=>setNewTitle(e.target.value)} placeholder={t("例如：星图之外","e.g. Beyond the Star Map","例：星図の向こう","例如：星圖之外")}/></label><label>{t("计划集数","Planned episodes","予定話数","計劃集數")}<input type="number" min={1} max={100} value={newCount} onChange={e=>setNewCount(Math.max(1,Math.min(100,Number(e.target.value)||1)))}/></label><label>{t("共享角色资料","Shared character records","共有キャラクター資料","共享角色資料")}<select value={newCollection} onChange={e=>setNewCollection(e.target.value)}><option value="">{t("暂不绑定","Choose later","後で選択","暫不綁定")}</option>{collections.map(item=><option key={item.id} value={item.id}>{item.name} · {item.card_count}</option>)}</select></label></div><button className="primary" disabled={!newTitle.trim()||!!busy} onClick={create}><Plus size={16}/>{t("建立剧本","Create script","脚本を作成","建立劇本")}</button></div>}
    {series&&<><div className="series-heading"><label>{t("剧本名称","Script title","脚本名","劇本名稱")}<input value={series.title} onChange={e=>change(value=>({...value,title:e.target.value}))}/></label><label>{t("共用角色资料","Shared character records","共有キャラクター資料","共用角色資料")}<select value={series.card_collection_id||""} onChange={e=>change(value=>({...value,card_collection_id:e.target.value||null}))}><option value="">{t("未指定","Not assigned","未指定","未指定")}</option>{collections.map(item=><option key={item.id} value={item.id}>{item.name} · {item.card_count} {t("张卡","cards","枚","張卡")}</option>)}</select></label></div>
      <p className="series-help"><BookOpen size={16}/>{t("绑定角色资料不会覆盖片段：先到“角色资料库”给各片段点“套用到当前片段”；新增卡后再点“同步到共享卡组”。","Linking character records never overwrites parts. In Character library, apply them to each part; sync any new cards explicitly.","人物資料を指定してもパートは上書きされません。「キャラクター資料庫」から各パートに適用し、新しいカードは明示的に同期してください。","綁定角色資料不會覆蓋片段：先到「角色資料庫」套用到各片段；新增卡後再同步到共享卡組。")}</p>
      <div className="series-actions"><button className="primary" disabled={!dirty||!!busy} onClick={save}><Save size={16}/>{t("保存剧本编排","Save script order","脚本構成を保存","儲存劇本編排")}</button><button disabled={dirty||!!busy} onClick={()=>void run(t("刷新成片状态","Refresh film status","映像状態を更新","重新整理成片狀態"),async()=>{await refreshOutputs(series.id)})}><RefreshCw size={16}/>{t("刷新成片状态","Refresh film status","映像状態を更新","重新整理成片狀態")}</button><span className="series-actions-spacer"/><button className="danger" disabled={!!busy} onClick={archive}><Trash2 size={16}/>{t("归档剧本","Archive script","脚本をアーカイブ","封存劇本")}</button></div>
      {dirty&&<p className="series-unsaved">{t("编排尚未保存；先保存，再刷新结果或合片。","Unsaved order. Save before checking readiness or assembling.","構成が未保存です。結果確認や結合の前に保存してください。","編排尚未儲存；先儲存，再檢查結果或合片。")}</p>}
      <div className="series-assembly-overview"><div><span className="eyebrow">ASSEMBLY DESK</span><h3>{t("成片工作台","Film assembly","映像編集デスク","成片工作台")}</h3><p>{t("可先完成已就绪的单集；不必等全剧跑完。合并只使用每个片段当前采用的视频。","Ready episodes can be assembled without waiting for the whole script. Only each part’s adopted takes are used.","完成した話から先に結合できます。各パートで採用した映像だけを使用します。","可先完成已就緒的單集；不用等全劇跑完。僅使用各片段目前採用的影片。")}</p></div><strong>{outputs?.episodes.filter(ep=>ep.all_ready).length||0}/{series.episodes.length} {t("集就绪","episodes ready","話準備完了","集就緒")}</strong></div>
      <div className="series-episodes">{series.episodes.map(ep=>{const output=outputs?.episodes.find(item=>item.index===ep.index);const expanded=expandedEpisodes.includes(ep.index);return <article key={ep.index} className={"series-episode "+(output?.all_ready?"is-ready":"is-pending")+(expanded?" expanded":" collapsed")}><header><span>{String(ep.index).padStart(2,"0")}</span><label>{t("本集标题","Episode title","話のタイトル","本集標題")}<input value={ep.title} onChange={e=>updateEpisode(ep.index,value=>({...value,title:e.target.value}))}/></label><em>{output?`${output.ready_count}/${output.part_count}`:"0/0"} {t("项目就绪","parts ready","作品準備完了","專案就緒")}</em><label className="series-select-episode"><input type="checkbox" checked={selectedEpisodes.includes(ep.index)} onChange={()=>toggleEpisode(ep.index)}/>{t("选择","Select","選択","選擇")}</label><button className="series-episode-toggle" aria-expanded={expanded} aria-label={expanded?t("收起本集","Collapse episode","この話を閉じる","收起本集"):t("展开本集","Expand episode","この話を開く","展開本集")} onClick={()=>toggleExpandedEpisode(ep.index)}>{expanded?<ChevronUp size={17}/>:<ChevronDown size={17}/>}</button></header>
        {expanded&&<div className="series-episode-body">
        <div className="series-parts">{ep.production_ids.map((id,position)=>{const project=projects.find(item=>item.id===id);const part=output?.parts.find(item=>item.production_id===id);return <div key={id} className="series-part"><span>{position+1}</span><button className="series-part-open" onClick={()=>void onOpenProduction(id)}><FolderOpen size={15}/>{project?.title||part?.title||id}<ChevronRight size={14}/></button><small>{part?.missing?t("项目已不在列表","Project missing","作品がありません","專案已不在列表"):part?.all_ready?t("视频已齐","Videos ready","映像準備完了","影片已齊"):part?`${part.ready_count}/${part.segment_count} ${t("段","clips","クリップ","段")}`:t("保存后检查","Check after save","保存後に確認","儲存後檢查")}</small><button title={t("上移","Move up","上へ","上移")} disabled={position===0} onClick={()=>movePart(ep.index,position,-1)}><ArrowUp size={14}/></button><button title={t("下移","Move down","下へ","下移")} disabled={position===ep.production_ids.length-1} onClick={()=>movePart(ep.index,position,1)}><ArrowDown size={14}/></button><button title={t("从本集移除，不删除项目","Remove from episode; keep project","話から外す・作品は保持","從本集移除，不刪專案")} onClick={()=>updateEpisode(ep.index,value=>({...value,production_ids:value.production_ids.filter(x=>x!==id)}))}><Trash2 size={14}/></button></div>})}</div>
        <label className="series-add-part">{t("加入剧集 / 片段","Add episode / clip part","エピソード／クリップを追加","加入劇集／片段")}<select value="" onChange={e=>addPart(ep.index,e.target.value)}><option value="">{t("选择现有片段…","Choose an existing part…","既存のパートを選択…","選擇現有片段…")}</option>{projects.filter(item=>!assigned.has(item.id)).map(item=><option key={item.id} value={item.id}>{item.title}</option>)}</select></label>{currentProductionId&&!assigned.has(currentProductionId)&&<button onClick={()=>addPart(ep.index,currentProductionId)}><Plus size={14}/>{t("加入当前片段","Add current part","現在のパートを追加","加入目前片段")}</button>}
        <div className="series-episode-actions"><span>{t("本集的项目按上方顺序连接。","Parts join in the order above.","作品は上の順に結合します。","本集的專案按上方順序連接。")}</span><button disabled={dirty||!!busy||!output?.all_ready} onClick={()=>assembleEpisode(ep.index)}><Film size={15}/>{output?.film_ready?t("已合并本集","Episode saved","この話を保存済み","已合併本集"):t("合并本集","Assemble episode","この話を結合","合併本集")}</button></div>
        {output?.film_ready&&output.film_url&&<div className="series-episode-film"><video controls playsInline preload="metadata" src={output.film_url}/><div className="series-film-location"><strong>{t("已保存到本机","Saved locally","ローカルに保存済み","已儲存到本機")}</strong><code title={output.file_path||""}>{output.file_path}</code><div className="series-film-links"><button onClick={()=>openFilmFolder("episode",ep.index)} disabled={!!busy}><FolderOpen size={15}/>{t("打开文件位置","Open file location","保存場所を開く","開啟檔案位置")}</button><a href={output.film_url+"&download=1"}><Download size={15}/>{t("另存副本","Download a copy","コピーを保存","另存副本")}</a></div></div></div>}
        </div>}
      </article>})}</div>
      <div className="series-export-grid"><div className="series-final"><div><span className="eyebrow">SELECTED EPISODES</span><strong>{t("选择合并","Selected merge","選択した話を結合","選擇合併")}</strong><p>{t("勾选至少两集，按剧本顺序合并；未选中的集不影响结果。","Select at least two episodes. They join in script order; unselected episodes do not block the result.","2話以上を選ぶと脚本順で結合します。選択外の話は影響しません。","勾選至少兩集，按劇本順序合併；未選中的集不影響結果。")}</p><span className="series-selection-count">{selectedEpisodes.length} {t("集已选","selected","話を選択","集已選")}</span></div><button disabled={dirty||!!busy||selectedEpisodes.length<2||!selection?.all_ready} onClick={assembleSelection}><Film size={16}/>{t("合并选中集","Assemble selected","選択した話を結合","合併選中集")}</button>{selection?.final_ready&&selection.final_url&&<div className="series-export-result"><video controls playsInline preload="metadata" src={selection.final_url}/><div className="series-film-location"><strong>{t("选集合片已保存","Selected film saved","選択映像を保存済み","選集合片已儲存")}</strong><code title={selection.file_path||""}>{selection.file_path}</code><div className="series-film-links"><button disabled={!!busy} onClick={()=>openFilmFolder("selection")}><FolderOpen size={15}/>{t("打开文件位置","Open file location","保存場所を開く","開啟檔案位置")}</button><a href={selection.download_url||selection.final_url}><Download size={15}/>{t("另存副本","Download a copy","コピーを保存","另存副本")}</a></div></div></div>}</div>
      <div className="series-final"><div><span className="eyebrow">COMPLETE SERIES</span><strong>{t("全剧最终成片","Complete series film","シリーズ完成映像","全劇最終成片")}</strong><p>{outputs?.all_ready?t("全部剧集的视频已齐，可以合并全剧。","All episodes are ready for the full film.","全話の映像が揃い、全編を結合できます。","全部劇集的影片已齊，可以合併全劇。") : t("必须等所有集齐备；缺片不会用别的项目补位。","Wait for every episode; missing clips are never silently substituted.","全話の完成が必要です。不足部分を別作品で代用しません。","必須等所有集齊備；缺片不會用別的專案補位。")}</p></div><button disabled={dirty||!!busy||!outputs?.all_ready} onClick={assemble}><Film size={16}/>{t("合并全剧","Assemble full series","全編を結合","合併全劇")}</button>{outputs?.final_ready&&outputs.final_url&&<div className="series-export-result"><video controls playsInline preload="metadata" src={outputs.final_url}/><div className="series-film-location"><strong>{t("全剧成片已保存","Full film saved","全編映像を保存済み","全劇成片已儲存")}</strong><code title={outputs.file_path||""}>{outputs.file_path}</code><div className="series-film-links"><button disabled={!!busy} onClick={()=>openFilmFolder("full")}><FolderOpen size={15}/>{t("打开文件位置","Open file location","保存場所を開く","開啟檔案位置")}</button><a href={outputs.download_url||outputs.final_url}><Download size={16}/>{t("另存副本","Download a copy","コピーを保存","另存副本")}</a></div></div></div>}</div></div>
    </>}
  </section>;
}
