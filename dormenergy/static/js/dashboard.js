'use strict';
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const html = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = name => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const num = (value, decimals = 2) => Number(value || 0).toLocaleString('en-PH', {minimumFractionDigits:decimals, maximumFractionDigits:decimals});
const pesos = value => `${Number(value)<0?'−':''}₱${num(Math.abs(Number(value)))}`;
const exactPesos = value => Math.abs(Number(value))>0 && Math.abs(Number(value))<.01 ? `₱${num(Math.abs(Number(value)),6)}` : pesos(Math.abs(Number(value)));
const date = value => value ? new Date(value).toLocaleString('en-PH', {timeZone:'Asia/Manila',month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}) : 'No reading yet';
const titleCase = value => String(value || 'unknown').replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase());
const badge = value => `<span class="badge badge-${html(value)}"><span class="mini-dot"></span>${html(titleCase(value))}</span>`;
const empty = (message, cols) => cols ? `<tr><td colspan="${cols}" class="empty-state">${html(message)}</td></tr>` : `<div class="empty-state">${html(message)}</div>`;
let state, view = 'overview', period = 'daily', overviewChart, detailChart, snapshotBusy = false, confirmCallback, toastTimeout;
let requestReference = crypto.randomUUID();
let archivedUnits = [];
let forecastChart, forecastTestChart;
const titles = {
  overview:['Dormitory overview','A clear view of your units, usage, and prepaid energy.'],
  units:['Dormitory units','Every unit, connected in one place.'],
  consumption:['Consumption insights','Understand the patterns behind your energy use.'],
  loads:['Prepaid loads','Manage electricity credit with a clear approval trail.'],
  transactions:['Transaction history','Every credit and deduction, accounted for.'],
  forecast:['Consumption forecasting','Prepare for a more predictable energy future.'],
  devices:['Device monitoring','Visibility from meter to gateway to dashboard.'],
  notifications:['Workspace notifications','Stay informed about your accounts and devices.'],
  settings:['Electricity rates','Manage pricing with an auditable effective-date history.']
};

