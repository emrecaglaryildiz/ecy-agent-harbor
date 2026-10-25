const historySelected=new Set();
let pendingDelete=null;

function canDeleteTask(task){
 return !!state&&state.user.role!=='viewer'&&(typeof projectWritable!=='function'||projectWritable(task.project_id||''))&&['completed','failed','cancelled','incomplete'].includes(task.status)&&
  !task.reserved&&(state.user.role==='admin'||task.owner_id===state.user.id);
}

function visibleDeletable(){
 const search=$('#search').value.toLocaleLowerCase('tr');const status=$('#status-filter').value;
 return state.tasks.filter(t=>canDeleteTask(t)&&(!status||t.status===status)&&t.prompt.toLocaleLowerCase('tr').includes(search));
}

function renderHistoryActions(){
 if(!state)return;
 $('#history-actions').classList.toggle('hidden',state.user.role==='viewer');
 for(const id of historySelected)if(!state.tasks.some(t=>t.id===id&&canDeleteTask(t)))historySelected.delete(id);
 const visible=visibleDeletable();
 $('#history-selection-count').textContent=historySelected.size+' görev seçili';
 $('#delete-selected').disabled=!historySelected.size;
 $('#clear-history').disabled=!state.tasks.some(canDeleteTask);
 $('#history-select-all').disabled=!visible.length;
 $('#history-select-all').checked=visible.length>0&&visible.every(t=>historySelected.has(t.id));
 $('#history-select-all').indeterminate=visible.some(t=>historySelected.has(t.id))&&!$('#history-select-all').checked;
}

function confirmDeletion(kind,data){
 pendingDelete={kind,data};$('#confirm-delete-error').textContent='';
 if(kind==='agent'){
  const name=state.agents.find(a=>a.id===data)?.name||'Ajan';
  $('#confirm-delete-title').textContent=name+' silinsin mi?';
  $('#confirm-delete-description').textContent='Ajan listeden kaldırılacak ve kayıtlı API anahtarı silinecek. Eski görev çıktıları ve ajan adı geçmişte korunacak. Bu işlem geri alınamaz.';
 }else{
  $('#confirm-delete-title').textContent=data.all?'Görev geçmişi temizlensin mi?':data.ids.length+' görev silinsin mi?';
  $('#confirm-delete-description').textContent=(data.all?(state.user.role==='admin'?'Biten tüm görevler':'Sana ait biten tüm görevler'):'Seçilen görevler')+'; çıktıları, logları, onay kayıtları ve sunucuya kaydedilen dosyalarıyla birlikte kalıcı olarak silinecek. Çalışan ve onay bekleyen görevler korunur. Kullanım toplamı korunur. Bu işlem geri alınamaz.';
 }
 $('#confirm-delete-dialog').showModal();
}

$('#history-select-all').onchange=event=>{
 for(const task of visibleDeletable())event.target.checked?historySelected.add(task.id):historySelected.delete(task.id);
 listSignature='';renderLists();
};
$('#delete-selected').onclick=()=>confirmDeletion('history',{ids:[...historySelected]});
$('#clear-history').onclick=()=>confirmDeletion('history',{all:true});
$('#cancel-delete').onclick=()=>$('#confirm-delete-dialog').close();
$('#confirm-delete-dialog').addEventListener('close',()=>pendingDelete=null);
document.addEventListener('change',event=>{
 const checkbox=event.target.closest('[data-history-check]');if(!checkbox)return;
 checkbox.checked?historySelected.add(checkbox.dataset.historyCheck):historySelected.delete(checkbox.dataset.historyCheck);
 renderHistoryActions();
});
document.addEventListener('click',async event=>{
 const task=event.target.closest('[data-delete-task]');
 if(task){confirmDeletion('history',{ids:[task.dataset.deleteTask]});return;}
 const agent=event.target.closest('[data-delete-agent]');
 if(agent){confirmDeletion('agent',agent.dataset.deleteAgent);return;}
 const toggle=event.target.closest('[data-toggle-agent]');
 if(toggle){
  toggle.disabled=true;
  try{await api('agents/'+toggle.dataset.toggleAgent+'/toggle',{enabled:toggle.dataset.enabled==='true'});await refresh(true);await loadAgentSettings();toast('Ajan durumu güncellendi');}
  catch(error){toast(error.message);toggle.disabled=false;}
 }
});
$('#confirm-delete-form').onsubmit=async event=>{
 event.preventDefault();if(!pendingDelete)return;
 const action=pendingDelete;$('#confirm-delete-submit').disabled=true;
 try{
  if(action.kind==='agent'){
   await api('agents/'+action.data+'/delete',{});$('#confirm-delete-dialog').close();
   await refresh(true);await loadAgentSettings();toast('Ajan ve API anahtarı silindi');
  }else{
   const result=await api('history/delete',action.data);
   if($('#task-dialog').open)$('#task-dialog').close();
   $('#confirm-delete-dialog').close();historySelected.clear();await refresh();toast(result.deleted+' görev silindi');
  }
 }catch(error){$('#confirm-delete-error').textContent=error.message;}
 finally{$('#confirm-delete-submit').disabled=false;}
};
