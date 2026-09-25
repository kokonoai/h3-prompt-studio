import type { Project, SceneContract, Shot } from "./model";
import {
  addSceneActor,
  addSceneObject,
  clearSceneContract,
  editSceneContract,
} from "./sceneContinuityState";
import "./SceneContinuity.css";
import { useUiLanguage } from "./i18n";

type Props = {
  project: Project;
  shot: Shot;
  index: number;
  update: (fn: (draft: Project) => void) => void;
};

/** Opening this panel is local; only an explicit edit takes ownership of the AI draft. */
export default function SceneContinuity({
  project,
  shot,
  index,
  update,
}: Props) {
  const {text:uiText}=useUiLanguage();
  const t=(zh:string,en:string,ja:string,tw=zh)=>uiText({'zh-CN':zh,'zh-TW':tw,en,ja});
  const prefix = t(`分镜 ${index+1}`,`Scene ${index+1}`,`シーン ${index+1}`,`分鏡 ${index+1}`);
  const contract = shot.scene_contract;
  const actors = contract?.actors || [];
  const objects = contract?.objects || [];
  const availablePeople = project.subjects.filter(
    (person) =>
      shot.visible_subject_ids.includes(person.id) &&
      !shot.offscreen_subject_ids.includes(person.id) &&
      !actors.some((actor) => actor.subject_id === person.id),
  );
  const availableObjects = project.assets.filter(
    (asset) =>
      asset.enabled &&
      asset.semantic_role === "object" &&
      !objects.some((object) => object.entity_id === asset.id),
  );
  const edit = (fn: (draft: SceneContract) => void) =>
    update((p) => editSceneContract(p, shot.id, fn));
  const field = (
    label: string,
    value: string | undefined,
    onChange: (value: string) => void,
    placeholder: string,
    maxLength = 500,
  ) => (
    <label className="scene-continuity-field">
      <span>{label}</span>
      <textarea
        aria-label={`${prefix} ${label}`}
        value={value || ""}
        rows={2}
        maxLength={maxLength}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );

  return (
    <details className="scene-continuity">
      <summary>
        {t("分镜连续性","Scene continuity","シーンの連続性","分鏡連續性")}{" "}
        <span>
          {contract
            ? shot.scene_contract_source === "generated"
              ? t("AI 草稿","AI draft","AI下書き","AI 草稿")
              : t("你的导演要求","Your direction","あなたの演出","你的導演要求")
            : t("人物、道具与环境","people, objects & surroundings","人物・物・環境","人物、道具與環境")}
        </span>
      </summary>
      <div className="scene-continuity-content">
        <p className="scene-continuity-help">
          {t("说明每个人从哪里开始、谁行动、谁保持不动，并固定每件道具的外观与数量。","Say where everyone starts, who acts and what stays still. Keep each object’s appearance and count consistent.","各人物の開始位置、誰が動き誰が静止するかを指定し、各小道具の外観と数を固定します。","說明每個人從哪裡開始、誰行動、誰保持不動，並固定每件道具的外觀與數量。")} 
          {shot.scene_contract_source === "generated"
            ? t("修改任一字段后，这些要求会在下次 AI 改写时保留。"," Editing any field keeps these directions for the next AI rewrite.","項目を編集すると、次回のAI書き換えでもこの指示を維持します。","修改任一欄位後，這些要求會在下次 AI 改寫時保留。")
            : contract
              ? t("AI 改写本分镜时会保留这些要求。"," AI keeps these directions when rewriting this scene.","AIがこのシーンを書き換える際も指示を維持します。","AI 改寫本分鏡時會保留這些要求。")
              : t("这些可选要求用于约束分镜过程与结尾。"," These optional directions guide the scene and its ending.","任意の指示でシーン進行と終了状態を導きます。","這些可選要求用於約束分鏡過程與結尾。")}
        </p>
        <section
          className="scene-continuity-section"
          aria-label={`${prefix} actor continuity`}
        >
          <h4>{t("画面中的人物","People in view","画面内の人物","畫面中的人物")}</h4>
          {actors.map((actor) => {
            const name =
              project.subjects.find((person) => person.id === actor.subject_id)
                ?.name || t("角色不可用","Unavailable character","利用できない人物","角色不可用");
            return (
              <fieldset
                className="scene-continuity-card"
                key={actor.subject_id}
              >
                <legend>{name}</legend>
                <div className="scene-continuity-card-heading">
                  <label className="scene-continuity-field">
                    <span>{t("本次动作中的角色","Role in this action","この動作での役割","本次動作中的角色")}</span>
                    <select
                      aria-label={`${prefix} ${name} activity`}
                      value={actor.activity}
                      onChange={(e) =>
                        edit((d) => {
                          const row = d.actors?.find(
                            (item) => item.subject_id === actor.subject_id,
                          );
                          if (row)
                            row.activity = e.target.value as "act" | "hold";
                        })
                      }
                    >
                      <option value="act">{t("执行动作","Performs the action","動作する","執行動作")}</option>
                      <option value="hold">{t("保持原位","Stay in place","その場を維持","保持原位")}</option>
                    </select>
                  </label>
                  <button
                    type="button"
                    aria-label={`${prefix} remove ${name} continuity`}
                    onClick={() =>
                      edit((d) => {
                        d.actors = d.actors?.filter(
                          (item) => item.subject_id !== actor.subject_id,
                        );
                      })
                    }
                  >
                    {t("移除要求","Remove direction","指示を削除","移除要求")}
                  </button>
                </div>
                <div className="scene-continuity-grid">
                  {field(
                    t(`${name} 的起始状态`,`${name} starts`,`${name}の開始状態`,`${name} 的起始狀態`),
                    actor.start,
                    (value) =>
                      edit((d) => {
                        const row = d.actors?.find(
                          (item) => item.subject_id === actor.subject_id,
                        );
                        if (row) row.start = value;
                      }),
                    t("例如：坐在桌子左侧，双手放在膝盖上","e.g. seated at the left of the table, hands on knees","例：テーブル左側に座り、両手は膝の上","例如：坐在桌子左側，雙手放在膝蓋上"),
                  )}
                  {field(
                    actor.activity==='hold'?t(`${name} 允许的小动作`,`${name} allowed small movement`,`${name}に許可する小さな動き`,`${name} 允許的小動作`):t(`${name} 的动作`,`${name} action`,`${name}の動作`,`${name} 的動作`),
                    actor.action,
                    (value) =>
                      edit((d) => {
                        const row = d.actors?.find(
                          (item) => item.subject_id === actor.subject_id,
                        );
                        if (row) row.action = value;
                      }),
                    actor.activity === "hold"
                      ? t("例如：安静注视；手脚保持不动","e.g. watches quietly; hands and feet remain still","例：静かに見守り、手足は動かさない","例如：安靜注視；手腳保持不動")
                      : t("例如：用右手拿起唯一的红色杯子","e.g. lifts the single red cup with her right hand","例：右手で1つだけの赤いカップを持ち上げる","例如：用右手拿起唯一的紅色杯子"),
                  )}
                  {field(
                    t(`${name} 的结束状态`,`${name} ends`,`${name}の終了状態`,`${name} 的結束狀態`),
                    actor.end,
                    (value) =>
                      edit((d) => {
                        const row = d.actors?.find(
                          (item) => item.subject_id === actor.subject_id,
                        );
                        if (row) row.end = value;
                      }),
                    t("例如：仍然坐着，把杯子举在胸前","e.g. still seated, holding the cup near her chest","例：座ったまま、胸元でカップを持つ","例如：仍然坐著，把杯子舉在胸前"),
                  )}
                </div>
              </fieldset>
            );
          })}
          <label className="scene-continuity-field">
            <span>{t("添加人物连续性要求","Add a person’s direction","人物の連続性指示を追加","新增人物連續性要求")}</span>
            <select
              aria-label={`${prefix} add actor continuity`}
              value=""
              disabled={!availablePeople.length || actors.length >= 32}
              onChange={(e) => {
                if (e.target.value)
                  update((p) => addSceneActor(p, shot.id, e.target.value));
              }}
            >
              <option value="">{t("选择画面中的角色…","Choose a visible person…","画面内の人物を選択…","選擇畫面中的角色…")}</option>
              {availablePeople.map((person) => (
                <option key={person.id} value={person.id}>
                  {person.name || t("未命名角色","Unnamed person","名前のない人物","未命名角色")}
                </option>
              ))}
            </select>
          </label>
          {!actors.length && !availablePeople.length && (
            <p className="scene-continuity-help">
              {t("先让一个已命名角色出现在本分镜，才能描述其动作；仍可在下方单独约束道具和环境。","Make a named person visible in this scene to describe their movement. You can still direct objects and surroundings below.","名前付き人物を画面に登場させると動きを指定できます。下では小道具と環境だけでも設定できます。","先讓一個已命名角色出現在本分鏡，才能描述其動作；仍可在下方單獨約束道具和環境。")}
            </p>
          )}
        </section>

        <section
          className="scene-continuity-section"
          aria-label={`${prefix} object continuity`}
        >
          <h4>{t("需要保持一致的道具","Objects to keep consistent","一貫性を保つ小道具","需要保持一致的道具")}</h4>
          {objects.map((object, objectIndex) => {
            const label = t(`道具 ${objectIndex+1}`,`Object ${objectIndex+1}`,`小道具 ${objectIndex+1}`,`道具 ${objectIndex+1}`);
            return (
              <fieldset
                className="scene-continuity-card"
                key={object.entity_id}
              >
                <legend>{object.name || label}</legend>
                <div className="scene-continuity-grid">
                  <label className="scene-continuity-field">
                    <span>{t("名称","Name","名前","名稱")}</span>
                    <input
                      aria-label={`${prefix} ${label} name`}
                      maxLength={120}
                      value={object.name}
                      onChange={(e) => {
                        const value = e.target.value || label;
                        edit((d) => {
                          const row = d.objects?.find(
                            (item) => item.entity_id === object.entity_id,
                          );
                          if (row) row.name = value;
                        });
                      }}
                    />
                  </label>
                  <label className="scene-continuity-field">
                    <span>{t("画面中的准确数量","Exact count in view","画面内の正確な数","畫面中的準確數量")}</span>
                    <input
                      aria-label={`${prefix} ${label} count`}
                      type="number"
                      min={1}
                      max={100}
                      step={1}
                      value={object.count}
                      onChange={(e) => {
                        const value = Number(e.target.value);
                        if (
                          e.target.value &&
                          Number.isInteger(value) &&
                          value >= 1 &&
                          value <= 100
                        )
                          edit((d) => {
                            const row = d.objects?.find(
                              (item) => item.entity_id === object.entity_id,
                            );
                            if (row) row.count = value;
                          });
                      }}
                    />
                  </label>
                  {field(
                    t(`${label} 外观`,`${label} appearance`,`${label}の外観`,`${label} 外觀`),
                    object.description,
                    (value) =>
                      edit((d) => {
                        const row = d.objects?.find(
                          (item) => item.entity_id === object.entity_id,
                        );
                        if (row) row.description = value;
                      }),
                    t("例如：带一条白色条纹的小红色陶瓷杯","e.g. small red ceramic cup with one white stripe","例：白い縞が1本ある小さな赤い陶器カップ","例如：帶一條白色條紋的小紅色陶瓷杯"),
                  )}
                  {field(
                    t(`${label} 起始位置`,`${label} starts`,`${label}の開始位置`,`${label} 起始位置`),
                    object.start,
                    (value) =>
                      edit((d) => {
                        const row = d.objects?.find(
                          (item) => item.entity_id === object.entity_id,
                        );
                        if (row) row.start = value;
                      }),
                    t("例如：在米拉面前的桌上","e.g. on the table in front of Mira","例：ミラの前のテーブル上","例如：在米拉面前的桌上"),
                  )}
                  {field(
                    t(`${label} 结束位置`,`${label} ends`,`${label}の終了位置`,`${label} 結束位置`),
                    object.end,
                    (value) =>
                      edit((d) => {
                        const row = d.objects?.find(
                          (item) => item.entity_id === object.entity_id,
                        );
                        if (row) row.end = value;
                      }),
                    t("例如：在米拉右手中；不得出现第二个杯子","e.g. held in Mira’s right hand; no second cup appears","例：ミラの右手にあり、2つ目のカップは出ない","例如：在米拉右手中；不得出現第二個杯子"),
                  )}
                </div>
                <button
                  type="button"
                  aria-label={`${prefix} remove ${label} continuity`}
                  onClick={() =>
                    edit((d) => {
                      d.objects = d.objects?.filter(
                        (item) => item.entity_id !== object.entity_id,
                      );
                    })
                  }
                >
                  {t("移除道具要求","Remove object direction","小道具の指示を削除","移除道具要求")}
                </button>
              </fieldset>
            );
          })}
          <div className="scene-continuity-card-heading">
            <button
              type="button"
              aria-label={`${prefix} add object continuity`}
              disabled={objects.length >= 24}
              onClick={() => update((p) => addSceneObject(p, shot.id))}
            >
              {t("添加道具","Add an object","小道具を追加","新增道具")}
            </button>
            {!!availableObjects.length && (
              <label className="scene-continuity-field">
                <span>{t("或使用道具参考图","Or use an object reference","または小道具参照を使用","或使用道具參考圖")}</span>
                <select
                  aria-label={`${prefix} add referenced object continuity`}
                  value=""
                  disabled={objects.length >= 24}
                  onChange={(e) => {
                    if (e.target.value)
                      update((p) => addSceneObject(p, shot.id, e.target.value));
                  }}
                >
                  <option value="">{t("选择参考图…","Choose a reference…","参照を選択…","選擇參考圖…")}</option>
                  {availableObjects.map((asset) => (
                    <option key={asset.id} value={asset.id}>
                      {asset.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
        </section>
        <div className="scene-continuity-grid">
          {field(
            t("环境","Environment","環境","環境"),
            contract?.environment,
            (value) =>
              edit((d) => {
                d.environment = value;
              }),
            t("例如：保持同一咖啡馆、暖光，桌子和门口位置固定","e.g. the same café, warm light, table and doorway fixed in place","例：同じカフェ、暖色光、テーブルと入口の位置を固定","例如：保持同一咖啡館、暖光，桌子和門口位置固定"),
            1000,
          )}
          {field(
            t("背景活动","Background activity","背景の動き","背景活動"),
            contract?.background_activity,
            (value) =>
              edit((d) => {
                d.background_activity = value;
              }),
            t("例如：远处行人继续走动；无人加入前景动作","e.g. distant pedestrians keep walking; no one joins the foreground action","例：遠くの通行人は歩き続け、前景の動作には誰も加わらない","例如：遠處行人繼續走動；無人加入前景動作"),
          )}
        </div>
        {!!contract && (
          <button
            className="scene-continuity-reset"
            type="button"
            aria-label={`${prefix} clear scene continuity`}
            onClick={() =>
              update((p) => {
                const target = p.shots.find((item) => item.id === shot.id);
                if (target) clearSceneContract(target);
              })
            }
          >
            {t("清除连续性要求 · 交给 AI","Clear continuity directions · let AI choose","連続性指示を消去・AIに任せる","清除連續性要求 · 交給 AI")}
          </button>
        )}
      </div>
    </details>
  );
}
