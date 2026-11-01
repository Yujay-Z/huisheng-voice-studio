'use strict';
const $ = id => document.getElementById(id);
let token = '', reference = null, currentJob = null, poll = null, state = null, busy = false;
let toastTimer;
let lastJob = null, lastReceivedAt = 0, connectionLost = false;
let pollSession = 0, pollController = null;
let audioBusy=false, uploading=false, backendReady=false, modelTimer=null, studioClosed=false;
const activeStates = new Set(['submitting','queued','loading','generating','combining']);
function setSidebar(open){
  $('sidebar').hidden=!open;$('sidebar-backdrop').hidden=!open;
  $('studio-main').inert=open;
  $('menu-toggle').setAttribute('aria-expanded',String(open));
  $('menu-toggle').setAttribute('aria-label',open?'收起侧栏':'展开侧栏');
  $(open?'sidebar-close':'menu-toggle').focus();
}
$('menu-toggle').addEventListener('click',()=>setSidebar($('sidebar').hidden));
for(const id of ['sidebar-close','sidebar-backdrop'])$(id).addEventListener('click',()=>setSidebar(false));
$('nav-workspace').addEventListener('click',event=>{event.preventDefault();setSidebar(false);$('target-text').focus();});
document.addEventListener('keydown',event=>{
  if($('sidebar').hidden)return;
  if(event.key==='Escape'){event.preventDefault();setSidebar(false);}
  if(event.key==='Tab'){
    const controls=[$('sidebar-close'),$('nav-workspace'),$('sidebar-help')];
    const index=controls.indexOf(document.activeElement);
    if(event.shiftKey&&index<=0){event.preventDefault();controls[controls.length-1].focus();}
    else if(!event.shiftKey&&(index===controls.length-1||index<0)){event.preventDefault();controls[0].focus();}
  }
});
for(const id of ['open-help','sidebar-help'])$(id).addEventListener('click',()=>{
  if(!$('sidebar').hidden)setSidebar(false);
  $('help-dialog').showModal();
});
$('help-close').addEventListener('click',()=>$('help-dialog').close());
$('segment-toggle').addEventListener('click',()=>$('segment-dialog').showModal());
$('segment-close').addEventListener('click',()=>$('segment-dialog').close());
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 9000); }
async function api(path, body, options = {}) {
  const controller = options.controller || new AbortController();
  let timedOut = false;
  const timeout = setTimeout(()=>{timedOut=true;controller.abort();}, options.timeoutMs || 10000);
  try {
    const request = {cache:'no-store',signal:controller.signal};
    if(body!==undefined)Object.assign(request,{method:'POST',headers:{'Content-Type':'application/json','X-Local-Token':token},body:JSON.stringify(body)});
    const response = await fetch(path, request);
    const data = await response.json();
    if (!response.ok) { const error = new Error(data.error || '请求失败'); error.status = response.status; throw error; }
    return data;
  } catch(error) {
    if(timedOut)throw new Error('状态请求超时，正在重试');
    throw error;
  } finally { clearTimeout(timeout); }
}
async function load() {
  state = await api('/api/state'); token = state.token;
  $('quit-studio').hidden=state.supports_shutdown!==true;
  if(state.api_version!==3||state.build!=='editor-variants-v1'){
    backendReady=false;syncAudioControls();
    stopPolling();$('file-notice').hidden=false;
    $('file-notice-title').textContent='当前连接的是旧版后台，需要重启服务。';
    $('file-notice-message').textContent='请在项目文件夹双击“重启音色工作室.cmd”，然后按 Ctrl+F5。关闭浏览器标签页不会退出后台服务。';
    $('generate').disabled=true;$('audio-file').disabled=true;
    $('index-status').textContent='后台版本较旧，请先重启';
    return;
  }
  backendReady=true;syncAudioControls();
  const engine = state.engines.index;
  $('index-status').textContent = engine.status;
  $('index-status').classList.toggle('ready', engine.ready);
  $('install-help').hidden = engine.ready;
  referenceEditor.setProcessingStatus(state.audio_processing);
  if(state.model)renderModel(state.model);
  if (state.jobs.length && !currentJob) { currentJob = state.jobs[0].id; render(state.jobs[0]); if(needsJobPolling(state.jobs[0]))startPoll(currentJob); }
}
function renderModel(model){
  clearTimeout(modelTimer);
  if(studioClosed)return;
  if(model.state==='ready'){$('index-status').textContent='模型已就绪 · 可快速生成';$('index-status').classList.add('ready');}
  else if(model.state==='loading'){
    $('index-status').textContent=`准备模型中 · ${model.elapsed_seconds||0} 秒`;
    $('index-status').classList.remove('ready');
  }else if(model.state==='error'){$('index-status').textContent='模型准备未完成，生成时重试';$('index-status').classList.remove('ready');}
  else{$('index-status').textContent='模型待准备 · 生成时加载';$('index-status').classList.remove('ready');}
  modelTimer=setTimeout(async()=>{try{renderModel(await api('/api/model'));}catch{renderModel(model);}},model.state==='loading'?1500:5000);
}
const referenceEditor=createReferenceEditor({api,
  onChange(data){reference=data.id;if($('reference-player').getAttribute('src')!==data.url)$('reference-player').src=data.url;$('reference-player').hidden=false;$('upload-detail').textContent=`${data.seconds.toFixed(2)} 秒 · 生成时使用波形中的选区`;},
  onBusy(value){audioBusy=value;syncAudioControls();},
  onError:toast,onNotice:toast
});
function syncAudioControls(){
  const locked=busy||uploading||!backendReady||studioClosed;
  $('generate').disabled=locked||audioBusy;
  $('audio-file').disabled=locked||audioBusy;
  $('dropzone').setAttribute('aria-busy',String(uploading));
  referenceEditor.setLocked(locked);
}
syncAudioControls();
async function upload(file) {
  if(!file)return;
  if(!backendReady||studioClosed)return toast('本地服务尚未就绪，请稍候。');
  if(uploading)return toast('音频正在上传并准备波形，请稍候，无需重新上传。');
  if(busy||audioBusy)return toast('当前任务正在处理，请完成后再更换音频。');
  if (file.size > 64*1024*1024) return toast('音频最大支持 64 MB。');
  uploading=true;reference=null;syncAudioControls();
  $('upload-title').textContent='正在上传并准备波形…';
  $('upload-detail').textContent='准备完成后即可裁剪，无需重新上传';
  referenceEditor.clear();referenceEditor.setUploading(true);
  $('reference-player').hidden=true;
  try {
    const response = await fetch('/api/upload', {method:'POST',headers:{'X-Local-Token':token,'X-Filename':encodeURIComponent(file.name)},body:file});
    const data = await response.json(); if (!response.ok) throw new Error(data.error);
    reference = data.id; $('upload-title').textContent = file.name;
    $('upload-detail').textContent = `${data.seconds} 秒 · ` + (data.short_reference ? '短片段可尝试克隆，相似度请试听确认' : '已准备好，点击可更换参考声音');
    referenceEditor.setSource(data);
  } catch (error) { $('upload-title').textContent = '点击选择，或拖入参考音频'; $('upload-detail').textContent = '请选择 1–30 秒的清晰录音'; toast(error.message); }
  finally { uploading=false;referenceEditor.setUploading(false);syncAudioControls();$('audio-file').value=''; }
}
$('audio-file').addEventListener('change', event => upload(event.target.files[0]));
for (const name of ['dragenter','dragover']) $('dropzone').addEventListener(name,event=>{event.preventDefault();$('dropzone').classList.add('drag');});
for (const name of ['dragleave','drop']) $('dropzone').addEventListener(name,event=>{event.preventDefault();$('dropzone').classList.remove('drag');});
$('dropzone').addEventListener('drop', event=>upload(event.dataTransfer.files[0]));
$('dropzone').addEventListener('keydown',event=>{
  if(event.key==='Enter'||event.key===' '){event.preventDefault();$('audio-file').click();}
});
$('target-text').addEventListener('input',()=>{ $('char-count').textContent = `${$('target-text').value.length} / 3000`; });
$('generate-form').addEventListener('submit',async event=>{
  event.preventDefault(); if(busy||audioBusy||uploading||!backendReady||studioClosed)return;
  if(!reference)return toast('请先上传参考音频。');
  if(!state.engines.index.ready)return toast(state.engines.index.status+'。请先补齐模型文件。');
  try {
    stopPolling();busy=true;$('generate').disabled=true;
    currentJob=null;
    render({id:null,state:'submitting',text:$('target-text').value,generated:false,message:'正在提交三次生成任务',segments:[],
      variant_count:3,current_variant:1,variants:Array.from({length:3},(_,i)=>({number:i+1,state:'queued',generated:false,transcription_state:'waiting'})),
      progress:{percent:0,indeterminate:true,elapsed_seconds:0,eta_seconds:null}});
    const preparedReference=await referenceEditor.forGeneration();
    const job=await api('/api/jobs',{reference:preparedReference,text:$('target-text').value,language:$('language').value});
    currentJob=job.id;$('result-player').removeAttribute('src');render(job);
    if(needsJobPolling(job))startPoll(job.id);
  } catch(error){
    busy=false;
    const elapsed=(lastJob?.progress?.elapsed_seconds||0)+Math.max(0,(performance.now()-lastReceivedAt)/1000);
    render({...lastJob,state:'error',message:'任务提交未完成：'+error.message,generated:false,
      progress:{...lastJob?.progress,elapsed_seconds:elapsed,indeterminate:false}});
    toast(error.message);$('generate').disabled=false;
  }
});
function stopPolling(){
  pollSession++;
  clearTimeout(poll);poll=null;
  if(pollController)pollController.abort();
  pollController=null;
}
function startPoll(jobId=currentJob){
  stopPolling();currentJob=jobId;
  const session=pollSession;
  async function tick(){
    if(session!==pollSession||jobId!==currentJob)return;
    const controller=new AbortController();pollController=controller;
    let again=true;
    try {
      const job=await api('/api/job/'+encodeURIComponent(jobId),undefined,{controller,timeoutMs:8000});
      if(session!==pollSession||jobId!==currentJob)return;
      if(job.id!==jobId)throw new Error('任务状态不匹配，正在重新查询');
      connectionLost=false;render(job);
      again=needsJobPolling(job);
    } catch(error){
      if(session!==pollSession||jobId!==currentJob)return;
      connectionLost=true;
      $('result-badge').textContent='正在重连';$('result-badge').className='badge bad';
      if(error.status===404){
        again=false;busy=false;connectionLost=false;syncAudioControls();$('cancel').hidden=true;
        if(lastJob){
          const elapsed=(lastJob.progress?.elapsed_seconds||0)+Math.max(0,(performance.now()-lastReceivedAt)/1000);
          lastJob={...lastJob,state:'error',generated:false,progress:{...lastJob.progress,elapsed_seconds:elapsed}};
        }
        $('result-badge').textContent='任务已中断';$('task-progress').classList.remove('indeterminate');
        $('job-message').hidden=false;
        $('job-message').textContent='任务已无法查询，服务可能已重新启动。请重新提交生成任务。';
      }
      updateClock();
    } finally {
      if(session===pollSession&&jobId===currentJob){
        if(pollController===controller)pollController=null;
        if(again)poll=setTimeout(tick,1000);
        else poll=null;
      }
    }
  }
  return tick();
}
function duration(seconds){
  seconds=Math.max(0,Math.floor(seconds));
  const minutes=Math.floor(seconds/60),rest=seconds%60;
  return `${String(minutes).padStart(2,'0')}:${String(rest).padStart(2,'0')}`;
}
function updateClock(){
  if(!lastJob)return;
  const p=lastJob.progress||{};
  const active=activeStates.has(lastJob.state);
  const delta=active?Math.max(0,(performance.now()-lastReceivedAt)/1000):0;
  const elapsed=duration((p.elapsed_seconds||0)+delta);
  const labels={submitting:'正在提交',queued:'等待处理',loading:'加载模型',generating:'生成中',combining:'保存音频',ready:'生成完成',error:'生成失败',cancelled:'已取消'};
  let text;
  if(connectionLost)text=`连接中断，自动重试中 · 已用 ${elapsed}`;
  else if(!active)text=`${labels[lastJob.state]||'已结束'} · 用时 ${elapsed}`;
  else {
    const percent=p.indeterminate?'':` ${Math.max(0,Math.min(100,p.percent||0))}%`;
    const eta=p.eta_seconds!=null&&p.eta_seconds-delta>0?`${lastJob.variants?'本次剩余约':'剩余约'} ${duration(p.eta_seconds-delta)}`:'剩余估算中';
    text=`${labels[lastJob.state]||'处理中'}${percent} · 已用 ${elapsed} · ${eta}`;
  }
  if(lastJob.variants&&active)text=`第 ${lastJob.current_variant||1}/3 次 · ${text}`;
  $('progress-text').textContent=text;
  $('progress-text').title=`${text}；${lastJob.message||''}；${p.estimate_source||'时间估算会调整'}`;
}
setInterval(updateClock,1000);
function addText(parent,tag,text,cls){const el=document.createElement(tag);el.textContent=text;if(cls)el.className=cls;parent.appendChild(el);return el;}
function render(job){
  lastJob=job;lastReceivedAt=performance.now();connectionLost=false;
  $('empty-result').hidden=true;$('job-result').hidden=false;
  busy=activeStates.has(job.state);syncAudioControls();
  const labels={submitting:'正在提交',queued:'等待处理',loading:'加载模型',generating:'生成中',combining:'保存音频',ready:'生成完成',error:'运行失败',cancelled:'已取消'};
  $('result-badge').textContent=labels[job.state]||job.state;$('result-badge').className='badge'+(job.generated?' good':job.state==='error'?' bad':'');
  if(job.state==='ready'&&needsJobPolling(job))$('result-badge').textContent='生成完成 · 识别文字中';
  $('job-message').textContent=job.message;
  $('job-message').hidden=busy;
  const p=job.progress||{},percent=Math.max(0,Math.min(100,p.percent||0));
  $('progress-bar').style.width=percent+'%';
  $('task-progress').classList.toggle('indeterminate',busy&&p.indeterminate);
  $('task-progress').classList.toggle('failed',['error','rejected'].includes(job.state));
  $('task-progress').setAttribute('aria-valuetext',job.message);
  if(busy&&p.indeterminate)$('task-progress').removeAttribute('aria-valuenow');
  else $('task-progress').setAttribute('aria-valuenow',String(percent));
  updateClock();
  const container=$('segments');
  // Preserve media playback during polling: redraw only when segments change.
  const signature=JSON.stringify(job.segments);
  if(container.dataset.signature!==signature){container.dataset.signature=signature;container.replaceChildren();job.segments.forEach((segment,index)=>{
    const row=document.createElement('div');row.className='segment';const title=addText(row,'div','', 'segment-header');addText(title,'span',`片段 ${String(index+1).padStart(2,'0')}`);addText(title,'span',segment.generated?'✓ 已生成':'等待生成');
    addText(row,'p','原文：'+segment.text);
    if(segment.preview){const player=document.createElement('audio');player.controls=true;player.preload='none';player.src=segment.preview;row.appendChild(player);}container.appendChild(row);
  });}
  $('cancel').hidden=!busy||!job.id;
  $('report-link').hidden=!job.id;$('log-link').hidden=!job.id;
  $('report-link').href='/report/'+job.id;$('log-link').href='/log/'+job.id;
  $('export').disabled=job.state!=='ready'||!job.generated;
  const availableSegments=job.segments.filter(segment=>segment.preview).length;
  $('segment-toggle').disabled=availableSegments===0;
  $('segment-count').textContent=String(availableSegments);
  $('result-player').hidden=Boolean(job.variants)||!job.preview;
  $('playback-hint').hidden=Boolean(job.preview);
  $('playback-hint').textContent=busy?'正在准备你的声音…':job.state==='error'?'生成未完成，请查看运行日志。':'任务已取消，可重新生成。';
  if(!job.variants&&job.preview && $('result-player').getAttribute('src')!==job.preview)$('result-player').src=job.preview;
  renderVariants(job);
}
document.addEventListener('visibilitychange',()=>{
  if(document.visibilityState==='visible'&&needsJobPolling(lastJob)&&currentJob)startPoll(currentJob);
});
$('export').addEventListener('click',()=>{
  if(!lastJob?.generated||!lastJob.download)return;
  const link=document.createElement('a');link.href=lastJob.download;link.download='voice.wav';
  document.body.appendChild(link);link.click();link.remove();
});
$('cancel').addEventListener('click',async()=>{try{await api('/api/cancel',{id:currentJob});$('cancel').disabled=true;setTimeout(()=>$('cancel').disabled=false,2000);}catch(error){toast(error.message);}});
$('quit-studio').addEventListener('click',async()=>{
  $('quit-studio').disabled=true;
  studioClosed=true;clearTimeout(modelTimer);
  try{
    await api('/api/shutdown',{});stopPolling();busy=false;
    clearTimeout(modelTimer);
    referenceEditor.stop();
    stopVariantAudio();
    syncAudioControls();
    if(lastJob)lastJob.state='cancelled';
    $('generate').disabled=true;$('audio-file').disabled=true;
    $('file-notice').hidden=false;$('file-notice-title').textContent='软件已退出。';
    $('file-notice-message').textContent='可以关闭此页面，下次双击桌面的“音色工作室”启动。';
    $('cancel').hidden=true;$('task-progress').classList.remove('indeterminate');
  }catch(error){studioClosed=false;renderModel(state?.model||{state:'idle'});$('quit-studio').disabled=false;toast(error.message);}
});
if (location.protocol === 'file:') {
  $('file-notice').hidden = false;
  $('generate').disabled = true;
  $('audio-file').disabled = true;
  $('index-status').textContent = '请先启动本地服务';
} else {
  load().catch(error=>{
    $('file-notice').hidden = false;
    $('generate').disabled = true;
    toast('无法连接本地服务：'+error.message);
  });
}
