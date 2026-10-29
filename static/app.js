'use strict';
const $=id=>document.getElementById(id),esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stamp=ms=>{let s=Math.max(0,Math.floor(ms/1000));return `${Math.floor(s/60).toString().padStart(2,'0')}:${(s%60).toString().padStart(2,'0')}`};
let p=null,edit=null,assets=new Map(),activeGroup=null,marked=new Set(),lastMark=-1,undoStack=[],saveTimer=null,saveChain=Promise.resolve(),preview=null,seenJobs=new Map(),currentSentence=null;
let visible=[],transcriptExportBusy=false,folderPickerBusy=false,shuttingDown=false,jobsPollTimer;
let navigation=0,editNavigation=0,editLoading=false,videoAsset=null,cancelVideoWait=null,pendingSaves=0;
const dirty=new Set(),assetMarks=new Map();
async function api(path,data,method){const options={method:method||(data===undefined?'GET':'POST'),headers:{'X-Longvideo-Client':'1'}};if(data!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(data)}const r=await fetch('/api'+path,options);const j=await r.json();if(!r.ok)throw Error(typeof j.detail==='string'?j.detail:'请求失败');return j}
function toast(message){$('toast').textContent=message;$('toast').classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').classList.remove('show'),6500)}
const attempt=fn=>async(...args)=>{try{await fn(...args)}catch(e){toast(e.message)}};
function group(){return edit?.groups.find(g=>g.id===activeGroup)}
function checkpoint(){undoStack.push(JSON.stringify(edit.groups));if(undoStack.length>40)undoStack.shift()}
function saveSoon(){if(!edit)return;dirty.add(edit.id);clearTimeout(saveTimer);$('saveState').textContent='待保存…';saveTimer=setTimeout(()=>flushSave().catch(()=>{}),350)}
function flushSave(){
  clearTimeout(saveTimer);if(!edit||!dirty.has(edit.id))return saveChain;
  const reference=edit,groups=structuredClone(edit.groups);dirty.delete(reference.id);pendingSaves++;
  saveChain=saveChain.catch(()=>{}).then(async()=>{
    const saved=await api(`/edits/${reference.id}`,{groups,revision:reference.revision},'PUT');reference.revision=saved.revision;
  }).catch(e=>{dirty.add(reference.id);$('saveState').textContent='保存失败，请刷新核对';toast(e.message);throw e}).finally(()=>{pendingSaves--;if(!pendingSaves&&!dirty.size)$('saveState').textContent='已自动保存'});
  return saveChain;
}
function showOutputPaths(info){for(const [id,path] of [['exportPath',info.exports_path],['transcriptExportPath',info.transcripts_path]]){$(id).textContent=path||'加载中…';$(id).title=path||''}}
async function loadInfo(fillPath=true){const x=await api('/info');$('shutdownBtn').disabled=!x.background_running;$('runtimeMode').textContent=x.background_running?'后台运行':'调试模式';$('keyStatus').textContent=x.credential_configured?`本机后端已配置密钥；${x.credential_storage}。可检查连接。`:'尚未配置密钥。';if(fillPath){showOutputPaths(x);$('exportPathStatus').textContent='';$('exportPathStatus').classList.remove('error')}return x}
function asset(id){return assets.get(id)}
function ratio(media){if(!media)return '';let w=media.display_width||media.width,h=media.display_height||media.height;const gcd=(a,b)=>b?gcd(b,a%b):a,n=gcd(w,h);return `${w/n}:${h/n}`}
function fpsNumber(profile){const [a,b]=String(profile?.fps_fraction||25).split('/').map(Number);return a/(b||1)}
function outputText(profile){return profile?`${profile.width} × ${profile.height} · ${Number(fpsNumber(profile).toFixed(2))} fps`:'首次加入素材后确定输出规格'}
function initialOutput(media){return {width:Math.max(2,Math.floor((media.display_width||media.width)/2)*2),height:Math.max(2,Math.floor((media.display_height||media.height)/2)*2),fps_fraction:media.fps_fraction||String(media.fps),fit:'pad'}}
async function listProjects(selected){const list=await api('/projects');assets=new Map(list.map(x=>[x.id,x]));renderAssetOptions(selected);return list}
function renderAssetOptions(selected=p?.id){const list=(edit?.asset_ids||[]).map(id=>asset(id)).filter(Boolean);$('projectSelect').innerHTML=list.length?list.map(x=>`<option value="${esc(x.id)}">${esc(x.name)} · ${ratio(x.media)}</option>`).join(''):'<option value="">从素材库加入或导入素材</option>';$('projectSelect').disabled=!list.length;if(selected&&list.some(x=>x.id===selected))$('projectSelect').value=selected;return list}
async function listEdits(selected=edit?.id){const list=await api('/edits');$('editSelect').innerHTML=list.map(x=>`<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('');if(selected)$('editSelect').value=selected;return list}
async function loadEdit(id){
  const token=++editNavigation;editLoading=true;document.querySelector('main').inert=true;
  for(const id of ['projectSelect','importBtn','manageAssets','newEdit','renameEdit'])$(id).disabled=true;
  preview=null;$('video').pause();navigation++;cancelVideoWait?.();
  try{
  const loaded=await api('/edits/'+id);if(token!==editNavigation)return;edit=loaded;activeGroup=edit.groups[0]?.id;undoStack=[];assetMarks.clear();marked=new Set();
  const list=renderAssetOptions();renderGroups();
  if(list.length)await loadProject(list.some(x=>x.id===p?.id)?p.id:list[0].id);else clearProject();
  $('saveState').textContent='已读取工程';$('editSelect').value=id;
  }finally{if(token===editNavigation){editLoading=false;document.querySelector('main').inert=false;for(const id of ['importBtn','manageAssets','newEdit','renameEdit'])$(id).disabled=false;$('projectSelect').disabled=!edit?.asset_ids.length;if(edit)$('editSelect').value=edit.id}}
}
function clearProject(){navigation++;cancelVideoWait?.();p=null;videoAsset=null;marked=new Set();preview=null;currentSentence=null;$('video').pause();$('video').removeAttribute('src');$('video').load();$('sourceMeta').textContent='';$('playTime').textContent='00:00';$('nowPlaying').textContent='从素材库加入已有素材，或导入新视频';renderTranscript();renderGroups()}
function waitMetadata(token){const v=$('video');if(v.readyState>=1)return Promise.resolve(true);return new Promise(resolve=>{let timer;const finish=ok=>{clearTimeout(timer);v.removeEventListener('loadedmetadata',ready);v.removeEventListener('error',failed);if(cancelVideoWait===cancel)cancelVideoWait=null;resolve(ok&&token===navigation)},ready=()=>finish(true),failed=()=>finish(false),cancel=()=>finish(false);cancelVideoWait=cancel;v.addEventListener('loadedmetadata',ready,{once:true});v.addEventListener('error',failed,{once:true});timer=setTimeout(failed,12000)})}
async function loadProject(id,{keepPreview=false,seekTo=null,play=false}={}){
  if(!id)return;if(!edit?.asset_ids.includes(id))throw Error('该素材不在当前工程中');
  if(!keepPreview)preview=null;const token=++navigation;cancelVideoWait?.();
  if(p)assetMarks.set(p.id,marked);const loaded=p?.id===id?p:await api('/projects/'+id);if(token!==navigation)return false;
  p=loaded;marked=assetMarks.get(id)||new Set();assetMarks.set(id,marked);lastMark=-1;currentSentence=null;
  clearRangeError();$('selectionRange').value='';$('search').value='';$('projectSelect').value=id;
  const v=$('video');if(videoAsset!==id){v.pause();videoAsset=id;v.src=`/api/projects/${encodeURIComponent(id)}/media`;v.load()}
  v.playbackRate=Number($('speed').value);
  $('sourceMeta').textContent=`${p.media.display_width||p.media.width}×${p.media.display_height||p.media.height} · ${p.media.fps.toFixed(2)} fps · ${stamp(p.media.duration_ms)}`;
  $('nowPlaying').textContent=p.name;renderTranscript();renderGroups();
  if(seekTo!==null){if(!await waitMetadata(token)){if(token===navigation)throw Error('原片无法预览，请检查文件路径或浏览器支持的编码');return false}if(token!==navigation)return false;v.currentTime=Math.max(0,seekTo/1000);if(play)await v.play()}
  return token===navigation;
}
function sentenceNumber(sentence){if(Number.isInteger(sentence.source_index)&&sentence.source_index>=0)return sentence.source_index+1;const index=p?.sentences.findIndex(s=>s.id===sentence.id)??-1;return index>=0?index+1:null}
function sentenceLabel(sentence){return sentence.source_number_label??sentence.source_number??sentenceNumber(sentence)??'—'}
function rangeNumbers(value,maximum){const parts=value.trim().replace(/[０-９]/g,c=>String(c.charCodeAt(0)-0xff10)).replace(/[~～—–至]/g,'-').replace(/\s*-\s*/g,'-').split(/[,，、;；\s]+/).filter(Boolean);if(!parts.length)throw Error('请输入句子序号，例如：1-5, 8, 12-15。');const numbers=new Set();for(const part of parts){const m=part.match(/^(\d+)(?:-(\d+))?$/);if(!m)throw Error('序号格式不正确，请用逗号分隔，并用短横线连接范围。');const a=Number(m[1]),b=Number(m[2]??m[1]);if(a<1||b>maximum)throw Error(`请输入 1–${maximum} 之间的句子序号。`);if(a>b)throw Error('范围的起始序号不能大于结束序号。');for(let n=a;n<=b;n++)numbers.add(n)}return numbers}
function clearRangeError(){$('selectionRangeError').hidden=true;$('selectionRangeError').textContent='';$('selectionRange').removeAttribute('aria-invalid')}
function renderTranscript(){
  const query=$('search').value.trim().toLowerCase();visible=(p?.sentences||[]).filter(s=>!query||s.text.toLowerCase().includes(query));$('sentenceCount').textContent=p?.sentences.length||0;
  if(!visible.length){let empty='从素材库加入已有素材，或导入视频自动生成文稿。';if(p?.sentences.length)empty='没有匹配的句子。';else if(p?.asr_status==='等待密钥')empty='请在设置中保存百炼 API Key，随后将自动转写。';else if(p?.asr_status==='转写中')empty='音频正在自动转写，完成后会显示文稿。';else if(p&&['转写未启动','转写失败','转写已取消'].includes(p.asr_status))empty=esc(p.asr_error||'自动转写未完成，请查看后台任务。');$('transcript').innerHTML='<div class="empty large">'+empty+'</div>';selectionState();return}
  $('transcript').innerHTML=visible.map(s=>`<div class="sentence ${marked.has(s.id)?'selected':''} ${currentSentence===s.id?'playing':''}" data-id="${esc(s.id)}"><input type="checkbox" aria-label="选择句子 ${sentenceNumber(s)}" ${marked.has(s.id)?'checked':''}><div class="sentence-meta"><span class="sentence-number">${sentenceNumber(s)}.</span><span class="stamp">${stamp(s.start_ms)}</span></div><span class="text">${esc(s.text)}</span><button class="edit" title="修正共享文稿文字" aria-label="修正句子 ${sentenceNumber(s)}">✎</button></div>`).join('');
  $('transcript').querySelectorAll('.sentence').forEach((row,i)=>{
    row.addEventListener('click',attempt(async event=>{if(event.target.matches('input')||event.target.closest('.edit'))return;preview=null;await loadProject(p.id,{seekTo:visible[i].start_ms,play:true})}));
    row.querySelector('input').addEventListener('click',event=>{event.stopPropagation();const checked=event.target.checked;if(event.shiftKey&&lastMark>=0){for(let n=Math.min(i,lastMark);n<=Math.max(i,lastMark);n++){if(checked)marked.add(visible[n].id);else marked.delete(visible[n].id)}}else if(checked)marked.add(visible[i].id);else marked.delete(visible[i].id);lastMark=i;renderTranscript()});
    row.querySelector('.edit').onclick=attempt(async event=>{event.stopPropagation();const s=visible[i],text=prompt('修正共享文稿文字（各工程引用同步，原声和切点保留）',s.text);if(text===null||!text.trim())return;await flushSave();await api(`/projects/${p.id}/sentence/${encodeURIComponent(s.id)}`,{text:text.trim()},'PUT');s.text=text.trim();edit=await api('/edits/'+edit.id);renderTranscript();renderGroups()});
  });selectionState();
}
function selectionState(){const count=p?.sentences.length||0;$('exportTranscript').disabled=transcriptExportBusy||!count;const outside=marked.size-visible.filter(s=>marked.has(s.id)).length;$('selectionCount').textContent=marked.size?`当前素材已选 ${marked.size} 句${outside?' · 筛选外 '+outside+' 句':''}`:'未选择句子';$('addSelected').disabled=!edit||!p||!marked.size;$('clearSelection').disabled=!marked.size;const maximum=(p?.sentences||[]).reduce((n,s)=>Math.max(n,sentenceNumber(s)||0),0);$('rangeBounds').textContent=maximum?`1–${maximum}`:'';$('bulkSelectionBtn').disabled=!maximum;$('applyRange').disabled=!maximum}
function selectedSpans(g){const spans=[];if(!g)return spans;for(const item of g.items){const m=asset(item.asset_id)?.media;if(!m)continue;const start=Math.max(0,item.start_ms-80),end=Math.min(m.duration_ms,item.end_ms+120),prev=spans.at(-1),last=prev?.items.at(-1);if(prev&&item.asset_id===prev.asset_id&&item.version===last.version&&item.source_index===last.source_index+1&&item.start_ms>=last.start_ms&&!item.edited&&!last.edited){prev.end_ms=Math.max(end,prev.end_ms);prev.items.push(item)}else spans.push({asset_id:item.asset_id,start_ms:start,end_ms:end,items:[item]})}for(const span of spans){const m=asset(span.asset_id).media;span.start_ms=Math.floor(span.start_ms/1000*m.fps)/m.fps*1000;span.end_ms=Math.min(m.duration_ms,Math.ceil(span.end_ms/1000*m.fps)/m.fps*1000)}return spans}
function renderGroups(){
  const groups=edit?.groups||[];$('groupTabs').innerHTML=groups.map(g=>`<button class="${g.id===activeGroup?'active':''}" data-id="${esc(g.id)}" title="${esc(g.name)}">${esc(g.name)} · ${g.items.length}</button>`).join('');$('groupTabs').querySelectorAll('button').forEach(b=>b.onclick=()=>{activeGroup=b.dataset.id;preview=null;renderGroups()});
  const g=group();$('groupName').value=g?.name||'';$('groupName').disabled=!g;$('groupDuration').textContent=stamp(selectedSpans(g).reduce((n,s)=>n+s.end_ms-s.start_ms,0));$('groupSources').textContent=g?.items.length?`${g.items.length}句 · ${new Set(g.items.map(i=>i.asset_id)).size}条素材`:'';
  $('groupItems').innerHTML=g?.items.length?g.items.map((item,i)=>`<div class="item" draggable="true" data-item="${esc(item.item_id)}"><span class="handle" title="拖动排序">⋮⋮</span><div><div class="item-origin"><span class="source-label" title="${esc(asset(item.asset_id)?.name||'来源素材不可用')}">${esc(asset(item.asset_id)?.name||'来源素材不可用')}</span><span class="sentence-number">${esc(sentenceLabel(item))}.</span><span class="source-format">${ratio(asset(item.asset_id)?.media)}</span></div><div class="item-heading"><div class="item-text">${esc(item.text)}</div></div><small>${stamp(item.start_ms)}—${stamp(item.end_ms)}<span class="trim">调整边界</span><span class="item-moves"><button class="move-up" ${i?'':'disabled'} aria-label="上移片段 ${i+1}">↑</button><button class="move-down" ${i<g.items.length-1?'':'disabled'} aria-label="下移片段 ${i+1}">↓</button></span></small></div><button class="remove" aria-label="移除片段 ${i+1}">×</button></div>`).join(''):'<div class="empty">从任意素材选句，再点击「加入当前组」。</div>';
  function move(i,target){if(target<0||target>=g.items.length)return;checkpoint();const [item]=g.items.splice(i,1);g.items.splice(target,0,item);preview=null;saveSoon();renderGroups()}
  $('groupItems').querySelectorAll('.item').forEach((row,i)=>{
    row.ondragstart=event=>{event.dataTransfer.setData('text/plain',g.items[i].item_id);event.dataTransfer.effectAllowed='move'};row.ondragover=event=>{event.preventDefault();row.classList.add('drag-over')};row.ondragleave=()=>row.classList.remove('drag-over');row.ondrop=event=>{event.preventDefault();row.classList.remove('drag-over');const from=g.items.findIndex(x=>x.item_id===event.dataTransfer.getData('text/plain'));if(from>=0&&from!==i)move(from,i)};
    row.querySelector('.move-up').onclick=()=>move(i,i-1);row.querySelector('.move-down').onclick=()=>move(i,i+1);
    row.querySelector('.remove').onclick=()=>{checkpoint();g.items.splice(i,1);preview=null;saveSoon();renderGroups()};
    row.querySelector('.item-text').onclick=attempt(async()=>{preview=null;await loadProject(g.items[i].asset_id,{seekTo:g.items[i].start_ms,play:true})});
    row.querySelector('.trim').onclick=()=>{const item=g.items[i],a=prompt('该素材中的开始时间，单位秒',String(item.start_ms/1000));if(a===null)return;const b=prompt('该素材中的结束时间，单位秒',String(item.end_ms/1000));if(b===null)return;const start=Number(a)*1000,end=Number(b)*1000;if(!Number.isFinite(start)||!Number.isFinite(end)||start<0||start>=end||end>(asset(item.asset_id)?.media.duration_ms||0)){toast('起止时间超出来源素材范围');return}checkpoint();item.start_ms=Math.round(start);item.end_ms=Math.round(end);item.edited=true;preview=null;saveSoon();renderGroups()};
  });
  $('outputSummary').textContent=outputText(g?.output);$('outputSettings').disabled=!g;$('exportPlan').innerHTML='';$('undo').disabled=!undoStack.length;for(const id of ['previewGroup','exportGroup'])$(id).disabled=!g?.items.length;$('newGroup').disabled=!edit;$('deleteGroup').disabled=!g;
}
async function showPlan(ids){await flushSave();const list=await api(`/edits/${edit.id}/export-plan`,{group_ids:ids});$('exportPlan').innerHTML=list.length?list.map(x=>`<div>${esc(x.file_name)} · ${stamp(x.duration_ms)} · ${x.sentence_count}句 · ${x.source_count}条素材<br>${outputText(x.output)}</div>`).join(''):'没有非空分组可导出。';return list}
async function exportGroups(all){if(!edit)return;const ids=all?null:[activeGroup];const list=await showPlan(ids);if(!list.length)return;await api(`/edits/${edit.id}/export`,{group_ids:ids});toast('已加入后台导出队列，可继续整理分组');pollJobs()}

const jobLabels={queued:'等待中',running:'处理中',succeeded:'已完成',failed:'失败',canceled:'已取消',interrupted:'已中断'};
let jobsSignature='';
async function pollJobs(){
  if(shuttingDown)return [];const jobs=await api('/jobs');if(shuttingDown)return [];const active=jobs.filter(j=>['queued','running'].includes(j.status)).length;
  $('activeJobCount').hidden=!active;$('activeJobCount').textContent=active;
  const signature=JSON.stringify(jobs);
  if(signature!==jobsSignature){
    jobsSignature=signature;
    $('jobs').innerHTML=jobs.length?jobs.map(j=>`<div class="job"><div class="job-heading"><b>${j.kind==='asr'?'音频转写':'视频导出'}</b><span class="job-state ${j.status==='failed'?'error':''}">${esc(jobLabels[j.status]||j.status)}</span>${['queued','running'].includes(j.status)?`<button data-cancel="${esc(j.id)}" class="quiet">取消</button>`:''}</div><div class="job-message ${j.status==='failed'?'error':''}">${esc(j.message)}</div><progress value="${j.progress||0}" max="1" aria-label="任务进度"></progress>${j.timing?`<small>${j.timing.sentences}句 · ${j.timing.words}字词 · ${j.timing.cached?'使用缓存':`提取${j.timing.extract_seconds.toFixed(1)}秒，云端${j.timing.cloud_seconds.toFixed(1)}秒`}</small>`:''}</div>`).join(''):'<div class="empty">暂无任务</div>';
    $('jobs').querySelectorAll('[data-cancel]').forEach(b=>b.onclick=attempt(async()=>{b.disabled=true;try{await api('/jobs/'+b.dataset.cancel+'/cancel',{});toast('已请求取消');await pollJobs()}finally{b.disabled=false}}));
  }
  for(const j of jobs){
    const old=seenJobs.get(j.id);seenJobs.set(j.id,j.status);
    if(j.status==='succeeded'&&((old&&old!=='succeeded')||(old===undefined&&j.kind==='asr'&&p?.id===j.project_id&&!p.sentences.length))){
      if(j.kind==='asr'&&p?.id===j.project_id){await flushSave();const updated=await api('/projects/'+p.id);p.sentences=updated.sentences;p.asr_version=updated.asr_version;p.recognized_range=updated.recognized_range;p.asr_status=updated.asr_status;marked.clear();renderTranscript();toast('转写完成，可开始选句')}
      if(j.kind==='export'){await loadExports();toast('短片已导出')}
    }else if(['failed','canceled'].includes(j.status)&&old&&old!==j.status&&j.kind==='asr'&&p?.id===j.project_id&&!p.sentences.length){p.asr_status=j.status==='failed'?'转写失败':'转写已取消';p.asr_error=j.message;renderTranscript();toast(j.message)}
  }
  return jobs;
}
async function loadExports(){const list=await api('/exports');$('exports').innerHTML=list.length?list.map(x=>`<a href="/api/exports/${encodeURIComponent(x.file_name)}" target="_blank">${esc(x.file_name)} · ${(x.bytes/1048576).toFixed(1)}MB</a>`).join(''):'<div class="empty">导出后可在这里打开视频。</div>'}
$('importBtn').onclick=attempt(async()=>{
  if(editLoading)return;
  const button=$('importBtn');button.disabled=true;button.textContent='选择素材…';
  try{
    await flushSave();
    if(!edit)throw Error('请先创建剪辑工程');
    const eid=edit.id,list=await api('/import/pick',{project_id:p?.id,edit_id:eid});
    if(!list.length)return;
    if(edit.id!==eid){await listProjects();toast('素材已导入原先选择的工程');return}
    edit=await api('/edits/'+eid);await listProjects(list[0].id);await loadProject(list[0].id);
    const states=list.map(x=>x.auto_transcription||{}),queued=states.filter(x=>['queued','running'].includes(x.status)),failed=states.find(x=>x.status==='failed');
    await pollJobs();
    if(states.some(x=>x.status==='needs_key')){await loadInfo();$('settings').showModal();toast('导入完成，请保存百炼 API Key，随后自动转写')}
    else if(failed)toast('导入完成，自动转写未启动：'+failed.message);
    else toast(queued.length?`已导入 ${list.length} 条素材，${queued.length} 条自动转写中`:`已载入 ${list.length} 条素材，已有文稿已保留`);
  }finally{button.disabled=false;button.textContent='导入素材'}
});
$('projectSelect').onchange=attempt(async e=>{if(editLoading)return;await flushSave();await loadProject(e.target.value)});
$('editSelect').onchange=attempt(async e=>{await flushSave();await loadEdit(e.target.value)});
$('newEdit').onclick=attempt(async()=>{const name=prompt('新建剪辑工程名称');if(name===null||!name.trim())return;await flushSave();const created=await api('/edits',{name:name.trim()});await listEdits(created.id);await loadEdit(created.id)});
$('renameEdit').onclick=attempt(async()=>{if(!edit)return;const name=prompt('修改剪辑工程名称',edit.name);if(name===null||!name.trim())return;await flushSave();edit=await api('/edits/'+edit.id,{name:name.trim(),revision:edit.revision},'PUT');await listEdits(edit.id)});
$('bulkSelectionBtn').onclick=()=>{
  const open=$('bulkSelectionPanel').hidden;
  $('bulkSelectionPanel').hidden=!open;
  $('bulkSelectionBtn').setAttribute('aria-expanded',String(open));
  if(open)$('selectionRange').focus();
};
$('selectionRange').oninput=clearRangeError;
$('bulkSelectionForm').onsubmit=e=>{
  e.preventDefault();if(!p?.sentences.length)return;
  clearRangeError();
  try{
    const maximum=p.sentences.reduce((n,s)=>Math.max(n,sentenceNumber(s)||0),0);
    const numbers=rangeNumbers($('selectionRange').value,maximum);
    let added=0;
    for(const s of p.sentences){
      if(numbers.has(sentenceNumber(s))&&!marked.has(s.id)){marked.add(s.id);added++}
    }
    lastMark=-1;renderTranscript();
    toast(added?`已新增选择 ${added} 句`:'这些句子已在选择中');
  }catch(error){
    $('selectionRangeError').textContent=error.message;
    $('selectionRangeError').hidden=false;
    $('selectionRange').setAttribute('aria-invalid','true');
    $('selectionRange').focus();
  }
};
$('search').oninput=()=>{lastMark=-1;renderTranscript()};$('clearSelection').onclick=()=>{marked.clear();lastMark=-1;clearRangeError();renderTranscript()};
$('addSelected').onclick=()=>{if(!p||!edit)return;checkpoint();if(!group()){edit.groups.push({id:crypto.randomUUID(),name:'切片001',items:[],output:null});activeGroup=edit.groups.at(-1).id}const g=group(),existing=new Set(g.items.map(x=>x.asset_id+':'+x.id));let n=0;for(const s of p.sentences){if(marked.has(s.id)&&!existing.has(p.id+':'+s.id)){const item={...structuredClone(s),asset_id:p.id,source_number:sentenceNumber(s),item_id:crypto.randomUUID(),edited:false};delete item.words;g.items.push(item);n++}}if(!g.output&&g.items.length)g.output=initialOutput(asset(g.items[0].asset_id).media);marked.clear();saveSoon();renderTranscript();renderGroups();toast(`已加入 ${n} 句`)};
$('newGroup').onclick=()=>{if(!edit)return;checkpoint();const g={id:crypto.randomUUID(),name:`切片${String(edit.groups.length+1).padStart(3,'0')}`,items:[],output:null};edit.groups.push(g);activeGroup=g.id;saveSoon();renderGroups()};
$('groupName').oninput=e=>{const g=group();if(!g)return;g.name=e.target.value||'未命名短片';saveSoon();const button=$('groupTabs').querySelector(`[data-id="${CSS.escape(g.id)}"]`);if(button)button.textContent=g.name+' · '+g.items.length;$('exportPlan').innerHTML=''};
$('deleteGroup').onclick=()=>{const g=group();if(!g)return;if(!confirm(`删除当前工程中的分组「${g.name}」？原素材保留。`))return;checkpoint();edit.groups=edit.groups.filter(x=>x.id!==g.id);activeGroup=edit.groups[0]?.id;preview=null;saveSoon();renderGroups()};
$('undo').onclick=()=>{if(!undoStack.length)return;edit.groups=JSON.parse(undoStack.pop());if(!edit.groups.some(g=>g.id===activeGroup))activeGroup=edit.groups[0]?.id;preview=null;saveSoon();renderGroups()};
$('previewGroup').onclick=attempt(async()=>{const g=group();if(!g?.items.length)return;await flushSave();const list=await api(`/edits/${edit.id}/preview-plan`,{group_ids:[g.id]});const spans=list[0]?.spans||[];if(!spans.length)return;const current={spans,index:0,transitioning:true};preview=current;await playPreviewSpan(current)});
async function playPreviewSpan(current){if(preview!==current)return;const span=current.spans[current.index];try{const loaded=await loadProject(span.asset_id,{keepPreview:true,seekTo:span.start_ms,play:true});if(loaded&&preview===current){current.transitioning=false;$('nowPlaying').textContent=`组预览 · ${current.index+1}/${current.spans.length} · ${asset(span.asset_id)?.name}`}}catch(error){if(preview===current)preview=null;toast(error.message)}}
function advancePreview(){const current=preview;if(!current||current.transitioning)return;current.transitioning=true;$('video').pause();current.index++;if(current.index>=current.spans.length){preview=null;$('nowPlaying').textContent='组预览结束';return}playPreviewSpan(current)}
$('stopPreview').onclick=()=>{preview=null;$('video').pause()};$('speed').onchange=e=>$('video').playbackRate=Number(e.target.value);
$('video').ontimeupdate=()=>{const time=$('video').currentTime*1000;$('playTime').textContent=stamp(time);if(preview&&!preview.transitioning&&videoAsset===preview.spans[preview.index].asset_id&&time>=preview.spans[preview.index].end_ms-35)advancePreview();if(!p)return;const s=p.sentences.find(x=>x.start_ms<=time&&time<=x.end_ms);if(s?.id!==currentSentence){$('transcript').querySelector('.playing')?.classList.remove('playing');if(s){$('transcript').querySelector(`[data-id="${CSS.escape(s.id)}"]`)?.classList.add('playing');if(!preview)$('nowPlaying').textContent=s.text}currentSentence=s?.id}};
$('video').onended=advancePreview;
$('exportGroup').onclick=attempt(()=>exportGroups(false));$('exportAll').onclick=attempt(()=>exportGroups(true));$('planExports').onclick=attempt(()=>showPlan(null));$('refreshJobs').onclick=attempt(pollJobs);$('refreshExports').onclick=attempt(loadExports);
$('jobsBtn').onclick=attempt(async()=>{$('jobsDialog').showModal();await pollJobs()});$('closeJobs').onclick=()=>$('jobsDialog').close();
$('settingsBtn').onclick=attempt(async()=>{await loadInfo();$('settings').showModal()});$('closeSettings').onclick=()=>$('settings').close();
async function chooseOutputFolder(kind){
  if(folderPickerBusy)return;
  const button=$(kind==='video'?'changeExportPath':'changeTranscriptPath'),status=$('exportPathStatus');
  folderPickerBusy=true;$('changeExportPath').disabled=true;$('changeTranscriptPath').disabled=true;button.textContent='选择中…';status.textContent='';status.classList.remove('error');
  try{
    const saved=await api('/settings/folder/pick',{kind});
    if(saved.canceled)return;
    showOutputPaths(saved);
    if(kind==='video'){$('exportPlan').innerHTML='';await loadExports()}
    status.textContent=kind==='video'?'视频导出位置已更改。':'文稿导出位置已更改。';
  }catch(error){status.textContent=error.message;status.classList.add('error')}
  finally{folderPickerBusy=false;$('changeExportPath').disabled=false;$('changeTranscriptPath').disabled=false;button.textContent='更改'}
}
$('shutdownBtn').onclick=attempt(async()=>{
  const button=$('shutdownBtn');button.disabled=true;button.textContent='正在退出…';
  try{
    await flushSave();await api('/shutdown',{});shuttingDown=true;clearInterval(jobsPollTimer);
    $('video').ontimeupdate=null;$('video').pause();$('settings').close();
    document.querySelectorAll('button,input,select').forEach(x=>x.disabled=true);
    const screen=document.createElement('div');screen.className='shutdown-screen';screen.setAttribute('role','status');
    screen.innerHTML='<h2>工作台已退出</h2><p>已保存的文稿和分组会保留。<br>再次使用时，双击「启动工作台」入口。</p>';
    document.body.append(screen);document.title='工作台已退出';
  }catch(error){button.disabled=false;button.textContent='退出工作台';throw error}
});
$('changeExportPath').onclick=()=>chooseOutputFolder('video');$('changeTranscriptPath').onclick=()=>chooseOutputFolder('transcript');
$('exportTranscript').onclick=attempt(async()=>{
  if(!p?.sentences.length||transcriptExportBusy)return;
  const button=$('exportTranscript'),pid=p.id;transcriptExportBusy=true;button.disabled=true;button.textContent='导出中…';
  try{const result=await api(`/projects/${pid}/transcript/export`,{});toast(`文稿已导出：${result.file_name} · ${result.sentence_count} 句`)}
  finally{transcriptExportBusy=false;button.textContent='导出文稿';button.disabled=!p?.sentences.length}
});
$('saveKey').onclick=attempt(async()=>{const key=$('apiKey').value.trim();if(!key){toast('请输入API Key');return}const saved=await api('/credentials',{key});$('apiKey').value='';await loadInfo(false);const outcomes=saved.auto_transcriptions||[],failed=outcomes.find(x=>x.status==='failed');if(p&&!p.sentences.length){const updated=await api('/projects/'+p.id);p.asr_status=updated.asr_status;p.asr_error=updated.asr_error;renderTranscript()}await pollJobs();toast(failed?'密钥已保存，自动转写未启动：'+failed.message:outcomes.length?`密钥已保存，${outcomes.length} 条素材已提交自动转写`:'密钥存储：'+saved.storage)});
$('checkKey').onclick=attempt(async()=>{$('keyStatus').textContent='正在检查…';try{await api('/credentials/check',{});$('keyStatus').textContent='连接成功，音频临时上传接口可用。'}catch(e){$('keyStatus').textContent=e.message;throw e}});
window.addEventListener('beforeunload',e=>{if(dirty.size||pendingSaves){flushSave().catch(()=>{});e.preventDefault();e.returnValue=''}});
let libraryChosen=new Set(),libraryUsed=new Set();
function renderLibrary(){const query=$('librarySearch').value.trim().toLowerCase();$('libraryItems').innerHTML=[...assets.values()].filter(a=>!query||a.name.toLowerCase().includes(query)).map(a=>`<label class="library-row"><input type="checkbox" value="${esc(a.id)}" ${libraryChosen.has(a.id)?'checked':''} ${libraryUsed.has(a.id)?'disabled':''}><span class="library-copy"><b>${esc(a.name)}</b><small>${ratio(a.media)} · ${stamp(a.media?.duration_ms||0)} · ${esc(a.asr_status||'未转写')}${libraryUsed.has(a.id)?' · 本工程分组引用中':''}</small></span></label>`).join('')||'<div class="empty">素材库为空，可先导入视频。</div>';$('librarySelection').textContent=`本工程已选 ${libraryChosen.size} 条素材`;$('libraryItems').querySelectorAll('input').forEach(input=>input.onchange=()=>{if(input.checked)libraryChosen.add(input.value);else libraryChosen.delete(input.value);$('librarySelection').textContent=`本工程已选 ${libraryChosen.size} 条素材`})}
$('manageAssets').onclick=attempt(async()=>{if(!edit)return;await flushSave();await listProjects();libraryChosen=new Set(edit.asset_ids);libraryUsed=new Set(edit.groups.flatMap(g=>g.items.map(i=>i.asset_id)));$('librarySearch').value='';renderLibrary();$('assetLibrary').showModal()});
$('closeLibrary').onclick=()=>$('assetLibrary').close();$('librarySearch').oninput=renderLibrary;
$('libraryForm').onsubmit=attempt(async event=>{event.preventDefault();const button=$('saveLibrary');button.disabled=true;try{await flushSave();edit=await api(`/edits/${edit.id}/assets`,{asset_ids:[...libraryChosen],revision:edit.revision},'PUT');const list=renderAssetOptions();$('assetLibrary').close();if(list.length)await loadProject(list.some(x=>x.id===p?.id)?p.id:list[0].id);else clearProject();toast('当前工程素材清单已保存，已有文稿直接复用')}finally{button.disabled=false}});
$('outputSettings').onclick=()=>{const g=group();if(!g)return;const profile=g.output||initialOutput(p?.media||{width:1080,height:1920,fps:30,fps_fraction:'30'});$('outputSize').querySelector('[data-original]')?.remove();$('outputFps').querySelector('[data-original]')?.remove();const size=`${profile.width}x${profile.height}`;if(![...$('outputSize').options].some(x=>x.value===size))$('outputSize').insertAdjacentHTML('afterbegin',`<option data-original value="${size}">原规格 · ${profile.width} × ${profile.height}</option>`);const rate=String(profile.fps_fraction);if(![...$('outputFps').options].some(x=>x.value===rate))$('outputFps').insertAdjacentHTML('afterbegin',`<option data-original value="${esc(rate)}">原帧率 · ${fpsNumber(profile).toFixed(2)} fps</option>`);$('outputSize').value=size;$('outputFps').value=rate;$('outputDialog').showModal()};
$('closeOutput').onclick=()=>$('outputDialog').close();
$('outputForm').onsubmit=attempt(async event=>{event.preventDefault();const g=group();if(!g)return;checkpoint();const [width,height]=$('outputSize').value.split('x').map(Number);g.output={width,height,fps_fraction:$('outputFps').value,fit:'pad'};preview=null;saveSoon();renderGroups();await flushSave();$('outputDialog').close();toast('当前组输出规格已保存')});
attempt(async()=>{await loadInfo();await listProjects();const list=await listEdits();if(list.length)await loadEdit(list[0].id);await pollJobs();await loadExports()})();jobsPollTimer=setInterval(()=>pollJobs().catch(()=>{}),2500);
