function renderMarkdown(source){
 if(!window.marked||!window.DOMPurify)return '<p>'+String(source??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))+'</p>';
 const cited=String(source??'').replace(/\[dosya:([a-f0-9]{32}):L(\d+)-L(\d+)\]/g,(_,id,start,end)=>`[Kaynak L${start}–L${end}](/api/files/${id}?start=${start})`);
 const html=marked.parse(cited,{gfm:true,breaks:true});
 const fragment=DOMPurify.sanitize(html,{
  RETURN_DOM_FRAGMENT:true,
  ALLOWED_TAGS:['p','br','hr','h1','h2','h3','h4','h5','h6','strong','em','del','s','ul','ol','li','blockquote','pre','code','a','img','table','thead','tbody','tr','th','td','input'],
  ALLOWED_ATTR:['href','title','src','alt','class','align','start','type','checked','disabled'],
  ALLOW_DATA_ATTR:false,ALLOW_ARIA_ATTR:false
 });
 for(const link of fragment.querySelectorAll('a')){
  try{
   const url=new URL(link.getAttribute('href'),location.href);
   if(!['http:','https:','mailto:'].includes(url.protocol))link.removeAttribute('href');
   else{link.target='_blank';link.rel='noopener noreferrer';}
  }catch(error){link.removeAttribute('href');}
 }
 for(const image of fragment.querySelectorAll('img')){
  const source=image.getAttribute('src')||'';
  try{
   const url=new URL(source,location.href);
   if(!['https:','http:'].includes(url.protocol)&&!/^data:image\/(png|jpeg|webp|gif);base64,/i.test(source)){
    image.replaceWith(document.createTextNode(image.alt||'[Görsel]'));continue;
   }
   image.loading='lazy';image.decoding='async';image.referrerPolicy='no-referrer';
  }catch(error){image.replaceWith(document.createTextNode(image.alt||'[Görsel]'));}
 }
 for(const input of fragment.querySelectorAll('input')){input.type='checkbox';input.disabled=true;}
 for(const table of fragment.querySelectorAll('table')){
  const wrapper=document.createElement('div');wrapper.className='markdown-table-wrap';
  table.parentNode.insertBefore(wrapper,table);wrapper.appendChild(table);
 }
 const container=document.createElement('div');container.appendChild(fragment);return container.innerHTML;
}

function finishMessage(run){
 if(run.finish_reason==='empty_response')return run.can_resume_from_evidence?'Sağlayıcı rapor metni döndürmedi. Kayıtlı kaynakları kullanarak aramaları tekrarlamadan rapor üretebilirsin.':'Sağlayıcı rapor metni döndürmedi. Bu görev tamamlanmadı; raporu yeniden üretebilirsin.';
 const reasons={refusal:'Sağlayıcı isteği reddetti.',length:'Sağlayıcı yanıtı çıkış token sınırında kesti.',max_tokens:'Sağlayıcı yanıtı çıkış token sınırında kesti.',
  max_output_tokens:'Yanıt çıkış sınırına ulaştı.',content_filter:'Sağlayıcı içerik filtresi yanıtı durdurdu.',
  tool_calls:'Sağlayıcı araç çağrısı istedi; bu uygulama model araçlarını doğrudan çalıştırmaz.',
  legacy_length_suspected:'Eski 1.500 token sınırına ulaşılmış. Bu yanıt yarıda kalmış olabilir.'};
 return reasons[run.finish_reason]||'Sağlayıcı yanıtı tamamlandığını bildirmedi.';
}

document.addEventListener('click',async event=>{
 const button=event.target.closest('[data-continue-run]');if(!button)return;
 button.disabled=true;
 try{
  const result=await api('tasks/'+button.dataset.continueTask+'/continue',{run_id:button.dataset.continueRun});
  await refresh();await openTask(result.id);toast('Devam yanıtı için yeni görev başlatıldı');
 }catch(error){toast(error.message);button.disabled=false;}
});
