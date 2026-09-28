// Execute worksheet logic against a minimal DOM, without opening a browser.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const html = fs.readFileSync(process.argv[2], 'utf8');
const plan = JSON.parse(html.match(/<script id="plan" type="application\/json">([\s\S]*?)<\/script>/)[1]);
const script = html.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
const elements = {}, saved = {}, blobs = [];
function element(id) {
  return elements[id] ||= {value:'',checked:false,textContent:'',listeners:{},
    addEventListener(name, fn){this.listeners[name]=fn;}, replaceChildren(){}, append(){}, click(){}};
}
element('plan').textContent=JSON.stringify(plan);
const context = vm.createContext({document:{getElementById:element,createElement:()=>element('created')},
  window:{addEventListener(){}},localStorage:{getItem:k=>saved[k],setItem:(k,v)=>{saved[k]=v;}},
  navigator:{clipboard:{writeText:async()=>{}}},confirm:()=>false,
  Blob, URL:class extends URL {static createObjectURL(b){blobs.push(b);return 'blob:test';}static revokeObjectURL(){}},
  setTimeout:fn=>fn(),console});
vm.runInContext(script, context);
assert.equal(element('prompt').textContent,plan.jobs[0].prompt);
element('answer').value='Smiles Dental is an option.';
element('answer').listeners.input();
element('model').value='Test model';
element('search').value='yes';
element('urls').value='https://smilesdental.example/';
element('complete').checked=true;
element('form').onsubmit({preventDefault(){}});
assert.match(element('message').textContent,/confirm that you reviewed/);
element('reviewed').checked=true;
element('form').onsubmit({preventDefault(){}});
assert.match(element('progress').textContent,/1 \/ 2 samples saved/);
element('download').onclick();
assert.equal(blobs.length,1);
(async()=>{
  const bundle=JSON.parse(await blobs[0].text());
  assert.equal(bundle.observations[0].answer_text,'Smiles Dental is an option.');
  assert.equal(bundle.observations[0].cited_urls[0],'https://smilesdental.example/');
  assert.equal(bundle.observations[0].prompt,plan.jobs[0].prompt);
  element('next').onclick();
  assert.equal(element('prompt').textContent,plan.jobs[1].prompt);
  element('restore').files=[{text:async()=>JSON.stringify(bundle)}];
  await element('restore').onchange();
  assert.match(element('message').textContent,/Restored capture file/);
  console.log('capture page prepare/save/download/restore passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
