const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const response=(data,status=200)=>({ok:status>=200&&status<300,status,json:async()=>data});
const flush=()=>new Promise(resolve=>setImmediate(resolve));

async function setup(initialHandler){
  const elements=new Map(),timeouts=new Map(),intervals=new Map(),documentEvents=new Map(),requests=[];
  let timer=0,now=0;
  class Element {
    constructor(){
      this.hidden=false;this.textContent='';this.style={};this.dataset={};this.attrs={};this.children=[];
      this.checked=false;this.value='';this.events=new Map();
      const classes=new Set();
      this.classList={toggle:(key,on)=>on?classes.add(key):classes.delete(key),add:key=>classes.add(key),
        remove:key=>classes.delete(key),contains:key=>classes.has(key)};
    }
    set id(value){this.attrs.id=value;elements.set(value,this);} get id(){return this.attrs.id;}
    set src(value){this.attrs.src=value;} get src(){return this.attrs.src;}
    addEventListener(type,fn){this.events.set(type,fn);}
    dispatch(type,event={}){return this.events.get(type)?.(event);}
    setAttribute(key,value){this.attrs[key]=value;} getAttribute(key){return this.attrs[key]??null;}
    removeAttribute(key){delete this.attrs[key];}
    appendChild(child){this.children.push(child);child.parent=this;}
    replaceChildren(){for(const child of [...this.children])child.remove();this.children=[];}
    click(){this.clicked=true;}
    focus(){context.document.activeElement=this;}
    showModal(){this.open=true;}
    close(){this.open=false;}
    pause(){this.paused=true;}
    play(){this.paused=false;return Promise.resolve();}
    remove(){if(this.id)elements.delete(this.id);if(this.parent)this.parent.children=this.parent.children.filter(child=>child!==this);}
  }
  const html=fs.readFileSync(path.join(__dirname,'../web/index.html'),'utf8');
  for(const match of html.matchAll(/<[a-z][^>]*\bid="([^"]+)"[^>]*>/g)){
    const node=new Element();node.id=match[1];node.hidden=/\bhidden\b/.test(match[0]);
  }
  const element=id=>elements.get(id)??null;
  element('language').value='ZH';
  const state={api_version:3,build:'editor-variants-v1',supports_shutdown:true,token:'test',model:{state:'ready'},audio_processing:{ready:true,message:'音频处理已就绪'},engines:{index:{ready:true,status:'ready'}},jobs:[]};
  let handler=initialHandler|| (async url=>response(state));
  const context=vm.createContext({
    document:{getElementById:element,querySelectorAll:()=>[],querySelector:()=>null,
      body:new Element(),createElement:()=>new Element(),visibilityState:'visible',addEventListener:(type,fn)=>documentEvents.set(type,fn)},
    location:{protocol:'http:'},performance:{now:()=>now},AbortController,
    fetch:async(url,options)=>{requests.push({url,options});return handler(url,options);},
    setInterval:(fn,ms)=>{intervals.set(++timer,{fn,ms});return timer;},clearInterval:id=>intervals.delete(id),
    setTimeout:(fn,ms)=>{timeouts.set(++timer,{fn,ms});return timer;},clearTimeout:id=>timeouts.delete(id)
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/audio-editor.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/results.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8'),context);
  await flush();
  return {context,element,timeouts,intervals,documentEvents,requests,advance:ms=>now+=ms,
    setHandler:fn=>handler=fn,
    async fire(ms){const entry=[...timeouts.entries()].find(([,value])=>value.ms===ms);assert.ok(entry,`missing ${ms}ms timer`);timeouts.delete(entry[0]);return entry[1].fn();}};
}

function job(state='loading',values={}){
  return {id:'test',state,passed:false,message:'处理当前任务',segments:[{text:'你好',passed:false,attempts:0}],
    progress:{phase:state,percent:0,indeterminate:state==='loading',elapsed_seconds:12,eta_seconds:100,estimate_source:'首次粗估',round:1},...values};
}
function finished(id='test'){
  return job('ready',{id,generated:true,preview:'/audio/final/'+id,download:'/download/'+id,message:'生成完成，请自行试听',
    progress:{phase:'combining',percent:100,elapsed_seconds:28,eta_seconds:null},
    segments:[{text:'你好',generated:true,preview:'/audio/segment/'+id+'/0'}]});
}

test('one compact status line updates elapsed and estimated time',async()=>{
  const ui=await setup();ui.context.render(job());
  assert.equal(ui.element('result-badge').textContent,'加载模型');
  assert.ok(ui.element('task-progress').classList.contains('indeterminate'));
  assert.match(ui.element('progress-text').textContent,/加载模型 · 已用 00:12 · 剩余约 01:40/);
  ui.advance(2000);ui.context.updateClock();
  assert.match(ui.element('progress-text').textContent,/已用 00:14 · 剩余约 01:38/);
  assert.equal(ui.element('elapsed-time'),null);
  assert.equal(ui.element('remaining-time'),null);
});

test('submission automatically reaches audio preview without reloading',async()=>{
  const ui=await setup();let checks=0;
  assert.equal(ui.element('install-help').hidden,true);
  ui.setHandler(async url=>{
    if(url==='/api/upload')return response({id:'reference',seconds:3,url:'/audio/reference/reference'});
    if(url==='/api/jobs')return response(job('queued'));
    if(url==='/api/job/test')return response(++checks===1?job('generating'):finished());
    throw new Error('unexpected request '+url);
  });
  await ui.context.upload({name:'test.wav',size:100});
  ui.element('target-text').value='你好';
  await ui.element('generate-form').dispatch('submit',{preventDefault(){}});await flush();
  const submitted=JSON.parse(ui.requests.find(r=>r.url==='/api/jobs').options.body);
  assert.equal(submitted.engine,undefined,'the server selects the only supported model');
  assert.equal(submitted.reference_text,undefined,'reference transcription has been removed');
  assert.equal(checks,1,'first status request should happen immediately');
  assert.equal(ui.element('result-badge').textContent,'生成中');
  await ui.fire(1000);
  assert.equal(ui.element('result-player').hidden,false);
  assert.equal(ui.element('result-player').src,'/audio/final/test');
  assert.equal(ui.element('listen-label'),null);
  assert.equal(ui.element('export').disabled,false);
  const beforeDownload=ui.requests.length;
  await ui.element('export').dispatch('click');
  assert.equal(ui.requests.length,beforeDownload,'download should not wait for an approval request');
  assert.equal(ui.context.document.body.children.length,0);
  assert.equal(ui.element('generate').disabled,false);
  assert.equal(ui.element('result-badge').textContent,'生成完成');
  assert.equal(ui.element('segment-toggle').disabled,false);
  await ui.element('segment-toggle').dispatch('click');
  assert.equal(ui.element('segment-dialog').open,true);
  await ui.element('segment-close').dispatch('click');
  assert.equal(ui.element('segment-dialog').open,false);
  assert.ok(![...ui.timeouts.values()].some(t=>t.ms===1000));
  assert.ok(ui.requests.filter(r=>r.url.startsWith('/api/job/')).every(r=>r.options.cache==='no-store'));
});

test('timed out status request is aborted and polling recovers automatically',async()=>{
  const ui=await setup();let checks=0;ui.context.render(job('generating'));
  ui.setHandler((url,options)=>{
    if(++checks===1)return new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>{const error=new Error('aborted');error.name='AbortError';reject(error);}));
    return response(finished());
  });
  const pending=ui.context.startPoll('test');
  await ui.fire(8000);await pending;
  assert.match(ui.element('progress-text').textContent,/自动重试/);
  await ui.fire(1000);
  assert.equal(ui.element('result-player').hidden,false);
  assert.equal(ui.element('result-badge').textContent,'生成完成');
});

