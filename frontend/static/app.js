// ---------------------------------------------------------------------------
// NE-SLAI frontend
// Talks to the Flask backend over plain REST + a Server-Sent Events stream
// for real-time incident push (no polling).
// ---------------------------------------------------------------------------

const state = {
  locations: {},       // name -> attrs
  currentRoutes: [],
  routeLayers: [],      // leaflet polylines for the currently drawn routes
  incidentMarkers: {},  // incident id -> leaflet marker
  selectedRouteIndex: 0,
};

const map = L.map('map', { zoomControl: true }).setView([25.6, 93.0], 6.4);

L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
  attribution: '&copy; OpenStreetMap contributors',
  maxZoom: 19,
}).addTo(map);

const ROUTE_COLORS = {
  recommended: '#3fa796',
  alternate: '#e8a33d',
  not_advisable: '#d6534a',
};

const INCIDENT_ICONS = {
  landslide: '\u26f0\ufe0f',
  flood: '\ud83c\udf0a',
  roadblock: '\ud83d\udea7',
  accident: '\ud83d\udea8',
  snow_closure: '\u2744\ufe0f',
  weather_alert: '⚠️',
};

function el(id) { return document.getElementById(id); }

function showToast(message) {
  const host = el('toastHost');
  const div = document.createElement('div');
  div.className = 'toast';
  div.textContent = message;
  host.appendChild(div);
  setTimeout(() => {
    div.style.opacity = '0';
    div.style.transition = 'opacity 0.4s';
    setTimeout(() => div.remove(), 400);
  }, 6000);
}

// ---------------------------------------------------------------------------
// Bootstrap: load locations, populate dropdowns + place markers
// ---------------------------------------------------------------------------
async function loadLocations() {
  const res = await fetch('/api/locations');
  const locations = await res.json();
  locations.forEach(loc => { state.locations[loc.name] = loc; });

  const sourceSel = el('sourceSelect');
  const destSel = el('destSelect');
  const accessSel = el('accessSelect');

  const byState = {};
  locations.forEach(loc => {
    byState[loc.state] = byState[loc.state] || [];
    byState[loc.state].push(loc);
  });

  [sourceSel, destSel, accessSel].forEach(sel => sel.innerHTML = '');

  Object.keys(byState).sort().forEach(stateName => {
    [sourceSel, destSel, accessSel].forEach(sel => {
      const group = document.createElement('optgroup');
      group.label = stateName;
      byState[stateName].forEach(loc => {
        const opt = document.createElement('option');
        opt.value = loc.name;
        opt.textContent = loc.name;
        group.appendChild(opt.cloneNode(true));
      });
      sel.appendChild(group);
    });
  });

  sourceSel.value = 'Guwahati';
  destSel.value = 'Aizawl';
  accessSel.value = 'Guwahati';

  // place location markers
  locations.forEach(loc => {
    L.circleMarker([loc.lat, loc.lon], {
      radius: 4,
      color: '#8fa3a6',
      fillColor: '#8fa3a6',
      fillOpacity: 0.9,
      weight: 1,
    }).addTo(map).bindTooltip(loc.name, { direction: 'top', opacity: 0.85 });
  });
}

// ---------------------------------------------------------------------------
// Road-segment dropdown for the "report incident" demo panel
// ---------------------------------------------------------------------------
async function loadEdgesForReporting() {
  // We don't have a dedicated /api/edges endpoint; derive road segments
  // from a route between the two most distant demo nodes is unnecessary —
  // instead fetch the accessibility list to get node names, and hardcode
  // segment source from known routing pairs via a lightweight endpoint call.
  const res = await fetch('/api/locations');
  const locations = await res.json();
  const names = locations.map(l => l.name);
  // Build the same edge list the backend uses, purely for the UI dropdown.
  const edges = KNOWN_EDGES.filter(([a, b]) => names.includes(a) && names.includes(b));
  const sel = el('edgeSelect');
  sel.innerHTML = '';
  edges.forEach(([a, b, hwy]) => {
    const opt = document.createElement('option');
    opt.value = JSON.stringify([a, b]);
    opt.textContent = `${a} \u2194 ${b}  (${hwy})`;
    sel.appendChild(opt);
  });
}

