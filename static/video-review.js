/* Video-specific controls and evidence views; no external browser dependencies. */
function collectVideoOptions() {
  return {
    content_profile:$('#content-profile').value,
    freeze_check:$('#check-freeze').checked, audio_check:$('#check-audio').checked,
    scene_check:$('#check-scenes').checked, caption_check:$('#check-captions').checked,
    alignment_check:$('#check-alignment').checked, vision_limit:Number($('#vision-limit').value),
    expected_aspect:$('#expected-aspect').value, target_lufs:Number($('#target-lufs').value),
    loudness_tolerance:Number($('#loudness-tolerance').value), freeze_seconds:Number($('#freeze-seconds').value),
    safe_margin:Number($('#safe-margin').value)/100, min_text_height:Number($('#text-height').value)/100,
    max_cps:Number($('#max-cps').value), glossary:$('#video-glossary').value
  };
}

function renderDecisionSummary() {
  if(!reportJob) return;
  let summary=$('#decision-summary');
  if(!summary) {summary=document.createElement('span');summary.id='decision-summary';summary.className='count';$('.report-status').append(summary);}
  const total=reportJob.report.issues.length;
  const count=Object.values(reportJob.decisions||{}).filter(d=>d.status!=='pending').length;
  summary.textContent=`人工复核 ${count} / ${total}`;
}

function decorateDecisions() {
  $$('#issues-list [data-issue]').forEach(card=>{
    card.setAttribute('role','group');
    const decision=reportJob.decisions?.[card.dataset.issue]||{status:'pending',note:''};
    card.insertAdjacentHTML('beforeend',`<div class="decision-controls"><label>人工状态<select data-decision="${card.dataset.issue}" aria-label="人工复核状态">${Object.entries({pending:'待复核',resolved:'已处理',dismissed:'无需处理'}).map(([key,label])=>`<option value="${key}" ${key===decision.status?'selected':''}>${label}</option>`).join('')}</select></label><label class="decision-note">复核备注<input data-note="${card.dataset.issue}" maxlength="1000" placeholder="记录核实依据或处理说明" value="${escapeHTML(decision.note)}"></label><button class="secondary" data-save-decision="${card.dataset.issue}">保存复核</button></div>`);
  });
}

