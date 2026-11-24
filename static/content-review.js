let contentScope='all', contentDimension='all';
const contentBasisLabels={internal_conflict:'内部证据冲突',reference_conflict:'与所附资料不一致',editorial_risk:'内容表达风险',verification:'需外部核实'};

function contentEvidenceHTML(issue) {
  if(issue.scope!=='content') return '';
  const related=(issue.related||[]).map(e=>`<div class="related-evidence"><span>对照证据 · ${escapeHTML(sourceLabels[e.source]||e.source)}</span>${e.start==null?'':`<button class="text-button" data-seek="${Number(e.start)}" ${e.source==='frame'?`data-evidence-frame="${escapeHTML(e.source_id)}"`:''}>${timestamp(e.start)} ↗</button>`}<p>${escapeHTML(e.evidence)}</p></div>`).join('');
  return `${issue.basis?`<span class="content-basis">${contentBasisLabels[issue.basis]}</span>`:''}${related}${issue.reference_quote?`<div class="reference-quote"><span>所附资料原文 · 尚未独立核验</span><p>${escapeHTML(issue.reference_quote)}</p></div>`:''}`;
}

function matchesContentScope(issue) {
  return (contentScope==='all'||(contentScope==='verification'?issue.basis==='verification':issue.scope===contentScope))
    &&(contentDimension==='all'||issue.dimension===contentDimension);
}

function renderContentReview(job) {
  const content=job.report.content_review;
  if(!content) return;
  const technical=job.report.issues.filter(i=>i.scope==='technical').length;
  $('.report-main').insertAdjacentHTML('afterbegin',`<section class="card content-review"><div class="card-heading"><h2>视频内容审核</h2><span class="badge">${escapeHTML(content.profile)}</span></div><p class="content-summary">${escapeHTML(content.summary)}</p><div class="content-totals"><b>${content.issue_count}<small>条内容疑点</small></b><b>${content.verification_count}<small>条需外部核实</small></b><span>${content.text_complete?'文字证据审读完成':'文字审读存在缺口'}<br>${content.vision_complete?'已完成选定画面的联合审读':'画面内容审读存在缺口'}</span></div><div class="dimension-grid">${content.dimensions.map(d=>`<button class="dimension ${d.status}" data-dimension="${d.key}" title="${escapeHTML(d.description)}"><b>${escapeHTML(d.name)}</b><span>${escapeHTML(d.label)}${d.issue_ids.length?` · ${d.issue_ids.length}`:''}</span></button>`).join('')}</div><p class="help">点击维度查看对应疑点。“已检查”仅指已审读本次获得的证据，不表示审核通过。</p><details class="content-notes"><summary>检查依据与未覆盖事项</summary>${content.dimensions.map(d=>`<p><b>${escapeHTML(d.name)}</b>：${d.notes.length?d.notes.map(escapeHTML).join('；'):'未获得有效检查结果'}</p>`).join('')}${content.limitations.map(g=>`<p class="content-gap">${escapeHTML(g)}</p>`).join('')}<p>${escapeHTML(content.notice)}</p></details></section>`);
  $('.filter-tabs').insertAdjacentHTML('beforebegin',`<div class="content-scope-tabs" role="group" aria-label="问题类型"><button data-scope="content">内容疑点 ${content.issue_count}</button><button data-scope="verification">待核实事实 ${content.verification_count}</button><button data-scope="technical">技术检查 ${technical}</button><button data-scope="all">全部问题</button></div><p id="dimension-filter" class="help" hidden></p>`);
  updateContentTabs();
}

function updateContentTabs() {
  $$('[data-scope]').forEach(button=>{button.classList.toggle('active',button.dataset.scope===contentScope);button.setAttribute('aria-pressed',String(button.dataset.scope===contentScope));});
  $$('[data-dimension]').forEach(button=>button.classList.toggle('selected',button.dataset.dimension===contentDimension));
  const label=$('#dimension-filter');
  if(label){label.hidden=contentDimension==='all';label.textContent=contentDimension==='all'?'':`当前维度：${reportJob.report.content_review.dimensions.find(d=>d.key===contentDimension)?.name} · 点击上方“内容疑点”可恢复全部维度`;}
}

document.addEventListener('click',event=>{
  const scope=event.target.closest('[data-scope]'), dimension=event.target.closest('[data-dimension]');
  if(scope||dimension){
    contentScope=scope?scope.dataset.scope:'content';contentDimension=dimension?dimension.dataset.dimension:'all';
    reportFilter='all';$$('[data-filter]').forEach(b=>b.classList.toggle('active',b.dataset.filter==='all'));
    updateContentTabs();renderIssues();
    if(dimension) $('.content-scope-tabs').scrollIntoView({behavior:'smooth',block:'center'});
  }
  const frame=event.target.closest('[data-evidence-frame]');
  if(frame)showFrameEvidence({source:'frame',source_id:frame.dataset.evidenceFrame});
});