// Mirrors backend/graph_data.py EDGES (name + highway only, for the dropdown)
const KNOWN_EDGES = [
  ["Siliguri", "Guwahati", "NH-27"],
  ["Siliguri", "Gangtok", "NH-10"],
  ["Guwahati", "Tezpur", "NH-15"],
  ["Tezpur", "North Lakhimpur", "NH-15"],
  ["North Lakhimpur", "Dibrugarh", "NH-15"],
  ["Guwahati", "Nagaon", "NH-27"],
  ["Nagaon", "Jorhat", "NH-27"],
  ["Jorhat", "Dibrugarh", "NH-37"],
  ["Jorhat", "Mokokchung", "State Road"],
  ["Dibrugarh", "Pasighat", "NH-13"],
  ["Guwahati", "Itanagar", "NH-15/415"],
  ["Itanagar", "Ziro", "State Road"],
  ["Ziro", "Pasighat", "State Road"],
  ["Tezpur", "Tawang", "NH-13 Sela Pass"],
  ["Guwahati", "Shillong", "NH-40"],
  ["Shillong", "Jowai", "NH-40"],
  ["Jowai", "Silchar", "NH-6"],
  ["Shillong", "Tura", "NH-44E"],
  ["Guwahati", "Dimapur", "NH-27"],
  ["Dimapur", "Kohima", "NH-29"],
  ["Kohima", "Imphal", "NH-2"],
  ["Kohima", "Mokokchung", "State Road"],
  ["Mokokchung", "Imphal", "NH-202"],
  ["Imphal", "Churachandpur", "NH-2"],
  ["Silchar", "Imphal", "NH-37"],
  ["Silchar", "Aizawl", "NH-306"],
  ["Silchar", "Karimganj", "NH-8"],
  ["Karimganj", "Agartala", "NH-8"],
  ["Churachandpur", "Aizawl", "State Road"],
  ["Aizawl", "Champhai", "NH-102B"],
  ["Aizawl", "Lunglei", "NH-54"],
  ["Lunglei", "Agartala", "NH-8/SH"],
];

// ---------------------------------------------------------------------------
// Route finding + drawing
// ---------------------------------------------------------------------------
function clearRouteLayers() {
  state.routeLayers.forEach(l => map.removeLayer(l));
  state.routeLayers = [];
}

function drawRoute(route, isSelected) {
  const color = ROUTE_COLORS[route.label] || '#8fa3a6';

  // ---------------------------------------------------------
  // REAL OSRM ROAD GEOMETRY
  // ---------------------------------------------------------

  if (route.geometry && route.geometry.length > 1) {
    const line = L.polyline(route.geometry, {
      color,
      weight: isSelected ? 6 : 3,
      opacity: isSelected ? 0.95 : 0.35,
    }).addTo(map);

    line.bindPopup(`
      <b>Route</b><br>
      Distance: ${route.distance_km} km<br>
      Estimated time: ${formatMinutes(route.estimated_time_min)}<br>
      Risk: ${route.risk_score}/100<br>
      Road geometry: ${route.geometry_source || 'OSRM'}
    `);

    state.routeLayers.push(line);
  }

  // ---------------------------------------------------------
  // KEEP SEGMENT INFORMATION
  // ---------------------------------------------------------

  route.segments.forEach(seg => {
    let popup = `
      <b>${seg.from} ↔ ${seg.to}</b><br>
      ${seg.highway}<br>
      ${seg.distance_km} km
      &middot;
      ~${seg.estimated_time_min} min
    `;

    if (seg.active_incident) {
      popup += `
        <br>
        <span style="color:#d6534a">
          ${seg.active_incident.type}
          (${seg.active_incident.severity}):
          ${seg.active_incident.description}
        </span>
      `;
    }
  });
}

