import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import ComfyPanel from "./ComfyPanel";
import { newShot, type Project } from "./model";

describe("ComfyUI reference preview", () => {
  it("shows the exact Picture order, declared overview members and identity authority", () => {
    const assetId="11111111-1111-4111-8111-111111111111";
    const project:Project={
      schema_version:1,id:"22222222-2222-4222-8222-222222222222",title:"Cast test",mode:"ref2va",
      duration:5,aspect_ratio:"16:9",profile:"director",authoring_mode:"manual",
      story:{text:"A and B enter.",locked:true},style:{},soundscape:"",music:"",custom_instructions:"",
      assets:[{id:assetId,name:"Cast overview",media_type:"image",role:"reference_image",semantic_role:"character",
        enabled:true,locked_order:false,description:"",observation:"",approved_observation:"",reference_overview:true,
        reference_card_bindings:[{name:"A",subject_name:"A",region:"the labelled region for A"},{name:"B",subject_name:"B",region:"the labelled region for B"}]}],
      subjects:[{id:"a",name:"A",asset_ids:[assetId],description:"hero"},{id:"b",name:"B",asset_ids:[assetId],description:"friend"}],
      shots:[newShot(5)],
    };
    const html=renderToStaticMarkup(<ComfyPanel project={project} prompt="ready" ready busy={false} onSettings={()=>{}}
      references={[{asset_id:assetId,token:"<Picture 1>",name:"Cast overview",role:"reference_image",semantic_role:"character"}]}/>);
    expect(html).toContain("Reference map before sending");
    expect(html).toContain("&lt;Picture 1&gt;");
    expect(html).toContain("Bound to");
    expect(html).toContain("A、B");
    expect(html).toContain("a bound image region is authoritative for visible appearance");
    expect(html).toContain("Character Bible and matching card own name, identity, relationships and continuity");
  });
});
