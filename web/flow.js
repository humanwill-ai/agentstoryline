'use strict';
(()=>{
const defaultRoot=document.getElementById('tf-flow'),defaultDesign=document.getElementById('design');
defaultRoot.classList.add('f-spotlight');defaultDesign.value='spotlight';
function prepareImportPicker(){
  if(document.getElementById('import-recording'))return;
  const input=document.getElementById('open-recording'),label=input.closest('label'),picker=document.createElement('div'),trigger=document.createElement('button'),choices=document.createElement('div');
  label.closest('.f-import').setAttribute('aria-label','Import recording');input.setAttribute('aria-label','Recording file');
  picker.className='f-import-picker';trigger.id='import-recording';trigger.type='button';trigger.className='f-primary f-import-trigger';trigger.textContent='Import recording';trigger.setAttribute('aria-expanded','false');trigger.setAttribute('aria-controls','import-source-choices');
  choices.id='import-source-choices';choices.className='f-import-choices';choices.hidden=true;choices.setAttribute('role','group');choices.setAttribute('aria-label','Recording source');
  for(const [kind,name,hint,accept] of [
    ['agentstoryline','AgentStoryline','Portable recording · .json','.json,application/json'],
    ['artex','Artex','Native archive or export · .tar, .tar.zst, .json','.tar,.zst,.tar.zst,.json,application/x-tar,application/zstd,application/json'],
    ['orcareplay','OrcaReplay','Offline run archive · .orca, .orca.zip, .tar · experimental','.orca,.orca.zip,.zip,.tar,application/zip,application/x-tar'],
    ['codex','Codex','Native rollout · .jsonl · experimental','.jsonl,application/x-ndjson'],
    ['otlp','OpenTelemetry','OTLP protobuf-JSON · .json · experimental','.json,application/json']
  ]){const button=document.createElement('button'),title=document.createElement('strong'),description=document.createElement('small');button.type='button';button.dataset.importKind=kind;button.dataset.accept=accept;title.textContent=name;description.textContent=hint;button.append(title,description);choices.append(button);}
  label.replaceWith(picker);picker.append(trigger,choices,input);
  const exportButton=document.createElement('button');exportButton.id='export-recording';exportButton.type='button';exportButton.textContent='Export recording';exportButton.disabled=true;exportButton.title='Download AgentStoryline JSON with recorded evidence, descriptions and attached scripts. Original archive/workspace files are not embedded.';picker.after(exportButton);
}
prepareImportPicker();
const embedded=JSON.parse(document.getElementById('recording').textContent),initial=embedded.format==='traceforge-recording'?embedded.payload:embedded,template=document.getElementById('tf-flow').cloneNode(true),explanationTemplate=document.getElementById('explanation-panel').cloneNode(true);
let bindExplanationService=()=>{},dispose=()=>{},current=initial,attachments=embedded.attachments||[],explanationService=null,explanationBusy=false,importInFlight=false;
function hasLoadedRecording(){return Boolean(current?.bundle?.manifest?.run_id&&Array.isArray(current?.bundle?.events)&&Array.isArray(current?.flow?.messages));}
function setImportChooserDisabled(value){const disabled=value||importInFlight;document.getElementById('open-recording').disabled=disabled;document.getElementById('import-recording').disabled=disabled;for(const button of document.querySelectorAll('#import-source-choices button'))button.disabled=disabled;document.getElementById('export-recording').disabled=disabled||!hasLoadedRecording();}
function storyAxisFromFlow(flow,story,messages,activities,fallbackDuration){
  const sourceDurations=[Number(story?.duration_ms),Number(flow?.duration_ms),Number(fallbackDuration)].filter(value=>Number.isFinite(value)&&value>0);
  const recordedDuration=Math.max(1,...sourceDurations);
  const sourceAnchorTimes=[];
  for(const edge of story?.edges||[])for(const key of ['source_ms','target_ms'])if(Number.isFinite(edge[key]))sourceAnchorTimes.push(edge[key]);
  for(const association of Object.values(flow?.logical_storyline?.activity_entities||{}))if(Number.isFinite(association?.target_ms))sourceAnchorTimes.push(association.target_ms);
  const supplied=flow?.story_axis,epsilon=.01;
  if(supplied&&supplied.version===1&&Array.isArray(supplied.segments)&&supplied.segments.length){
    const suppliedRecordedDuration=Number(supplied.recorded_duration_ms),displayDuration=Number(supplied.display_duration_ms),segments=supplied.segments.map(segment=>({
      ...segment,
      recorded_start_ms:Number(segment.recorded_start_ms),recorded_end_ms:Number(segment.recorded_end_ms),
      display_start_ms:Number(segment.display_start_ms),display_end_ms:Number(segment.display_end_ms)
    }));
    const sourceClockConsistent=sourceDurations.every(value=>Math.abs(value-recordedDuration)<=epsilon);
    const invalidIdleBreak=segment=>segment.kind==='idle_break'&&(
      (activities||[]).some(activity=>
        ['interval','presentation_span'].includes(activity.timing)&&Number.isFinite(activity.start_ms)&&Number.isFinite(activity.end_ms)&&
        activity.end_ms>segment.recorded_start_ms&&activity.start_ms<segment.recorded_end_ms)||
      (messages||[]).some(message=>Number.isFinite(message.offset_ms)&&message.offset_ms>segment.recorded_start_ms&&message.offset_ms<segment.recorded_end_ms)||
      (activities||[]).some(activity=>['start_ms','end_ms'].some(key=>Number.isFinite(activity[key])&&activity[key]>segment.recorded_start_ms&&activity[key]<segment.recorded_end_ms))||
      sourceAnchorTimes.some(value=>value>segment.recorded_start_ms&&value<segment.recorded_end_ms)
    );
    const valid=sourceClockConsistent&&Number.isFinite(suppliedRecordedDuration)&&Math.abs(suppliedRecordedDuration-recordedDuration)<=epsilon&&
      Number.isFinite(displayDuration)&&displayDuration>0&&segments.every((segment,index)=>
      ['recorded','idle_break'].includes(segment.kind)&&
      [segment.recorded_start_ms,segment.recorded_end_ms,segment.display_start_ms,segment.display_end_ms].every(Number.isFinite)&&
      segment.recorded_end_ms>=segment.recorded_start_ms&&segment.display_end_ms>=segment.display_start_ms&&
      (segment.kind!=='recorded'||Math.abs((segment.recorded_end_ms-segment.recorded_start_ms)-(segment.display_end_ms-segment.display_start_ms))<=epsilon)&&
      (segment.kind!=='idle_break'||segment.display_end_ms-segment.display_start_ms<=segment.recorded_end_ms-segment.recorded_start_ms+epsilon)&&
      !invalidIdleBreak(segment)&&
      (!index||(Math.abs(segment.recorded_start_ms-segments[index-1].recorded_end_ms)<=epsilon&&Math.abs(segment.display_start_ms-segments[index-1].display_end_ms)<=epsilon))
    )&&Math.abs(segments[0].recorded_start_ms)<=epsilon&&Math.abs(segments[0].display_start_ms)<=epsilon&&
      Math.abs(segments.at(-1).recorded_end_ms-recordedDuration)<=epsilon&&Math.abs(segments.at(-1).display_end_ms-displayDuration)<=epsilon;
    if(valid)return {version:1,source:'recorded',recorded_duration_ms:recordedDuration,display_duration_ms:displayDuration,segments,
      threshold_ms:Number(supplied.threshold_ms)||30000,compact_idle_ms:Number(supplied.compact_idle_ms)||5000};
  }
  if(flow?.time_mode==='review_sequence')return {version:1,source:'fallback',recorded_duration_ms:recordedDuration,display_duration_ms:recordedDuration,segments:[{kind:'recorded',recorded_start_ms:0,recorded_end_ms:recordedDuration,display_start_ms:0,display_end_ms:recordedDuration}],threshold_ms:30000,compact_idle_ms:5000};
  const points=[0,recordedDuration];
  for(const message of messages||[])if(Number.isFinite(message.offset_ms)&&message.offset_ms>=0&&message.offset_ms<=recordedDuration)points.push(message.offset_ms);
  for(const activity of activities||[])for(const key of ['start_ms','end_ms'])if(Number.isFinite(activity[key])&&activity[key]>=0&&activity[key]<=recordedDuration)points.push(activity[key]);
  for(const value of sourceAnchorTimes)if(value>=0&&value<=recordedDuration)points.push(value);
  const ordered=[...new Set(points)].sort((a,b)=>a-b),threshold=30000,compact=5000,breaks=[];
  for(let index=0;index<ordered.length-1;index++){
    const start=ordered[index],end=ordered[index+1];
    if(end-start<threshold)continue;
    const covered=(activities||[]).some(activity=>['interval','presentation_span'].includes(activity.timing)&&Number.isFinite(activity.start_ms)&&Number.isFinite(activity.end_ms)&&activity.end_ms>start&&activity.start_ms<end);
    if(!covered)breaks.push({kind:'idle_break',recorded_start_ms:start,recorded_end_ms:end,label:'No recorded activity',basis:'Client-derived legacy fallback'});
  }
  const segments=[];let recordedCursor=0,displayCursor=0;
  for(const idle of breaks){
    if(idle.recorded_start_ms>recordedCursor){const span=idle.recorded_start_ms-recordedCursor;segments.push({kind:'recorded',recorded_start_ms:recordedCursor,recorded_end_ms:idle.recorded_start_ms,display_start_ms:displayCursor,display_end_ms:displayCursor+span});displayCursor+=span;}
    const retained=Math.min(compact,idle.recorded_end_ms-idle.recorded_start_ms);
    segments.push({...idle,display_start_ms:displayCursor,display_end_ms:displayCursor+retained,omitted_ms:idle.recorded_end_ms-idle.recorded_start_ms-retained});
    recordedCursor=idle.recorded_end_ms;displayCursor+=retained;
  }
  if(recordedCursor<recordedDuration){const span=recordedDuration-recordedCursor;segments.push({kind:'recorded',recorded_start_ms:recordedCursor,recorded_end_ms:recordedDuration,display_start_ms:displayCursor,display_end_ms:displayCursor+span});displayCursor+=span;}
  if(!segments.length)segments.push({kind:'recorded',recorded_start_ms:0,recorded_end_ms:recordedDuration,display_start_ms:0,display_end_ms:recordedDuration});
  return {version:1,source:'fallback',recorded_duration_ms:recordedDuration,display_duration_ms:displayCursor,segments,threshold_ms:threshold,compact_idle_ms:compact};
}
function storyAxisDisplayAt(axis,recordedMs){
  const value=Math.max(0,Math.min(axis.recorded_duration_ms,Number(recordedMs)||0)),segment=axis.segments.find(item=>value<=item.recorded_end_ms)||axis.segments.at(-1);
  const span=Math.max(1,segment.recorded_end_ms-segment.recorded_start_ms),fraction=Math.max(0,Math.min(1,(value-segment.recorded_start_ms)/span));
  return segment.display_start_ms+(segment.display_end_ms-segment.display_start_ms)*fraction;
}
function storyAxisRecordedAt(axis,displayMs){
  const value=Math.max(0,Math.min(axis.display_duration_ms,Number(displayMs)||0)),segment=axis.segments.find(item=>value<=item.display_end_ms)||axis.segments.at(-1);
  const span=Math.max(1,segment.display_end_ms-segment.display_start_ms),fraction=Math.max(0,Math.min(1,(value-segment.display_start_ms)/span));
  return segment.recorded_start_ms+(segment.recorded_end_ms-segment.recorded_start_ms)*fraction;
}
function validateExplanations(p){
  const messages=p.flow?.messages||[],events=new Map((p.bundle?.events||[]).map(e=>[e.id,e])),byMessage=new Map(messages.map(m=>[m.id,m]));
  const keys=(value,expected)=>value&&typeof value==='object'&&!Array.isArray(value)&&Object.keys(value).sort().join('|')===[...expected].sort().join('|');
  const shortText=value=>typeof value==='string'&&value.trim()&&[...value].length<=280&&!/[\x00-\x1f\u0085\u2028\u2029]/u.test(value);
  function refs(value,message,description=false){
    const own=message.event_ids?.[0],event=events.get(own);
    if(!event||!Array.isArray(value)||!value.length||value.length>40||value.some(id=>typeof id!=='string'||!events.has(id))||new Set(value).size!==value.length||!value.includes(own))throw new Error('Invalid explanation evidence.');
    const prior=[event.parent_id,...(event.evidence_refs||[]),...(event.dependencies||[]).filter(d=>d&&d.provenance==='recorded').map(d=>d.event_id)];
    const permitted=new Set([own,...[...new Set(prior)].filter(id=>id!==own&&events.has(id)&&(events.get(id).sequence??0)<=(event.sequence??0)).slice(0,12)]);
    const allowed=description?new Set(message.event_ids):permitted;
    if(value.some(id=>!allowed.has(id)||(events.get(id).sequence??0)>(event.sequence??0)))throw new Error('Explanation references future or unconnected evidence.');
  }
  for(const m of messages)if(m.description!==undefined){const d=m.description;if(!keys(d,['text','basis','event_ids','source_path'])||!shortText(d.text)||d.basis!=='recorded'||typeof d.source_path!=='string'||!d.source_path.trim()||[...d.source_path].length>512)throw new Error('Invalid recorded description.');refs(d.event_ids,m,true);}
  const x=p.explanations;if(x===undefined)return;
  const v2=x.version===2;
  const label=value=>typeof value==='string'&&value.trim()&&[...value.trim()].length<=200&&!/[\x00-\x1f]/u.test(value)&&!/(?:(?:sk|rk|pk)-[A-Za-z0-9_-]{12,}|(?:Basic|Bearer)\s+\S+|(?:api[_-]?key|token|password|secret)\s*[=:]\s*\S+|https?:\/\/[^/\s]+@)/i.test(value);
  const provenance=value=>keys(value,['method','model','provider','created_at','adapter_id','profile_version'])&&['offline-development-worker','provider-api'].includes(value.method)&&['model','provider','adapter_id'].every(k=>label(value[k]))&&Number.isInteger(value.profile_version)&&value.profile_version>=0&&value.profile_version<=x.profile?.version&&value.adapter_id===x.profile?.adapter_id&&(value.profile_version===0?value.created_at===null:typeof value.created_at==='string'&&/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(value.created_at)&&Number.isFinite(Date.parse(value.created_at)));
  if(!keys(x,v2?['version','run_id','bundle_sha256','provenance','annotations','profile']:['version','run_id','bundle_sha256','provenance','annotations'])||![1,2].includes(x.version)||typeof x.run_id!=='string'||x.run_id!==p.bundle?.manifest?.run_id||typeof x.bundle_sha256!=='string'||! /^[a-f0-9]{64}$/i.test(x.bundle_sha256)||!(keys(x.provenance,['method','model','provider'])&&(v2?['offline-development-worker','provider-api'].includes(x.provenance.method):x.provenance.method==='offline-development-worker')&&['model','provider'].every(k=>label(x.provenance[k])))||!Array.isArray(x.annotations)||x.annotations.length<1||x.annotations.length>(v2?5000:40))throw new Error('Invalid explanation metadata.');
  if(v2&&(!keys(x.profile,['adapter_id','version'])||!['artex','generic'].includes(x.profile.adapter_id)||x.profile.adapter_id!==(declaredAdapter(p)==='artex'?'artex':'generic')||!Number.isInteger(x.profile.version)||x.profile.version!==1))throw new Error('Invalid explanation profile.');
  const seen=new Set();for(const a of x.annotations){if(!keys(a,v2?['message_id','text','basis','event_ids','provenance']:['message_id','text','basis','event_ids'])||!byMessage.has(a.message_id)||seen.has(a.message_id)||!shortText(a.text)||!['recorded_purpose','inferred_purpose','observed_action','insufficient_information'].includes(a.basis)||(v2&&!provenance(a.provenance)))throw new Error('Invalid explanation annotation.');seen.add(a.message_id);refs(a.event_ids,byMessage.get(a.message_id));}
}
function explanationFor(message,annotations){
  const annotation=annotations.get(message.id);
  if(annotation)return {...annotation,label:annotation.basis==='inferred_purpose'?'AI interpretation':'AI summary',generated:true};
  if(message.description?.text)return {...message.description,label:'Recorded description',generated:false};
  return null;
}
function explanationCoverage(messages,annotations){
  const visible=explanationChatMessages(messages,'explained');
  return {ai:visible.filter(m=>annotations.has(m.id)).length,recorded:visible.filter(m=>!annotations.has(m.id)&&m.description?.text).length,original:visible.filter(m=>!annotations.has(m.id)&&!m.description?.text).length,bookkeeping:messages.length-visible.length};
}
function explanationMode(preference,messages,annotations){return ['original','explained'].includes(preference)?preference:messages.some(m=>m.description?.text||annotations.has(m.id))?'explained':'original';}
function explanationChatMessages(messages,mode){return mode==='explained'?messages.filter(m=>m.bookkeeping!==true):messages;}
function explanationServiceOrigin(location){return location.protocol==='http:'&&['localhost','127.0.0.1','[::1]'].includes(location.hostname);}
function explanationTargets(messages,annotations,scope,start,end){return messages.filter(m=>m.bookkeeping!==true&&m.event_ids?.length&&(scope==='missing'?!annotations.has(m.id)&&!m.description?.text:scope==='interval'?m.offset_ms>=start&&m.offset_ms<=end:true));}
function explanationTimestamp(value){const parts=String(value).trim().split(':');if(!parts.length||parts.length>3||parts.some(p=>!/^\d+(?:\.\d+)?$/.test(p)))return NaN;return parts.reduce((sum,p)=>sum*60+Number(p),0)*1000;}
async function explanationRequest(path,body){if(!explanationService||!explanationServiceOrigin(window.location))throw new Error('Start the local explanation service first.');const response=await fetch(path,{method:body===undefined?'GET':'POST',credentials:'same-origin',headers:body===undefined?{}:{'Content-Type':'application/json','X-TraceForge-Token':explanationService.token},...(body===undefined?{}:{body:JSON.stringify({...body,recording_id:explanationService.recording_id})})});const data=await response.json();if(!response.ok)throw new Error(data.error||'Local explanation service failed.');return data;}
function declaredAdapter(payload){
  const manifest=payload?.bundle?.manifest||{};
  if(manifest.source&&Object.prototype.hasOwnProperty.call(manifest.source,'adapter'))return String(manifest.source.adapter||'unknown').toLowerCase();
  if(payload?.source?.adapter_id)return payload.source.adapter_id;
  const legacy=String(manifest.agent?.name||'').toLowerCase();
  return legacy==='artex'?'artex':legacy==='codex'?'codex':legacy==='opentelemetry'?'otlp':legacy==='orcareplay'?'orcareplay':'unknown';
}
function sourceIdentity(payload){
  if(payload===current&&explanationService?.source)return explanationService.source;
  const manifest=payload?.bundle?.manifest||{},source=manifest.source||{},agent=manifest.agent||{},completeness=manifest.completeness||{};
  return {adapter_id:declaredAdapter(payload),native_format:source.format||'unknown',
    native_version:source.format_version||source.session_meta?.cli_version||agent.version||'unknown',
    capture_surface:source.capture_surface||'unknown',coverage_status:completeness.status||'unknown',
    coverage_gap_count:Array.isArray(completeness.gaps)?completeness.gaps.length:0};
}
function renderSourceBadge(payload){
  const source=sourceIdentity(payload),id=source.adapter_id;
  document.getElementById('source-badge').textContent=id==='artex'?'Artex · supported':id==='codex'?'Codex · experimental':id==='otlp'?'OpenTelemetry · experimental':id==='orcareplay'?'OrcaReplay · experimental':'Generic recording · unknown source';
  const details=[source.native_format&&source.native_format!=='unknown'?source.native_format:null,source.native_version&&source.native_version!=='unknown'?'version '+source.native_version:null,source.coverage_status==='partial'?'partial capture · '+source.coverage_gap_count+' declared gaps':null];
  document.getElementById('source-note').textContent=(payload?.sharing?.kind==='sanitized-derivative'?'Sanitized copy · ':'')+details.filter(Boolean).join(' · ')+(details.some(Boolean)?' · ':'')+'file-declared, not attested';
}
function portableRecording(payload=current){return {format:'traceforge-recording',version:1,payload,attachments};}
function initialize(p,preferences){
validateExplanations(p);
const annotations=new Map((p.explanations?.annotations||[]).map(a=>[a.message_id,a]));
let textMode=explanationMode(preferences?.textMode,p.flow?.messages||[],annotations);
const $=id=>document.getElementById(id),root=$('tf-flow'),f=p.flow||{},story=p.storyline||{},messages=f.messages||[],actors=new Map((f.actors||[]).map(a=>[a.id,a])),events=new Map((p.bundle?.events||[]).map(e=>[e.id,e])),activities=story.activities||[],duration=Math.max(1,Number(f.duration_ms)||0);
let inspectorRefresh=null;
let position=-1,playing=false,previous=0,frame=0,layout='single',selected=null,sound=false,audioContext=null,startedAt=0,soundBuffer=null,activeSounds=new Set(),lastArrival=0,selectedActivity=null;
const panels=new Map(),visible=new Set(),sourcePacing=f.pacing||{};
let pacingMode='smart',reviewPosition=-1,chatMode=preferences?.chatMode==='full'?'full':'compact';
const chatMessageVisible=m=>(chatMode!=='compact'||compactChatVisible(m))&&(textMode!=='explained'||m.bookkeeping!==true);
let playbackMessages=messages.filter(chatMessageVisible),pacing=filteredChatPacing(sourcePacing,messages,playbackMessages,f.time_mode),entries=pacing.entries||[],segments=pacing.segments||[],entryTimes=new Map(entries.map(e=>[e.message_id,e.at_ms]));
let smartAvailable=entries.length===playbackMessages.length&&segments.length>0,viewingDuration=Math.max(1,Number(pacing.duration_ms)||duration);
const zoomLevels=[1,2,4,8,16,32],storyViewport=$('story-viewport'),storyHost=$('lanes');
let storyZoom=zoomLevels.includes(preferences?.storyZoom)?preferences.storyZoom:1;
let storyLaneMode=preferences?.storyLaneMode==='source'?'source':'logical',storyAxisMode=preferences?.storyAxisMode==='recorded'?'recorded':'condensed';
let storyFollow=preferences?.storyFollow??true,storyScrollTarget=null,storyLastScrollLeft=storyViewport?.scrollLeft||0;
let storyResizeObserver=null,storyLayoutFrame=0,storyDrag=null;
let storyCursorAnchor=null;
let renderedStoryActivities=new Map();
const storyListeners=[];
const condensedStoryAxis=storyAxisFromFlow(f,story,messages,activities,story.duration_ms||duration);
const storyIdleBreaks=condensedStoryAxis.segments.filter(segment=>segment.kind==='idle_break');
const storyRecordedDuration=condensedStoryAxis.recorded_duration_ms;
const storyDisplayDuration=()=>storyAxisMode==='condensed'?condensedStoryAxis.display_duration_ms:storyRecordedDuration;
const storyDisplayAt=recordedMs=>storyAxisMode==='condensed'?storyAxisDisplayAt(condensedStoryAxis,recordedMs):Math.max(0,Math.min(storyRecordedDuration,Number(recordedMs)||0));
const storyRecordedAt=displayMs=>storyAxisMode==='condensed'?storyAxisRecordedAt(condensedStoryAxis,displayMs):Math.max(0,Math.min(storyRecordedDuration,Number(displayMs)||0));
const storyAxisFraction=recordedMs=>Math.max(0,Math.min(1,storyDisplayAt(recordedMs)/Math.max(1,storyDisplayDuration())));
const storyAxisSpan=(startMs,endMs)=>Math.max(0,storyDisplayAt(endMs)-storyDisplayAt(startMs));

function storyListen(type,fn,options){
  if(!storyViewport)return;
  storyViewport.addEventListener(type,fn,options);
  storyListeners.push([type,fn,options]);
}
function storyGeometry(){
  const track=storyHost.querySelector('.f-story-track');
  const hostRect=storyHost.getBoundingClientRect(),trackRect=track?.getBoundingClientRect();
  return {
    label:trackRect?trackRect.left-hostRect.left:130,
    width:trackRect?.width||Math.max(1,storyHost.clientWidth-130)
  };
}
function storyFraction(){
  return storyAxisFraction(storyCursorRecordedAt());
}
function setStoryScroll(value){
  if(!storyViewport)return;
  const target=Math.max(0,Math.min(storyViewport.scrollWidth-storyViewport.clientWidth,value));
  storyScrollTarget=target;
  storyViewport.scrollLeft=target;
}
function setStoryFollow(value){
  storyFollow=value;
  const button=$('story-follow');
  if(button)button.setAttribute('aria-pressed',String(value));
}
function followStory(force=false){
  if(!storyViewport||!storyFollow||untimed||(!playing&&!force))return;
  const {label,width}=storyGeometry(),x=label+storyFraction()*width;
  const left=storyViewport.scrollLeft,visibleWidth=storyViewport.clientWidth;
  if(force||x<left+label+24||x>left+visibleWidth*.8)setStoryScroll(x-visibleWidth*.45);
}
function sizeStory(zoom=storyZoom){
  if(!storyViewport)return;
  const old=storyGeometry(),viewportWidth=storyViewport.clientWidth;
  if(!viewportWidth)return;
  const fraction=Math.max(0,Math.min(1,(storyViewport.scrollLeft+viewportWidth/2-old.label)/old.width));
  const cursorX=old.label+storyFraction()*old.width-storyViewport.scrollLeft;
  const anchorPlayhead=playing&&storyFollow&&!untimed;
  storyZoom=zoom;
  storyHost.style.width=(Math.max(1,viewportWidth-old.label)*zoom+old.label)+'px';
  const geometry=storyGeometry(),durationSpan=Math.max(1,storyDisplayDuration());
  for(const clip of storyHost.querySelectorAll('.f-story-clip')){
    const original=(typeof renderedStoryActivities!=='undefined'?renderedStoryActivities.get(clip.dataset.id):null)||activities.find(a=>a.id===clip.dataset.id);
    const a=clip.dataset.contact==='true'?{...original,label:clip.dataset.label,start_ms:Number(clip.dataset.anchor),end_ms:Number(clip.dataset.anchor),timing:'instant'}:{...original,label:clip.dataset.label||original.label};
    const span=storyAxisSpan(a.start_ms,a.end_ms??a.start_ms),intervalTiming=['interval','presentation_span'].includes(a.timing);
    const point=clip.dataset.annotation==='true'||!intervalTiming||span/durationSpan*geometry.width<36;
    clip.classList.toggle('f-story-point',point);
    clip.classList.toggle('f-point-end',point&&storyAxisFraction(a.start_ms??a.end_ms)>.8);
    clip.style.width=point?'':span/durationSpan*100+'%';
    clip.replaceChildren();
    if(point){
      const symbol=a.timing==='clock_conflict'||a.timing==='incomplete'?'◇':'◆';
      clip.append(document.createTextNode(symbol),n('span',a.label||a.kind,'f-point-caption'));
    }else clip.textContent=a.label||a.kind;
    // Sizing rebuilds point/bar contents; restore the evidence markers each time.
    markStoryClip(clip,a);
  }
  for(const track of storyHost.querySelectorAll('.f-story-track')){
    const clips=[...track.querySelectorAll('.f-story-clip')];
    const tierHeight=clips.length&&typeof getComputedStyle==='function'?parseFloat(getComputedStyle(clips[0]).height)||27:27;
    track.style.height=tierHeight*Math.max(1,Number(track.dataset.tiers)||1)+'px';
    for(const clip of clips)if(clip.dataset.level!==undefined)clip.style.top=tierHeight*(Number(clip.dataset.level)||0)+'px';
    for(const clip of clips){
      const caption=clip.querySelector('.f-point-caption');
      if(!caption)continue;
      const x=parseFloat(clip.style.left)/100*geometry.width,end=clip.classList.contains('f-point-end');
      const neighbors=clips.filter(other=>other!==clip&&other.style.top===clip.style.top)
        .map(other=>parseFloat(other.style.left)/100*geometry.width);
      const gap=end?x-Math.max(0,...neighbors.filter(other=>other<x))
        :Math.min(geometry.width,...neighbors.filter(other=>other>x))-x;
      const marked=clip.classList.contains('f-story-marked');
      const available=end?x:geometry.width-x;
      caption.style.maxWidth=Math.max(0,Math.min(marked?260:120,(marked?available-42:gap-18)))+'px';
      clip.dataset.captionCrowded=String(gap<56);
    }
  }
  const ticks=storyHost.querySelector('.f-story-ticks');
  if(ticks){
    ticks.replaceChildren();
    const count=Math.max(4,Math.min(300,Math.floor(geometry.width/120)));
    for(let i=0;i<=count;i++){
      const displayMs=storyDisplayDuration()*i/count,recordedMs=storyRecordedAt(displayMs);
      if(storyAxisMode==='condensed'&&storyIdleBreaks.some(gap=>recordedMs>gap.recorded_start_ms&&recordedMs<gap.recorded_end_ms))continue;
      const tick=n('span',clock(recordedMs),'f-story-tick');tick.style.left=(i/count*100)+'%';
      if(i===0)tick.dataset.edge='start';else if(i===count)tick.dataset.edge='end';ticks.append(tick);
    }
    if(storyAxisMode==='condensed')for(const [index,gap] of storyIdleBreaks.entries()){
      const recordedSpan=gap.recorded_end_ms-gap.recorded_start_ms,minutes=Math.floor(recordedSpan/60000),secondsPart=Math.floor(recordedSpan/1000)%60;
      const durationLabel=(minutes?minutes+'m ':'')+secondsPart+'s',label=durationLabel+' · no recorded activity';
      const button=btn(label,()=>inspectStoryBreak(gap),'f-story-break');button.dataset.start=String(gap.recorded_start_ms);button.dataset.end=String(gap.recorded_end_ms);
      button.style.left=((storyDisplayAt(gap.recorded_start_ms)+storyAxisSpan(gap.recorded_start_ms,gap.recorded_end_ms)/2)/durationSpan*100)+'%';
      button.title='Recorded '+clock(gap.recorded_start_ms)+'–'+clock(gap.recorded_end_ms)+' · '+label+' · storyline axis condensed';
      button.setAttribute('aria-label','Collapsed interval from '+clock(gap.recorded_start_ms)+' to '+clock(gap.recorded_end_ms)+', '+durationLabel+' with no recorded activity');
      button.setAttribute('aria-controls','detail');
      const description=n('span','Presentation-only condensation; original timestamps and evidence are unchanged.','f-sr-only');description.id='story-break-description-'+index;
      button.setAttribute('aria-describedby',description.id);ticks.append(button,description);
    }
    const rows=[...storyHost.querySelectorAll('.f-story-row')],axisRect=storyHost.querySelector('.f-story-axis')?.getBoundingClientRect(),lastRect=rows.at(-1)?.getBoundingClientRect();
    const breakHeight=Math.max(27,(lastRect?.bottom??axisRect?.bottom??0)-(axisRect?.bottom??0));
    const playheadTrack=storyHost.querySelector('.f-story-playhead-track');
    if(playheadTrack){
      const hostRect=storyHost.getBoundingClientRect(),axisHeight=storyHost.querySelector('.f-story-axis')?.offsetHeight||24;
      playheadTrack.style.left=geometry.label+'px';playheadTrack.style.width=geometry.width+'px';
      playheadTrack.style.top=axisHeight+'px';playheadTrack.style.height=Math.max(0,(lastRect?.bottom??hostRect.top+axisHeight)-hostRect.top-axisHeight)+'px';
    }
    for(const button of ticks.querySelectorAll('.f-story-break'))button.style.setProperty('--f-story-break-height',breakHeight+'px');
  }
  setStoryScroll(geometry.label+(anchorPlayhead?storyFraction():fraction)*geometry.width
    -(anchorPlayhead?cursorX:viewportWidth/2));
  const output=$('story-zoom-level');
  if(output)output.textContent=zoom===1?'Fit':zoom+'×';
  if($('story-zoom-out'))$('story-zoom-out').disabled=zoom===1;
  if($('story-zoom-in'))$('story-zoom-in').disabled=zoom===32;
  storyHost.dataset.zoom=String(zoom);
  drawDependencies();
}
function storySeekRecorded(recordedMs){
  const value=Math.max(0,Math.min(duration,recordedMs));
  let target=value;
  if(isSmart()){
    // Recorded holds have multiple viewing positions; choose their first arrival.
    const hold=segments.find(s=>s.recorded_start_ms===value&&s.recorded_end_ms===value);
    const segment=segments.find(s=>value>=s.recorded_start_ms&&value<=s.recorded_end_ms);
    target=value>=duration?viewingDuration:hold?hold.start_ms:segment?
      segment.start_ms+(value-segment.recorded_start_ms)/Math.max(1,segment.recorded_end_ms-segment.recorded_start_ms)*(segment.end_ms-segment.start_ms):0;
  }
  seek(target);
}
function storySeekPointer(event){
  const track=storyHost.querySelector('.f-story-track')||storyHost.querySelector('.f-story-ticks');
  if(!track)return;
  const rect=track.getBoundingClientRect(),fraction=Math.max(0,Math.min(1,(event.clientX-rect.left)/Math.max(1,rect.width)));
  storySeekRecorded(storyRecordedAt(fraction*storyDisplayDuration()));
}
function finishStoryDrag(resume=true){
  if(!storyDrag)return;
  const drag=storyDrag;storyDrag=null;
  root.classList.remove('f-story-dragging');
  if(storyViewport.hasPointerCapture?.(drag.id))storyViewport.releasePointerCapture(drag.id);
  if(resume&&drag.playing){playing=true;startedAt=previous=lastArrival=performance.now();frame=requestAnimationFrame(tick);}
  render(false);
}
function setupStoryNavigation(){
  if(!storyViewport)return;
  cancelAnimationFrame(storyLayoutFrame);
  setStoryFollow(storyFollow);
  for(const [id,change] of [['story-zoom-out',-1],['story-zoom-in',1]]){
    if($(id))$(id).onclick=()=>sizeStory(zoomLevels[
      Math.max(0,Math.min(zoomLevels.length-1,zoomLevels.indexOf(storyZoom)+change))
    ]);
  }
  if($('story-zoom-reset'))$('story-zoom-reset').onclick=()=>sizeStory(1);
  if($('story-follow'))$('story-follow').onclick=()=>{
    setStoryFollow(!storyFollow);
    followStory(true);
  };
  storyListen('pointerdown',event=>{
    if(untimed||storyDrag||event.button!==0||!event.target.closest('.f-story-cursor'))return;
    event.preventDefault();
    storyDrag={id:event.pointerId,playing};playing=false;cancelAnimationFrame(frame);stopAudio();
    root.classList.add('f-story-dragging');
    storyViewport.setPointerCapture(event.pointerId);
    storyHost.querySelector('.f-story-handle')?.focus({preventScroll:true});
    storySeekPointer(event);
  });
  storyListen('pointermove',event=>{if(storyDrag?.id===event.pointerId){event.preventDefault();storySeekPointer(event);}});
  storyListen('pointerup',event=>{if(storyDrag?.id===event.pointerId){storySeekPointer(event);finishStoryDrag();}});
  storyListen('pointercancel',event=>{if(storyDrag?.id===event.pointerId)finishStoryDrag();});
  storyListen('lostpointercapture',event=>{if(storyDrag?.id===event.pointerId)finishStoryDrag();});
  storyListen('keydown',event=>{
    if(!event.target.closest('.f-story-handle'))return;
    const step=event.shiftKey?10000:1000;
    const cursor=storyCursorRecordedAt();
    const next={ArrowLeft:cursor-step,ArrowDown:cursor-step,ArrowRight:cursor+step,ArrowUp:cursor+step,Home:0,End:duration}[event.key];
    if(next===undefined)return;
    event.preventDefault();event.stopImmediatePropagation();storySeekRecorded(next);
  });
  storyListen('wheel',event=>{
    if(Math.abs(event.deltaX)>0||event.shiftKey)setStoryFollow(false);
  },{passive:true});
  storyListen('keydown',event=>{
    if(['ArrowLeft','ArrowRight'].includes(event.key))setStoryFollow(false);
  });
  storyListen('scroll',()=>{
    const left=storyViewport.scrollLeft;
    // Vertical lane browsing does not change the horizontal following preference.
    if(Math.abs(left-storyLastScrollLeft)<.5)return;
    storyLastScrollLeft=left;
    if(storyScrollTarget!==null&&Math.abs(left-storyScrollTarget)<2){
      storyScrollTarget=null;
      return;
    }
    storyScrollTarget=null;
    setStoryFollow(false);
  },{passive:true});
  const resize=()=>{
    cancelAnimationFrame(storyLayoutFrame);
    storyLayoutFrame=requestAnimationFrame(()=>sizeStory());
  };
  window.addEventListener('resize',resize);
  storyListeners.push(['window-resize',resize]);
  if(typeof ResizeObserver!=='undefined'){
    storyResizeObserver=new ResizeObserver(resize);
    storyResizeObserver.observe(storyViewport);
  }
  sizeStory();
}
const arrivalSpacing=750,typingDelay=350;
const scheduledAt=m=>isSmart()?(entryTimes.get(m.id)??Infinity):Number(m.offset_ms);
const pendingMessage=()=>playbackMessages.find(m=>!visible.has(m.id));
const untimed=f.time_mode==='review_sequence';
const isSmart=()=>pacingMode==='smart'&&smartAvailable;
const playbackDuration=()=>isSmart()?viewingDuration:duration;
const playPosition=()=>isSmart()?reviewPosition:position;
function currentSegment(){if(reviewPosition<0)return null;return segments.find(s=>reviewPosition>=s.start_ms&&reviewPosition<s.end_ms)||segments.at(-1);}
function recordedAt(ms){if(ms<0)return -1;const s=segments.find(s=>ms>=s.start_ms&&ms<s.end_ms)||segments.at(-1);if(!s)return ms;const fraction=Math.max(0,Math.min(1,(ms-s.start_ms)/Math.max(1,s.end_ms-s.start_ms)));return s.recorded_start_ms+(s.recorded_end_ms-s.recorded_start_ms)*fraction;}
function storyCursorRecordedAt(ms=reviewPosition){
  if(!isSmart()||untimed||ms<0||storyDrag)return position;
  // This is a presentation cursor only. The evidence clock and message arrivals
  // continue to use recordedAt() and the original reading schedule.
  const first=segments.find(segment=>segment.kind!=='lead')||segments[0];
  let start=first?.start_ms??0,recordedStart=first?.recorded_start_ms??0;
  const minimumTravel=storyDisplayDuration()/Math.max(1,storyGeometry().width);
  for(const segment of segments){
    // Distinct timestamps can be only microseconds apart. Treat endpoints less
    // than one visible pixel away as a group, so their reading holds share the
    // travel to the next visible point instead of appearing to stop at each one.
    if(segment.recorded_end_ms<=recordedStart||storyDisplayAt(segment.recorded_end_ms)-storyDisplayAt(recordedStart)<minimumTravel)continue;
    if(ms<=segment.end_ms){
      // Include all holds at the same recorded point in the travel to the next
      // distinct point. Interpolate on the chosen axis, including its breaks.
      if(storyCursorAnchor&&storyCursorAnchor.viewing_ms>=start&&storyCursorAnchor.viewing_ms<=segment.end_ms){
        start=storyCursorAnchor.viewing_ms;recordedStart=storyCursorAnchor.recorded_ms;
      }
      const fraction=Math.max(0,Math.min(1,(ms-start)/Math.max(1,segment.end_ms-start)));
      const from=storyDisplayAt(recordedStart),to=storyDisplayAt(segment.recorded_end_ms);
      return storyRecordedAt(from+(to-from)*fraction);
    }
    start=segment.end_ms;recordedStart=segment.recorded_end_ms;
  }
  return position;
}
function shownMessages(){return playbackMessages.filter(m=>isSmart()?(entryTimes.get(m.id)??Infinity)<=reviewPosition:Number(m.offset_ms)<=position);}
function viewingAt(ms){if(ms<0)return -1;const revealed=messages.filter(m=>visible.has(m.id));const last=revealed.at(-1),entry=entries.find(e=>e.message_id===last?.id);const gap=segments.find(s=>s.kind==='gap'&&ms>s.recorded_start_ms&&ms<s.recorded_end_ms);if(gap)return gap.start_ms+(ms-gap.recorded_start_ms)/Math.max(1,gap.recorded_end_ms-gap.recorded_start_ms)*(gap.end_ms-gap.start_ms);return entry?.at_ms??0;}
function refreshChatSchedule(){
  playbackMessages=messages.filter(chatMessageVisible);pacing=filteredChatPacing(sourcePacing,messages,playbackMessages,f.time_mode);
  entries=pacing.entries||[];segments=pacing.segments||[];entryTimes=new Map(entries.map(e=>[e.message_id,e.at_ms]));
  smartAvailable=entries.length===playbackMessages.length&&segments.length>0;viewingDuration=Math.max(1,Number(pacing.duration_ms)||duration);
  if(position>=0)storySeekRecorded(position);
}
const seconds=ms=>(Math.max(0,ms)/1000).toFixed(1).replace(/\.0$/,'')+'s';

function n(tag,text,cls){const el=document.createElement(tag);if(text!==undefined)el.textContent=String(text);if(cls)el.className=cls;return el;}
function btn(text,fn,cls){const b=n('button',text,cls);b.type='button';b.onclick=fn;return b;}
const json=v=>typeof v==='string'?v:JSON.stringify(v,null,2),clock=ms=>{const sec=Math.max(0,ms)/1000;return Math.floor(sec/60).toString().padStart(2,'0')+':'+Math.floor(sec%60).toString().padStart(2,'0');};
const recordedCoordinators=f.participants?.coordinators||[...actors.values()].filter(a=>a.avatar_kind==='coordinator'||a.role==='coordinator');
const singletonCoordinator=recordedCoordinators.length===1;
const presentationName=name=>singletonCoordinator&&name==='Coordinator 1'?'Coordinator':name;
const displayName=a=>presentationName(a?.display_name||a?.name||'Recorded source');
const displayRole=a=>a?.display_role||a?.role||'Recorded source';
// Logical chat entities are additive presentation metadata; actor evidence is unchanged.
function deriveChatEntities(flow){
  const sourceActors=flow.actors||[],declared=flow.entities||[],entities=new Map(declared.map(e=>[e.id,e])),actorEntities=new Map(),fallbackEntities=new Map();
  for(const entity of declared)for(const actorId of entity.actor_ids||[])if(!actorEntities.has(actorId))actorEntities.set(actorId,entity.id);
  for(const actor of sourceActors){
    const role=actor.avatar_kind||actor.display_role||actor.role,participant=actor.participant_identity;
    const key=actor.role==='model'?'legacy:model:'+JSON.stringify([actor.display_name||actor.name,actor.provider||'',actor.display_model_basis||'',actor.model||'']):participant?'legacy:participant:'+participant:'legacy:actor:'+actor.id;
    if(!fallbackEntities.has(key)){
      const participantName=actor.associated_worker_name||actor.associated_coordinator_name;
      const participantRole=actor.associated_worker_name?'worker':actor.associated_coordinator_name?'coordinator':role;
      fallbackEntities.set(key,{...actor,id:key,kind:actor.role==='model'?'model':participant?participantRole:role,display_name:participant&&actor.role!=='model'?(participantName||actor.display_name||actor.name):actor.display_name||actor.name,display_role:participant&&actor.role!=='model'?participantRole:actor.display_role||actor.role,avatar_kind:participant&&actor.role!=='model'?participantRole:actor.avatar_kind||actor.role,actor_ids:[]});
    }
    const entity=fallbackEntities.get(key);entity.actor_ids.push(actor.id);
    if(actor.role==='source'&&entity.role!=='source')Object.assign(entity,{...actor,id:key,actor_ids:entity.actor_ids});
    if(!actorEntities.has(actor.id))actorEntities.set(actor.id,key);
  }
  for(const [id,entity] of fallbackEntities)if([...actorEntities.values()].includes(id))entities.set(id,entity);
  const messageEntity=m=>entities.has(m.entity_id)?m.entity_id:actorEntities.get(m.actor_id)||'legacy:unattributed';
  const messageOwner=m=>entities.has(m.owner_entity_id)?m.owner_entity_id:messageEntity(m);
  for(const message of flow.messages||[])if(!entities.has(messageEntity(message)))entities.set(messageEntity(message),{id:messageEntity(message),display_name:'Recorded source',display_role:'record',avatar_kind:'records'});
  return {entities,messageEntity,messageOwner};
}
function participantStripEntities(flow,entities,entityFor,ownerFor){
  const used=new Set((flow.messages||[]).flatMap(m=>[entityFor(m),ownerFor(m),m.target_entity_id,m.model_entity_id]).filter(Boolean));
  // Source records and annotations retain their identities in chat/details and source lanes.
  return [...entities.values()].filter(entity=>used.has(entity.id)&&entity.primary_lane!==false);
}
function targetDisplayName(entity){
  const name=entity?.display_name||entity?.name||'Recorded target';
  return /^Target(?:\s*·|\b)/i.test(name)?name:'Target · '+name;
}
function compactChatVisible(message){
  return message.compact_hidden!==true||!['observation','context'].includes(message.kind)||Boolean(message.semantic_tags?.length);
}
function filteredChatPacing(sourcePacing,sourceMessages,shownMessages,timeMode){
  if(sourceMessages.length===shownMessages.length)return sourcePacing;
  const result={version:1,duration_ms:0,entries:[],segments:[]};
  if(!shownMessages.length)return result;
  const sourceEntries=new Map((sourcePacing.entries||[]).map(entry=>[entry.message_id,entry]));
  if(shownMessages.some(message=>!sourceEntries.has(message.id)))return sourcePacing;
  let cursor=0,previous=0;
  function segment(duration,start,end,kind,label){result.segments.push({start_ms:cursor,end_ms:cursor+duration,recorded_start_ms:start,recorded_end_ms:end,kind,label});cursor+=duration;}
  function gap(offset){if(offset>previous)segment(Math.min(2500,Math.max(80,Math.round(Math.sqrt(offset-previous)*16))),previous,offset,'gap',timeMode==='recorded'?'Recorded interval':'Review sequence interval');previous=offset;}
  segment(400,0,0,'lead','Preparing review');
  for(const message of shownMessages){
    const source=sourceEntries.get(message.id),offset=Number(message.offset_ms),dwell=source.dwell_ms;
    gap(offset);result.entries.push({...source,at_ms:cursor});segment(dwell,offset,offset,'hold','Reading message');
  }
  // A hidden tail advances the recorded clock without adding a reading hold.
  gap(Number(sourceMessages.at(-1)?.offset_ms)||previous);result.duration_ms=cursor;
  return result;
}
// Pair only unique structural correlations, never adjacent messages or tool names.
function compactChatEntries(sourceMessages,allMessages,entityFor,ownerFor,correlatedIds){
  const tools=correlatedIds?[]:allMessages.filter(m=>['tool.start','tool.end'].includes(m.kind)),pairs=correlatedIds||new Map(),paired=new Set(),order=correlatedIds?null:new Map(allMessages.map((m,i)=>[m.id,i]));
  if(!correlatedIds)for(const field of ['activity_id','call_id']){
    const groups=new Map();
    for(const message of tools){
      if(!message[field])continue;
      const key=JSON.stringify([ownerFor(message),message[field]]);
      if(!groups.has(key))groups.set(key,[]);
      groups.get(key).push(message);
    }
    for(const group of groups.values()){
      const starts=group.filter(m=>m.kind==='tool.start'),ends=group.filter(m=>m.kind==='tool.end');
      if(starts.length!==1||ends.length!==1||paired.has(starts[0].id)||paired.has(ends[0].id))continue;
      const start=starts[0],end=ends[0];
      if(start.call_id&&end.call_id&&start.call_id!==end.call_id)continue;
      // A result preceding its call cannot establish a presentation update.
      if(order.get(start.id)>order.get(end.id))continue;
      pairs.set(start.id,start.id);pairs.set(end.id,start.id);paired.add(start.id);paired.add(end.id);
    }
  }
  const result=[],entries=new Map();
  for(const message of sourceMessages){
    // Full chat, inspectors and source lanes keep every original message.
    if(!compactChatVisible(message))continue;
    const action=['tool.start','tool.end'].includes(message.kind),id=action?(pairs.get(message.id)||message.id):message.id;
    if(!entries.has(id)){
      const entry={id,action,entity_id:action?ownerFor(message):entityFor(message),messages:[]};
      entries.set(id,entry);result.push(entry);
    }
    entries.get(id).messages.push(message);
  }
  return result;
}
const chatIdentity=deriveChatEntities(f),chatEntities=chatIdentity.entities,entityId=chatIdentity.messageEntity,ownerId=chatIdentity.messageOwner;
const entityActor=m=>chatEntities.get(entityId(m))||actors.get(m.actor_id),ownerActor=m=>chatEntities.get(ownerId(m))||actors.get(m.actor_id);
const actionPairs=new Map(compactChatEntries(messages,messages,entityId,ownerId).flatMap(entry=>entry.messages.map(m=>[m.id,entry.id])));
const compactTotals={original:compactChatEntries(messages,messages,entityId,ownerId,actionPairs).length,explained:compactChatEntries(explanationChatMessages(messages,'explained'),messages,entityId,ownerId,actionPairs).length};
const messageRole=(a,m)=>['worker','coordinator'].includes(a?.avatar_kind)?({'tool.start':'Tool call','tool.end':'Tool result','model.request':'Model request','model.response':'Model response','task.input':'Task input','parameters':'Parameters'}[m.kind]||m.kind.replaceAll('.',' ')):displayRole(a);
// Native SVG keeps recorded worker and coordinator identities readable at every size.
function robotAvatar(coordinator){return '<svg class="f-robot-avatar" viewBox="0 0 48 48" aria-hidden="true" focusable="false" xmlns="http://www.w3.org/2000/svg">'+(coordinator?'<path d="M8 21C3 20 3 14 7 12l7 1C17 0 31 0 34 13l7-1c4 2 4 8-1 9Z" fill="#342a32" stroke="#e9b861" stroke-width="2"/><path d="M16 17h16" stroke="#e9b861" stroke-width="2"/><circle cx="24" cy="10" r="3" fill="#fff0c9"/><path d="m20 14 8-4m-8 0 8 4" stroke="#fff0c9" stroke-width="1.4"/>':'<path d="M24 6v6" stroke="currentColor" stroke-width="2.5"/><circle cx="24" cy="5" r="2.5" fill="currentColor"/>')+'<rect x="7" y="24" width="5" height="10" rx="2" fill="currentColor"/><rect x="36" y="24" width="5" height="10" rx="2" fill="currentColor"/><rect x="11" y="'+(coordinator?'20':'13')+'" width="26" height="25" rx="7" fill="'+(coordinator?'#efba70':'currentColor')+'" stroke="'+(coordinator?'#805334':'#243c39')+'" stroke-width="2"/><rect x="15" y="24" width="18" height="9" rx="4" fill="#243c39"/>'+ (coordinator?'<path d="m13 22 21 12" stroke="#342a32" stroke-width="2"/><circle cx="29" cy="28" r="4.5" fill="#342a32"/><circle cx="19" cy="28" r="2" fill="#fff0c9"/>':'<circle cx="19" cy="28" r="2" fill="#e8fff4"/><circle cx="29" cy="28" r="2" fill="#e8fff4"/>')+'<path d="M20 37h8" stroke="#243c39" stroke-width="2" stroke-linecap="round"/></svg>';}
function avatar(a){const role=a?.avatar_kind||a?.role||'records',el=n('span',undefined,'f-avatar');el.dataset.avatar=role;el.title=displayName(a);if(/^#[0-9a-f]{6}$/i.test(a?.accent||a?.color||''))el.style.setProperty('--actor-accent',a.accent||a.color);if(role==='model'||role==='openai'||a?.role==='model'||a?.kind==='model'){if(a.avatar_kind==='openai'||String(a.provider||'').toLowerCase()==='openai'){el.textContent='OA';}else el.textContent='◈';}else if(['agent','worker','coordinator'].includes(role)){el.innerHTML=robotAvatar(role==='coordinator');if(role==='worker'){const badge=n('small',displayName(a).match(/\d+/)?.[0]||'W','f-avatar-badge');el.append(badge);}}else el.textContent=['task','system','records','document'].includes(role)?'▤':role==='target'?'◎':role==='user'?'◉':role==='tool'?'⌘':'▤';return el;}

function compactTimestamp(value){if(!value)return 'Time unavailable';const match=String(value).match(/T(\d{2}:\d{2}:\d{2})/);return match?match[1]:String(value);}
function buildParticipants(){
  const host=$('participants'),meta=f.participants||{},displayed=participantStripEntities(f,chatEntities,entityId,ownerId);
  for(const entity of displayed){
    const chip=n('span',undefined,'f-participant');chip.append(avatar(entity),n('span',displayName(entity)));
    if(entity.display_model_basis==='caller_declared_profile')chip.append(n('small','Declared','f-declared'));
    chip.title=[displayRole(entity),entity.display_model_basis?'Model basis: '+entity.display_model_basis:''].filter(Boolean).join(' · ');host.append(chip);
  }
  const workers=meta.workers||displayed.filter(e=>e.avatar_kind==='worker'),coordinators=meta.coordinators||displayed.filter(e=>e.avatar_kind==='coordinator'),count=meta.worker_count??workers.length;
  host.append(n('span',count+' recorded worker'+(count===1?'':'s')+(coordinators.length?' · '+coordinators.length+' recorded coordinator'+(coordinators.length===1?'':'s'):' · Coordinator not captured'),'f-participant-coverage'));
  const timestamp=messages.find(m=>m.timestamp)?.timestamp;const recordedDays=new Set(messages.filter(m=>m.timestamp).map(m=>{const value=String(m.timestamp),date=value.match(/^\d{4}-\d{2}-\d{2}/)?.[0],zone=value.match(/(?:Z|[+-]\d{2}:?\d{2})$/)?.[0];return date+'|'+zone;}));if(recordedDays.size>1){$('run-time').textContent='Times as recorded · multiple dates or timezones';$('run-time').title='Each message preserves its original clock time; inspect its full timestamp.';}else if(timestamp){const match=String(timestamp).match(/^(\d{4}-\d{2}-\d{2})T.*?(Z|[+-]\d{2}:?\d{2})$/);$('run-time').textContent=match?'Recorded '+match[1]+' · '+(match[2]==='Z'?'UTC':'UTC'+match[2]):'Recorded date/timezone: inspect original timestamps';$('run-time').title=timestamp;}else $('run-time').textContent='Recorded date/time unavailable';}

function kind(m){if(m.kind==='parameters')return 'parameters';if(m.kind==='tool.start'||m.kind==='tool')return 'tool';if(m.kind==='tool.end'||m.kind==='output')return 'output';if(m.kind==='prompt'||m.kind==='task.input'||m.kind==='model.request'||actors.get(m.actor_id)?.role==='user')return 'prompt';return 'message';}
function mentionText(el,text){const names=[...new Set([...actors.values()].flatMap(a=>[displayName(a),a.name]))].filter(x=>typeof x==='string'&&x.length).sort((a,b)=>b.length-a.length);let rest=String(text||'');while(rest){let index=-1,name=null;for(const x of names){const i=rest.indexOf('@'+x);if(i>=0&&(index<0||i<index)&&(!rest[i+x.length+1]||/[\s.,!?;:]/.test(rest[i+x.length+1]))){index=i;name=x;}}if(index<0){el.append(document.createTextNode(rest));break;}el.append(document.createTextNode(rest.slice(0,index)),n('span','@'+name,'f-mention'));rest=rest.slice(index+name.length+1);}}
function section(label,value){const box=n('div');box.append(n('div',label,'f-source'),n('pre',json(value)));return box;}
function artifactIsShared(payload){return payload.sharing?.kind==='sanitized-derivative'||payload.bundle?.manifest?.source?.sanitization?.kind==='sanitized-derivative';}
let artifactListPromise=null;const artifactChecks=new Set();
async function artifactRequest(path,artifactId){
  if(artifactIsShared(p))throw new Error('Original file bytes are omitted from this sanitized derivative.');
  const service=explanationService,identity=service?.recording_id,recording=p;
  if(!service||!explanationServiceOrigin(window.location))throw new Error('File bytes unavailable in standalone or portable viewing. Open the original local bundle in the local service.');
  const response=await fetch(path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-TraceForge-Token':service.token},body:JSON.stringify({recording_id:identity,...(artifactId?{artifact_id:artifactId}:{})})});
  const valid=()=>!explanationDisposed&&current===recording&&explanationService===service&&service.recording_id===identity;
  if(!valid())throw new Error('Recording changed; reopen the file.');
  if(path==='/api/artifact-download'){
    if(!response.ok){const data=await response.json();throw new Error(data.error||'File download unavailable.');}
    if(response.headers.get('X-TraceForge-Recording-Id')!==identity)throw new Error('File response belongs to another recording.');
    const blob=await response.blob();if(!valid())throw new Error('Recording changed; reopen the file.');return blob;
  }
  const data=await response.json();if(!valid())throw new Error('Recording changed; reopen the file.');
  if(!response.ok)throw new Error(data.error||'File bytes unavailable.');
  if(data.recording_id!==identity)throw new Error('File response belongs to another recording.');return data;
}
function artifactAvailability(){return artifactListPromise??=artifactRequest('/api/artifacts').catch(error=>{artifactListPromise=null;throw error;});}
function artifactControls(host,file){
  const status=n('p',artifactIsShared(p)?'Original file bytes omitted from sanitized derivative.':'File bytes unavailable until checked against the local service.','f-source'),actions=n('div',undefined,'f-artifact-actions'),preview=btn('Preview',()=>run(false)),download=btn('Download',()=>run(true)),content=n('pre',undefined,'f-artifact-preview');
  status.setAttribute('role','status');preview.disabled=true;download.disabled=true;actions.append(preview,download);host.append(status,actions,content);
  let artifact=null,serial=0;
  async function run(save){const ticket=++serial;preview.disabled=true;download.disabled=true;status.textContent=save?'Preparing download…':'Loading bounded text preview…';try{
    const data=await artifactRequest(save?'/api/artifact-download':'/api/artifact-preview',artifact.artifact_id);
    if(!host.isConnected||ticket!==serial)return;
    if(save){const url=URL.createObjectURL(new Blob([data],{type:'application/octet-stream'})),link=document.createElement('a');link.href=url;link.download=String(artifact.name||file.path||'artifact').split(/[\\/]/).at(-1)||'artifact';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);status.textContent='Downloaded original bytes as an attachment.';}
    else {content.textContent=data.binary?'Binary file · text preview unavailable. Download original bytes for inspection.':String(data.text??'');status.textContent=data.binary?'Binary file · no embedded rendering.':data.truncated?'Bounded text preview · truncated; download for full bytes.':'Text preview · displayed literally; scripts and HTML never execute.';}
  }catch(error){if(host.isConnected&&ticket===serial)status.textContent=error.message;}finally{if(host.isConnected&&ticket===serial){preview.disabled=!artifact;download.disabled=!artifact;}}}
  if(artifactIsShared(p))return;
  const check=()=>{if(!host.isConnected){artifactChecks.delete(check);return;}artifactAvailability().then(data=>{if(!host.isConnected)return;const found=(data.artifacts||[]).find(item=>item.path===file.path);artifact=found?.available===true&&typeof found.artifact_id==='string'?found:null;preview.disabled=!artifact;download.disabled=!artifact;status.textContent=artifact?'Original bytes available · '+found.bytes+' bytes · SHA-256 '+found.sha256:(found?.reason||'File bytes unavailable in this local service binding.');}).catch(error=>{if(host.isConnected)status.textContent=error.message;});};artifactChecks.add(check);queueMicrotask(check);
}
function inspectArchiveFile(file){const box=open(file.path||'Source archive file');box.append(section('Source file metadata',file));artifactControls(box,file);}
function buildArchiveInventory(){
  const source=p.bundle?.manifest?.source||{},inventory=source.archive_inventory;
  const exported=source.recording_export;
  if(exported&&typeof exported==='object'){
    const info=n('details',undefined,'f-export-coverage');info.append(n('summary','Read-only export coverage · '+String(exported.exporter||'source-declared')));
    info.append(n('p','Database: '+String(exported.database_consistency||exported.consistency||'unspecified')+' · '+String(exported.snapshot_started_at||'unknown start')+' → '+String(exported.snapshot_finished_at||'unknown finish'),'f-source'));
    const fs=exported.filesystem;
    if(fs&&typeof fs==='object'){
      info.append(n('p','Files: '+String(fs.consistency||'unspecified')+' · '+String(fs.started_at||'unknown start')+' → '+String(fs.finished_at||'unknown finish'),'f-source'));
      const files=Array.isArray(fs.files)?fs.files:[],omitted=files.filter(file=>file?.status!=='captured');
      info.append(n('p',files.filter(file=>file?.status==='captured').length+' files captured · '+omitted.length+' omissions. Files and database are captured separately; this is not one atomic snapshot.'));
      if(omitted.length)info.append(section('File omissions reported by exporter',omitted));
    }
    if(exported.omitted_artifacts)info.append(section('Outside export scope',exported.omitted_artifacts));
    if(exported.limits)info.append(section('Export limits',exported.limits));
    const raw=n('details');raw.append(n('summary','Complete source export metadata'),section('Unmodified source metadata',exported));info.append(raw);$('coverage').append(info);
  }
  if(!Array.isArray(inventory)||!inventory.length)return;
  const sectionBox=n('details',undefined,'f-archive-inventory');sectionBox.append(n('summary','Source archive files · '+inventory.length));
  sectionBox.append(n('p','Inventory describes source-export bytes, hashes and preservation status. Recorded events and file bytes may have separate capture bases; neither proves complete runtime capture. Missing files may have been omitted by export or never recorded. Portable JSON and standalone HTML do not carry original workspace bytes. Transcript history does not establish additional executions.','f-source'));
  const transcriptByPath=new Map();for(const event of p.bundle?.events||[])if(event.data?.source_kind==='archive.transcript'){const path=event.data.source_path;if(!transcriptByPath.has(path))transcriptByPath.set(path,[]);transcriptByPath.get(path).push(event);}
  for(const file of inventory.slice(0,4096)){
    const row=n('details'),path=typeof file.path==='string'?file.path:'Unknown path';row.append(n('summary',path+' · '+(Number.isSafeInteger(file.bytes)?file.bytes.toLocaleString()+' bytes':'Size unknown')));row.append(section('Source file metadata',file),btn('Inspect file',()=>inspectArchiveFile(file),'f-reference'));artifactControls(row,file);
    const transcriptEvents=transcriptByPath.get(path)||[];if(transcriptEvents.length){row.append(n('p',transcriptEvents.length+' imported transcript history observations. Repeated identical UUID rows are preserved in the original file without duplicate timeline observations.'));for(const event of transcriptEvents.slice(0,100))row.append(btn('Transcript line '+event.data.source_line+' → '+event.id,()=>inspectEvent(event.id),'f-reference'));if(transcriptEvents.length>100)row.append(n('p','First 100 evidence links shown; all imported rows remain in the recording.','f-source'));}sectionBox.append(row);
  }$('coverage').append(sectionBox);
}

function open(title){inspectorRefresh=null;$('detail-title').textContent=title;$('detail-body').replaceChildren();$('detail').hidden=false;$('detail').classList.remove('closing');root.classList.add('f-has-detail');return $('detail-body');}
function inspectEvent(id){const e=events.get(id),box=open(id);if(!e){box.append(n('p','Original event unavailable'));return;}const sourceFile=(p.bundle?.manifest?.source?.archive_inventory||[]).find(file=>file.path===e.data?.source_path);if(sourceFile){box.append(btn('Inspect source file → '+sourceFile.path,()=>inspectArchiveFile(sourceFile),'f-reference'));artifactControls(box,sourceFile);}box.append(section('Exact original event',p.evidence_text?.[id]?.event??e),section('Exact original data',p.evidence_text?.[id]?.data??e.data));for(const d of e.dependencies||[])box.append(btn('Dependency · '+d.provenance+' → '+d.event_id,()=>inspectEvent(d.event_id),'f-reference'));for(const ref of e.evidence_refs||[])box.append(btn('Evidence → '+ref,()=>inspectEvent(ref),'f-reference'));if(e.parent_id)box.append(btn('Recorded parent → '+e.parent_id,()=>inspectEvent(e.parent_id),'f-reference'));for(const edge of story.edges||[])if(edge.source_event_id===id||edge.target_event_id===id)box.append(section('Relationship · '+edge.provenance,edge));}
function appendExplanation(box,m){const explanation=explanationFor(m,annotations);if(!explanation)return;box.append(section(explanation.label,explanation.text),section('Explanation basis',{basis:explanation.basis,source_path:explanation.source_path,...(explanation.generated?{provenance:explanation.provenance||p.explanations.provenance,bundle_sha256:p.explanations.bundle_sha256,binding:'File-declared bundle hash; browser has not cryptographically verified it',...(explanation.provenance?.created_at===null?{legacy:'Legacy annotation · generation time/profile version unavailable'}:{})}:{})}));for(const id of explanation.event_ids||[])box.append(btn('Explanation evidence → '+id,()=>inspectEvent(id),'f-reference'));}
function inspect(m){selected=m.id;selectedActivity=m.activity_id;drawDependencies();const a=actors.get(m.actor_id),box=open(m.title||m.kind),identity=n('div',undefined,'f-detail-identity');identity.append(avatar(a),n('span',displayName(a)+' · '+displayRole(a)));appendExplanation(box,m);box.append(identity,section('Message markers',m.semantic_tags||[]),section('Selected message',m.text||m.title),...(m.parameters!==null&&m.parameters!==undefined?[section('Parameters',m.parameters_text??m.parameters)]:[]),section('Timing and provenance',{timestamp:m.timestamp,offset_ms:m.offset_ms,timing_basis:m.timing_basis,playback_timing_basis:m.playback_timing_basis,recorded_offset_ms:m.recorded_offset_ms,provenance:m.provenance}));for(const id of m.event_ids||[])box.append(btn('Original evidence → '+id,()=>inspectEvent(id),'f-reference'));if(m.context?.length)box.append(section('Recorded conversation context',m.context.map(c=>({title:c.title,text:c.text,parameters:c.parameters_text??c.parameters,event_ids:c.event_ids,provenance:c.provenance}))));const actorRaw=n('details');actorRaw.append(n('summary','Recorded actor identity'),n('pre',json(a||{})));box.append(actorRaw);const activity=activities.find(x=>x.id===m.activity_id);if(activity)box.append(section('Evidence-derived activity',activity));document.querySelectorAll('.f-message').forEach(b=>b.classList.toggle('f-selected',b.dataset.id===selected||JSON.parse(b.dataset.messageIds||'[]').includes(selected)));inspectorRefresh=()=>inspect(m);}
function stopAudio(){for(const src of activeSounds){try{src.stop();}catch{}}activeSounds.clear();}
async function enableAudio(){const C=window.AudioContext||window.webkitAudioContext;if(!C)return;audioContext??=new C();await audioContext.resume();if(p.notification_sound&&!soundBuffer){const data=p.notification_sound.split(',')[1],bytes=Uint8Array.from(atob(data),x=>x.charCodeAt(0));soundBuffer=await audioContext.decodeAudioData(bytes.buffer);}}
function pop(delay=0){if(!sound||!audioContext)return;const now=audioContext.currentTime+delay,gain=audioContext.createGain();gain.connect(audioContext.destination);let src;if(soundBuffer){src=audioContext.createBufferSource();src.buffer=soundBuffer;gain.gain.value=.25;}else{src=audioContext.createOscillator();src.frequency.setValueAtTime(720,now);src.frequency.exponentialRampToValueAtTime(350,now+.07);gain.gain.setValueAtTime(.045,now);gain.gain.exponentialRampToValueAtTime(.001,now+.09);}src.connect(gain);activeSounds.add(src);src.onended=()=>activeSounds.delete(src);src.start(now);if(!soundBuffer)src.stop(now+.1);}
function storyMarkerEntries(sourceMessages,activity){const ids=new Set(typeof activity==='string'?[activity]:activity.source_activity_ids||[activity.id]);return sourceMessages.filter(m=>ids.has(m.activity_id)).flatMap(m=>(m.semantic_tags||[]).filter(t=>['success','danger'].includes(t.kind)).map(tag=>({message:m,tag})));}
function markerLabel(tag){return (tag.kind==='success'?'💡 ':'⚠ Danger · ')+tag.label;}
function markStoryClip(clip,activity){const entries=storyMarkerEntries(messages,activity);if(!entries.length)return;const labels=entries.map(({tag})=>markerLabel(tag)),danger=entries.some(({tag})=>tag.kind==='danger'),success=entries.some(({tag})=>tag.kind==='success');clip.classList.add('f-story-marked');if(danger)clip.dataset.phase='exploitation';if(success)clip.dataset.outcome='success';clip.title=(clip.dataset.contact==='true'?(clip.dataset.label||activity.label)+' · Recorded target contact; no target execution duration':(activity.label||activity.kind)+' · '+activity.category+' · '+activity.timing)+' · '+labels.join(' · ')+' · Individual observations';clip.setAttribute('aria-label',clip.title);const caption=clip.querySelector('.f-point-caption');if(caption){caption.textContent=labels.join(' · ');caption.classList.add('f-story-marker-caption');}const marker=n('span',[...new Set(entries.map(({tag})=>tag.kind==='danger'?'⚠':'💡'))].join(' '),'f-story-marker');marker.setAttribute('aria-hidden','true');if(clip.classList.contains('f-story-point'))clip.firstChild.textContent='';clip.prepend(marker);}
function selectedInEntry(entry){return entry.messages.some(m=>m.id===selected);}
function appendMessageTags(button,sourceMessages){
  const tags=n('span',undefined,'f-semantic-tags'),seen=new Set();
  for(const message of sourceMessages)for(const tag of message.semantic_tags||[]){
    const key=JSON.stringify([tag.kind,tag.label,tag.basis]);
    if(!['success','danger'].includes(tag.kind)||seen.has(key))continue;
    seen.add(key);const badge=n('span',markerLabel(tag),'f-semantic-tag f-tag-'+tag.kind);
    badge.title=tag.basis+' · Individual observation; not overall run success';tags.append(badge);
    if(tag.kind==='danger')button.dataset.phase='exploitation';
    if(tag.kind==='success')button.dataset.outcome='success';
  }
  if(tags.childElementCount)button.append(tags);
}
function messageStamp(button,sourceMessages){
  const first=sourceMessages[0],last=sourceMessages.at(-1),value=compactTimestamp(first.timestamp),stamp=n('time',first!==last&&last.timestamp?value+' → '+compactTimestamp(last.timestamp):value,'f-message-foot');
  stamp.title=sourceMessages.map(m=>m.timestamp||'Time unavailable').join(' → ');if(first.timestamp)stamp.dateTime=first.timestamp;button.append(stamp);
}
function inspectAction(entry){
  const first=entry.messages[0],last=entry.messages.at(-1),actor=ownerActor(first);
  selected=last.id;selectedActivity=first.activity_id||last.activity_id;drawDependencies();
  const box=open(first.action_label||first.title||'Recorded tool action'),identity=n('div',undefined,'f-detail-identity');
  identity.append(avatar(actor),n('span',displayName(actor)+' · '+displayRole(actor)));box.append(identity);
  for(const message of entry.messages){
    appendExplanation(box,message);
    box.append(section(message.kind==='tool.start'?(message.execution_status==='proposal_only_not_execution'?'Tool proposal · execution not established':'Invocation message'):'Recorded result message',message.text??message.title),section('Message markers',message.semantic_tags||[]));
    if(message.parameters!==null&&message.parameters!==undefined)box.append(section('Exact parameters',message.parameters_text??message.parameters));
    box.append(section('Timing and provenance',{id:message.id,call_id:message.call_id,activity_id:message.activity_id,timestamp:message.timestamp,offset_ms:message.offset_ms,timing_basis:message.timing_basis,playback_timing_basis:message.playback_timing_basis,recorded_offset_ms:message.recorded_offset_ms,provenance:message.provenance}));
    for(const id of message.event_ids||[])box.append(btn('Original evidence → '+id,()=>inspectEvent(id),'f-reference'));
    if(message.context?.length)box.append(section('Recorded conversation context',message.context.map(c=>({title:c.title,text:c.text,parameters:c.parameters_text??c.parameters,event_ids:c.event_ids,provenance:c.provenance}))));
  }
  const activity=activities.find(a=>a.id===selectedActivity);if(activity)box.append(section('Evidence-derived activity',activity));
  box.append(section('Logical owner',actor),section('Recorded source actors',entry.messages.map(m=>actors.get(m.actor_id)||{})));
  for(const button of root.querySelectorAll('.f-message'))button.classList.toggle('f-selected',JSON.parse(button.dataset.messageIds||'[]').includes(selected)||button.dataset.id===selected);
  inspectorRefresh=()=>inspectAction(entry);
}
function makeAction(entry){
  const first=entry.messages[0],start=entry.messages.find(m=>m.kind==='tool.start'),end=entry.messages.find(m=>m.kind==='tool.end'),actor=ownerActor(start||first),row=n('div',undefined,'f-message-row f-action-row');
  row.dataset.kind='tool';row.dataset.entity=entry.entity_id;row.append(avatar(actor));
  const button=btn('',()=>inspectAction(entry),'f-message f-action');button.dataset.kind='tool';button.dataset.id=entry.id;button.dataset.messageIds=JSON.stringify(entry.messages.map(m=>m.id));button.classList.toggle('f-selected',selectedInEntry(entry));
  const proposal=start?.execution_status==='proposal_only_not_execution';
  const state=end?'Result recorded':proposal?'Proposal recorded':'Invocation recorded';button.dataset.state=end?'result':proposal?'proposal':'invocation';
  const label=start?.action_label||end?.action_label||start?.title||first.title||'Tool action';
  button.append(n('div',displayName(actor),'f-meta'),n('span',label,'f-action-label'),n('span',state,'f-action-state'));
  const target=chatEntities.get(start?.target_entity_id||end?.target_entity_id);
  if(target&&target.id!==entry.entity_id)button.append(n('span',targetDisplayName(target),'f-action-target'));
  const explanation=textMode==='explained'?explanationFor(entry.messages.at(-1),annotations):null;
  if(explanation)button.append(n('small',explanation.label,'f-explanation-label'));
  const summary=explanation?.text||end?.compact_text||end?.text||start?.compact_text||start?.text||'';
  if(summary){const body=n('span',undefined,'f-body');mentionText(body,summary.length>220?summary.slice(0,220)+'…':summary);button.append(body);}
  appendMessageTags(button,entry.messages);button.append(n('span','Inspect parameters, output and evidence','f-action-inspect'));messageStamp(button,entry.messages);row.append(button);return row;
}
function makeMessage(m){
  const row=n('div',undefined,'f-message-row'),k=kind(m),a=chatMode==='compact'?entityActor(m):actors.get(m.actor_id);row.dataset.kind=k;row.dataset.entity=entityId(m);if(k!=='parameters')row.append(avatar(a));
  const b=btn('',()=>inspect(m),'f-message');b.dataset.kind=k;b.dataset.id=m.id;b.dataset.messageIds=JSON.stringify([m.id]);b.title=m.title||m.kind;b.classList.toggle('f-selected',selected===m.id);
  const association=chatMode==='compact'&&m.owner_entity_id&&m.owner_entity_id!==entityId(m)?chatEntities.get(m.owner_entity_id):null;
  const legacy=actors.get(m.actor_id),legacyName=legacy?.role==='model'?(legacy.associated_worker_name||legacy.associated_coordinator_name||legacy.associated_agent_name):null;
  const relation=association?' · for '+displayName(association):legacyName?(m.kind==='model.response'?' · reply to ':' · request from ')+presentationName(legacyName):'';
  b.append(n('div',displayName(a)+' · '+messageRole(a,m)+relation,'f-meta'));
  const explanation=textMode==='explained'?explanationFor(m,annotations):null;
  if(explanation)b.append(n('small',explanation.label,'f-explanation-label'));
  const body=n('span',undefined,'f-body'),preview=explanation?.text||(chatMode==='compact'?m.compact_text:null)||m.text||m.title||m.kind,limit=chatMode==='compact'?420:1200;
  mentionText(body,preview.length>limit?preview.slice(0,limit)+'… · Inspect full evidence':preview);b.append(body);appendMessageTags(b,[m]);row.append(b);
  if(chatMode==='full'&&m.parameters!==null&&m.parameters!==undefined){const param=n('div',undefined,'f-inline-parameters'),pb=n('div',undefined,'f-parameter-content'),paramText=m.parameters_text??json(m.parameters);pb.append(n('div','Parameters · inspect full evidence','f-meta'),n('span',paramText.length>420?paramText.slice(0,420)+'…':paramText,'f-body'));b.append(param);param.append(pb);}
  messageStamp(b,[m]);return row;
}
function buildPanels(){
  const oldPanels=[...panels.entries()].map(([id,d])=>({id,top:d.stream.scrollTop,follow:d.follow}));panels.clear();$('board').replaceChildren();
  const used=new Set(playbackMessages.map(m=>chatMode==='compact'&&['tool.start','tool.end'].includes(m.kind)?ownerId(m):entityId(m))),entities=layout==='single'?[{id:'all',display_name:'Recorded conversation',detail:'All logical entities · original message order'}]:[...chatEntities.values()].filter(e=>used.has(e.id));
  if(!entities.length)entities.push({id:'all',display_name:'Recorded conversation',detail:'No messages captured'});
  for(const entity of entities){
    const panel=n('section',undefined,'f-panel'),head=n('div',undefined,'f-panel-head'),heading=n('div');panel.dataset.entity=entity.id;
    heading.append(n('h2',displayName(entity)),n('small',entity.detail||displayRole(entity)));head.append(avatar(entity.id==='all'?null:entity),heading);
    const stream=n('div',undefined,'f-stream'),items=n('div'),typing=n('div',undefined,'f-typing');typing.title='Presentation preview of upcoming messages; not live typing telemetry.';stream.append(items,typing);
    const data={panel,stream,items,typing,follow:true,lastCount:0,ids:[]};stream.onscroll=()=>{data.follow=stream.scrollHeight-stream.clientHeight-stream.scrollTop<45;};head.append(btn('↓ Latest',()=>{data.follow=true;stream.scrollTop=stream.scrollHeight;},'f-latest'));panel.append(head,stream);$('board').append(panel);panels.set(entity.id,data);
  }
  render(false);if(oldPanels.length)for(const [id,d] of panels){const old=oldPanels.find(x=>x.id===id)||oldPanels[0];d.follow=old.follow;d.stream.scrollTop=old.follow?d.stream.scrollHeight:old.top;}
}
// Milestones are projections of explicit source markers, gated by actual chat reveal.
function milestoneEntries(sourceMessages){
  const seen=new Set(),result=[];
  for(const message of sourceMessages)for(const tag of message.semantic_tags||[]){
    if(!['success','danger'].includes(tag.kind))continue;
    const key=JSON.stringify([message.id,tag.kind,tag.label,tag.basis]);
    if(seen.has(key))continue;
    seen.add(key);
    const actionId=actionPairs.get(message.id),invocation=messages.find(m=>m.id===actionId&&m.kind==='tool.start');
    const attribution=invocation||message,tool=['tool.start','tool.end'].includes(message.kind);
    const candidate=tool?ownerActor(attribution):entityActor(message);
    // Missing ownership cannot turn a target/tool result source into an achiever.
    const role=candidate?.avatar_kind||candidate?.kind||candidate?.role;
    const actor=tool&&!['worker','coordinator','agent'].includes(role)&&!attribution.owner_entity_id?null:candidate;
    result.push({key,message,tag,actor});
  }
  return result;
}
function renderMilestones(shown){
  const host=$('milestone-list'),entries=milestoneEntries(shown),signature=JSON.stringify(entries.map(e=>e.key));
  if(host.dataset.signature===signature)return;
  const follow=host.scrollHeight-host.scrollTop-host.clientHeight<40,oldTop=host.scrollTop;
  const previous=JSON.parse(host.dataset.signature||'[]'),keys=entries.map(e=>e.key);let common=0;
  while(common<previous.length&&common<keys.length&&previous[common]===keys[common])common++;
  if(!previous.length)host.replaceChildren();
  while(host.children.length>common)host.lastElementChild.remove();
  host.dataset.signature=signature;
  if(!entries.length)host.append(n('p','Milestones appear as marked evidence is revealed.','f-milestone-empty'));
  for(const {message,tag,actor} of entries.slice(common)){
    const card=btn('',()=>inspect(message),'f-milestone-card');card.dataset.kind=tag.kind;card.dataset.messageId=message.id;
    card.title=(tag.basis||'Recorded marker')+' · Individual observation';
    const identity=n('span',undefined,'f-milestone-identity');identity.append(avatar(actor),n('span',displayName(actor)));
    const badge=n('span',tag.kind==='success'?'★ Success':'⚠ Attempt','f-milestone-badge');
    card.append(identity,n('strong',tag.label||'Recorded milestone','f-milestone-label'),badge);
    const stamp=n('time',message.timestamp?compactTimestamp(message.timestamp):'Time unavailable','f-milestone-time');
    if(message.timestamp)stamp.dateTime=message.timestamp;stamp.title=message.timestamp||'Recorded timestamp unavailable';card.append(stamp);host.append(card);
  }
  host.scrollTop=follow?host.scrollHeight:oldTop;
}
function render(notify=false){
  const shown=messages.filter(m=>visible.has(m.id)),chatShown=explanationChatMessages(shown,textMode),compact=chatMode==='compact'?compactChatEntries(chatShown,messages,entityId,ownerId,actionPairs):null;
  renderMilestones(shown);
  root.classList.toggle('f-compact',chatMode==='compact');
  for(const [entity,d] of panels){
    const list=compact?compact.filter(entry=>entity==='all'||entry.entity_id===entity):chatShown.filter(m=>entity==='all'||entityId(m)===entity).map(m=>({id:m.id,messages:[m],action:false}));
    const signatures=list.map(entry=>entry.id+':'+entry.messages.map(m=>m.id).join('|')),oldTop=d.stream.scrollTop,follow=d.follow;let common=0;
    while(common<d.ids.length&&common<list.length&&d.ids[common]===signatures[common])common++;
    while(d.items.children.length>common)d.items.lastElementChild.remove();
    for(const entry of list.slice(common))d.items.append(entry.action?makeAction(entry):makeMessage(entry.messages[0]));
    const changed=common!==d.ids.length||common!==list.length;d.ids=signatures;d.lastCount=list.length;if(changed){if(follow)d.stream.scrollTop=d.stream.scrollHeight;else d.stream.scrollTop=oldTop;}
    const belongs=m=>entity==='all'||entityId(m)===entity;
    const active=activities.filter(a=>Number.isFinite(a.start_ms)&&Number.isFinite(a.end_ms)&&a.end_ms>a.start_ms&&position>=a.start_ms&&position<a.end_ms&&a.timing==='interval'&&['tool','model'].includes(a.category)&&f.time_mode!=='review_sequence'&&messages.some(m=>m.activity_id===a.id&&belongs(m)));
    d.panel.classList.toggle('f-active',active.length>0);
    const next=pendingMessage(),preview=[];
    for(const activity of active){const candidates=playbackMessages.filter(m=>m.activity_id===activity.id&&belongs(m));const source=candidates.find(m=>['tool.end','model.response','output'].includes(m.kind))||candidates.at(-1);if(source&&chatMessageVisible(source))preview.push(source);}
    if(next&&chatMessageVisible(next)&&belongs(next)&&!preview.some(m=>m.activity_id===next.activity_id))preview.unshift(next);
    const identities=new Map();for(const m of preview){const actor=chatMode==='compact'?entityActor(m):actors.get(m.actor_id);if(!actor)continue;const label=['task','system'].includes(actor.role||actor.kind)?'Next message…':['record','records','source','document'].includes(actor.avatar_kind)?'Next record…':displayName(actor)+(['tool.start','tool.end'].includes(m.kind)?' · recorded action…':' is typing…');identities.set(actor.id,{label,actor});}
    const signature=[...identities.values()].map(x=>x.label).join('|');if(d.typing.dataset.preview!==signature){d.typing.replaceChildren(...[...identities.values()].map(({label,actor})=>{const pill=n('span',undefined,'f-typing-pill');pill.append(avatar(actor),n('span',label));const dots=n('span',undefined,'f-typing-dots');dots.append(n('i'),n('i'),n('i'));pill.append(dots);return pill;}));d.typing.dataset.preview=signature;}
    d.typing.classList.toggle('visible',playing&&performance.now()-Math.max(startedAt,lastArrival)>=typingDelay&&identities.size>0);
  }
document.querySelectorAll('.f-story-cursor').forEach(c=>{c.hidden=f.time_mode==='review_sequence';c.style.left=storyFraction()*100+'%';});const storyHandle=storyHost.querySelector('.f-story-handle');if(storyHandle){storyHandle.setAttribute('aria-valuenow',String(Math.max(0,storyCursorRecordedAt())));storyHandle.setAttribute('aria-valuetext',clock(storyCursorRecordedAt())+' storyline cursor; evidence clock '+clock(position)+' recorded');}followStory();$('clock').textContent=untimed?'Recorded time unavailable':'Recorded '+clock(position)+' / '+clock(duration);$('review-clock').textContent=untimed?'Viewing '+clock(playPosition())+' / '+clock(playbackDuration()):isSmart()?'Viewing '+clock(reviewPosition)+' / '+clock(viewingDuration):'Viewing '+clock(position)+' / '+clock(duration);$('scrub').max=playbackDuration();$('scrub').value=Math.max(0,playPosition());$('scrub-label').textContent=untimed?'Source review sequence':isSmart()?'Viewing time':'Recorded elapsed time';$('scrub').setAttribute('aria-label',$('scrub-label').textContent);const segment=isSmart()?currentSegment():null;const recordedSpan=segment?segment.recorded_end_ms-segment.recorded_start_ms:0,viewingSpan=segment?segment.end_ms-segment.start_ms:0;$('pacing-note').textContent=untimed?'Source review sequence · reading order only; no recorded seconds invented':segment?.kind==='gap'&&recordedSpan>viewingSpan?seconds(recordedSpan)+' recorded · condensed to '+seconds(viewingSpan)+(activities.some(a=>a.category==='model'&&a.timing==='interval'&&Number.isFinite(a.start_ms)&&Number.isFinite(a.end_ms)&&a.end_ms>a.start_ms&&position>=a.start_ms&&position<a.end_ms)?' · Model in progress':activities.some(a=>a.category==='tool'&&a.timing==='interval'&&Number.isFinite(a.start_ms)&&Number.isFinite(a.end_ms)&&a.end_ms>a.start_ms&&position>=a.start_ms&&position<a.end_ms)?' · Tool in progress':' · Condensed wait'):segment?.kind==='hold'?'Reading hold · storyline cursor uses viewing pace; evidence clock held for review':isSmart()?'Smart viewing pace · reading order preserves original concurrency':'Recorded clock · arrivals buffered for reading; timestamps unchanged';$('counter').textContent=compact?compact.length+' / '+compactTotals[textMode]+' conversation entries':chatShown.length+' / '+playbackMessages.length+' messages';$('status').textContent=position<0?(messages[0]?.offset_ms>1000?'First event at '+clock(messages[0].offset_ms)+' · Next event skips the recorded idle interval':'Ready to review recorded evidence'):playPosition()>=playbackDuration()&&!pendingMessage()?'End of recorded flow':playing&&shownMessages().length>shown.length?'Presenting buffered recorded messages':playing?'Reviewing recorded flow':'Review paused';$('play').textContent=playing?'Ⅱ Pause':'▶ Recorded playback';root.classList.toggle('f-playing',playing);document.querySelectorAll('.f-story-clip').forEach(b=>{const a=renderedStoryActivities.get(b.dataset.id)||activities.find(a=>a.id===b.dataset.id);b.classList.toggle('f-future',position<(b.dataset.contact==='true'?Number(b.dataset.anchor):a?.start_ms??a?.end_ms??Infinity));});}
function seek(ms){stopAudio();startedAt=previous=lastArrival=performance.now();if(isSmart()){reviewPosition=ms;position=recordedAt(ms);}else position=ms;storyCursorAnchor={viewing_ms:reviewPosition,recorded_ms:position};visible.clear();shownMessages().forEach(m=>visible.add(m.id));for(const d of panels.values())d.lastCount=-1;render(false);}
function revealOne(now){const next=pendingMessage();if(next&&scheduledAt(next)<=playPosition()&&(!lastArrival||now-lastArrival>=arrivalSpacing)){visible.add(next.id);lastArrival=now;if(textMode!=='explained'||next.bookkeeping!==true)pop();return true;}return false;}
function tick(now){if(!playing)return;const delta=Math.min(100,Math.max(0,now-previous))*Number($('speed').value);if(isSmart()){let target=Math.min(viewingDuration,Math.max(0,reviewPosition)+delta);const next=pendingMessage();if(next)target=Math.min(target,Math.max(reviewPosition,scheduledAt(next)));reviewPosition=target;position=recordedAt(reviewPosition);}else position=Math.min(duration,Math.max(0,position)+delta);previous=now;revealOne(now);render();if(playPosition()>=playbackDuration()&&!pendingMessage()){playing=false;render();return;}frame=requestAnimationFrame(tick);}
$('play').onclick=async()=>{if(sound)try{await enableAudio();}catch{}cancelAnimationFrame(frame);playing=!playing;if(!playing)stopAudio();if(playing){setStoryFollow(true);if(playPosition()>=playbackDuration()&&!pendingMessage())seek(-1);startedAt=previous=lastArrival=performance.now();frame=requestAnimationFrame(tick);}render();};$('next').onclick=async()=>{if(sound)try{await enableAudio();}catch{}const next=pendingMessage();if(next){if(isSmart()){reviewPosition=Math.max(reviewPosition,entryTimes.get(next.id));position=recordedAt(reviewPosition);}else position=Math.max(position,next.offset_ms);storyCursorAnchor={viewing_ms:reviewPosition,recorded_ms:position};previous=performance.now();visible.add(next.id);lastArrival=previous;if(textMode!=='explained'||next.bookkeeping!==true)pop();render();}};$('restart').onclick=()=>{playing=false;cancelAnimationFrame(frame);seek(-1);};$('pacing').onchange=()=>{const nextMode=$('pacing').value;if(nextMode===pacingMode)return;stopAudio();if(nextMode==='smart')reviewPosition=viewingAt(position);pacingMode=nextMode;if(isSmart())position=recordedAt(reviewPosition);storyCursorAnchor={viewing_ms:reviewPosition,recorded_ms:position};startedAt=previous=lastArrival=performance.now();render(false);};$('scrub').max=playbackDuration();$('scrub').oninput=()=>seek(Number($('scrub').value));$('sound').onclick=async()=>{sound=!sound;if(sound){try{await enableAudio();}catch{sound=false;}}else stopAudio();$('sound').textContent=sound?'Sound on':'Sound off';$('sound').setAttribute('aria-pressed',String(sound));};$('close').onclick=()=>{$('detail').classList.add('closing');setTimeout(()=>{if($('detail').classList.contains('closing')){$('detail').hidden=true;root.classList.remove('f-has-detail');}},200);};$('design').onchange=()=>{root.classList.toggle('f-spotlight',$('design').value==='spotlight');root.classList.toggle('f-dark',$('design').value==='dark');};
for(const b of document.querySelectorAll('[data-view]'))b.onclick=()=>{root.classList.toggle('f-terminal',b.dataset.view==='terminal');document.querySelectorAll('[data-view]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));};for(const b of document.querySelectorAll('[data-layout]'))b.onclick=()=>{layout=b.dataset.layout;root.classList.toggle('f-single',layout==='single');document.querySelectorAll('[data-layout]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));buildPanels();};
for(const b of root.querySelectorAll('[data-chat-mode]')){b.setAttribute('aria-pressed',String(b.dataset.chatMode===chatMode));b.onclick=()=>{chatMode=b.dataset.chatMode;root.querySelectorAll('[data-chat-mode]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));for(const data of panels.values())data.ids=[];refreshChatSchedule();buildPanels();};}
for(const b of root.querySelectorAll('[data-text-mode]')){b.setAttribute('aria-pressed',String(b.dataset.textMode===textMode));b.onclick=()=>{textMode=b.dataset.textMode;root.querySelectorAll('[data-text-mode]').forEach(x=>x.setAttribute('aria-pressed',String(x===b)));for(const data of panels.values())data.ids=[];refreshChatSchedule();buildPanels();};}
let dependencySvg=null,allDependencies=false;
function inspectEdge(e){const d=open('Dependency · '+e.provenance);d.append(section('Analysis provenance',e));for(const id of [e.source_event_id,e.target_event_id].filter(Boolean))d.append(btn('Original source → '+id,()=>inspectEvent(id),'f-reference'));}
function inspectStoryBreak(gap){const box=open('No recorded activity');box.append(n('p','This storyline interval is condensed for review. Original timestamps and evidence are unchanged.'),section('Exact recorded timing',{recorded_start_ms:gap.recorded_start_ms,recorded_end_ms:gap.recorded_end_ms,recorded_start:clock(gap.recorded_start_ms),recorded_end:clock(gap.recorded_end_ms),duration_ms:gap.recorded_end_ms-gap.recorded_start_ms}),section('Presentation mapping',{display_start_ms:gap.display_start_ms,display_end_ms:gap.display_end_ms,display_duration_ms:gap.display_end_ms-gap.display_start_ms,omitted_ms:gap.omitted_ms??(gap.recorded_end_ms-gap.recorded_start_ms)-(gap.display_end_ms-gap.display_start_ms),basis:gap.basis||'No recorded activity within this interval',axis_source:condensedStoryAxis.source}));for(const id of gap.boundary_activity_ids||[]){const activity=activities.find(item=>item.id===id);box.append(btn('Boundary activity → '+(activity?.label||id),()=>activity?inspectStoryActivity(activity):open(id).append(n('p','Original activity unavailable')),'f-reference'));}for(const id of gap.boundary_event_ids||[])box.append(btn('Boundary evidence → '+id,()=>inspectEvent(id),'f-reference'));}
function drawDependencies(){if(!dependencySvg)return;const host=$('lanes'),rect=host.getBoundingClientRect();dependencySvg.replaceChildren();dependencySvg.setAttribute('viewBox','0 0 '+rect.width+' '+rect.height);dependencySvg.style.height=rect.height+'px';const clips=new Map();for(const clip of host.querySelectorAll('.f-story-clip')){if(clip.dataset.contact==='true')continue;let ids=[clip.dataset.id];try{ids=JSON.parse(clip.dataset.sourceActivityIds||'null')||ids;}catch{}for(const id of ids)clips.set(id,clip);}for(const e of story.edges||[]){if(!['dependency'].includes(e.kind)||e.source_activity_id===e.target_activity_id)continue;if(!allDependencies&&e.source_activity_id!==selectedActivity&&e.target_activity_id!==selectedActivity)continue;const sb=clips.get(e.source_activity_id),tb=clips.get(e.target_activity_id);if(!sb||!tb)continue;const sa=activities.find(a=>a.id===e.source_activity_id),ta=activities.find(a=>a.id===e.target_activity_id),sr=sb.parentElement.getBoundingClientRect(),tr=tb.parentElement.getBoundingClientRect();const sm=Number.isFinite(e.source_ms)?e.source_ms:sa.end_ms??sa.start_ms,tm=Number.isFinite(e.target_ms)?e.target_ms:ta.start_ms??ta.end_ms;if(!Number.isFinite(sm)||!Number.isFinite(tm))continue;const x1=sr.left-rect.left+storyAxisFraction(sm)*sr.width,x2=tr.left-rect.left+storyAxisFraction(tm)*tr.width,y1=sb.getBoundingClientRect().top-rect.top+13,y2=tb.getBoundingClientRect().top-rect.top+13;const path=document.createElementNS(dependencySvg.namespaceURI,'path');path.setAttribute('d',`M ${x1} ${y1} C ${x1+24} ${y1-20}, ${x2-24} ${y2-20}, ${x2} ${y2}`);path.setAttribute('class','f-dependency-path '+(e.provenance==='inferred'?'inferred':''));path.setAttribute('tabindex','0');path.setAttribute('role','button');path.setAttribute('aria-label',(e.label||e.kind)+' · '+e.provenance);path.onclick=()=>inspectEdge(e);path.onkeydown=k=>{if(k.key==='Enter')inspectEdge(e);};dependencySvg.append(path);const arrow=document.createElementNS(dependencySvg.namespaceURI,'path');arrow.setAttribute('d',`M ${x2} ${y2} l-6 -5 v10 z`);arrow.setAttribute('class','f-dependency-head');dependencySvg.append(arrow);}}
function buildDependencies(){const box=n('details',undefined,'f-dependencies');box.append(n('summary','Dependency links · select an activity to highlight its relationships'));const label=n('label'),check=n('input');check.type='checkbox';check.checked=allDependencies;check.onchange=()=>{allDependencies=check.checked;drawDependencies();};label.append(check,document.createTextNode(' Show all activity dependencies'));box.append(label);for(const e of story.edges||[])box.append(btn((e.label||e.kind)+' · '+e.provenance+' · '+(e.source_event_id||'?')+' → '+(e.target_event_id||'?'),()=>inspectEdge(e),'f-reference'));$('lanes').append(box);dependencySvg=document.createElementNS('http://www.w3.org/2000/svg','svg');dependencySvg.classList.add('f-dependency-overlay');dependencySvg.setAttribute('aria-label','Activity dependency arrows; inferred relationships dashed');$('lanes').append(dependencySvg);box.ontoggle=drawDependencies;storyLayoutFrame=requestAnimationFrame(drawDependencies);}
function storyLaneView(sourceStory,flow,mode){
  const sourceActivities=sourceStory.activities||[],logical=flow.logical_storyline;
  if(mode==='source'||!Array.isArray(logical?.lanes))return {mode:'source',lanes:(sourceStory.lanes||[]).map(lane=>({...lane,activities:sourceActivities.filter(a=>a.lane_id===lane.id)})),annotations:[],annotationEntries:[],available:Array.isArray(logical?.lanes)};
  const grouped=Array.isArray(logical.activity_groups),lanes=logical.lanes.map(lane=>({...lane,activities:[],contacts:[],single_tier:grouped})),byId=new Map(lanes.map(lane=>[lane.id,lane]));
  const omitted=new Set(logical.omitted_activity_ids||[]),annotationEntries=(logical.annotations||[]).filter(a=>!omitted.has(a.activity_id)),annotated=new Set(annotationEntries.map(a=>a.activity_id)),annotations=[];
  if(grouped)for(const group of logical.activity_groups){const lane=byId.get(group.entity_id||group.lane_id);if(lane)lane.activities.push(group);}
  for(const activity of sourceActivities){
    const lane=byId.get(logical.activity_lanes?.[activity.id]);
    if(!grouped&&lane&&!annotated.has(activity.id))lane.activities.push(activity);
    else if(!omitted.has(activity.id)&&(annotated.has(activity.id)||!lane))annotations.push(activity);
    const association=logical.activity_entities?.[activity.id],target=byId.get(association?.target_entity_id);
    if(target&&target!==lane){
      const anchor=Number.isFinite(association.target_ms)?association.target_ms:activity.end_ms??activity.start_ms;
      if(Number.isFinite(anchor))target.contacts.push({activity,anchor,label:'Contact · '+(activity.label||activity.kind)});
    }
  }
  return {mode:'logical',lanes,annotations,annotationEntries,available:true};
}
function inspectStoryActivity(activity){
  const sourceIds=activity.source_activity_ids||[activity.id],meaningful=activity.meaningful_activity_ids||sourceIds;
  selectedActivity=meaningful[0]||sourceIds[0];drawDependencies();
  if(!activity.source_activity_ids){const message=messages.find(m=>m.activity_id===activity.id);if(message){inspect(message);return;}}
  const box=open(activity.label||'Grouped recorded activity'),actor=chatEntities.get(activity.entity_id),identity=n('div',undefined,'f-detail-identity');
  if(actor){identity.append(avatar(actor),n('span',displayName(actor)+' · '+displayRole(actor)));box.append(identity);}
  box.append(section('Logical storyline grouping',{label:activity.label,source_activity_ids:sourceIds,meaningful_activity_ids:meaningful,grouping_basis:activity.grouping_basis,provenance:activity.provenance,start_ms:activity.start_ms,end_ms:activity.end_ms,timing:activity.timing}));
  for(const id of sourceIds){
    const original=activities.find(item=>item.id===id),part=n('details');part.append(n('summary',(original?.label||id)+' · original activity'));
    if(original)part.append(section('Original activity',original));
    for(const message of messages.filter(item=>item.activity_id===id))part.append(btn('Message → '+(message.action_label||message.title||message.kind),()=>inspect(message),'f-reference'));
    for(const eventId of original?.event_ids||[])part.append(btn('Original evidence → '+eventId,()=>inspectEvent(eventId),'f-reference'));
    box.append(part);
  }
}
function appendStoryRow(lane,rowActivities,contacts=[],annotationEntries=[]){
  const row=n('div',undefined,'f-story-row'),track=n('div',undefined,'f-story-track'),laneLabel=n('span',undefined,'f-source f-story-lane-label');
  row.dataset.laneId=lane.id;row.dataset.laneKind=lane.kind||'source';
  laneLabel.append(n('strong',lane.label||lane.id));
  if(lane.detail)laneLabel.append(n('small',lane.detail));
  laneLabel.title=lane.detail||lane.label||lane.id;
  if(/^#[0-9a-f]{6}$/i.test(lane.accent||''))row.style.setProperty('--lane-accent',lane.accent);
  row.append(laneLabel,track);
  const entries=[...rowActivities.map(activity=>({activity,anchor:activity.start_ms??activity.end_ms})),...contacts.map(contact=>({...contact,contact:true}))]
    .filter(entry=>Number.isFinite(entry.anchor)).sort((a,b)=>a.anchor-b.anchor);
  const ends=[],categories=[];
  for(const entry of entries){
    const activity=entry.activity,start=entry.anchor,annotation=annotationEntries.find(a=>a.activity_id===activity.id);
    const label=entry.label||annotation?.label||activity.label||activity.kind;
    renderedStoryActivities.set(activity.id,activity);
    const clip=btn(label,()=>inspectStoryActivity(activity),'f-story-clip');
    clip.dataset.id=activity.id;clip.dataset.kind=activity.category;clip.dataset.label=label;
    clip.dataset.sourceActivityIds=JSON.stringify(activity.source_activity_ids||[activity.id]);
    if(lane.kind==='annotation')clip.dataset.annotation='true';
    if(entry.contact){clip.dataset.contact='true';clip.dataset.anchor=String(start);clip.dataset.label=label;clip.classList.add('f-story-contact');}
    clip.title=label+' · '+(entry.contact?'Recorded target contact; no target execution duration':activity.category+' · '+activity.timing);
    clip.setAttribute('aria-label',clip.title);
    clip.style.background=activity.category==='model'?'var(--f-prompt)':activity.category==='tool'?'var(--f-tool)':'var(--f-param)';
    clip.style.left=storyAxisFraction(start)*100+'%';
    const slot=ends.findIndex((end,index)=>categories[index]===activity.category&&end<=start),level=lane.kind==='annotation'||lane.single_tier?0:slot<0?ends.length:slot;
    const intervalTiming=['interval','presentation_span'].includes(activity.timing);
    ends[level]=!entry.contact&&intervalTiming?Math.max(start+1,activity.end_ms??start):start+1;categories[level]=activity.category;
    track.dataset.tiers=String(ends.length||1);track.style.height=(27*(ends.length||1))+'px';clip.dataset.level=String(level);clip.style.top=(27*level)+'px';
    clip.style.width=Math.max(.8,storyAxisSpan(start,activity.end_ms??start)/Math.max(1,storyDisplayDuration())*100)+'%';
    if(lane.kind==='annotation'||entry.contact||!intervalTiming||storyAxisSpan(start,activity.end_ms??start)/Math.max(1,storyDisplayDuration())<.035){
      clip.classList.add('f-story-point');if(storyAxisFraction(start)>.8)clip.classList.add('f-point-end');
      clip.textContent=activity.timing==='clock_conflict'||activity.timing==='incomplete'?'◇':'◆';clip.append(n('span',label,'f-point-caption'));
    }
    markStoryClip(clip,activity);clip.classList.toggle('f-future',position<start);track.append(clip);
  }
  storyHost.append(row);
}
function buildStory(){
  if(storyDrag)finishStoryDrag();
  const view=storyLaneView(story,f,storyLaneMode);
  storyHost.replaceChildren();dependencySvg=null;renderedStoryActivities=new Map();storyHost.dataset.laneMode=storyLaneMode;storyHost.dataset.axisMode=storyAxisMode;
  const axis=n('div',undefined,'f-story-axis'),ticks=n('div',undefined,'f-story-ticks');
  axis.append(n('span',story.duration_ms?'Elapsed':'Timing unknown'),ticks);
  const handleTrack=n('div',undefined,'f-story-handle-track'),handle=n('div',undefined,'f-story-cursor f-story-handle');
  handle.tabIndex=0;handle.setAttribute('role','slider');handle.setAttribute('aria-label','Storyline recorded time');handle.setAttribute('aria-valuemin','0');handle.setAttribute('aria-valuemax',String(duration));handle.title='Drag to scrub recorded time; arrow keys move 1 second, Shift moves 10 seconds';
  handle.hidden=untimed;handleTrack.append(handle);axis.append(handleTrack);storyHost.append(axis);
  const unknown=activities.filter(a=>!Number.isFinite(a.start_ms)&&!Number.isFinite(a.end_ms));
  if(unknown.length)storyHost.append(n('p',unknown.length+' activities have unknown timing; inspect recording coverage.','f-source'));
  const axisLabel=f.time_mode==='review_sequence'?'Review sequence · cursor hidden':storyAxisMode==='condensed'?(storyIdleBreaks.length?'Condensed axis · '+storyIdleBreaks.length+' idle interval'+(storyIdleBreaks.length===1?'':'s'):'Condensed axis · no idle intervals'):'Recorded axis · linear elapsed time';
  $('time-mode').textContent=axisLabel+' · '+(view.mode==='logical'?'Logical entities':view.available?'Source identities · diagnostic rows':'Source identities · logical mapping unavailable');
  for(const lane of view.lanes)appendStoryRow(lane,lane.activities,lane.contacts||[]);
  if(view.annotations.length)appendStoryRow({id:'story:annotations',label:'Run annotations',detail:'Task, setup & source records',kind:'annotation'},view.annotations,[],view.annotationEntries);
  for(const mode of ['logical','source'])$('story-lanes-'+mode)?.setAttribute('aria-pressed',String(storyLaneMode===mode));
  for(const mode of ['condensed','recorded'])$('story-axis-'+mode)?.setAttribute('aria-pressed',String(storyAxisMode===mode));
  const playheadTrack=n('div',undefined,'f-story-playhead-track'),playhead=n('div',undefined,'f-story-cursor f-story-body-cursor');
  playhead.setAttribute('aria-hidden','true');playhead.hidden=untimed;playheadTrack.append(playhead);storyHost.append(playheadTrack);
  buildDependencies();
  if(!activities.length)storyHost.append(n('p','No activity intervals recorded.','f-source'));
}
function setupStoryLaneModes(){
  const group=n('div',undefined,'f-story-lane-modes');group.setAttribute('role','group');group.setAttribute('aria-label','Storyline lane grouping');
  for(const [mode,label] of [['logical','Logical entities'],['source','Source identities']]){
    const button=btn(label,()=>{if(storyLaneMode===mode)return;storyLaneMode=mode;buildStory();sizeStory();render(false);},'');
    button.id='story-lanes-'+mode;button.setAttribute('aria-controls','story-viewport');button.setAttribute('aria-pressed',String(storyLaneMode===mode));group.append(button);
  }
  root.querySelector('.f-story-heading').append(group);
}
function setupStoryAxisModes(){
  const group=n('div',undefined,'f-story-axis-modes');group.setAttribute('role','group');group.setAttribute('aria-label','Storyline time axis');
  for(const [mode,label] of [['condensed','Condensed'],['recorded','Recorded']]){
    const button=btn(label,()=>{if(storyAxisMode===mode)return;storyAxisMode=mode;buildStory();sizeStory();render(false);},'');
    button.id='story-axis-'+mode;button.setAttribute('aria-controls','story-viewport');button.setAttribute('aria-pressed',String(storyAxisMode===mode));button.title=mode==='condensed'?'Collapse long intervals with no recorded activity':'Show the complete linear recorded timeline';group.append(button);
  }
  root.querySelector('.f-story-heading').append(group);
}
let explanationTimer=0,explanationDisposed=false,explanationCompleted=-1,explanationPreviewTimer=0,explanationPreviewSerial=0,explanationPreviewPending=true,explanationPreview=null,explanationCanRetry=false,explanationPollInFlight=null,explanationPollEpoch=0,explanationStarting=false,explanationOfflineBusy=false,explanationOfflineWaiting=false,explanationOfflinePollInFlight=null;
function explanationOffline(){return $('explanation-mode').value==='offline';}
function refreshExplanationCoverage(){const counts=explanationCoverage(messages,annotations);$('explanation-coverage').textContent=counts.ai+' AI · '+counts.recorded+' recorded · '+counts.original+' original';}
function applyExplanationPackage(value){validatePackage(value);if(value.payload.bundle?.manifest?.run_id!==p.bundle?.manifest?.run_id||JSON.stringify(value.payload.bundle)!==JSON.stringify(p.bundle))throw new Error('Service recording changed; reopen the intended recording.');const replacement=value.payload.explanations;validateExplanations({...p,explanations:replacement});const next=new Map((replacement?.annotations||[]).map(a=>[a.message_id,a]));p.explanations=replacement;annotations.clear();for(const [id,item] of next)annotations.set(id,item);const refreshInspector=inspectorRefresh;for(const data of panels.values())data.ids=[];render(false);if(refreshInspector&&!$('detail').hidden)refreshInspector();refreshExplanationCoverage();}
function setExplanationBusy(value){explanationBusy=value;$('explanation-offline-start').disabled=value||explanationOfflineBusy||explanationOfflineWaiting||!explanationService||explanationPreviewPending||!explanationPreview?.targets;$('explanation-offline-cancel').disabled=value||explanationOfflineBusy||!explanationOfflineWaiting;setImportChooserDisabled(value);$('open-script').disabled=value;$('explanation-mode').disabled=value||explanationOfflineBusy;$('explanation-offline-export').disabled=!explanationOffline()||value||explanationOfflineBusy||!explanationService||explanationPreviewPending||!explanationPreview?.targets;$('explanation-offline-import').disabled=!explanationOffline()||value||explanationOfflineBusy||!explanationService;$('explanation-generate').disabled=explanationOffline()||explanationOfflineWaiting||value||!explanationService||explanationPreviewPending||!explanationPreview?.targets;$('explanation-cancel').disabled=!value||explanationOfflineBusy;$('explanation-retry').disabled=explanationOfflineWaiting||value||!explanationService||!explanationCanRetry;$('explanation-save').disabled=value||!explanationService;$('explanation-html').disabled=value||!explanationService;}
async function explanationAnnotations(epoch=explanationPollEpoch){const data=await explanationRequest('/api/annotations');if(explanationDisposed||epoch!==explanationPollEpoch)return;checkExplanationBinding(data);applyExplanationPackage({format:'traceforge-recording',version:1,payload:{...p,explanations:data.explanations??undefined},attachments});}
async function explanationHistory(epoch=explanationPollEpoch){const data=await explanationRequest('/api/history');if(explanationDisposed||epoch!==explanationPollEpoch)return;const host=$('explanation-history');host.replaceChildren();for(const revision of (Array.isArray(data)?data.map(id=>({id})):data.revisions||data.history||[])){const row=n('div',undefined,'f-explanation-revision');row.append(n('span',(revision.created_at||revision.id||revision.revision_id)+' · '+(revision.annotation_count??revision.count??'saved')+' descriptions'),btn('Restore',async()=>{try{if(explanationBusy||explanationOfflineBusy||explanationOfflineWaiting)throw new Error('Wait for the current operation before restoring a revision.');await explanationRequest('/api/restore',{revision:revision.id||revision.revision_id});await explanationAnnotations();await explanationHistory();updateExplanationEstimate();}catch(error){$('explanation-status').textContent=error.message;}}));host.append(row);}if(!host.childElementCount)host.append(n('p','No earlier revisions in this session.'));}
function startExplanationJob(){explanationStarting=true;explanationPollEpoch++;clearTimeout(explanationTimer);explanationCompleted=-1;explanationCanRetry=false;setExplanationBusy(true);$('explanation-progress').value=0;$('explanation-status').textContent='Starting…';return explanationPollEpoch;}
async function explanationPoll(epoch=explanationPollEpoch){if(explanationStarting||explanationPollInFlight===epoch||explanationDisposed||epoch!==explanationPollEpoch)return;clearTimeout(explanationTimer);explanationPollInFlight=epoch;try{const state=await explanationRequest('/api/status');if(explanationDisposed||epoch!==explanationPollEpoch)return;checkExplanationBinding(state);explanationCanRetry=['failed','cancelled'].includes(state.state)&&(state.completed||0)<(state.total||0);const busy=state.busy===true||['running','generating','cancelling'].includes(state.state);setExplanationBusy(busy);$('explanation-progress').max=Math.max(1,state.total||1);$('explanation-progress').value=state.completed||0;$('explanation-status').textContent=[state.state,(state.completed||0)+' / '+(state.total||0)+' completed',state.failed?state.failed+' failed':'',state.error||''].filter(Boolean).join(' · ');if((state.completed||0)!==explanationCompleted){await explanationAnnotations(epoch);if(explanationDisposed||epoch!==explanationPollEpoch)return;explanationCompleted=state.completed||0;}if(busy)explanationTimer=setTimeout(()=>explanationPoll(epoch),800);else{await explanationAnnotations(epoch);if(explanationDisposed||epoch!==explanationPollEpoch)return;await explanationHistory(epoch);if(explanationDisposed||epoch!==explanationPollEpoch)return;if($('explanation-panel').open)updateExplanationEstimate();}}catch(error){if(explanationDisposed||epoch!==explanationPollEpoch)return;setExplanationBusy(false);$('explanation-status').textContent=error.message;}finally{if(explanationPollInFlight===epoch)explanationPollInFlight=null;}}

function checkExplanationBinding(data){if(!data.recording_id||data.recording_id!==explanationService?.recording_id)throw new Error('Local service recording changed. Reopen the intended recording before generating descriptions.');}
function explanationSelection(){const scope=$('explanation-scope').value,interval=scope==='interval';$('explanation-interval').hidden=!interval;const start=explanationTimestamp($('explanation-start').value),end=explanationTimestamp($('explanation-end').value);return {scope,...(interval?{start_ms:start,end_ms:end}:{}),model:explanationOffline()?'gpt-6.1-sol':$('explanation-model').value.trim()||'preview-model',valid:!interval||(Number.isFinite(start)&&Number.isFinite(end)&&end>=start)};}
function updateExplanationEstimate(){try{const origin=new URL($('explanation-endpoint').value.trim()).origin;$('explanation-provider').textContent='Provider: '+(origin==='https://api.openai.com'?'OpenAI':origin)+' · change endpoint in Optional details';}catch{$('explanation-provider').textContent='Provider: enter a valid endpoint in Optional details';}clearTimeout(explanationPreviewTimer);const serial=++explanationPreviewSerial,selection=explanationSelection();explanationPreviewPending=true;explanationPreview=null;setExplanationBusy(explanationBusy);if(!selection.valid){$('explanation-estimate').textContent='Enter a valid timestamp interval (seconds or hh:mm:ss).';return;}if(!explanationService){$('explanation-estimate').textContent='Start the local service to preview eligible messages and context size. No provider calls are made by preview.';return;}$('explanation-estimate').textContent='Checking eligible messages and context size…';explanationPreviewTimer=setTimeout(async()=>{try{const {valid,...body}=selection,data=await explanationRequest('/api/preview',body);if(explanationDisposed||serial!==explanationPreviewSerial)return;checkExplanationBinding(data);if(!Number.isInteger(data.targets)||data.targets<0||!Number.isInteger(data.batches)||data.batches<0||!Number.isFinite(data.request_bytes)||data.request_bytes<0)throw new Error('Invalid description preview from local service.');explanationPreview=data;explanationPreviewPending=false;if(data.targets===0&&!explanationBusy&&!explanationOfflineWaiting&&selection.scope==='missing'&&$('explanation-status').textContent==='Ready to improve missing descriptions in this recording.')$('explanation-status').textContent='No missing descriptions to improve. Use Optional details to regenerate.';$('explanation-estimate').textContent=data.profile.adapter_id+' profile v'+data.profile.version+' · '+data.targets+' eligible messages · '+data.batches+' batches · '+data.request_bytes.toLocaleString()+' bytes of request context (estimate, not a price). Selected messages, actor labels, descriptions and bounded recorded evidence (own event and up to 12 prior references) '+(explanationOffline()?'are included in the request queued for your Codex session.':'are sent to your provider. Provider charges may apply.')+' Full recording and request history are excluded.';setExplanationBusy(explanationBusy);}catch(error){if(explanationDisposed||serial!==explanationPreviewSerial)return;explanationPreviewPending=false;$('explanation-estimate').textContent='Preview failed: '+error.message;setExplanationBusy(explanationBusy);}},250);}
$('improve-descriptions').onclick=()=>{$('explanation-panel').showModal();updateExplanationEstimate();if(explanationService){if(explanationBusy||(!explanationOffline()&&!explanationOfflineWaiting))explanationPoll();else explanationOfflinePoll();explanationHistory().catch(error=>{if(!explanationDisposed)$('explanation-status').textContent=error.message;});}};
$('explanation-close').onclick=()=>$('explanation-panel').close();$('explanation-panel').addEventListener('close',()=>{$('explanation-key').value='';});$('explanation-panel').addEventListener('cancel',()=>{$('explanation-key').value='';});
for(const id of ['explanation-scope','explanation-start','explanation-end','explanation-model','explanation-endpoint'])$(id).oninput=updateExplanationEstimate;
$('explanation-mode').onchange=()=>{$('explanation-key').value='';const offline=explanationOffline();$('explanation-api-fields').hidden=offline;$('explanation-api-actions').hidden=offline;$('explanation-offline-help').hidden=!offline;$('explanation-offline-actions').hidden=!offline;if(!explanationBusy){explanationPollEpoch++;clearTimeout(explanationTimer);$('explanation-status').textContent=offline?'Ready to improve missing descriptions in this recording.':'No provider calls occur until Generate descriptions is clicked.';}updateExplanationEstimate();if(explanationService)explanationOfflinePoll().then(()=>{if(!explanationDisposed&&!explanationOffline()&&!explanationOfflineWaiting)explanationPoll();});};
async function explanationOfflinePoll(epoch=explanationPollEpoch,renderStatus=true){if(explanationDisposed||epoch!==explanationPollEpoch||explanationOfflineBusy||explanationBusy||explanationOfflinePollInFlight===epoch)return;clearTimeout(explanationTimer);explanationOfflinePollInFlight=epoch;try{const state=await explanationRequest('/api/offline-status');if(explanationDisposed||epoch!==explanationPollEpoch)return;checkExplanationBinding(state);if(!['none','waiting','complete','cancelled','failed'].includes(state.state))throw new Error('Invalid offline request status.');explanationOfflineWaiting=state.state==='waiting';setExplanationBusy(explanationBusy);$('explanation-progress').max=Math.max(1,state.total||1);$('explanation-progress').value=state.completed||0;if(renderStatus&&(explanationOffline()||explanationOfflineWaiting))$('explanation-status').textContent=explanationOfflineWaiting?'Waiting for Codex session. Ask your session to process the pending descriptions; no file path is needed.'+(explanationOffline()?'':' Switch to Offline / Codex session to cancel this request.'):state.state==='complete'?'Descriptions updated. Review Explained text against original evidence.':state.state==='none'?'Ready to improve missing descriptions in this recording.':state.state+(state.error?' · '+state.error:'');if(state.state==='complete'){await explanationAnnotations(epoch);if(explanationDisposed||epoch!==explanationPollEpoch)return;await explanationHistory(epoch);if(explanationDisposed||epoch!==explanationPollEpoch)return;updateExplanationEstimate();}if(explanationOfflineWaiting)explanationTimer=setTimeout(()=>explanationOfflinePoll(epoch,renderStatus),1000);}catch(error){if(!explanationDisposed&&epoch===explanationPollEpoch){if(renderStatus)$('explanation-status').textContent=error.message;setExplanationBusy(explanationBusy);}}finally{if(explanationOfflinePollInFlight===epoch)explanationOfflinePollInFlight=null;}}
$('explanation-offline-start').onclick=async()=>{let epoch;try{const selection=explanationSelection();if(explanationOfflineWaiting||!selection.valid||explanationPreviewPending||!explanationPreview?.targets)throw new Error('Select at least one message in a valid scope.');epoch=startOfflineOperation();const {valid,...body}=selection,data=await explanationRequest('/api/offline-start',body);if(explanationDisposed||epoch!==explanationPollEpoch)return;checkExplanationBinding(data);finishOfflineOperation(epoch);await explanationOfflinePoll(epoch);}catch(error){if(!explanationDisposed&&(epoch===undefined||epoch===explanationPollEpoch))$('explanation-status').textContent=error.message;}finally{if(epoch!==undefined)finishOfflineOperation(epoch);}};
$('explanation-offline-cancel').onclick=async()=>{const epoch=explanationPollEpoch;try{await explanationRequest('/api/offline-cancel',{});if(!explanationDisposed&&epoch===explanationPollEpoch)await explanationOfflinePoll(epoch);}catch(error){if(!explanationDisposed&&epoch===explanationPollEpoch)$('explanation-status').textContent=error.message;}};
function startOfflineOperation(){if(explanationBusy||explanationOfflineBusy||!explanationOffline()||!explanationService)throw new Error('Wait for the current operation or connect the local service first.');explanationOfflineBusy=true;explanationPollEpoch++;clearTimeout(explanationTimer);setExplanationBusy(true);return explanationPollEpoch;}
function finishOfflineOperation(epoch){if(explanationDisposed||epoch!==explanationPollEpoch)return;explanationOfflineBusy=false;setExplanationBusy(false);}
$('explanation-offline-export').onclick=async()=>{let epoch;try{const selection=explanationSelection();if(!selection.valid||explanationPreviewPending||!explanationPreview?.targets)throw new Error('Select at least one message in a valid scope.');const targetCount=explanationPreview.targets;epoch=startOfflineOperation();const {valid,...body}=selection,response=await fetch('/api/offline-request',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-TraceForge-Token':explanationService.token},body:JSON.stringify({...body,recording_id:explanationService.recording_id})}),raw=await response.text();if(explanationDisposed||epoch!==explanationPollEpoch)return;const data=JSON.parse(raw);if(!response.ok)throw new Error(data.error||'Request export failed.');if(data.format!=='traceforge-explanation-request-set'||!Array.isArray(data.requests)||typeof data.request_id!=='string')throw new Error('Invalid offline request from local service.');downloadExplanation(raw,'agentstoryline-explanation-request-'+data.request_id.replace(/[^a-zA-Z0-9_-]/g,'').slice(0,12)+'.json','application/json');$('explanation-status').textContent='Exported '+data.requests.length+' request batches for '+targetCount+' messages. Find the file in your browser downloads (or chosen save location). Ask Codex in this session to process it and return the response-set JSON, then import that file.';}catch(error){if(!explanationDisposed&&(epoch===undefined||epoch===explanationPollEpoch))$('explanation-status').textContent=error.message;}finally{if(epoch!==undefined){finishOfflineOperation(epoch);explanationOfflinePoll(epoch,false);}}};
$('explanation-offline-import').onclick=()=>{$('explanation-response-file').click();};
$('explanation-response-file').onchange=async event=>{const file=event.target.files[0];if(!file)return;let epoch;try{if(file.size>16*1024*1024)throw new Error('Response JSON must be at most 16 MiB.');epoch=startOfflineOperation();const raw=await file.text();if(explanationDisposed||epoch!==explanationPollEpoch)return;const data=await explanationRequest('/api/offline-import',{response_json:raw});if(explanationDisposed||epoch!==explanationPollEpoch)return;checkExplanationBinding(data);await explanationAnnotations(epoch);if(explanationDisposed||epoch!==explanationPollEpoch)return;await explanationHistory(epoch);if(explanationDisposed||epoch!==explanationPollEpoch)return;updateExplanationEstimate();$('explanation-status').textContent='Imported '+data.imported+' descriptions · '+data.total+' total. Review Explained text against original evidence.';}catch(error){if(!explanationDisposed&&(epoch===undefined||epoch===explanationPollEpoch))$('explanation-status').textContent=error.message;}finally{event.target.value='';if(epoch!==undefined){finishOfflineOperation(epoch);explanationOfflinePoll(epoch,false);}}};
$('explanation-generate').onclick=async()=>{if(explanationOffline()||explanationOfflineWaiting)return;let epoch=explanationPollEpoch;const selected=explanationSelection(),key=$('explanation-key').value;$('explanation-key').value='';try{if(!selected.valid||explanationPreviewPending||!explanationPreview?.targets)throw new Error('Select at least one message in a valid scope.');const endpoint=$('explanation-endpoint').value.trim(),model=$('explanation-model').value.trim();if(!endpoint||!model)throw new Error('Enter an endpoint URL and model.');epoch=startExplanationJob();await explanationRequest('/api/generate',{provider:'openai-compatible',endpoint,model,api_key:key,scope:selected.scope,...(selected.scope==='interval'?{start_ms:selected.start_ms,end_ms:selected.end_ms}:{})});if(!explanationDisposed&&epoch===explanationPollEpoch){explanationStarting=false;explanationPoll(epoch);}}catch(error){if(explanationDisposed||epoch!==explanationPollEpoch)return;explanationStarting=false;setExplanationBusy(false);$('explanation-status').textContent=error.message;}};
$('explanation-cancel').onclick=async()=>{try{await explanationRequest('/api/cancel',{});$('explanation-status').textContent='Cancellation requested; completed descriptions will be preserved.';}catch(error){$('explanation-status').textContent=error.message;}};
$('explanation-retry').onclick=async()=>{if(explanationOfflineWaiting)return;const epoch=startExplanationJob();try{await explanationRequest('/api/retry',{});if(!explanationDisposed&&epoch===explanationPollEpoch){explanationStarting=false;explanationPoll(epoch);}}catch(error){if(explanationDisposed||epoch!==explanationPollEpoch)return;explanationStarting=false;setExplanationBusy(false);$('explanation-status').textContent=error.message;}};
function downloadExplanation(data,name,type){const url=URL.createObjectURL(new Blob([data],{type})),link=n('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function rawExplanationExport(){const response=await fetch('/api/export',{credentials:'same-origin'});if(!response.ok)throw new Error('Recording export failed.');return response.text();}
$('explanation-save').onclick=async()=>{try{downloadExplanation(await rawExplanationExport(),'enriched.traceforge.json','application/json');}catch(error){$('explanation-status').textContent=error.message;}};
$('explanation-html').onclick=async()=>{try{const raw=await rawExplanationExport(),clone=document.documentElement.cloneNode(true);clone.querySelector('#tf-flow').replaceWith(template.cloneNode(true));clone.querySelector('#explanation-panel').replaceWith(explanationTemplate.cloneNode(true));const policy=clone.querySelector('meta[http-equiv="Content-Security-Policy"]');policy.content=policy.content.replace("connect-src 'self'","connect-src 'none'");clone.querySelector('#recording').textContent=raw.replace(/</g,'\\u003c');downloadExplanation('<!doctype html>'+clone.outerHTML,'enriched.agentstoryline.html','text/html');}catch(error){$('explanation-status').textContent=error.message;}};
refreshExplanationCoverage();setExplanationBusy(explanationBusy);
bindExplanationService=()=>{if(explanationDisposed||!explanationService)return;renderSourceBadge(p);artifactListPromise=null;for(const check of artifactChecks)check();$('explanation-connection').textContent='Connected to local explanation service · credentials stay in session memory.';setExplanationBusy(explanationBusy);explanationHistory().catch(()=>{});if(explanationBusy)explanationPoll();else explanationOfflinePoll();if($('explanation-panel').open)updateExplanationEstimate();};
bindExplanationService();
$('title').textContent=sourceIdentity(p).adapter_id==='artex'?'Artex recorded playback':f.title||'Recorded playback';renderSourceBadge(p);$('identity').textContent=[p.bundle?.manifest?.agent?.name,p.bundle?.manifest?.run_id,messages.length+' recorded messages',p.bundle?.manifest?.completeness?.status==='partial'?'Partial capture · see recording coverage':null].filter(Boolean).join(' · ');
buildArchiveInventory();const explanationCounts=explanationCoverage(messages,annotations);$('coverage').append(n('p','Explained chat coverage: '+explanationCounts.ai+' AI explanations · '+explanationCounts.recorded+' recorded description fallbacks · '+explanationCounts.original+' original text fallbacks. '+explanationCounts.bookkeeping+' known bookkeeping entries are hidden from Explained chat. Every source message remains available in Full / Original mode; original evidence and source playback clocks are preserved.'));setupStoryAxisModes();setupStoryLaneModes();buildStory();for(const note of [...(f.notes||[]),...(story.notes||[]),...(p.bundle?.manifest?.completeness?.gaps||[])])$('coverage').append(n('p',note));$('coverage').append(n('p','Typing and responding indicators preview recorded actors in the presentation, not live telemetry. Playback spaces message arrivals by at least 750 ms at every speed; recorded pacing may buffer arrivals while its source clock advances. Smart pacing is a presentation schedule for reading. Its storyline cursor glides between recorded points while the evidence clock and markers retain source timing. The storyline axis can condense long intervals with no recorded activity independently from playback pacing; its break markers retain exact recorded boundaries. Original timestamps, activity intervals, concurrency and timeline positions remain evidence-derived. Staggered messages are reading order and do not establish new causality. Untimed recordings use source review sequence; viewing durations are never invented recorded timings.'));$('coverage').append(section('Coverage',p.bundle?.manifest?.completeness||{}));for(const chapter of story.chapters||[]){const card=n('div',undefined,'f-chapter');card.append(n('strong',chapter.title),n('p',chapter.summary),n('small','Basis: '+chapter.basis));for(const id of chapter.event_ids||[])card.append(btn('Evidence → '+id,()=>inspectEvent(id),'f-reference'));$('coverage').append(card);}const provenance=n('details');provenance.append(n('summary','Complete analysis provenance'),n('pre',json(p.narrative||{})));$('coverage').append(provenance);const replay=n('details');replay.append(n('summary','Procedural replay plan · review only'));for(const step of p.replay?.steps||[]){const card=n('details');card.append(n('summary',step.id+' · '+(step.title||step.kind||'Replay step')),n('pre',p.replay_steps_text?.[step.id]??json(step)));for(const id of step.event_ids||step.evidence_refs||[])card.append(btn('Source evidence → '+id,()=>inspectEvent(id),'f-reference'));replay.append(card);}if(!p.replay?.steps?.length)replay.append(n('pre',json(p.replay||{})));$('coverage').append(replay);$('manifest').onclick=()=>open('Original manifest').append(section('Exact manifest',p.manifest_text||p.bundle?.manifest||{}));buildParticipants();sound=preferences?.sound??Boolean(p.notification_sound);$('sound').textContent=sound?'Sound on':'Sound off';$('sound').setAttribute('aria-pressed',String(sound));buildPanels();setupStoryNavigation();
dispose=()=>{if(storyDrag){storyDrag.playing=false;finishStoryDrag(false);}explanationDisposed=true;clearTimeout(explanationTimer);clearTimeout(explanationPreviewTimer);explanationPreviewSerial++;$('explanation-key').value='';playing=false;cancelAnimationFrame(frame);stopAudio();audioContext?.close().catch(()=>{});cancelAnimationFrame(storyLayoutFrame);storyResizeObserver?.disconnect();for(const [type,fn,options] of storyListeners){if(type==='window-resize')window.removeEventListener('resize',fn);else storyViewport?.removeEventListener(type,fn,options);}};
setupImport();
}
function reset(payload,files,label){const previous=current,oldFiles=attachments;const design=document.getElementById('design').value,speed=document.getElementById('speed').value,pace=document.getElementById('pacing').value,preferences={textMode:document.querySelector('[data-text-mode][aria-pressed="true"]')?.dataset.textMode,chatMode:document.querySelector('[data-chat-mode][aria-pressed="true"]')?.dataset.chatMode||'compact',sound:document.getElementById('sound').getAttribute('aria-pressed')==='true',storyZoom:Number(document.getElementById('lanes').dataset.zoom)||1,storyFollow:document.getElementById('story-follow')?.getAttribute('aria-pressed')!=='false',storyLaneMode:document.getElementById('lanes').dataset.laneMode||'logical',storyAxisMode:document.getElementById('lanes').dataset.axisMode||'condensed'},savedLayout=document.querySelector('[data-layout][aria-pressed="true"]').dataset.layout,savedView=document.querySelector('[data-view][aria-pressed="true"]').dataset.view;dispose();document.getElementById('tf-flow').replaceWith(template.cloneNode(true));document.getElementById('explanation-panel').replaceWith(explanationTemplate.cloneNode(true));try{attachments=files;initialize(payload,preferences);current=payload;renderSourceBadge(payload);}catch(error){dispose();attachments=oldFiles;document.getElementById('tf-flow').replaceWith(template.cloneNode(true));document.getElementById('explanation-panel').replaceWith(explanationTemplate.cloneNode(true));initialize(previous,preferences);for(const [id,value] of [['design',design],['speed',speed],['pacing',pace]]){const el=document.getElementById(id);el.value=value;el.dispatchEvent(new Event('change'));}document.querySelector('[data-layout="'+savedLayout+'"]').click();document.querySelector('[data-view="'+savedView+'"]').click();throw new Error('Recording has an unsupported or malformed presentation structure.');}for(const [id,value] of [['design',design],['speed',speed],['pacing',pace]]){const el=document.getElementById(id);el.value=value;el.dispatchEvent(new Event('change'));}document.querySelector('[data-layout="'+savedLayout+'"]').click();document.querySelector('[data-view="'+savedView+'"]').click();document.getElementById('import-status').textContent=label+' · file-supplied provenance · paused';}
function validatePackage(value){if(value?.format!=='traceforge-recording'||value.version!==1)throw new Error('Choose an AgentStoryline portable recording version 1; native sources require the local viewer service.');let count=0;function walk(x,depth){if(++count>1000000||depth>48)throw new Error('Recording structure exceeds import limits.');if(typeof x==='number'&&!Number.isFinite(x))throw new Error('Non-finite recording number.');if(typeof x==='string'&&x.length>32*1024*1024)throw new Error('Recording text exceeds import limits.');if(x&&typeof x==='object')for(const child of Object.values(x))walk(child,depth+1);}walk(value,0);const p=value.payload;if(!p||!Array.isArray(p.flow?.messages)||!Array.isArray(p.flow?.actors)||!Array.isArray(p.bundle?.events)||!Array.isArray(p.storyline?.activities)||!Array.isArray(p.storyline?.lanes))throw new Error('Recording is missing flow or original evidence.');for(const list of [p.flow.messages,p.flow.actors,p.bundle.events,p.storyline.activities,p.storyline.lanes]){if(list.length>5000)throw new Error('Recording exceeds 5,000 entries per presentation collection.');const ids=new Set();for(const item of list){if(!item||typeof item!=='object'||typeof item.id!=='string'||ids.has(item.id))throw new Error('Recording contains invalid or duplicate identifiers.');ids.add(item.id);}}for(const m of p.flow.messages){if(typeof m.kind!=='string'||typeof m.actor_id!=='string'||!Number.isFinite(m.offset_ms))throw new Error('Invalid message fields.');}if(p.notification_sound&&!/^data:audio\/wav;base64,[A-Za-z0-9+/=]+$/.test(p.notification_sound))throw new Error('Only embedded WAV notification audio is supported.');validateExplanations(p);const files=value.attachments||[];if(!Array.isArray(files)||files.length>20||files.some(f=>typeof f.name!=='string'||f.name.length>180||typeof f.text!=='string'||f.text.length>1024*1024||f.language!=='python'))throw new Error('Invalid script attachments.');return value;}
async function openNativeRecording(file){
  if(file.size>32*1024*1024)throw new Error('Native recordings must be at most 32 MiB.');
  if(!explanationService||!explanationServiceOrigin(window.location))throw new Error('Native recordings, including OTLP JSON and OrcaReplay .orca ZIP or plain tar archives, need the local viewer. Run: python3 agentstoryline.py viewer /path/to/recording, then open its local URL.');
  const before=current,service=explanationService,identity=service.recording_id,bytes=new Uint8Array(await file.arrayBuffer());let binary='';
  for(let start=0;start<bytes.length;start+=32768)binary+=String.fromCharCode(...bytes.subarray(start,start+32768));
  if(current!==before||service!==explanationService||identity!==service.recording_id)throw new Error('Recording changed; open the file again.');
  const registered=await explanationRequest('/api/import-native',{source_base64:btoa(binary),filename:file.name});
  if(typeof registered.recording_id!=='string')throw new Error('Import returned no recording identity.');
  service.recording_id=registered.recording_id;service.source=registered.source;
  try{const value=validatePackage(await explanationRequest('/api/recording'));reset(value.payload,value.attachments||[],file.name);}catch(error){explanationService=null;throw new Error('Native recording imported but viewer refresh failed. Reload the local viewer: '+error.message);}
}
function setupImportPicker(){
  const byId=id=>document.getElementById(id),trigger=byId('import-recording'),picker=trigger.closest('.f-import-picker'),choices=byId('import-source-choices'),input=byId('open-recording'),buttons=[...choices.querySelectorAll('button')];
  byId('export-recording').onclick=exportLoadedRecording;
  const close=focus=>{choices.hidden=true;trigger.setAttribute('aria-expanded','false');if(focus)trigger.focus();};
  const open=()=>{if(trigger.disabled)return;choices.hidden=false;trigger.setAttribute('aria-expanded','true');};
  trigger.onclick=()=>choices.hidden?open():close(false);
  trigger.onkeydown=event=>{if(event.key==='ArrowDown'){event.preventDefault();open();buttons[0]?.focus();}};
  picker.onkeydown=event=>{if(event.key==='Escape'&&!choices.hidden){event.preventDefault();close(true);}};
  for(const button of buttons)button.onclick=()=>{input.accept=button.dataset.accept;input.dataset.importKind=button.dataset.importKind;byId('import-status').textContent='Choose a recording from '+button.querySelector('strong').textContent;close(false);input.value='';input.click();};
  byId('tf-flow').addEventListener('click',event=>{if(!choices.hidden&&!picker.contains(event.target))close(false);});
}
function setupImport(){setupShare();setupImportPicker();const status=document.getElementById('import-status');document.getElementById('open-recording').onchange=async event=>{const file=event.target.files[0];if(!file)return;if(importInFlight){status.textContent='Wait for the current recording import to finish.';return;}importInFlight=true;setImportChooserDisabled(true);try{if(explanationBusy)throw new Error('Wait for generation to finish or cancel it before opening another recording.');if(/(?:\.tar(?:\.zst)?|\.orca(?:\.zip)?|\.zip|\.jsonl)$/i.test(file.name)){
await openNativeRecording(file);return;
}if(file.size>128*1024*1024)throw new Error('Recording must be at most 128 MiB.');const rawFileText=await file.text(),decoded=JSON.parse(rawFileText);if(decoded?.format!=='traceforge-recording'){await openNativeRecording(file);return;}let value=validatePackage(decoded);if(explanationBusy)throw new Error('Wait for generation to finish or cancel it before opening another recording.');if(explanationService){try{const registered=await explanationRequest('/api/recording',{recording_json:rawFileText});if(typeof registered.recording_id!=='string')throw new Error('Service registration did not return a recording identity.');const synchronized=validatePackage(await explanationRequest('/api/recording'));explanationService.recording_id=registered.recording_id;explanationService.source=registered.source||null;value=synchronized;}catch(error){if(/busy|running/i.test(error.message))throw error;explanationService=null;reset(value.payload,value.attachments||[],file.name);document.getElementById('import-status').textContent='Opened locally · explanation service synchronization failed: '+error.message;return;}}reset(value.payload,value.attachments||[],file.name);}catch(error){document.getElementById('import-status').textContent='Open failed: '+error.message;document.getElementById('open-recording').value='';}finally{importInFlight=false;setImportChooserDisabled(explanationBusy);}};document.getElementById('open-script').onchange=async event=>{const file=event.target.files[0];if(!file)return;if(importInFlight){status.textContent='Wait for the current recording import to finish.';event.target.value='';return;}setImportChooserDisabled(true);event.target.disabled=true;try{if(!file.name.toLowerCase().endsWith('.py')||file.size>1024*1024||attachments.length>=20)throw new Error('Choose a .py file up to 1 MiB (maximum 20 attachments).');const recording=current,next=[...attachments,{name:file.name.slice(0,180),language:'python',text:await file.text()}];if(recording!==current)throw new Error('Recording changed; add the script again.');if(explanationService){const registered=await explanationRequest('/api/attachments',{attachments:next});if(registered.recording_id!==explanationService.recording_id||recording!==current)throw new Error('Recording changed while saving script attachments.');}attachments=next;renderFiles();status.textContent='Added '+file.name+' for inspection only · never executed';}catch(error){status.textContent=error.message;}finally{setImportChooserDisabled(explanationBusy);event.target.disabled=explanationBusy;event.target.value='';}};renderFiles();}
async function exportLoadedRecording(){
  const button=document.getElementById('export-recording'),recording=current,service=explanationService,identity=service?.recording_id;
  if(button.disabled||!hasLoadedRecording())return;
  button.disabled=true;
  try{
    let raw;
    if(service){
      const response=await fetch('/api/export',{credentials:'same-origin'});
      if(!response.ok)throw new Error('The local service could not export this recording.');
      raw=await response.text();
    }else raw=JSON.stringify(portableRecording(recording));
    if(current!==recording||explanationService!==service||service?.recording_id!==identity||importInFlight)throw new Error('Recording changed. Export the currently loaded recording again.');
    if(new Blob([raw]).size>128*1024*1024)throw new Error('Recording exceeds the 128 MiB import limit.');
    const packageValue=validatePackage(JSON.parse(raw));
    if(JSON.stringify(packageValue.payload.bundle)!==JSON.stringify(recording.bundle))throw new Error('The service has a different recording. Reload it before exporting.');
    const name=String(recording.bundle.manifest.run_id).replace(/[^a-zA-Z0-9_-]/g,'_').slice(0,100)||'recording',url=URL.createObjectURL(new Blob([raw],{type:'application/json'})),link=document.createElement('a');
    link.href=url;link.download=name+'.agentstoryline.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    document.getElementById('import-status').textContent='Exported AgentStoryline recording';
  }catch(error){document.getElementById('import-status').textContent='Export failed: '+error.message;}
  finally{setImportChooserDisabled(explanationBusy);}
}
function setupShare(){
  const $=id=>document.getElementById(id),panel=$('share-panel');let preview=null,serial=0;
  function invalidate(){serial++;preview=null;$('share-review').hidden=true;$('share-reviewed').checked=false;$('share-download').disabled=true;}
  $('share-recording').onclick=()=>{if(!panel.open)panel.showModal();};
  $('share-close').onclick=()=>panel.close();
  panel.addEventListener('close',()=>{invalidate();$('share-text').value='';$('share-report').textContent='';});
  $('share-terms').oninput=invalidate;
  $('share-reviewed').onchange=()=>{$('share-download').disabled=!preview||!$('share-reviewed').checked;};
  $('share-preview').onclick=async()=>{
    invalidate();const ticket=serial,recording=current,identity=explanationService?.recording_id;
    $('share-preview').disabled=true;$('share-status').textContent='Preparing sanitized preview…';
    try{const data=await explanationRequest('/api/share-preview',{custom_terms:$('share-terms').value.split(/\r?\n/).map(term=>term.trim()).filter(Boolean)});
      if(ticket!==serial||current!==recording||identity!==explanationService?.recording_id||!panel.isConnected)return;
      if(typeof data.preview_id!=='string'||data.recording_id!==identity||typeof data.preview_text!=='string')throw new Error('The local service returned an invalid share preview.');
      preview={preview_id:data.preview_id,recording_id:identity};$('share-report').textContent=JSON.stringify(data.report,null,2);$('share-text').value=data.preview_text.slice(0,200000);$('share-review').hidden=false;
      $('share-truncated').textContent=data.truncated||data.preview_truncated||data.preview_text.length>200000?'Preview truncated to 200,000 characters. The download includes the complete sanitized recording; review it before sharing.':'Complete sanitized preview.';
      $('share-status').textContent='Review the redaction report and sanitized content, then confirm below. Nothing has been shared.';
    }catch(error){if(ticket===serial&&panel.isConnected)$('share-status').textContent='Preview failed: '+error.message;}finally{if(panel.isConnected)$('share-preview').disabled=false;}
  };
  $('share-download').onclick=async()=>{
    if(!preview||!$('share-reviewed').checked)return;const selected=preview,ticket=serial;
    $('share-download').disabled=true;
    try{if(selected.recording_id!==explanationService?.recording_id)throw new Error('Recording changed. Prepare a new preview.');
      const response=await fetch('/api/share-export',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-TraceForge-Token':explanationService.token},body:JSON.stringify(selected)}),raw=await response.text();
      if(!response.ok){let message='Sanitized export failed.';try{message=JSON.parse(raw).error||message;}catch{}throw new Error(message);}
      if(ticket!==serial||!panel.isConnected)return;const parsed=JSON.parse(raw);if(parsed.format!=='traceforge-recording')throw new Error('Invalid sanitized recording.');
      const url=URL.createObjectURL(new Blob([raw],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download='sanitized.traceforge.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);$('share-status').textContent='Sanitized copy downloaded. Review the complete file before sharing.';
    }catch(error){if(ticket===serial&&panel.isConnected)$('share-status').textContent=error.message;}finally{if(ticket===serial&&panel.isConnected)$('share-download').disabled=!$('share-reviewed').checked;}
  };
}
function renderFiles(){const host=document.getElementById('script-files');host.replaceChildren();for(const file of attachments){const box=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre'),button=document.createElement('button');summary.textContent=file.name+' · Python · inspect only';pre.textContent=file.text;button.textContent='Download script';button.onclick=()=>{const url=URL.createObjectURL(new Blob([file.text],{type:'text/plain;charset=utf-8'})),link=document.createElement('a');link.href=url;link.download=file.name.split(/[\\/]/).at(-1)||'replay.py';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};box.append(summary,pre,button);host.append(box);}}
initialize(initial);
if(explanationServiceOrigin(window.location))fetch('/api/session',{credentials:'same-origin'}).then(response=>response.ok?response.json():null).then(session=>{if(current!==initial||session?.service!=='traceforge-explanations'||typeof session.token!=='string'||typeof session.recording_id!=='string')return;explanationService=session;explanationBusy=Boolean(session.busy);bindExplanationService();}).catch(error=>{document.getElementById('explanation-connection').textContent='Local service unavailable: '+error.message;});
})();