function renderRoutes() {
  clearRouteLayers();
  state.currentRoutes.forEach((route, idx) => {
    drawRoute(route, idx === state.selectedRouteIndex);
  });

  const listEl = el('routesList');
  listEl.innerHTML = '';
  state.currentRoutes.forEach((route, idx) => {
    const card = document.createElement('div');
    card.className = `route-card ${route.label}` + (idx === state.selectedRouteIndex ? ' active-selection' : '');
    const tagText = route.label === 'recommended' ? 'Recommended \u2014 best current option'
                  : route.label === 'alternate' ? 'Alternate route'
                  : 'Not advisable right now';

    let warningHtml = '';
    if (route.incidents_on_route.length) {
      const desc = route.incidents_on_route.map(i => `${i.location}: ${i.type} (${i.severity})`).join('; ');
      warningHtml = `<div class="route-warning">\u26a0 ${desc}</div>`;
    }

    card.innerHTML = `
      <div class="row-top">
        <span class="route-tag">${tagText}</span>
        <span class="route-time">${formatMinutes(route.estimated_time_min)}</span>
      </div>
      <div class="route-meta">
        <span>${route.distance_km} km</span>
        <span>risk ${route.risk_score}/100</span>
        <span>${route.segments.length} leg${route.segments.length > 1 ? 's' : ''}</span>
      </div>
      ${warningHtml}
    `;
    card.addEventListener('click', () => {
      state.selectedRouteIndex = idx;
      renderRoutes();
      fitToRoute(route);
    });
    listEl.appendChild(card);
  });

  el('routesCard').style.display = 'block';
}

function formatMinutes(mins) {
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function fitToRoute(route) {
  const coords = route.geometry && route.geometry.length
    ? route.geometry
    : route.segments.flatMap(s => s.coords);

  const bounds = L.latLngBounds(coords);

  map.fitBounds(bounds, {
    padding: [40, 40]
  });
}

async function findRoutes() {
  const source = el('sourceSelect').value;
  const destination = el('destSelect').value;
  if (source === destination) {
    showToast('Choose two different locations.');
    return;
  }

  const btn = el('findRouteBtn');
  btn.disabled = true;
  btn.textContent = 'Calculating\u2026';

  try {
    const res = await fetch('/api/route', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source, destination }),
    });
    const data = await res.json();
    if (!res.ok) {
      showToast(data.error || 'Could not compute a route.');
      return;
    }
    state.currentRoutes = data.routes;
    state.selectedRouteIndex = 0;
    renderRoutes();
    fitToRoute(data.routes[0]);
  } catch (e) {
    showToast('Could not reach the routing service.');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Find Routes';
  }
}
async function analyzeRouteWithAI() {
  if (!state.currentRoutes.length) {
    showToast('Find a route first.');
    return;
  }

  const source = el('sourceSelect').value;
  const destination = el('destSelect').value;

  const selectedRoute =
    state.currentRoutes[state.selectedRouteIndex];

  const btn = el('aiAnalysisBtn');
  const resultBox = el('aiAnalysisResult');
  const textBox = el('aiAnalysisText');

  btn.disabled = true;
  btn.textContent = '🤖 Analyzing route...';

  resultBox.style.display = 'block';
  textBox.innerHTML = '<p class="muted">Gemini is analyzing the current route and risks...</p>';

  try {
    const res = await fetch('/api/ai-analysis', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        source,
        destination,
        selected_route: selectedRoute,
        routes: state.currentRoutes
      })
    });

    const data = await res.json();

    if (!res.ok) {
      textBox.innerHTML = `
        <p class="muted">
          ${data.error || 'AI analysis failed.'}
        </p>
      `;
      return;
    }

    textBox.innerHTML = `
      <div class="ai-response">
        ${formatAIResponse(data.analysis)}
      </div>
    `;

  } catch (error) {
    textBox.innerHTML = `
      <p class="muted">
        Could not connect to Gemini.
      </p>
    `;
  } finally {
    btn.disabled = false;
    btn.textContent = '🤖 AI Route Analysis';
  }
}

function formatAIResponse(text) {
  return text
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>');
}

