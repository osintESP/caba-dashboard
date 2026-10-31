const state={stats:null,editions:[],norms:[],procurements:[],intelligence:null,sync:null,bac:null,aperturas:null,normativeImpact:null,pliegosCaidos:null,recurringVendorWatch:null};
const $=id=>document.getElementById(id);
const fmt=new Intl.NumberFormat('es-AR');
const fmtCurrency=new Intl.NumberFormat('es-AR',{style:'currency',currency:'ARS',maximumFractionDigits:0});
const PAGE_SIZE=25;
let normsPage=1,procPage=1,pliegosPage=1,impactPage=1;
const dtf=new Intl.DateTimeFormat('es-AR',{timeZone:'America/Argentina/Buenos_Aires',dateStyle:'short',timeStyle:'medium'});
async function fetchJson(path){const r=await fetch(path,{cache:'no-store'});if(!r.ok)throw new Error(`${path}: HTTP ${r.status}`);return r.json()}
async function fetchOptional(path){try{return await fetchJson(path)}catch(e){console.warn(`optional dataset unavailable: ${path}`,e);return null}}
// Una página HTML por sección; cada una baja SÓLO los datasets que usa (norms.json solo pesa
// ~18MB: antes cada visita bajaba ~26MB aunque se mirara sólo el resumen). La página se
// identifica por <body data-page>. Para agregar una sección: sumarla acá y crear su .html.
const PAGES={
  hoy:{file:'index.html',label:'Hoy',data:['stats','editions','sync','aperturas','pliegosCaidos','recurringVendorWatch','bac','normativeImpact'],render:['metrics','syncNotice','hoy','novedades']},
  licitaciones:{file:'licitaciones.html',label:'Licitaciones',data:['sync','aperturas','presupuestos'],render:['aperturas']},
  senales:{file:'senales.html',label:'Señales para revisar',data:['sync','presupuestos','bac','recurringVendorWatch','pliegosCaidos','normativeImpact'],render:['presupuestos','bac','recurringVendorWatch','pliegosCaidos','normativeImpact']},
  normativa:{file:'normativa.html',label:'Normativa de sistemas',data:['sync','normativeImpact'],render:['normativeImpact']},
  panorama:{file:'panorama.html',label:'Panorama',data:['sync','intelligence','bac','bacSyncState'],render:['intelligence','bac']},
  explorar:{file:'explorar.html',label:'Explorar',data:['sync','norms','procurements','bac'],render:['bars','orgRanking','lists']},
};
const DATASETS={stats:['data/stats.json',true],editions:['data/editions.json',true],norms:['data/norms.json',true],procurements:['data/procurements.json',true],intelligence:['data/procurement_intelligence.json'],sync:['data/sync_manifest.json'],bac:['data/bac_catalog.json'],bacSyncState:['data/bac_sync_state.json'],aperturas:['data/bac_aperturas.json'],normativeImpact:['data/normative_impact.json'],pliegosCaidos:['data/bac_pliegos_caidos.json'],recurringVendorWatch:['data/bac_recurring_vendor_watch.json'],presupuestos:['data/bac_presupuestos.json']};
const PAGE=PAGES[document.body.dataset.page]?document.body.dataset.page:'hoy';
async function loadData(){const keys=PAGES[PAGE].data;const values=await Promise.all(keys.map(k=>{const[path,required]=DATASETS[k];return required?fetchJson(path):fetchOptional(path)}));keys.forEach((k,i)=>{state[k]=values[i]});$('data-status').textContent='Datos conectados';$('data-status').className='status status-ok'}
function fillSelect(el,values){for(const v of values){const o=document.createElement('option');o.value=v;o.textContent=v;el.appendChild(o)}}
function populateFilters(){fillSelect($('filter-org'),[...new Set(state.norms.map(x=>x.organismo).filter(Boolean))].sort());fillSelect($('filter-type'),[...new Set(state.norms.map(x=>x.tipo).filter(Boolean))].sort());fillSelect($('filter-category'),[...new Set(state.procurements.map(x=>x.categoria).filter(Boolean))].sort())}
function filteredNorms(){const q=$('filter-search').value.trim().toLowerCase(),org=$('filter-org').value,type=$('filter-type').value;return state.norms.filter(n=>{const hay=`${n.nombre||''} ${n.sumario||''} ${n.organismo||''} ${n.tipo||''}`.toLowerCase();return(!q||hay.includes(q))&&(!org||n.organismo===org)&&(!type||n.tipo===type)}).sort((a,b)=>(Number(b.id_norma)||0)-(Number(a.id_norma)||0))}
function filteredProcurements(){const q=$('filter-search').value.trim().toLowerCase(),org=$('filter-org').value,type=$('filter-type').value,cat=$('filter-category').value;return state.procurements.filter(n=>{const hay=`${n.nombre||''} ${n.sumario||''} ${n.organismo||''} ${n.tipo||''} ${n.categoria||''}`.toLowerCase();return(!q||hay.includes(q))&&(!org||n.organismo===org)&&(!type||n.tipo===type)&&(!cat||n.categoria===cat)})}
function uniqueProcessCount(rows){return new Set(rows.map(x=>x.proceso_id||x.id_norma)).size}
function groupByProceso(rows){const groups=new Map();for(const r of rows){const key=r.proceso_id||r.id_norma;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(r)}
  // Cada proceso puede acumular actos a lo largo de meses (llamado, circulares, prórroga...);
  // se ordena por el acto MÁS RECIENTE de cada proceso, no por cuándo arrancó, para que la
  // actividad reciente quede arriba. El orden interno del grupo no se toca (el primer acto
  // publicado -normalmente el "Llamado", con el resumen más descriptivo- sigue eligiéndose
  // como principal en procesoGroupHtml).
  return[...groups.values()].sort((a,b)=>Math.max(...b.map(x=>Number(x.id_norma)||0))-Math.max(...a.map(x=>Number(x.id_norma)||0)))}
