'use strict';
const $=id=>document.getElementById(id), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const stamp=ms=>{let s=Math.max(0,Math.floor(ms/1000));return `${Math.floor(s/60).toString().padStart(2,'0')}:${(s%60).toString().padStart(2,'0')}`};
let p=null,activeGroup=null,marked=new Set(),lastMark=-1,undoStack=[],saveTimer=null,saveChain=Promise.resolve(),preview=null,seenJobs=new Map(),currentSentence=null;
let visible=[];
let transcriptExportBusy=false,folderPickerBusy=false;
async function api(path,data,method){const options={method:method||(data===undefined?'GET':'POST'),headers:{'X-Longvideo-Client':'1'}};if(data!==undefined){options.headers['Content-Type']='application/json';options.body=JSON.stringify(data)}const r=await fetch('/api'+path,options);const j=await r.json();if(!r.ok)throw Error(typeof j.detail==='string'?j.detail:'请求失败');return j}
function toast(message){$('toast').textContent=message;$('toast').classList.add('show');clearTimeout(toast.timer);toast.timer=setTimeout(()=>$('toast').classList.remove('show'),6500)}
const attempt=fn=>async(...args)=>{try{await fn(...args)}catch(e){toast(e.message)}};
function group(){return p?.groups.find(g=>g.id===activeGroup)}
function checkpoint(){undoStack.push(JSON.stringify(p.groups));if(undoStack.length>40)undoStack.shift()}
function saveSoon(){clearTimeout(saveTimer);$('saveState').textContent='待保存…';saveTimer=setTimeout(flushSave,350)}
function flushSave(){clearTimeout(saveTimer);if(!p)return saveChain;const pid=p.id,groups=structuredClone(p.groups);saveChain=saveChain.catch(()=>{}).then(()=>api(`/projects/${pid}/groups`,{groups},'PUT')).then(()=>{$('saveState').textContent='已自动保存'}).catch(e=>{$('saveState').textContent='保存失败';toast(e.message);throw e});return saveChain}
function showOutputPaths(info){for(const [id,path] of [['exportPath',info.exports_path],['transcriptExportPath',info.transcripts_path]]){$(id).textContent=path||'加载中…';$(id).title=path||''}}
async function loadInfo(fillPath=true){const x=await api('/info');$('keyStatus').textContent=x.credential_configured?`本机后端已配置密钥；${x.credential_storage}。可检查连接。`:'尚未配置密钥。';if(fillPath){showOutputPaths(x);$('exportPathStatus').textContent='';$('exportPathStatus').classList.remove('error')}return x}
async function listProjects(selected){const list=await api('/projects');$('projectSelect').innerHTML='<option value="">选择原视频</option>'+list.map(x=>`<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('');if(selected)$('projectSelect').value=selected;return list}
async function loadProject(id){if(!id)return;p=await api('/projects/'+id);activeGroup=p.groups[0]?.id;marked.clear();undoStack=[];lastMark=-1;preview=null;clearRangeError();$('selectionRange').value='';$('video').src=`/api/projects/${id}/media`;$('video').playbackRate=Number($('speed').value);$('sourceMeta').textContent=`${p.media.display_width||p.media.width}×${p.media.display_height||p.media.height} · ${p.media.fps.toFixed(2)} fps · ${stamp(p.media.duration_ms)}`;$('saveState').textContent='已读取本地项目';renderTranscript();renderGroups()}
function sentenceNumber(sentence){
  if(Number.isInteger(sentence.source_index)&&sentence.source_index>=0)return sentence.source_index+1;
  const index=p?.sentences.findIndex(s=>s.id===sentence.id)??-1;
  return index>=0?index+1:null;
}
function rangeNumbers(value,maximum){
  const parts=value.trim().replace(/[０-９]/g,c=>String(c.charCodeAt(0)-0xff10)).replace(/[~～—–至]/g,'-').replace(/\s*-\s*/g,'-').split(/[,，、;；\s]+/).filter(Boolean);
  if(!parts.length)throw Error('请输入句子序号，例如：1-5, 8, 12-15。');
  const numbers=new Set();
  for(const part of parts){
    const match=part.match(/^(\d+)(?:-(\d+))?$/);
    if(!match)throw Error('序号格式不正确，请用逗号分隔，并用短横线连接范围。');
    const start=Number(match[1]),end=Number(match[2]??match[1]);
    if(start<1||end>maximum)throw Error(`请输入 1–${maximum} 之间的句子序号。`);
    if(start>end)throw Error('范围的起始序号不能大于结束序号。');
    for(let n=start;n<=end;n++)numbers.add(n);
  }
  return numbers;
}
function clearRangeError(){
  $('selectionRangeError').hidden=true;
  $('selectionRangeError').textContent='';
  $('selectionRange').removeAttribute('aria-invalid');
}
function renderTranscript(){
  const query=$('search').value.trim().toLowerCase();
  visible=(p?.sentences||[]).filter(s=>!query||s.text.toLowerCase().includes(query));
  $('sentenceCount').textContent=p?.sentences.length||0;
  if(!visible.length){
    let empty='导入视频后自动生成文稿。';
    if(p?.sentences.length)empty='没有匹配的句子。';
    else if(p?.asr_status==='等待密钥')empty='请在设置中保存百炼 API Key，随后将自动转写。';
    else if(p?.asr_status==='转写中')empty='音频正在自动转写，完成后会显示文稿。';
    else if(p&&['转写未启动','转写失败','转写已取消'].includes(p.asr_status))empty=esc(p.asr_error||'自动转写未完成，请查看后台任务。');
    $('transcript').innerHTML='<div class="empty large">'+empty+'</div>';
    selectionState();return;
  }
  $('transcript').innerHTML=visible.map(s=>{
    const number=sentenceNumber(s);
    return `<div class="sentence ${marked.has(s.id)?'selected':''} ${currentSentence===s.id?'playing':''}" data-id="${esc(s.id)}"><input type="checkbox" aria-label="选择句子 ${number}" ${marked.has(s.id)?'checked':''}><div class="sentence-meta"><span class="sentence-number" title="原句 ${number}">${number}.</span><span class="stamp">${stamp(s.start_ms)}</span></div><span class="text">${esc(s.text)}</span><button class="edit" title="修正显示文字" aria-label="修正句子 ${number}">✎</button></div>`;
  }).join('');
  $('transcript').querySelectorAll('.sentence').forEach((row,i)=>{
    row.addEventListener('click',e=>{if(e.target.matches('input')||e.target.closest('.edit'))return;preview=null;seek(visible[i].start_ms,true)});
    row.querySelector('input').addEventListener('click',e=>{
      e.stopPropagation();const checked=e.target.checked;
      if(e.shiftKey&&lastMark>=0){
        for(let n=Math.min(i,lastMark);n<=Math.max(i,lastMark);n++){
          if(checked)marked.add(visible[n].id);else marked.delete(visible[n].id);
        }
      }else if(checked)marked.add(visible[i].id);else marked.delete(visible[i].id);
      lastMark=i;renderTranscript();
    });
    row.querySelector('.edit').onclick=attempt(async e=>{
      e.stopPropagation();const s=visible[i],text=prompt('修正显示文字（原声和时间位置不变）',s.text);
      if(text===null||!text.trim())return;
      await flushSave();await api(`/projects/${p.id}/sentence/${encodeURIComponent(s.id)}`,{text:text.trim()},'PUT');
      s.text=text.trim();p.groups.forEach(g=>g.items.forEach(item=>{if(item.id===s.id)item.text=s.text}));
      renderTranscript();renderGroups();
    });
  });
  selectionState();
}
function selectionState(){
  $('exportTranscript').disabled=transcriptExportBusy||!p?.sentences.length;
  const outside=marked.size-visible.filter(s=>marked.has(s.id)).length;
  $('selectionCount').textContent=marked.size?`已选择 ${marked.size} 句${outside?` · 筛选外 ${outside} 句`:''}`:'未选择句子';
  $('addSelected').disabled=!p||!marked.size;
  $('clearSelection').disabled=!marked.size;
  const maximum=(p?.sentences||[]).reduce((n,s)=>Math.max(n,sentenceNumber(s)||0),0);
  $('rangeBounds').textContent=maximum?`1–${maximum}`:'';
  $('bulkSelectionBtn').disabled=!maximum;
  $('applyRange').disabled=!maximum;
}
function selectedSpans(g){const spans=[];if(!p||!g)return spans;for(const item of g.items){const start=Math.max(0,item.start_ms-80),end=Math.min(p.media.duration_ms,item.end_ms+120),prev=spans.at(-1),last=prev?.items.at(-1);if(prev&&item.version===last.version&&item.source_index===last.source_index+1&&item.start_ms>=last.start_ms&&!item.edited&&!last.edited){prev.end_ms=Math.max(end,prev.end_ms);prev.items.push(item)}else spans.push({start_ms:start,end_ms:end,items:[item]})}return spans}
function renderGroups(){if(!p)return;$('groupTabs').innerHTML=p.groups.map(g=>`<button class="${g.id===activeGroup?'active':''}" data-id="${esc(g.id)}" title="${esc(g.name)}">${esc(g.name)} · ${g.items.length}</button>`).join('');$('groupTabs').querySelectorAll('button').forEach(b=>b.onclick=()=>{activeGroup=b.dataset.id;preview=null;renderGroups()});const g=group();$('groupName').value=g?.name||'';$('groupName').disabled=!g;$('groupDuration').textContent=stamp(selectedSpans(g).reduce((t,s)=>t+s.end_ms-s.start_ms,0));$('groupItems').innerHTML=g?.items.length?g.items.map((item,i)=>`<div class="item" draggable="true" data-item="${esc(item.item_id)}"><span class="handle" title="拖动排序">⋮⋮</span><div><div class="item-heading"><span class="sentence-number" title="原句 ${sentenceNumber(item)}">${sentenceNumber(item)??"—"}.</span><div class="item-text">${esc(item.text)}</div></div><small>${stamp(item.start_ms)}—${stamp(item.end_ms)}<span class="trim">调整边界</span></small></div><button class="remove" aria-label="移除片段 ${i+1}">×</button></div>`).join(''):'<div class="empty">点选文稿句子，再点击「加入当前组」。</div>';$('groupItems').querySelectorAll('.item').forEach((row,i)=>{row.ondragstart=e=>{e.dataTransfer.setData('text/plain',g.items[i].item_id);e.dataTransfer.effectAllowed='move'};row.ondragover=e=>{e.preventDefault();row.classList.add('drag-over')};row.ondragleave=()=>row.classList.remove('drag-over');row.ondrop=e=>{e.preventDefault();row.classList.remove('drag-over');const id=e.dataTransfer.getData('text/plain'),old=g.items.findIndex(x=>x.item_id===id);if(old<0||old===i)return;checkpoint();const [item]=g.items.splice(old,1);g.items.splice(i,0,item);preview=null;saveSoon();renderGroups()};row.querySelector('.remove').onclick=()=>{checkpoint();g.items.splice(i,1);preview=null;saveSoon();renderGroups()};row.querySelector('.item-text').onclick=()=>{preview=null;seek(g.items[i].start_ms,true)};row.querySelector('.trim').onclick=()=>{const item=g.items[i],start=prompt('原片开始时间，单位秒',String(item.start_ms/1000));if(start===null)return;const end=prompt('原片结束时间，单位秒',String(item.end_ms/1000));if(end===null)return;const a=Number(start)*1000,b=Number(end)*1000;if(!Number.isFinite(a)||!Number.isFinite(b)||a<0||a>=b||b>p.media.duration_ms){toast('起止时间无效');return}checkpoint();item.start_ms=Math.round(a);item.end_ms=Math.round(b);item.edited=true;saveSoon();renderGroups()}});$('exportPlan').innerHTML='';$('undo').disabled=!undoStack.length}
function seek(ms,play){const v=$('video');v.currentTime=Math.max(0,ms/1000);if(play)v.play().catch(()=>{})}
async function showPlan(ids){await flushSave();const list=await api(`/projects/${p.id}/export-plan`,{group_ids:ids});$('exportPlan').innerHTML=list.length?list.map(x=>`<div>${esc(x.file_name)} · ${stamp(x.duration_ms)} · ${x.sentence_count}句</div>`).join(''):'没有非空分组可导出。';return list}
async function exportGroups(all){if(!p)return;const ids=all?null:[activeGroup];const list=await showPlan(ids);if(!list.length)return;await api(`/projects/${p.id}/export`,{group_ids:ids});toast('已加入后台导出队列，可继续整理其他分组');pollJobs()}
const jobLabels={queued:'等待中',running:'处理中',succeeded:'已完成',failed:'失败',canceled:'已取消',interrupted:'已中断'};
let jobsSignature='';
async function pollJobs(){
  const jobs=await api('/jobs'),active=jobs.filter(j=>['queued','running'].includes(j.status)).length;
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
  const button=$('importBtn');button.disabled=true;button.textContent='选择素材…';
  try{
    await flushSave();
    const list=await api('/import/pick',{project_id:p?.id});
    if(!list.length)return;
    await listProjects(list[0].id);await loadProject(list[0].id);
    const states=list.map(x=>x.auto_transcription||{}),queued=states.filter(x=>['queued','running'].includes(x.status)),failed=states.find(x=>x.status==='failed');
    await pollJobs();
    if(states.some(x=>x.status==='needs_key')){await loadInfo();$('settings').showModal();toast('导入完成，请保存百炼 API Key，随后自动转写')}
    else if(failed)toast('导入完成，自动转写未启动：'+failed.message);
    else toast(queued.length?`已导入 ${list.length} 条素材，${queued.length} 条自动转写中`:`已载入 ${list.length} 条素材，已有文稿已保留`);
  }finally{button.disabled=false;button.textContent='导入素材'}
});
$('projectSelect').onchange=attempt(async e=>{await flushSave();await loadProject(e.target.value)});
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
$('addSelected').onclick=()=>{if(!p)return;if(!group()){p.groups.push({id:crypto.randomUUID(),name:'切片001',items:[]});activeGroup=p.groups[0].id}checkpoint();const g=group(),existing=new Set(g.items.map(x=>x.id));let n=0;for(const s of p.sentences){if(marked.has(s.id)&&!existing.has(s.id)){g.items.push({...structuredClone(s),item_id:crypto.randomUUID(),edited:false});n++}}marked.clear();saveSoon();renderTranscript();renderGroups();toast(`已加入 ${n} 句`)};
$('newGroup').onclick=()=>{if(!p)return;checkpoint();const g={id:crypto.randomUUID(),name:`切片${String(p.groups.length+1).padStart(3,'0')}`,items:[]};p.groups.push(g);activeGroup=g.id;saveSoon();renderGroups()};
$('groupName').oninput=e=>{const g=group();if(!g)return;g.name=e.target.value||'未命名短片';saveSoon();const button=$('groupTabs').querySelector(`[data-id="${CSS.escape(g.id)}"]`);if(button)button.textContent=g.name+' · '+g.items.length;$('exportPlan').innerHTML=''};
$('deleteGroup').onclick=()=>{const g=group();if(!g)return;if(!confirm(`删除分组「${g.name}」？原素材保留。`))return;checkpoint();p.groups=p.groups.filter(x=>x.id!==g.id);activeGroup=p.groups[0]?.id;saveSoon();renderGroups()};
$('undo').onclick=()=>{if(!undoStack.length)return;p.groups=JSON.parse(undoStack.pop());if(!p.groups.some(g=>g.id===activeGroup))activeGroup=p.groups[0]?.id;saveSoon();renderGroups()};
$('previewGroup').onclick=()=>{const g=group(),spans=selectedSpans(g);if(!spans.length){toast('当前组没有片段');return}preview={spans,index:0};seek(spans[0].start_ms,true);$('nowPlaying').textContent=`组预览：${g.name} · 1/${spans.length}`};
$('stopPreview').onclick=()=>{preview=null;$('video').pause()};$('speed').onchange=e=>$('video').playbackRate=Number(e.target.value);
$('video').ontimeupdate=()=>{const time=$('video').currentTime*1000;$('playTime').textContent=stamp(time);if(preview&&time>=preview.spans[preview.index].end_ms-35){preview.index++;if(preview.index>=preview.spans.length){preview=null;$('video').pause();$('nowPlaying').textContent='组预览结束'}else{seek(preview.spans[preview.index].start_ms,true);$('nowPlaying').textContent=`组预览 · ${preview.index+1}/${preview.spans.length}`}}if(!p)return;const s=p.sentences.find(x=>x.start_ms<=time&&time<=x.end_ms);if(s?.id!==currentSentence){$('transcript').querySelector('.playing')?.classList.remove('playing');if(s){$('transcript').querySelector(`[data-id="${CSS.escape(s.id)}"]`)?.classList.add('playing');if(!preview)$('nowPlaying').textContent=s.text}currentSentence=s?.id}};
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
$('changeExportPath').onclick=()=>chooseOutputFolder('video');$('changeTranscriptPath').onclick=()=>chooseOutputFolder('transcript');
$('exportTranscript').onclick=attempt(async()=>{
  if(!p?.sentences.length||transcriptExportBusy)return;
  const button=$('exportTranscript'),pid=p.id;transcriptExportBusy=true;button.disabled=true;button.textContent='导出中…';
  try{const result=await api(`/projects/${pid}/transcript/export`,{});toast(`文稿已导出：${result.file_name} · ${result.sentence_count} 句`)}
  finally{transcriptExportBusy=false;button.textContent='导出文稿';button.disabled=!p?.sentences.length}
});
$('saveKey').onclick=attempt(async()=>{const key=$('apiKey').value.trim();if(!key){toast('请输入API Key');return}const saved=await api('/credentials',{key});$('apiKey').value='';await loadInfo(false);const outcomes=saved.auto_transcriptions||[],failed=outcomes.find(x=>x.status==='failed');if(p&&!p.sentences.length){const updated=await api('/projects/'+p.id);p.asr_status=updated.asr_status;p.asr_error=updated.asr_error;renderTranscript()}await pollJobs();toast(failed?'密钥已保存，自动转写未启动：'+failed.message:outcomes.length?`密钥已保存，${outcomes.length} 条素材已提交自动转写`:'密钥存储：'+saved.storage)});
$('checkKey').onclick=attempt(async()=>{$('keyStatus').textContent='正在检查…';try{await api('/credentials/check',{});$('keyStatus').textContent='连接成功，音频临时上传接口可用。'}catch(e){$('keyStatus').textContent=e.message;throw e}});
window.addEventListener('beforeunload',e=>{if($('saveState').textContent==='待保存…'){flushSave();e.preventDefault();e.returnValue=''}});
attempt(async()=>{await loadInfo();const list=await listProjects();if(list.length){$('projectSelect').value=list[0].id;await loadProject(list[0].id)}await pollJobs();await loadExports()})();setInterval(()=>pollJobs().catch(()=>{}),2500);