// If the currently displayed route is affected by a brand-new incident,
// silently recompute so the map reflects live conditions immediately.
async function recalculateIfAffected(incident) {
  if (!state.currentRoutes.length) return;
  const [a, b] = incident.edge;
  const affected = state.currentRoutes.some(route =>
    route.segments.some(seg => (seg.from === a && seg.to === b) || (seg.from === b && seg.to === a))
  );
  if (!affected) return;

  showToast(`New ${incident.type} reported (${a} \u2194 ${b}) \u2014 recalculating your routes\u2026`);
  const source = el('sourceSelect').value;
  const destination = el('destSelect').value;
  const res = await fetch('/api/route', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source, destination }),
  });
  const data = await res.json();
  if (res.ok) {
    state.currentRoutes = data.routes;
    state.selectedRouteIndex = 0;
    renderRoutes();
  }
}

// ---------------------------------------------------------------------------
// Accessibility panel
// ---------------------------------------------------------------------------
async function loadAccessibility(name) {
  const res = await fetch(`/api/accessibility/${encodeURIComponent(name)}`);
  const data = await res.json();
  const container = el('accessResult');
  if (!res.ok) {
    container.innerHTML = `<p class="muted">${data.error}</p>`;
    return;
  }
  container.innerHTML = `
    <div class="access-score-row">
      <span class="access-score-num">${data.score}</span>
      <span class="access-tier">${data.tier}</span>
    </div>
    <div class="access-bar-track"><div class="access-bar-fill" style="width:${data.score}%"></div></div>
    <ul class="access-reasons">${data.reasons.map(r => `<li>${r}</li>`).join('')}</ul>
  `;
}

// ---------------------------------------------------------------------------
// Live incident feed (Server-Sent Events)
// ---------------------------------------------------------------------------
function upsertIncidentMarker(incident) {

  let lat = incident.lat;
  let lon = incident.lon;

  if (
    (!lat || !lon) &&
    incident.edge &&
    incident.edge.length === 2
  ) {

    const [a, b] = incident.edge;

    const locA = state.locations[a];
    const locB = state.locations[b];

    if (!locA || !locB) {
      return;
    }

    lat = (
      locA.lat + locB.lat
    ) / 2;

    lon = (
      locA.lon + locB.lon
    ) / 2;
  }

  if (!lat || !lon) {
    return;
  }

  if (
    state.incidentMarkers[
      incident.id
    ]
  ) {

    map.removeLayer(
      state.incidentMarkers[
        incident.id
      ]
    );
  }

  const icon = L.divIcon({

    className: '',

    html: `
      <div style="
        font-size:20px;
        filter:drop-shadow(0 0 4px #000);
      ">
        ${
          INCIDENT_ICONS[
            incident.type
          ] || '⚠️'
        }
      </div>
    `,

    iconSize: [24, 24],

    iconAnchor: [12, 12]
  });

  const marker = L.marker(
    [lat, lon],
    { icon }
  ).addTo(map);

  const locationText =
    incident.area
    ||
    (
      incident.edge &&
      incident.edge.length === 2
        ? `${incident.edge[0]} ↔ ${incident.edge[1]}`
        : "Government alert area"
    );

  marker.bindPopup(`

    <b>
      ${incident.type.replace('_', ' ')}
      — ${incident.severity}
    </b>

    <br>

    ${locationText}

    <br>

    ${incident.description}

    <br>

    <span style="color:#8fa3a6">
      Source: ${incident.source}
    </span>

    <br>

    <span style="color:#8fa3a6">
      ${new Date(
        incident.reported_at
      ).toLocaleString()}
    </span>

  `);

  state.incidentMarkers[
    incident.id
  ] = marker;
}

function removeIncidentMarker(incident) {
  if (state.incidentMarkers[incident.id]) {
    map.removeLayer(state.incidentMarkers[incident.id]);
    delete state.incidentMarkers[incident.id];
  }
}

function renderIncidentFeed(incidents) {

  const feed = el(
    'incidentFeed'
  );

  if (!incidents.length) {

    feed.innerHTML =
      '<p class="muted">No active government alerts right now.</p>';

    return;
  }

  const sorted = [
    ...incidents
  ].sort(
    (a, b) =>
      new Date(b.reported_at)
      -
      new Date(a.reported_at)
  );

  feed.innerHTML = sorted.map(
    incident => {

      const location =
        incident.area
        ||
        (
          incident.edge &&
          incident.edge.length === 2
            ? `${incident.edge[0]} ↔ ${incident.edge[1]}`
            : "Government alert area"
        );

      return `

        <div class="incident-item ${incident.severity}">

          <div class="inc-top">

            <span>
              ${
                incident.type
                  .replace('_', ' ')
              }
            </span>

            <span>
              ${incident.severity}
            </span>

          </div>

          <div>
            ${location}
          </div>

          <div class="muted">
            ${incident.description}
          </div>

          <div class="muted">
            Source:
            ${incident.source}
          </div>

        </div>

      `;
    }
  ).join('');
}

