let setupRequired=false, authMode='live', agentSettings=[];

async function initializeAuth(){
 try{
  const response=await fetch('/api/auth/status');
  const info=await response.json();
  setupRequired=info.setup_required;authMode=info.mode;
  configureLogin();
  if(setupRequired)showLogin();else await refresh(true);
 }catch(error){toast('Sunucuya ulaşılamadı. Başlat.command ile uygulamayı çalıştır.');}
}

function configureLogin(){
 $('#login-title').textContent=setupRequired?'Kendi çalışma alanını kur':'Çalışma alanına giriş';
 $('#login-description').textContent=setupRequired?'Yönetici parolanı belirle. Ardından API ayarlarından kendi ajanlarını ekleyebilirsin.':authMode==='mock'?'Test ortamı: bir rol seç.':'Hesabını seç ve parolanla giriş yap.';
 $('#login-role-field').classList.toggle('hidden',setupRequired);
 $('#setup-confirm-field').classList.toggle('hidden',!setupRequired);
 $('#setup-confirm').required=setupRequired;$('#bootstrap-field').classList.toggle('hidden',!setupRequired);$('#bootstrap-code').required=setupRequired;
 $('#login-token').required=authMode!=='mock';
 $('#login-token').minLength=setupRequired?12:0;
 $('#login-token').autocomplete=setupRequired?'new-password':'current-password';
 $('#login-submit').textContent=setupRequired?'Çalışma alanını oluştur →':'Giriş yap →';
}

async function handleLogin(event){
 event.preventDefault();$('#login-submit').disabled=true;
 try{
  const token=$('#login-token').value;
  if(setupRequired){
   if(token!==$('#setup-confirm').value)throw new Error('Parolalar eşleşmiyor');
   await api('setup',{password:token,bootstrap_token:$('#bootstrap-code').value});$('#bootstrap-code').value='';
   setupRequired=false;$('#login-user').value='admin';
  }
  await api('login',{user:$('#login-user').value,token,code:$('#login-code').value});$('#login-code').value='';
  $('#login-token').value='';$('#setup-confirm').value='';$('#login-error').textContent='';
  $('#login-dialog').close();await refresh(true);
  if(!state.agents.some(a=>a.configured&&a.enabled))go('settings','providers');
 }catch(error){$('#login-error').textContent=error.message;configureLogin();}
 finally{$('#login-submit').disabled=false;}
}

async function loadAgentSettings(){
 const admin=state?.user.role==='admin';
 $('#settings-admin').classList.toggle('hidden',!admin);
 $('#settings-readonly').classList.toggle('hidden',admin);
 if(!admin)return;
 updateManagementOverview();
 try{
  const data=await api('agents');agentSettings=data.agents;
  $('#provider-settings-list').innerHTML=agentSettings.map(a=>{
   const test=a.last_test;
   return `<section class="panel provider-card"><div class="provider-card-top">${agentAvatar(a)}<div><h3>${esc(a.name)}</h3><p>${esc(a.role)} · ${esc(a.model)}</p></div><span class="badge ${!a.enabled?'cancelled':test?.ok?'completed':test?'failed':'queued'}">${!a.enabled?'Devre dışı':test?.ok?'Bağlantı doğrulandı':test?'Test başarısız':a.configured?'Test bekliyor':'API ekle'}</span></div><div class="provider-address">${a.endpoint?esc(a.endpoint):esc(I18n.t('Henüz API adresi eklenmedi'))}</div><div class="provider-facts"><span>${esc(a.provider)}</span><span>${a.has_api_key?'🔒 Anahtar kayıtlı':'Anahtar yok'}</span><span>$${a.price.toFixed(6)} / 1K token</span></div>${test?`<p class="connection-result ${test.ok?'':'error'}">${esc(test.message)} · ${test.latency_ms} ms · ${date(test.checked_at)}</p>`:''}<div class="detail-actions"><button type="button" class="button secondary" data-edit-agent="${esc(a.id)}">Düzenle</button><button type="button" class="button secondary" data-toggle-agent="${esc(a.id)}" data-enabled="${!a.enabled}">${a.enabled?'Pasife al':'Etkinleştir'}</button><button type="button" class="button secondary danger" data-delete-agent="${esc(a.id)}">Sil</button><button type="button" class="button primary" data-test-agent="${esc(a.id)}" ${a.configured&&a.enabled?'':'disabled'}>Bağlantıyı test et ↗</button></div></section>`;
  }).join('');
 }catch(error){toast(error.message);}
}