test('repeated submit while creation is pending does not create duplicate jobs',async()=>{
  const ui=await setup();let resolveSubmit,submissions=0;
  ui.setHandler(url=>{
    if(url==='/api/upload')return response({id:'reference',seconds:3,url:'/audio/reference/reference'});
    if(url==='/api/jobs'){submissions++;return new Promise(resolve=>resolveSubmit=resolve);}
    return response(finished());
  });
  await ui.context.upload({name:'test.wav',size:100});
  const first=ui.element('generate-form').dispatch('submit',{preventDefault(){}});
  assert.equal(ui.element('empty-result').hidden,true);
  assert.equal(ui.element('job-result').hidden,false);
  assert.equal(ui.element('result-badge').textContent,'正在提交');
  await ui.element('generate-form').dispatch('submit',{preventDefault(){}});
  assert.equal(submissions,1);
  resolveSubmit(response(job('queued')));await first;await flush();
  assert.equal(ui.element('result-player').hidden,false);
});

test('late old response cannot replace a new task or stop its polling',async()=>{
  const ui=await setup();let resolveOld,checks=0;
  ui.setHandler(url=>{
    if(url==='/api/job/old')return new Promise(resolve=>resolveOld=resolve);
    return response(++checks===1?job('generating',{id:'new'}):finished('new'));
  });
  ui.context.render(job('generating',{id:'old'}));
  const old=ui.context.startPoll('old');
  ui.context.render(job('queued',{id:'new'}));
  await ui.context.startPoll('new');
  resolveOld(response(finished('old')));await old;
  assert.equal(ui.element('result-badge').textContent,'生成中');
  assert.equal(ui.element('result-player').hidden,true);
  await ui.fire(1000);
  assert.equal(ui.element('result-player').src,'/audio/final/new');
});

