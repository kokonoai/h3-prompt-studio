import { useEffect,useState } from 'react';
import type { Project } from './model';
import { getPeople } from './simple';
import { ensurePromptTags } from './tags';
import { localText,useUiLanguage } from './i18n';

export default function IdeaBuilder({project:p,update}:{project:Project;update:(fn:(p:Project)=>void)=>void}) {
  const {text:uiText}=useUiLanguage();
  const t=(zh:string,en:string,ja:string,tw=zh)=>uiText({'zh-CN':zh,'zh-TW':tw,en,ja});
  const out=(zh:string,en:string,ja:string,tw=zh)=>localText(p.production_language||'zh-CN',{'zh-CN':zh,'zh-TW':tw,en,ja});
  const actions=[
    ['walks into the scene',t('走进画面','walks into the scene','画面に入る','走進畫面')],
    ['smiles at the camera',t('对镜头微笑','smiles at the camera','カメラに微笑む','對鏡頭微笑')],
    ['turns toward the camera',t('转向镜头','turns toward the camera','カメラの方を向く','轉向鏡頭')],
    ['shows',t('展示','shows','見せる','展示')],['holds',t('拿着','holds','持つ','拿著')],
    ['picks up',t('拿起','picks up','持ち上げる','拿起')],['puts down',t('放下','puts down','置く','放下')],
    ['shares',t('分享','shares','分け合う','分享')],['talks',t('说话','talks','話す','說話')],
  ];
  const [who,setWho]=useState(''),[action,setAction]=useState('walks into the scene'),[target,setTarget]=useState(''),[object,setObject]=useState(''),[place,setPlace]=useState(''),[ending,setEnding]=useState('');
  useEffect(()=>{setWho('');setTarget('');setObject('');setPlace('');setEnding('');},[p.id]);
  const people=getPeople(p), preview=structuredClone(p);ensurePromptTags(preview);
  const objects=preview.assets.filter(a=>a.enabled&&a.semantic_role==='object');
  const places=preview.assets.filter(a=>a.enabled&&a.semantic_role==='background');
  const person=people.find(s=>s.id===who), other=people.find(s=>s.id===target), prop=objects.find(a=>a.id===object), room=places.find(a=>a.id===place);
  const actionLabel=actions.find(([key])=>key===action)?.[1]||action;
  const sentence=out(
    `${person?.name||'主体'}${actionLabel}${prop?` @${prop.prompt_tag} 中的道具`:''}${other?`，与${other.name}一起`:''}${room?`，地点采用 @${room.prompt_tag}`:''}。${ending.trim()?`结束时：${ending.trim().replace(/[。.!?]$/,'')}。`:''}`,
    `${person?.name||(people.length?'Choose a person':'The subject')} ${action}${prop?` the object from @${prop.prompt_tag}`:''}${other?` with ${other.name}`:''}${room?` in the place from @${room.prompt_tag}`:''}.${ending.trim()?` End with ${ending.trim().replace(/[.!?]$/,'')}.`:''}`,
    `${person?.name||'主体'}が${actionLabel}${prop?`。@${prop.prompt_tag}の小道具を使用`:''}${other?`、${other.name}と一緒に`:''}${room?`、場所は@${room.prompt_tag}`:''}。${ending.trim()?`終了状態：${ending.trim().replace(/[。.!?]$/,'')}。`:''}`,
    `${person?.name||'主體'}${actionLabel}${prop?` @${prop.prompt_tag} 中的道具`:''}${other?`，與${other.name}一起`:''}${room?`，地點採用 @${room.prompt_tag}`:''}。${ending.trim()?`結束時：${ending.trim().replace(/[。.!?]$/,'')}。`:''}`,
  );
  return <details className="idea-builder"><summary>{t('用选项搭建创意','Build my idea with choices','選択肢でアイデアを作る','用選項搭建創意')} <span>{t('可选','optional','任意','可選')}</span></summary>
    <p>{t('选择下面几项，将句子加入创意后仍可修改每个字。','Choose a few things below, then add the sentence to your idea. You can edit every word.','下から項目を選び、文章をアイデアに追加します。追加後も自由に編集できます。','選擇下面幾項，將句子加入創意後仍可修改每個字。')}</p>
    <div className="idea-builder-grid">
      <label className="simple-field"><span>{t('谁？','Who?','誰？','誰？')}</span><select aria-label={t('主要角色','Idea main person','主な人物','主要角色')} value={who} onChange={e=>setWho(e.target.value)}><option value="">{people.length?t('选择角色','Choose a person','人物を選択','選擇角色'):t('主体','The subject','主体','主體')}</option>{people.map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
      <label className="simple-field"><span>{t('做什么？','Does what?','何をする？','做什麼？')}</span><select aria-label={t('动作','Idea action','動作','動作')} value={action} onChange={e=>setAction(e.target.value)}>{actions.map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>
      <label className="simple-field"><span>{t('哪个道具？','Which object?','どの小道具？','哪個道具？')}</span><select aria-label={t('道具','Idea object','小道具','道具')} value={object} onChange={e=>setObject(e.target.value)}><option value="">{t('不使用道具','No object','小道具なし','不使用道具')}</option>{objects.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      <label className="simple-field"><span>{t('和谁？','With whom?','誰と？','和誰？')}</span><select aria-label={t('其他角色','Idea other person','相手の人物','其他角色')} value={target} onChange={e=>setTarget(e.target.value)}><option value="">{t('没有其他人','Nobody else','他の人物なし','沒有其他人')}</option>{people.filter(s=>s.id!==who).map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
      <label className="simple-field"><span>{t('在哪里？','Where?','どこで？','在哪裡？')}</span><select aria-label={t('地点','Idea place','場所','地點')} value={place} onChange={e=>setPlace(e.target.value)}><option value="">{t('由我描述地点','I’ll describe the place','場所は自分で説明','由我描述地點')}</option>{places.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}</select></label>
      <label className="simple-field"><span>{t('如何结束？','How should it end?','どう終わる？','如何結束？')}</span><input aria-label={t('结束状态','Idea ending','終了状態','結束狀態')} value={ending} onChange={e=>setEnding(e.target.value)} placeholder={t('例如：两人微笑，盒子保持关闭','both smiling; the box stays closed','例：二人が微笑み、箱は閉じたまま','例如：兩人微笑，盒子保持關閉')}/></label>
    </div>
    <p className="idea-builder-preview">{sentence}</p>
    <button type="button" disabled={!!people.length&&!person} onClick={()=>update(d=>{ensurePromptTags(d);d.story.text=[d.story.text.trim(),sentence].filter(Boolean).join('\n');})}>{t('把句子加入创意','Add sentence to my idea','文章をアイデアに追加','把句子加入創意')}</button>
  </details>;
}
