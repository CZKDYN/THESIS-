'use strict';
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const html = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const icon = name => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
const numeric = value => value===null || value===undefined || typeof value==='boolean' || String(value).trim()==='' || !Number.isFinite(Number(value)) ? null : Number(value);
const num = (value, decimals = 2) => numeric(value)===null?'—':numeric(value).toLocaleString('en-PH',{minimumFractionDigits:decimals,maximumFractionDigits:decimals});
function money(value, absolute=false) {
  if(numeric(value)===null)return '—';
  const raw=String(value),match=raw.match(/^(-?)(\d+)(?:\.(\d+))?$/);
  if(!match)return (numeric(value)<0 && !absolute?'−':'')+'₱'+num(Math.abs(numeric(value)),2);
  let fraction=(match[3]||'').replace(/0+$/,'');fraction=fraction.padEnd(2,'0');
  return (match[1] && !absolute?'−':'')+'₱'+match[2].replace(/\B(?=(\d{3})+(?!\d))/g,',')+'.'+fraction;
}
const pesos = value => money(value);
const exactPesos = value => money(value,true);
const date = value => value && Number.isFinite(new Date(value).getTime()) ? new Date(value).toLocaleString('en-PH',{timeZone:'Asia/Manila',month:'short',day:'numeric',year:'numeric',hour:'numeric',minute:'2-digit'}) : 'Not recorded';
const titleCase = value => String(value || 'unknown').replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase());
const badge = value => `<span class="badge badge-${html(value)}"><span class="mini-dot"></span>${html(titleCase(value))}</span>`;
const empty = (message, cols) => cols ? `<tr><td colspan="${cols}" class="empty-state">${html(message)}</td></tr>` : `<div class="empty-state">${html(message)}</div>`;
let state, view = 'overview', period = 'daily', overviewChart, detailChart, snapshotBusy = false, confirmCallback, toastTimeout;
let requestReference = crypto.randomUUID();
let archivedUnits = [];
let forecastChart, forecastTestChart;
let refreshTimer,refreshQueued=false,refreshFailures=0,connectionPaused=false,lastSnapshotFilter='',inspectedUnit;
const deferredContent=new Map();
const reports={loads:{page:1,anchor:null,data:null,busy:false,reload:false},transactions:{page:1,anchor:null,data:null,busy:false,reload:false}};
const titles = {
  overview:['Operations center','Current meter data, prepaid credit and tasks for your selected units.'],
  units:['Dormitory units','Every unit, connected in one place.'],
  consumption:['Consumption insights','Understand the patterns behind your energy use.'],
  loads:['Prepaid loads','Manage electricity credit with a clear approval trail.'],
  transactions:['Transaction history','Every credit and deduction, accounted for.'],
  forecast:['Consumption forecasting','Prepare for a more predictable energy future.'],
  devices:['Device monitoring','Original measurements and backend receipts, with clear evidence limits.'],
  notifications:['Workspace notifications','Stay informed about your accounts and devices.'],
  settings:['Electricity rates','Your utility reference, billing validity, and rate history.']
};