function updateProviderFields(){
 const bridge=$('#agent-provider').value==='hermes-bridge';
 $('#agent-bridge-field').classList.toggle('hidden',!bridge);
 $('#bridge-explanation').textContent=bridge?'Hermes seçeneği ayrı bir bridge servisi gerektirir. Hermes kurulumunun doğrudan API adresi olduğunu varsaymaz. Bridge için /v1/tasks yolu otomatik eklenir.':'OpenAI uyumlu servisin base URL adresini gir. Örnek: Ollama için http://localhost:11434/v1. Model kimliğini sağlayıcının kullandığı şekilde yaz.';
}

async function editAgent(id=null){
 if(state.user.role!=='admin'){toast('Yönetici erişimi gerekli');return;}
 if(id&&!agentSettings.some(a=>a.id===id))await loadAgentSettings();
 const a=agentSettings.find(a=>a.id===id)||{};
 $('#agent-form').reset();$('#agent-id').value=a.id||'';
 $('#agent-editor-title').textContent=id?'Ajan ayarlarını düzenle':'Yeni ajan ekle';
 for(const field of ['name','role','model','endpoint','hardware','instruction'])$('#agent-'+field).value=a[field]||'';
 $('#agent-provider').value=a.provider||'openai-compatible';
 const caps=a.capabilities||{};for(const key of ['vision','tools'])$('#agent-'+key).value=caps[key]===true?'yes':caps[key]===false?'no':'unknown';$('#agent-context').value=caps.context_tokens||'';$('#agent-specialties').value=(caps.specialties||[]).join(', ');
 $('#agent-price').value=a.price??0;$('#agent-color').value=a.color||'mint';
 $('#agent-enabled').checked=a.enabled??true;$('#agent-safe-mode').checked=!!a.safe_mode;
 $('#agent-key-status').textContent=a.has_api_key?'Kayıtlı anahtar korunur. Değiştirmek için yeni anahtar yaz.':'Anahtar gerekmeyen yerel servisler için boş bırakabilirsin.';
 $('#agent-form-error').textContent='';updateProviderFields();$('#agent-dialog').showModal();
}

$('#new-agent').onclick=()=>editAgent();$('#close-agent').onclick=()=>$('#agent-dialog').close();
$('#agent-dialog').addEventListener('close',()=>{$('#agent-key').value='';});
$('#agent-provider').onchange=updateProviderFields;
$('#agent-form').onsubmit=async event=>{
 event.preventDefault();$('#save-agent').disabled=true;
 try{
  const body={};for(const field of ['id','name','role','model','endpoint','hardware','instruction','provider','color'])body[field]=$('#agent-'+field).value;
  body.capabilities={vision:$('#agent-vision').value==='unknown'?null:$('#agent-vision').value==='yes',tools:$('#agent-tools').value==='unknown'?null:$('#agent-tools').value==='yes',context_tokens:$('#agent-context').value?Number($('#agent-context').value):null,specialties:$('#agent-specialties').value.split(',').map(x=>x.trim()).filter(Boolean)};
  body.api_key=$('#agent-key').value;body.clear_key=$('#agent-clear-key').checked;
  body.price=Number($('#agent-price').value);body.enabled=$('#agent-enabled').checked;body.safe_mode=$('#agent-safe-mode').checked;
  const result=await api('agents',body);$('#agent-key').value='';$('#agent-dialog').close();
  await refresh(true);await loadAgentSettings();
  if(!selected.length&&body.enabled){selected=[result.id];renderPicker();}
  toast('Ajan kaydedildi. Bağlantıyı test edebilirsin.');
 }catch(error){$('#agent-form-error').textContent=error.message;}
 finally{$('#save-agent').disabled=false;}
};

document.addEventListener('click',async event=>{
 const edit=event.target.closest('[data-edit-agent]');if(edit){await editAgent(edit.dataset.editAgent);return;}
 const test=event.target.closest('[data-test-agent]');
 if(test){
  test.disabled=true;test.textContent='Bağlantı kontrol ediliyor…';
  try{const result=await api('agents/'+test.dataset.testAgent+'/test',{});await refresh();await loadAgentSettings();toast(result.message);}
  catch(error){toast(error.message);test.disabled=false;test.textContent='Bağlantıyı test et ↗';}
 }
 const download=event.target.closest('[data-export-task]');
 if(download)downloadReport(download.dataset.exportTask,download.dataset.format,download.dataset.exportRun);
});

$('#access-form').onsubmit=async event=>{
 event.preventDefault();
 try{const result=await api('access/password',{user:$('#access-user').value,password:$('#access-password').value});$('#access-password').value='';toast('Parola güncellendi');if(result.reauthenticate){state=null;csrf='';showLogin();}}
 catch(error){toast(error.message);}
};

