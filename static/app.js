const $ = (id) => document.getElementById(id);
const fmt = (value, digits = 1) => value === null || value === undefined || Number.isNaN(Number(value)) ? "—" : Number(value).toFixed(digits);
const state = { snapshot: null, history: {}, lastAlarmIds: new Set(), chartTimer: 0, supervisionZone: "synthesis" };
const GAME_TEXT_FR = {
  ACTIVE:"ACTIF",ACTIVO:"ACTIF",ACTIVA:"ACTIVE",INACTIVE:"INACTIF",INACTIVO:"INACTIF",INACTIVA:"INACTIVE",
  OPERATIVE:"OPÉRATIONNEL",OPERATIONAL:"OPÉRATIONNEL",OPERATIVO:"OPÉRATIONNEL",
  OFFLINE:"HORS LIGNE",APAGADO:"ARRÊTÉ",DETENIDO:"ARRÊTÉ",ENCENDIDO:"EN MARCHE",
  FUNCIONANDO:"EN MARCHE",STANDBY:"EN ATTENTE",ESPERA:"EN ATTENTE","EN ESPERA":"EN ATTENTE",LISTO:"PRÊT",
  AUTOMATICO:"AUTOMATIQUE","MODO AUTOMATICO":"AUTOMATIQUE",MANUAL:"MANUEL",
  PRESURIZADO:"PRESSURISÉ","NO PRESURIZADO":"NON PRESSURISÉ",DESPRESURIZADO:"DÉPRESSURISÉ",
  "NO INSTALADO":"NON INSTALLÉ","NO INSTALADA":"NON INSTALLÉE","SIN INSTALAR":"NON INSTALLÉ",
  "NO DISPONIBLE":"INDISPONIBLE","SIN COMBUSTIBLE":"SANS CARBURANT","COMBUSTIBLE BAJO":"CARBURANT FAIBLE",
  FALLO:"DÉFAUT",FALLA:"DÉFAUT",AVERIA:"DÉFAUT","REQUIERE MANTENIMIENTO":"MAINTENANCE REQUISE"
};
const gameText = value => {
  if (value === null || value === undefined || value === "") return "INCONNU";
  const key = String(value).normalize("NFD").replace(/[\u0300-\u036f]/g,"").trim().toUpperCase().replaceAll("_"," ").replaceAll("-"," ").replace(/\s+/g," ");
  return GAME_TEXT_FR[key] || String(value);
};

const AREA_LABELS = {
  reactor: "Régulation du cœur", grid: "Suivi de la demande réseau", secondary: "Circuits secondaires",
  condenser: "Condenseur et vide", retention: "Réservoir de rétention",
  pressurizer: "Pressuriseur", primary_makeup: "Appoint circuit primaire",
  chemistry: "Chimie et bore (si installé)", poisons: "Protection xénon et iode"
};

function showToast(message, error = false) {
  const toast = $("toast");
  toast.textContent = message; toast.className = error ? "show error" : "show";
  clearTimeout(showToast.timer); showToast.timer = setTimeout(() => toast.className = "", 3200);
}