function normalizeOrgName(name){return String(name||'').normalize('NFKD').replace(/[\u0300-\u036f]/g,'').replace(/\./g,'').replace(/\s+/g,' ').trim().toUpperCase()}
function renderMetrics(){const latest=[...state.editions].sort((a,b)=>Number(b.numero_boletin)-Number(a.numero_boletin))[0];$('metric-bulletin').textContent=latest?`N.º ${latest.numero_boletin}`:'—';$('metric-bulletin-date').textContent=latest?.fecha_publicacion||'—';$('metric-norms').textContent=fmt.format(state.stats?.norms??state.norms.length);$('metric-procurements').textContent=fmt.format(state.stats?.procurements??uniqueProcessCount(state.procurements));$('metric-editions').textContent=fmt.format(state.stats?.editions??state.editions.length);$('data-generated-at').textContent=state.stats?.generated_at?`Datos generados ${dtf.format(new Date(state.stats.generated_at))} ART`:'histórico disponible';}
function renderHeaderSync(){const syncAt=state.sync?.public_synced_at||state.sync?.published_at;$('dashboard-sync-at').textContent=syncAt?`Dashboard sincronizado ${dtf.format(new Date(syncAt))} ART`:'Dashboard sin sincronización automática registrada'}
function renderIntelligence(){const s=state.intelligence?.summary;if(!s){$('intel-status').textContent='sin sincronizar';$('metric-tech').textContent='—';$('metric-cyber').textContent='—';$('intel-tags').innerHTML='<div class="empty">La capa Intelligence todavía no fue publicada en el repositorio público.</div>';return}$('intel-status').textContent='activo';$('metric-tech').textContent=fmt.format(s.technology_related||0);$('metric-cyber').textContent=fmt.format(s.cybersecurity_related||0);const rows=Object.entries(s.tag_counts||{}).sort((a,b)=>b[1]-a[1]),max=rows[0]?.[1]||1;$('intel-tags').innerHTML=rows.length?rows.map(([k,v])=>`<div class="bar-row"><div class="bar-label">${esc(k.replaceAll('_',' '))}</div><div class="bar-track"><div class="bar-fill" style="width:${Math.max(4,v/max*100)}%"></div></div><div class="bar-value">${v}</div></div>`).join(''):'<div class="empty">Sin etiquetas tecnológicas.</div>'}
function renderRankRows(container,rows){container.innerHTML=rows.map(([name,value,badge],i)=>`<div class="rank-row"><span class="rank-index">${i+1}</span><span class="rank-name">${esc(name)}${badge||''}</span><span class="rank-value">${value}</span></div>`).join('')||'<div class="empty">Sin resultados.</div>'}
function pliegoCaidoRecordHtml(p){
  const fecha=p.fecha_apertura?dtf.format(new Date(p.fecha_apertura)):'Fecha no disponible';
  const pills=[p.organismo,p.tipo_proceso,p.numero_proceso].filter(Boolean).map(v=>`<span class="meta-pill">${esc(String(v))}</span>`);
  return `<article class="record"><div><h3 class="record-title">${esc(p.nombre_proceso||p.numero_proceso||'Proceso sin título')} <span class="badge audit-flag">${esc(p.estado||'—')}</span></h3><div class="record-meta">${pills.join('')}</div><p class="record-summary">Fecha de apertura: ${esc(fecha)} ART.</p></div></article>`;
}
function renderPliegosCaidos(){
  const data=state.pliegosCaidos,el=$('bac-failed-tenders'),countEl=$('novedad-failed-count');
  const items=data?.pliegos||[];
  const shown=items.slice(0,pliegosPage*PAGE_SIZE);
  if(el)el.innerHTML=items.length?shown.map(pliegoCaidoRecordHtml).join(''):'<div class="empty">Sin pliegos caídos detectados.</div>';
  setLoadMore('pliegos-load-more',shown.length,items.length);
  setText('count-pliegos',items.length>shown.length?`${fmt.format(shown.length)} de ${fmt.format(items.length)}`:fmt.format(items.length));
  if(countEl)countEl.textContent=`${fmt.format(items.length)} detectado${items.length===1?'':'s'}`;
}
function recurringVendorRecordHtml(c){
  const a=c.confirmed_recent_award;
  const pills=[c.organismo,`Conocido: ${c.known_awards_count} adjudicaciones hasta ${c.known_date_to||'—'}`,a?.numero_proceso].filter(Boolean).map(v=>`<span class="meta-pill">${esc(String(v))}</span>`);
  const fecha=a?.fecha_apertura?dtf.format(new Date(a.fecha_apertura)):null;
  const summary=a?`Confirmado: ${esc(a.proveedor_confirmado||c.vendor)} volvió a ganar por contratación directa${fecha?` (apertura ${esc(fecha)} ART)`:''}${a.monto?`, monto ${esc(a.monto)}`:''}.`:'';
  return `<article class="record"><div><h3 class="record-title">${esc(c.vendor)} <span class="badge audit-flag">Patrón vigente</span></h3><div class="record-meta">${pills.join('')}</div><p class="record-summary">${summary}</p></div></article>`;
}
function renderRecurringVendorWatch(){
  const data=state.recurringVendorWatch,el=$('bac-recurring-vendor-watch'),countEl=$('novedad-recurring-vendor-count');
  const items=(data?.candidates||[]).filter(c=>c.confirmed_recent_award);
  if(el)el.innerHTML=items.length?items.map(recurringVendorRecordHtml).join(''):'<div class="empty">Sin patrones confirmados por ahora.</div>';
  if(countEl)countEl.textContent=`${fmt.format(items.length)} confirmado${items.length===1?'':'s'}`;
  setText('count-recurrente',fmt.format(items.length));
}
function renderBAC(data,syncState){
  const statusEl=$('bac-status'),summaryEl=$('bac-summary'),rankingEl=$('vendor-ranking'),concEl=$('bac-concentration'),fracEl=$('bac-fractionation'),repeatEl=$('bac-repeat-winner');
  const panel=statusEl?.closest('.panel')||statusEl?.parentElement;
  if(!data){if(panel)panel.style.display='none';return}
  if(panel)panel.style.display='';
  const okStates=['ok','unchanged'];
  if(statusEl)statusEl.textContent=okStates.includes(data.status)?'activo':(data.status==='resource_not_found'||data.status==='download_error'||data.status==='schema_unexpected')?'error':'pendiente';
  const s=data.summary||{},audit=data.audit_signals||{},cov=data.coverage||{},nco=audit.non_competitive_open_tenders||{};
  const concentration=audit.vendor_concentration_by_organismo||[];
  const highConc=concentration.filter(c=>c.high_concentration).length;
  const checkedAt=syncState?.checked_at;
  if(summaryEl)summaryEl.innerHTML=`
    <div>Dataset actualizado: ${esc(data.dataset?.metadata_modified||data.collected_at||'—')}</div>
    ${checkedAt?`<div>Última verificación contra la fuente: ${esc(checkedAt)}${syncState?.status==='unchanged'?' (sin cambios desde la última descarga)':''}</div>`:''}
    <div>Cobertura de releases procesados: ${esc(cov.date_from||'—')} a ${esc(cov.date_to||'—')} (${fmt.format(cov.releases_processed||0)} releases — ver nota de cobertura)</div>
    ${cov.note?`<div class="bac-note">${esc(cov.note)}</div>`:''}
    <div>Monto total adjudicado: ${fmtCurrency.format(s.total_awarded_ars||0)} · en tecnología: ${fmtCurrency.format(s.tech_awarded_ars||0)} (${s.tech_share_pct||0}%)</div>
    ${s.fx?.usd_awards_converted?`<div class="bac-note">Incluye ${fmt.format(s.fx.usd_awards_converted)} adjudicaciones en dólares (USD ${fmt.format(Math.round(s.fx.usd_amount||0))}, de los cuales USD ${fmt.format(Math.round(s.fx.usd_tech_amount||0))} en tecnología) convertidas a pesos a la cotización oficial del BCRA del día de cada adjudicación.</div>`:''}
    ${Object.keys(s.fx?.excluded_awards_by_currency||{}).length?`<div class="bac-note">Atención: ${esc(Object.entries(s.fx.excluded_awards_by_currency).map(([k,v])=>`${v} adjudicaciones en ${k}`).join(', '))} quedaron fuera de los totales por falta de cotización.</div>`:''}
    <div>Contratación directa/limitada en tecnología: ${audit.direct_or_limited_share_pct||0}% del monto</div>
    <div>Licitaciones públicas sin competencia real (tecnología): ${fmt.format(nco.count||0)} procesos, ${fmtCurrency.format(nco.amount_ars||0)} (${nco.share_pct_of_tech||0}% del monto en tecnología)</div>
    ${nco.note?`<div class="bac-note">${esc(nco.note)}</div>`:''}
    <div>Organismos con alta concentración de proveedor (&ge;60% en un solo vendor): ${fmt.format(highConc)}</div>
    ${audit.possible_fractionation?.note?`<div class="bac-note">${esc(audit.possible_fractionation.note)}</div>`:''}
    ${audit.repeat_winner_across_organismos?.note?`<div class="bac-note">${esc(audit.repeat_winner_across_organismos.note)}</div>`:''}
  `;
  if(rankingEl)renderRankRows(rankingEl,(data.vendor_ranking_tech||[]).slice(0,8).map(v=>[v.name,fmtCurrency.format(v.amount_ars)]));
  if(concEl){
    const rows=[...concentration].sort((a,b)=>(b.organismo_tech_amount_ars||0)-(a.organismo_tech_amount_ars||0)).map(c=>{
      const label=`${c.high_concentration?'⚠ ':''}${c.top_vendor_share_pct}% en "${c.top_vendor}"`;
      const badge=` <span class="badge${c.high_concentration?' audit-flag':''}">${esc(label)}</span>`;
      return[c.organismo,fmtCurrency.format(c.organismo_tech_amount_ars||0),badge];
    });
    renderRankRows(concEl,rows);
  }
  setText('count-concentracion',highConc===concentration.length?`${fmt.format(highConc)} con ≥60%`:`${fmt.format(concentration.length)} organismos · ${fmt.format(highConc)} con ≥60%`);
  setText('count-fraccionamiento',fmt.format((audit.possible_fractionation?.awards||[]).length));
  setText('count-multiorganismo',fmt.format((audit.repeat_winner_across_organismos?.vendors||[]).length));
  if(fracEl){
    const flags=audit.possible_fractionation?.awards||[];
    const rows=flags.map(f=>[`${f.organismo} · ${f.vendor}`,fmtCurrency.format(f.total_amount_ars||0),` <span class="badge audit-flag">${f.awards_count} adjudicaciones en ${f.window_days}d</span>`]);
    renderRankRows(fracEl,rows);
  }
  if(repeatEl){
    const flags=audit.repeat_winner_across_organismos?.vendors||[];
    const rows=flags.map(f=>{
      const title=`Organismos: ${f.organismos.join(', ')}`;
      return[f.vendor,fmtCurrency.format(f.total_amount_ars||0),` <span class="badge audit-flag" title="${attr(title)}">${f.organismos_count} organismos en ${f.window_days}d</span>`];
    });
    renderRankRows(repeatEl,rows);
  }
}
function renderSyncNotice(){const el=$('sync-notice');if(!el)return;if(!state.sync){el.innerHTML='<strong>Estado de los datos.</strong> Datos base operativos. Pendiente: registro de sincronización.';return}const bulletin=state.sync.latest_bulletin?` Boletín N.º ${esc(state.sync.latest_bulletin)} sincronizado.`:'';el.innerHTML=`<strong>Estado de los datos.</strong> Datos sincronizados.${bulletin}`}
function renderBars(){const rowsSource=filteredProcurements();const seen=new Set(),uniqueRows=[];for(const x of rowsSource){const pid=x.proceso_id||x.id_norma;if(seen.has(pid))continue;seen.add(pid);uniqueRows.push(x)}const counts={};for(const x of uniqueRows)counts[x.categoria||'otros']=(counts[x.categoria||'otros']||0)+1;const rows=Object.entries(counts).sort((a,b)=>b[1]-a[1]),max=rows[0]?.[1]||1;$('procurement-total').textContent=fmt.format(uniqueRows.length);$('category-bars').innerHTML=rows.length?rows.map(([k,v])=>`<div class="bar-row"><div class="bar-label">${esc(k.replaceAll('_',' '))}</div><div class="bar-track"><div class="bar-fill" style="width:${Math.max(4,v/max*100)}%"></div></div><div class="bar-value">${v}</div></div>`).join(''):'<div class="empty">Sin resultados.</div>'}
function bacOrgIndex(){const list=state.bac?.audit_signals?.vendor_concentration_by_organismo||[];const idx=new Map();for(const c of list)idx.set(normalizeOrgName(c.organismo),c);return idx}
function orgAuditBadge(organismo,idx){const c=idx.get(normalizeOrgName(organismo));if(!c)return'';const label=c.high_concentration?`⚠ BAC: ${c.top_vendor_share_pct}% concentrado en "${c.top_vendor}"`:`BAC: ${c.top_vendor_share_pct}% en "${c.top_vendor}"`;return` <span class="badge audit-flag" title="${attr(label)}">⚠ BAC</span>`}
// Cruce por EXPEDIENTE (más preciso que bacOrgIndex, que sólo cruza por nombre de organismo):
// bac_tender_id se extrae en data_model.py del propio sumario del Boletín cuando cita el
// número de proceso de BAC como texto libre; tender_index (bac_catalog.json) lo agrega por
// proceso, acotado a tecnología. Cobertura parcial -no todo sumario cita ese ID-, así que la
// ausencia de badge no implica ausencia de cruce en BAC, sólo que no se pudo confirmar acá.
function bacTenderIndex(){return state.bac?.tender_index||{}}
function tenderAuditBadge(tenderId){const t=tenderId&&bacTenderIndex()[tenderId];if(!t)return'';const method=({direct:'directa',limited:'limitada',open:'pública'})[t.method]||t.method||'método desconocido';const comp=t.competitive===false?', sin competencia real':'';const label=`BAC ${tenderId}: ${fmtCurrency.format(t.amount_ars||0)}${t.amount_usd?` (incluye USD ${fmt.format(Math.round(t.amount_usd))} convertidos)`:''} adjudicados a ${t.suppliers.join(', ')||'proveedor sin identificar'} (contratación ${method}${comp})`;return` <span class="badge audit-flag" title="${attr(label)}">⚠ BAC expediente</span>`}
function renderOrgRanking(){const counts={};for(const x of filteredNorms()){const k=x.organismo||'Sin organismo';counts[k]=(counts[k]||0)+1}const idx=bacOrgIndex();const sorted=Object.entries(counts).sort((a,b)=>b[1]-a[1]);const top=sorted.slice(0,8);const topNames=new Set(top.map(([name])=>name));
  // No cortar el top-8 (por cantidad de normas del Boletín) antes de mirar los hallazgos de BAC:
  // un organismo con alta concentración de proveedor puede publicar pocas normas y quedar afuera
  // del top pese a ser el hallazgo más severo del cruce (bug real: Vicejefatura de Gobierno,
  // $493M/100% en un proveedor, quedaba invisible por tener pocas normas en el Boletín).
  const flagged=sorted.filter(([name])=>!topNames.has(name)&&idx.get(normalizeOrgName(name))?.high_concentration);
  const rows=[...top,...flagged].map(([name,value])=>[name,value,orgAuditBadge(name,idx)]);renderRankRows($('org-ranking'),rows)}
