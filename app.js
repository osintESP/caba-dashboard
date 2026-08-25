const state={stats:null,editions:[],norms:[],procurements:[],intelligence:null,sync:null,bac:null};
const $=id=>document.getElementById(id);
const fmt=new Intl.NumberFormat('es-AR');
const fmtCurrency=new Intl.NumberFormat('es-AR',{style:'currency',currency:'ARS',maximumFractionDigits:0});
const PAGE_SIZE=25;
let normsPage=1,procPage=1;
const dtf=new Intl.DateTimeFormat('es-AR',{timeZone:'America/Argentina/Buenos_Aires',dateStyle:'short',timeStyle:'medium'});
async function fetchJson(path){const r=await fetch(path,{cache:'no-store'});if(!r.ok)throw new Error(`${path}: HTTP ${r.status}`);return r.json()}
async function fetchOptional(path){try{return await fetchJson(path)}catch(e){console.warn(`optional dataset unavailable: ${path}`,e);return null}}
async function loadData(){const [stats,editions,norms,procurements,intelligence,sync,bac]=await Promise.all([fetchJson('data/stats.json'),fetchJson('data/editions.json'),fetchJson('data/norms.json'),fetchJson('data/procurements.json'),fetchOptional('data/procurement_intelligence.json'),fetchOptional('data/sync_manifest.json'),fetchOptional('data/bac_catalog.json')]);Object.assign(state,{stats,editions,norms,procurements,intelligence,sync,bac});$('data-status').textContent='Datos conectados';$('data-status').className='status status-ok'}
function fillSelect(el,values){for(const v of values){const o=document.createElement('option');o.value=v;o.textContent=v;el.appendChild(o)}}
function populateFilters(){fillSelect($('filter-org'),[...new Set(state.norms.map(x=>x.organismo).filter(Boolean))].sort());fillSelect($('filter-type'),[...new Set(state.norms.map(x=>x.tipo).filter(Boolean))].sort());fillSelect($('filter-category'),[...new Set(state.procurements.map(x=>x.categoria).filter(Boolean))].sort())}
function filteredNorms(){const q=$('filter-search').value.trim().toLowerCase(),org=$('filter-org').value,type=$('filter-type').value;return state.norms.filter(n=>{const hay=`${n.nombre||''} ${n.sumario||''} ${n.organismo||''} ${n.tipo||''}`.toLowerCase();return(!q||hay.includes(q))&&(!org||n.organismo===org)&&(!type||n.tipo===type)})}
function filteredProcurements(){const q=$('filter-search').value.trim().toLowerCase(),org=$('filter-org').value,type=$('filter-type').value,cat=$('filter-category').value;return state.procurements.filter(n=>{const hay=`${n.nombre||''} ${n.sumario||''} ${n.organismo||''} ${n.tipo||''} ${n.categoria||''}`.toLowerCase();return(!q||hay.includes(q))&&(!org||n.organismo===org)&&(!type||n.tipo===type)&&(!cat||n.categoria===cat)})}
function uniqueProcessCount(rows){return new Set(rows.map(x=>x.proceso_id||x.id_norma)).size}
function groupByProceso(rows){const groups=new Map();for(const r of rows){const key=r.proceso_id||r.id_norma;if(!groups.has(key))groups.set(key,[]);groups.get(key).push(r)}return[...groups.values()]}
function normalizeOrgName(name){return String(name||'').normalize('NFKD').replace(/[\u0300-\u036f]/g,'').replace(/\./g,'').replace(/\s+/g,' ').trim().toUpperCase()}
function renderMetrics(){const latest=[...state.editions].sort((a,b)=>Number(b.numero_boletin)-Number(a.numero_boletin))[0];$('metric-bulletin').textContent=latest?`N.º ${latest.numero_boletin}`:'—';$('metric-bulletin-date').textContent=latest?.fecha_publicacion||'—';$('metric-norms').textContent=fmt.format(state.stats?.norms??state.norms.length);$('metric-procurements').textContent=fmt.format(state.stats?.procurements??uniqueProcessCount(state.procurements));$('metric-editions').textContent=fmt.format(state.stats?.editions??state.editions.length);$('data-generated-at').textContent=state.stats?.generated_at?`Datos generados ${dtf.format(new Date(state.stats.generated_at))} ART`:'histórico disponible';const syncAt=state.sync?.public_synced_at||state.sync?.published_at;$('dashboard-sync-at').textContent=syncAt?`Dashboard sincronizado ${dtf.format(new Date(syncAt))} ART`:'Dashboard sin sincronización automática registrada'}
function renderIntelligence(){const s=state.intelligence?.summary;if(!s){$('intel-status').textContent='sin sincronizar';$('metric-tech').textContent='—';$('metric-cyber').textContent='—';$('intel-tags').innerHTML='<div class="empty">La capa Intelligence todavía no fue publicada en el repositorio público.</div>';return}$('intel-status').textContent='activo';$('metric-tech').textContent=fmt.format(s.technology_related||0);$('metric-cyber').textContent=fmt.format(s.cybersecurity_related||0);const rows=Object.entries(s.tag_counts||{}).sort((a,b)=>b[1]-a[1]),max=rows[0]?.[1]||1;$('intel-tags').innerHTML=rows.length?rows.map(([k,v])=>`<div class="bar-row"><div class="bar-label">${esc(k.replaceAll('_',' '))}</div><div class="bar-track"><div class="bar-fill" style="width:${Math.max(4,v/max*100)}%"></div></div><div class="bar-value">${v}</div></div>`).join(''):'<div class="empty">Sin etiquetas tecnológicas.</div>'}
function renderRankRows(container,rows){container.innerHTML=rows.map(([name,value,badge],i)=>`<div class="rank-row"><span class="rank-index">${i+1}</span><span class="rank-name">${esc(name)}${badge||''}</span><span class="rank-value">${value}</span></div>`).join('')||'<div class="empty">Sin resultados.</div>'}
function renderBAC(data){
  const statusEl=$('bac-status'),summaryEl=$('bac-summary'),rankingEl=$('vendor-ranking'),concEl=$('bac-concentration'),fracEl=$('bac-fractionation');
  const panel=statusEl?.closest('.panel')||statusEl?.parentElement;
  if(!data){if(panel)panel.style.display='none';return}
  if(panel)panel.style.display='';
  const okStates=['ok','unchanged'];
  statusEl.textContent=okStates.includes(data.status)?'activo':(data.status==='resource_not_found'||data.status==='download_error'||data.status==='schema_unexpected')?'error':'pendiente';
  const s=data.summary||{},audit=data.audit_signals||{},cov=data.coverage||{},nco=audit.non_competitive_open_tenders||{};
  const concentration=audit.vendor_concentration_by_organismo||[];
  const highConc=concentration.filter(c=>c.high_concentration).length;
  summaryEl.innerHTML=`
    <div>Dataset actualizado: ${esc(data.dataset?.metadata_modified||data.collected_at||'—')}</div>
    <div>Cobertura de releases procesados: ${esc(cov.date_from||'—')} a ${esc(cov.date_to||'—')} (${fmt.format(cov.releases_processed||0)} releases — ver nota de cobertura)</div>
    <div>Monto total adjudicado: ${fmtCurrency.format(s.total_awarded_ars||0)} · en tecnología: ${fmtCurrency.format(s.tech_awarded_ars||0)} (${s.tech_share_pct||0}%)</div>
    <div>Contratación directa/limitada en tecnología: ${audit.direct_or_limited_share_pct||0}% del monto</div>
    <div>Licitaciones públicas sin competencia real (tecnología): ${fmt.format(nco.count||0)} procesos, ${fmtCurrency.format(nco.amount_ars||0)} (${nco.share_pct_of_tech||0}% del monto en tecnología)</div>
    ${nco.note?`<div class="bac-note">${esc(nco.note)}</div>`:''}
    <div>Organismos con alta concentración de proveedor (&ge;60% en un solo vendor): ${fmt.format(highConc)}</div>
    ${audit.possible_fractionation?.note?`<div class="bac-note">${esc(audit.possible_fractionation.note)}</div>`:''}
  `;
  if(rankingEl)renderRankRows(rankingEl,(data.vendor_ranking||[]).slice(0,8).map(v=>[v.name,fmtCurrency.format(v.amount_ars)]));
  if(concEl){
    const rows=[...concentration].sort((a,b)=>(b.organismo_tech_amount_ars||0)-(a.organismo_tech_amount_ars||0)).map(c=>{
      const label=`${c.high_concentration?'⚠ ':''}${c.top_vendor_share_pct}% en "${c.top_vendor}"`;
      const badge=` <span class="badge${c.high_concentration?' audit-flag':''}">${esc(label)}</span>`;
      return[c.organismo,fmtCurrency.format(c.organismo_tech_amount_ars||0),badge];
    });
    renderRankRows(concEl,rows);
  }
  if(fracEl){
    const flags=audit.possible_fractionation?.awards||[];
    const rows=flags.map(f=>[`${f.organismo} · ${f.vendor}`,fmtCurrency.format(f.total_amount_ars||0),` <span class="badge audit-flag">${f.awards_count} adjudicaciones en ${f.window_days}d</span>`]);
    renderRankRows(fracEl,rows);
  }
}
function renderSyncNotice(){const missing=[];if(!state.intelligence)missing.push('Intelligence');if(!state.sync)missing.push('registro de sincronización');if(missing.length){$('sync-notice').innerHTML=`<strong>Dashboard v1 mejorada.</strong> Datos base operativos. Pendiente de sincronización pública: ${esc(missing.join(' + '))}.`}else{const bulletin=state.sync.latest_bulletin?` Boletín N.º ${esc(state.sync.latest_bulletin)} sincronizado.`:'';$('sync-notice').innerHTML=`<strong>Dashboard v1 mejorada.</strong> Boletín e Intelligence sincronizados.${bulletin}`}}
function renderBars(){const rowsSource=filteredProcurements();const seen=new Set(),uniqueRows=[];for(const x of rowsSource){const pid=x.proceso_id||x.id_norma;if(seen.has(pid))continue;seen.add(pid);uniqueRows.push(x)}const counts={};for(const x of uniqueRows)counts[x.categoria||'otros']=(counts[x.categoria||'otros']||0)+1;const rows=Object.entries(counts).sort((a,b)=>b[1]-a[1]),max=rows[0]?.[1]||1;$('procurement-total').textContent=fmt.format(uniqueRows.length);$('category-bars').innerHTML=rows.length?rows.map(([k,v])=>`<div class="bar-row"><div class="bar-label">${esc(k.replaceAll('_',' '))}</div><div class="bar-track"><div class="bar-fill" style="width:${Math.max(4,v/max*100)}%"></div></div><div class="bar-value">${v}</div></div>`).join(''):'<div class="empty">Sin resultados.</div>'}
function bacOrgIndex(){const list=state.bac?.audit_signals?.vendor_concentration_by_organismo||[];const idx=new Map();for(const c of list)idx.set(normalizeOrgName(c.organismo),c);return idx}
function orgAuditBadge(organismo,idx){const c=idx.get(normalizeOrgName(organismo));if(!c)return'';const label=c.high_concentration?`⚠ BAC: ${c.top_vendor_share_pct}% concentrado en "${c.top_vendor}"`:`BAC: ${c.top_vendor_share_pct}% en "${c.top_vendor}"`;return` <span class="badge audit-flag" title="${attr(label)}">⚠ BAC</span>`}
function renderOrgRanking(){const counts={};for(const x of filteredNorms()){const k=x.organismo||'Sin organismo';counts[k]=(counts[k]||0)+1}const idx=bacOrgIndex();const rows=Object.entries(counts).sort((a,b)=>b[1]-a[1]).slice(0,8).map(([name,value])=>[name,value,orgAuditBadge(name,idx)]);renderRankRows($('org-ranking'),rows)}
function safeHref(u){return typeof u==='string'&&/^https?:\/\//i.test(u)?u:null}
function recordHtml(n,proc){const pills=[n.organismo,n.tipo,`Boletín ${n.numero_boletin}`].filter(Boolean).map(v=>`<span class="meta-pill">${esc(String(v))}</span>`);if(proc&&n.categoria)pills.unshift(`<span class="meta-pill category">${esc(n.categoria.replaceAll('_',' '))}</span>`);const href=safeHref(n.url_norma);const link=href?`<a class="record-action" href="${attr(href)}" target="_blank" rel="noopener">Documento oficial ↗</a>`:'';return `<article class="record"><div><h3 class="record-title">${esc(n.nombre||`Norma ${n.id_norma}`)}</h3><div class="record-meta">${pills.join('')}</div><p class="record-summary">${esc(n.sumario||'Sin sumario disponible.')}</p></div>${link}</article>`}
function procesoGroupHtml(group){const[main,...related]=group;const mainHtml=recordHtml(main,true);if(!related.length)return `<div class="process-group">${mainHtml}</div>`;const relatedHtml=related.map(r=>recordHtml(r,true)).join('');return `<div class="process-group">${mainHtml}<details class="related-acts"><summary>${related.length} acto${related.length===1?'':'s'} relacionado${related.length===1?'':'s'} (circulares, prórroga, etc.)</summary>${relatedHtml}</details></div>`}
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
function renderAll(){renderMetrics();renderIntelligence();renderBAC(state.bac);renderSyncNotice();renderBars();renderOrgRanking();renderLists()}
function resetPagingAndRenderAll(){normsPage=1;procPage=1;renderAll()}
function bind(){
  ['filter-search','filter-org','filter-type','filter-category'].forEach(id=>$(id).addEventListener(id==='filter-search'?'input':'change',resetPagingAndRenderAll));
  $('clear-filters').addEventListener('click',()=>{$('filter-search').value='';$('filter-org').value='';$('filter-type').value='';$('filter-category').value='';resetPagingAndRenderAll()});
  $('norms-load-more').addEventListener('click',()=>{normsPage++;renderLists()});
  $('procs-load-more').addEventListener('click',()=>{procPage++;renderLists()});
}
function esc(v=''){return String(v).replace(/[&<>\"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]))}
function attr(v=''){return esc(v).replace(/'/g,'&#39;')}
(async()=>{try{await loadData();populateFilters();bind();renderAll()}catch(e){console.error(e);$('data-status').textContent='Datos no disponibles';$('data-status').className='status status-error';$('dashboard-sync-at').textContent=e.message}})();