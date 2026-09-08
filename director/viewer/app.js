'use strict';
const $ = id => document.getElementById(id);
const params = () => new URLSearchParams(location.hash.slice(1));
const token = params().get('token') || '';
let rows = [], page = 0, requestVersion = 0;
const pageSize = 12;

function node(tag, text, className) {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  return element;
}

function showError(error) {
  $('error').hidden = !error;
  $('error').textContent = error ? error.message : '';
}

async function api(path) {
  if (!token) throw new Error('Open the full URL printed by director memory to access this local session.');
  const response = await fetch(path, {headers: {'X-Viewer-Token': token}, cache: 'no-store'});
  if (!response.ok) throw new Error(response.status === 403 ? 'This viewer session has ended. Reopen the URL printed by director memory.' : await response.text());
  return response.json();
}

function route(id) {
  const next = new URLSearchParams({token});
  if (id) next.set('lesson', id);
  location.hash = next.toString();
}

function list() {
  const query = $('lp-search').value.toLowerCase();
  const filtered = rows.filter(row => (row.title + ' ' + row.kind + ' ' + row.id).toLowerCase().includes(query));
  page = Math.min(page, Math.max(0, Math.ceil(filtered.length / pageSize) - 1));
  $('lp-list').replaceChildren();
  for (const row of filtered.slice(page * pageSize, (page + 1) * pageSize)) {
    const button = node('button', '', 'lp-item');
    button.type = 'button';
    button.append(node('span', row.kind, 'lp-id'), node('span', row.title, 'lp-title'),
      node('span', (row.stored_at || row.source_date || 'Date unknown').slice(0, 10), 'lp-date'), node('span', '›', 'lp-arrow'));
    button.onclick = () => route(row.id);
    $('lp-list').append(button);
  }
  if (!filtered.length) $('lp-list').append(node('p', rows.length ? 'No matching teachings.' : 'No saved teachings found. Add teaching to your profile to see it here.', 'lp-empty'));
  $('lp-count').textContent = `${rows.length} saved entries`;
  $('lp-range').textContent = filtered.length ? `${page * pageSize + 1}–${Math.min((page + 1) * pageSize, filtered.length)} of ${filtered.length} entries` : '0 entries';
  $('lp-prev').disabled = page === 0;
  $('lp-next').disabled = (page + 1) * pageSize >= filtered.length;
}

function blocks(id, items, empty, interpretation = false) {
  $(id).replaceChildren();
  for (const item of items) {
    const box = node('div', '', 'lp-box' + (interpretation ? ' understanding' : ''));
    box.append(node('p', item.text, 'lp-understanding'), node('p', `${item.label} · ${item.path}`, 'lp-source'));
    $(id).append(box);
  }
  if (!items.length) $(id).append(node('p', empty, 'lp-sub'));
}

function detail(row) {
  $('lp-detail-id').textContent = row.kind;
  $('lp-detail-title').textContent = row.title;
  $('lp-status').textContent = `Status: ${row.status}`;
  $('lp-source-date').textContent = row.source_date || 'Not recorded';
  $('stored-at').textContent = row.stored_at || 'Not recorded per entry';
  $('scope').textContent = row.scope || 'Not explicitly recorded';
  blocks('evidence', row.evidence, 'No separate original words recorded. Inspect the saved guidance below.');
  blocks('interpretations', row.interpretations, 'No separate saved interpretation identified.', true);
  $('source').textContent = row.source || '';
  $('source-section').hidden = !row.source;
  $('locations').replaceChildren();
  for (const item of row.locations) {
    const details = document.createElement('details');
    details.append(node('summary', item.path + (item.line ? `:${item.line}` : '') + ' · Inspect saved record'));
    if (item.recorded_at) details.append(node('p', 'Recorded: ' + item.recorded_at, 'lp-source'));
    details.append(node('pre', item.raw));
    $('locations').append(details);
  }
  $('conditions').replaceChildren();
  for (const [key, value] of Object.entries(row.conditions)) {
    $('conditions').append(node('dt', key.replaceAll('_', ' ')), node('dd', value));
  }
  $('related-section').hidden = !row.related.length;
  $('related').replaceChildren();
  for (const item of row.related) {
    const button = node('button', item.title, 'lp-button');
    button.type = 'button';
    button.onclick = () => route(item.id);
    $('related').append(button);
  }
}

async function renderRoute() {
  const version = ++requestVersion;
  const id = params().get('lesson');
  showError(null);
  $('lp-list-page').hidden = Boolean(id);
  $('lp-detail-page').hidden = true;
  if (!id) { list(); return; }
  try {
    const row = await api('/api/lesson?id=' + encodeURIComponent(id));
    if (version !== requestVersion) return;
    detail(row);
    $('lp-detail-page').hidden = false;
    $('lp-back').focus({preventScroll: true});
    window.scrollTo(0, 0);
  } catch (error) {
    if (version !== requestVersion) return;
    $('lp-list-page').hidden = false;
    list();
    showError(error);
  }
}

async function refresh() {
  $('refresh').disabled = true;
  try {
    const result = await api('/api/lessons');
    rows = result.records;
    $('warnings').hidden = !result.warnings.length;
    $('warning-text').replaceChildren(...result.warnings.map(text => node('p', text)));
    await renderRoute();
  } catch (error) { showError(error); $('lp-count').textContent = 'Could not load teaching.'; }
  finally { $('refresh').disabled = false; }
}

$('lp-back').onclick = () => route(null);
$('lp-search').oninput = () => { page = 0; list(); };
$('lp-prev').onclick = () => { page--; list(); };
$('lp-next').onclick = () => { page++; list(); };
$('refresh').onclick = refresh;
window.addEventListener('hashchange', renderRoute);
refresh();