test('temporary server error retries and then displays the final result',async()=>{
  const ui=await setup();let checks=0;ui.context.render(job('generating'));
  ui.setHandler(()=>++checks===1?response({error:'temporary'},503):response(finished()));
  await ui.context.startPoll('test');
  assert.equal(ui.element('result-badge').textContent,'正在重连');
  await ui.fire(1000);
  assert.equal(ui.element('result-player').hidden,false);
});

test('failure stops animation and timer without displaying a completed bar',async()=>{
  const ui=await setup(),failed=job('error');failed.progress.percent=42;ui.context.render(failed);
  ui.advance(10000);ui.context.updateClock();
  assert.equal(ui.element('progress-text').textContent,'生成失败 · 用时 00:12');
  assert.equal(ui.element('progress-bar').style.width,'42%');
  assert.ok(!ui.element('task-progress').classList.contains('indeterminate'));
});

test('missing task stops polling and clearly reports interruption',async()=>{
  const ui=await setup();ui.context.render(job());
  ui.setHandler(()=>response({error:'未找到'},404));
  await ui.context.startPoll('test');
  assert.equal(ui.element('result-badge').textContent,'任务已中断');
  assert.equal(ui.element('generate').disabled,false);
  assert.match(ui.element('progress-text').textContent,/生成失败/);
  assert.ok(![...ui.timeouts.values()].some(t=>t.ms===1000));
});

test('returning to the tab immediately catches up with a finished task',async()=>{
  const ui=await setup();let checks=0;
  ui.context.render(job('generating'));
  ui.setHandler(()=>response(++checks===1?job('generating'):finished()));
  await ui.context.startPoll('test');
  ui.documentEvents.get('visibilitychange')();await flush();
  assert.equal(ui.element('result-player').hidden,false);
});

test('old backend cannot start a task',async()=>{
  const ui=await setup();
  ui.setHandler(()=>response({token:'old',engines:{},jobs:[]}));
  await ui.context.load();
  assert.equal(ui.element('generate').disabled,true);
  assert.equal(ui.element('file-notice').hidden,false);
  assert.match(ui.element('file-notice-title').textContent,/旧版后台/);
});

test('submission failure appears in result panel instead of staying empty',async()=>{
  const ui=await setup();
  ui.setHandler(url=>url==='/api/upload'?response({id:'reference',seconds:3,url:'/audio/reference/reference'}):response({error:'request failed'},400));
  await ui.context.upload({name:'test.wav',size:100});
  await ui.element('generate-form').dispatch('submit',{preventDefault(){}});
  assert.equal(ui.element('empty-result').hidden,true);
  assert.equal(ui.element('job-result').hidden,false);
  assert.equal(ui.element('result-badge').textContent,'运行失败');
  assert.match(ui.element('job-message').textContent,/request failed/);
});

