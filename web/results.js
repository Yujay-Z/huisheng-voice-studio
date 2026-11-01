'use strict';
let variantJob=null,variantCards=[];
function stopVariantAudio(){for(const card of variantCards)card.player.pause();}
function renderVariants(job){
  const variants=job.variants||[],batch=variants.length>0;
  $('preview-card').classList.toggle('has-variants',batch);
  $('variant-results').hidden=!batch;
  $('single-playback').hidden=batch;
  $('single-details').hidden=batch;
  $('export').hidden=batch;
  if(!batch)return;
  $('result-player').pause();
  if(variantJob!==job.id||variantCards.length!==variants.length){
    stopVariantAudio();
    variantJob=job.id;variantCards=[];$('variant-results').replaceChildren();
    for(let i=0;i<variants.length;i++){
      const root=addText($('variant-results'),'article','', 'variant-card');root.setAttribute('role','listitem');
      const heading=addText(root,'div','', 'variant-heading');
      addText(heading,'strong',`第 ${i+1} 次`);
      const status=addText(heading,'span','', 'variant-state');status.id=`variant-${i}-status`;
      const download=addText(heading,'a','下载 WAV ↓');download.id=`variant-${i}-download`;download.download=`voice-${i+1}.wav`;
      const player=document.createElement('audio');player.id=`variant-${i}-player`;player.controls=true;player.preload='metadata';player.setAttribute('aria-label',`第 ${i+1} 次生成音频`);root.appendChild(player);
      const waiting=addText(root,'p','', 'variant-wait');
      const transcript=addText(root,'div','', 'variant-transcript');
      addText(transcript,'span','识别文字');
      const text=addText(transcript,'p','');text.id=`variant-${i}-text`;text.setAttribute('role','status');
      variantCards.push({root,status,download,player,waiting,text});
    }
  }
  variants.forEach((variant,index)=>{
    const card=variantCards[index],ready=Boolean(variant.generated&&variant.preview);
    const labels={queued:'等待生成',loading:'准备模型',generating:'生成中',combining:'保存音频',ready:'已生成',error:'运行失败',cancelled:'已取消'};
    const recognizing=['queued','processing'].includes(variant.transcription_state);
    card.status.textContent=ready&&recognizing?'已生成 · 识别文字中':labels[variant.state]||'等待生成';
    card.root.classList.toggle('variant-failed',variant.state==='error');
    card.player.hidden=!ready;card.download.hidden=!ready;
    if(ready){
      if(card.player.getAttribute('src')!==variant.preview)card.player.src=variant.preview;
      card.download.href=variant.download;
    }
    card.waiting.hidden=ready;card.waiting.textContent=variant.message||labels[variant.state]||'等待生成';
    let text='音频生成后显示识别文字，供你自行校对。';
    if(variant.transcription_state==='ready')text=variant.recognized_text||'未识别出文字，请自行试听。';
    else if(variant.transcription_state==='error')text='文字识别未完成：'+(variant.transcription_error||'请自行试听。');
    else if(recognizing)text='正在识别文字，可先试听或下载音频。';
    else if(variant.transcription_state==='cancelled')text='文字识别已取消。';
    else if(variant.transcription_state==='unavailable')text='本次未生成音频。';
    if(card.text.textContent!==text)card.text.textContent=text;
  });
}
function needsJobPolling(job){
  return Boolean(job&&(activeStates.has(job.state)||(job.variants||[]).some(v=>v.generated&&['queued','processing'].includes(v.transcription_state))));
}