function safeHref(u){return typeof u==='string'&&/^https?:\/\//i.test(u)?u:null}
function recordHtml(n,proc,tenderId){const pills=[n.organismo,n.tipo,`Boletín ${n.numero_boletin}`].filter(Boolean).map(v=>`<span class="meta-pill">${esc(String(v))}</span>`);if(proc&&n.categoria)pills.unshift(`<span class="meta-pill category">${esc(n.categoria.replaceAll('_',' '))}</span>`);const href=safeHref(n.url_norma);const link=href?`<a class="record-action" href="${attr(href)}" target="_blank" rel="noopener">Documento oficial ↗</a>`:'';const badge=tenderAuditBadge(tenderId===undefined?n.bac_tender_id:tenderId);return `<article class="record"><div><h3 class="record-title">${esc(n.nombre||`Norma ${n.id_norma}`)}${badge}</h3><div class="record-meta">${pills.join('')}</div><p class="record-summary">${esc(n.sumario||'Sin sumario disponible.')}</p></div>${link}</article>`}
const artDay=new Intl.DateTimeFormat('en-CA',{timeZone:'America/Argentina/Buenos_Aires'});
function dayDiff(iso){return Math.round((Date.parse(artDay.format(new Date(iso)))-Date.parse(artDay.format(new Date())))/864e5)}
// "En Apertura" con apertura de hace más de un año: BAC tiene procesos trabados en ese estado desde 2014. No se listan como abiertos.
const EN_APERTURA_STALE_DAYS=365;
function plural(n,s){return `${fmt.format(n)} ${s}${n===1?'':'s'}`}
function aperturaWhen(a){if(!a.fecha_apertura)return 'Sin fecha de apertura';const d=dayDiff(a.fecha_apertura);if(a.estado_buscado==='En Apertura')return d>=0?'Abre hoy':`Abrió hace ${plural(-d,'día')}`;if(d<0)return `Apertura hace ${plural(-d,'día')} (BAC todavía no actualizó el estado)`;return d===0?'Abre hoy':d===1?'Abre mañana':`Faltan ${plural(d,'día')}`}
// Montos abreviados en millones con separador de miles ("$ 18.425 M", "US$ 12,1 M", "US$ 126 mil"): el formato
// compacto de Intl en es-AR da "18,4 mil M" o "126,2 k", que se leen mal.
const fmt1=new Intl.NumberFormat('es-AR',{maximumFractionDigits:1});
function fmtMoneyShort(v,prefix){if(v>=1e7)return `${prefix} ${fmt.format(Math.round(v/1e6))} M`;if(v>=1e6)return `${prefix} ${fmt1.format(v/1e6)} M`;if(v>=1e4)return `${prefix} ${fmt.format(Math.round(v/1e3))} mil`;return `${prefix} ${fmt.format(Math.round(v))}`}
const fmtUsd={format:v=>fmtMoneyShort(v,'US$')},fmtArsCompact={format:v=>fmtMoneyShort(v,'$')};
// Presupuesto oficial (detalle de BAC, ver bac_presupuesto_oficial.py). El puesto se muestra siempre como contexto; la
// etiqueta "entre los más altos" sólo si supera el umbral documentado. Nunca se habla de sobreprecio.
function presupuestoHtml(numero){const b=state.presupuestos?.procesos?.[numero];if(!b||b.monto==null)return'';const monto=b.moneda==='USD'?fmtUsd.format(b.monto):fmtArsCompact.format(b.monto);const dur=b.duracion_meses?` · ${b.duracion_meses>=1?`${fmt.format(b.duracion_meses)} meses`:'menos de 1 mes'}`:'';const usd=b.moneda!=='USD'&&b.monto_usd?` (≈ ${fmtUsd.format(b.monto_usd)})`:'';const puesto=b.rank&&b.n>1?` · ${b.rank}º de ${b.n} ${b.grupo_comparacion==='Licitaciones públicas'?'licitaciones públicas':'contrataciones del mismo tipo'} abiertas${b.ratio_mediana>=2?`, ${fmt.format(Math.round(b.ratio_mediana))}× la mediana`:''}`:'';const flag=b.flag?' <span class="badge audit-flag" title="Supera el umbral documentado frente a procesos del mismo tipo. No indica sobreprecio: puede reflejar alcance o un contrato plurianual.">Presupuesto entre los más altos</span>':'';return `<p class="record-summary">Presupuesto oficial: <strong>${esc(monto)}</strong>${esc(usd)}${esc(dur)}${esc(puesto)}${flag}</p>`}
function presupuestoRecordHtml(numero){const b=state.presupuestos.procesos[numero];const pills=[b.organismo,b.tipo_proceso,numero].filter(Boolean).map(v=>`<span class="meta-pill">${esc(String(v))}</span>`);return `<article class="record"><div><h3 class="record-title">${esc(b.nombre_proceso||numero)}</h3><div class="record-meta">${pills.join('')}</div>${presupuestoHtml(numero)}</div></article>`}
function renderPresupuestos(){
  const el=$('presupuesto-list'),data=state.presupuestos;if(!el)return;
  if(!data){el.innerHTML='<div class="empty">Presupuestos de licitaciones abiertas todavía no publicados.</div>';setText('count-presupuesto','—');return}
  const procs=data.procesos||{},flagged=Object.keys(procs).filter(k=>procs[k].flag).sort((a,b)=>(procs[b].monto_usd||0)-(procs[a].monto_usd||0));
  setText('count-presupuesto',fmt.format(flagged.length));
  const top=Object.keys(procs).filter(k=>procs[k].grupo_comparacion==='Licitaciones públicas'&&procs[k].rank<=3).sort((a,b)=>procs[a].rank-procs[b].rank);
  const t=data.thresholds||{};
  const intro=`<div class="bac-note">${esc(data.note||'')}</div>`;
  el.innerHTML=intro+(flagged.length?flagged.map(presupuestoRecordHtml).join(''):`<div class="empty">Hoy ninguna licitación de tecnología abierta supera el umbral (z ≥ ${esc(String(t.outlier_z??2))} con al menos ${esc(String(t.outlier_min_peers??15))} procesos comparables). Como contexto, las de mayor presupuesto entre las licitaciones públicas abiertas:</div>${top.map(presupuestoRecordHtml).join('')}`);
}
function aperturaRecordHtml(a){const pills=[a.organismo,a.tipo_proceso,a.numero_proceso].filter(Boolean).map(v=>`<span class="meta-pill">${esc(String(v))}</span>`);const src=a.tech_source==='palabra_clave'?'<span class="meta-pill category" title="No está en el rubro Informática de BAC; se detectó por palabra clave en el nombre">por palabra clave</span>':'<span class="meta-pill category">rubro Informática</span>';const fecha=a.fecha_apertura?dtf.format(new Date(a.fecha_apertura)):'—';return `<article class="record"><div><h3 class="record-title">${esc(a.nombre_proceso||a.numero_proceso||'Proceso sin nombre')} <span class="badge badge-count">${esc(aperturaWhen(a))}</span></h3><div class="record-meta">${pills.join('')}${src}</div><p class="record-summary">Apertura: ${esc(fecha)} ART</p>${presupuestoHtml(a.numero_proceso)}</div></article>`}
function renderAperturas(){
  const data=state.aperturas,statusEl=$('aperturas-status'),abiertasEl=$('aperturas-abiertas-list'),enAperturaEl=$('aperturas-en-apertura-list'),syncEl=$('aperturas-sync'),countEl=$('novedad-abiertas-count');
  const rows=data?.licitaciones_abiertas;
  if(!data||!Array.isArray(rows)){
    // Sin datos o con el formato viejo (bloques de la home, schema_version 1) hasta que corra el scraper nuevo.
    if(statusEl)statusEl.textContent='sin sincronizar';
    for(const el of [abiertasEl,enAperturaEl])if(el)el.innerHTML='<div class="empty">Radar de licitaciones abiertas todavía no fue publicado.</div>';
    return;
  }
  if(statusEl)statusEl.textContent=data.status==='ok'?'activo':data.status==='incompleto'?'incompleto':'error';
  const publicados=rows.filter(a=>a.estado_buscado==='Publicado');
  const enApertura=rows.filter(a=>a.estado_buscado==='En Apertura');
  const stale=enApertura.filter(a=>a.fecha_apertura&&dayDiff(a.fecha_apertura)<-EN_APERTURA_STALE_DAYS);
  const recientes=enApertura.filter(a=>!stale.includes(a)).sort((x,y)=>(y.fecha_apertura||'').localeCompare(x.fecha_apertura||''));
  const totalPublicados=data.totales_reportados_por_bac?.Publicado;
  if(syncEl)syncEl.textContent=data.collected_at?`Actualizado ${dtf.format(new Date(data.collected_at))} ART · ${totalPublicados!=null?`${fmt.format(totalPublicados)} procesos abiertos en BAC, `:''}${fmt.format(publicados.length)} de tecnología`:'';
  if(countEl)countEl.textContent=`${fmt.format(publicados.length)} abierta${publicados.length===1?'':'s'}`;
  setText('navcount-licitaciones',fmt.format(publicados.length));
  if(abiertasEl)abiertasEl.innerHTML=publicados.length?publicados.map(aperturaRecordHtml).join(''):'<div class="empty">Sin licitaciones de tecnología abiertas en este momento.</div>';
  const staleHtml=stale.length?`<details class="related-acts"><summary>${plural(stale.length,'proceso')} con apertura de hace más de un año que siguen "En Apertura" en BAC (registro probablemente no cerrado; no se cuentan como abiertos)</summary>${stale.map(aperturaRecordHtml).join('')}</details>`:'';
  if(enAperturaEl)enAperturaEl.innerHTML=(recientes.length?recientes.map(aperturaRecordHtml).join(''):'<div class="empty">Sin procesos de tecnología en apertura.</div>')+staleHtml;
}
function normativeImpactRecordHtml(n){const pills=[n.sigla_unidad,n.tipo,`Boletín ${n.numero_boletin}`].filter(Boolean).map(v=>`<span class="meta-pill">${esc(String(v))}</span>`);const topicPills=(n.topics||[]).map(t=>`<span class="meta-pill category">${esc(t.replaceAll('_',' '))}</span>`);const href=safeHref(n.url_norma);const link=href?`<a class="record-action" href="${attr(href)}" target="_blank" rel="noopener">Documento oficial ↗</a>`:'';return `<article class="record"><div><h3 class="record-title">${esc(n.nombre||`Norma ${n.id_norma}`)}</h3><div class="record-meta">${topicPills.join('')}${pills.join('')}</div><p class="record-summary">${esc(n.sumario||'Sin sumario disponible.')}</p></div>${link}</article>`}
function priceRedeterminationRecordHtml(f){const pills=[f.sigla_unidad,`Orden de Compra ${f.orden_de_compra}`,`Boletín ${f.numero_boletin}`].filter(Boolean).map(v=>`<span class="meta-pill">${esc(String(v))}</span>`);const href=safeHref(f.url_norma);const link=href?`<a class="record-action" href="${attr(href)}" target="_blank" rel="noopener">Documento oficial ↗</a>`:'';return `<article class="record"><div><h3 class="record-title">${esc(f.nombre||'Disposición')} <span class="badge audit-flag">${esc(f.ordinal_word)} actualización</span></h3><div class="record-meta">${pills.join('')}</div><p class="record-summary">Redeterminación N.º ${f.ordinal} sobre la misma Orden de Compra.</p></div>${link}</article>`}
function renderNormativeImpact(){
  const data=state.normativeImpact,statusEl=$('normative-impact-status'),summaryEl=$('normative-impact-summary'),listEl=$('normative-impact-list'),priceEl=$('price-redetermination-list');
  if(!data){if(statusEl)statusEl.textContent='sin sincronizar';if(summaryEl)summaryEl.innerHTML='<div class="empty">Impacto normativo en sistemas todavía no fue publicado.</div>';if(listEl)listEl.innerHTML='';if(priceEl)priceEl.innerHTML='';return}
  if(statusEl)statusEl.textContent='activo';
  const norms=data.norms||[],s=data.summary||{},priceFlags=data.price_redeterminations?.flags||[];
  const bySigla=Object.entries(s.by_sigla||{}).map(([k,v])=>`${k}: ${v}`).join(' · ');
  if(summaryEl)summaryEl.innerHTML=`<div>${fmt.format(s.total_flagged||0)} normas de unidades de sistemas/transformación digital detectadas (${esc(bySigla||'—')})</div>${data.note?`<div class="bac-note">${esc(data.note)}</div>`:''}${data.price_redeterminations?.note?`<div class="bac-note">${esc(data.price_redeterminations.note)}</div>`:''}`;
  if(priceEl)priceEl.innerHTML=priceFlags.length?priceFlags.map(priceRedeterminationRecordHtml).join(''):'<div class="empty">Sin redeterminaciones reiteradas detectadas.</div>';
  const shown=norms.slice(0,impactPage*PAGE_SIZE);
  if(listEl)listEl.innerHTML=norms.length?shown.map(normativeImpactRecordHtml).join(''):'<div class="empty">Sin normas detectadas.</div>';
  setLoadMore('impact-load-more',shown.length,norms.length);
  setText('normative-impact-count',norms.length?`(${fmt.format(shown.length)} de ${fmt.format(norms.length)})`:'');
  setText('count-redeterminaciones',fmt.format(priceFlags.length));
  setText('navcount-normativa',fmt.format(norms.length));
  const normativeCountEl=$('novedad-normative-count');
  if(normativeCountEl)normativeCountEl.textContent=`${fmt.format(norms.length)} detectada${norms.length===1?'':'s'}`;
  const priceCountEl=$('novedad-price-redetermination-count');
  if(priceCountEl)priceCountEl.textContent=`${fmt.format(priceFlags.length)} detectada${priceFlags.length===1?'':'s'}`;
}
function procesoGroupHtml(group){const[main,...related]=group;
  // El acto principal (normalmente el "Llamado") no siempre es el que cita el número de
  // proceso de BAC en su sumario -a veces sólo una circular/prórroga lo hace-, así que se
  // busca en TODO el grupo, no sólo en el acto mostrado, para no perder el cruce.
  const tenderId=group.map(x=>x.bac_tender_id).find(id=>id&&bacTenderIndex()[id]);
  const mainHtml=recordHtml(main,true,tenderId);if(!related.length)return `<div class="process-group">${mainHtml}</div>`;const relatedHtml=related.map(r=>recordHtml(r,true)).join('');return `<div class="process-group">${mainHtml}<details class="related-acts"><summary>${related.length} acto${related.length===1?'':'s'} relacionado${related.length===1?'':'s'} (circulares, prórroga, etc.)</summary>${relatedHtml}</details></div>`}
function renderLists(){
  const nr=filteredNorms(),prGroups=groupByProceso(filteredProcurements());
  const nshow=nr.slice(0,normsPage*PAGE_SIZE),gshow=prGroups.slice(0,procPage*PAGE_SIZE);
  $('norm-result-count').textContent=fmt.format(nr.length);
  $('proc-result-count').textContent=fmt.format(prGroups.length);
  $('norm-list').innerHTML=nshow.map(n=>recordHtml(n,false)).join('')||'<div class="empty">Sin normas.</div>';
  $('procurement-list').innerHTML=gshow.map(procesoGroupHtml).join('')||'<div class="empty">Sin contrataciones.</div>';
  $('norms-load-more').style.display=nr.length>nshow.length?'':'none';
  $('procs-load-more').style.display=prGroups.length>gshow.length?'':'none';
}
function setText(id,v){const el=$(id);if(el)el.textContent=v}
function setLoadMore(id,shown,total){const el=$(id);if(el)el.style.display=total>shown?'':'none'}
// Solapa "Hoy": resumen de recuentos que enlaza a cada sección. Se calcula de state, no del DOM.
// Badges de Novedades (sólo en Hoy): se calculan de state, sin depender de los renderers de cada sección.
function renderNovedades(){const pl=(n,s)=>`${fmt.format(n)} ${s}${n===1?'':'s'}`;const rows=state.aperturas?.licitaciones_abiertas;if(Array.isArray(rows))setText('novedad-abiertas-count',pl(rows.filter(a=>a.estado_buscado==='Publicado').length,'abierta'));if(state.pliegosCaidos)setText('novedad-failed-count',pl((state.pliegosCaidos.pliegos||[]).length,'detectado'));if(state.recurringVendorWatch)setText('novedad-recurring-vendor-count',pl((state.recurringVendorWatch.candidates||[]).filter(c=>c.confirmed_recent_award).length,'confirmado'));if(state.normativeImpact){setText('novedad-normative-count',pl((state.normativeImpact.norms||[]).length,'detectada'));setText('novedad-price-redetermination-count',pl((state.normativeImpact.price_redeterminations?.flags||[]).length,'detectada'))}}
function renderHoy(){
  const rows=state.aperturas?.licitaciones_abiertas;
  if(Array.isArray(rows)){const pub=rows.filter(a=>a.estado_buscado==='Publicado'),enAp=rows.filter(a=>a.estado_buscado==='En Apertura'&&!(a.fecha_apertura&&dayDiff(a.fecha_apertura)<-EN_APERTURA_STALE_DAYS));setText('hoy-abiertas',fmt.format(pub.length));setText('hoy-en-apertura',fmt.format(enAp.length));setText('navcount-licitaciones',fmt.format(pub.length));const next=pub.find(a=>a.fecha_apertura&&dayDiff(a.fecha_apertura)>=0);if(next)setText('hoy-abiertas-foot',`próxima: ${aperturaWhen(next).toLowerCase()}`)}
  if(state.pliegosCaidos)setText('hoy-pliegos',fmt.format((state.pliegosCaidos.pliegos||[]).length));
  if(state.recurringVendorWatch)setText('hoy-recurrente',fmt.format((state.recurringVendorWatch.candidates||[]).filter(c=>c.confirmed_recent_award).length));
  if(state.bac)setText('hoy-concentracion',fmt.format((state.bac.audit_signals?.vendor_concentration_by_organismo||[]).filter(c=>c.high_concentration).length));
  if(state.normativeImpact){setText('hoy-redeterminaciones',fmt.format((state.normativeImpact.price_redeterminations?.flags||[]).length));const n=(state.normativeImpact.norms||[]).length;setText('hoy-normativa',fmt.format(n));setText('navcount-normativa',fmt.format(n))}
}
// Encabezado + navegación compartidos por todas las páginas: se arman acá, desde PAGES, para
// no duplicar el markup en cada .html (no hay build step que lo haga).
// Aviso de versión: recuadro sutil (celeste) en todas las páginas, con el detalle desplegable.
// Reemplaza al aviso amarillo de "Nuevo" para no apilar dos carteles. Actualizar en cada versión.
const VERSION_NOTE={label:'Versión 2 · 31/10/2026',summary:'Dashboard reorganizado en secciones, con todas las licitaciones de tecnología abiertas de BAC y los montos en pesos y dólares.',changes:[
  '<strong>Secciones:</strong> el dashboard ya no es una sola página larga. Cada sección (Hoy, Licitaciones, Señales para revisar, Normativa de sistemas, Panorama, Explorar) es una página propia con su dirección, y se puede compartir el link a cualquiera.',
  '<strong>Más rápido:</strong> cada página descarga sólo los datos que usa. Hoy pasó de ~26 MB a ~0,4 MB; sólo Explorar baja el histórico completo de normas y procesos.',
  '<strong>Licitaciones abiertas:</strong> ahora se ven <a href="licitaciones.html">todas las de tecnología</a> publicadas en Buenos Aires Compras, con cuenta regresiva a la apertura, en vez de las 12 de la home de BAC (que muchas veces no incluían ninguna de tecnología).',
  '<strong>Señales para revisar:</strong> <a href="senales.html">agrupadas en una sola página</a>, plegables y con su recuento. Las listas que antes se cortaban (pliegos caídos, impacto normativo) ahora muestran "N de M" y tienen "Cargar más".',
  '<strong>Hoy:</strong> portada con los números del día; cada tarjeta lleva a su sección.',
  '<strong>Montos en pesos y en dólares:</strong> las adjudicaciones en USD ya no se descartan: se convierten a pesos a la cotización oficial del BCRA del día de cada adjudicación, conservando el monto original. Con eso el total adjudicado en tecnología pasó de ~$ 8.766 M a ~$ 21.546 M, la contratación directa/limitada bajó de 68 % a 33 % del monto, y la Agencia de Sistemas de Información (que adjudica casi todo en dólares) ahora aparece en las señales. <a href="panorama.html">Ver Panorama</a>',
  '<strong>Presupuesto oficial de cada licitación abierta:</strong> se lee del pliego en BAC y se muestra en pesos y su equivalente en dólares, con duración y puesto frente a las demás del mismo tipo (por ejemplo, "1º de 17 licitaciones públicas abiertas, 26× la mediana"). Los presupuestos que se apartan mucho del resto aparecen en <a href="senales.html#senal-presupuesto">Señales para revisar</a>; un monto alto no indica sobreprecio.',
]};
function renderShell(){
  const el=$('shell');if(!el)return;
  const notice=`<details class="version-note"><summary><span class="version-tag">${esc(VERSION_NOTE.label)}</span> ${esc(VERSION_NOTE.summary)} <span class="version-more">Ver cambios</span></summary><ul>${VERSION_NOTE.changes.map(c=>`<li>${c}</li>`).join('')}</ul></details>`;
  const nav=Object.entries(PAGES).map(([k,p])=>`<a href="${p.file}"${k===PAGE?' aria-current="page"':''}>${esc(p.label)}<span id="navcount-${k}" class="tab-count"></span></a>`).join('');
  el.outerHTML=`<header class="topbar"><div><div class="eyebrow">INTELIGENCIA PÚBLICA · CABA</div><h1><a class="home-link" href="index.html">Curador CABA Intelligence</a></h1><p>Observatorio ejecutivo del Boletín Oficial, contrataciones y actividad tecnológica de la Ciudad.</p></div><div class="status-wrap"><span id="data-status" class="status status-loading">Cargando datos</span><span id="dashboard-sync-at" class="muted"></span></div></header>${notice?`<div class="container shell-notices">${notice}</div>`:''}<nav class="tabs" aria-label="Secciones del dashboard"><div class="tabs-inner">${nav}</div></nav>`;
}
// Links viejos (de cuando todo era una sola página, con anclas o solapas por hash): si el
// ancla no existe en esta página, se busca en qué página vive y se redirige.
const ANCHOR_PAGE={licitaciones:'licitaciones','bac-licitaciones-abiertas':'licitaciones','aperturas-en-apertura-list':'licitaciones',senales:'senales','bac-failed-tenders':'senales','bac-recurring-vendor-watch':'senales','bac-concentration':'senales','bac-fractionation':'senales','bac-repeat-winner':'senales','price-redetermination-list':'senales','senal-concentracion':'senales','senal-fraccionamiento':'senales','senal-multiorganismo':'senales','senal-recurrente':'senales','senal-pliegos-caidos':'senales','senal-redeterminaciones':'senales','senal-presupuesto':'senales',normativa:'normativa','normative-impact-panel':'normativa',panorama:'panorama',explorar:'explorar',hoy:'hoy'};
function redirectLegacyHash(){const id=decodeURIComponent(location.hash.slice(1));if(!id||$(id))return false;const page=PAGES[ANCHOR_PAGE[id]];if(!page)return false;const keepHash=!PAGES[id];location.replace(`${page.file}${keepHash?`#${encodeURIComponent(id)}`:''}`);return true}
// Dentro de una página: abrir el <details> que contiene el ancla y llevarla a la vista.
function revealHash(){const id=decodeURIComponent(location.hash.slice(1));const target=id&&$(id);if(!target)return;for(let d=target.closest('details');d;d=d.parentElement?.closest('details'))d.open=true;requestAnimationFrame(()=>target.scrollIntoView({block:'start'}))}
function safeRender(label,fn){try{fn()}catch(e){console.error(`render failed: ${label}`,e)}}
const RENDERERS={presupuestos:renderPresupuestos,metrics:renderMetrics,syncNotice:renderSyncNotice,hoy:renderHoy,novedades:renderNovedades,aperturas:renderAperturas,bac:()=>renderBAC(state.bac,state.bacSyncState),recurringVendorWatch:renderRecurringVendorWatch,pliegosCaidos:renderPliegosCaidos,normativeImpact:renderNormativeImpact,intelligence:renderIntelligence,bars:renderBars,orgRanking:renderOrgRanking,lists:renderLists};
function renderAll(){safeRender('headerSync',renderHeaderSync);for(const k of PAGES[PAGE].render)safeRender(k,RENDERERS[k])}
function resetPagingAndRenderAll(){normsPage=1;procPage=1;renderAll()}
function on(id,ev,fn){const el=$(id);if(el)el.addEventListener(ev,fn)}
function bind(){
  ['filter-search','filter-org','filter-type','filter-category'].forEach(id=>on(id,id==='filter-search'?'input':'change',resetPagingAndRenderAll));
  on('clear-filters','click',()=>{$('filter-search').value='';$('filter-org').value='';$('filter-type').value='';$('filter-category').value='';resetPagingAndRenderAll()});
  on('norms-load-more','click',()=>{normsPage++;renderLists()});
  on('procs-load-more','click',()=>{procPage++;renderLists()});
  on('pliegos-load-more','click',()=>{pliegosPage++;renderPliegosCaidos()});
  on('impact-load-more','click',()=>{impactPage++;renderNormativeImpact()});
  window.addEventListener('hashchange',revealHash);
}
function esc(v=''){return String(v).replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]))}
function attr(v=''){return esc(v).replace(/'/g,'&#39;')}
if(!redirectLegacyHash()){renderShell();
(async()=>{try{await loadData();if(PAGE==='explorar')populateFilters();bind();renderAll();revealHash()}catch(e){console.error(e);$('data-status').textContent='Datos no disponibles';$('data-status').className='status status-error';$('dashboard-sync-at').textContent=e.message}})()}