test('quit button stops polling and explains how to reopen from desktop',async()=>{
  const ui=await setup();ui.context.render(job('generating'));
  let shutdowns=0;
  ui.setHandler(url=>{assert.equal(url,'/api/shutdown');shutdowns++;return response({ok:true});});
  await ui.element('quit-studio').dispatch('click');
  assert.equal(shutdowns,1);
  assert.equal(ui.element('generate').disabled,true);
  assert.equal(ui.element('file-notice-title').textContent,'软件已退出。');
  assert.match(ui.element('file-notice-message').textContent,/桌面/);
});

test('sidebar overlays the workspace, closes with Escape and restores keyboard focus',async()=>{
  const ui=await setup();
  assert.equal(ui.element('sidebar').hidden,true);
  assert.equal(ui.element('reference-text'),null);
  assert.equal(ui.element('transcribe'),null);
  await ui.element('menu-toggle').dispatch('click');
  assert.equal(ui.element('sidebar').hidden,false);
  assert.equal(ui.element('sidebar-backdrop').hidden,false);
  assert.equal(ui.element('studio-main').inert,true);
  assert.equal(ui.element('menu-toggle').getAttribute('aria-expanded'),'true');
  ui.documentEvents.get('keydown')({key:'Escape',preventDefault(){}});
  assert.equal(ui.element('sidebar').hidden,true);
  assert.equal(ui.element('studio-main').inert,false);
  assert.equal(ui.context.document.activeElement,ui.element('menu-toggle'));
});

test('help opens from the drawer and upload accepts keyboard activation',async()=>{
  const ui=await setup();
  await ui.element('menu-toggle').dispatch('click');
  await ui.element('sidebar-help').dispatch('click');
  assert.equal(ui.element('sidebar').hidden,true);
  assert.equal(ui.element('help-dialog').open,true);
  await ui.element('help-close').dispatch('click');
  assert.equal(ui.element('help-dialog').open,false);
  await ui.element('dropzone').dispatch('keydown',{key:'Enter',preventDefault(){}});
  assert.equal(ui.element('audio-file').clicked,true);
});

test('selected samples are cropped before generation and the original upload can be restored',async()=>{
  const ui=await setup();
  ui.setHandler(url=>{
    if(url==='/api/upload')return response({id:'original',seconds:60,peaks:[.1,.8,.4],url:'/audio/reference/original'});
    if(url==='/api/audio/crop')return response({id:'trimmed',seconds:2,peaks:[.2,.7],url:'/audio/reference/trimmed'});
    if(url==='/api/jobs')return response(job('queued'));
    if(url==='/api/job/test')return response(finished());
    throw new Error('Unexpected request: '+url);
  });
  await ui.context.upload({name:'recording.wav',size:100});
  assert.equal(ui.element('reference-editor').hidden,false);
  assert.match(ui.element('reference-wave-path').getAttribute('d'),/M/);
  await ui.element('selection-start').dispatch('change',{target:{value:'2'}});
  await ui.element('selection-end').dispatch('change',{target:{value:'4'}});
  await ui.element('generate-form').dispatch('submit',{preventDefault(){}});await flush();
  const crop=JSON.parse(ui.requests.find(r=>r.url==='/api/audio/crop').options.body);
  assert.deepEqual(crop,{reference:'original',start:2,end:4});
  const submitted=JSON.parse(ui.requests.find(r=>r.url==='/api/jobs').options.body);
  assert.equal(submitted.reference,'trimmed');
  await ui.element('restore-audio').dispatch('click');
  assert.equal(ui.element('reference-player').src,'/audio/reference/original');
  assert.ok(!ui.requests.some(r=>r.url==='/api/audio/separate'),'separation must remain optional');
});