function toast(message, error = false) {
  clearTimeout(toastTimeout);
  $('#toast').textContent = message;
  $('#toast').classList.toggle('error', error);
  $('#toast').hidden = false;
  toastTimeout = setTimeout(() => $('#toast').hidden = true, 6000);
}
async function api(path, payload) {
  const response = await fetch(path, {
    method: payload ? 'POST' : 'GET', credentials:'same-origin',
    headers: payload ? {'Content-Type':'application/json','X-CSRFToken':$('meta[name="csrf-token"]').content} : {},
    body: payload ? JSON.stringify(payload) : undefined
  });
  if (response.status === 401) { window.location.assign('/login'); throw new Error('Your session expired.'); }
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'The request could not be completed.');
  return result;
}
function setView(next) {
  if (!$('#view-'+next)) return;
  view = next;
  $$('.view').forEach(el => el.hidden = el.id !== 'view-'+next);
  $$('.nav-link').forEach(el => { el.classList.toggle('active', el.dataset.view === next); el.setAttribute('aria-current',el.dataset.view === next ? 'page' : 'false'); });
  $('#breadcrumb-title').textContent = next === 'settings' ? 'Electricity rates' : titleCase(next);
  let [title, description] = titles[next];
  if (next === 'overview' && state?.user.role === 'tenant') {
    title = 'Your energy, at a glance'; description = 'Stay connected to your consumption and in control of your credit.';
  }
  $('#page-title').textContent = title;
  $('#page-description').textContent = description;
  $('#sidebar').classList.remove('is-open');
  $('#sidebar-scrim').hidden = true;
  $('#mobile-menu').setAttribute('aria-expanded','false');
  if (next === 'consumption' && state) renderDetailChart();
  if (next === 'settings') loadRates();
  if (next === 'forecast' && state) renderForecast(state.forecast);
}
function chartConfiguration(series, canvas, label) {
  const ctx = canvas.getContext('2d');
  const gradient = ctx.createLinearGradient(0,0,0,260);
  gradient.addColorStop(0,'rgba(21,155,125,.13)'); gradient.addColorStop(1,'rgba(21,155,125,0)');
  return {
    type:'line', data:{labels:series.labels,datasets:[{label,data:series.values,borderColor:'#159b7d',backgroundColor:gradient,fill:true,tension:.32,borderWidth:2,pointRadius:3,pointBackgroundColor:'#fff',pointBorderWidth:1.5,pointHoverRadius:5,spanGaps:false}]},
    options:{responsive:true,maintainAspectRatio:false,animation:false,interaction:{intersect:false,mode:'index'},
      plugins:{legend:{display:false},tooltip:{backgroundColor:'#173143',padding:11,displayColors:false,callbacks:{label:c=>`${num(c.parsed.y,3)} kWh`}}},
      scales:{x:{grid:{display:false},border:{display:false},ticks:{font:{size:9},color:'#9aa8b1',maxRotation:0,maxTicksLimit:9}},y:{beginAtZero:true,border:{display:false},grid:{color:'#eef2f4',drawTicks:false},ticks:{font:{size:9},color:'#9aa8b1',padding:10,maxTicksLimit:5}}}
    }
  };
}
function renderOverviewChart() {
  const series = state.charts[period];
  $('#chart-total').textContent = num(series.values.reduce((a,b)=>a+(b || 0),0));
  $('#chart-total-period').textContent = {hourly:'in the last 24 hours',daily:'in the last 7 days',history:'in the last 30 days'}[period];
  $('#chart-note').textContent = `${state.units.some(u=>u.source==='simulation') ? 'Includes synthetic simulation readings' : 'Recorded consumption'} · Asia/Manila · gaps indicate missing observations`;
  if (!overviewChart) overviewChart = new Chart($('#overview-chart'),chartConfiguration(series,$('#overview-chart'),'Actual consumption'));
  else { overviewChart.data.labels=series.labels; overviewChart.data.datasets[0].data=series.values; overviewChart.update(); }
}
function renderDetailChart() {
  const series = state.charts[$('#detail-period').value];
  if (!detailChart) detailChart = new Chart($('#detail-chart'),chartConfiguration(series,$('#detail-chart'),'Actual consumption'));
  else { detailChart.data.labels=series.labels; detailChart.data.datasets[0].data=series.values; detailChart.update(); }
}
function renderForecast(data) {
  $('#forecast-summary-status').textContent = data.status === 'available' ? `${num(data.points[0].kwh,3)} kWh · next full hour` : data.message;
  $('#forecast-summary-copy').textContent = data.synthetic ? 'Synthetic demonstration · historical test results are not real-meter accuracy.' : data.detail;
  $('#forecast-status').textContent = data.message;
  $('#forecast-description').textContent = data.detail;
  $('#forecast-source').textContent = data.source === 'simulation' ? 'SYNTHETIC DEMONSTRATION · Trained on simulated readings. These results do not establish real-meter accuracy.' : data.source === 'hardware' ? 'Hardware readings · Evaluate performance for your meter and study conditions.' : '';
  const busy = data.job && ['pending','running'].includes(data.job.status);
  const button = $('#train-forecast');
  if (button) {button.disabled = busy || state.units.length !== 1;button.innerHTML = icon('bolt') + (busy?' Training in progress':' Train XGBoost');}
  $('#forecast-job').hidden = !data.job;
  $('#forecast-job').dataset.status = data.job?.status || '';
  $('#forecast-job').textContent = data.job ? `${titleCase(data.job.status)} · ${data.job.message}` : '';
  $('#forecast-next').textContent = data.points.length ? `${num(data.points[0].kwh,3)} kWh` : 'Not available';
  $('#forecast-next-time').textContent = data.points.length ? date(data.points[0].timestamp) : '';
  $('#forecast-duration').textContent = data.remaining_duration === null ? 'Cannot yet be determined' : data.remaining_duration === 0 ? 'Credit exhausted' : `${num(data.remaining_duration/3600,1)} hours`;
  $('#forecast-horizon').textContent = data.horizon_hours ? `Within ${data.horizon_hours} upcoming hours · estimated` : 'Awaiting a covered forecast horizon';
  const report = data.model;
  $('#forecast-errors').textContent = report ? `${num(report.test.mae,4)} / ${num(report.test.rmse,4)} kWh` : 'Not evaluated';
  $('#forecast-mape').textContent = report ? `MAPE ${report.test.mape === null?'not defined':num(report.test.mape,1)+'%'} · ${report.test.mape_zero_excluded} zero hours excluded` : '';
  $('#forecast-model-note').textContent = report ? `${report.evaluation} · Trained ${date(report.trained_at)} · ${report.coverage.complete_hours} covered hours.` : 'Training uses earlier consumption. Historical evaluation uses later, held-out hours.';
  $('#forecast-future-card').hidden = !data.points.length;
  $('#forecast-test-card').hidden = !report;
  if (view !== 'forecast') return;
  if (data.points.length) {
    const series = {labels:data.points.map(p=>date(p.timestamp)),values:data.points.map(p=>Number(p.kwh))};
    $('#forecast-generated').textContent = `${data.synthetic?'Synthetic demo':'Hardware-data estimate'} · Generated ${date(data.generated_at)} · later hours use earlier predictions`;
    if (!forecastChart) forecastChart = new Chart($('#forecast-chart'),chartConfiguration(series,$('#forecast-chart'),'Predicted kWh'));
    else {forecastChart.data.labels=series.labels;forecastChart.data.datasets[0].data=series.values;forecastChart.update();}
    forecastChart.resize();
  }
  if (report) {
    const series = {labels:report.comparison.map(p=>date(p.timestamp)),values:report.comparison.map(p=>p.actual)};
    $('#forecast-test-window').textContent = `${date(report.splits.test.start)} – ${date(report.splits.test.end)} · ${report.test.samples} held-out hours (showing ${report.comparison.length}) · ${data.synthetic?'synthetic observations':'hardware observations'}`;
    const persistence = report.baselines.persistence;
    const seasonal = report.baselines.seasonal_24h;
    $('#forecast-baselines').textContent = `Baseline MAE: previous hour ${num(persistence.mae,4)} kWh (${persistence.samples} hours)${seasonal?`; same hour yesterday ${num(seasonal.mae,4)} kWh (${seasonal.samples} hours)`:''}. Lower is better. These metrics evaluate the next hour; recursive multi-hour error has not been evaluated.`;
    if (!forecastTestChart) {
      const config=chartConfiguration(series,$('#forecast-test-chart'),'Observed kWh');
      config.data.datasets[0].fill=false;
      config.data.datasets.push({label:'Held-out XGBoost prediction',data:report.comparison.map(p=>p.predicted),borderColor:'#d89b37',borderDash:[5,4],pointRadius:0,tension:.2,borderWidth:2});
      config.options.plugins.legend={display:true,labels:{boxWidth:16,font:{size:10}}};
      config.options.plugins.tooltip.callbacks.label=c=>`${c.dataset.label}: ${num(c.parsed.y,4)} kWh`;
      forecastTestChart=new Chart($('#forecast-test-chart'),config);
    } else {forecastTestChart.data.labels=series.labels;forecastTestChart.data.datasets[0].data=series.values;forecastTestChart.data.datasets[1].data=report.comparison.map(p=>p.predicted);forecastTestChart.update();}
    forecastTestChart.resize();
  }
}
function activityRow(t) {
  return `<div class="activity-row"><span class="activity-icon ${Number(t.amount)>0?'green':''}">${icon(Number(t.amount)>0?'wallet':'bolt')}</span><div class="activity-copy"><strong>${html(t.unit)} · ${t.kind==='consumption'?'Energy consumption':t.kind==='demo_credit'?'Sample opening credit':'Prepaid top-up'}</strong><small>${html(date(t.timestamp))}</small></div><div class="activity-amount ${Number(t.amount)>0?'positive':'negative'}">${Number(t.amount)>0?'+':'−'}${exactPesos(t.amount)}<small>${html(t.kind==='demo_credit'?'Simulation':'Recorded')}</small></div></div>`;
}
function alertRow(n) {
  return `<div class="alert-row">${icon(n.kind.includes('balance')?'wallet':n.kind.includes('power')?'bolt':'info')}<div><strong>${html(n.message)}</strong><small>${html(date(n.timestamp))}</small></div></div>`;
}
function render() {
  const admin = state.user.role === 'admin';
  const units = state.units;
  $('#mode-pill').innerHTML = icon('info') + (state.simulation ? 'Simulation environment' : 'Hardware environment');
  $('#stat-balance').textContent = pesos(state.summary.balance);
  $('#stat-energy').textContent = num(state.summary.today_kwh);
  $('#stat-power').textContent = num(state.summary.power_w,0);
  $('#stat-devices').innerHTML = admin ? `${state.summary.online}<small>/ ${units.length} online</small>` : html(titleCase(units[0]?.power_status));
  $('#stat-device-footer').textContent = admin ? `${units.length-state.summary.online} offline or awaiting readings` : (units[0]?.online ? 'Device is communicating' : 'Device offline or awaiting readings');
  const streaming = units.some(u=>u.online);
  $('#live-label').textContent = streaming ? (state.simulation ? 'Simulation streaming · synthetic measurements' : 'Live meter monitoring') : 'Awaiting fresh meter readings';
  $('#live-dot').style.backgroundColor = streaming ? '#25ae89' : '#b6a981';
  $('#last-updated').textContent = `Updated ${new Date(state.updated_at).toLocaleTimeString('en-PH',{timeZone:'Asia/Manila',hour:'numeric',minute:'2-digit',second:'2-digit'})}`;
  $('#unit-count').textContent = units.length;
  $('#pending-count').textContent = state.summary.pending;
  $('#pending-count').hidden = !state.summary.pending;
  $('#notification-dot').hidden = !state.notifications.length;
  if (admin && !$('#unit-filter').value) {
    $('#unit-filter').innerHTML = '<option value="">All dormitory units</option>'+units.map(u=>`<option value="${u.id}">${html(u.name)}</option>`).join('');
  }
  $('#export-link').href = '/api/transactions/export' + ($('#unit-filter')?.value ? `?unit_id=${$('#unit-filter').value}` : '');
  $('#unit-cards').innerHTML = units.map(u=>`
    <div class="unit-card"><div class="unit-card-head"><span class="unit-avatar">${icon('home')}</span><div><h3>${html(u.name)}</h3><div class="unit-room">${html(u.room)}</div></div>${badge(u.power_status)}</div>
    <div class="unit-balance-label">Available prepaid balance</div><div class="unit-balance">${pesos(u.balance)}</div><div class="balance-track"><div class="balance-fill ${Number(u.balance)<=Number(u.threshold)?'low':''}" data-progress="${Math.max(0,Math.min(100,Number(u.balance)/1000*100))}"></div></div>
    <div class="unit-metrics"><div><span>Used today</span><strong>${num(u.today_kwh)} <small>kWh</small></strong></div><div><span>Current power</span><strong>${u.power===null?'—':num(u.power,0)} <small>W</small></strong></div></div><div class="unit-card-foot"><span>${html(u.source==='simulation'?'Simulated meter':'Hardware meter')}</span><button class="text-button" data-unit="${u.id}">${admin?'View unit':'View consumption'} ${icon('arrow')}</button></div></div>`).join('');
  if (!units.length) $('#unit-cards').innerHTML = empty('No active units. Register a unit or restore one from Dormitory units.');
  $$('[data-progress]').forEach(el=>el.style.width=el.dataset.progress+'%');
  $('#recent-transactions').innerHTML = state.transactions.slice(0,4).map(activityRow).join('') || empty('No activity yet.');
  const lowBalanceAlerts = units.filter(u=>Number(u.balance)<=Number(u.threshold)).map(u=>({kind:'low_balance',message:`${u.name}: prepaid credit is ${pesos(u.balance)}. A top-up may be needed.`,timestamp:state.updated_at}));
  const offlineAlerts = units.filter(u=>!u.online).map(u=>({kind:'device',message:`${u.name}: no fresh meter reading. Live measurements are unavailable.`,timestamp:u.reading_at}));
  const alerts = [...lowBalanceAlerts,...offlineAlerts,...state.notifications].slice(0,3);
  $('#overview-alerts').innerHTML = alerts.map(alertRow).join('') || empty('All clear. No account or device alerts.');
  if (admin) renderUnits();
  $('#loads-table').innerHTML = state.requests.map(r=>`<tr><td><strong>${html(r.reference)}</strong><small>${html(date(r.timestamp))}</small></td><td>${html(r.unit)}<small>${html(r.tenant)}</small></td><td><strong>${pesos(r.amount)}</strong></td><td>${badge(r.status)}</td><td class="wrap-cell">${admin && r.status==='pending'?`<div class="review-buttons"><button class="btn btn-primary btn-small" data-review="${r.id}" data-decision="approved">Approve</button><button class="btn btn-danger btn-small" data-review="${r.id}" data-decision="rejected">Reject</button></div>${r.note?`<small>${html(r.note)}</small>`:''}`:html(r.note || 'Reviewed by administrator')}</td></tr>`).join('') || empty('No load requests yet. New requests will appear here.',5);
  $('#transactions-table').innerHTML = state.transactions.map(t=>`<tr><td>${html(date(t.timestamp))}</td><td>${html(t.unit)}</td><td class="wrap-cell">${html(t.description)}</td><td class="${Number(t.amount)>0?'positive':'negative'}">${Number(t.amount)>0?'+':'−'}${exactPesos(t.amount)}</td><td>${pesos(t.balance_after)}</td></tr>`).join('') || empty('No transactions recorded yet.',5);
  $('#notifications-list').innerHTML = state.notifications.map(alertRow).join('') || empty('No notifications yet.');
  $('#measurement-cards').innerHTML = units.map(u=>`<article class="card measurement-card"><h3>${html(u.name)} ${badge(u.online?'connected':'offline')}</h3><div class="measurement-values"><div><span>Voltage</span><strong>${u.online?num(u.voltage,1):'—'} <small>V</small></strong></div><div><span>Current</span><strong>${u.online?num(u.current,3):'—'} <small>A</small></strong></div><div><span>Active power</span><strong>${u.power===null?'—':num(u.power,0)} <small>W</small></strong></div><div><span>Cumulative energy</span><strong>${num(u.energy,3)} <small>kWh</small></strong></div></div><p class="small muted">${html(u.source==='simulation'?'Synthetic readings':'Hardware readings')} · Last measurement ${html(date(u.reading_at))}</p></article>`).join('');
  $('#device-cards').innerHTML = units.map(u=>`<article class="card device-card"><div class="device-card-heading"><h3>${html(u.name)}</h3>${badge(u.online?'connected':'offline')}</div><dl><dt>ESP32 identifier</dt><dd>${html(u.device)}</dd><dt>Data source</dt><dd>${html(u.source==='simulation'?'Simulation · no physical hardware':'Hardware · authenticated API')}</dd><dt>Raspberry Pi gateway</dt><dd>${u.source==='simulation'?'Simulated gateway':u.gateway_online?'Recent authenticated communication':'Awaiting communication'}</dd><dt>Last received / last measured</dt><dd>${html(date(u.last_seen))}<br>${html(date(u.reading_at))}</dd><dt>Synchronization</dt><dd>${u.online?'Fresh readings':'Offline or stale; ordered replay required'}</dd><dt>Last reported supply state</dt><dd>${html(titleCase(u.last_power_status))}${u.online?'':' · may be stale'}</dd></dl></article>`).join('');
  $('#events-list').innerHTML = state.events.map(e=>`<div class="activity-row"><span class="activity-icon">${icon('chip')}</span><div class="activity-copy"><strong>${html(titleCase(e.kind))} · ${html(titleCase(e.state))}</strong><small>${html(e.message)} · ${html(date(e.timestamp))}</small></div></div>`).join('') || empty('No device events recorded.');
  const rateInput = $('#rate');
  if (rateInput && !rateInput.value) rateInput.value = state.rate;
  renderOverviewChart();
  if (view==='consumption') renderDetailChart();
  renderForecast(state.forecast);
}
function renderUnits() {
  const archived = $('#unit-status').value === 'archived';
  const units = archived ? archivedUnits : state.units;
  $('#units-table').innerHTML = units.map(u=>`<tr><td><strong>${html(u.name)}</strong><small>${html(u.room)}${archived?` · Removed ${html(date(u.archived_at))}`:''}</small></td><td>${html(u.tenant)}</td><td><strong>${pesos(u.balance)}</strong></td><td>${html(u.device)}<small>${html(u.source)}</small></td><td>${badge(archived?'archived':u.power_status)}</td><td><div class="unit-actions">${archived?`<a class="text-button" href="/api/transactions/export?unit_id=${u.id}&include_archived=true">Export history</a><button class="btn btn-secondary btn-small" data-restore="${u.id}">Restore unit</button>`:`${u.tenant==='Unassigned'?`<button class="btn btn-secondary btn-small" data-assign="${u.id}">Assign tenant</button>`:`<button class="text-button" data-unit="${u.id}">View ${icon('arrow')}</button>`}<button class="btn btn-danger btn-small" data-remove="${u.id}">Remove unit</button>`}</div></td></tr>`).join('') || empty(archived?'No archived units.':'No active units. Register a unit to get started.',6);
}
async function refresh() {
  if(snapshotBusy) return;
  snapshotBusy=true;
  try {
    const filter = $('#unit-filter')?.value;
    state=await api('/api/snapshot'+(filter?`?unit_id=${filter}`:''));
    if (state.user.role === 'admin' && $('#unit-status').value === 'archived') {
      archivedUnits = (await api('/api/units?status=archived')).units;
    }
    $('#connection-alert').hidden=true;
    render();
  } catch(error) {
    $('#connection-alert').hidden=false;
    $('#live-label').textContent='Live updates paused';
    $('#live-dot').style.backgroundColor='#c58d57';
    if (!state) $('#unit-cards').innerHTML=empty('Unable to load your workspace. Retrying shortly.');
    console.error('Workspace update failed:',error.message);
  } finally { snapshotBusy=false; }
}
async function loadRates() {
  if(!$('#rate-history'))return;
  try {
    const data=await api('/api/rates');
    $('#rate-history').innerHTML=data.rates.map(r=>`<div class="activity-row"><span class="activity-icon green">${icon('bolt')}</span><div class="activity-copy"><strong>${pesos(r.rate)} / kWh</strong><small>Effective ${html(date(r.effective_at))}${new Date(r.effective_at)>new Date()?' · Scheduled':''}</small></div></div>`).join('')||empty('No rates recorded.');
  }catch(error){toast(error.message,true);}
}
function confirm(title, description, callback) {
  $('#confirm-title').textContent=title;
  $('#confirm-description').textContent=description;
  $('#confirm-dialog .form-error').hidden=true;
  confirmCallback=callback;
  $('#confirm-dialog').showModal();
}
$('#confirm-action').addEventListener('click',async()=>{
  const button=$('#confirm-action');button.disabled=true;
  try { await confirmCallback();$('#confirm-dialog').close();await refresh(); }
  catch(error){const el=$('#confirm-dialog .form-error');el.textContent=error.message;el.hidden=false;}
  finally{button.disabled=false;}
});
document.addEventListener('click',event=>{
  const button=event.target.closest('button');if(!button)return;
  if(button.dataset.view)setView(button.dataset.view);
  if(button.dataset.open){const dialog=$('#'+button.dataset.open);dialog.querySelectorAll('.form-error').forEach(el=>el.hidden=true);dialog.showModal();}
  if(button.hasAttribute('data-close'))button.closest('dialog')?.close();
  if(button.dataset.period){period=button.dataset.period;$$('[data-period]').forEach(b=>b.classList.toggle('active',b===button));if(state)renderOverviewChart();}
  if(button.dataset.unit){if(state?.user.role==='admin'){$('#unit-filter').value=button.dataset.unit;refresh();setView('overview');}else setView('consumption');}
  if(button.dataset.amount)$('#load-amount').value=button.dataset.amount;
  if(button.dataset.review){const item=state.requests.find(r=>r.id===Number(button.dataset.review));const decision=button.dataset.decision;
    confirm(`${decision==='approved'?'Approve':'Reject'} this load request?`,`${item.unit} · ${pesos(item.amount)}. ${decision==='approved'?'This adds prepaid credit once and records a transaction.':'No credit will be added. The tenant will be notified.'}`,async()=>{const result=await api(`/api/load-requests/${item.id}/review`,{decision});toast(result.message);});}
  if(button.dataset.assign){const unit=state.units.find(u=>u.id===Number(button.dataset.assign));$('#tenant-form input[name="unit_id"]').value=unit.id;$('#assignment-label').textContent=`Create a tenant account for ${unit.name}.`;$('#tenant-dialog .form-error').hidden=true;$('#tenant-dialog').showModal();}
  if(button.dataset.remove){const unit=state.units.find(u=>u.id===Number(button.dataset.remove));
    confirm(`Remove ${unit.name}?`,'This archives the unit and retains its balance and history. Tenant access and meter updates will stop. Pending load requests will be rejected. You can restore the unit from Archived units.',async()=>{const result=await api(`/api/units/${unit.id}/archive`,{});$('#unit-filter').value='';toast(result.message);});}
  if(button.dataset.restore){const unit=archivedUnits.find(u=>u.id===Number(button.dataset.restore));
    confirm(`Restore ${unit.name}?`,'This restores the existing unit and balance. Its assigned tenant can sign in again, and meter updates can resume. Previously rejected requests stay rejected.',async()=>{const result=await api(`/api/units/${unit.id}/restore`,{});$('#unit-filter').value='';toast(result.message);});}
});
function bindForm(selector, endpoint, payloadBuilder, onSuccess) {
  const form=$(selector);if(!form)return;
  form.addEventListener('submit',async event=>{
    event.preventDefault();const button=form.querySelector('button[type="submit"], .dialog-actions .btn-primary');const errorBox=form.querySelector('.form-error');
    button.disabled=true;errorBox.hidden=true;
    try{const values=Object.fromEntries(new FormData(form));const result=await api(typeof endpoint==='function'?endpoint(values):endpoint,payloadBuilder(values));form.closest('dialog').close();form.reset();toast(result.message);onSuccess?.();await refresh();}
    catch(error){errorBox.textContent=error.message;errorBox.hidden=false;}
    finally{button.disabled=false;}
  });
}
bindForm('#load-form','/api/load-requests',v=>({amount:v.amount,note:v.note,reference:requestReference}),()=>{requestReference=crypto.randomUUID();setView('loads');});
bindForm('#unit-form','/api/units',v=>v,()=>{if($('#unit-filter'))$('#unit-filter').value='';setView('units');});
bindForm('#tenant-form',v=>`/api/units/${v.unit_id}/tenant`,v=>({name:v.name,email:v.email,password:v.password}));
$('#rate-form')?.addEventListener('submit',event=>{
  event.preventDefault();const values=Object.fromEntries(new FormData(event.target));
  const payload={rate:values.rate};if(values.effective_at)payload.effective_at=new Date(values.effective_at).toISOString();
  confirm('Save this electricity rate?',`${pesos(values.rate)} per kWh, effective ${values.effective_at?date(payload.effective_at):'now'}. Earlier charges remain unchanged.`,async()=>{const result=await api('/api/rates',payload);toast(result.message);await loadRates();});
});
$('#unit-filter')?.addEventListener('change',refresh);
$('#unit-status')?.addEventListener('change',refresh);
$('#train-forecast')?.addEventListener('click',async()=>{
  if (state.units.length !== 1) return;
  const button=$('#train-forecast');button.disabled=true;
  try {const result=await api(`/api/units/${state.units[0].id}/forecast/train`,{});toast(result.message);await refresh();}
  catch(error){toast(error.message,true);button.disabled=false;}
});
$('#detail-period').addEventListener('change',()=>{if(state)renderDetailChart();});
$('#mobile-menu').addEventListener('click',()=>{const open=$('#sidebar').classList.toggle('is-open');$('#sidebar-scrim').hidden=!open;$('#mobile-menu').setAttribute('aria-expanded',String(open));});
$('#sidebar-scrim').addEventListener('click',()=>{$('#sidebar').classList.remove('is-open');$('#sidebar-scrim').hidden=true;$('#mobile-menu').setAttribute('aria-expanded','false');});
$('#today-label span').textContent=new Date().toLocaleDateString('en-PH',{timeZone:'Asia/Manila',month:'short',day:'numeric',year:'numeric'});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
refresh();setInterval(()=>{if(!document.hidden)refresh();},10000);
