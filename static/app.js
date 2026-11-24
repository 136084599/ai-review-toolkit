const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const escapeHTML = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = name => `<svg aria-hidden="true"><use href="#i-${name}"/></svg>`;
const timestamp = value => `${Math.floor(value / 60).toString().padStart(2, '0')}:${Math.floor(value % 60).toString().padStart(2, '0')}`;
const states = {queued:'等待处理',running:'正在审核',completed:'预审完成',failed:'处理失败',cancelled:'已取消'};
const sourceLabels = {text:'稿件正文',title:'稿件标题',speech:'实际口播',ocr:'画面文字',caption:'底部字幕候选',subtitle:'上传字幕',frame:'画面证据',technical:'音画检测'};
const coverageStates = {complete:'已完成',sampled:'抽样检查',failed:'未完成',skipped:'未执行',not_applicable:'不适用'};
const severityLabels = {high:'重点问题',medium:'待核实',low:'文字建议'};
const editorialInstructions = '使用场景：央媒供稿、新闻报道、政务宣传。重点检查人名职务、机构名称、时间地点、数据单位、引述来源、标题与正文一致性。没有权威依据的事项标注待核实，不编造政策依据。';
let activePage = 'text', activeJob = null, reportJob = null, selectedFile = null, pollTimer = null, reportFilter = 'all', galleryCount = 12;