function toast(message, error = false) {
  clearTimeout(toastTimeout);
  $('#toast').textContent = message;
  $('#toast').classList.toggle('error', error);
  $('#toast').hidden = false;
  toastTimeout = setTimeout(() => $('#toast').hidden = true, 6000);
}
async function api(path,payload) {
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
  try {
    const response=await fetch(path,{method:payload?'POST':'GET',credentials:'same-origin',cache:'no-store',signal:controller.signal,
      headers:payload?{'Content-Type':'application/json','X-CSRFToken':$('meta[name="csrf-token"]').content}:{},body:payload?JSON.stringify(payload):undefined});
    if(response.status===401){window.location.assign('/login');throw new Error('Your session expired.');}
    const result=await response.json().catch(()=>null);
    if(!result || typeof result!=='object' || Array.isArray(result))throw new Error('The service returned an unreadable response. Please retry.');
    if(result.password_change_required){window.location.assign('/account/password');throw new Error(result.error);}
    if(!response.ok)throw Object.assign(new Error(result.error||'The request could not be completed.'),{status:response.status});
    if(result.error && !result.message)throw new Error(result.error);
    return result;
  } catch(error) {
    if(error.name==='AbortError')throw new Error(payload?'The response could not be confirmed. Check recorded history before retrying.':'The service took too long to respond. Retry when available.');
    throw error;
  } finally {clearTimeout(timeout);}
}
function setView(next) {
  if (!$('#view-'+next)) return;
  view = next;
  $$('.view').forEach(el => el.hidden = el.id !== 'view-'+next);
  $$('.nav-link').forEach(el => { el.classList.toggle('active', el.dataset.view === next); if(el.dataset.view===next)el.setAttribute('aria-current','page');else el.removeAttribute('aria-current'); });
  $('#breadcrumb-title').textContent = next === 'settings' ? 'Electricity rates' : titleCase(next);
  let [title, description] = titles[next];
  if (next === 'overview' && state?.user.role === 'tenant') {
    title = 'Your energy, at a glance'; description = 'Stay connected to your consumption and in control of your credit.';
  }
  $('#page-title').textContent = title;
  $('#page-description').textContent = description;
  closeNavigation();
  history.replaceState(null,'',location.pathname+'#'+next);
  document.title=title+' · DormEnergy';
  $('#page-title').focus({preventScroll:true});
  window.scrollTo({top:0,behavior:'instant'});
  if((next==='loads' || next==='transactions') && state)loadReport(next);
  if (next === 'consumption' && state) renderDetailChart();
  if (next === 'settings') loadRates();
  if (next === 'forecast' && state) renderForecast(state.forecast);
  if (next === 'notifications' && state?.user.role==='admin') loadEmailStatus();
  if ($('#email-delivery-card')) $('#email-delivery-card').hidden = next!=='notifications';
}
function setContent(selector, markup) {
  const node=$(selector); if(!node)return;
  if(node.innerHTML===markup){deferredContent.delete(node);return;}
  if($('dialog[open]') || node.contains(document.activeElement)) { deferredContent.set(node,markup); return; }
  deferredContent.delete(node);node.innerHTML=markup;
}
function measurementLabel(unit) {
  if(connectionPaused)return ['unconfirmed','Connection unconfirmed'];
  const age=Date.now()-new Date(unit.reading_at).getTime();
  if(!unit.reading_at)return ['awaiting_readings','Awaiting readings'];
  if(!Number.isFinite(age))return ['unknown','Measurement time unavailable'];
  if(age<0)return ['clock_ahead','Measurement clock ahead'];
  if(age>=90000)return ['stale','Stale measurement'];
  return unit.online?['fresh','Fresh measurement']:['fresh','Fresh sample · no recent receipt'];
}
function measurementBadge(unit) {const [key,label]=measurementLabel(unit);return `<span class="badge badge-${key}"><span class="mini-dot"></span>${html(label)}</span>`;}
function livePower(unit) {return !connectionPaused && unit.online && measurementLabel(unit)[0]==='fresh'?numeric(unit.power):null;}
function notificationCategory(kind) {
  if(kind.includes('balance') || kind.includes('credit'))return 'balance';
  if(kind.startsWith('load_'))return 'loads';
  if(/device|power|meter|fresh/.test(kind))return 'devices';
  if(/account|security|password|activation/.test(kind))return 'account';
  return 'forecast';
}
function renderNotifications() {
  const category=$('#notification-category').value;
  const rows=state.notifications.filter(n=>!category || notificationCategory(n.kind)===category);
  setContent('#notifications-list',rows.map(alertRow).join('') || empty('No recent messages in this category.'));
  $('#notification-summary').textContent=`${rows.length} of the latest ${state.notifications.length} recorded messages. Read/unread state is not tracked.`;
}
function renderDevices() {
  const selected=$('#device-filter').value;
  const units=state.units.filter(u=>!selected || measurementLabel(u)[0]===selected);
  setContent('#device-cards',units.map(u=>`<article class="card device-card"><div class="device-card-heading"><h3>${html(u.name)}</h3>${measurementBadge(u)}</div><dl><dt>Meter identifier</dt><dd>${html(u.device)}</dd><dt>Data source</dt><dd>${u.source==='simulation'?'Synthetic simulation · no physical hardware':'Hardware source configured'}</dd><dt>Last measurement · original time</dt><dd>${html(date(u.reading_at))}</dd><dt>Last reading received by backend</dt><dd>${html(date(u.last_received||u.last_seen))}</dd><dt>API receipt evidence</dt><dd>${connectionPaused?'Backend temporarily unreachable; recent receipt cannot be confirmed.':u.gateway_online?(u.source==='simulation'?'Recent synthetic reading receipt':'Recent authenticated reading receipt'):'No recent reading receipt; this does not prove a physical disconnection.'}</dd><dt>Last reported supply</dt><dd>${html(titleCase(u.last_power_status))}${measurementLabel(u)[0]==='fresh'?'':' · last recorded, may be stale'}</dd><dt>Credential configuration</dt><dd>${u.source==='simulation'?'Not a physical meter':u.token_provisioned?'Device token provisioned; validity of an upstream credential is not reported here.':'Device token has not been provisioned.'}</dd></dl>${state.user.role==='admin' && u.source==='hardware'?`<div class="device-credentials"><button class="btn btn-secondary btn-small" data-device-token="${u.id}">${u.token_provisioned?'Manage device token':'Generate device token'}</button></div>`:''}</article>`).join('') || empty('No selected meters match this measurement state.'));
}
function render() {
  const admin=state.user.role==='admin', units=state.units;
  $('#mode-pill').innerHTML=icon('info')+(state.simulation?'Simulation environment':'Hardware mode');
  $('#system-data-mode').textContent=state.simulation?'Simulation · synthetic readings are labeled':'Hardware · connection evidence is shown per meter';
  $('#stat-balance').textContent=pesos(state.summary.balance);
  $('#stat-energy').textContent=num(state.summary.today_kwh);
  const powers=units.map(livePower).filter(value=>value!==null);
  $('#stat-power').textContent=powers.length?num(powers.reduce((a,b)=>a+b,0),0):'—';
  if($('#workspace-setup'))$('#workspace-setup').hidden=state.simulation || (units.length>0 && state.rate!==null && units.every(u=>u.tenant_id && u.token_provisioned && u.reading_at));
  $('#stat-devices').textContent=admin?`${connectionPaused?'Unconfirmed':units.filter(u=>u.online && measurementLabel(u)[0]==='fresh').length+' / '+units.length}`:titleCase(units[0]?.power_status);
  $('#stat-device-footer').textContent=connectionPaused?'Backend updates paused; live status unavailable':admin?`${state.operations.recent_receipts} recent receipts · ${units.length-state.operations.recent_receipts} without recent receipts`:(units[0]?.online?'Fresh data from your meter':'Last reported supply may be stale');
  $('#live-label').textContent=connectionPaused?'Backend updates paused':units.some(u=>measurementLabel(u)[0]==='fresh')?(state.simulation?'Synthetic test measurements':'Fresh meter data received'):'Awaiting fresh meter data';
  $('#live-dot').style.backgroundColor=connectionPaused?'#986712':units.some(u=>u.online)?'#08745f':'#6e7c86';
  $('#last-updated').textContent=`Last update ${date(state.updated_at)}`;
  $('#unit-count').textContent=units.length;
  $('#pending-count').textContent=state.summary.pending;$('#pending-count').hidden=!state.summary.pending;
  $('#notification-dot').hidden=true;
  if(admin && !$('#unit-filter').value) {
    const options='<option value="">All dormitory units</option>'+units.map(u=>`<option value="${u.id}">${html(u.name)}</option>`).join('');
    if($('#unit-filter').innerHTML!==options && document.activeElement!==$('#unit-filter'))$('#unit-filter').innerHTML=options;
  }
  if(admin) {
    for(const [field,id] of Object.entries({active_units:'op-units',stale_units:'op-stale',low_balance_units:'op-low',pending_requests:'op-pending',pending_activations:'op-activation'}))$('#'+id).textContent=state.operations[field];
  } else if(units[0]) {
    const unit=units[0],low=numeric(unit.balance)<=numeric(unit.threshold);
    $('.balance-context').dataset.warning=low;
    $('#balance-warning').textContent=numeric(unit.balance)<=0?'Credit exhausted — contact your administrator':low?'Your credit is below the warning threshold':'Your prepaid credit is above the warning threshold';
    $('#balance-threshold').textContent=`Low-credit threshold: ${pesos(unit.threshold)}. This is a credit warning, not a battery percentage or a guaranteed time remaining.`;
    $('#latest-topup').textContent=state.insights.latest_topup?`Latest recorded approved top-up: ${pesos(state.insights.latest_topup.amount)} · ${date(state.insights.latest_topup.timestamp)}`:'No approved top-up is recorded during your tenancy.';
  }
  $('#insight-seven').textContent=num(state.insights.seven_day_kwh,3);
  $('#insight-thirty').textContent=num(state.insights.thirty_day_kwh,3);
  $('#insight-expense').textContent=pesos(state.insights.deductions.seven_days);
  $('#insight-expense-month').textContent=`30 days: ${pesos(state.insights.deductions.thirty_days)} · actual ledger deductions`;
  setContent('#unit-cards',units.map(u=>`<article class="unit-card"><div class="unit-card-head"><span class="unit-avatar">${icon('home')}</span><div><h3>${html(u.name)}</h3><div class="unit-room">${html(u.room)}</div></div>${measurementBadge(u)}</div><p class="unit-tenant">${html(u.tenant)}</p><div class="unit-balance-label">Available prepaid balance</div><div class="unit-balance">${pesos(u.balance)}</div><p class="credit-note ${numeric(u.balance)<=numeric(u.threshold)?'low':''}">${numeric(u.balance)<=numeric(u.threshold)?'Low credit · ':'Warning threshold · '}${pesos(u.threshold)}</p><div class="unit-metrics"><div><span>Recorded today</span><strong>${num(u.today_kwh,3)} <small>kWh</small></strong></div><div><span>Current power</span><strong>${num(livePower(u),0)} <small>W</small></strong></div></div><p class="unit-time">Measured ${html(date(u.reading_at))}</p><div class="unit-card-foot"><span>${u.source==='simulation'?'Synthetic test data':'Hardware meter'}</span><button class="text-button" data-unit="${u.id}">${admin?'View unit':'View consumption'} ${icon('arrow')}</button></div></article>`).join('') || empty('No active units. Register a unit or restore one from Dormitory units.'));
  setContent('#recent-transactions',state.transactions.slice(0,4).map(activityRow).join('') || empty('No recorded credit or deductions yet.'));
  if(admin) {
    setContent('#overview-alerts',state.actions.map(item=>`<div class="action-row"><span class="action-count">${item.count}</span><div class="action-copy"><strong>${html(item.label)}</strong><p>${html(item.detail)}</p></div><button class="text-button" data-view="${html(item.view)}" data-action-key="${html(item.key)}" aria-label="${html(item.label)}">Review ${icon('arrow')}</button></div>`).join('') || empty('No current operational tasks for the selected units.'));
    renderUnits();
  } else {
    const notes=[];const u=units[0];
    if(u && numeric(u.balance)<=numeric(u.threshold))notes.push({kind:'low_balance',message:`Your ${pesos(u.balance)} credit is at or below the ${pesos(u.threshold)} warning threshold. Request a top-up; payment is verified manually.`,timestamp:state.updated_at});
    if(u && livePower(u)===null)notes.push({kind:'device',message:'Current power is unavailable because fresh meter data and a recent backend receipt cannot be confirmed.',timestamp:u.reading_at});
    if(state.insights.seven_day_kwh!==null)notes.push({kind:'insight',message:state.insights.comparison_note,timestamp:state.updated_at});
    setContent('#overview-alerts',notes.map(alertRow).join('') || empty('No current credit or measurement warnings.'));
  }
  renderNotifications();
  setContent('#measurement-cards',units.map(u=>`<article class="card measurement-card"><h3>${html(u.name)}</h3>${measurementBadge(u)}<div class="measurement-values"><div><span>Latest recorded voltage</span><strong>${num(u.voltage,1)} <small>V</small></strong></div><div><span>Latest recorded current</span><strong>${num(u.current,4)} <small>A</small></strong></div><div><span>Current power · fresh only</span><strong>${num(livePower(u),0)} <small>W</small></strong></div><div><span>Cumulative meter counter</span><strong>${num(u.energy,6)} <small>kWh</small></strong></div></div><p class="small muted">${u.source==='simulation'?'Synthetic readings':'Recorded meter readings'} · Measured ${html(date(u.reading_at))}. Stale electrical values are last recorded, not live.</p></article>`).join('') || empty('No meters registered for these units.'));
  renderDevices();
  setContent('#events-list',state.events.map(e=>`<div class="activity-row"><span class="activity-icon">${icon('chip')}</span><div class="activity-copy"><strong>${html(titleCase(e.kind))} · ${html(titleCase(e.state))}</strong><small>${html(e.message)} · ${html(date(e.timestamp))}</small></div></div>`).join('') || empty('No device events recorded.'));
  const rateInput=$('#rate');if(rateInput && !rateInput.value && state.rate!==null && document.activeElement!==rateInput)rateInput.value=state.rate;
  updateExport();renderOverviewChart();if(view==='consumption')renderDetailChart();renderForecast(state.forecast);
}

