import { STAGES,COLUMNS,age,loadEvidence,buildEvidence,filterEvidence } from './evidence-data.js';
import { timestamp, count } from './data.js';
const el=(tag,text,cls)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e;};
const badge=(state,text=state.replaceAll('-',' '))=>el('span',text,`ev-badge ev-${state}`);
const root=document.getElementById('evidence-explorer');
let data, graph, selected='gate', pending;
let filters={scope:'all',model:'all',from:'',to:''};
let selectedDetail;
function line(name,value){const row=el('div');row.append(el('dt',name),el('dd',value??'Unknown'));return row;}
function sourceLink(key){const a=el('a','Inspect pinned source at commit');const path=data.sources[key].path;a.href=`https://github.com/mjfrieden/orographic/blob/${data.source_commit}/web${path}`;a.target='_blank';a.rel='noopener noreferrer';return a;}
function inspect(item,focus=false){
  selected=item.id;selectedDetail=item;const panel=document.getElementById('ev-detail');
  panel.replaceChildren(el('p','Evidence inspector','eyebrow'),el('h3',item.title),badge(item.state),el('p',item.value,'ev-detail-value'));
  const facts=el('dl','', 'ev-facts');facts.tabIndex=0;facts.setAttribute('aria-label','Evidence facts');for(const [k,v]of item.facts)facts.append(line(k,v));
  facts.append(line('Artifact generation',timestamp(data.sources[item.source].generated_at_utc)),line('Source SHA-256',data.sources[item.source].sha256));
  panel.append(facts,sourceLink(item.source));
  if(/^https:\/\/github\.com\/mjfrieden\/orographic\/actions\/runs\/\d+$/.test(item.reviewLink??'')){const a=el('a','Inspect dated failure run');a.href=item.reviewLink;a.target='_blank';a.rel='noopener noreferrer';panel.append(a);}
  const next=el('div',undefined,'ev-next');next.append(el('strong','Next observation'),el('p',item.next));panel.append(next);
  document.querySelectorAll('[data-evidence-id]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.evidenceId===item.id)));
  document.getElementById('ev-announcement').textContent=`Inspecting ${item.title}. ${item.value}.`;
  if(focus)panel.focus();
}
function selectionButton(item,cls){const b=el('button',undefined,cls);b.type='button';b.dataset.evidenceId=item.id;b.setAttribute('aria-pressed',String(selected===item.id));b.setAttribute('aria-controls','ev-detail');b.addEventListener('click',()=>inspect(item,window.matchMedia('(max-width:760px)').matches));return b;}
function renderViews(){
  const nodes=filterEvidence(graph.nodes,filters);const map=document.getElementById('ev-map');map.replaceChildren();
  for(let stage=0;stage<STAGES.length;stage++){
    const column=el('section',undefined,'ev-stage');column.append(el('h3',`${String(stage+1).padStart(2,'0')}  ${STAGES[stage]}`));
    for(const n of nodes.filter(n=>n.stage===stage)){const b=selectionButton(n,'ev-node');b.append(el('small',n.scope==='shared'?'Shared mart':'Orographic'),el('strong',n.title),el('span',n.value,'ev-node-value'),badge(n.state),el('small',`${timestamp(n.date).slice(0,10)} · ${age(n.date).text}`));column.append(b);}
    if(!nodes.some(n=>n.stage===stage))column.append(el('p','No evidence in this view','ev-empty'));map.append(column);
  }
  const body=document.getElementById('ev-matrix-body');body.replaceChildren();
  const rows=graph.matrix.filter(r=>(filters.scope==='all'||r.scope===filters.scope)&&(!filters.from||data.sources[r.source].generated_at_utc?.slice(0,10)>=filters.from)&&(!filters.to||data.sources[r.source].generated_at_utc?.slice(0,10)<=filters.to));
  for(const r of rows){const tr=el('tr');const th=el('th',r.name);th.scope='row';tr.append(th);r.cells.forEach((c,i)=>{const td=el('td');const item={...c,id:`cell-${r.name}-${i}`,title:`${r.name} / ${COLUMNS[i]}`,source:r.source,date:data.sources[r.source].generated_at_utc};const b=selectionButton(item,'ev-cell');b.setAttribute('aria-label',`${item.title}: ${c.value}`);b.append(badge(c.state),el('span',c.value));td.append(b);tr.append(td);});body.append(tr);}
  if(!rows.length){const tr=el('tr');const td=el('td','No family-specific evidence for these filters. Shared mart feature totals do not establish individual family health.');td.colSpan=6;tr.append(td);body.append(tr);}
  document.getElementById('ev-active').textContent=`${filters.scope==='all'?'All evidence':filters.scope==='shared'?'Shared mart':'Orographic runtime'} · ${filters.model==='all'?'All model identities':data.models.find(m=>m.id===filters.model)?.title ?? 'Unknown model'} · Artifact dates ${filters.from||'earliest'} to ${filters.to||'latest'} · ${nodes.length} nodes. Snapshot ${data.snapshot_id.slice(0,12)} fixed.`;
  const current=nodes.find(n=>n.id===selected);
  if(current)inspect(current);else if(nodes.length)inspect(nodes[0]);else {selected='';selectedDetail=null;document.getElementById('ev-detail').replaceChildren(el('h3','No matching evidence'),el('p','Reset filters to return to the pinned snapshot.'));}
  renderDecisions();
}
function renderDecisions(){
  const list=document.getElementById('ev-decisions');list.replaceChildren();
  const records=data.decisions.filter(r=>(filters.scope!=='shared')&&filters.model==='all'&&(!filters.from||r.date.slice(0,10)>=filters.from)&&(!filters.to||r.date.slice(0,10)<=filters.to));
  list.append(el('p',`${records.length} displayed of ${data.decision_total} recent live-lane records. These are separate from the shared-mart cohort. Dates here are decision dates. Model-filtered records are withheld: historical model joins are missing.`, 'muted'));
  for(const record of records){const b=el('button',undefined,'ev-record');b.type='button';b.append(el('strong',record.symbol),el('span',`${record.date.slice(0,10)} · ${record.contract_symbol}`),badge(record.status==='complete'?'available':record.status==='pending'?'pending':'unknown',record.status));b.addEventListener('click',()=>{
    inspect({id:record.id,title:`${record.symbol} / recorded recommendation`,state:record.status==='pending'?'pending':'available',value:'Quote-derived observation',date:record.date,source:'ledger',facts:[
      ['Decision time',timestamp(record.date)],['Decision key',record.id],['Source',record.quote.entry_data_source ?? 'Unknown'],['Quote captured',timestamp(record.quote.captured_at_utc)],['Chain snapshot',timestamp(record.quote.chain_snapshot_at_utc)],['Entry side',record.quote.entry_quote_type ?? 'Unknown'],['Entry ask',typeof record.quote.ask==='number'?String(record.quote.ask):'Unknown'],['Lane',record.lane],['Model and feature lineage','Missing in compact ledger; do not attach current models to historical recommendations.'],['Friday-close quote proxy',typeof record.friday==='number'&&Number.isFinite(record.friday)?`${(record.friday*100).toFixed(2)}%`:'Missing / pending; not zero'],['Costs','Separate fee/slippage and outcome availability clocks are absent in this compact export. Do not call this an after-cost return.'],['Evidence type','Recorded quote proxy, not broker fills or realized account returns.'],['Replay','Inspection only; no time replay because outcome availability clocks are missing.']
    ],next:'Join exact historical feature/model identities, full exit quote and cost receipts before an end-to-end validated replay.'});
  });list.append(b);}
  if(!records.length)list.append(el('p','No recommendations match this view. Missing lineage is kept visible.','ev-empty'));
}
function updateUrl(){const params=new URLSearchParams();params.set('snapshot',data.snapshot_id);for(const[k,v]of Object.entries(filters))if(v && v!=='all')params.set(k,v);history.replaceState(null,'',`${location.pathname}#${params}`);}
function mount(snapshot){
  data=snapshot;graph=buildEvidence(data);root.replaceChildren();
  const params=new URLSearchParams(location.hash.slice(1));
  if(params.get('snapshot')&&params.get('snapshot')!==data.snapshot_id)root.append(el('p','The linked snapshot is unavailable. Showing the current pinned snapshot; this is not the original shared view.','notice'));
  else {for(const key of ['scope','model','from','to'])if(params.has(key))filters[key]=params.get(key);}
  if(!['all','shared','runtime'].includes(filters.scope))filters.scope='all';
  if(filters.model!=='all'&&!data.models.some(m=>m.id===filters.model))filters.model='all';
  for(const key of ['from','to'])if(!/^\d{4}-\d{2}-\d{2}$/.test(filters[key]))filters[key]='';
  const title=el('div',undefined,'ev-heading');const intro=el('div');intro.append(el('p','01 / Evidence workspace','eyebrow'),el('h2','Follow the evidence'),el('p','Select a stage or health cell to inspect what is known, what is missing, and the next observation.','muted'));
  const local=['localhost','127.0.0.1','::1'].includes(location.hostname);
  title.append(intro,badge('local-only',local?'Local visual slice':'Served visual slice'));root.append(title);
  const clocks=el('div',undefined,'ev-clocks');
  for(const [name,key] of [['Shared mart','sync'],['Runtime receipt','runtime']]){const clock=el('div');const a=age(data.sources[key].generated_at_utc);clock.append(el('span',name),el('strong',timestamp(data.sources[key].generated_at_utc).slice(0,10)),badge(a.state,a.text));clocks.append(clock);}
  const identity=el('div');identity.append(el('span','Snapshot identity'),el('strong',data.snapshot_id.slice(0,12)),el('small','Fixed while exploring · 48h age threshold'));clocks.append(identity);root.append(clocks);
  const controls=el('div',undefined,'ev-controls');
  function select(label,key,options){const l=el('label',label);const s=el('select');s.id=`ev-${key}`;for(const[v,t]of options){const o=el('option',t);o.value=v;s.append(o);}s.value=filters[key];s.addEventListener('change',()=>{filters[key]=s.value;updateUrl();renderViews();});l.append(s);controls.append(l);}
  select('Evidence cohort','scope',[['all','All evidence'],['shared','Shared mart / Oct 16'],['runtime','Orographic runtime / Oct 29']]);
  select('Model identity','model',[['all','All model identities'],...data.models.map(m=>[m.id,m.title])]);
  for(const[key,name]of [['from','Artifact date from (UTC)'],['to','Artifact date to (UTC)']]){const l=el('label',name);const input=el('input');input.type='date';input.value=filters[key];input.addEventListener('change',()=>{filters[key]=input.value;updateUrl();renderViews();});input.id=`ev-${key}`;l.append(input);controls.append(l);}
  const reset=el('button','Reset view');reset.type='button';reset.addEventListener('click',()=>{filters={scope:'all',model:'all',from:'',to:''};history.replaceState(null,'',location.pathname);mount(data);});controls.append(reset);root.append(controls);
  const active=el('p',undefined,'ev-active');active.id='ev-active';active.setAttribute('role','status');root.append(active);
  root.append(el('p','Stage order describes documented pipeline design, not causation or verified row-level joins. Shared and runtime evidence stay separate. Model filters select identities; they do not imply model-specific feature coverage.','ev-caption'));
  const workspace=el('div',undefined,'ev-workspace');const canvas=el('div',undefined,'ev-canvas');
  const map=el('div',undefined,'ev-map');map.id='ev-map';map.setAttribute('aria-label','Evidence flow by stage');canvas.append(map);
  const matrixHead=el('div',undefined,'ev-matrix-heading');matrixHead.append(el('h3','Feature-health matrix'),el('p','Presence is not predictive contribution. Every cell opens its evidence.','muted'));canvas.append(matrixHead);
  const wrap=el('div',undefined,'ev-table-wrap');wrap.tabIndex=0;wrap.setAttribute('aria-label','Scrollable feature health table');const table=el('table',undefined,'ev-matrix');const caption=el('caption','Orographic feature families · distinct availability and evaluation states');table.append(caption);const head=el('thead');const header=el('tr');for(const title of ['Feature family',...COLUMNS]){const th=el('th',title);th.scope='col';header.append(th);}head.append(header);table.append(head);const body=el('tbody');body.id='ev-matrix-body';table.append(body);wrap.append(table);canvas.append(wrap);
  canvas.append(el('h3','Recorded decisions / outcome inspection'));const decisions=el('div');decisions.id='ev-decisions';canvas.append(decisions);
  const panel=el('aside',undefined,'ev-detail');panel.id='ev-detail';panel.tabIndex=-1;panel.setAttribute('aria-label','Selected evidence');workspace.append(canvas,panel);root.append(workspace);
  const announcement=el('p',undefined,'sr-only');announcement.id='ev-announcement';announcement.setAttribute('aria-live','polite');root.append(announcement);
  const footer=el('div',undefined,'ev-footer');footer.append(el('p',`Evidence source commit ${data.source_commit.slice(0,12)}. ${local?'This is a local preview.':'This page is served, but this snapshot does not attest its deployment commit.'} Operational runtime identity remains unverified by this snapshot. Snapshot packaging does not refresh source clocks.`));
  const check=el('button','Check for newer snapshot');const apply=el('button','Load newer snapshot');apply.hidden=true;
  check.addEventListener('click',async()=>{check.disabled=true;try{const next=await loadEvidence();if(next.snapshot_id!==data.snapshot_id){pending=next;apply.hidden=false;check.textContent='New snapshot available — current view stays fixed';}else check.textContent='Pinned snapshot is unchanged';}catch{check.textContent='Update check failed — pinned view retained';}finally{check.disabled=false;}});
  apply.addEventListener('click',()=>{if(pending){filters={scope:'all',model:'all',from:'',to:''};history.replaceState(null,'',location.pathname);mount(pending);pending=null;}});footer.append(check,apply);root.append(footer);renderViews();
}
async function start(){const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),15000);try{mount(await loadEvidence(controller.signal));}catch(error){root.replaceChildren(el('h2','Evidence workspace unavailable'),el('p',error.message),el('p','No inferred values are displayed. Existing source inspectors remain below.'));const retry=el('button','Retry evidence snapshot');retry.addEventListener('click',start);root.append(retry);}finally{clearTimeout(timer);}}
if(root)start();
