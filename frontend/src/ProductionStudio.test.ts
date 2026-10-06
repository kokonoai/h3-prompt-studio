import { describe, expect, it } from "vitest";
import { productionDialogueExport, productionKeyframeSize, productionStoryExport, qualityAcceptanceTargets, segmentHasCompletedVideo, segmentNeedsVideoPrompt, selectedClipTargets, videoPromptTargets, videoRenderTargets } from "./ProductionStudio";

const production:any={
  title:"Library",language:"ja",brief:"Source story",character_bible:"A remains A.",
  style_bible:"Hand-drawn",continuity_notes:"The book stays with A.",
  episodes:[{index:1,title:"Meeting",logline:"A meets B.",story:"They meet.",continuity_notes:"Book retained."}],
  segments:[{index:1,title:"Hello",duration:6,story:"A enters.",setting:"library",action:"A waves.",ending:"A stops.",
    dialogue:[{speaker:"A",text:"こんにちは。",language:"Japanese",voiceover:false},{speaker:"B",text:"待って。",language:"Japanese",voiceover:true}]}],
};

describe("production text exports",()=>{
  it("exports the full story, bibles, episodes and clip plan",()=>{
    const text=productionStoryExport(production);
    expect(text).toContain("# Library");
    expect(text).toContain("Source story");
    expect(text).toContain("A remains A.");
    expect(text).toContain("01 · Meeting");
    expect(text).toContain("01 · Hello · 6s");
  });

  it("exports exact dialogue in production order and marks voiceover",()=>{
    const text=productionDialogueExport(production);
    expect(text).toContain("プロジェクト言語：日本語");
    expect(text).toContain("[01.1] A：こんにちは。");
    expect(text).toContain("[01.2] B（画外音）：待って。");
  });
});

describe("episode batch production",()=>{
  it("targets only checked clips and keeps storyboard order",()=>{
    const clips:any[]=[{id:"clip-1"},{id:"clip-2"},{id:"clip-3"}];
    expect(selectedClipTargets(clips,["clip-3","clip-1"]).map(item=>item.id)).toEqual(["clip-1","clip-3"]);
    expect(selectedClipTargets(clips,[])).toEqual([]);
  });

  it("targets only explicit unaccepted QC rejections for batch release",()=>{
    const outputs:any={segments:[
      {segment_id:"clip-1",index:1,title:"Rejected",candidates:[{id:"run-1",status:"succeeded",video_url:"/1.mp4",quality_review:{status:"failed",accepted:false}}]},
      {segment_id:"clip-2",index:2,title:"Already accepted",candidates:[{id:"run-2",status:"succeeded",video_url:"/2.mp4",quality_review:{status:"failed",accepted:true}}]},
      {segment_id:"clip-3",index:3,title:"Passed",candidates:[{id:"run-3",status:"succeeded",video_url:"/3.mp4",quality_review:{status:"passed",accepted:false}}]},
    ]};
    expect(qualityAcceptanceTargets(outputs)).toEqual([
      {segment_id:"clip-1",run_id:"run-1",index:1,title:"Rejected"},
    ]);
  });

  it("skips only a prompt that is current, prepared, and non-stale",()=>{
    const current:any={video_prompt:"A complete prompt",status:"ready",project_id:"project-1",stale_reasons:[]};
    expect(segmentNeedsVideoPrompt(current)).toBe(false);
    expect(segmentNeedsVideoPrompt({...current,status:"stale"})).toBe(true);
    expect(segmentNeedsVideoPrompt({...current,video_prompt:""})).toBe(true);
    expect(segmentNeedsVideoPrompt({...current,project_id:null})).toBe(true);
  });

  it("skips only clips with an adopted completed take",()=>{
    const clip:any={id:"clip-ready",status:"ready",stale_reasons:[],prompt_updated_at:100};
    const outputs:any={segments:[{segment_id:"clip-ready",selected:{id:"run-1",created_at:101}},{segment_id:"clip-missing",selected:null}]};
    expect(segmentHasCompletedVideo(clip,outputs)).toBe(true);
    expect(segmentHasCompletedVideo({...clip,status:"stale"},outputs)).toBe(false);
    expect(segmentHasCompletedVideo({...clip,prompt_updated_at:102},outputs)).toBe(false);
    expect(segmentHasCompletedVideo({...clip,id:"clip-missing"},outputs)).toBe(false);
  });

  it("force mode includes completed prompts and videos while normal mode skips them",()=>{
    const ready:any={id:"ready",video_prompt:"Existing prompt",status:"ready",project_id:"project-1",stale_reasons:[],prompt_updated_at:100};
    const missing:any={id:"missing",video_prompt:"",status:"unprepared",project_id:null,stale_reasons:[]};
    const outputs:any={segments:[{segment_id:"ready",selected:{id:"run-1",created_at:101}},{segment_id:"missing",selected:null}]};
    expect(videoPromptTargets([ready,missing]).map(item=>item.id)).toEqual(["missing"]);
    expect(videoPromptTargets([ready,missing],true).map(item=>item.id)).toEqual(["ready","missing"]);
    expect(videoRenderTargets([ready,missing],outputs).map(item=>item.id)).toEqual(["missing"]);
    expect(videoRenderTargets([ready,missing],outputs,true).map(item=>item.id)).toEqual(["ready","missing"]);
  });
});

describe("production image dimensions",()=>{
  it("uses the project's video aspect for storyboard stills",()=>{
    expect(productionKeyframeSize("16:9")).toEqual({width:768,height:432});
    expect(productionKeyframeSize("9:16")).toEqual({width:432,height:768});
    expect(productionKeyframeSize("1:1")).toEqual({width:768,height:768});
    expect(productionKeyframeSize("4:3")).toEqual({width:768,height:576});
    expect(productionKeyframeSize("3:4")).toEqual({width:576,height:768});
  });
});