test('separation updates automatically and extracted vocals can become the reference',async()=>{
  const ui=await setup();let checks=0;
  ui.setHandler(url=>{
    if(url==='/api/upload')return response({id:'original',seconds:2,peaks:[.1,.8],url:'/audio/reference/original'});
    if(url==='/api/audio/separate')return response({id:'audio-task',state:'queued',message:'准备处理'});
    if(url==='/api/audio/task/audio-task')return response(++checks===1?{state:'processing',message:'分离人声'}:{state:'ready',message:'完成',vocals:{id:'voice',seconds:2,peaks:[.2,.7],url:'/audio/reference/voice',download:'/audio/reference/voice?download=1'},background:{id:'background',url:'/audio/reference/background',download:'/audio/reference/background?download=1'}});
    if(url==='/api/jobs')return response(job('queued'));
    if(url==='/api/job/test')return response(finished());
    throw new Error('Unexpected request: '+url);
  });
  await ui.context.upload({name:'recording.wav',size:100});
  ui.element('separation-mode').value='both';
  await ui.element('separate-start').dispatch('click');await flush();
  assert.equal(ui.element('generate').disabled,true);
  await ui.fire(1000);
  assert.equal(ui.element('separation-results').hidden,false);
  assert.equal(ui.element('generate').disabled,false);
  assert.equal(ui.element('download-background').href,'/audio/reference/background?download=1');
  await ui.element('use-vocals').dispatch('click');
  assert.equal(ui.element('reference-player').src,'/audio/reference/voice');
  await ui.element('generate-form').dispatch('submit',{preventDefault(){}});await flush();
  assert.equal(JSON.parse(ui.requests.find(r=>r.url==='/api/jobs').options.body).reference,'voice');
});

test('the main crop button applies the selection in one click without opening any dialog',async()=>{
  const ui=await setup();
  ui.setHandler(url=>{
    if(url==='/api/upload')return response({id:'original',seconds:4,peaks:[.1,.8],url:'/audio/reference/original'});
    if(url==='/api/audio/crop')return response({id:'trimmed',seconds:2,peaks:[.2,.7],url:'/audio/reference/trimmed'});
    throw new Error('Unexpected request: '+url);
  });
  await ui.context.upload({name:'recording.wav',size:100});
  await ui.element('selection-start').dispatch('change',{target:{value:'1'}});
  await ui.element('selection-end').dispatch('change',{target:{value:'3'}});
  await ui.element('crop-reference').dispatch('click');
  assert.equal(ui.requests.filter(r=>r.url==='/api/audio/crop').length,1);
  assert.equal(ui.element('reference-player').src,'/audio/reference/trimmed');
  assert.notEqual(ui.element('audio-dialog').open,true);
  assert.notEqual(ui.element('separation-dialog').open,true);
  assert.match(ui.element('toast').textContent,/裁剪完成/);
  assert.ok(!ui.requests.some(r=>r.url==='/api/audio/separate'));
});

test('vocal separation has an independent entry and dialog',async()=>{
  const ui=await setup();
  ui.setHandler(url=>url==='/api/upload'?response({id:'original',seconds:2,peaks:[.1,.8],url:'/audio/reference/original'}):response({}));
  await ui.context.upload({name:'recording.wav',size:100});
  assert.equal(ui.element('open-separation').disabled,false);
  await ui.element('open-separation').dispatch('click');
  assert.equal(ui.element('separation-dialog').open,true);
  assert.notEqual(ui.element('audio-dialog').open,true);
  await ui.element('separation-close').dispatch('click');
  await ui.element('edit-audio').dispatch('click');
  assert.equal(ui.element('audio-dialog').open,true);
  assert.equal(ui.element('separation-dialog').open,false);
  assert.ok(!ui.requests.some(r=>r.url==='/api/audio/separate'),'opening the tool must not start processing');
});

test('slow upload keeps every crop control locked until the reference is ready',async()=>{
  const ui=await setup();let resolveUpload;
  ui.setHandler(url=>{
    if(url==='/api/upload')return new Promise(resolve=>resolveUpload=resolve);
    if(url==='/api/audio/crop')return response({id:'trimmed',seconds:2,url:'/audio/reference/trimmed'});
    throw new Error('Unexpected request: '+url);
  });
  const pending=ui.context.upload({name:'slow.wav',size:100});
  assert.match(ui.element('upload-title').textContent,/正在上传/);
  assert.equal(ui.element('dropzone').getAttribute('aria-busy'),'true');
  const controls=['crop-reference','apply-crop','edit-audio','preview-reference','preview-selection','restore-audio','selection-start','selection-end-range','open-separation','separate-start','generate','audio-file'];
  for(const id of controls)assert.equal(ui.element(id).disabled,true,id);
  // A late generation-status response must not unlock an unfinished upload.
  ui.context.render(finished());
  for(const id of controls)assert.equal(ui.element(id).disabled,true,id);
  for(const id of ['crop-reference','apply-crop']){
    await ui.element(id).dispatch('click');
    assert.match(ui.element('toast').textContent,/正在上传并准备波形/);
    assert.doesNotMatch(ui.element('toast').textContent,/请先上传/);
  }
  assert.ok(!ui.requests.some(r=>r.url==='/api/audio/crop'));
  resolveUpload(response({id:'new',seconds:60,peaks:[.2,.8],url:'/audio/reference/new'}));await pending;
  assert.equal(ui.element('dropzone').getAttribute('aria-busy'),'false');
  for(const id of controls)assert.equal(ui.element(id).disabled,false,id);
  await ui.element('selection-start').dispatch('change',{target:{value:'1'}});
  await ui.element('selection-end').dispatch('change',{target:{value:'3'}});
  await ui.element('crop-reference').dispatch('click');
  assert.deepEqual(JSON.parse(ui.requests.find(r=>r.url==='/api/audio/crop').options.body),{reference:'new',start:1,end:3});
  assert.equal(ui.element('reference-player').src,'/audio/reference/trimmed');
});