let activeIncidents = [];

function connectLiveStream() {
  const source = new EventSource('/api/stream');
  const indicator = el('liveIndicator');

  source.addEventListener('snapshot', (e) => {
    activeIncidents = JSON.parse(e.data);
    activeIncidents.forEach(upsertIncidentMarker);
    renderIncidentFeed(activeIncidents);
    indicator.classList.add('connected');
    indicator.innerHTML = '<span class="dot"></span>NDMA live feed connected';
  });

  source.addEventListener('incident_new', (e) => {
    const incident = JSON.parse(e.data);
    activeIncidents.push(incident);
    upsertIncidentMarker(incident);
    renderIncidentFeed(activeIncidents);
    showToast(`\u26a0 ${incident.type.replace('_', ' ')} (${incident.severity}): ${incident.edge[0]} \u2194 ${incident.edge[1]}`);
    recalculateIfAffected(incident);
  });

  source.addEventListener('incident_cleared', (e) => {
    const incident = JSON.parse(e.data);
    activeIncidents = activeIncidents.filter(i => i.id !== incident.id);
    removeIncidentMarker(incident);
    renderIncidentFeed(activeIncidents);
    showToast(`\u2705 Cleared: ${incident.edge[0]} \u2194 ${incident.edge[1]} reopened`);
    recalculateIfAffected(incident);
  });

  source.onerror = () => {
    indicator.classList.remove('connected');
    indicator.innerHTML = '<span class="dot"></span> reconnecting to NDMA\u2026';
  };
}

// ---------------------------------------------------------------------------
// Manual "report incident" demo panel
// ---------------------------------------------------------------------------
// async function reportIncident() {
//   const [node_a, node_b] = JSON.parse(el('edgeSelect').value);
//   const type = el('typeSelect').value;
//   const severity = el('severitySelect').value;

//   const btn = el('reportBtn');
//   btn.disabled = true;
//   try {
//     const res = await fetch('/api/incidents', {
//       method: 'POST',
//       headers: { 'Content-Type': 'application/json' },
//       body: JSON.stringify({ node_a, node_b, type, severity, ttl_minutes: 15 }),
//     });
//     const data = await res.json();
//     if (!res.ok) showToast(data.error || 'Could not report incident.');
//   } finally {
//     btn.disabled = false;
//   }
// }

// ---------------------------------------------------------------------------
// Wire up
// ---------------------------------------------------------------------------
(async function init() {
  await loadLocations();
  // await loadEdgesForReporting();
  connectLiveStream();

  el('findRouteBtn').addEventListener('click', findRoutes);

  el('aiAnalysisBtn').addEventListener('click',  analyzeRouteWithAI);

  el('accessSelect').addEventListener('change',(e) => loadAccessibility(e.target.value));

  // el('reportBtn').addEventListener('click', reportIncident);
  loadAccessibility('Guwahati');
  findRoutes();
})();

async function updateLiveRisk() {

  const source = el('sourceSelect').value;
  const destination = el('destSelect').value;

  if (!source || !destination || source === destination) {
    return;
  }

  try {

    const res = await fetch('/api/live-risk', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        source,
        destination
      })
    });

    const data = await res.json();

    if (!res.ok || !data.routes || !data.routes.length) {
      return;
    }

    state.currentRoutes = data.routes;

    if (
      state.selectedRouteIndex >=
      state.currentRoutes.length
    ) {
      state.selectedRouteIndex = 0;
    }

    renderRoutes();

  } catch (error) {

    console.log(
      'Live risk update failed:',
      error
    );
  }
}

// setInterval(
//   updateLiveRisk,
//   60000
// );