/* Language preference tests execute the actual browser module without network/model calls. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'..'),source=fs.readFileSync(path.join(root,'static/i18n.js'),'utf8'),catalog=JSON.parse(fs.readFileSync(path.join(root,'static/locales/en.json'),'utf8'));
async function boot(browserLanguage,saved,unavailable=false){
 const values=new Map(saved===undefined?[]:[['ecy-harbor-language',saved]]);
 const document={body:{},documentElement:{},title:'',addEventListener(){},dispatchEvent(){},getElementById(){return null},querySelectorAll(){return[]},createTreeWalker(){return{nextNode(){return null}}}};
 const context={document,navigator:{language:browserLanguage},localStorage:{getItem(k){if(unavailable)throw Error('storage unavailable');return values.get(k)||null},setItem(k,v){if(unavailable)throw Error('storage unavailable');values.set(k,v)}},fetch:async()=>({ok:true,json:async()=>catalog}),NodeFilter:{SHOW_TEXT:4},CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail}},addEventListener(){},requestAnimationFrame(fn){fn()},console};
 context.window=context;vm.createContext(context);vm.runInContext(source,context);await context.I18n.ready;return{context,values};
}
(async()=>{
 for(const locale of ['tr','tr-TR','TR-tr'])assert.equal((await boot(locale)).context.I18n.language,'tr');
 for(const locale of ['en-US','de-DE','fr-FR','',undefined])assert.equal((await boot(locale)).context.I18n.language,'en');
 assert.equal((await boot('tr-TR','en')).context.I18n.language,'en');assert.equal((await boot('en-US','tr')).context.I18n.language,'tr');
 assert.equal((await boot('de-DE','invalid')).context.I18n.language,'en');assert.equal((await boot('tr-TR',undefined,true)).context.I18n.language,'tr');
 const {context:c,values}=await boot('en-US');assert.equal(c.I18n.t('Ajan Alanı'),'Agent Workspace');assert.equal(c.I18n.t('Görev stüdyosu'),'Task studio');assert.equal(c.I18n.t('2 dosya seçili'),'2 files selected');
 const privateText='Özgün kullanıcı kaydı: 401 / benim ajanım';assert.equal(c.I18n.t(privateText),privateText);
 c.I18n.setLanguage('tr');assert.equal(c.document.documentElement.lang,'tr');assert.equal(c.I18n.t('Görev stüdyosu'),'Görev stüdyosu');assert.equal(values.get('ecy-harbor-language'),'tr');
 c.I18n.setLanguage('en');assert.equal(c.uiLocale(),'en-US');assert.equal(c.I18n.t('● 2 çalışıyor'),'● 2 working');assert.equal(c.I18n.t(' · Değerlendirme'),' · Analysis');assert.equal(c.I18n.t('Bugünkü kullanım: $1 · Toplam: $2'),"Today's usage: $1 · Total: $2");
 c.I18n.setLanguage('invalid');assert.equal(c.I18n.language,'en');
 console.log('Language preference, persistence, fallback and translation tests passed.');
})().catch(error=>{console.error(error);process.exitCode=1});