test('replacing audio with an open crop dialog cannot crop or restore the previous file',async()=>{
  const ui=await setup();let resolveUpload;
  ui.setHandler(()=>response({id:'old',seconds:4,peaks:[.3,.7],url:'/audio/reference/old'}));
  await ui.context.upload({name:'old.wav',size:100});
  await ui.element('edit-audio').dispatch('click');
  assert.equal(ui.element('audio-dialog').open,true);
  ui.setHandler(()=>new Promise(resolve=>resolveUpload=resolve));
  const pending=ui.context.upload({name:'new.wav',size:100});
  assert.equal(ui.element('editor-wave-path').getAttribute('d'),'');
  assert.equal(ui.element('editor-player').getAttribute('src'),null);
  assert.equal(ui.element('reference-player').getAttribute('src'),null);
  assert.equal(ui.element('apply-crop').disabled,true);
  await ui.element('restore-audio').dispatch('click');
  await ui.element('apply-crop').dispatch('click');
  assert.match(ui.element('audio-edit-notice').textContent,/正在上传/);
  assert.ok(!ui.requests.some(r=>r.url==='/api/audio/crop'));
  resolveUpload(response({id:'new',seconds:2,peaks:[.1,.9],url:'/audio/reference/new'}));await pending;
  assert.equal(ui.element('audio-dialog').open,true);
  assert.equal(ui.element('apply-crop').disabled,false);
  assert.equal(ui.element('editor-player').src,'/audio/reference/new');
  assert.match(ui.element('audio-edit-notice').textContent,/已就绪/);
});

test('upload failure releases the file input but keeps crop disabled until a successful retry',async()=>{
  const ui=await setup();
  ui.setHandler(()=>response({error:'音频读取失败'},400));
  await ui.context.upload({name:'bad.wav',size:100});
  assert.equal(ui.element('audio-file').disabled,false);
  assert.equal(ui.element('crop-reference').disabled,true);
  assert.equal(ui.element('apply-crop').disabled,true);
  assert.equal(ui.element('dropzone').getAttribute('aria-busy'),'false');
  assert.match(ui.element('toast').textContent,/音频读取失败/);
  ui.setHandler(()=>response({id:'retry',seconds:2,peaks:[.5],url:'/audio/reference/retry'}));
  await ui.context.upload({name:'retry.wav',size:100});
  assert.equal(ui.element('crop-reference').disabled,false);
  assert.equal(ui.element('apply-crop').disabled,false);
  assert.equal(ui.element('reference-player').src,'/audio/reference/retry');
});

test('upload waits for the local session to be ready instead of sending an empty token',async()=>{
  let resolveState;
  const ui=await setup(()=>new Promise(resolve=>resolveState=resolve));
  assert.equal(ui.element('audio-file').disabled,true);
  await ui.context.upload({name:'early.wav',size:100});
  assert.ok(!ui.requests.some(r=>r.url==='/api/upload'));
  assert.match(ui.element('toast').textContent,/本地服务尚未就绪/);
  resolveState(response({api_version:3,build:'editor-variants-v1',token:'ready-token',engines:{index:{ready:true}},jobs:[]}));await flush();
  assert.equal(ui.element('audio-file').disabled,false);
  ui.setHandler(()=>response({id:'ready',seconds:2,url:'/audio/reference/ready'}));
  await ui.context.upload({name:'ready.wav',size:100});
  assert.equal(ui.requests.find(r=>r.url==='/api/upload').options.headers['X-Local-Token'],'ready-token');
});

