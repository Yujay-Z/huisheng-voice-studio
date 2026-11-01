'use strict';
function createReferenceEditor({api,onChange,onBusy,onError,onNotice=()=>{}}){
  const el=id=>document.getElementById(id);
  let original=null,current=null,start=0,end=0,working=false,preparing=false,locked=false,drag=null,outputs=null,task=null,timer=null,waveSource=null;
  const time=value=>`${Math.floor(value/60)}:${(value%60).toFixed(2).padStart(5,'0')}`;
  function notice(text){el('audio-edit-notice').textContent=text;}
  function separationNotice(text){el('separation-notice').textContent=text;}
  function refreshControls(){
    const disabled=working||preparing||locked||!current;
    for(const id of ['selection-start','selection-end','selection-start-range','selection-end-range','apply-crop','crop-reference','restore-audio','separate-start','separation-mode','use-vocals','preview-reference','preview-selection','edit-audio','open-separation'])el(id).disabled=disabled;
  }
  function setBusy(value){
    working=value;refreshControls();onBusy(value);
  }
  function requireSource(){
    if(preparing)throw new Error('音频正在上传并准备波形，请稍候，无需重新上传。');
    if(!current)throw new Error('请先上传参考音频。');
  }
  function draw(){
    if(!current)return;
    if(waveSource!==current){
      const peaks=Array.isArray(current.peaks)?current.peaks:[];
      const path=peaks.map((value,index)=>{const x=(index+.5)*1000/peaks.length,h=Math.max(1,Math.min(1,Number(value)||0)*43);return `M${x.toFixed(2)},${(50-h).toFixed(2)}V${(50+h).toFixed(2)}`;}).join(' ');
      for(const id of ['reference-wave-path','editor-wave-path'])el(id).setAttribute('d',path);
      waveSource=current;
    }
    for(const id of ['reference-selection','editor-selection']){el(id).style.left=(start/current.seconds*100)+'%';el(id).style.width=((end-start)/current.seconds*100)+'%';}
    el('selection-start').value=start.toFixed(2);el('selection-end').value=end.toFixed(2);
    el('selection-start-range').value=start;el('selection-end-range').value=end;
    el('selection-duration').textContent=`已选 ${(end-start).toFixed(2)} 秒`;
    el('reference-duration').textContent=`${time(start)} – ${time(end)} / ${time(current.seconds)}`;
    el('separation-source').textContent=`处理当前参考选区：${time(start)} – ${time(end)}，共 ${(end-start).toFixed(2)} 秒。`;
  }
  function changeSelection(value,which){
    if(!current||working||preparing||locked)return;
    value=Number(value);if(!Number.isFinite(value))return;
    if(which==='start')start=Math.max(0,Math.min(end-.05,value));
    else end=Math.min(current.seconds,Math.max(start+.05,value));
    draw();
  }
  function setCurrent(data){
    current=data;start=0;end=Math.min(30,data.seconds);
    for(const id of ['editor-player','reference-player'])el(id).pause();
    if(el('editor-player').getAttribute('src')!==data.url)el('editor-player').src=data.url;
    for(const id of ['selection-start','selection-end','selection-start-range','selection-end-range'])el(id).max=data.seconds;
    el('reference-editor').hidden=false;el('dropzone').classList.add('has-audio');
    refreshControls();draw();onChange(data);
  }
  async function applySelection(){
    requireSource();
    if(end-start<1-.00001||end-start>30+.00001)throw new Error('请选择 1–30 秒的音频片段。');
    if(start<.00001&&Math.abs(end-current.seconds)<.00001)return current.id;
    setBusy(true);
    el('crop-reference').textContent='裁剪中…';el('apply-crop').textContent='裁剪中…';
    try{
      const data=await api('/api/audio/crop',{reference:current.id,start,end});
      setCurrent(data);notice('裁剪已应用，原始上传仍可恢复。');return data.id;
    }finally{el('crop-reference').textContent='裁剪选区';el('apply-crop').textContent='应用裁剪';setBusy(false);}
  }
  for(const which of ['start','end']){
    el('selection-'+which).addEventListener('change',event=>changeSelection(event.target.value,which));
    el('selection-'+which+'-range').addEventListener('input',event=>changeSelection(event.target.value,which));
  }
  for(const id of ['reference-wave','editor-wave'])el(id).addEventListener('pointerdown',event=>{
    if(!current||working||preparing||locked||event.button!==0)return;
    const rect=el(id).getBoundingClientRect(),value=Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width))*current.seconds;
    drag={id,rect,which:Math.abs(value-start)<=Math.abs(value-end)?'start':'end'};
    el(id).setPointerCapture(event.pointerId);changeSelection(value,drag.which);event.preventDefault();
  });
  document.addEventListener('pointermove',event=>{if(!drag||!current)return;changeSelection((event.clientX-drag.rect.left)/drag.rect.width*current.seconds,drag.which);});
  document.addEventListener('pointerup',()=>{drag=null;});
  document.addEventListener('pointercancel',()=>{drag=null;});
  el('edit-audio').addEventListener('click',()=>{if(current&&!working&&!preparing&&!locked)el('audio-dialog').showModal();});
  el('audio-close').addEventListener('click',()=>el('audio-dialog').close());
  el('open-separation').addEventListener('click',()=>{if(current&&!working&&!preparing&&!locked)el('separation-dialog').showModal();});
  el('separation-close').addEventListener('click',()=>el('separation-dialog').close());
  async function previewSelection(playerId){
    if(!current||working||preparing||locked)return;
    const player=el(playerId);player.currentTime=start;
    try{await player.play();}catch(error){onError(error.message);}
  }
  el('preview-selection').addEventListener('click',()=>previewSelection('editor-player'));
  el('preview-reference').addEventListener('click',()=>previewSelection('reference-player'));
  for(const id of ['editor-player','reference-player'])el(id).addEventListener('timeupdate',()=>{if(el(id).currentTime>=end)el(id).pause();});
  el('apply-crop').addEventListener('click',async()=>{if(working)return;try{await applySelection();}catch(error){notice(error.message);onError(error.message);}});
  el('crop-reference').addEventListener('click',async()=>{
    if(working)return;
    try{await applySelection();notice('裁剪已完成，原始上传仍可恢复。');onNotice('裁剪完成，已使用所选音频。');}
    catch(error){notice(error.message);onError(error.message);}
  });
  el('restore-audio').addEventListener('click',()=>{if(original&&!working&&!preparing&&!locked){setCurrent(original);notice('已恢复原始上传。');}});
  function renderTask(data){
    el('separation-status').textContent=`${data.message} · 已用 ${data.elapsed_seconds||0} 秒`;
    el('separation-progress').hidden=false;
    const active=!['ready','error','cancelled'].includes(data.state);
    el('separate-cancel').hidden=!active;
    if(data.state==='ready'){
      outputs=data;el('separation-results').hidden=false;el('separation-progress').hidden=true;
      el('vocals-player').src=data.vocals.url;el('background-player').src=data.background.url;
      el('download-vocals').href=data.vocals.download;el('download-background').href=data.background.download;
      separationNotice(data.message);setBusy(false);
    }else if(!active){el('separation-progress').hidden=true;separationNotice(data.message);setBusy(false);}
    return active;
  }
  async function pollTask(id){
    if(id!==task)return;
    try{const result=await api('/api/audio/task/'+id,undefined,{timeoutMs:8000});if(id===task&&renderTask(result))timer=setTimeout(()=>pollTask(id),1000);}
    catch(error){if(id!==task)return;if(error.status===404){separationNotice('音频处理任务已中断，请重新处理。');setBusy(false);}else{el('separation-status').textContent='状态暂未连接，正在重新查询…';timer=setTimeout(()=>pollTask(id),1500);}}
  }
  el('separate-start').addEventListener('click',async()=>{
    if(!current||working||preparing||locked)return;
    setBusy(true);el('separation-results').hidden=true;separationNotice('只处理当前选区，原始音频不会被覆盖。');
    try{
      const result=await api('/api/audio/separate',{reference:current.id,start,end,mode:el('separation-mode').value||'noise'});
      task=result.id;renderTask(result);pollTask(task);
    }catch(error){setBusy(false);separationNotice(error.message);onError(error.message);}
  });
  el('separate-cancel').addEventListener('click',async()=>{if(task)try{await api('/api/audio/cancel',{id:task});}catch(error){onError(error.message);}});
  el('use-vocals').addEventListener('click',()=>{if(outputs&&!working&&!preparing&&!locked){setCurrent(outputs.vocals);separationNotice('已使用提取的人声作为参考；仍可在“精确选区”恢复原始上传。');}});
  return {
    clear(){clearTimeout(timer);task=null;outputs=null;original=null;current=null;waveSource=null;drag=null;for(const id of ['reference-player','editor-player']){el(id).pause();el(id).removeAttribute('src');}for(const id of ['reference-wave-path','editor-wave-path'])el(id).setAttribute('d','');el('reference-editor').hidden=true;el('dropzone').classList.remove('has-audio');el('separation-results').hidden=true;el('separation-progress').hidden=true;refreshControls();},
    setUploading(value){preparing=value;refreshControls();if(value)notice('音频正在上传并准备波形，请稍候，无需重新上传。');},
    setLocked(value){locked=value;refreshControls();},
    stop(){clearTimeout(timer);task=null;for(const id of ['editor-player','vocals-player','background-player'])el(id).pause();},
    setSource(data){clearTimeout(timer);task=null;outputs=null;original=data;preparing=false;el('separation-results').hidden=true;el('separation-progress').hidden=true;setCurrent(data);notice('音频已就绪，可立即裁剪。可拖动波形边界，也可输入起止时间。');},
    forGeneration:applySelection,
    setProcessingStatus(data){el('separation-model-status').textContent=data?.message||'本地 CPU 处理，首次需运行“安装音频处理.cmd”。';}
  };
}
