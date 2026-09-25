import { useEffect, useRef, useState } from 'react';
import { api, downloadText } from './api';
import { useUiLanguage } from './i18n';
import type { Project } from './model';
import './ComfyPanel.css';

type ReferenceBinding = {asset_id:string;token:string;name:string;role:string;semantic_role?:string;source?:string};
type Props = { project:Project; prompt:string; references?:ReferenceBinding[]; ready:boolean; busy:boolean; onSettings:(settings:any)=>void; onContinuationSource?:(value:string)=>void; sendEmbedded?:(ticket:string)=>boolean; embedded?:boolean };
const defaults = { resolution:'0.3', quality:'fast', steps:'auto', seed:9072026 };
export default function ComfyPanel({project:p,prompt,references=[],ready,busy,onSettings,onContinuationSource,sendEmbedded,embedded}:Props) {
  const {text:t}=useUiLanguage();
  const tr=(zh:string,en:string,ja:string,tw=zh)=>t({'zh-CN':zh,'zh-TW':tw,en,ja});
  const [catalog,setCatalog]=useState<any>(null), [sending,setSending]=useState(false), [error,setError]=useState(''),[sent,setSent]=useState<any>(null);
  const [overlapNotice,setOverlapNotice]=useState('');
  const catalogRequest=useRef(0);
  const current=useRef({p,prompt}); current.current={p,prompt};
  const draftKey=JSON.stringify(p);
  const config={...defaults,...p.comfy_render};
  const sourceValue=String(config.continuation_source??'').replaceAll('\\','/').replace(/^output::/,'');
  const sourceOptions=(catalog?.mmh3_sources??[]).map((item:any)=>({...item,value:String(item.value??item.selector??'').replaceAll('\\','/').replace(/^output::/,'')}));
  const refresh=async()=>{
    const request=++catalogRequest.current, projectId=p.id, source=sourceValue;
    const stillCurrent=()=>request===catalogRequest.current&&current.current.p.id===projectId&&
      String(current.current.p.comfy_render?.continuation_source??'').replaceAll('\\','/').replace(/^output::/,'')===source;
    try {const next=await api('/comfy/options');if(stillCurrent()){setCatalog(next);setError('');}}
    catch(e){if(stillCurrent())setError((e as Error).message);}
  };
  useEffect(()=>{setCatalog(null);void refresh();return()=>{catalogRequest.current++;};},[p.id,sourceValue]);
  useEffect(()=>{setSent(null);setError('');},[p.id]);
  useEffect(()=>{setSent(null);},[draftKey,prompt]);
  const change=(key:string,value:any)=>{onSettings({...config,[key]:value});setSent(null);};
  const recipe=p.mode==='ref2va'?'minimax_h3_ref2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors':'minimax_h3_fl2v_turbo_4step_v0.1_768p_sla_comfyui_bf16.safetensors';
  const loras:any[]=config.loras??[{name:recipe,strength:1,enabled:true}];
  const names:string[]=(catalog?.available_loras??catalog?.loras??[]).map((x:any)=>typeof x==='string'?x:x.name).filter(Boolean);
  const editLora=(index:number,key:string,value:any)=>change('loras',loras.map((l,i)=>i===index?{...l,[key]:value}:l));
  const imported=p.comfy_source?.generation_settings;
  const generatedFrames=Math.ceil((p.duration*24-5)/17)*17+5;
  const overlap=config.continuation_overlap_frames??39;
  const overlapChoices:number[]=(catalog?.mmh3_overlap_frames??[39]).filter((frames:number)=>frames<generatedFrames);
  const overlapChoicesKey=overlapChoices.join(',');
  const overlapValid=overlapChoices.includes(overlap);
  const missingSource=!!sourceValue&&!!catalog&&!sourceOptions.some((item:any)=>item.value===sourceValue);
  const continuationReady=!config.continuation_source||(!!catalog&&!missingSource&&overlapValid);
  const pictureReferences=references.filter(reference=>reference.token.startsWith('<Picture'));
  const semanticLabel=(role:string)=>({
    character:t({'zh-CN':'角色身份','zh-TW':'角色身分',en:'character identity',ja:'人物識別'}),
    face:t({'zh-CN':'面部身份','zh-TW':'臉部身分',en:'facial identity',ja:'顔の識別'}),
    wardrobe:t({'zh-CN':'角色服装','zh-TW':'角色服裝',en:'wardrobe',ja:'衣装'}),
    object:t({'zh-CN':'道具外观','zh-TW':'道具外觀',en:'prop appearance',ja:'小道具の外観'}),
    background:t({'zh-CN':'环境布局','zh-TW':'環境配置',en:'environment layout',ja:'環境・配置'}),
    style:t({'zh-CN':'视觉风格','zh-TW':'視覺風格',en:'visual style',ja:'視覚スタイル'}),
    palette:t({'zh-CN':'色彩','zh-TW':'色彩',en:'color palette',ja:'色彩'}),
    pose:t({'zh-CN':'姿势构图','zh-TW':'姿勢構圖',en:'pose / composition',ja:'ポーズ・構図'}),
    other:t({'zh-CN':'画面内容','zh-TW':'畫面內容',en:'visible content',ja:'画面内容'}),
  } as Record<string,string>)[role]||role;
  useEffect(()=>{
    if(catalog&&config.continuation_source&&!overlapValid&&overlapChoices.length){
      const next=overlapChoices.includes(39)?39:overlapChoices[0];
      onSettings({...config,continuation_overlap_frames:next});
      setOverlapNotice(tr(`已把衔接上下文调整为 ${(next/24).toFixed(3)} 秒，以适配本段。`,`Context adjusted to ${(next/24).toFixed(3)} seconds so it fits this clip.`,`このクリップに合わせ、継続コンテキストを ${(next/24).toFixed(3)} 秒に調整しました。`,`已把銜接上下文調整為 ${(next/24).toFixed(3)} 秒，以適配本段。`));
      setSent(null);
    }
  },[p.id,config.continuation_source,overlap,generatedFrames,overlapChoicesKey,!!catalog]);
  const prepare=async()=>{
    if(!ready||busy||sending||!continuationReady)return;
    // Open during the click so browser popup protection does not swallow the handoff.
    const tab=embedded?null:window.open('about:blank','_blank');
    if(tab){tab.opener=null;tab.document.title='Preparing ComfyUI workflow';tab.document.body.textContent='Preparing your prompt, photos and render settings…';}
    setSending(true);setError('');setSent(null);
    const draft=JSON.stringify(current.current.p),text=current.current.prompt;
    try {
      const result=await api('/comfy/prepare',{project:current.current.p,prompt:text});
      if(JSON.stringify(current.current.p)!==draft||current.current.prompt!==text)throw new Error('The project changed during transfer. Review it and send the updated version.');
      setSent({...result,draftKey:draft,prompt:text});
      if(embedded&&sendEmbedded?.(result.ticket))return;
      if(tab)tab.location.replace(result.open_url);
    }catch(e){tab?.close();setError(e instanceof Error?e.message:String(e));}
    finally{setSending(false);}
  };
  const download=async()=>{try{const value=await api('/comfy/transfers/'+sent.ticket);downloadText('H3-Prompt-Studio-workflow.json',JSON.stringify(value.workflow,null,2),'application/json');}catch(e){setError((e as Error).message);}};
  return <section className="comfy-panel" aria-label={tr("ComfyUI 视频设置","ComfyUI video settings","ComfyUI映像設定","ComfyUI 影片設定")}>
    <div className="comfy-heading"><div><h3>{tr("在 ComfyUI 中生成视频","Make the video in ComfyUI","ComfyUIで映像を生成","在 ComfyUI 中生成影片")}</h3><p>{tr("将提示词、图片和设置发送过去；检查新工作流后点击运行。","Send this prompt with its photos and settings. Review the new workflow, then press Run.","プロンプト・画像・設定を送り、新しいワークフローを確認して実行します。","將提示詞、圖片和設定傳送過去；檢查新工作流後點擊執行。")}</p></div><span>{p.duration}s · {config.resolution} MP</span></div>
    <details><summary>{tr("视频设置与 LoRA","Video settings & LoRAs","映像設定・LoRA","影片設定與 LoRA")}</summary>
      <div className="comfy-fields">
        <label><span>{tr("分辨率","Resolution","解像度","解析度")}</span><select aria-label="Comfy resolution" value={config.resolution} onChange={e=>change('resolution',e.target.value)}>{['0.3','0.5','0.7','1.0'].map(v=><option key={v} value={v}>{v} MP{v==='0.3'?tr(' · 快速预览',' · quick preview','・高速プレビュー',' · 快速預覽'):''}</option>)}</select></label>
        <label><span>{tr("配方","Recipe","レシピ","配方")}</span><select aria-label="Comfy recipe" value={config.quality} onChange={e=>change('quality',e.target.value)}><option value="fast">{tr("已验证快速配方","Fast tested recipe","検証済み高速レシピ","已驗證快速配方")}</option><option value="detailed">{tr("双倍步数 · 对比质量","Double steps · compare quality","倍ステップ・品質比較","雙倍步數 · 對比品質")}</option></select></label>
        <label><span>{tr("采样步数","Sampling steps","サンプリングステップ","採樣步數")}</span><select aria-label="Comfy steps" value={config.steps} onChange={e=>change('steps',e.target.value==='auto'?'auto':Number(e.target.value))}><option value="auto">{tr("使用配方默认值","Recipe default","レシピ既定値","使用配方預設值")}</option>{[4,8,16].map(v=><option key={v} value={v}>{v} {tr("步","steps","ステップ","步")}</option>)}</select></label>
        <label><span>{tr("随机种子","Seed","Seed","隨機種子")}</span><input aria-label="Comfy seed" type="number" min={0} max={9007199254740991} step={1} value={config.seed} onChange={e=>change('seed',Number(e.target.value))}/></label>
      </div>
      <p className="comfy-hint">{tr("视频尺寸跟随所选画幅。H3 文本编码器保持 Qwen 32B Heretic；增加步数或额外 LoRA 会影响质量和速度。","Video size follows your selected shape. The H3 text encoder stays Qwen 32B Heretic. More steps or extra LoRAs can change quality and speed.","映像サイズは選択した画面比率に従います。H3テキストエンコーダーはQwen 32B Hereticのままです。ステップ数や追加LoRAは品質と速度に影響します。","影片尺寸跟隨所選畫幅。H3 文字編碼器保持 Qwen 32B Heretic；增加步數或額外 LoRA 會影響品質和速度。")}</p>
      <div className="comfy-lora-heading"><strong>{tr("LoRA · 从上到下依次应用","LoRAs · applied from top to bottom","LoRA・上から順に適用","LoRA · 從上到下依次套用")}</strong><button type="button" onClick={refresh}>{tr("刷新已安装文件","Refresh installed files","インストール済みファイルを更新","重新整理已安裝檔案")}</button></div>
      <div className="comfy-loras">{loras.map((l,i)=><div key={i} className="comfy-lora">
        <input aria-label={`Enable LoRA ${i+1}`} type="checkbox" checked={l.enabled!==false} onChange={e=>editLora(i,'enabled',e.target.checked)}/>
        <label><span>LoRA {i+1}</span><select aria-label={`LoRA ${i+1} file`} title={l.name} value={l.name} onChange={e=>editLora(i,'name',e.target.value)}><option value="">{tr("选择已安装的 LoRA…","Choose an installed LoRA…","インストール済みLoRAを選択…","選擇已安裝的 LoRA…")}</option>{[...new Set([l.name,...names].filter(Boolean))].map(name=><option key={name} value={name}>{name}</option>)}</select></label>
        <label className="comfy-strength"><span>{tr("强度","Strength","強度","強度")}</span><input aria-label={`LoRA ${i+1} strength`} type="number" min={-4} max={4} step={0.05} value={l.strength} onChange={e=>editLora(i,'strength',Number(e.target.value))}/></label>
        <button type="button" aria-label={`Move LoRA ${i+1} up`} disabled={i===0} onClick={()=>{const next=[...loras];[next[i-1],next[i]]=[next[i],next[i-1]];change('loras',next);}}>↑</button>
        <button type="button" aria-label={`Remove LoRA ${i+1}`} onClick={()=>change('loras',loras.filter((_,j)=>j!==i))}>×</button>
      </div>)}</div>
      <div className="comfy-actions"><button type="button" disabled={loras.length>=8} onClick={()=>change('loras',[...loras,{name:'',strength:1,enabled:true}])}>{tr("+ 添加 LoRA","+ Add another LoRA","+ LoRAを追加","+ 新增 LoRA")}</button><button type="button" onClick={()=>change('loras',undefined)}>{tr("恢复配方 LoRA","Reset to recipe LoRA","レシピLoRAに戻す","恢復配方 LoRA")}</button><span className="comfy-hint">{loras.length}/8 {tr("行","rows","行","列")}</span></div>
      <p className="comfy-hint">{tr("请选择与当前 H3 模型匹配的 LoRA；其他模型家族的文件不一定兼容。配方已包含速度 LoRA，可在额外行添加风格或角色 LoRA。","Choose LoRAs made for your H3 model. Installed files from other model families are not necessarily compatible. The recipe includes its speed LoRA; add style or character LoRAs as extra rows.","現在のH3モデル用LoRAを選んでください。他モデル系統のファイルは互換とは限りません。レシピには速度LoRAが含まれ、追加行にスタイルや人物LoRAを設定できます。","請選擇與目前 H3 模型相符的 LoRA；其他模型家族的檔案不一定相容。配方已包含速度 LoRA，可在額外列新增風格或角色 LoRA。")}</p>
      {imported&&<details className="comfy-imported"><summary>{tr("从 ComfyUI 接收的设置","Settings received from ComfyUI","ComfyUIから受信した設定","從 ComfyUI 接收的設定")}</summary><p>{imported.width} × {imported.height} · {imported.summary?.steps??'—'} {tr("步","steps","ステップ","步")} · seed {imported.summary?.seed??'—'}</p>{imported.loras?.map((l:any,i:number)=><p key={i}>{i+1}. {l.name} · {tr("强度","strength","強度","強度")} {l.strength_model}{l.enabled===false?tr(' · 已禁用',' · disabled','・無効',' · 已停用'):''}</p>)}<p className="comfy-hint">{tr("这是所选工作流发回的设置快照；上面的选项控制下一份新工作流。","This is the snapshot sent from the selected workflow. The settings above control the new workflow.","選択したワークフローから届いた設定スナップショットです。上の設定が次のワークフローを制御します。","這是所選工作流傳回的設定快照；上面的選項控制下一份新工作流。")}</p><button type="button" onClick={()=>{onSettings({...config,...(Number.isInteger(imported.summary?.seed)?{seed:imported.summary.seed}:{}),...([4,8,16].includes(imported.summary?.steps)?{steps:imported.summary.steps}:{}),...(imported.loras?.length?{loras:imported.loras.map((l:any)=>({name:l.name,strength:l.strength_model??1,enabled:l.enabled!==false}))}:{})});setSent(null);}}>{tr("采用它的种子、支持步数和 LoRA","Use its seed, supported steps & LoRAs","Seed・対応ステップ・LoRAを採用","採用它的種子、支援步數和 LoRA")}</button></details>}
      {catalog?.error&&<p className="comfy-hint">{catalog.error}</p>}
    </details>
    <details className="comfy-continuation"><summary>{tr("保存并延续实际视频运动","Save & continue actual video motion","実際の映像モーションを保存・継続","儲存並延續實際影片運動")}</summary>
      <p className="comfy-hint">{tr("MMH3 会在 MP4 旁保存工作文件，包含采样的视频/音频状态、参考图和 LoRA 设置。需要延续上一段的结尾运动时，在下方选择已保存片段。","MMH3 stores a working file alongside the MP4, with sampled video/audio state, references and LoRA settings. Choose a saved clip below when you want to carry its ending motion into this one.","MMH3はMP4と一緒に、サンプリング済み映像・音声状態、参照画像、LoRA設定を含む作業ファイルを保存します。前の終了モーションを引き継ぐ場合は下で保存済みクリップを選びます。","MMH3 會在 MP4 旁儲存工作檔案，包含取樣的影片／音訊狀態、參考圖和 LoRA 設定。需要延續上一段的結尾運動時，在下方選擇已儲存片段。")}</p>
      <label className="comfy-checkbox"><input type="checkbox" aria-label="Save continuation state" disabled={!catalog?.mmh3_save_available} checked={config.save_mmh3??!!catalog?.mmh3_save_available} onChange={e=>change('save_mmh3',e.target.checked)}/><span>{tr("随视频保存续写状态（.mmh3）","Save continuation state (.mmh3) with this video","映像と一緒に継続状態（.mmh3）を保存","隨影片儲存續寫狀態（.mmh3）")}</span></label>
      <label><span>{tr("从已保存片段续写 · 可选","Continue from a saved clip · optional","保存済みクリップから継続・任意","從已儲存片段續寫 · 可選")}</span><select aria-label="Continue from saved video" disabled={!catalog?.mmh3_continuation_available&&!sourceValue} value={sourceValue} onChange={e=>{setSent(null);setOverlapNotice('');onContinuationSource?onContinuationSource(e.target.value):change('continuation_source',e.target.value);}}><option value="">{tr("新视频 · 使用我的图片和故事","New video · use my photos and story","新規映像・画像と物語を使う","新影片 · 使用我的圖片和故事")}</option>{sourceValue&&!sourceOptions.some((item:any)=>item.value===sourceValue)&&<option value={sourceValue}>{catalog?tr('保存片段不可用','Unavailable saved clip','保存クリップは利用不可','儲存片段不可用'):tr('正在检查保存片段','Checking saved clip','保存クリップを確認中','正在檢查儲存片段')} · {sourceValue}</option>}{sourceOptions.map((item:any)=><option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
      {missingSource&&<p className="comfy-error" role="alert">{tr("选中的保存片段不可用。请刷新列表、选择其它文件，或改为“新视频”。","The selected saved clip is unavailable. Refresh saved clips, choose another file, or choose New video.","選択した保存クリップは利用できません。一覧を更新して別のファイルを選ぶか、「新規映像」を選択してください。","選取的儲存片段不可用。請重新整理清單、選擇其他檔案，或改為「新影片」。")}</p>}
      <button type="button" onClick={refresh}>{tr("刷新已保存片段","Refresh saved clips","保存クリップを更新","重新整理已儲存片段")}</button>
      {!catalog?.mmh3_available&&<p className="comfy-hint">{catalog?.mmh3_note||tr('打开 ComfyUI Desktop 后刷新，以检测 MMH3 节点。','Open ComfyUI Desktop and refresh to detect the MMH3 nodes.','ComfyUI Desktopを開いて更新し、MMH3ノードを検出してください。','開啟 ComfyUI Desktop 後重新整理，以偵測 MMH3 節點。')}</p>}
      {catalog?.mmh3_available&&!catalog?.mmh3_sources?.length&&<p className="comfy-hint">{tr("首次生成时开启状态保存，完成后刷新，即可在这里选择工作文件。旧 MP4 本身不包含采样运动状态。","After your first render with state saving enabled, refresh to choose its working file here. An older MP4 alone does not contain the sampled motion state.","初回生成時に状態保存を有効にし、完了後に更新すると作業ファイルを選べます。古いMP4だけにはサンプリング済みモーション状態がありません。","首次生成時開啟狀態儲存，完成後重新整理，即可在這裡選擇工作檔案。舊 MP4 本身不包含取樣運動狀態。")}</p>}
      {config.continuation_source&&<><p className="comfy-hint">{tr("保存片段会以原分辨率提供起始运动和声音；所选参考图用于约束新画面。此时起始帧照片会由保存上下文取代。","The saved clip supplies starting motion and sound at its original resolution. Reference photos, if selected, direct the new footage. A starting-frame photo is replaced by this saved context.","保存クリップは元の解像度で開始モーションと音声を提供し、選択した参照画像が新しい映像を導きます。開始フレーム画像は保存済みコンテキストに置き換わります。","儲存片段會以原解析度提供起始運動和聲音；所選參考圖用於約束新畫面。此時起始幀照片會由儲存上下文取代。")}</p>
        <label><span>{tr("保留多少结尾运动","How much ending motion to carry over","引き継ぐ終了モーションの長さ","保留多少結尾運動")}</span><select aria-label="Continuation context" value={overlapValid?overlap:''} onChange={e=>{setOverlapNotice('');change('continuation_overlap_frames',Number(e.target.value));}}>{!overlapValid&&<option value="">{tr("正在调整上下文…","Adjusting context to fit…","コンテキストを調整中…","正在調整上下文…")}</option>}{overlapChoices.map((frames:number)=><option key={frames} value={frames}>{(frames/24).toFixed(3)} {tr("秒","seconds","秒","秒")}{frames===39?tr(' · 推荐',' · recommended','・推奨',' · 推薦'):''}</option>)}</select></label>
        {overlapNotice&&<p className="comfy-hint" role="status">{overlapNotice}</p>}
        {overlapValid&&<p className="comfy-timing">{(generatedFrames/24).toFixed(3)}s generated = {(overlap/24).toFixed(3)}s carried context + {((generatedFrames-overlap)/24).toFixed(3)}s new footage</p>}
        <p className="comfy-hint">{tr("对连续生成且兼容的片段，可使用 MMH3 Latent Stitch 工作流去除重复上下文。故事规划时长描述的是预期内容，最终片长会受这些重叠影响。","Use the MMH3 Latent Stitch workflow for compatible clips generated in sequence to remove repeated context. The story planner's times describe your intended story; final film length depends on these overlaps.","連続生成した互換クリップにはMMH3 Latent Stitchを使って重複コンテキストを除けます。ストーリープランの時間は意図した内容を示し、最終尺は重複量に左右されます。","對連續生成且相容的片段，可使用 MMH3 Latent Stitch 工作流移除重複內容。故事規劃時長描述的是預期內容，最終片長會受這些重疊影響。")}</p></>}
    </details>
    {sourceValue&&<p className="comfy-source comfy-hint">{tr("正在续写保存片段：","Continuing saved clip: ","継続する保存クリップ：","正在續寫儲存片段：")}<strong>{sourceValue.split('/').at(-1)}</strong>。{tr("新工作流会从其保存的运动和音频开始。","The new workflow starts from its saved motion and audio.","新しいワークフローは保存済みモーションと音声から始まります。","新工作流會從其儲存的運動和音訊開始。")}</p>}
    {p.mode==='ref2va'&&<details className="comfy-reference-preview" open>
      <summary>{t({'zh-CN':'发送前参考图映射','zh-TW':'傳送前參考圖對應',en:'Reference map before sending',ja:'送信前の参照画像マップ'})} · {pictureReferences.length}/9</summary>
      <p className="comfy-hint">{t({'zh-CN':'这里就是 ComfyUI 将收到的实际图片顺序。图号由程序确定，角色名和用途来自卡库绑定，不让本地识图模型自行猜名字。','zh-TW':'這裡就是 ComfyUI 將收到的實際圖片順序。圖號由程式確定，角色名與用途來自卡庫綁定，不讓本地識圖模型自行猜名字。',en:'This is the exact image order ComfyUI receives. The program assigns picture numbers; names and uses come from card bindings rather than vision-model guesses.',ja:'ComfyUIへ送る実際の画像順です。番号はプログラムが決め、人物名と用途は画像認識の推測ではなくカードの紐付けを使います。'})}</p>
      {pictureReferences.length?<div className="comfy-reference-grid">{pictureReferences.map((reference,index)=>{
        const asset=p.assets.find(item=>item.id===reference.asset_id);
        const declared=Array.isArray(asset?.reference_card_bindings)?asset.reference_card_bindings.filter((row:any)=>row&&typeof row.name==='string'):[];
        const subjectNames=[...new Set(p.subjects.filter(subject=>subject.asset_ids.includes(reference.asset_id)).map(subject=>subject.name).filter(Boolean))];
        const boundNames=declared.map((row:any)=>String(row.subject_name||row.name)).filter(Boolean);
        const names=[...new Set(boundNames.length?boundNames:subjectNames)];
        const overview=asset?.reference_overview===true;
        return <article className="comfy-reference-row" key={reference.asset_id+'-'+reference.token+'-'+index}>
          {asset?.media_type==='image'?<img src={`/api/assets/${reference.asset_id}/thumbnail`} alt={reference.name||asset.name}/>:<div className="comfy-reference-placeholder"/>}
          <div><div className="comfy-reference-title"><code>{reference.token}</code><strong>{reference.name||asset?.name}</strong>{overview&&<span>{t({'zh-CN':'总图','zh-TW':'總圖',en:'Overview',ja:'総覧'})}</span>}</div>
            <p>{t({'zh-CN':'用途','zh-TW':'用途',en:'Use',ja:'用途'})}：{semanticLabel(reference.semantic_role||asset?.semantic_role||'other')}</p>
            <p>{names.length?t({'zh-CN':'绑定','zh-TW':'綁定',en:'Bound to',ja:'紐付け'})+'：'+names.join('、'):t({'zh-CN':'全局场景参考','zh-TW':'全域場景參考',en:'Global scene reference',ja:'シーン全体の参照'})}</p>
            {overview&&declared.length>0&&<small>{declared.map((row:any)=>`${row.region||''}${row.region?' = ':''}${row.name}`).join('；')}</small>}
          </div>
        </article>;
      })}</div>:<p className="comfy-reference-empty">{t({'zh-CN':'当前没有会发送给 H3 的参考图。请先选择或准备参考卡。','zh-TW':'目前沒有會傳送給 H3 的參考圖。請先選擇或準備參考卡。',en:'No reference image is currently being sent to H3. Select or prepare reference cards first.',ja:'現在H3へ送る参照画像はありません。先に参照カードを選択・準備してください。'})}</p>}
      {pictureReferences.some(reference=>p.assets.find(asset=>asset.id===reference.asset_id)?.reference_overview===true)&&<p className="comfy-reference-authority">{t({'zh-CN':'总图规则：已绑定的图片区域主导可见外貌；角色圣经与同名卡主导姓名、身份、关系和连续性。识图推断不能覆盖这两类正本。','zh-TW':'總圖規則：已綁定的圖片區域主導可見外貌；角色聖經與同名卡主導姓名、身分、關係和連續性。識圖推斷不能覆蓋這兩類正本。',en:'Overview rule: a bound image region is authoritative for visible appearance; the Character Bible and matching card own name, identity, relationships and continuity. Vision inference may override neither.',ja:'総覧画像の規則：紐付け済みの画像領域は見た目の正本、キャラクター設定と同名カードは名前・身分・関係・連続性の正本です。画像認識の推測はどちらも上書きしません。'})}</p>}
    </details>}
    <div className="comfy-actions"><button className="comfy-send" type="button" onClick={prepare} disabled={!ready||busy||sending||!continuationReady}>{sending?tr('正在准备图片和工作流…','Preparing photos & workflow…','画像とワークフローを準備中…','正在準備圖片和工作流…'):tr('发送提示词和图片到 ComfyUI →','Send prompt + photos to ComfyUI →','プロンプトと画像をComfyUIへ送信 →','傳送提示詞和圖片到 ComfyUI →')}</button>{!ready&&<span className="comfy-hint">{tr("请先生成或编译提示词。","Make or build your prompt first.","先にプロンプトを作成してください。","請先生成或編譯提示詞。")}</span>}</div>
    <p className="comfy-hint">{tr("在 ComfyUI 的 MMH3 Create 中可修改提示词和种子。要延长成片，请在 Save 节点使用“Continue this result in Studio”。更换种子只会产生另一版画面，不会自动推进剧情。","In ComfyUI, edit the prompt and seed in MMH3 Create. To extend the finished video, use Continue this result in Studio on its Save node. Changing a seed makes another variation; it does not advance the story by itself.","ComfyUIのMMH3 CreateでプロンプトとSeedを編集できます。完成映像を延長するにはSaveノードの「Continue this result in Studio」を使います。Seed変更は別バリエーションを作るだけで、物語は自動で進みません。","在 ComfyUI 的 MMH3 Create 中可修改提示詞和種子。要延長成片，請在 Save 節點使用「Continue this result in Studio」。更換種子只會產生另一版畫面，不會自動推進劇情。")}</p>
    {error&&<p className="comfy-error" role="alert">{error}</p>}
    {sent&&sent.draftKey===draftKey&&sent.prompt===prompt&&<div className="comfy-sent"><strong>{tr("工作流已准备 · 尚未排队生成视频","Workflow prepared · no video queued","ワークフロー準備完了・映像は未実行","工作流已準備 · 尚未排隊生成影片")}</strong><p>{sent.manifest.width} × {sent.manifest.height} · {sent.manifest.steps} {tr("步","steps","ステップ","步")} · {sent.manifest.images.length} {tr("张图片已验证","photos verified","枚の画像を確認","張圖片已驗證")}</p><p>{sent.manifest.duration_note}</p>{sent.manifest.lora_warnings?.map((warning:string)=><p className="comfy-hint" key={warning}>{warning}</p>)}<div className="comfy-actions"><a href={sent.open_url} target="_blank" rel="noreferrer">{tr("打开已准备的 ComfyUI 工作流","Open prepared ComfyUI workflow","準備したComfyUIワークフローを開く","開啟已準備的 ComfyUI 工作流")}</a><button type="button" onClick={download}>{tr("下载工作流","Download workflow","ワークフローを保存","下載工作流")}</button></div><p className="comfy-hint">{tr("工作流副本","Workflow copy","ワークフローのコピー","工作流副本")}：{sent.export_folder}<br/>{tr("运行后视频","Video after Run","実行後の映像","執行後影片")}：ComfyUI output/{sent.manifest.output_prefix}{sent.manifest.mmh3?.save_enabled&&<><br/>{tr("工作状态","Working state","作業状態","工作狀態")}：ComfyUI output/{sent.manifest.mmh3.output_prefix}*.mmh3</>}</p></div>}
  </section>;
}