async function api(path, options = {}) {
  let response;
  try { response = await fetch(path, options); } catch { throw new Error('无法连接本地服务，请确认启动窗口仍在运行'); }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = body.detail;
    throw new Error(Array.isArray(detail) ? detail.map(x => x.msg).join('；') : detail || `请求失败（${response.status}）`);
  }
  return body;
}
function toast(message) {
  $('#toast').textContent = message; $('#toast').hidden = false;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => { $('#toast').hidden = true; }, 6500);
}
async function busy(button, callback) {
  const old = button.innerHTML;
  button.disabled = true;
  try { await callback(); } catch (error) { toast(error.message); }
  finally { button.disabled = false; button.innerHTML = old; }
}
function navigate(page) {
  activePage = page;
  if (page !== 'report') clearTimeout(pollTimer);
  $$('.page').forEach(el => { el.hidden = el.id !== `page-${page}`; });
  $$('.nav').forEach(el => el.classList.toggle('active', el.dataset.page === page));
  $('#breadcrumb').textContent = {text:'AI 审稿',video:'AI 审视频',history:'审核记录',settings:'模型设置',report:'预审报告'}[page];
  if (page === 'history') loadHistory().catch(error => toast(error.message));
  if (page === 'settings' || page === 'video') loadSettings().catch(error => toast(error.message));
  window.scrollTo({top:0, behavior:'instant'});
}
document.addEventListener('click', event => {
  const target = event.target.closest('[data-page]');
  if (target) navigate(target.dataset.page);
});
async function loadSettings() {
  const data = await api('/api/settings');
  $$('.connection').forEach(el => { el.textContent = data.configured ? 'DeepSeek 已配置' : 'DeepSeek 待配置'; });
  $('#text-model').value = data.model; $('#vision-model').value = data.vision_model;
  $('#asr-status').textContent = data.asr_ready ? '本地语音模型已就绪' : '语音模型未安装 · 查看模型设置';
  $('#model-status').textContent = `${data.asr_model} 中文多语言模型：${data.asr_ready ? '已安装，可以自动转写口播' : '未安装，视频口播审核会标为未完成'}`;
}
$('#text-content').addEventListener('input', () => { $('#word-count').textContent = `${$('#text-content').value.length.toLocaleString()} / 30,000`; });
$('#load-example').addEventListener('click', () => {
  if ($('#text-content').value.trim() && !confirm('替换当前输入的稿件为测试示例？')) return;
  $('#text-title').value = '示例｜城市书房让阅读触手可及';
  $('#text-content').value = '2026年2月30日，春和市新建的城市书房正式向公众开放。\n\n据工作人员介绍，书房面积约300平方米，设有80个阅读座位，藏书5000余册。项目于去年12月启动，历时三个多月建成。。\n\n今年，春和市计划新建城市书房12座，较去年增长百分之20%。市民可通过预约电话13800138000了解开放安排。';
  $('#text-reference').value = '参考资料（测试用，非真实新闻）：城市书房于2026年3月1日开放，面积300平方米，60个阅读座位，藏书5000余册。今年计划新建12座，去年新建10座。预约电话为工作人员个人手机，公开稿件请改用官方服务电话。';
  $('#text-content').dispatchEvent(new Event('input'));
  toast('已载入测试示例，可直接开始预审');
});
$('#text-file').addEventListener('change', async event => {
  const file = event.target.files[0]; if (!file) return;
  try {
    if (file.size > 150000) throw new Error('文本文件过大，请控制在 30,000 字以内');
    let content = await file.text();
    if (content.includes('\uFFFD')) content = new TextDecoder('gb18030').decode(await file.arrayBuffer());
    if (content.length > 30000) throw new Error('文本超过 30,000 字，请分段审核');
    $('#text-content').value = content; $('#text-title').value = file.name.slice(0,120);
    $('#text-content').dispatchEvent(new Event('input'));
  } catch (error) { toast(error.message); }
  event.target.value = '';
});
$('#text-form').addEventListener('submit', event => {
  event.preventDefault();
  busy(event.submitter, async () => {
    if (!$('#text-content').value.trim()) throw new Error('请输入稿件正文');
    const job = await api('/api/reviews/text', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({
      title:$('#text-title').value || '未命名稿件',text:$('#text-content').value,reference:$('#text-reference').value,
      instructions:editorialInstructions + '\n' + $('#text-instructions').value, use_ai:$('#text-ai').checked
    })});
    openJob(job.id);
  });
});
function setVideo(file) {
  if (!file) return;
  if (!/\.(mp4|mov|mkv|webm|m4v)$/i.test(file.name)) return toast('请选择支持的视频文件');
  if (file.size > 512 * 1024 * 1024) return toast('视频不能超过 512 MB');
  selectedFile = file;
  $('#video-filename').textContent = file.name;
  $('#video-fileinfo').textContent = `${(file.size/1024/1024).toFixed(1)} MB · 点击可重新选择`;
}
$('#video-file').addEventListener('change', event => setVideo(event.target.files[0]));
['dragenter','dragover'].forEach(type => $('#dropzone').addEventListener(type, event => {event.preventDefault(); $('#dropzone').classList.add('dragging');}));
['dragleave','drop'].forEach(type => $('#dropzone').addEventListener(type, event => {event.preventDefault(); $('#dropzone').classList.remove('dragging');}));
$('#dropzone').addEventListener('drop', event => setVideo(event.dataTransfer.files[0]));
$('#video-form').addEventListener('submit', event => {
  event.preventDefault();
  busy(event.submitter, async () => {
    if (!selectedFile) throw new Error('请先选择待审核的视频');
    const form = new FormData(); form.append('file', selectedFile); form.append('title', $('#video-title').value);
    form.append('reference', $('#video-reference').value); form.append('instructions', editorialInstructions + '\n' + $('#video-instructions').value);
    form.append('interval', $('#sample-interval').value); form.append('use_ai', $('#video-ai').checked); form.append('vision', $('#video-vision').checked);
    form.append('options', JSON.stringify(collectVideoOptions()));
    const subtitle = $('#subtitle-file').files[0];
    if (subtitle) {
      if (subtitle.size > 600000) throw new Error('字幕文件过大');
      let text = await subtitle.text();
      if (text.includes('\uFFFD')) text = new TextDecoder('gb18030').decode(await subtitle.arrayBuffer());
      form.append('subtitles', text);
    }
    event.submitter.textContent = '正在上传视频…';
    const job = await api('/api/reviews/video', {method:'POST', body:form});
    openJob(job.id);
  });
});
$('#settings-form').addEventListener('submit', event => {
  event.preventDefault();
  busy(event.submitter, async () => {
    await api('/api/settings', {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({key:$('#api-key').value.trim(),model:$('#text-model').value.trim(),vision_model:$('#vision-model').value.trim()})});
    $('#api-key').value = ''; await loadSettings(); toast('配置已保存');
  });
});
$('#test-connection').addEventListener('click', event => busy(event.currentTarget, async () => {
  $('#connection-result').textContent = '正在连接 DeepSeek…';
  try {
    const data = await api('/api/settings/test', {method:'POST'});
    $('#connection-result').textContent = `连接成功 · 可用模型：${data.models.join('、')}`;
  } catch (error) { $('#connection-result').textContent = error.message; throw error; }
}));
$('#refresh-history').addEventListener('click', event => busy(event.currentTarget, loadHistory));
async function loadHistory() {
  const jobs = await api('/api/jobs');
  $('#history-list').innerHTML = jobs.length ? jobs.map(job => `<div class="history-row"><span class="row-icon">${icon(job.kind === 'video' ? 'video' : 'document')}</span><div><button class="history-title" data-job="${job.id}">${escapeHTML(job.title)}</button><small>${job.kind === 'video' ? '视频预审' : '稿件预审'}</small></div><span class="status-cell muted">${states[job.status]}${job.status==='running' ? ` · ${job.progress}%` : ''}</span><span class="history-date muted">${new Date(job.created).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'})}</span><div class="row-actions"><button class="text-button" data-job="${job.id}">查看</button><button class="text-button danger-text" data-delete="${job.id}">删除</button></div></div>`).join('') : `<div class="empty-state">${icon('history')}<h2>还没有审核记录</h2><p>提交第一份稿件或视频，预审报告会保存在这里。</p><button class="secondary" data-page="text">开始第一次预审</button></div>`;
}
$('#history-list').addEventListener('click', async event => {
  const job = event.target.closest('[data-job]'); if (job) openJob(job.dataset.job);
  const remove = event.target.closest('[data-delete]');
  if (remove && confirm('删除这条记录及对应的视频和截图？删除后无法恢复。')) await busy(remove, async () => {await api(`/api/jobs/${remove.dataset.delete}`,{method:'DELETE'});await loadHistory();toast('记录已删除');});
});
function openJob(id) {
  clearTimeout(pollTimer); activeJob = id; reportJob = null; reportFilter = 'all'; galleryCount = 12;
  navigate('report'); $('#report-content').innerHTML = '<div class="card progress-card"><span class="spinner"></span><p>正在读取审核任务…</p></div>'; $('#report-tools').innerHTML = '';
  pollJob(id);
}
async function pollJob(id) {
  try {
    const job = await api(`/api/jobs/${id}`);
    if (activeJob !== id || activePage !== 'report') return;
    if (job.status === 'completed') { reportJob = job; renderReport(job); return; }
    const running = ['queued','running'].includes(job.status);
    $('#report-content').innerHTML = `<div class="card progress-card">${running ? '<span class="spinner"></span>' : icon('shield')}<h2>${escapeHTML(job.title)}</h2><p>${escapeHTML(job.error || job.stage)}</p>${running ? `<progress max="100" value="${job.progress}"></progress><div class="progress-percent">${job.progress}% · 可以切换页面，稍后在审核记录中查看</div><p>视频识别需要一些时间，首次载入本地模型可能较慢。</p><button class="secondary" id="cancel-job">取消任务</button>` : '<button class="secondary" data-page="history">返回记录</button>'}</div>`;
    if (running) {
      $('#cancel-job').onclick = event => busy(event.currentTarget, async () => {await api(`/api/jobs/${id}/cancel`,{method:'POST'});toast('已请求取消，当前处理步骤结束后停止');});
      pollTimer = setTimeout(() => pollJob(id), 1600);
    }
  } catch (error) {
    if (activeJob !== id || activePage !== 'report') return;
    $('#report-content').innerHTML = `<div class="card empty-state"><h2>暂时无法读取任务</h2><p>${escapeHTML(error.message)}</p><button class="secondary" id="retry-poll">重试</button></div>`;
    $('#retry-poll').onclick = () => pollJob(id);
  }
}
function renderReport(job) {
  const report = job.report, isVideo = job.kind === 'video';
  contentScope=isVideo&&report.content_review&&job.payload.use_ai?'content':'all';contentDimension='all';
  $('#report-tools').innerHTML = `<a class="secondary" href="/api/jobs/${job.id}/export">导出报告</a><a class="secondary" href="/api/jobs/${job.id}/export?format=json">JSON</a><button class="secondary" id="print-report">打印 / PDF</button>`;
  $('#print-report').onclick = () => {
    reportFilter = 'all';
    contentScope='all';contentDimension='all';updateContentTabs();
    $$('[data-filter]').forEach(button => button.classList.toggle('active',button.dataset.filter === 'all'));
    renderIssues();
    window.print();
  };
  $('#report-content').innerHTML = `<div class="eyebrow">${isVideo ? 'VIDEO' : 'DOCUMENT'} REVIEW REPORT</div><h1 class="report-title">${escapeHTML(job.title)}</h1><div class="report-status"><span class="verdict">${escapeHTML(report.verdict)}</span>${Object.entries(report.counts).map(([key,value])=>`<span class="count ${key}">${severityLabels[key]} ${value}</span>`).join('')}</div>${report.warnings.length ? `<div class="warning-box"><b>以下部分需要补充复核</b>${report.warnings.map(x=>`<p>${escapeHTML(x)}</p>`).join('')}</div>` : ''}<div class="report-grid"><div class="report-main">${isVideo ? `<video class="video-player" id="review-video" controls preload="metadata" src="/api/jobs/${job.id}/media"></video><p class="help">若浏览器不支持原片编码，请用本地播放器打开原片；下方时间码与截图仍可用于定位。</p><div id="frame-gallery" class="frame-gallery"></div><button class="text-button load-more" id="more-frames">查看更多抽帧</button>` : `<div class="source-text" id="source-text">${escapeHTML(job.payload.text)}</div>`}<div class="filter-tabs">${['all','high','medium','low'].map(key=>`<button data-filter="${key}" class="${key==='all'?'active':''}">${key==='all'?'全部问题':severityLabels[key]}</button>`).join('')}</div><div id="issues-list"></div>${isVideo ? '<h2 class="report-subheading">识别文本 · 点击时间码回放</h2><div class="card transcript" id="transcript"></div>' : ''}</div><aside class="report-side"><div class="card"><h2>本次审核覆盖</h2>${report.coverage.map(c=>`<div class="coverage-item"><div><b>${escapeHTML(c.name)}</b><span class="coverage-state ${c.status}">${coverageStates[c.status]}</span></div><p>${escapeHTML(c.detail)}</p></div>`).join('')}<p class="report-notice">${escapeHTML(report.notice)}</p></div><div class="card"><h2>送审前，最后确认</h2><ul class="human-list"><li>名称、职务与机构准确</li><li>时间、地点、数据有依据</li><li>引述内容与来源已核实</li><li>地图、版权与隐私已确认</li><li>未覆盖部分已人工复核</li></ul><p class="report-notice">以上为人工复核提醒，不代表已完成。</p></div></aside></div>`;
  renderIssues();
  renderDecisionSummary();
  if (isVideo) {
    renderVideoDetails(job);
    renderContentReview(job);
    renderGallery(); $('#more-frames').onclick = () => {galleryCount += 24;renderGallery();};
    $('#transcript').innerHTML = report.sources.length ? report.sources.map(s=>`<button data-seek="${Number(s.start||0)}"><span>${timestamp(s.start||0)} · ${sourceLabels[s.kind]}</span>${escapeHTML(s.text)}</button>`).join('') : '<p class="muted">未获取有效语音或画面文字，请人工复核。</p>';
    $('#review-video').addEventListener('error', () => toast('当前浏览器无法播放该编码，请使用本地播放器查看原片'));
  }
}
function renderIssues() {
  const issues = reportJob.report.issues.filter(i=>(reportFilter==='all'||i.severity===reportFilter)&&matchesContentScope(i));
  $('#issues-list').innerHTML = issues.length ? issues.map(issue=>`<article class="card issue-card ${issue.severity}" tabindex="0" role="button" data-issue="${issue.id}" aria-label="定位：${escapeHTML(issue.title)}"><div class="issue-top"><span class="severity">${severityLabels[issue.severity]}</span>${escapeHTML(issue.title)}${issue.start != null ? `<span class="issue-time">${timestamp(issue.start)} ↗</span>` : ''}</div><div class="issue-meta">${escapeHTML(issue.category)} · ${sourceLabels[issue.source]} · ${issue.engine==='ai'?'AI 建议':'本地检测'}${issue.needs_verification?' · 请复核':''}</div><div class="evidence">${escapeHTML(issue.evidence)}</div>${contentEvidenceHTML(issue)}<p class="suggestion">${escapeHTML(issue.suggestion)}</p></article>`).join('') : '<div class="card no-issues">当前分类没有发现问题。请结合审核覆盖情况与原文继续复核。</div>';
  decorateDecisions();
}
function renderGallery() {
  const frames = reportJob.report.frames || [];
  $('#frame-gallery').innerHTML = frames.slice(0,galleryCount).map(frame=>`<button class="frame-button" data-seek="${frame.start}" title="跳转到 ${timestamp(frame.start)}"><img loading="lazy" src="/api/jobs/${reportJob.id}/frames/${frame.id}" alt="${timestamp(frame.start)} 的视频截图"><span>${timestamp(frame.start)}</span></button>`).join('');
  $('#more-frames').hidden = galleryCount >= frames.length;
}
function focusIssue(id) {
  const issue = reportJob.report.issues.find(x=>x.id===id); if (!issue) return;
  if (reportJob.kind==='video' && issue.start != null) { showFrameEvidence(issue); seek(issue.start); return; }
  const text = (issue.source === 'title' ? reportJob.payload.publication_title||reportJob.payload.title : reportJob.payload.text)||'';
  const target = issue.source === 'title' ? $('.report-title') : $('#source-text');
  let start = issue.offset ?? text.indexOf(issue.evidence);
  if (start < 0 || text.slice(start,start+issue.evidence.length) !== issue.evidence) start=text.indexOf(issue.evidence);
  if (start >= 0) {
    target.innerHTML = `${escapeHTML(text.slice(0,start))}<mark>${escapeHTML(issue.evidence)}</mark>${escapeHTML(text.slice(start+issue.evidence.length))}`;
    target.querySelector('mark').scrollIntoView({behavior:'smooth',block:'center'});
  }
}
function seek(time) { const video=$('#review-video'); if(video){video.currentTime=Math.max(0,time);video.scrollIntoView({behavior:'smooth',block:'center'});video.play().catch(()=>{});} }
$('#report-content').addEventListener('click', event => {
  if(event.target.closest('.decision-controls')) return;
  const filter=event.target.closest('[data-filter]'); if(filter){reportFilter=filter.dataset.filter;$$('[data-filter]').forEach(b=>b.classList.toggle('active',b===filter));renderIssues();}
  const issue=event.target.closest('[data-issue]'); if(issue)focusIssue(issue.dataset.issue);
  const target=event.target.closest('[data-seek]'); if(target)seek(Number(target.dataset.seek));
});
$('#report-content').addEventListener('keydown', event => { if(['Enter',' '].includes(event.key) && event.target.matches('[data-issue]')){event.preventDefault();focusIssue(event.target.dataset.issue);} });
loadSettings().catch(error => {$$('.connection').forEach(el=>el.textContent='本地服务未连接');toast(error.message);});