function threeTakes(state='generating',recognized=false){
  return job(state,{id:'three',variant_count:3,current_variant:state==='ready'?3:2,generated:true,
    variants:Array.from({length:3},(_,i)=>({number:i+1,state:state==='ready'||i===0?'ready':'queued',
      generated:state==='ready'||i===0,preview:state==='ready'||i===0?'/audio/variant/three/'+i:null,
      download:'/download/variant/three/'+i,transcription_state:recognized?'ready':i===0||state==='ready'?'queued':'waiting',
      recognized_text:recognized?'第'+(i+1)+'份识别文字':''}))});
}

test('three independent result cards stay ordered and first audio is usable before later runs finish',async()=>{
  const ui=await setup();
  ui.context.render({...threeTakes(),preview:'/audio/final/three'});
  assert.equal(ui.element('variant-results').children.length,3);
  assert.equal(ui.element('single-playback').hidden,true);
  assert.equal(ui.element('export').hidden,true);
  assert.equal(ui.element('result-player').getAttribute('src'),null,'the hidden legacy player must not request unfinished batch audio');
  assert.equal(ui.element('variant-0-player').hidden,false);
  assert.equal(ui.element('variant-1-player').hidden,true);
  assert.equal(ui.element('variant-2-player').hidden,true);
  assert.equal(ui.element('variant-0-download').href,'/download/variant/three/0');
  assert.equal(ui.element('variant-0-download').hidden,false);
  assert.match(ui.element('variant-0-text').textContent,/可先试听或下载/);
  assert.match(ui.element('progress-text').textContent,/第 2\/3 次/);
  const player=ui.element('variant-0-player');player.currentTime=1.25;player.paused=false;
  const updated=threeTakes();updated.variants[0].transcription_state='ready';updated.variants[0].recognized_text='<不是目标文字>，也照常显示。';
  ui.context.render(updated);
  assert.equal(ui.element('variant-0-player'),player);
  assert.equal(player.currentTime,1.25);
  assert.equal(player.paused,false,'poll updates must not interrupt playback');
  assert.equal(ui.element('variant-0-text').textContent,'<不是目标文字>，也照常显示。');
  const headings=ui.element('variant-results').children.map(card=>card.children[0].children[0].textContent);
  assert.deepEqual(headings,['第 1 次','第 2 次','第 3 次']);
});

test('finished audio unlocks generation while polling continues until transcription is displayed',async()=>{
  const ui=await setup();let checks=0;
  ui.setHandler(()=>response(++checks===1?threeTakes('ready'):threeTakes('ready',true)));
  await ui.context.startPoll('three');
  assert.equal(ui.element('generate').disabled,false);
  assert.equal(ui.element('variant-2-download').hidden,false);
  assert.match(ui.element('result-badge').textContent,/识别文字中/);
  assert.ok([...ui.timeouts.values()].some(t=>t.ms===1000));
  await ui.fire(1000);
  for(let i=0;i<3;i++)assert.equal(ui.element(`variant-${i}-text`).textContent,`第${i+1}份识别文字`);
  assert.equal(ui.element('result-badge').textContent,'生成完成');
  assert.ok(![...ui.timeouts.values()].some(t=>t.ms===1000));
});

test('cancelled batch and failed transcription keep completed takes playable and downloadable',async()=>{
  const ui=await setup(),data=threeTakes('cancelled');
  data.variants[0].transcription_state='error';data.variants[0].transcription_error='识别模型缺失';
  for(const item of data.variants.slice(1)){item.state='cancelled';item.transcription_state='cancelled';}
  ui.context.render(data);
  assert.equal(ui.element('variant-0-player').hidden,false);
  assert.equal(ui.element('variant-0-download').hidden,false);
  assert.match(ui.element('variant-0-text').textContent,/识别模型缺失/);
  assert.equal(ui.element('variant-1-download').hidden,true);
  assert.equal(ui.element('generate').disabled,false);
});