async function downloadReport(id,format,run=null){
 try{
  const path='/api/tasks/'+encodeURIComponent(id)+'/export?format='+format+'&lang='+uiLanguage()+(run?'&run='+encodeURIComponent(run):'');
  const response=await fetch(path);
  if(!response.ok){const data=await response.json();throw new Error(data.error||'Rapor oluşturulamadı');}
  const blob=await response.blob();const url=URL.createObjectURL(blob);
  const link=document.createElement('a');link.href=url;link.download='harbor-'+id.slice(0,8)+(run?'-ajan':'')+'.'+format;
  document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
 }catch(error){toast(error.message);}
}

let internetEnabled=false;
function updateBrowserStatus(){
 const browser=state?.browser;if(!browser)return;
 updateManagementOverview();
 const button=$('#install-browser');
 $('#browser-status-text').textContent=browser.installing?(browser.stage||'Kuruluyor…'):browser.error|| (browser.installed?'Playwright MCP hazır · İzole tarayıcı profili':'Kurulum gerekli · Node.js 18+ ve Chrome/Chromium kullanılır');
 button.disabled=browser.installing||browser.installed;
 button.textContent=browser.installing?'Kuruluyor…':browser.installed?'MCP hazır ✓':'Playwright MCP kur';
 if(!browser.installed&&internetEnabled){internetEnabled=false;$('#internet-toggle').textContent='◎ İnternet kapalı';$('#internet-toggle').setAttribute('aria-pressed','false');}
}
$('#internet-toggle').onclick=()=>{
 if(!internetEnabled&&!state?.browser?.installed){go('settings','browser');toast('Önce Playwright MCP kurulumunu tamamla');return;}
 internetEnabled=!internetEnabled;
 $('#internet-toggle').textContent=internetEnabled?'◉ İnternet açık':'◎ İnternet kapalı';
 $('#internet-toggle').setAttribute('aria-pressed',String(internetEnabled));
};
$('#install-browser').onclick=async()=>{
 try{await api('browser/install',{});$('#install-browser').disabled=true;$('#browser-status-text').textContent='Kurulum başlatıldı…';await refresh();toast('MCP kurulumu başlatıldı; durum burada güncellenecek');}
 catch(error){toast(error.message);}
};


// Management tabs preserve existing forms and credentials; only visibility changes.
let managementSection='overview';
const managementSections=['overview','providers','browser','access','notifications','usage','backup','hooks','security','audit'];
function selectManagementSection(section){
 managementSection=managementSections.includes(section)?section:'overview';
 document.querySelectorAll('.management-tab').forEach(button=>{
  const active=button.dataset.managementTab===managementSection;
  button.classList.toggle('active',active);button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;
 });
 document.querySelectorAll('.management-panel').forEach(panel=>panel.classList.toggle('hidden',panel.id!=='management-panel-'+managementSection));
 updateManagementOverview();if(typeof refreshAudit==='function'&&managementSection==='audit')refreshAudit(true);if(typeof refreshMCP==='function'&&['browser','security'].includes(managementSection))refreshMCP(true);if(typeof refreshPlatform==='function'&&['backup','hooks','security'].includes(managementSection))refreshPlatform(true);
}
function updateManagementOverview(){
 if(!state)return;
 const agents=state.agents.filter(a=>a.configured&&a.enabled).length;
 $('#management-agents').textContent=agents+' etkin bağlantı';
 $('#management-browser').textContent=state.browser?.installing?'Kurulum sürüyor':state.browser?.installed?'MCP hazır':'Henüz kurulmadı';
 const telegram=typeof workspaceData!=='undefined'?workspaceData.telegram:{};
 $('#management-telegram').textContent=telegram?.enabled?'Bildirimler açık':'Bildirimler kapalı';
 $('#management-cost').textContent='$'+state.usage.cost.toFixed(4)+' · Bugün';
}
document.addEventListener('click',event=>{const target=event.target.closest('[data-management-tab]');if(target)selectManagementSection(target.dataset.managementTab)});
document.querySelector('.management-rail').addEventListener('keydown',event=>{
 const tabs=[...document.querySelectorAll('.management-tab')],index=tabs.indexOf(document.activeElement);
 if(index<0||!['ArrowDown','ArrowUp','ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
 event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(['ArrowDown','ArrowRight'].includes(event.key)?1:-1)+tabs.length)%tabs.length;
 selectManagementSection(tabs[next].dataset.managementTab);tabs[next].focus();
});
