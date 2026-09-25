import { useState } from "react";
import type { Project, Shot } from "./model";
import { getPeople } from "./simple";
import {
  appendSceneAction,
  applyCameraSetup,
  CAMERA_MOVES,
  CAMERA_SETUPS,
  duplicateScene,
  moveScene,
  setDirectorValue,
  setPersonVisibility,
  setSceneDuration,
  SHOT_SIZES,
} from "./shotDirections";
import type { DirectedShot, DirectorPath } from "./shotDirections";
import SceneContinuity from "./SceneContinuity";
import "./SceneDirector.css";
import { useUiLanguage } from "./i18n";

type Props = {
  project: Project;
  shot: Shot;
  index: number;
  update: (fn: (draft: Project) => void) => void;
};

export function SceneDirector({ project, shot, index, update }: Props) {
  const {text:uiText}=useUiLanguage();
  const t=(zh:string,en:string,ja:string,tw=zh)=>uiText({'zh-CN':zh,'zh-TW':tw,en,ja});
  const optionTitle=(key:string,title:string)=>({
    'wide':t('广角 · 人物与环境','Wide · people and surroundings','ワイド・人物と環境','廣角 · 人物與環境'),
    'long shot':t('全景 · 全身','Long shot · full body','ロング・全身','全景 · 全身'),
    'medium':t('中景 · 腰部以上','Medium · waist up','ミディアム・腰上','中景 · 腰部以上'),
    'close-up':t('近景 · 脸与情绪','Close-up · face and emotion','クローズアップ・顔と感情','近景 · 臉與情緒'),
    'extreme close-up':t('细节 · 物体或局部','Detail · an object or small feature','ディテール・物や細部','細節 · 物體或局部'),
    'over-the-shoulder':t('过肩镜头','Over the shoulder','肩越し','過肩鏡頭'),
    'two-shot':t('双人同框','Two people together','二人ショット','雙人同框'),
    'static':t('固定机位','Still camera','固定カメラ','固定機位'),
    'pan':t('横摇 · 转向侧面','Pan · turn to the side','パン・横へ向く','橫搖 · 轉向側面'),
    'tracking':t('跟随动作','Follow the action','動作を追う','跟隨動作'),
    'push-in':t('推进','Move closer','寄る','推進'),
    'pull-back':t('拉远','Move away','引く','拉遠'),
    'orbit':t('环绕','Circle around','周回','環繞'),
  } as Record<string,string>)[key]||title;
  const setupTitle=(id:string,label:string)=>({place:t('展示环境','Show the place','場所を見せる','展示環境'),emotion:t('突出情绪','Show emotion','感情を見せる','突出情緒'),conversation:t('双人对话','Two people talking','二人の会話','雙人對話'),follow:t('跟随人物','Follow someone','人物を追う','跟隨人物'),detail:t('展示物体','Show an object','物を見せる','展示物體')} as Record<string,string>)[id]||label;
  const n = index + 1;
  const [actor, setActor] = useState("");
  const [verb, setVerb] = useState("holds");
  const [objectId, setObjectId] = useState("");
  const [customAction, setCustomAction] = useState("");
  const people = getPeople(project);
  const objects = project.assets.filter(
    (a) => a.enabled && a.semantic_role === "object",
  );
  const locks = (shot as DirectedShot).director_locks || [];
  const set = (path: DirectorPath, value: string) =>
    update((d) => setDirectorValue(d, shot.id, path, value));
  const choice = (
    path: DirectorPath,
    label: string,
    value: string,
    options: readonly (readonly [string, string])[],
  ) => (
    <label className="scene-director-field">
      <span>
        {label}
        {locks.includes(path) && (
          <small title={t("AI 会保留此选择","AI keeps this choice","AIはこの選択を維持","AI 會保留此選擇")}> · {t("已选","chosen","選択済み","已選")}</small>
        )}
      </span>
      <select
        aria-label={`Scene ${n} ${label.toLowerCase()}`}
        value={value || ""}
        onChange={(e) => set(path, e.target.value)}
      >
        <option value="">{t("AI 决定","AI chooses","AIに任せる","AI 決定")}</option>
        {value && !options.some(([key]) => key === value) && (
          <option value={value}>{value}</option>
        )}
        {options.map(([key, title]) => (
          <option key={key} value={key}>
            {optionTitle(key,title)}
          </option>
        ))}
      </select>
    </label>
  );
  const textField = (
    path: DirectorPath,
    label: string,
    value: string,
    placeholder: string,
  ) => (
    <label className="scene-director-field">
      <span>{label}</span>
      <input
        aria-label={`Scene ${n} ${label.toLowerCase()}`}
        value={value || ""}
        placeholder={placeholder}
        onChange={(e) => set(path, e.target.value)}
      />
    </label>
  );

  return (
    <div className="scene-director">
      <div className="scene-director-main">
        {choice(
          "camera.framing",
          t("景别","Shot size","ショットサイズ","景別"),
          shot.camera?.framing,
          SHOT_SIZES,
        )}
        {choice(
          "camera.movement",
          t("摄影机运动","Camera movement","カメラ移動","攝影機運動"),
          shot.camera?.movement,
          CAMERA_MOVES,
        )}
        {index > 0 &&
          choice("transition", t("分镜转场","Scene change","シーン切替","分鏡轉場"), shot.transition, [
            ["continuous", t("继续拍摄 · 不切镜","Keep filming · no cut","撮影継続・カットなし","繼續拍攝 · 不切鏡")],
            ["cut", t("切到新镜头","Cut to a new shot","新しいショットへカット","切到新鏡頭")],
          ])}
      </div>
      <p className="scene-director-hint">
        {t("选择对你重要的项目；AI 会保留这些选择并补全其余内容。","Choose what matters to you. AI keeps your choices and fills in the rest.","重要な項目を選ぶと、AIが選択を維持して残りを補います。","選擇對你重要的項目；AI 會保留這些選擇並補全其餘內容。")}
      </p>
      <details className="scene-director-more">
        <summary>
          {t("更多分镜选项","More scene options","その他のシーン設定","更多分鏡選項")} <span>{t("时序、人物、地点与摄影机","timing, people, place & camera","時間・人物・場所・カメラ","時序、人物、地點與攝影機")}</span>
        </summary>
        <div className="scene-director-options">
          <div className="scene-director-timing">
            <label className="scene-director-field">
              <span>{t("分镜时长（秒）","Scene length (seconds)","シーンの長さ（秒）","分鏡時長（秒）")}</span>
              <input
                aria-label={`Scene ${n} duration`}
                type="number"
                min={0.25}
                max={project.duration}
                step={0.25}
                value={shot.duration}
                disabled={project.shots.length === 1}
                onChange={(e) => {
                  if (e.target.value)
                    update((d) =>
                      setSceneDuration(d, shot.id, Number(e.target.value)),
                    );
                }}
              />
            </label>
            <p>
              {project.shots.length === 1
                ? t("单分镜请在上方修改视频总时长。","Change the video length above for a single scene.","1シーンの場合は上で映像全体の長さを変更します。","單分鏡請在上方修改影片總時長。")
                : t(`其他分镜会自动调整，使总时长保持 ${project.duration} 秒。`,`Other scenes adjust to keep the whole video at ${project.duration} seconds.`,`他のシーンを調整し、全体を${project.duration}秒に保ちます。`,`其他分鏡會自動調整，使總時長保持 ${project.duration} 秒。`)}
            </p>
          </div>
          <div className="scene-director-buttons">
            <button
              type="button"
              aria-label={`Move scene ${n} earlier`}
              disabled={index === 0}
              onClick={() => update((d) => moveScene(d, shot.id, -1))}
            >
              {t("前移","Move earlier","前へ","前移")}
            </button>
            <button
              type="button"
              aria-label={`Move scene ${n} later`}
              disabled={index === project.shots.length - 1}
              onClick={() => update((d) => moveScene(d, shot.id, 1))}
            >
              {t("后移","Move later","後ろへ","後移")}
            </button>
            <button
              type="button"
              aria-label={`Duplicate scene ${n}`}
              disabled={project.shots.length >= 6}
              title={t("复制分镜设置；对白和精确连续性仍留在原分镜","Copy this scene's setup; speech and precise continuity stay in the original","シーン設定を複製し、台詞と正確な連続性は元のシーンに残します","複製分鏡設定；對白和精確連續性仍留在原分鏡")}
              onClick={() => update((d) => duplicateScene(d, shot.id))}
            >
              {t("复制设置","Duplicate setup","設定を複製","複製設定")}
            </button>
          </div>
          <p className="scene-director-hint">
            {t("复制会拷贝设置并平分时长；对白留在原分镜，请为副本补写新的连续性说明。","Duplicate copies the setup and splits its time. Spoken lines stay in the original scene. Add fresh continuity directions to the copy.","複製は設定をコピーして時間を分割します。台詞は元のシーンに残るため、複製側に新しい連続性指示を追加してください。","複製會拷貝設定並平分時長；對白留在原分鏡，請為副本補寫新的連續性說明。")}
          </p>
          <fieldset className="scene-director-setups">
            <legend>{t("尝试摄影机预设","Try a camera setup","カメラプリセットを試す","嘗試攝影機預設")}</legend>
            <div className="scene-director-buttons">
              {CAMERA_SETUPS.map((setup) => (
                <button
                  type="button"
                  aria-label={t(`分镜 ${n}：${setupTitle(setup.id,setup.label)}`,`Scene ${n}: ${setup.label}`,`シーン${n}：${setupTitle(setup.id,setup.label)}`,`分鏡 ${n}：${setupTitle(setup.id,setup.label)}`)}
                  key={setup.id}
                  onClick={() =>
                    update((d) => applyCameraSetup(d, shot.id, setup.id))
                  }
                >
                  {setupTitle(setup.id,setup.label)}
                </button>
              ))}
            </div>
          </fieldset>
          <div className="scene-director-grid">
            {choice("camera.height", t("摄影机角度","Camera angle","カメラ角度","攝影機角度"), shot.camera?.height, [
              ["eye level", t("平视","Eye level","目線の高さ","平視")],
              ["low angle", t("仰拍","Looking up","ローアングル","仰拍")],
              ["high angle", t("俯拍","Looking down","ハイアングル","俯拍")],
              ["overhead", t("正上方俯视","From directly above","真上から","正上方俯視")],
            ])}
            {choice("camera.focus", t("焦点","Focus","フォーカス","焦點"), shot.camera?.focus, [
              ["keep the speaking face in focus", t("说话的人","The person speaking","話している人物","說話的人")],
              [
                "sharp subject, softly blurred background",
                t("主体清晰 · 背景柔化","Subject sharp · background soft","主体を鮮明に・背景をぼかす","主體清晰 · 背景柔化"),
              ],
              ["deep focus", t("全景深","Everything in focus","全体にピント","全景深")],
            ])}
            {textField(
              "setting",
              t("地点","Place","場所","地點"),
              shot.setting,
              t("例如：环境图中的咖啡馆","e.g. the café from the background photo","例：背景画像のカフェ","例如：環境圖中的咖啡館"),
            )}
            {textField(
              "final_state",
              t("这一幕如何结束","How this scene ends","このシーンの終了状態","這一幕如何結束"),
              shot.final_state,
              t("例如：诺拉正拿着礼物","e.g. Nora is holding the gift","例：ノラが贈り物を持っている","例如：諾拉正拿著禮物"),
            )}
          </div>
          {!!people.length && (
            <fieldset className="scene-director-people">
              <legend>{t("谁出现在这个分镜？","Who is in this scene?","このシーンに誰がいる？","誰出現在這個分鏡？")}</legend>
              <p className="scene-director-hint">
                {t("画外表示能听到声音，但画面中不出现本人。","Off screen means their voice can be heard without showing them.","画面外は声だけ聞こえ、本人は映らない状態です。","畫外表示能聽到聲音，但畫面中不出現本人。")}
              </p>
              {people.map((person) => (
                <label className="scene-director-person" key={person.id}>
                  <span>{person.name || t("未命名角色","Unnamed person","名前のない人物","未命名角色")}</span>
                  <select
                    aria-label={`Scene ${n} ${person.name} visibility`}
                    value={
                      shot.visible_subject_ids.includes(person.id)
                        ? "visible"
                        : shot.offscreen_subject_ids.includes(person.id)
                          ? "offscreen"
                          : "absent"
                    }
                    onChange={(e) =>
                      update((d) =>
                        setPersonVisibility(
                          d,
                          shot.id,
                          person.id,
                          e.target.value as "visible" | "offscreen" | "absent",
                        ),
                      )
                    }
                  >
                    <option value="visible">{t("出现在画面中","Visible in the shot","画面に登場","出現在畫面中")}</option>
                    <option value="offscreen">{t("画外说话","Speaking off screen","画面外で話す","畫外說話")}</option>
                    <option value="absent">{t("本分镜不出现","Not in this scene","このシーンには登場しない","本分鏡不出現")}</option>
                  </select>
                </label>
              ))}
              {(locks.includes("visible_subject_ids") ||
                locks.includes("offscreen_subject_ids")) && (
                <button
                  className="scene-director-reset"
                  type="button"
                  aria-label={`Scene ${n} let AI choose people`}
                  onClick={() =>
                    update((d) => {
                      setDirectorValue(
                        d,
                        shot.id,
                        "visible_subject_ids",
                        [],
                        false,
                      );
                      setDirectorValue(
                        d,
                        shot.id,
                        "offscreen_subject_ids",
                        [],
                        false,
                      );
                    })
                  }
                >
                  {t("由 AI 决定谁出现","Let AI choose who is visible","登場人物をAIに任せる","由 AI 決定誰出現")}
                </button>
              )}
            </fieldset>
          )}
          {!!people.length && (
            <details className="scene-director-builder">
              <summary>{t("辅助编写动作","Help me write an action","動作作成を補助","輔助編寫動作")}</summary>
              <div className="scene-director-grid">
                <label className="scene-director-field">
                  <span>{t("谁？","Who?","誰？","誰？")}</span>
                  <select
                    aria-label={`Scene ${n} action person`}
                    value={actor}
                    onChange={(e) => setActor(e.target.value)}
                  >
                    <option value="">{t("选择角色…","Choose a person…","人物を選択…","選擇角色…")}</option>
                    {people.map((person) => (
                      <option key={person.id} value={person.id}>
                        {person.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="scene-director-field">
                  <span>{t("做什么？","Does what?","何をする？","做什麼？")}</span>
                  <select
                    aria-label={`Scene ${n} action type`}
                    value={verb}
                    onChange={(e) => setVerb(e.target.value)}
                  >
                    {[
                      "holds",
                      "picks up",
                      "looks at",
                      "points to",
                      "walks toward",
                      "custom",
                    ].map((v) => (
                      <option key={v} value={v}>
                        {v==='custom'?t('自行填写…','Write my own…','自由入力…','自行填寫…'):({holds:t('拿着','holds','持つ','拿著'),'picks up':t('拿起','picks up','持ち上げる','拿起'),'looks at':t('看向','looks at','見る','看向'),'points to':t('指向','points to','指さす','指向'),'walks toward':t('走向','walks toward','近づく','走向')} as Record<string,string>)[v]||v}
                      </option>
                    ))}
                  </select>
                </label>
                {verb === "custom" ? (
                  <label className="scene-director-field">
                    <span>{t("动作","Action","動作","動作")}</span>
                    <input
                      aria-label={`Scene ${n} custom action`}
                      value={customAction}
                      placeholder={t("例如：挥手，然后打开门","e.g. waves, then opens the door","例：手を振ってから扉を開ける","例如：揮手，然後打開門")}
                      onChange={(e) => setCustomAction(e.target.value)}
                    />
                  </label>
                ) : (
                  <label className="scene-director-field">
                    <span>{t("哪个道具？","Which object?","どの小道具？","哪個道具？")}</span>
                    <select
                      aria-label={`Scene ${n} action object`}
                      value={objectId}
                      onChange={(e) => setObjectId(e.target.value)}
                    >
                      <option value="">{t("选择道具图…","Choose an object photo…","小道具画像を選択…","選擇道具圖…")}</option>
                      {objects.map((object) => (
                        <option key={object.id} value={object.id}>
                          {object.name}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
              </div>
              <button
                type="button"
                aria-label={`Add action to scene ${n}`}
                disabled={
                  !actor ||
                  (verb === "custom" ? !customAction.trim() : !objectId)
                }
                onClick={() => {
                  update((d) =>
                    appendSceneAction(
                      d,
                      shot.id,
                      actor,
                      verb === "custom" ? customAction : verb,
                      verb === "custom" ? "" : objectId,
                    ),
                  );
                  setCustomAction("");
                }}
              >
                {t("加入“画面里发生什么”","Add to “What happens”","「何が起こる？」に追加","加入「畫面裡發生什麼」")}
              </button>
            </details>
          )}
        </div>
      </details>
      <SceneContinuity project={project} shot={shot} index={index} update={update} />
    </div>
  );
}

export default SceneDirector;