document.addEventListener('click',event=>{
  const button=event.target.closest('[data-save-decision]');
  if(button) busy(button,async()=>{
    const id=button.dataset.saveDecision, job=reportJob;
    const decision=await api(`/api/jobs/${job.id}/issues/${id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:$(`[data-decision="${id}"]`).value,note:$(`[data-note="${id}"]`).value})});
    if(reportJob?.id===job.id){reportJob.decisions??={};reportJob.decisions[id]=decision;renderDecisionSummary();}
    toast('人工复核已保存，导出报告会包含状态与备注');
  });
});

function renderVideoDetails(job) {
  const report=job.report, metadata=report.metadata||{}, metrics=report.metrics||{};
  const hasSpeech=report.sources.some(s=>s.kind==='speech');
  $('#report-tools').insertAdjacentHTML('beforeend',`<a class="secondary" href="/api/jobs/${job.id}/export?format=csv">问题清单 CSV</a>${hasSpeech?`<a class="secondary" href="/api/jobs/${job.id}/export?format=srt" title="自动转写，需人工校对">转写 SRT</a>`:''}`);
  if(!report.options) return; // Reports created by v1.0 remain readable.
  const value=(n,suffix='')=>n==null?'未测得':`${Number(n).toFixed(1)}${suffix}`;
  $('.report-main').insertAdjacentHTML('afterbegin',`<section class="media-metrics card"><div><span>画面规格</span><b>${metadata.width} × ${metadata.height}</b><small>${escapeHTML(metadata.video_codec||'未知编码')} · ${metadata.fps} fps</small></div><div><span>全片响度</span><b>${value(metrics.integrated_lufs,' LUFS')}</b><small>本次目标 ${report.options.target_lufs} ± ${report.options.loudness_tolerance} LU</small></div><div><span>全片真峰值</span><b>${value(metrics.true_peak_dbfs,' dBFS')}</b><small>${metadata.channels??'—'} 声道 · ${metadata.sample_rate??'—'} Hz</small></div></section>`);
  $('#review-video').insertAdjacentHTML('afterend','<section id="frame-evidence" class="card" hidden></section>');
  const scenes=report.scenes||[];
  if(scenes.length) {
    const checked=scenes.filter(s=>s.vision_checked).length;
    $('#more-frames').insertAdjacentHTML('afterend',`<details class="card scene-panel"><summary>镜头检查地图 <span>识别 ${scenes.length} 个 · AI 已抽查 ${checked} 个</span></summary><p class="help">绿色表示该镜头中至少有一帧完成 AI 视觉检查，不代表整个镜头均已审核。</p><div class="shot-list">${scenes.slice(0,150).map(s=>`<button class="shot ${s.vision_checked?'checked':''}" data-seek="${s.start}" title="${timestamp(s.start)}–${timestamp(s.end)}，${s.vision_checked?'已有视觉抽查帧':'未送视觉审核'}">${s.index}<small>${timestamp(s.start)}</small></button>`).join('')}</div>${scenes.length>150?'<p class="help">界面显示前 150 个镜头；完整列表在 JSON 报告中。</p>':''}</details>`);
  }
  if(report.alignment?.length) {
    $('#transcript').insertAdjacentHTML('beforebegin',`<section class="card alignment-panel"><div class="card-heading"><h2>口播 / 字幕逐条对照</h2><label class="check-label"><input type="checkbox" id="alignment-differences" checked>只看待核实</label></div><p class="help">文字相似度用于寻找差异，不是识别置信度。OCR 时间码为采样近似值；配套 SRT/VTT 可辅助检查偏移和遗漏。</p><div id="alignment-rows"></div></section>`);
    $('#alignment-differences').onchange=renderAlignment;
    renderAlignment();
  }
  $('.report-side').insertAdjacentHTML('beforeend',`<details class="card applied-options"><summary>本次检查参数</summary><p class="help">${report.options.scene_check?'镜头切分已启用':'镜头切分未启用'} · ${report.options.freeze_check?`静止提示 ${report.options.freeze_seconds} 秒`:'静止检查未启用'}<br>字幕边距 ${(report.options.safe_margin*100).toFixed(0)}% · 文字高度 ${(report.options.min_text_height*100).toFixed(1)}%<br>字幕速度 ${report.options.max_cps} 字/秒 · AI 画面最多 ${report.options.vision_limit} 帧<br>期望比例 ${escapeHTML(report.options.expected_aspect)}<br>${report.options.audio_check?'响度声道检查已启用':'响度声道检查未启用'} · ${report.options.caption_check?'字幕版式检查已启用':'字幕版式检查未启用'} · ${report.options.alignment_check?'口播字幕对照已启用':'口播字幕对照未启用'}</p><p class="help">人工复核状态不会修改原始视频或自动改变 AI 结论。SRT 为自动转写，使用前请校对。</p></details>`);
}

function renderAlignment() {
  const rows=reportJob.report.alignment.filter(row=>!$('#alignment-differences').checked||row.status!=='matched');
  const labels={matched:'文字匹配',different:'文字差异',timing:'时间偏移候选',unmatched:'未对齐 / 需核对'};
  $('#alignment-rows').innerHTML=rows.length?rows.map(row=>`<div class="alignment-row"><button class="text-button" data-seek="${row.start}">${timestamp(row.start)} ↗</button><div><span>字幕</span><p>${escapeHTML(row.caption)}</p><span>同期口播转写</span><p>${escapeHTML(row.speech||'没有同期转写证据')}</p></div><small class="alignment-status ${row.status}">${labels[row.status]}${row.similarity==null?'':`<br>相似度 ${Math.round(row.similarity*100)}%`}</small></div>`).join(''):'<p class="no-issues">当前没有待核实对照项。仍需人工确认专名、数字和语音识别准确性。</p>';
}

function showFrameEvidence(issue) {
  const panel=$('#frame-evidence');
  if(!panel || issue.source!=='frame') return;
  const frame=reportJob.report.frames.find(f=>f.id===issue.source_id);
  if(!frame) return;
  const margin=(reportJob.report.options?.safe_margin||.05)*1000;
  const boxes=(frame.boxes||[]).filter(box=>box.bounds[1]>=.55);
  panel.innerHTML=`<div class="card-heading"><h2>画面证据 · ${timestamp(frame.start)}</h2><button class="text-button" id="close-frame-evidence">收起</button></div><div class="evidence-image"><img src="/api/jobs/${reportJob.id}/frames/${frame.id}" alt="问题对应视频帧"><svg viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-hidden="true"><rect class="safe-zone" x="${margin}" y="${margin}" width="${1000-2*margin}" height="${1000-2*margin}"/>${boxes.map(box=>`<rect class="caption-box" x="${box.bounds[0]*1000}" y="${box.bounds[1]*1000}" width="${(box.bounds[2]-box.bounds[0])*1000}" height="${(box.bounds[3]-box.bounds[1])*1000}"/>`).join('')}</svg></div><p class="help">绿虚线为本次自设安全区，橙框为 OCR 底部文字位置，仅用于人工复看。</p>`;
  panel.hidden=false;
  $('#close-frame-evidence').onclick=()=>{panel.hidden=true;};
}
