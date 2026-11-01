// Offline viewport checks using an isolated, headless browser; never opens user tabs.
const fs=require('node:fs'),path=require('node:path'),http=require('node:http');
const {spawn}=require('node:child_process'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),base=path.join(root,'.cache/ui-review');
fs.mkdirSync(base,{recursive:true});
const browserPath=process.env.LAYOUT_BROWSER||[
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
].find(file=>fs.existsSync(file));
if(!browserPath)throw new Error('Set LAYOUT_BROWSER to a Chromium executable.');
const wait=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const wav=Buffer.alloc(44+48000);wav.write('RIFF');wav.writeUInt32LE(wav.length-8,4);wav.write('WAVEfmt ',8);
wav.writeUInt32LE(16,16);wav.writeUInt16LE(1,20);wav.writeUInt16LE(1,22);wav.writeUInt32LE(24000,24);
wav.writeUInt32LE(48000,28);wav.writeUInt16LE(2,32);wav.writeUInt16LE(16,34);wav.write('data',36);wav.writeUInt32LE(48000,40);
const state={api_version:3,build:'editor-variants-v1',supports_shutdown:true,token:'layout-test',model:{state:'ready'},audio_processing:{ready:true,message:'音频处理已就绪'},engines:{index:{ready:true,status:'文件已就绪'}},jobs:[]};
const server=http.createServer((req,res)=>{
  const route=new URL(req.url,'http://localhost').pathname;
  const json=data=>{res.setHeader('Content-Type','application/json');res.end(JSON.stringify(data));};
  if(route==='/api/state')return json(state);
  if(route==='/api/upload'){req.resume();return json({id:'reference',seconds:1,peaks:Array.from({length:200},(_,i)=>Math.abs(Math.sin(i*.33))*.8+.1),short_reference:true,url:'/audio/reference/reference'});}
  if(route.startsWith('/audio/')){res.setHeader('Content-Type','audio/wav');return res.end(wav);}
  const file={'/':'index.html','/style.css':'style.css','/app.js':'app.js','/audio-editor.js':'audio-editor.js','/results.js':'results.js'}[route];
  if(!file){res.statusCode=404;return res.end();}
  res.setHeader('Content-Type',{'index.html':'text/html; charset=utf-8','style.css':'text/css','app.js':'text/javascript','audio-editor.js':'text/javascript','results.js':'text/javascript'}[file]);
  res.end(fs.readFileSync(path.join(root,'web',file)));
});
class Protocol{
  constructor(socket){this.socket=socket;this.next=0;this.pending=new Map();this.errors=[];
    socket.addEventListener('message',event=>{const data=JSON.parse(event.data);
      if(data.id){const pending=this.pending.get(data.id);if(pending){this.pending.delete(data.id);clearTimeout(pending.timer);data.error?pending.reject(new Error(JSON.stringify(data.error))):pending.resolve(data.result);}}
      if(data.method==='Runtime.exceptionThrown')this.errors.push(data.params.exceptionDetails);
    });
  }
  send(method,params={},sessionId){return new Promise((resolve,reject)=>{const id=++this.next;
    const timer=setTimeout(()=>{this.pending.delete(id);reject(new Error('Protocol timeout: '+method));},10000);
    this.pending.set(id,{resolve,reject,timer});this.socket.send(JSON.stringify({id,method,params,...(sessionId?{sessionId}:{})}));
  });}
}
(async()=>{
  let browser,protocol,session;
  try{
    await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
    const profile=fs.mkdtempSync(path.join(base,'browser-'));
    const endpoint=await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>reject(new Error('Headless browser did not start.')),10000);let output='';
      browser=spawn(browserPath,['--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
        '--disable-background-networking','--disable-component-update','--disable-extensions','--disable-sync',
        '--remote-debugging-address=127.0.0.1','--remote-debugging-port=0','--force-device-scale-factor=1',
        '--user-data-dir='+profile,'about:blank'],{windowsHide:true,stdio:['ignore','ignore','pipe']});
      browser.on('error',error=>{clearTimeout(timer);reject(error);});
      browser.stderr.on('data',data=>{output+=data.toString();const match=output.match(/DevTools listening on (ws:\S+)/);if(match){clearTimeout(timer);resolve(match[1]);}});
      browser.on('exit',code=>{clearTimeout(timer);reject(new Error('Headless browser exited: '+code+' '+output.slice(-500)));});
    });
    const socket=new WebSocket(endpoint);await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true});});
    protocol=new Protocol(socket);
    const target=await protocol.send('Target.createTarget',{url:'about:blank'});
    session=(await protocol.send('Target.attachToTarget',{targetId:target.targetId,flatten:true})).sessionId;
    const send=(method,params)=>protocol.send(method,params,session);
    const evaluate=async expression=>{const response=await send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(response.exceptionDetails)throw new Error(JSON.stringify(response.exceptionDetails));return response.result.value;};
    await send('Page.enable');await send('Runtime.enable');
    if(process.env.VOICE_PROFILE_LIVE==='1'){
      await send('Emulation.setDeviceMetricsOverride',{width:1366,height:768,deviceScaleFactor:1,mobile:false});
      await send('Page.navigate',{url:'http://127.0.0.1:7865'});
      let ready=false;for(let i=0;i<80;i++){try{ready=await evaluate(`typeof referenceEditor!=='undefined'&&state?.build==='editor-variants-v1'`);}catch{}if(ready)break;await wait(50);}
      assert.ok(ready,'Live UI did not initialize');
      assert.ok(await evaluate(`!busy`),'A generation task is running; do not profile now.');
      const files=fs.readdirSync(path.join(root,'data/uploads')).filter(name=>name.endsWith('.wav')).map(name=>({name,time:fs.statSync(path.join(root,'data/uploads',name)).mtimeMs})).sort((a,b)=>b.time-a.time);
      const bytes=fs.readFileSync(path.join(root,'data/uploads',files[0].name)).toString('base64');
      const uploadTime=await evaluate(`(async()=>{const bytes=Uint8Array.from(atob(${JSON.stringify(bytes)}),c=>c.charCodeAt(0)),begin=performance.now();await upload(new File([bytes],'profile.wav',{type:'audio/wav'}));return {ms:performance.now()-begin,duration:document.getElementById('selection-end').value};})()`);
      const cropTime=await evaluate(`(async()=>{const original=reference,begin=performance.now();const end=Number(document.getElementById('selection-end').value);document.getElementById('selection-start').value=.1;document.getElementById('selection-start').dispatchEvent(new Event('change'));document.getElementById('selection-end').value=Math.min(end,1.15);document.getElementById('selection-end').dispatchEvent(new Event('change'));document.getElementById('crop-reference').click();for(let i=0;i<100;i++){if(!audioBusy)break;await new Promise(r=>setTimeout(r,10));}return {ms:performance.now()-begin,changed:reference!==original,busy:audioBusy,message:document.getElementById('toast').textContent};})()`);
      assert.equal(cropTime.changed,true);assert.equal(cropTime.busy,false);
      const result={upload:uploadTime,crop:cropTime,browserErrors:protocol.errors};
      fs.writeFileSync(path.join(base,'crop-profile.json'),JSON.stringify(result,null,2));
      console.log(JSON.stringify(result));return;
    }
    const measure=async ids=>evaluate(`(()=>{const ids=${JSON.stringify(ids)};return {viewport:[innerWidth,innerHeight],scroll:[document.documentElement.scrollWidth,document.documentElement.scrollHeight],controls:ids.map(id=>{const el=document.getElementById(id),r=el.getBoundingClientRect();const center=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {id,x:r.x,y:r.y,w:r.width,h:r.height,hit:!!center&&(el===center||el.contains(center))}})}})()`);
    const check=(label,result)=>{
      const [width,height]=result.viewport;
      assert.ok(result.scroll[0]<=width&&result.scroll[1]<=height,label+': page overflow '+JSON.stringify(result));
      for(const item of result.controls)assert.ok(item.x>=0&&item.y>=0&&item.w>0&&item.h>0&&item.x+item.w<=width+1&&item.y+item.h<=height+1&&item.hit,label+': inaccessible '+JSON.stringify(item));
    };
    for(const [width,height] of [[1920,1080],[1440,900],[1366,768],[1280,720],[1024,600],[900,560],[390,844]]){
      await send('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:false});
      await send('Page.navigate',{url:'http://127.0.0.1:'+server.address().port});
      let ready=false;for(let i=0;i<60;i++){try{ready=await evaluate(`document.readyState==='complete'&&document.getElementById('index-status').classList.contains('ready')`);}catch{}if(ready)break;await wait(50);}
      assert.ok(ready,'UI did not initialize');
      const core=['menu-toggle','open-help','quit-studio','dropzone','target-text','language','generate'];
      check(`${width}x${height} empty`,await measure([...core,'preview-heading']));
      if(width===1366){const shot=await send('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(base,'studio-empty.png'),Buffer.from(shot.data,'base64'));}
      await evaluate(`(async()=>{await upload(new File([new Uint8Array(64)],'参考声音.wav',{type:'audio/wav'}));render({id:'test',state:'generating',generated:false,message:'正在生成',segments:[],progress:{percent:42,elapsed_seconds:18,eta_seconds:26}})})()`);
      check(`${width}x${height} generating`,await measure([...core,'reference-player','cancel','task-progress','export']));
      await evaluate(`render({id:'test',state:'ready',generated:true,message:'生成完成，可直接试听、导出。内容、读音和情感请自行核查。',preview:'/audio/final/test',download:'/download/test',segments:Array.from({length:50},(_,i)=>({text:'这是第'+(i+1)+'段生成的内容。',generated:true,preview:'/audio/segment/test/'+i})),progress:{percent:100,elapsed_seconds:28}})`);
      check(`${width}x${height} ready`,await measure([...core,'reference-wave','reference-player','result-player','export','segment-toggle','report-link','log-link','crop-reference','preview-reference','edit-audio','open-separation']));
      const audioLayout=await evaluate(`(()=>{const box=id=>document.getElementById(id).getBoundingClientRect(),reference=document.querySelector('.reference-section').getBoundingClientRect(),text=document.querySelector('.text-section').getBoundingClientRect(),editor=document.querySelector('.editor').getBoundingClientRect(),output=document.querySelector('.result-card').getBoundingClientRect();return {wave:box('reference-wave').height,upload:box('dropzone').height,referenceBottom:reference.bottom,textTop:text.top,textHeight:box('target-text').height,editorTop:editor.top,editorBottom:editor.bottom,editorRight:editor.right,outputLeft:output.left,outputTop:output.top}})()`);
      assert.ok(audioLayout.referenceBottom<=audioLayout.textTop,`${width}x${height}: audio overlaps text input`);
      if(width>760){
        assert.ok(audioLayout.editorRight<=audioLayout.outputLeft,`${width}x${height}: preview must stay to the right of the editor`);
        assert.ok(Math.abs(audioLayout.editorTop-audioLayout.outputTop)<1,`${width}x${height}: panels should align at the top`);
      }else assert.ok(audioLayout.editorBottom<=audioLayout.outputTop,`${width}x${height}: narrow-screen panels overlap`);
      assert.ok(audioLayout.textHeight>=48,`${width}x${height}: text input is too short`);
      if(width===1366){const shot=await send('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(base,'studio-ready.png'),Buffer.from(shot.data,'base64'));}
      const before=await evaluate(`document.getElementById('workspace').getBoundingClientRect().width`);
      await evaluate(`setSidebar(true)`);
      assert.equal(await evaluate(`document.getElementById('workspace').getBoundingClientRect().width`),before,'drawer should not resize workspace');
      check(`${width}x${height} drawer`,await measure(['sidebar-close','nav-workspace','sidebar-help']));
      await evaluate(`setSidebar(false);document.getElementById('segment-dialog').showModal()`);
      check(`${width}x${height} segments`,await measure(['segment-close']));
      assert.ok(await evaluate(`document.getElementById('segments').scrollHeight>document.getElementById('segments').clientHeight`),'long segments should scroll inside dialog');
      await evaluate(`document.getElementById('segment-dialog').close();document.getElementById('help-dialog').showModal()`);
      check(`${width}x${height} help`,await measure(['help-close']));
      await evaluate(`document.getElementById('help-dialog').close()`);
      await evaluate(`document.getElementById('edit-audio').click()`);
      check(`${width}x${height} audio editor`,await measure(['audio-close']));
      assert.ok(await evaluate(`document.getElementById('audio-dialog').open`));
      assert.ok(await evaluate(`!document.getElementById('audio-dialog').contains(document.getElementById('separate-start'))`),'crop dialog must not contain separation controls');
      await evaluate(`document.getElementById('audio-dialog').close()`);
      await evaluate(`document.getElementById('open-separation').click()`);
      assert.ok(await evaluate(`document.getElementById('separation-dialog').open&&!document.getElementById('audio-dialog').open`));
      check(`${width}x${height} vocal separation`,await measure(['separation-close','separation-mode','separate-start']));
      assert.ok(await evaluate(`document.getElementById('separation-dialog').contains(document.getElementById('separate-start'))`));
      await evaluate(`document.getElementById('separation-dialog').close()`);
      await evaluate(`(()=>{const variants=Array.from({length:3},(_,i)=>({number:i+1,state:'ready',generated:true,preview:'/audio/variant/batch/'+i,download:'/download/variant/batch/'+i,transcription_state:i===0?'queued':'waiting',recognized_text:''}));render({id:'batch',state:'generating',variant_count:3,current_variant:2,variants:[variants[0],{number:2,state:'generating',generated:false},{number:3,state:'queued',generated:false}],text:'你好，欢迎来到音色工作室。',generated:true,segments:[],message:'第 2/3 次：合成语音',progress:{percent:45,elapsed_seconds:8,eta_seconds:3}})})()`);
      check(`${width}x${height} early take`,await measure(['variant-0-player','variant-0-download','cancel','generate']));
      await evaluate(`(()=>{const variants=Array.from({length:3},(_,i)=>({number:i+1,state:'ready',generated:true,preview:'/audio/variant/batch/'+i,download:'/download/variant/batch/'+i,transcription_state:'ready',recognized_text:'你好，这是第'+(i+1)+'次生成的语音。你可以分别试听、下载，自行校对文字。'}));render({id:'batch',state:'ready',variant_count:3,current_variant:3,variants,text:'你好，欢迎来到音色工作室。',generated:true,segments:[],message:'三次生成已结束，3 份音频可试听、下载。识别文字仅供自行校对。',progress:{percent:100,elapsed_seconds:12}})})()`);
      const results=Array.from({length:3},(_,i)=>[`variant-${i}-player`,`variant-${i}-download`,`variant-${i}-text`]);
      if(width>760)check(`${width}x${height} three takes`,await measure([...core,...results.flat(),'report-link','log-link']));
      else for(let i=0;i<3;i++){
        await evaluate(`document.getElementById('variant-${i}-player').parentElement.scrollIntoView({block:'nearest'})`);
        check(`${width}x${height} take ${i+1}`,await measure(results[i]));
      }
      if(width===1366){const shot=await send('Page.captureScreenshot',{format:'png'});fs.writeFileSync(path.join(base,'studio-variants.png'),Buffer.from(shot.data,'base64'));}
      console.log(`${width}x${height}: empty / generating / ready / drawer / dialogs OK; upload ${audioLayout.upload}px, waveform ${audioLayout.wave}px`);
    }
    assert.deepEqual(protocol.errors,[],'Browser script errors');
    console.log('Screenshots: '+path.join(base,'studio-ready.png'));
  }finally{
    if(protocol){await protocol.send('Browser.close').catch(()=>{});protocol.socket.close();}
    if(browser&&browser.exitCode===null)browser.kill();
    await new Promise(resolve=>server.close(resolve));
  }
})().catch(error=>{console.error(error);process.exitCode=1;});
