/* UI localization only. Prompts, model output, evidence and user-owned names stay untouched. */
(() => {
 const storageKey='ecy-harbor-language';
 const supported=value=>value==='tr'||value==='en';
 let saved;try{saved=localStorage.getItem(storageKey)}catch(_){}
 let language=supported(saved)?saved:((navigator.language||'').toLowerCase().startsWith('tr')?'tr':'en');
 let catalog={}, scheduled=false;
 const originals=new WeakMap(), attrs=new WeakMap();
 const excluded='.markdown-body,.prompt-detail,.evidence-lines,.provider-address,#detail-title,#project-grid h2,#file-library b,#attachment-list .attachment-chip span,#agent-picker b,#agent-cards h2,.provider-card h3,#connection-nodes b,#connection-nodes small,.flow-person,#flow-judge-name,[data-i18n-ignore="true"],#template-grid article[data-user-template="true"] h2,#template-grid article[data-user-template="true"] p,#schedule-list h2,#schedule-list p[data-user-prompt="true"]';
 const rules=[
  [/^Eşleşen satırlar: (\d+)–(\d+)$/, 'Matching lines: $1–$2'],
  [/^([\d.,]+) etkin bağlantı$/, '$1 active connections'],
  [/^1 ajan seçili$/, '1 agent selected'],[/^1 dosya seçili$/, '1 file selected'],[/^1 görev seçili$/, '1 task selected'],
  [/^● ([\d.,]+) çalışıyor$/, '● $1 working'],[/^● ([\d.,]+) çalışmıyor$/, '● $1 not working'],[/^◌ ([\d.,]+) test edilmedi$/, '◌ $1 untested'],
  [/^(.+) görevini seç$/, 'Select task: $1'],[/^(.+) görevini incele$/, 'Review task: $1'],[/^(.+) görevini sil$/, 'Delete task: $1'],
  [/^([\d.,]+) ajan seçili$/, '$1 agents selected'],[/^([\d.,]+) dosya seçili$/, '$1 files selected'],[/^([\d.,]+) görev seçili$/, '$1 tasks selected'],
  [/^([\d.,]+) görev silindi$/, '$1 tasks deleted'],[/^([\d.,]+) görev silinsin mi\?$/, 'Delete $1 tasks?'],[/^(.+) silinsin mi\?$/, 'Delete $1?'],
  [/^Kaynak envanteri \((\d+)\)$/, 'Source inventory ($1)'],[/^Yürütme kayıtları \((\d+)\)$/, 'Execution logs ($1)'],
  [/^Her ([\d.,]+) dakikada · Sonraki: (.+)$/, 'Every $1 minutes · Next: $2'],[/^([\d.,]+) satır · (.+)$/, '$1 lines · $2'],
  [/^Bugünkü kullanım: (.+) · Toplam: (.+)$/, "Today's usage: $1 · Total: $2"],
  [/^([a-f0-9]{8}) · 1 ajan( \+ baş uzman)?$/, '$1 · 1 agent$2'],[/^([a-f0-9]{8}) · (\d+) ajan( \+ baş uzman)?$/, '$1 · $2 agents$3'],
  [/^Sağlayıcı HTTP (\d+) döndürdü$/, 'Provider returned HTTP $1'],[/^Dosya boş veya (\d+) MB dosya yükleme sınırını aşıyor$/, 'File is empty or exceeds the $1 MB upload limit'],
  [/^(.+) başladı · (.+)$/, '$1 started · $2'],[/^(.+) sağlayıcı çağrısı başarısız$/, '$1 provider call failed'],
  [/^Görev kuyruğa alındı · (.+)$/, 'Task queued · $1'],[/^([\d.,]+) görev silindi · (.+)$/, '$1 tasks deleted · $2']
 ];
 function t(value,depth=0){
  const text=String(value??'');if(language!=='en'||depth>3)return text;
  const key=text.trim().replace(/\s+/g,' ');let result=catalog[key];
  if(result===undefined){for(const [pattern,replacement] of rules){if(pattern.test(key)){result=key.replace(pattern,replacement).replace(' + baş uzman',' + lead expert');break}}}
  if(result===undefined&&/^\d[\d.,]* giriş \/ /.test(key))result=key.replace('giriş /','input /').replace('çıkış tokenı','output tokens').replace('(tahmini)','(estimated)').replace('Çıkış sınırı:','Output limit:');
  if(result===undefined&&key.includes(' · ')){const parts=key.split(' · '),translated=parts.map(p=>t(p,depth+1));if(parts.some((p,i)=>p!==translated[i]))result=translated.join(' · ')}
  if(result===undefined){const decorated=key.match(/^([·◈⌁✧♜♧♙◇▧✓◷⚙◎◉]\s+)(.+)$/u);if(decorated){const inner=t(decorated[2],depth+1);if(inner!==decorated[2])result=decorated[1]+inner}}
  if(result===undefined)return text;
  return text.replace(text.trim(),result);
 }
 function ignore(node,attribute=false){
  const element=node.nodeType===Node.ELEMENT_NODE?node:node.parentElement;
  if(!element||element.closest('script,style,pre,code,#language-toggle,[data-toggle-language]'))return true;
  if(!attribute&&element.closest('textarea'))return true;
  if(element.closest(excluded))return true;
  if(element.classList.contains('task-title')&&node.nodeType===Node.TEXT_NODE)return true;
  if(element.id==='profile-name'&&node.nodeType===Node.TEXT_NODE)return true;
  if(element.tagName==='OPTION'&&element.value&&element.closest('#project-scope,#judge,#fallback-agent'))return true;
  return false;
 }
 function textNode(node){
  if(ignore(node)||!node.data.trim())return;
  let entry=originals.get(node);if(!entry||node.data!==entry.last)entry={source:node.data,last:node.data};
  let next=t(entry.source);
  if(language==='en'&&['ajan','görev'].includes(entry.source.trim())&&node.parentElement?.closest('#stat-agents,#stat-active,#stat-done')){const n=parseInt(node.parentElement.parentElement.textContent,10);if(n!==1)next=next.replace(/agent\b/,'agents').replace(/task\b/,'tasks')}
  if(node.data!==next)node.data=next;entry.last=next;originals.set(node,entry);
 }
 function elementAttrs(element){
  if(ignore(element,true))return;
  let saved=attrs.get(element)||{};
  for(const name of ['title','aria-label','placeholder']){
   const value=element.getAttribute(name);if(value===null)continue;
   let entry=saved[name];if(!entry||value!==entry.last)entry={source:value,last:value};
   const next=t(entry.source);if(value!==next)element.setAttribute(name,next);entry.last=next;saved[name]=entry;
  }
  attrs.set(element,saved);
 }
 function buttons(){
  const button=document.getElementById('language-toggle');
  if(button){const name=language==='tr'?'Switch to English':'Türkçeye geç';if(button.getAttribute('aria-label')!==name)button.setAttribute('aria-label',name);if(button.title!==name)button.title=name;button.querySelectorAll('[data-language]').forEach(el=>el.classList.toggle('selected',el.dataset.language===language))}
  document.querySelectorAll('[data-toggle-language]').forEach(el=>{const text=language==='tr'?'English':'Türkçe',label=language==='tr'?'Switch to English':'Türkçeye geç';if(el.textContent!==text)el.textContent=text;if(el.getAttribute('aria-label')!==label)el.setAttribute('aria-label',label)});
 }
 function apply(){
  scheduled=false;document.documentElement.lang=language;
  const walk=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT);let node;while((node=walk.nextNode()))textNode(node);
  document.querySelectorAll('[title],[aria-label],[placeholder]').forEach(elementAttrs);
  const counter=document.getElementById('char-count'),prompt=document.getElementById('prompt');if(counter&&prompt){const text=prompt.value.length.toLocaleString(language==='tr'?'tr-TR':'en-US')+' / '+(16000).toLocaleString(language==='tr'?'tr-TR':'en-US');if(counter.textContent!==text)counter.textContent=text}
  document.title=language==='tr'?'ECY Agent Harbor — Ajan Alanı':'ECY Agent Harbor — Agent Workspace';buttons();
 }
 function schedule(){if(!scheduled){scheduled=true;requestAnimationFrame(apply)}}
 function setLanguage(value,persist=true){
  if(!supported(value))return;language=value;document.documentElement.lang=value;
  if(persist)try{localStorage.setItem(storageKey,value)}catch(_){}
  document.dispatchEvent(new CustomEvent('ecy-language-change',{detail:{language}}));apply();
 }
 window.I18n={get language(){return language},t,setLanguage,apply,locale:()=>language==='tr'?'tr-TR':'en-US'};
 window.uiLocale=()=>I18n.locale();window.uiLanguage=()=>I18n.language;
 document.documentElement.lang=language;
 I18n.ready=fetch('/locales/en.json').then(r=>{if(!r.ok)throw Error('locale');return r.json()}).then(data=>{catalog=data;if(document.body)apply()}).catch(()=>{});
 document.addEventListener('DOMContentLoaded',()=>{
  apply();new MutationObserver(schedule).observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['title','aria-label','placeholder']});
  document.addEventListener('click',event=>{const target=event.target.closest('#language-toggle,[data-toggle-language]');if(!target)return;const selected=event.target.closest('[data-language]')?.dataset.language;setLanguage(selected|| (language==='tr'?'en':'tr'))});
 });
 window.addEventListener('storage',e=>{if(e.key===storageKey&&supported(e.newValue))setLanguage(e.newValue,false)});
})();
