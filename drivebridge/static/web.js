'use strict';
const $=id=>document.getElementById(id);
const csrf=document.querySelector('meta[name="csrf-token"]').content;
let state={}, selected=null, editing=false, lastLog=0, folders={mode:'local',path:'',trail:[]};
function notice(text){$('notice').textContent=text;$('notice').hidden=!text;if(text&&$('account-dialog').open)$('oauth-notice').textContent=text;}
async function api(path,options={}){
  options.headers={...options.headers,'X-CSRF-Token':csrf};
  if(options.body && !(options.body instanceof FormData)){options.headers['Content-Type']='application/json';options.body=JSON.stringify(options.body);}
  const response=await fetch(path,options);
  if(response.status===401){location.href='/login';throw Error('Sessão encerrada.');}
  let data;try{data=await response.json();}catch{throw Error('O servidor não respondeu como esperado.');}
  if(!response.ok)throw Error(data.error||'Falha na operação.');
  return data;
}
function option(select,value,label){const element=document.createElement('option');element.value=value;element.textContent=label;select.append(element);}
function renderPairs(){
  const container=$('pairs');container.replaceChildren();
  const profiles=state.profiles||{};$('pair-count').textContent=Object.keys(profiles).length;
  for(const [key,p] of Object.entries(profiles)){
    const button=document.createElement('button');button.className='pair'+(key===selected?' selected':'');button.textContent=p.name;
    const small=document.createElement('small');small.textContent=({upload:'Local → Drive',download:'Drive → Local',both:'Bidirecional ↔'})[p.direction]+(p.enabled?' · Agendado':'');button.append(small);
    button.onclick=()=>selectProfile(key);container.append(button);
  }
}
function selectProfile(key){
  selected=key;const p=state.profiles[key];if(!p)return;
  for(const field of ['name','local','remote','direction','account'])$(field).value=p[field];
  const minutes=p.interval||15;
  const frequency=minutes%1440===0?'days':minutes%60===0?'hours':'minutes';
  const schedule=p.schedule||{frequency:p.enabled?frequency:'manual',every:minutes/({minutes:1,hours:60,days:1440}[frequency]),start:'interval',time:'12:00',weekday:0,day:1};
  document.querySelector(`input[name="frequency"][value="${schedule.frequency}"]`).checked=true;
  document.querySelector(`input[name="schedule-start"][value="${schedule.start}"]`).checked=true;
  $('schedule-every').value=schedule.every;$('schedule-time').value=schedule.time;$('schedule-weekday').value=schedule.weekday;$('schedule-day').value=schedule.day;
  document.querySelector(`input[name="overwrite"][value="${p.overwrite||'changed'}"]`).checked=true;
  updateSchedule();$('workers').value=p.workers||4;$('chunk').value=p.chunk_mib||16;
  $('editor-title').textContent=p.name;$('remote-name').textContent=p.remote==='root'?'root = todo o Meu Drive':p.remote;
  $('performance-badge').textContent=$('workers').value+' transferências paralelas';
  editing=false;renderPairs();
}
function updateSchedule(){
  const frequency=document.querySelector('input[name="frequency"]:checked').value;
  const manual=frequency==='manual';
  $('repeat-settings').hidden=manual;$('start-settings').hidden=manual;
  const start=document.querySelector('input[name="schedule-start"]:checked').value;
  const timed=start==='time';
  $('weekly-settings').hidden=manual||frequency!=='weeks'||!timed;$('monthly-settings').hidden=manual||frequency!=='months'||!timed;
  $('schedule-time').disabled=manual||!timed;
  const units={minutes:'minutos',hours:'horas',days:'dias',weeks:'semanas',months:'meses'};
  $('schedule-unit').textContent=units[frequency]||'';
  $('schedule-summary').textContent=manual?'Executa apenas ao clicar em Sincronizar agora.':`Repetir a cada ${$('schedule-every').value} ${units[frequency]}. `+(timed?`Primeira execução no próximo horário ${$('schedule-time').value}.`:start==='startup'?'Primeira execução após iniciar o servidor ou ativar este agendamento.':'Primeira execução após transcorrer o intervalo.');
  const bidirectional=$('direction').value==='both';
  const always=document.querySelector('input[name="overwrite"][value="always"]');
  $('overwrite-note').textContent=document.querySelector('input[name="overwrite"]:checked').value==='newer'?'Compara as datas de alteração. Datas ausentes ou iguais com conteúdos diferentes geram conflito. Exclusões não são propagadas.':'Em modo bidirecional, alterações simultâneas são sinalizadas como conflitos. Exclusões não são propagadas.';
  always.disabled=bidirectional;
  if(bidirectional&&always.checked)document.querySelector('input[name="overwrite"][value="changed"]').checked=true;
}
function newProfile(){selected=null;editing=true;$('profile-form').reset();$('name').value='Novo par';$('remote').value='root';$('schedule-every').value=1;updateSchedule();$('workers').value=4;$('chunk').value=16;$('editor-title').textContent='Novo par de pastas';$('remote-name').textContent='root = todo o Meu Drive';renderPairs();}
async function save(){
  const schedule={frequency:document.querySelector('input[name="frequency"]:checked').value,every:Number($('schedule-every').value),start:document.querySelector('input[name="schedule-start"]:checked').value,time:$('schedule-time').value,weekday:Number($('schedule-weekday').value),day:Number($('schedule-day').value)};
  const data={id:selected,schedule,overwrite:document.querySelector('input[name="overwrite"]:checked').value,workers:Number($('workers').value),chunk_mib:Number($('chunk').value)};
  for(const field of ['name','local','remote','direction','account'])data[field]=$(field).value;
  const result=await api('/api/profiles',{method:'POST',body:data});selected=result.id;editing=false;await refresh();notice('Par salvo.');return result.id;
}
function renderStatus(){
  const p=state.progress||{};$('connection').textContent=state.busy?'Operação em andamento':'Servidor conectado';
  $('phase').textContent=state.phase==='scanning'?'VERIFICANDO ARQUIVOS':state.busy?'SINCRONIZANDO':'PRONTO';
  $('status').textContent=state.status;$('progress-track').classList.toggle('scanning',state.phase==='scanning');
  $('progress-bar').style.width=(p.percent||0)+'%';$('percent').textContent=state.phase==='scanning'?'—':(p.percent||0).toFixed(1)+'%';
  $('bytes').textContent=state.phase==='scanning'?'Calculando total…':`${p.done_text} / ${p.total_text}`;
  $('files').textContent=`${p.finished||0} / ${p.files||0}`;$('rate').textContent=state.phase==='scanning'?'—':p.rate;$('average').textContent=state.phase==='scanning'?'—':p.average;
  $('active-files').textContent=p.active?.length?p.active.map(f=>(f.direction==='upload'?'↑ Enviando: ':'↓ Baixando: ')+f.name).join('  |  '):state.phase==='scanning'?'Verificando pastas e checksums antes das transferências.':'Nenhuma transferência em andamento.';
  for(const id of ['save','sync','remove'])$(id).disabled=state.busy;$('cancel').disabled=!state.busy;
  const next=state.next_runs?.[selected];$('next-run').textContent=next?'Próxima execução: '+new Date(next*1000).toLocaleString('pt-BR'):'';
  const logs=state.logs||[];if(logs.length && logs.at(-1).id!==lastLog){const stick=$('logs').scrollTop+$('logs').clientHeight>=$('logs').scrollHeight-30;$('logs').textContent=logs.map(l=>new Date(l.time*1000).toLocaleTimeString('pt-BR')+'  '+l.message).join('\n');lastLog=logs.at(-1).id;if(stick)$('logs').scrollTop=$('logs').scrollHeight;}
}
async function refresh(){
  const data=await api('/api/state');state=data;
  const account=$('account').value;$('account').replaceChildren();
  for(const [id,user]of Object.entries(state.accounts))option($('account'),id,user.emailAddress);
  if(account && state.accounts[account])$('account').value=account;
  if(!selected&&!editing&&Object.keys(state.profiles).length)selectProfile(Object.keys(state.profiles)[0]);
  renderPairs();renderStatus();$('oauth-notice').textContent=state.oauth_configured?'Configuração OAuth disponível.':'Primeira configuração: importe o cliente OAuth nas opções avançadas.';
}
let folderRequest=0;
async function showFolders(mode){
  folders={mode,path:mode==='local'?($('local').value||state.server_home):($('remote').value||'root'),trail:[{id:'root',name:'Meu Drive'}],valid:false};
  $('folder-title').textContent=mode==='local'?'Escolher pasta no Debian':'Escolher pasta no Google Drive';
  $('folder-shortcuts').hidden=mode!=='local';
  $('folder-dialog').showModal();await loadFolders(true);
}
async function loadFolders(initial=false){
  const requestId=++folderRequest, mode=folders.mode;
  folders.valid=false;$('folder-use').disabled=true;
  $('folder-path').value=folders.path;
  $('folder-trail').textContent=mode==='local'?folders.path:folders.trail.map(t=>t.name).join(' / ');
  $('folder-error').textContent='';$('folder-list').textContent='Carregando pastas…';
  try{
    const data=mode==='local'?await api('/api/folders/local?path='+encodeURIComponent(folders.path)+(initial?'&fallback=home':'')):await api('/api/folders/remote?account='+encodeURIComponent($('account').value)+'&parent='+encodeURIComponent(folders.path));
    if(requestId!==folderRequest)return;
    if(mode==='local'){folders.path=data.path;folders.parent=data.parent;}
    $('folder-path').value=folders.path;$('folder-trail').textContent=mode==='remote'?folders.trail.map(t=>t.name).join(' / '):folders.path;
    $('folder-error').textContent=data.notice||'';
    $('folder-list').replaceChildren();
    for(const item of data.folders){const button=document.createElement('button');button.className='folder';button.textContent='▸ '+item.name;button.onclick=async()=>{folders.path=item.id;if(mode==='remote')folders.trail.push(item);await loadFolders();};$('folder-list').append(button);}
    if(!data.folders.length)$('folder-list').textContent='Nenhuma subpasta. Você pode usar esta pasta.';
    folders.valid=true;$('folder-use').disabled=false;
  }catch(error){if(requestId!==folderRequest)return;$('folder-list').textContent='';$('folder-error').textContent=error.message;}
}
function action(id,callback){if(!$(id))return;$(id).onclick=async()=>{try{notice('');await callback();}catch(error){notice(error.message);}};}
$('profile-form').onsubmit=async event=>{event.preventDefault();try{await save();}catch(error){notice(error.message);}};
$('profile-form').oninput=()=>{editing=true;$('performance-badge').textContent=$('workers').value+' transferências paralelas';updateSchedule();};
action('new',newProfile);
action('discard',()=>{if(selected)selectProfile(selected);else newProfile();notice('Alterações descartadas.');});
action('sync',async()=>{const id=await save();await api('/api/sync/'+id,{method:'POST'});notice('Sincronização iniciada. Acompanhe a atividade abaixo.');await refresh();});
action('cancel',async()=>{await api('/api/cancel',{method:'POST'});await refresh();});
action('remove',async()=>{if(selected&&confirm('Remover este par? Os arquivos permanecerão intactos.')){await api('/api/profiles/'+selected,{method:'DELETE'});newProfile();await refresh();}});
action('logout',async()=>{await api('/logout',{method:'POST'});location.href='/login';});
action('local-picker',()=>showFolders('local'));action('remote-picker',()=>showFolders('remote'));
action('folder-close',()=>{folderRequest++;$('folder-dialog').close();});action('folder-go',async()=>{folders.path=$('folder-path').value;await loadFolders();});
action('folder-up',async()=>{if(folders.mode==='local')folders.path=folders.parent||state.server_home;else{if(folders.trail.length>1)folders.trail.pop();folders.path=folders.trail.at(-1).id;}await loadFolders();});
action('folder-use',()=>{if(!folders.valid)return;$(folders.mode==='local'?'local':'remote').value=folders.path;if(folders.mode==='remote')$('remote-name').textContent=folders.trail.map(t=>t.name).join(' / ');editing=true;$('folder-dialog').close();});
for(const [id,path] of [['folder-root','/'],['folder-home',null],['folder-mnt','/mnt'],['folder-media','/media']])action(id,async()=>{folders.path=path||state.server_home;await loadFolders();});
$('folder-path').onkeydown=event=>{if(event.key==='Enter'){event.preventDefault();$('folder-go').click();}};
action('add-account',()=>$('account-dialog').showModal());action('account-close',()=>$('account-dialog').close());
action('google-login',async()=>{const data=await api('/api/oauth/start',{method:'POST',body:{email:$('email').value}});location.href=data.url;});
action('import-oauth',async()=>{const file=$('oauth-file').files[0];if(!file)throw Error('Escolha o arquivo JSON.');const body=new FormData();body.append('file',file);await api('/api/oauth/config',{method:'POST',body});$('oauth-notice').textContent='Configuração salva. Agora entre com Google.';});
action('export-log',()=>{const blob=new Blob([(state.logs||[]).map(l=>new Date(l.time*1000).toISOString()+' '+l.message).join('\n')],{type:'text/plain'});const link=document.createElement('a');link.href=URL.createObjectURL(blob);link.download='drivebridge-atividade.txt';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),1000);});
async function openEmail(){
  const settings=await api('/api/email');
  for(const field of ['host','port','security','username','sender'])$('email-'+field).value=settings[field]||'';
  $('email-enabled').checked=settings.enabled;$('email-password').value='';
  $('email-password-state').textContent=settings.password_saved?'Senha já salva. Deixe em branco para mantê-la.':'Nenhuma senha de envio salva.';
  $('email-recipient').textContent='Conta selecionada: '+(state.accounts[$('account').value]?.emailAddress||'Selecione uma conta conectada.');
  $('email-result').textContent='';$('email-dialog').showModal();
}
action('email-open',openEmail);action('email-close',()=>$('email-dialog').close());
action('email-gmail',()=>{const account=state.accounts[$('account').value]?.emailAddress||'';$('email-host').value='smtp.gmail.com';$('email-port').value=587;$('email-security').value='starttls';$('email-username').value=account;$('email-sender').value=account;});
if($('email-form'))$('email-form').onsubmit=async event=>{event.preventDefault();try{const data={enabled:$('email-enabled').checked};for(const field of ['host','port','security','username','sender','password'])data[field]=$( 'email-'+field).value;const result=await api('/api/email',{method:'POST',body:data});$('email-password').value='';$('email-password-state').textContent=result.password_saved?'Senha já salva. Deixe em branco para mantê-la.':'Nenhuma senha de envio salva.';$('email-result').textContent='Configuração salva. O destinatário será a conta Google de cada par.';}catch(error){$('email-result').textContent=error.message;}};
action('email-retry',async()=>{const result=await api('/api/email/retry',{method:'POST'});$('email-result').textContent=result.count+' relatório(s) recolocado(s) na fila.';});
action('email-test',async()=>{await api('/api/email/test',{method:'POST',body:{account:$('account').value}});$('email-result').textContent='Teste colocado na fila. Acompanhe o envio na atividade do painel.';});
async function poll(){try{await refresh();}catch(error){$('connection').textContent='Conexão interrompida';notice(error.message);}finally{setTimeout(poll,500);}}
poll();