function cleanSeries(series) {return {...series,values:series.values.map(value=>numeric(value)!==null && numeric(value)>=0?numeric(value):null)};}
function bucketDate(value) {return value?.length===10?new Date(value+'T00:00:00+08:00').toLocaleDateString('en-PH',{timeZone:'Asia/Manila',month:'short',day:'numeric',year:'numeric'}):date(value);}
function chartSummary(series) {
  const observed=series.values.filter(value=>value!==null);
  return observed.length?`${num(observed.reduce((a,b)=>a+b,0),3)} kWh recorded in ${observed.length} of ${series.values.length} time buckets. Gaps mean unavailable observations, not zero consumption; totals may cover only some selected units. ${state.units.some(u=>u.source==='simulation')?'Includes synthetic test data.':''}`:'No observations in this period. Consumption is unavailable; it is not assumed to be zero.';
}
function chartConfiguration(series, canvas, label) {
  return {type:'line',data:{labels:series.labels,datasets:[{label,data:series.values,borderColor:'#087c64',backgroundColor:'rgba(8,124,100,.08)',fill:true,tension:.2,borderWidth:2,pointRadius:3,pointBackgroundColor:'#fff',pointBorderWidth:1.5,spanGaps:false}]},
    options:{responsive:true,maintainAspectRatio:false,animation:false,interaction:{intersect:false,mode:'index'},plugins:{legend:{display:false},tooltip:{backgroundColor:'#173143',padding:12,callbacks:{title:items=>series.timestamps?.[items[0].dataIndex]?bucketDate(series.timestamps[items[0].dataIndex]):items[0].label,label:c=>numeric(c.parsed.y)===null?'Observation unavailable':`${num(c.parsed.y,3)} kWh`,afterLabel:c=>series.observed_units?`${series.observed_units[c.dataIndex]} of ${state.units.length} selected units observed`:''}}},scales:{x:{grid:{display:false},ticks:{font:{size:12},color:'#526575',maxRotation:0,maxTicksLimit:6}},y:{beginAtZero:true,title:{display:true,text:'Energy · kWh',color:'#526575'},grid:{color:'#e7ecef'},ticks:{font:{size:12},color:'#526575',maxTicksLimit:5}}}}};
}
function renderOverviewChart() {
  const series=cleanSeries(state.charts[period]),known=series.values.some(v=>v!==null);
  $('#chart-total').textContent=known?num(series.values.reduce((sum,value)=>sum+(value??0),0),3):'—';
  $('#chart-total-period').textContent={hourly:'in the last 24 hours',daily:'in the last 7 days',history:'in the last 30 days'}[period];
  $('#chart-note').textContent=`${state.units.some(u=>u.source==='simulation')?'Synthetic test observations':'Recorded meter observations'} · Asia/Manila · gaps are unavailable`;
  $('#overview-chart-summary').textContent=chartSummary(series);$('#overview-chart-empty').hidden=known;$('#overview-chart').parentElement.hidden=!known;
  setContent('#overview-chart-data',series.values.map((value,i)=>`<tr><td>${html(bucketDate(series.timestamps?.[i])||series.labels[i])}</td><td>${value===null?'Unavailable':num(value,3)}</td><td>${series.observed_units?.[i]??'Not recorded'}</td></tr>`).join(''));
  $$('[data-period]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.period===period)));
  if(!overviewChart && known)overviewChart=new Chart($('#overview-chart'),chartConfiguration(series,$('#overview-chart'),'Recorded consumption'));
  else if(overviewChart){overviewChart.data.labels=series.labels;overviewChart.data.datasets[0].data=series.values;overviewChart.options.plugins.tooltip=chartConfiguration(series,$('#overview-chart'),'Recorded consumption').options.plugins.tooltip;overviewChart.update();overviewChart.resize();}
}
function renderDetailChart() {
  const series=cleanSeries(state.charts[$('#detail-period').value]),known=series.values.some(v=>v!==null);
  $('#detail-chart-summary').textContent=chartSummary(series);$('#detail-chart-empty').hidden=known;$('#detail-chart').parentElement.hidden=!known;
  if(!detailChart && known)detailChart=new Chart($('#detail-chart'),chartConfiguration(series,$('#detail-chart'),'Recorded consumption'));
  else if(detailChart){detailChart.data.labels=series.labels;detailChart.data.datasets[0].data=series.values;detailChart.options.plugins.tooltip=chartConfiguration(series,$('#detail-chart'),'Recorded consumption').options.plugins.tooltip;detailChart.update();detailChart.resize();}
}

function renderForecast(data) {
  const points=data.status==='available' && !data.model_age?.expired?(data.points||[]).filter(p=>numeric(p.kwh)!==null && numeric(p.kwh)>=0 && Number.isFinite(new Date(p.timestamp).getTime()) && new Date(p.timestamp)>=new Date()):[];
  const report=data.model,busy=data.job && ['pending','running'].includes(data.job.status);
  const status=data.model_age?.expired?'Model expired — retraining required':data.message;
  $('#forecast-summary-status').textContent=points.length?`${num(points[0].kwh,3)} kWh · next full hour`:status;
  $('#forecast-summary-copy').textContent=data.synthetic?'Synthetic demonstration · not real-meter accuracy.':data.detail;
  $('#forecast-status').textContent=status;$('#forecast-description').textContent=data.detail;
  $('#forecast-source').textContent=data.source==='simulation'?'SYNTHETIC DEMONSTRATION · Trained on simulated readings. These results do not establish real-meter accuracy.':data.source==='hardware'?'Hardware readings · Forecasts are estimates. Missing data and occupancy changes can affect reliability.':'';
  const button=$('#train-forecast');if(button){button.disabled=busy || state.units.length!==1;button.innerHTML=icon('bolt')+(busy?' Training in progress':' Train XGBoost');}
  $('#forecast-job').hidden=!data.job;$('#forecast-job').dataset.status=data.job?.status||'';$('#forecast-job').textContent=data.job?`${titleCase(data.job.status)} · ${data.job.message}`:'';
  $('#forecast-next').textContent=points.length?`${num(points[0].kwh,3)} kWh`:'Not available';$('#forecast-next-time').textContent=points.length?date(points[0].timestamp):'';
  $('#forecast-duration').textContent=!points.length || numeric(data.remaining_duration)===null?'Cannot yet be determined':numeric(data.remaining_duration)===0?'Credit exhausted':`${num(data.remaining_duration/3600,1)} hours · estimate`;
  $('#forecast-horizon').textContent=points.length?`Within ${points.length} upcoming hours · estimated`:'Awaiting a supported forecast horizon';
  $('#forecast-errors').textContent=report?`${num(report.test.mae,4)} / ${num(report.test.rmse,4)} kWh`:'Not evaluated';
  $('#forecast-mape').textContent=report?`MAPE ${numeric(report.test.mape)===null?'not defined':num(report.test.mape,1)+'%'} · ${report.test.mape_zero_excluded} zero hours excluded`:'';
  $('#forecast-generated-summary').textContent=points.length?date(data.generated_at):'No current forecast';$('#forecast-horizon-label').textContent=points.length?`${points.length} future full hours`:'Not available';
  $('#forecast-trained').textContent=report?date(report.trained_at):'Not trained';$('#forecast-age').textContent=data.model_age?.expired?'Expired — retraining required':data.model_age?.retraining_due?'Retraining due':report?'Within configured age limit':'Awaiting model';
  $('#forecast-model-note').textContent=report?`${report.coverage.complete_hours} covered hours · ${report.test.samples} next-hour test samples. Retraining age limits are operational policy, not proof of accuracy.`:'Hardware training requires at least 720 covered hourly observations. Missing periods are not manufactured.';
  $('#forecast-future-card').hidden=!points.length;$('#forecast-test-card').hidden=!report;$('#forecast-evaluation-card').hidden=!report;
  if(view!=='forecast')return;
  if(points.length){
    const series={labels:points.map(p=>date(p.timestamp)),values:points.map(p=>numeric(p.kwh))};
    $('#forecast-generated').textContent=`${data.synthetic?'Synthetic demo':'Hardware-data estimate'} · Generated ${date(data.generated_at)} · later hours use previous predictions`;
    if(!forecastChart)forecastChart=new Chart($('#forecast-chart'),chartConfiguration(series,$('#forecast-chart'),'Predicted kWh'));
    else{forecastChart.data.labels=series.labels;forecastChart.data.datasets[0].data=series.values;forecastChart.update();}forecastChart.resize();
  }
  if(!report)return;
  const comparison=report.comparison||[],series={labels:comparison.map(p=>date(p.timestamp)),values:comparison.map(p=>numeric(p.actual))};
  $('#forecast-test-window').textContent=`${date(report.splits.test.start)} – ${date(report.splits.test.end)} · ${report.test.samples} held-out hours (showing ${comparison.length}) · ${data.synthetic?'synthetic observations':'hardware observations'}`;
  const persistence=report.baselines.persistence,seasonal=report.baselines.seasonal_24h,backtest=report.recursive_backtest;
  const nextRows=[['XGBoost',report.test],['Persistence · previous hour',persistence],['Seasonal · same hour yesterday',seasonal]].filter(([,m])=>m);
  setContent('#forecast-comparison-table',nextRows.map(([label,m])=>`<tr><th scope="row">${html(label)}</th><td>${num(m.mae,4)}</td><td>${num(m.rmse,4)}</td><td>${numeric(m.mape)===null?'Not defined':num(m.mape,1)+'%'}</td><td>${m.samples}</td></tr>`).join(''));
  const worse=persistence.samples===report.test.samples && numeric(report.test.mae)>=numeric(persistence.mae);
  $('#forecast-caution').textContent=worse?'XGBoost did not outperform the persistence comparison model in this evaluation. Interpret forecasts cautiously, especially after occupancy changes.':'Historical results do not guarantee future accuracy. Check the baselines and recent occupancy before relying on an estimate.';
  $('#forecast-comparison-note').textContent=`MAPE excludes ${report.test.mape_zero_excluded} zero-use targets and can be unstable near zero. ${seasonal && seasonal.samples!==report.test.samples?'Seasonal results use a different matched sample count; direct ranking is limited.':''}`;
  const horizons=backtest?.status==='evaluated'?Object.entries(backtest.horizons).filter(([,r])=>r):[];
  setContent('#forecast-recursive-table',horizons.map(([h,r])=>`<tr><th scope="row">${h} h</th><td>${num(r.endpoint.mae,4)} / ${num(r.endpoint.rmse,4)}</td><td>${num(r.cumulative.mae,4)} / ${num(r.cumulative.rmse,4)}</td><td>${num(r.persistence.cumulative.mae,4)}</td><td>${r.seasonal_24h?num(r.seasonal_24h.cumulative.mae,4)+' ('+r.seasonal_24h.cumulative.samples+')':'Unavailable'}</td><td>${r.cumulative.samples}</td></tr>`).join('') || empty('Recursive evaluation needs model retraining and contiguous held-out observations.',6));
  const last=backtest?.horizons?.['24'];
  $('#forecast-baselines').textContent=last?`24h: endpoint MAE ${num(last.endpoint.mae,4)}, cumulative MAE ${num(last.cumulative.mae,4)} kWh (${last.cumulative.samples} origins). No calibrated confidence intervals.`:'Historical next-hour evaluation only; recursive metrics are unavailable for this artifact. No calibrated confidence intervals.';
  if(!forecastTestChart){const config=chartConfiguration(series,$('#forecast-test-chart'),'Observed kWh');config.data.datasets[0].fill=false;config.data.datasets.push({label:'Held-out XGBoost prediction',data:comparison.map(p=>numeric(p.predicted)),borderColor:'#986712',borderDash:[5,4],pointRadius:0,tension:.2,borderWidth:2});config.options.plugins.legend={display:true,labels:{boxWidth:16,font:{size:12},color:'#526575'}};config.options.plugins.tooltip.callbacks.label=c=>`${c.dataset.label}: ${num(c.parsed.y,4)} kWh`;forecastTestChart=new Chart($('#forecast-test-chart'),config);}
  else{forecastTestChart.data.labels=series.labels;forecastTestChart.data.datasets[0].data=series.values;forecastTestChart.data.datasets[1].data=comparison.map(p=>numeric(p.predicted));forecastTestChart.update();}forecastTestChart.resize();
}

function activityRow(t) {
  return `<div class="activity-row"><span class="activity-icon ${Number(t.amount)>0?'green':''}">${icon(Number(t.amount)>0?'wallet':'bolt')}</span><div class="activity-copy"><strong>${html(t.unit)} · ${t.kind==='consumption'?'Energy consumption':t.kind==='demo_credit'?'Sample opening credit':'Prepaid top-up'}</strong><small>${html(date(t.timestamp))}</small></div><div class="activity-amount ${Number(t.amount)>0?'positive':'negative'}">${Number(t.amount)>0?'+':'−'}${exactPesos(t.amount)}<small>${html(t.kind==='demo_credit'?'Simulation':'Recorded')}</small></div></div>`;
}
function alertRow(n) {
  return `<div class="alert-row">${icon(n.kind.includes('balance')?'wallet':n.kind.includes('power')?'bolt':'info')}<div><strong>${html(n.message)}</strong><small>${html(date(n.timestamp))}</small></div></div>`;
}
function renderUnits() {
  const status=$('#unit-status').value;
  const archived=status!=='active', demo=status==='demo';
  const units=archived?archivedUnits:state.units;
  setContent('#units-table',units.map(u=>{
    let actions;
    if(archived) actions=`<a class="text-button" href="/api/transactions/export?unit_id=${u.id}&include_archived=true${demo?'&include_demo=true':''}">Export history</a>${demo?'':`<button class="btn btn-secondary btn-small" data-restore="${u.id}">Restore unit</button>`}`;
    else actions=`<button class="btn btn-secondary btn-small" data-edit-unit="${u.id}">Edit unit</button>${u.tenant_id?`<button class="btn btn-secondary btn-small" data-manage-tenant="${u.id}">Manage tenant</button>`:`<button class="btn btn-secondary btn-small" data-assign="${u.id}">Assign tenant</button>`}<button class="btn btn-danger btn-small" data-remove="${u.id}">Remove unit</button>`;
    if(!archived && u.invitation_status && u.invitation_status!=='Activated') actions+=`<button class="btn btn-secondary btn-small" data-resend-invitation="${u.id}">Resend invitation</button>`;
    return `<tr><td><strong>${html(u.name)}</strong><small>${html(u.room)}</small></td><td>${html(u.tenant)}<small>${html(u.tenant_email||'')}</small>${u.tenant_id?`<small class="invitation-state">${html(u.invitation_status)}${u.email_delivery?' · '+html(emailLabel(u.email_delivery)):''}</small>${u.email_delivery_error?`<small class="email-warning">${html(u.email_delivery_error)}</small>`:''}`:''}</td><td><strong>${pesos(u.balance)}</strong></td><td>${html(u.device)}<small>${html(u.source)}</small></td><td>${badge(archived?'archived':u.power_status)}</td><td><div class="unit-actions">${actions}</div></td></tr>`;
  }).join('')||empty(demo?'No previous demonstration records.':archived?'No archived units.':'No active units. Register a unit to get started.',6));
}
function reportParams(name, includePage=true) {
  const params=new URLSearchParams(new FormData($('#'+name+'-filters')));
  for(const [key,value] of [...params])if(!value)params.delete(key);
  const unit=$('#unit-filter')?.value;if(unit)params.set('unit_id',unit);
  if(includePage){params.set('page',reports[name].page);params.set('per_page','25');if(reports[name].anchor!==null)params.set('anchor',reports[name].anchor);}
  return params;
}
function updateExport() {
  const params=reportParams('transactions',false),link=$('#export-link');
  const invalid=params.get('date_from') && params.get('date_to') && params.get('date_from')>params.get('date_to');
  link.href='/api/transactions/export'+(params.size?'?'+params:'');link.setAttribute('aria-disabled',String(Boolean(invalid)));
}
function renderReport(name, data) {
  const rows=name==='transactions'?data.transactions:data.requests;
  const markup=name==='transactions'?rows.map(t=>`<tr><td>${html(date(t.timestamp))}</td><td>${html(t.unit)}</td><td class="wrap-cell"><strong>${html(t.description)}</strong>${t.reference?`<small>Reference ${html(t.reference)}</small>`:''}</td><td class="${numeric(t.amount)>0?'positive':'negative'}">${numeric(t.amount)>0?'+':'−'}${exactPesos(t.amount)}<small>${t.kind==='consumption'?'Energy deduction':t.kind==='demo_credit'?'Synthetic credit':'Credit addition'}</small></td><td>${pesos(t.balance_after)}</td></tr>`).join(''):rows.map(r=>`<tr><td><strong>${html(r.reference)}</strong><small>${html(date(r.timestamp))}</small></td><td>${html(r.unit)}<small>${html(r.tenant)}</small></td><td><strong>${pesos(r.amount)} requested</strong><small>${r.credited_amount!==null?pesos(r.credited_amount)+' credited':'No credit recorded'}</small></td><td>${badge(r.status)}<small>${r.reviewed_at?html(date(r.reviewed_at)):'Awaiting review'}</small></td><td class="wrap-cell">${state.user.role==='admin' && r.status==='pending'?`<div class="review-buttons"><button class="btn btn-primary btn-small" data-review="${r.id}" data-decision="approved">Approve</button><button class="btn btn-danger btn-small" data-review="${r.id}" data-decision="rejected">Reject</button></div>`:''}<small>${html(r.note||'Payment is verified manually.')}</small></td></tr>`).join('');
  setContent('#'+name+'-table',markup || empty('No recorded results match these filters.',5));
  const p=data.pagination,start=rows.length?(p.page-1)*p.per_page+1:0,end=start?start+rows.length-1:0;
  $('#'+name+'-status').textContent=`${start}–${end} of ${p.total} matching records · Philippine time`;$('#'+name+'-status').dataset.error='false';
  $('#'+name+'-page-label').textContent=`Page ${p.page} of ${p.pages}`;
  $('#'+name+'-previous').disabled=p.page<=1;$('#'+name+'-next').disabled=p.page>=p.pages;
}
async function loadReport(name) {
  if(!state)return;
  const store=reports[name];if(store.busy){store.reload=true;return;}
  store.busy=true;store.reload=false;
  const key=reportParams(name).toString(),status=$('#'+name+'-status');
  status.textContent=store.data?'Updating results… Previous results remain until the request completes.':'Loading matching records…';
  status.dataset.error='false';$('#'+name+'-table').setAttribute('aria-busy','true');
  try {
    const data=await api('/api/'+(name==='loads'?'load-requests':'transactions')+'?'+key);
    if(!Array.isArray(data[name==='loads'?'requests':'transactions']) || !Number.isInteger(data.pagination?.page) || !Number.isInteger(data.pagination?.total))throw new Error('History response was incomplete. Retry the workspace update.');
    if(key!==reportParams(name).toString()){store.reload=true;return;}
    if(data.pagination.page>data.pagination.pages){store.page=data.pagination.pages;store.reload=true;return;}
    store.data=data;store.anchor=data.pagination.anchor;renderReport(name,data);
  } catch(error) {
    status.dataset.error='true';status.textContent=error.message+(store.data?' Previous results are retained.':'');
    if(!store.data)setContent('#'+name+'-table',empty('History could not be loaded. Correct the filters or retry the workspace update.',5));
  } finally {
    store.busy=false;$('#'+name+'-table').setAttribute('aria-busy','false');
    if(store.reload)loadReport(name);
  }
}
function initializeReports() {
  for(const name of ['loads','transactions']) {
    const form=$('#'+name+'-filters');let timer;
    function changed(){reports[name].page=1;reports[name].anchor=null;updateExport();loadReport(name);}
    form.addEventListener('submit',event=>{event.preventDefault();clearTimeout(timer);changed();});
    form.addEventListener('input',event=>{clearTimeout(timer);if(event.target.type==='search')timer=setTimeout(changed,350);});
    form.addEventListener('change',event=>{if(event.target.type!=='search')changed();});
    form.addEventListener('reset',()=>{clearTimeout(timer);setTimeout(changed,0);});
    $('#'+name+'-previous').addEventListener('click',()=>{reports[name].page=Math.max(1,reports[name].page-1);loadReport(name);});
    $('#'+name+'-next').addEventListener('click',()=>{reports[name].page++;loadReport(name);});
  }
  $('#export-link').addEventListener('click',event=>{if(event.currentTarget.getAttribute('aria-disabled')==='true'){event.preventDefault();toast('Correct the date range before exporting.',true);}});
}
function scheduleRefresh(delay=10000) {clearTimeout(refreshTimer);if(!document.hidden)refreshTimer=setTimeout(refresh,delay);}
async function refresh() {
  clearTimeout(refreshTimer);
  if(snapshotBusy){refreshQueued=true;return;}
  snapshotBusy=true;refreshQueued=false;
  const filter=$('#unit-filter')?.value||'';
  $('.workspace').setAttribute('aria-busy','true');
  try {
    const result=await api('/api/snapshot'+(filter?'?unit_id='+encodeURIComponent(filter):''));
    if(filter!==($('#unit-filter')?.value||'')){refreshQueued=true;return;}
    if(!result.user || !Array.isArray(result.units) || !result.summary || !result.insights || !result.operations || !result.forecast || !Array.isArray(result.actions)
      || !Array.isArray(result.transactions) || !Array.isArray(result.requests) || !Array.isArray(result.notifications) || !Array.isArray(result.events)
      || !['hourly','daily','history'].every(name=>Array.isArray(result.charts?.[name]?.values) && Array.isArray(result.charts?.[name]?.labels)))throw new Error('Workspace response was incomplete. Previous recorded data are retained.');
    state=result;connectionPaused=false;lastSnapshotFilter=filter;
    if(state.user.role==='admin' && $('#unit-status').value!=='active')archivedUnits=(await api('/api/units?status='+$('#unit-status').value)).units;
    $('#connection-alert').hidden=true;render();
    $('#workspace-status').textContent=`Workspace loaded. ${state.units.length} selected units. Last successful update ${date(state.updated_at)}.`;
    if(view==='transactions' || view==='loads'){
      const focused=$('#'+view+'-filters').contains(document.activeElement) || $('#'+view+'-table').contains(document.activeElement);
      if(!focused && !$('dialog[open]')){if(reports[view].page===1)reports[view].anchor=null;loadReport(view);}
    }
    if(view==='notifications' && state.user.role==='admin')loadEmailStatus();
    refreshFailures=0;
  } catch(error) {
    connectionPaused=true;refreshFailures++;
    $('#connection-alert').hidden=false;
    $('#connection-message').textContent=`${error.status===503?'Backend temporarily unavailable.':'Workspace updates could not be confirmed.'} ${state?'Last successful update '+date(state.updated_at)+'. Recorded values are retained; live status is unconfirmed.':'No workspace data have been loaded.'}`;
    if(state)render();else setContent('#unit-cards',empty('Workspace data are unavailable. Use Retry now; no readings are assumed to be zero.'));
    $('#live-label').textContent='Backend updates paused';
  } finally {
    snapshotBusy=false;$('.workspace').setAttribute('aria-busy','false');
    if(refreshQueued){refreshQueued=false;queueMicrotask(refresh);}else scheduleRefresh(Math.min(60000,10000*Math.max(1,refreshFailures)));
  }
}
function inspectUnit(id) {
  const unit=state.units.find(u=>u.id===Number(id));if(!unit)return;
  inspectedUnit=unit.id;$('#unit-detail-title').textContent=unit.name+' · '+unit.room;
  $('#unit-detail-content').innerHTML=`${measurementBadge(unit)}<p class="unit-tenant">${html(unit.tenant)}</p><dl class="forecast-facts"><div><dt>Available credit</dt><dd>${pesos(unit.balance)}</dd></div><div><dt>Warning threshold</dt><dd>${pesos(unit.threshold)}</dd></div><div><dt>Recorded today</dt><dd>${num(unit.today_kwh,3)} kWh</dd></div><div><dt>Current power</dt><dd>${num(livePower(unit),0)} W</dd></div><div><dt>Last measurement</dt><dd>${html(date(unit.reading_at))}</dd></div><div><dt>Last backend receipt</dt><dd>${html(date(unit.last_received||unit.last_seen))}</dd></div></dl><p class="field-help">${unit.source==='simulation'?'Synthetic test data.':'Recorded meter data.'} Unavailable readings are not assumed to be zero; backend receipts do not establish physical gateway health.</p>`;
  $('#unit-detail-dialog').showModal();
}
const mobileNavigation=window.matchMedia('(max-width:700px)');
function closeNavigation(restore=false) {
  $('#sidebar').classList.remove('is-open');$('#sidebar-scrim').hidden=true;$('#mobile-menu').setAttribute('aria-expanded','false');
  $('#sidebar').removeAttribute('role');$('#sidebar').removeAttribute('aria-modal');$('#sidebar').inert=mobileNavigation.matches;
  $('.workspace').inert=false;document.body.classList.remove('nav-open');if(restore)$('#mobile-menu').focus();
}
function openNavigation() {
  if(!mobileNavigation.matches)return;
  $('#sidebar').inert=false;$('#sidebar').classList.add('is-open');$('#sidebar-scrim').hidden=false;$('#mobile-menu').setAttribute('aria-expanded','true');
  $('#sidebar').setAttribute('role','dialog');$('#sidebar').setAttribute('aria-modal','true');$('.workspace').inert=true;document.body.classList.add('nav-open');
  $('#sidebar .nav-link.active').focus();
}
function initializeAccessibility() {
  closeNavigation();mobileNavigation.addEventListener('change',()=>closeNavigation());
  document.addEventListener('keydown',event=>{
    if(!$('#sidebar').classList.contains('is-open'))return;
    if(event.key==='Escape'){event.preventDefault();closeNavigation(true);}
    if(event.key==='Tab'){
      const targets=$$('#sidebar a[href], #sidebar button:not(:disabled)'),first=targets[0],last=targets.at(-1);
      if(event.shiftKey && document.activeElement===first){event.preventDefault();last.focus();}
      else if(!event.shiftKey && document.activeElement===last){event.preventDefault();first.focus();}
    }
  });
  document.addEventListener('focusout',()=>setTimeout(flushContent,0));
  $$('dialog').forEach(dialog=>dialog.addEventListener('close',()=>setTimeout(flushContent,0)));
  $$('form').forEach((form,index)=>{
    const error=form.querySelector('.form-error');if(error){error.id=error.id||'form-error-'+index;error.tabIndex=-1;form.setAttribute('aria-describedby',error.id);}
    form.addEventListener('invalid',event=>{event.target.setAttribute('aria-invalid','true');},true);
    form.addEventListener('input',event=>event.target.removeAttribute('aria-invalid'));
  });
}
function flushContent() {
  if($('dialog[open]'))return;
  for(const [node,markup] of deferredContent)if(!node.contains(document.activeElement)){if(node.isConnected)node.innerHTML=markup;deferredContent.delete(node);}
}

async function loadRates() {
  if(!$('#rate-history'))return;
  try {
    const data=await api('/api/rates');
    if($('#utility-reference-rate')){
      const policy=data.policy;
      $('#utility-reference-rate').value=policy.reference_rate?num(policy.reference_rate,4):'Unavailable';
      $('#utility-provider').textContent=[policy.provider,policy.service_area].filter(Boolean).join(' · ');
      $('#utility-rate-status').textContent=policy.message;
      $('#utility-billing-rate').textContent=data.active_rate?`Active billing rate: ₱${num(data.active_rate,4)} / kWh · Period ${data.active_period} · Valid until ${date(data.active_valid_until)}. Source: ${data.active_source}`:'No confirmed billing rate is active. The reference value does not create electricity charges.';
      $('#utility-source').textContent=policy.confirmed?`Configured source: ${policy.source} · Period: ${policy.period}${policy.effective_at?' · Applies from '+date(policy.effective_at):''} · Valid until ${date(policy.valid_until)}.`:'Awaiting confirmation from the applicable utility bill. No monthly rate is fetched automatically.';
    }
    $('#rate-history').innerHTML=data.rates.map(r=>`<div class="activity-row"><span class="activity-icon green">${icon('bolt')}</span><div class="activity-copy"><strong>₱${num(r.rate,4)} / kWh</strong><small>Effective ${html(date(r.effective_at))}${new Date(r.effective_at)>new Date()?' · Scheduled':''}${r.valid_until?' · Valid until '+html(date(r.valid_until)):''}</small><small>${r.owner_confirmed?html(r.provider)+' · '+html(r.period)+' · Owner-confirmed utility source':'Historical manually configured rate'}${r.source?' · '+html(r.source):''}</small></div></div>`).join('')||empty('No billing rates recorded.');
  }catch(error){toast(error.message,true);}
}
function emailLabel(status){return {pending:'Pending delivery',sending:'Sending',sent:'Provider accepted',failed:'Not sent / failed',cancelled:'Cancelled',test:'Test only · not sent'}[status]||status;}
async function loadEmailStatus(){
  try{const data=await api('/api/email-status');$('#email-configuration-status').textContent=data.configuration_message;
    $('#email-delivery-table').innerHTML=data.messages.map(item=>`<tr><td><strong>${html(titleCase(item.kind))}</strong><small>${html(date(item.created_at))}</small></td><td>${html(emailLabel(item.status))}</td><td>${item.attempts}<small>${html(item.error||'')}${item.status==='pending' && item.attempts?' · Retry '+html(date(item.next_attempt_at)):''}</small></td></tr>`).join('')||empty('No emails have been queued.',3);
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
  try { await confirmCallback();$('#confirm-dialog').close();$('#page-title').focus({preventScroll:true});await refresh(); }
  catch(error){const el=$('#confirm-dialog .form-error');el.textContent=error.message;el.hidden=false;}
  finally{button.disabled=false;}
});
document.addEventListener('click',event=>{
  const button=event.target.closest('button');if(!button)return;
  if(button.dataset.view){
    if(button.dataset.actionKey==='pending_requests'){$('#loads-kind').value='pending';reports.loads.page=1;reports.loads.anchor=null;}
    if(button.dataset.actionKey==='stale_units')$('#device-filter').value='';
    setView(button.dataset.view);
  }
  if(button.dataset.open){const dialog=$('#'+button.dataset.open);dialog.querySelectorAll('.form-error').forEach(el=>el.hidden=true);dialog.showModal();}
  if(button.hasAttribute('data-close')){const dialog=button.closest('dialog');if(dialog?.id==='device-secret-dialog')clearDeviceSecret();dialog?.close();}
  if(button.dataset.period){period=button.dataset.period;$$('[data-period]').forEach(b=>b.classList.toggle('active',b===button));if(state)renderOverviewChart();}
  if(button.dataset.unit)inspectUnit(button.dataset.unit);
  if(button.dataset.amount)$('#load-amount').value=button.dataset.amount;
  if(button.dataset.review){const item=(reports.loads.data?.requests||state.requests).find(r=>r.id===Number(button.dataset.review));if(!item){toast('Reload the request history before reviewing.',true);return;}const decision=button.dataset.decision;
    confirm(`${decision==='approved'?'Approve':'Reject'} this load request?`,`${item.unit} · ${pesos(item.amount)}. ${decision==='approved'?'This adds prepaid credit once and records a transaction.':'No credit will be added. The tenant will be notified.'}`,async()=>{const result=await api(`/api/load-requests/${item.id}/review`,{decision});toast(result.message);});}
  if(button.dataset.assign){const unit=state.units.find(u=>u.id===Number(button.dataset.assign));$('#tenant-form input[name="unit_id"]').value=unit.id;$('#assignment-label').textContent=`Create a tenant account for ${unit.name}.`;$('#tenant-dialog .form-error').hidden=true;$('#tenant-dialog').showModal();}
  if(button.dataset.editUnit){const unit=state.units.find(u=>u.id===Number(button.dataset.editUnit));const form=$('#edit-unit-form');form.elements.unit_id.value=unit.id;form.elements.name.value=unit.name;form.elements.room.value=unit.room;form.elements.threshold.value=unit.threshold;form.querySelector('.form-error').hidden=true;$('#edit-unit-dialog').showModal();}
  if(button.dataset.manageTenant){const unit=state.units.find(u=>u.id===Number(button.dataset.manageTenant));$('#reset-tenant-form').elements.unit_id.value=unit.id;$('#tenant-details').textContent=`${unit.name} · ${unit.tenant} · ${unit.tenant_email}`;$('#manage-tenant-dialog .form-error').hidden=true;$('#manage-tenant-dialog').showModal();}
  if(button.dataset.resendInvitation){const unit=state.units.find(u=>u.id===Number(button.dataset.resendInvitation));confirm('Resend this invitation?',`Replace the previous link for ${unit.tenant}. The old link stops working. A new email will be queued; provider acceptance is tracked separately.`,async()=>{const result=await api(`/api/units/${unit.id}/tenant/invitation`,{});toast(result.message,result.email_delivery==='failed');});}
  if(button.dataset.deviceToken){const unit=state.units.find(u=>u.id===Number(button.dataset.deviceToken));$('#device-token-form').elements.unit_id.value=unit.id;$('#device-token-label').textContent=`${unit.name} · ${unit.device}`;$('#device-token-dialog .form-error').hidden=true;$('#device-token-dialog').showModal();}
  if(button.dataset.remove){const unit=state.units.find(u=>u.id===Number(button.dataset.remove));
    confirm(`Remove ${unit.name}?`,'This archives the unit and retains its balance and history. Tenant access and meter updates will stop. Pending load requests will be rejected. You can restore the unit from Archived units.',async()=>{const result=await api(`/api/units/${unit.id}/archive`,{});$('#unit-filter').value='';toast(result.message);});}
  if(button.dataset.restore){const unit=archivedUnits.find(u=>u.id===Number(button.dataset.restore));
    confirm(`Restore ${unit.name}?`,'This restores the existing unit and balance. Its assigned tenant can sign in again, and meter updates can resume. Previously rejected requests stay rejected.',async()=>{const result=await api(`/api/units/${unit.id}/restore`,{});$('#unit-filter').value='';toast(result.message);});}
});
function describeFormError(form,error,show) {
  if(!form)return;
  for(const input of form.querySelectorAll('input:not([type="hidden"]),select,textarea')) {
    const ids=(input.getAttribute('aria-describedby')||'').split(' ').filter(id=>id && id!==error.id);
    if(show)ids.push(error.id);
    if(ids.length)input.setAttribute('aria-describedby',ids.join(' '));else input.removeAttribute('aria-describedby');
  }
}
function bindForm(selector, endpoint, payloadBuilder, onSuccess) {
  const form=$(selector);if(!form)return;
  form.addEventListener('submit',async event=>{
    event.preventDefault();const button=form.querySelector('button[type="submit"], .dialog-actions .btn-primary');const errorBox=form.querySelector('.form-error');
    button.disabled=true;errorBox.hidden=true;errorBox.textContent='';describeFormError(form,errorBox,false);
    try{const values=Object.fromEntries(new FormData(form));const result=await api(typeof endpoint==='function'?endpoint(values):endpoint,payloadBuilder(values));form.closest('dialog').close();form.reset();$('#page-title').focus({preventScroll:true});toast(result.message,result.email_delivery==='failed');onSuccess?.(result);await refresh();}
    catch(error){errorBox.textContent=error.message;errorBox.hidden=false;describeFormError(form,errorBox,true);errorBox.focus();}
    finally{button.disabled=false;}
  });
}
bindForm('#load-form','/api/load-requests',v=>({amount:v.amount,note:v.note,reference:requestReference}),()=>{requestReference=crypto.randomUUID();setView('loads');});
bindForm('#unit-form','/api/units',v=>v,()=>{if($('#unit-filter'))$('#unit-filter').value='';setView('units');});
bindForm('#tenant-form',v=>`/api/units/${v.unit_id}/tenant`,v=>({name:v.name,email:v.email,registration_method:v.registration_method,...(v.registration_method==='temporary_password'?{password:v.password}:{})}),()=>onboardingMethod());
function onboardingMethod(){const manual=$('#tenant-registration-method')?.value==='temporary_password';if(!$('#tenant-password'))return;$('#tenant-password-fields').hidden=!manual;$('#tenant-password').disabled=!manual;$('#tenant-password').required=manual;$('#tenant-invitation-help').hidden=manual;}
$('#tenant-registration-method')?.addEventListener('change',onboardingMethod);
bindForm('#edit-unit-form',v=>`/api/units/${v.unit_id}/edit`,v=>({name:v.name,room:v.room,threshold:v.threshold}));
bindForm('#reset-tenant-form',v=>`/api/units/${v.unit_id}/tenant/password`,v=>({password:v.password}));
bindForm('#device-token-form',v=>`/api/units/${v.unit_id}/device-token`,v=>({action:v.action,current_password:v.current_password}),result=>{
  if(!result.token)return;
  $('#device-secret-value').value=result.token;
  $('#device-endpoint').value=window.location.origin+result.ingestion_path;
  $('#device-header-help').textContent=`Device ID: ${result.device_id} · Unit ID: ${result.unit_id}. Send X-Device-ID with this identifier and Authorization: Bearer with the token.`;
  $('#device-secret-dialog').showModal();
});
$('#end-tenancy')?.addEventListener('click',()=>{
  const id=$('#reset-tenant-form').elements.unit_id.value;
  $('#manage-tenant-dialog').close();
  confirm('End this tenancy?','The tenant will lose access and pending requests will be rejected. The unit balance and all records are retained. Settle the remaining credit according to your dormitory policy before assigning the next tenant.',async()=>{const result=await api(`/api/units/${id}/tenant/end`,{});toast(result.message);});
});
$('#copy-device-token')?.addEventListener('click',async()=>{try{await navigator.clipboard.writeText($('#device-secret-value').value);toast('Device token copied.');}catch{toast('Select the token and copy it manually.',true);}});
function clearDeviceSecret() {$('#device-secret-value').value='';$('#device-endpoint').value='';$('#device-header-help').textContent='';}
$$('dialog').forEach(dialog=>dialog.addEventListener('close',()=>{
  dialog.querySelectorAll('input[type="password"]').forEach(input=>input.value='');
  dialog.querySelectorAll('.form-error').forEach(error=>{error.hidden=true;error.textContent='';describeFormError(error.closest('form'),error,false);});
  if(dialog.id==='device-secret-dialog')clearDeviceSecret();
}));
$('#rate-form')?.addEventListener('submit',event=>{
  event.preventDefault();const values=Object.fromEntries(new FormData(event.target));
  const payload={rate:values.rate};if(values.effective_at)payload.effective_at=new Date(values.effective_at).toISOString();
  confirm('Save this electricity rate?',`${pesos(values.rate)} per kWh, effective ${values.effective_at?date(payload.effective_at):'now'}. Earlier charges remain unchanged.`,async()=>{const result=await api('/api/rates',payload);toast(result.message);await loadRates();});
});
$('#unit-filter')?.addEventListener('change',()=>{reports.loads.page=1;reports.transactions.page=1;reports.loads.anchor=null;reports.transactions.anchor=null;refresh();});
$('#unit-status')?.addEventListener('change',refresh);
$('#train-forecast')?.addEventListener('click',async()=>{
  if (state.units.length !== 1) return;
  const button=$('#train-forecast');button.disabled=true;
  try {const result=await api(`/api/units/${state.units[0].id}/forecast/train`,{});toast(result.message);await refresh();}
  catch(error){toast(error.message,true);button.disabled=false;}
});
$('#detail-period').addEventListener('change',()=>{if(state)renderDetailChart();});
$('#mobile-menu').addEventListener('click',()=>$('#sidebar').classList.contains('is-open')?closeNavigation(true):openNavigation());
$('#sidebar-scrim').addEventListener('click',()=>closeNavigation(true));
$('#today-label span').textContent=new Date().toLocaleDateString('en-PH',{timeZone:'Asia/Manila',month:'short',day:'numeric',year:'numeric'});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();else clearTimeout(refreshTimer);});
$('#retry-update').addEventListener('click',refresh);
$('#device-filter').addEventListener('change',()=>{if(state)renderDevices();});
$('#notification-category').addEventListener('change',()=>{if(state)renderNotifications();});
$('#unit-detail-action').addEventListener('click',()=>{
  $('#unit-detail-dialog').close();
  if(state.user.role==='admin'){$('#unit-filter').value=String(inspectedUnit);reports.loads.page=1;reports.transactions.page=1;reports.loads.anchor=null;reports.transactions.anchor=null;refresh();setView('overview');}
  else setView('consumption');
});
initializeReports();initializeAccessibility();
const initialView=location.hash.slice(1);if(initialView && $('#view-'+initialView))setView(initialView);
refresh();
