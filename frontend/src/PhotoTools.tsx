import { useEffect, useRef, useState } from 'react';
import type { Asset, Project } from './model';
import { ensurePromptTags, movePhoto, renamePromptTag } from './tags';
import { useUiLanguage } from './i18n';
import './PhotoTools.css';

export default function PhotoTools({project, asset, index, update, onReplace}: {
  project:Project; asset:Asset; index:number; update:(fn:(p:Project)=>void)=>void;
  onReplace:(id:string,file:File)=>Promise<void>;
}) {
  const {text:uiText}=useUiLanguage();
  const t=(zh:string,en:string,ja:string,tw=zh)=>uiText({'zh-CN':zh,'zh-TW':tw,en,ja});
  const [tag,setTag] = useState(asset.prompt_tag || '');
  const [error,setError] = useState('');
  const file = useRef<HTMLInputElement>(null);
  useEffect(()=>{setTag(asset.prompt_tag || '');setError('');},[asset.id,asset.prompt_tag]);
  const rename = () => {
    try {
      const preview = structuredClone(project); ensurePromptTags(preview); renamePromptTag(preview,asset.id,tag);
      update(p=>{ensurePromptTags(p);renamePromptTag(p,asset.id,tag);});setError('');
    } catch(e) { setError((e as Error).message); }
  };
  const move = (delta:number) => {
    try { const preview=structuredClone(project);movePhoto(preview,asset.id,delta);update(p=>movePhoto(p,asset.id,delta));setError(''); }
    catch(e) {setError((e as Error).message);}
  };
  return <details className="photo-tools">
    <summary>{asset.prompt_tag ? `@${asset.prompt_tag}` : t('名称、标签与替换','Name, tag & replace','名前・タグ・置換','名稱、標籤與替換')} <span>{t('编辑图片','Edit photo','画像を編集','編輯圖片')}</span></summary>
    <label className="simple-field"><span>{t('图片名称','Photo name','画像名','圖片名稱')}</span><input aria-label={t(`图片 ${index+1} 名称`,`Photo ${index+1} name`,`画像${index+1}の名前`,`圖片 ${index+1} 名稱`)} value={asset.name} onChange={e=>update(p=>{const a=p.assets.find(a=>a.id===asset.id);if(a)a.name=e.target.value;})}/></label>
    <label className="simple-field"><span>{t('易用引用标签','Easy reference tag','参照タグ','易用引用標籤')}</span><input aria-label={t(`图片 ${index+1} 标签`,`Photo ${index+1} tag`,`画像${index+1}のタグ`,`圖片 ${index+1} 標籤`)} value={tag} placeholder="green-dress" onChange={e=>setTag(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'){e.preventDefault();rename();}}}/></label>
    <button type="button" onClick={rename}>{t('保存标签','Save tag','タグを保存','儲存標籤')}</button>
    <small>{t(`在创意中使用 @${tag||'tag'}。重命名会更新书面引用，但精确对白不会改变。`,`Use @${tag||'tag'} in your idea. Renaming updates written references; exact spoken words stay unchanged.`,`アイデア内で@${tag||'tag'}を使えます。名前変更は文章中の参照を更新しますが、正確な台詞は変えません。`,`在創意中使用 @${tag||'tag'}。重新命名會更新書面引用，但精確對白不會改變。`)}</small>
    <div className="photo-tools-actions">
      <button type="button" onClick={()=>file.current?.click()}>{t('替换图片','Replace photo','画像を置換','替換圖片')}</button>
      <button type="button" aria-label={t(`把图片 ${index+1} 前移`,`Move photo ${index+1} earlier`,`画像${index+1}を前へ`,`把圖片 ${index+1} 前移`)} disabled={project.assets[0]?.id===asset.id||asset.locked_order} onClick={()=>move(-1)}>←</button>
      <button type="button" aria-label={t(`把图片 ${index+1} 后移`,`Move photo ${index+1} later`,`画像${index+1}を後ろへ`,`把圖片 ${index+1} 後移`)} disabled={project.assets.at(-1)?.id===asset.id||asset.locked_order} onClick={()=>move(1)}>→</button>
    </div>
    <input ref={file} className="photo-replace-input" type="file" accept="image/*" aria-label={t(`替换图片 ${index+1}`,`Replace photo ${index+1} file`,`画像${index+1}を置換`,`替換圖片 ${index+1}`)} onChange={async e=>{const selected=e.target.files?.[0]; e.target.value='';if(selected)await onReplace(asset.id,selected);}}/>
    <small>{t('替换后会保留标签、顺序和角色绑定；下次 AI 会读取新图片。','Replacement keeps the tag, position and person assignment. AI reads the new photo next time.','置換後もタグ・順序・人物の紐付けを維持し、次回AIが新しい画像を読みます。','替換後會保留標籤、順序和角色綁定；下次 AI 會讀取新圖片。')}</small>
    {error&&<p role="alert" className="photo-tools-error">{error}</p>}
  </details>;
}

export function ReferenceInsert({project,onInsert,label='Insert photo reference'}:{project:Project;onInsert:(text:string)=>void;label?:string}) {
  const {text:uiText}=useUiLanguage();
  const t=(zh:string,en:string,ja:string,tw=zh)=>uiText({'zh-CN':zh,'zh-TW':tw,en,ja});
  const preview=structuredClone(project);ensurePromptTags(preview);
  return <div className="reference-insert"><select aria-label={label} value="" onChange={e=>{if(e.target.value)onInsert('@'+e.target.value);}}>
    <option value="">{t('+ 按名称插入图片','+ Insert a photo by name','+ 名前で画像を挿入','+ 按名稱插入圖片')}</option>
    {preview.assets.filter(a=>a.enabled&&a.media_type==='image').map(a=><option key={a.id} value={a.prompt_tag}>@{a.prompt_tag} · {a.name}{a.role==='context'?t(' · 灵感',' · inspiration','・参考',' · 靈感'):''}</option>)}
  </select><small>{t('可选：普通名称和自然语言句子也可以。','Optional: normal names and sentences work too.','任意：通常の名前や文章でも使えます。','可選：普通名稱和自然語言句子也可以。')}</small></div>;
}
