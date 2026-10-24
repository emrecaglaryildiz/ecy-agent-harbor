(() => {
 const key='agent-harbor-theme';
 let saved=null;
 try{saved=localStorage.getItem(key);}catch(error){}
 const initial=['light','dark'].includes(saved)?saved:(matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light');
 document.documentElement.dataset.theme=initial;
 function updateButton(){
  const button=document.getElementById('theme-toggle');if(!button)return;
  const dark=document.documentElement.dataset.theme==='dark';
  button.textContent=dark?'☀':'☾';
  button.setAttribute('aria-label',dark?'Açık temaya geç':'Karanlık temaya geç');
  button.setAttribute('aria-pressed',String(dark));button.title=dark?'Açık tema':'Karanlık tema';
 }
 document.addEventListener('DOMContentLoaded',()=>{
  updateButton();
  document.getElementById('theme-toggle').addEventListener('click',()=>{
   const next=document.documentElement.dataset.theme==='dark'?'light':'dark';
   document.documentElement.dataset.theme=next;
   try{localStorage.setItem(key,next);}catch(error){}
   updateButton();
  });
 });
})();
