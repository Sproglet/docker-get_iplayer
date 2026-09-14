'use strict';
const base = document.body.dataset.prefix;
const $ = id => document.getElementById(id);
let csrf = '', view = 'search', events, queue = {items: [], counts: {}, total: 0}, logId = null, logBusy = false;
const offsets = {search: 0, queue: 0, history: 0};
const pendingKeys = new Map();
const titles = {search: 'Discover', queue: 'Downloads', history: 'History', pvr: 'Scheduled'};
function node(tag, text, className) { const n = document.createElement(tag); if (text !== undefined) n.textContent = text; if (className) n.className = className; return n; }
function button(text, action, className = 'secondary') { const n = node('button', text, className); n.type = 'button'; n.addEventListener('click', async () => { n.disabled = true; try { await action(); } catch (e) { notice(e.message, true); } finally { n.disabled = false; } }); return n; }
function notice(message, error = false) { $('notice').hidden = false; $('notice').className = error ? 'error' : ''; $('notice').textContent = message; }
function showLogin() { events?.close(); $('application').hidden = true; $('login').hidden = false; $('log-dialog').close(); csrf = ''; }
async function api(path, body, key) {
  let response;
  try { response = await fetch(base + '/api/' + path, {method: body ? 'POST' : 'GET', credentials: 'same-origin', headers: body ? {'Content-Type': 'application/json', 'X-CSRF-Token': csrf, ...(key ? {'Idempotency-Key': key} : {})} : {}, ...(body ? {body: JSON.stringify(body)} : {})}); }
  catch (_) { throw new Error('Cannot reach Recorder. Check the connection and try again; accepted jobs stay in your queue.'); }
  const data = await response.json();
  if (!response.ok) { if (response.status === 401) showLogin(); throw new Error(data.error || 'This request could not be completed. Try again.'); }
  return data;
}
function empty(target, title, detail, action) { const card = node('div', undefined, 'empty'); card.append(node('span', '◌', 'empty-icon'), node('h3', title), node('p', detail)); if (action) card.append(button(action[0], action[1], 'primary')); $(target).replaceChildren(card); }
function loading(target) { empty(target, 'Just a moment…', 'Loading your library.'); }
function when(value) { if (!value || !Number.isFinite(Number(value))) return 'Date unavailable'; return new Date(Number(value) * 1000).toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'}); }
function pages(type, total) { const parent = $(type + '-pages'), offset = offsets[type], size = 40; parent.replaceChildren(); if (!total) return; const prev = button('← Previous', () => { offsets[type] = Math.max(0, offset - size); return loadView(); }); prev.disabled = offset === 0; const next = button('Next →', () => { offsets[type] = offset + size; return loadView(); }); next.disabled = offset + size >= total; parent.append(prev, node('span', `${offset + 1}–${Math.min(offset + size, total)} of ${total}`), next); }
async function enqueue(kind, pid = '', title = '') {
  const signature = kind + ':' + pid;
  if (!pendingKeys.has(signature)) pendingKeys.set(signature, [...crypto.getRandomValues(new Uint8Array(16))].map(n => n.toString(16).padStart(2, '0')).join(''));
  const data = await api('jobs', {kind, pid, title}, pendingKeys.get(signature));
  pendingKeys.delete(signature);
  notice(data.created ? `${data.job.title} added to the queue.` : `${data.job.title} is already ${data.job.state}.`);
  await refreshQueue();
  return data;
}
function warnings(data) { if (data.warnings?.length) notice(data.warnings.join(' '), true); }
async function search() {
  const query = new URLSearchParams({q: $('search-input').value, type: $('media-type').value, limit: 40, offset: offsets.search});
  const data = await api('search?' + query);
  warnings(data);
  const latest = Math.max(...data.caches.map(c => c.updated || 0));
  $('catalogue-status').textContent = latest ? `Updated ${when(latest)}` : 'Your catalogue is ready to fill';
  if (!data.items.length) {
    const missing = !data.caches.some(c => c.updated);
    empty('search-results', missing ? 'Your next favourite starts here.' : 'No programmes found.', missing ? 'Refresh the catalogue to bring in available TV and radio programmes. You can also queue a BBC episode link above.' : 'Try a shorter title, another channel or a different programme type. If the catalogue is out of date, refresh it.', ['Refresh catalogue', () => enqueue('refresh')]);
  } else {
    const cards = data.items.map(item => {
      const card = node('article', undefined, 'programme-card');
      const top = node('div', undefined, 'programme-top' + (item.type === 'radio' ? ' radio' : ''));
      top.append(node('span', item.channel || (item.type === 'radio' ? 'BBC Radio' : 'BBC TV')), node('span', item.type === 'radio' ? 'RADIO' : 'TV'));
      const body = node('div', undefined, 'programme-body'); body.append(node('h3', item.name || item.pid), node('p', item.episode || 'Programme', 'episode'), node('p', item.desc || 'No description is available for this programme.', 'description'));
      const active = queue.items.find(job => job.target === item.pid && ['queued', 'running'].includes(job.state));
      body.append(active ? button(active.state === 'running' ? 'Recording now →' : 'In your queue →', () => navigate('queue')) : button('＋ Add to queue', () => enqueue('download', item.pid, [item.name, item.episode].filter(Boolean).join(' · '))));
      card.append(top, body); return card;
    });
    $('search-results').replaceChildren(...cards);
  }
  pages('search', data.total);
}
function renderQueue(data) {
  const focused = document.activeElement?.dataset.focusKey;
  const cards = data.items.map(job => {
    const card = node('article', undefined, 'job');
    const heading = node('div', undefined, 'job-heading'), title = node('div', undefined, 'job-title');
    const stateText = job.cancel_requested && job.state === 'running' ? 'Cancelling…' : job.state[0].toUpperCase() + job.state.slice(1);
    title.append(node('span', stateText, 'state ' + job.state), node('p', `${job.kind === 'download' ? 'Programme' : job.kind === 'pvr' ? 'PVR recordings' : 'Catalogue refresh'} · ${job.source === 'schedule' ? 'Scheduled' : 'Requested'} · ${when(job.created)}${job.attempt > 1 ? ' · Attempt ' + job.attempt : ''}`, 'job-meta'), node('h3', job.title));
    const actions = node('div', undefined, 'job-actions');
    const logs = button('View log', () => openLog(job.id)); logs.dataset.focusKey = job.id + '-log'; actions.append(logs);
    if (['queued', 'running'].includes(job.state)) {
      const cancel = button(job.cancel_requested ? 'Stopping…' : 'Cancel', async () => { await api('jobs/' + job.id + '/cancel', {}); notice('Cancellation requested for this job. Other jobs will continue.'); await refreshQueue(); });
      cancel.dataset.focusKey = job.id + '-cancel'; cancel.disabled = Boolean(job.cancel_requested); actions.append(cancel);
    } else if (['failed', 'cancelled', 'interrupted'].includes(job.state)) {
      const retry = button('Retry', async () => { const result = await api('jobs/' + job.id + '/retry', {}); notice(result.created ? 'A new attempt has been queued.' : 'The retry already exists.'); await refreshQueue(); }); retry.dataset.focusKey = job.id + '-retry'; actions.append(retry);
    }
    heading.append(title, actions); card.append(heading);
    if (job.state === 'running') {
      const line = node('div', undefined, 'progress-line'), progress = node('progress'); progress.max = 100; progress.setAttribute('aria-label', 'Current engine stream progress'); if (job.progress !== null) progress.value = job.progress;
      line.append(progress, node('span', job.progress === null ? 'Working…' : job.progress.toFixed(1) + '%'));
      card.append(line);
    }
    card.append(node('p', job.message || (job.state === 'queued' ? 'Waiting its turn. It will start when the worker is free.' : 'No additional details.'), 'job-message'));
    return card;
  });
  if (!cards.length) empty('queue-results', 'Nothing waiting in the wings.', 'Find a programme and add it to your queue. Downloads and scheduled recordings will appear here.', ['Discover programmes', () => navigate('search')]);
  else $('queue-results').replaceChildren(...cards);
  if (focused) [...$('queue-results').querySelectorAll('button')].find(b => b.dataset.focusKey === focused)?.focus({preventScroll: true});
  pages('queue', data.total);
}
function counts(data) { $('stat-running').textContent = data.counts.running || 0; $('stat-queued').textContent = data.counts.queued || 0; $('stat-completed').textContent = data.counts.completed || 0; $('queue-badge').textContent = (data.counts.running || 0) + (data.counts.queued || 0); }
async function refreshQueue() { const data = await api('jobs?limit=40&offset=' + offsets.queue); counts(data); if (view === 'queue') renderQueue(data); }
async function history() {
  const data = await api('history?' + new URLSearchParams({q: $('history-input').value, offset: offsets.history, limit: 40})); warnings(data);
  if (!data.items.length) empty('history-results', $('history-input').value ? 'No matching recordings.' : 'A collection in the making.', $('history-input').value ? 'Try another title or programme ID.' : 'Completed recordings from get_iplayer will appear here, including recordings from your existing history.', ['Discover programmes', () => navigate('search')]);
  else $('history-results').replaceChildren(...data.items.map(item => { const row = node('article', undefined, 'history-item'), detail = node('div'); detail.append(node('h3', item.name || item.pid), node('p', [item.episode, item.channel].filter(Boolean).join(' · '), 'muted'), node('p', `${item.type === 'radio' ? 'Radio' : 'TV'} · ${item.pid}`, 'hint')); row.append(detail, node('span', when(item.timeadded), 'history-date')); return row; }));
  pages('history', data.total);
}
async function pvr() {
  const data = await api('pvr'); warnings(data);
  $('pvr-run').disabled = !data.items.some(item => !item.disabled);
  if (!data.items.length) empty('pvr-results', 'No saved searches yet.', 'Existing get_iplayer PVR searches will appear here automatically. For now, you can record individual programmes from Discover.', ['Discover programmes', () => navigate('search')]);
  else $('pvr-results').replaceChildren(...data.items.map(item => { const card = node('article', undefined, 'job'), heading = node('div', undefined, 'job-heading'); heading.append(node('h3', item.name), node('span', item.disabled ? 'Disabled' : 'Hourly', 'state ' + (item.disabled ? 'cancelled' : 'completed'))); card.append(heading, node('p', `${item.type} · ${item.search}`, 'job-message')); return card; }));
}
async function loadView() {
  try { if (view === 'search') await search(); if (view === 'queue') await refreshQueue(); if (view === 'history') await history(); if (view === 'pvr') await pvr(); }
  catch (error) { notice(error.message, true); empty(view === 'search' ? 'search-results' : view + '-results', 'We could not load this view.', error.message, ['Try again', loadView]); }
}
function navigate(next) { location.hash = next; if (view === next) return loadView(); }
function selectView() { view = Object.hasOwn(titles, location.hash.slice(1)) ? location.hash.slice(1) : 'search'; document.title = titles[view] + ' · Recorder'; document.querySelectorAll('.view').forEach(n => { n.hidden = n.id !== 'view-' + view; }); document.querySelectorAll('[data-view]').forEach(n => { n.classList.toggle('active', n.dataset.view === view); n.setAttribute('aria-current', n.dataset.view === view ? 'page' : 'false'); }); if (csrf) { loading(view + '-results'); loadView(); } }
async function openLog(id) { logId = id; $('log-output').textContent = 'Loading log…'; $('log-dialog').showModal(); await updateLog(); }
async function updateLog() { if (!logId || logBusy || !$('log-dialog').open) return; logBusy = true; const id = logId; try { const job = await api('jobs/' + id); if (id !== logId) return; $('log-title').textContent = job.title; $('log-message').textContent = `${job.state} · ${job.message}`; const pre = $('log-output'), atEnd = pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30; pre.textContent = job.log || (job.state === 'queued' ? 'This job has not started yet.' : 'No retained engine output for this job. Older job logs may have expired.'); if (atEnd) pre.scrollTop = pre.scrollHeight; } catch (error) { $('log-message').textContent = error.message; } finally { logBusy = false; } }
function connect() {
  events?.close(); events = new EventSource(base + '/api/events');
  events.onopen = () => { $('connection').textContent = 'Live updates'; $('connection').classList.remove('offline'); };
  events.onmessage = event => { const next = JSON.parse(event.data), previousCompleted = queue.counts.completed || 0, previousActive = JSON.stringify(queue.items.filter(j => ['queued', 'running'].includes(j.state)).map(j => [j.id, j.state])); queue = next; if (view === 'search' && (previousCompleted !== (next.counts.completed || 0) || previousActive !== JSON.stringify(next.items.filter(j => ['queued', 'running'].includes(j.state)).map(j => [j.id, j.state])))) search().catch(e => notice(e.message, true)); counts(next); if (view === 'queue') { if (offsets.queue === 0) renderQueue({...next, items: next.items.slice(0, 40)}); else refreshQueue().catch(e => notice(e.message, true)); } if (view === 'history' && previousCompleted !== (next.counts.completed || 0)) history().catch(e => notice(e.message, true)); updateLog(); };
  events.onerror = () => { $('connection').textContent = 'Reconnecting…'; $('connection').classList.add('offline'); api('session').catch(() => {}); };
}
async function enter() { const session = await api('session'); csrf = session.csrf; $('login').hidden = true; $('application').hidden = false; selectView(); connect(); }
$('login-form').addEventListener('submit', async event => { event.preventDefault(); const submit = event.submitter; submit.disabled = true; $('login-error').textContent = ''; try { const result = await api('login', {token: $('token').value}); csrf = result.csrf; $('token').value = ''; await enter(); } catch (error) { $('login-error').textContent = error.message; } finally { submit.disabled = false; } });
$('logout').addEventListener('click', async () => { try { await api('logout', {}); showLogin(); } catch (e) { notice(e.message, true); } });
document.querySelectorAll('[data-view]').forEach(n => n.addEventListener('click', () => navigate(n.dataset.view)));
window.addEventListener('hashchange', selectView);
$('search-form').addEventListener('submit', event => { event.preventDefault(); offsets.search = 0; loadView(); });
$('history-form').addEventListener('submit', event => { event.preventDefault(); offsets.history = 0; loadView(); });
$('pid-form').addEventListener('submit', async event => { event.preventDefault(); const submit = event.submitter; submit.disabled = true; try { await enqueue('download', $('pid-input').value); $('pid-input').value = ''; navigate('queue'); } catch (e) { notice(e.message, true); } finally { submit.disabled = false; } });
for (const [id, kind] of [['refresh', 'refresh'], ['pvr-run', 'pvr']]) $(id).addEventListener('click', async () => { $(id).disabled = true; try { await enqueue(kind); } catch (e) { notice(e.message, true); } finally { $(id).disabled = false; } });
$('close-log').addEventListener('click', () => $('log-dialog').close());
$('log-dialog').addEventListener('close', () => { logId = null; });
setInterval(updateLog, 2000);
enter().catch(error => { showLogin(); if (!error.message.includes('Sign in')) $('login-error').textContent = error.message; });