async function api(path, options = {}) {
  const response = await fetch(path, {headers: {"Content-Type": "application/json"}, ...options});
  let data = {};
  try { data = await response.json(); } catch (_) {}
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function switchView(name) {
  document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
  document.querySelectorAll(".nav-button").forEach(v => v.classList.toggle("active", v.dataset.view === name));
  $(`view-${name}`).classList.add("active");
  if (name === "variables") loadVariables();
}

document.querySelectorAll(".nav-button").forEach(button => button.addEventListener("click", () => switchView(button.dataset.view)));
document.querySelectorAll("[data-open]").forEach(button => button.addEventListener("click", () => switchView(button.dataset.open)));

function switchSupervisionZone(zone) {
  state.supervisionZone = zone;
  document.querySelectorAll("[data-supervision-zone]").forEach(button => button.classList.toggle("active", button.dataset.supervisionZone === zone));
  document.querySelectorAll("#view-overview [data-supervision-zones]").forEach(panel => {
    panel.classList.toggle("zone-hidden", !panel.dataset.supervisionZones.split(" ").includes(zone));
  });
  document.querySelectorAll(".systems-panel .system-row").forEach(row => {
    const hide = (zone === "chemistry" && row.dataset.system !== "chemistry") || (zone === "fluids" && row.dataset.system === "chemistry");
    row.classList.toggle("zone-filter-hidden", hide);
  });
  $("reservoir-list").classList.toggle("zone-filter-hidden", zone === "chemistry");
  $("chemical-reservoir-section").classList.toggle("zone-filter-hidden", zone === "fluids");
  $("main-generator-list").classList.toggle("zone-filter-hidden", zone === "emergency");
  $("emergency-generator-section").classList.toggle("zone-filter-hidden", zone === "production");
  requestAnimationFrame(() => { drawChart(); drawPoisonChart(); });
}

document.querySelectorAll("[data-supervision-zone]").forEach(button => button.addEventListener("click", () => switchSupervisionZone(button.dataset.supervisionZone)));

function statusClass(value, warning, critical, inverse = false) {
  if (value === null || value === undefined) return "";
  if (inverse) return value <= critical ? "danger" : value <= warning ? "warn" : "ok";
  return value >= critical ? "danger" : value >= warning ? "warn" : "ok";
}

function setBar(id, value, warningMode = "none") {
  const bar = $(id); const n = Math.max(0, Math.min(100, Number(value) || 0)); bar.style.width = `${n}%`;
  let color = "var(--green)";
  if (warningMode === "high") color = n > 75 ? "var(--red)" : n > 60 ? "var(--amber)" : "var(--green)";
  if (warningMode === "low") color = n < 25 ? "var(--red)" : n < 45 ? "var(--amber)" : "var(--green)";
  bar.style.background = color;
}

function isNotInstalled(value) {
  if (value === null || value === undefined) return false;
  const text = String(value).normalize("NFD").replace(/[\u0300-\u036f]/g,"").trim().toUpperCase().replaceAll("_", " ").replaceAll("-", " ").replace(/\s+/g," ");
  return Number(value) === 4 || ["NOT INSTALLED","NO INSTALAD","SIN INSTALAR","NON INSTALLE","NOT PURCHASED","NO COMPRAD"].some(marker=>text.includes(marker));
}

function trainInstalled(s, i) {
  const flag = s[`STEAM_TURBINE_${i}_INSTALLED`];
  if (flag !== null && flag !== undefined && !Boolean(flag)) return false;
  if (isNotInstalled(s[`STEAM_GEN_${i}_STATUS`])) return false;
  return flag !== null && flag !== undefined ? Boolean(flag) : s[`GENERATOR_${i}_KW`] !== undefined || s[`STEAM_TURBINE_${i}_RPM`] !== undefined;
}

function trainCards(s, targetTotal) {
  const available = [0,1,2].filter(i => trainInstalled(s, i) && s[`GENERATOR_${i}_KW`] !== undefined);
  const target = available.length ? targetTotal / available.length : 0;
  $("train-cards").innerHTML = [0,1,2].map(i => {
    if (!trainInstalled(s, i)) {
      return `<div class="train-card"><header><span>GROUPE ${i+1}</span><span>NON INSTALLÉ</span></header>
        <strong>—</strong><div class="bar"><i style="width:0%"></i></div><small><span>COMMANDES DÉSACTIVÉES</span></small></div>`;
    }
    const kw = Number(s[`GENERATOR_${i}_KW`] || 0), pct = Math.max(0, Math.min(100, target ? kw / target * 100 : 0));
    const rpm = s[`STEAM_TURBINE_${i}_RPM`];
    return `<div class="train-card"><header><span>GROUPE ${i+1}</span><span>${rpm === undefined ? "VITESSE —" : `${fmt(rpm,0)} RPM`}</span></header>
      <strong>${fmt(kw/1000)} MW</strong><div class="bar"><i style="width:${pct}%;background:${pct > 110 ? 'var(--amber)' : 'var(--green)'}"></i></div>
      <small><span>MSCV ${fmt(s[`MSCV_${i}_OPENING_ACTUAL`])}%</span><span>SEC ${fmt(s[`COOLANT_SEC_CIRCULATION_PUMP_${i}_ORDERED_SPEED`])}%</span></small></div>`;
  }).join("");
}

function measurement(value, unit = "") {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  const rendered = unit === "L" ? number.toLocaleString("fr-FR", {maximumFractionDigits: 0}) : fmt(number);
  return `${rendered}${unit ? ` ${unit}` : ""}`;
}

function reservoirRows(items) {
  return items.map(item => {
    const percent = item.percent === null || item.percent === undefined ? null : Math.max(0, Math.min(100, Number(item.percent)));
    const scale = item.capacity ? ` · capacité ${measurement(item.capacity,"L")}` : "";
    const percentText = percent !== null && item.unit !== "%" ? `<small>${fmt(percent)} %</small>` : "";
    return `<div class="reservoir-item"><div><strong>${escapeHtml(item.label)}</strong><small>${escapeHtml(item.variable || item.id)}${escapeHtml(scale)}</small></div>
      <div class="reservoir-meter ${percent === null ? 'no-scale' : ''}"><i style="width:${percent ?? 0}%"></i></div>
      <div class="reservoir-value">${escapeHtml(measurement(item.value,item.unit))}${percentText}</div></div>`;
  }).join("");
}

function renderReservoirs(derived, chemistry) {
  const reservoirs = derived.reservoirs || [];
  $("reservoir-list").innerHTML = reservoirs.length ? reservoirRows(reservoirs) : '<div class="empty compact">Aucun niveau exposé</div>';
  const chemicalSection = $("chemical-reservoir-section");
  const chemical = derived.chemical_reservoirs || [];
  chemicalSection.classList.toggle("hidden", !chemistry.installed);
  if (chemistry.installed) {
    $("chemical-reservoir-note").textContent = chemical.length ? `${chemical.length} mesure(s)` : "NIVEAU NON EXPOSÉ";
    $("chemical-reservoir-list").innerHTML = chemical.length ? reservoirRows(chemical) : '<div class="empty compact">Le jeu ne publie pas encore le niveau du réservoir d’acide borique.</div>';
  }
}

function renderGenerators(generators) {
  const main = generators.main || [];
  $("main-generator-list").innerHTML = main.length ? main.map(generator => {
    const breaker = generator.breaker_open === null ? "DISJ. —" : generator.breaker_open ? "DISJ. OUVERT" : "DISJ. FERMÉ";
    return `<div class="generator-row"><div class="generator-number">G${generator.id + 1}</div><div class="generator-copy"><strong>Générateur principal ${generator.id + 1}</strong><small><span class="status-pill ${generator.status_class}">${escapeHtml(generator.status)}</span> · ${breaker}</small></div>
      <div class="generator-metrics"><span>PUISSANCE<b>${measurement(generator.power_kw === null ? null : generator.power_kw / 1000,"MW")}</b></span><span>VITESSE<b>${measurement(generator.rpm,"RPM")}</b></span><span>FRÉQUENCE<b>${measurement(generator.frequency,"Hz")}</b></span><span>TENSION<b>${measurement(generator.voltage,"V")}</b></span><span>COURANT<b>${measurement(generator.current,"A")}</b></span></div></div>`;
  }).join("") : '<div class="empty compact">Aucun générateur principal exposé</div>';

  const emergency = generators.emergency || [];
  $("emergency-generator-section").classList.toggle("hidden", !emergency.length);
  if (emergency.length) {
    $("emergency-generator-list").innerHTML = emergency.map(generator => `<div class="generator-row"><div class="generator-number">E${generator.id}</div><div class="generator-copy"><strong>Groupe de secours ${generator.id}</strong><small><span class="status-pill ${generator.installed ? 'ok' : ''}" title="${escapeHtml(generator.installation_source || 'DÉTECTION AUTO')}">${escapeHtml(generator.installation_status)}</span> · ${escapeHtml(generator.installation_source || 'DÉTECTION AUTO')}${generator.installed ? ` · <span class="status-pill ${generator.status_class}">${escapeHtml(generator.status)}</span> · mode ${escapeHtml(generator.mode ?? '—')}${generator.maintenance ? ' · MAINTENANCE REQUISE' : ''}` : ''}</small></div>
      <div class="generator-metrics"><span>CARBURANT<b>${measurement(generator.fuel,generator.fuel_unit || "L")}</b></span><span>PRESSURISEUR<b>${escapeHtml(generator.pressurizer ?? '—')}</b></span></div></div>`).join("");
  }
}

function renderElectrical(electrical = {}) {
  const transformers = electrical.transformers || [];
  $("transformer-list").innerHTML = transformers.length ? transformers.map(transformer => `<div class="transformer-item ${transformer.energized ? 'energized' : ''}"><div class="transformer-symbol">T</div><div><strong>${escapeHtml(transformer.label)}</strong><small>${escapeHtml(transformer.detail)}</small></div><div class="transformer-reading"><span class="status-pill ${transformer.status_class || ''}">${escapeHtml(transformer.status)}</span><b>${measurement(transformer.power_kw === null ? null : transformer.power_kw / 1000,"MW")}</b></div></div>`).join("") : '<div class="empty compact">Aucun flux électrique disponible</div>';
  const resistors = electrical.resistors || {}, banks = resistors.banks || [];
  $("resistor-status").textContent = resistors.status || "INDISPONIBLE";
  $("resistor-status").className = `status-pill ${resistors.status_class || ''}`;
  $("resistor-absorbed").textContent = measurement(resistors.absorbed_mw,"MW");
  $("resistor-capacity").textContent = measurement(resistors.capacity_mw,"MW");
  $("resistor-surplus").textContent = measurement(resistors.surplus_mw,"MW");
  $("resistor-load").textContent = `${fmt(resistors.load_pct)} %`;
  setBar("resistor-load-bar",resistors.load_pct,"high");
  const bankCards = [{id:"M",active:resistors.main_on,available:resistors.available,label:"GÉNÉRAL"},...banks.map(bank=>({...bank,label:`BANC ${bank.id}`}))];
  $("resistor-banks").innerHTML = bankCards.map(bank=>`<div class="resistor-bank ${bank.active ? 'active' : ''} ${!bank.available ? 'unavailable' : ''}"><span>${escapeHtml(bank.label)}</span><strong>${!bank.available ? 'INDISP.' : bank.active ? 'ACTIF' : 'ARRÊT'}</strong></div>`).join("");
}

function renderPoisons(poisons = {}) {
  const iodine = poisons.iodine || {}, xenon = poisons.xenon || {};
  const trend = item => item.trend_per_min === null || item.trend_per_min === undefined ? "—" : `${item.trend_per_min >= 0 ? "+" : ""}${fmt(item.trend_per_min,3)} /min`;
  $("iodine-generation").textContent = fmt(iodine.generation,3);
  $("iodine-cumulative").textContent = fmt(iodine.cumulative,3);
  $("iodine-trend").textContent = trend(iodine);
  $("xenon-generation").textContent = fmt(xenon.generation,3);
  $("xenon-cumulative").textContent = fmt(xenon.cumulative,3);
  $("xenon-trend").textContent = trend(xenon);
  $("poison-status").textContent = poisons.status || "INDISPONIBLE";
  $("poison-status").className = `status-pill ${poisons.status_class || ""}`;
  $("poison-message").textContent = poisons.message || "Variables indisponibles";
  $("poison-guard").textContent = !poisons.management_enabled ? "DÉSACTIVÉE" : poisons.guard_active ? "ACTIVE — RAMPES LIMITÉES" : "EN VEILLE";
  $("poison-guard").className = poisons.guard_active ? "warn-text" : poisons.management_enabled ? "ok-text" : "";
}

function renderAlarms(alarms) {
  const pending = alarms.filter(a => !a.acknowledged).length;
  const count = $("alarm-count"), nav = $("alarms-nav-button"); count.textContent = alarms.length;
  count.className = `count ${pending ? "needs-ack" : alarms.length ? "acknowledged" : ""}`;
  nav.classList.toggle("alarm-needs-ack", pending > 0); nav.classList.toggle("alarm-acknowledged", alarms.length > 0 && pending === 0);
  nav.title = pending ? `${pending} alarme(s) à acquitter` : alarms.length ? "Toutes les alarmes actives sont acquittées" : "Aucune alarme active";
  const globalButton = $("ack-all"); globalButton.disabled = pending === 0; globalButton.textContent = pending ? `Tout acquitter (${pending})` : "Tout est acquitté"; globalButton.className = pending ? "danger-button" : "secondary-button";
  const html = alarms.length ? alarms.map(a => `<div class="alarm-item ${a.severity} ${a.acknowledged ? 'acknowledged' : 'needs-ack'}"><i></i><div><strong>${escapeHtml(a.title)}</strong><span>${escapeHtml(a.detail)}</span></div><time>${new Date(a.since).toLocaleTimeString()}</time><button class="${a.acknowledged ? 'alarm-acknowledged-button' : 'alarm-needs-ack-button'}" data-ack="${escapeHtml(a.alarm_id)}" ${a.acknowledged ? 'disabled' : ''}>${a.acknowledged ? "ACQUITTÉE" : "À ACQUITTER"}</button></div>`).join("") : '<div class="empty">Aucune alarme active</div>';
  $("alarm-preview").innerHTML = alarms.length ? alarms.slice(0,4).map(a => `<div class="alarm-item ${a.severity}"><i></i><div><strong>${escapeHtml(a.title)}</strong><span>${escapeHtml(a.detail)}</span></div><time>${new Date(a.since).toLocaleTimeString()}</time></div>`).join("") : html;
  $("alarm-full").innerHTML = html;
  document.querySelectorAll("[data-ack]").forEach(b => b.addEventListener("click", () => acknowledge(b.dataset.ack)));
  const activeIds = new Set(alarms.filter(a => !a.acknowledged).map(a => a.alarm_id));
  if ([...activeIds].some(id => !state.lastAlarmIds.has(id))) beep();
  state.lastAlarmIds = activeIds;
}

function renderJournal(actions) {
  $("journal-rows").innerHTML = actions.length ? actions.map(a => `<tr><td>${new Date(a.timestamp).toLocaleTimeString()}</td><td>${escapeHtml(a.area)}</td><td class="${a.ok ? 'ok-text' : 'bad-text'}">${escapeHtml(a.command)}</td><td>${escapeHtml(String(a.value))}</td><td>${escapeHtml(a.reason)}</td></tr>`).join("") : '<tr><td colspan="5">Aucune commande enregistrée</td></tr>';
}

function render(snapshot) {
  state.snapshot = snapshot; const s = snapshot.state || {}, d = snapshot.derived || {}, auto = snapshot.autopilot;
  $("connection-dot").className = snapshot.connected ? "ok" : "bad";
  $("connection-label").textContent = snapshot.connected ? "JEU CONNECTÉ" : "HORS CONNEXION";
  $("connection-banner").classList.toggle("hidden", snapshot.connected);
  $("connection-error").textContent = snapshot.last_error || "Démarrez le webserveur depuis la tablette du jeu.";
  $("capabilities").textContent = `${snapshot.capabilities.readable} / ${snapshot.capabilities.writable}`;
  $("autopilot-toggle").checked = auto.enabled; $("cycle").textContent = auto.cycle;
  $("mode-title").textContent = auto.enabled ? "PILOTAGE INTÉGRAL" : "SUPERVISION";
  $("mode-subtitle").textContent = auto.enabled ? "Régulation et suivi réseau actifs" : "Pilote automatique à l’arrêt";
  $("temp-setpoint").textContent = fmt(auto.target_core_temp,0);
  const temp = Number(s.CORE_TEMP); $("core-temp").textContent = fmt(temp);
  const tempPct = Math.max(0, Math.min(100, (temp - 250) / 160 * 100));
  $("temp-ring").style.background = `conic-gradient(${temp >= 390 ? 'var(--red)' : temp >= 355 ? 'var(--amber)' : 'var(--green)'} 0 ${tempPct}%, #23312d ${tempPct}% 100%)`;
  $("temp-marker").style.left = `${tempPct}%`;
  const coreState = d.core_state || (s.CORE_STATE ? gameText(s.CORE_STATE) : (snapshot.connected ? "EN LIGNE" : "INCONNU")); $("core-state").textContent = coreState;
  $("core-state").className = `status-pill ${statusClass(temp,355,390)}`;
  $("criticality").textContent = fmt(s.CORE_STATE_CRITICALITY,3); $("core-pressure").textContent = fmt(s.CORE_PRESSURE);
  $("core-integrity").textContent = fmt(s.CORE_INTEGRITY); const rod = s.ROD_BANK_POS_0_ACTUAL ?? s.RODS_POS_ACTUAL;
  $("rod-position").textContent = `${fmt(rod)} %`; setBar("rod-rail", rod);
  $("generated").textContent = fmt((d.generated_kw || 0)/1000); $("demand").textContent = fmt((d.demand_kw || 0)/1000);
  const balance = Number(d.power_balance_kw || 0); $("balance-label").textContent = Math.abs(balance) < 3000 ? "ÉQUILIBRE" : balance < 0 ? "DÉFICIT" : "EXCÉDENT";
  $("grid-status").textContent = Math.abs(balance) < 3000 ? "STABLE" : balance < 0 ? "SOUS-PRODUCTION" : "SURPLUS";
  $("grid-status").className = `status-pill ${Math.abs(balance) < 3000 ? 'ok' : 'warn'}`;
  trainCards(s, Number(d.demand_kw || 0) + auto.grid_buffer_mw * 1000);
  const hydraulic = { condenser: d.condenser_fill_pct, vacuum: d.vacuum_pct, primary: s.COOLANT_CORE_PRIMARY_LOOP_LEVEL, pressurizer: d.pressurizer_pct, retention: d.retention_pct };
  const hydraulicText = { condenser: 'condenser-fill', vacuum: 'vacuum', primary: 'primary-level', pressurizer: 'pressurizer', retention: 'retention' };
  Object.entries(hydraulic).forEach(([key,value]) => { $(hydraulicText[key]).textContent = `${fmt(value)} %`; setBar(`${key}-bar`, value, key === 'retention' ? 'high' : 'low'); });
  const chemistry = snapshot.chemistry || {status:'unavailable',message:'Module chimique indisponible'};
  const chemistryLabels = {ready:'PRÊT',fault:'DÉFAUT',read_only:'LECTURE SEULE',not_installed:'NON INSTALLÉ',unavailable:'INDISPONIBLE'};
  $("chemistry-status").textContent = chemistryLabels[chemistry.status] || chemistry.status;
  $("chemistry-status").className = `status-pill ${chemistry.status === 'ready' ? 'ok' : chemistry.status === 'fault' ? 'danger' : chemistry.status === 'read_only' ? 'warn' : ''}`;
  $("chemistry-detail").textContent = chemistry.message || 'État inconnu';
  $("boron-ppm").textContent = `${fmt(chemistry.ppm)} ppm`;
  renderReservoirs(d, chemistry); renderGenerators(d.generators || {}); renderElectrical(d.electrical || {}); renderPoisons(d.poisons || {});
  renderAlarms(snapshot.alarms || []); renderJournal(snapshot.actions || []);
}

function drawChart() {
  const canvas = $("power-chart"), ctx = canvas.getContext("2d"), ratio = devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect(); canvas.width = Math.max(300, rect.width * ratio); canvas.height = 150 * ratio; ctx.scale(ratio, ratio);
  const w = rect.width, h = 150; ctx.clearRect(0,0,w,h); ctx.strokeStyle = "#20302b"; ctx.lineWidth = 1;
  for(let y=20;y<h;y+=32){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke();}
  const series = [0,1,2].map(i => state.history[`GENERATOR_${i}_KW`] || []); const all = series.flat(); if(!all.length) return;
  const minT = Math.min(...all.map(p=>p[0])), maxT = Math.max(...all.map(p=>p[0])); const maxV = Math.max(1,...all.map(p=>p[1])); const colors=["#52e29a","#58a6ff","#f2b94b"];
  series.forEach((points,index)=>{ctx.strokeStyle=colors[index];ctx.lineWidth=1.7;ctx.beginPath();points.forEach((p,i)=>{const x=(p[0]-minT)/Math.max(1,maxT-minT)*w, y=h-12-(p[1]/maxV)*(h-28);i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke();});
}

function drawPoisonChart() {
  const canvas = $("poison-chart"); if (!canvas) return;
  const rect = canvas.getBoundingClientRect(); if (!rect.width) return;
  const ctx = canvas.getContext("2d"), ratio = devicePixelRatio || 1;
  canvas.width = Math.max(300, rect.width * ratio); canvas.height = 130 * ratio; ctx.scale(ratio, ratio);
  const w = rect.width, h = 130; ctx.clearRect(0,0,w,h); ctx.strokeStyle = "#20302b"; ctx.lineWidth = 1;
  for(let y=18;y<h;y+=28){ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke();}
  const series = [state.history.CORE_IODINE_CUMULATIVE || [], state.history.CORE_XENON_CUMULATIVE || []];
  const all = series.flat(); if(!all.length) return;
  const minT=Math.min(...all.map(p=>p[0])), maxT=Math.max(...all.map(p=>p[0]));
  const minV=Math.min(...all.map(p=>p[1])), maxV=Math.max(...all.map(p=>p[1]));
  const span=Math.max(0.000001,maxV-minV), colors=["#f2b94b","#58a6ff"];
  series.forEach((points,index)=>{ctx.strokeStyle=colors[index];ctx.lineWidth=1.8;ctx.beginPath();points.forEach((p,i)=>{const x=(p[0]-minT)/Math.max(1,maxT-minT)*w,y=h-10-((p[1]-minV)/span)*(h-24);i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke();});
}

async function refreshHistory() { try { state.history = await api('/api/history?variables=GENERATOR_0_KW,GENERATOR_1_KW,GENERATOR_2_KW,CORE_IODINE_CUMULATIVE,CORE_XENON_CUMULATIVE&seconds=1800'); drawChart(); drawPoisonChart(); } catch (_) {} }
async function refresh() { try { render(await api('/api/state')); } catch (err) { $("connection-dot").className="bad"; $("connection-label").textContent="APPLICATION INDISPONIBLE"; } }

function confirmAction(title, copy, danger = true) {
  return new Promise(resolve => { const dialog=$("confirm-dialog"); $("dialog-title").textContent=title; $("dialog-copy").textContent=copy; $("dialog-confirm").className=danger?"danger-button":"primary-button"; dialog.showModal(); dialog.addEventListener("close",()=>resolve(dialog.returnValue==="confirm"),{once:true}); });
}

$("autopilot-toggle").addEventListener("change", async e => {
  const enabled=e.target.checked; if(enabled && !await confirmAction("Activer le pilotage automatique intégral ?","L’application commandera les barres, turbines, pompes et vannes exposées par le jeu. Surveillez le premier cycle.",false)){e.target.checked=false;return;}
  try { await api('/api/autopilot',{method:'POST',body:JSON.stringify({enabled})}); showToast(enabled?'Pilote automatique activé':'Pilote automatique arrêté'); refresh(); } catch(err){e.target.checked=!enabled;showToast(err.message,true);}
});

$("scram").addEventListener("click", async () => { if(!await confirmAction("Déclencher le SCRAM ?","Cette commande insère les barres et lance le refroidissement d’urgence dans le jeu.")) return; try{await api('/api/scram',{method:'POST',body:'{}'});showToast('SCRAM envoyé au jeu');}catch(err){showToast(err.message,true);} });
async function acknowledge(id){try{await api('/api/ack',{method:'POST',body:JSON.stringify({alarm_id:id})});refresh();}catch(err){showToast(err.message,true);}}
async function acknowledgeAll(){try{const result=await api('/api/ack-all',{method:'POST',body:'{}'});showToast(`${result.acknowledged} alarme(s) acquittée(s)`);refresh();}catch(err){showToast(err.message,true);}}
$("ack-all").addEventListener("click",acknowledgeAll);

let searchTimer; $("variable-search").addEventListener("input",()=>{clearTimeout(searchTimer);searchTimer=setTimeout(loadVariables,180)});
async function loadVariables(){try{const data=await api(`/api/variables?q=${encodeURIComponent($("variable-search").value)}`);$("variable-rows").innerHTML=data.variables.map(v=>`<tr><td>${escapeHtml(v.name)}</td><td>${escapeHtml(String(v.value ?? '—'))}</td><td><span class="tag ${v.writable?'write':''}">${v.writable?'LECTURE / ÉCRITURE':'LECTURE'}</span></td></tr>`).join('');}catch(err){showToast(err.message,true);}}

async function loadSettings(){try{const c=await api('/api/config');$("game-url").value=c.game_url;$("poll-seconds").value=c.poll_seconds;$("control-seconds").value=c.control_seconds;$("pool-capacity").value=c.reservoir_capacities_l.core_pool_tank;$("external-capacity").value=c.reservoir_capacities_l.external_coolant;$("emergency-generator-1-installation").value=c.equipment_overrides?.emergency_generators?.["1"]||"auto";$("emergency-generator-2-installation").value=c.equipment_overrides?.emergency_generators?.["2"]||"auto";$("target-temp").value=c.autopilot.target_core_temp;$("grid-buffer").value=c.autopilot.grid_buffer_mw;$("target-boron").value=c.autopilot.target_boron_ppm ?? '';$("boron-deadband").value=c.autopilot.boron_deadband_ppm;$("boron-max-output").value=c.autopilot.boron_max_output_pct;$("xenon-warning-ratio").value=c.thresholds.xenon_warning_ratio;$("xenon-critical-ratio").value=c.thresholds.xenon_critical_ratio;$("xenon-rise-guard").value=c.thresholds.xenon_rise_guard_pct_per_min;$("xenon-power-ramp").value=c.autopilot.xenon_power_ramp_mw_per_min;$("xenon-temp-ramp").value=c.autopilot.xenon_temp_ramp_c_per_cycle;$("area-toggles").innerHTML=Object.entries(AREA_LABELS).map(([key,label])=>`<label class="area-check"><input type="checkbox" data-area="${key}" ${c.autopilot.areas[key]?'checked':''}>${label}</label>`).join('');}catch(err){showToast(err.message,true);}}
$("settings-form").addEventListener("submit",async e=>{e.preventDefault();const areas={};document.querySelectorAll('[data-area]').forEach(i=>areas[i.dataset.area]=i.checked);const targetBoron=$("target-boron").value.trim();const payload={game_url:$("game-url").value,poll_seconds:Number($("poll-seconds").value),control_seconds:Number($("control-seconds").value),reservoir_capacities_l:{core_pool_tank:Number($("pool-capacity").value),external_coolant:Number($("external-capacity").value)},equipment_overrides:{emergency_generators:{"1":$("emergency-generator-1-installation").value,"2":$("emergency-generator-2-installation").value}},thresholds:{xenon_warning_ratio:Number($("xenon-warning-ratio").value),xenon_critical_ratio:Number($("xenon-critical-ratio").value),xenon_rise_guard_pct_per_min:Number($("xenon-rise-guard").value)},autopilot:{target_core_temp:Number($("target-temp").value),grid_buffer_mw:Number($("grid-buffer").value),target_boron_ppm:targetBoron===''?null:Number(targetBoron),boron_deadband_ppm:Number($("boron-deadband").value),boron_max_output_pct:Number($("boron-max-output").value),xenon_power_ramp_mw_per_min:Number($("xenon-power-ramp").value),xenon_temp_ramp_c_per_cycle:Number($("xenon-temp-ramp").value),areas}};try{await api('/api/config',{method:'POST',body:JSON.stringify(payload)});$("save-status").textContent='Réglages enregistrés';showToast('Configuration enregistrée');setTimeout(()=>$("save-status").textContent='',2500);}catch(err){showToast(err.message,true);}});

function beep(){try{const audio=new AudioContext();const osc=audio.createOscillator(),gain=audio.createGain();osc.frequency.value=660;gain.gain.setValueAtTime(.04,audio.currentTime);gain.gain.exponentialRampToValueAtTime(.001,audio.currentTime+.18);osc.connect(gain).connect(audio.destination);osc.start();osc.stop(audio.currentTime+.18);}catch(_) {}}
function escapeHtml(value){return String(value).replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));}

switchSupervisionZone("synthesis"); loadSettings(); refresh(); refreshHistory(); setInterval(refresh,1000); setInterval(refreshHistory,10000); window.addEventListener('resize',()=>{drawChart();drawPoisonChart();});
