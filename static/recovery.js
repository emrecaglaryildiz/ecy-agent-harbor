let backupReadinessVersion=0;
async function refreshBackupReadiness(){if(!state||state.user.role!=='admin'||page!=='settings'||managementSection!=='backup')return;const version=++backupReadinessVersion;try{const d=await api('platform/backup/status');if(version!==backupReadinessVersion)return;const text=d.ready?(d.queued?'Duraklatılmış kuyruk yedeklenmeye hazır.':'Yedekleme için hazır.'):'Yedekleme için işler henüz tamamlanmadı.';$('#backup-readiness').innerHTML=`<p><b>${I18n.t(text)}</b></p><p>${I18n.t('Kuyrukta')}: ${d.queued} · ${I18n.t('Çalışan görev')}: ${d.running_tasks} · ${I18n.t('Süren ajan çağrısı')}: ${d.running_runs} · ${I18n.t('Süren MCP çağrısı')}: ${d.mcp_active} · ${I18n.t('Gönderilen bildirim')}: ${d.notifications_sending}</p>${d.queued&&!d.dispatch_paused?'<p>'+I18n.t('Kuyruğu koruyarak yedeklemek için önce görev işlemeyi duraklat.')+'</p>':''}`;$('#backup-form button').disabled=!d.ready}catch(error){$('#backup-readiness').textContent=error.message;$('#backup-form button').disabled=true}}
$('#backup-readiness-refresh').onclick=refreshBackupReadiness;

let storageVersion=0,retentionVersion=0,retentionNext=null,retentionScope=null,retentionDays=null,storageSnapshot=null,retentionSnapshot=null;
async function refreshStorage(){
 if(!state||state.user.role!=='admin'||page!=='settings'||managementSection!=='storage')return;
 const version=++storageVersion;retentionVersion++;retentionSnapshot=null;$('#retention-form button').disabled=false;retentionNext=null;$('#retention-preview').textContent='';$('#retention-next').classList.add('hidden');
 try{const d=await api('platform/storage');if(version!==storageVersion||page!=='settings'||managementSection!=='storage')return;
 storageSnapshot=d;renderStorageSummary(d);
 }catch(error){$('#storage-summary').textContent=error.message}
}
async function previewRetention(offset=0){
 const version=++retentionVersion,scope=currentProject||null,days=offset===0?Number($('#retention-days').value):retentionDays;
 $('#retention-next').classList.add('hidden');$('#retention-form button').disabled=true;
 try{const d=await api('platform/retention/preview',{project_id:scope,days,offset});if(version!==retentionVersion||scope!==(currentProject||null)||page!=='settings'||managementSection!=='storage')return;
 retentionScope=scope;retentionDays=days;retentionNext=d.next_offset;retentionSnapshot=d;renderRetentionSummary(d);
 }catch(error){if(version===retentionVersion)$('#retention-preview').textContent=error.message}finally{if(version===retentionVersion)$('#retention-form button').disabled=false}
}
$('#storage-refresh').onclick=refreshStorage;
$('#retention-form').onsubmit=e=>{e.preventDefault();previewRetention()};
$('#retention-next').onclick=()=>{if(retentionNext!==null&&retentionScope===(currentProject||null))previewRetention(retentionNext)};

function renderStorageSummary(d){
 const tr=I18n.t;$('#storage-summary').innerHTML=`<p>${tr('Veritabanı')}: ${(d.database_bytes/1048576).toFixed(2)} MiB</p>${Object.entries(d.stores).map(([kind,s])=>`<p><b>${tr(kind==='uploads'?'Yüklenen dosyalar':'Kayıtlı raporlar')}</b>: ${s.files} · ${(s.bytes/1048576).toFixed(2)} MiB · ${tr('Eksik')}: ${s.missing} · ${tr('Boyut uyuşmazlığı')}: ${s.size_mismatches} · ${tr('Referanssız')}: ${s.unreferenced} · ${tr('Güvensiz / geçici kayıt')}: ${s.unsafe_entries}</p>`).join('')}<p>${tr(d.truncated?'Tarama sınırına ulaşıldı; sayılar kısmidir.':'Tarama tamamlandı.')} ${tr('Bu kontrol dosya özetlerini doğrulamaz; bütünlük için yedekleme provasını kullan.')}</p>`
}

function renderRetentionSummary(d){
const tr=I18n.t;
 $('#retention-preview').innerHTML=`<p><b>${tr('Yalnızca önizleme; hiçbir veri silinmedi.')}</b></p><p>${tr('Eşik tarihi')}: ${date(d.cutoff)} · ${tr('Uygun görev')}: ${d.total} · ${tr('Korunacak kullanım')}: $${d.cost_preserved_usd.toFixed(4)}</p>${d.items.map(t=>`<div class="search-result"><code>${esc(t.id)}</code> · ${esc(tr(names[t.status]||t.status))} · ${date(t.updated_at)}</div>`).join('')||'<p>'+tr('Bu aralıkta uygun görev yok.')+'</p>'}`;
 $('#retention-next').classList.toggle('hidden',retentionNext===null)
}
document.addEventListener('ecy-language-change',()=>{if(storageSnapshot)renderStorageSummary(storageSnapshot);if(retentionSnapshot)renderRetentionSummary(retentionSnapshot)});
