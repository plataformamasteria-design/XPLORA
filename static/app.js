const $ = (selector) => document.querySelector(selector);
const meetingsEl = $('#meetings');
const toastEl = $('#toast');
const sendForm = $('#send-form');
const sendButton = sendForm.querySelector('button[type="submit"]');
let runtimeReady = false;
let activeBot = false;
let meetingsLoading = false;
let recordingsCache = [];

function toast(message, bad = false) {
  toastEl.textContent = message;
  toastEl.className = bad ? 'show bad' : 'show';
  setTimeout(() => { toastEl.className = ''; }, 4200);
}

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options });
  const contentType = response.headers.get('content-type') || '';
  const body = contentType.includes('application/json') ? await response.json() : { error: await response.text() };
  if (!response.ok) throw new Error(body.error || `Não foi possível concluir a ação (${response.status}).`);
  return body;
}

function prettyDate(value) {
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value));
}

function state(status) {
  const labels = { starting: 'Preparando entrada', in_meeting: 'Na reunião', stopping: 'Saindo e transcrevendo', removed: 'Removido', finished: 'Finalizado', failed: 'Falhou', interrupted: 'Interrompido', transcribing: 'Transcrevendo', transcribed: 'Transcrito', transcription_failed: 'Falhou ao transcrever' };
  return labels[status] || status;
}

function active(status) { return ['starting', 'in_meeting', 'stopping'].includes(status); }

function card(meeting) {
  const isBot = !meeting.recording;
  const recentLogs = (meeting.logs || []).slice(-20);
  const logs = recentLogs.map(line => `<div>${escapeHtml(line)}</div>`).join('');
  const lastError = ['failed', 'transcription_failed'].includes(meeting.status) && recentLogs.length ? `<p class="card-error">${escapeHtml(recentLogs.at(-1))}</p>` : '';
  return `<article class="card"><div class="card-top"><div><span class="status ${meeting.status}"></span><strong>${escapeHtml(meeting.name)}</strong><p>${meeting.recording ? escapeHtml(meeting.recording) : escapeHtml(meeting.url)}</p></div><span class="tag">${state(meeting.status)}</span></div>${lastError}<div class="card-foot"><small>${prettyDate(meeting.created_at)}</small>${isBot && active(meeting.status) ? `<button class="remove" data-stop="${meeting.id}" ${meeting.status === 'stopping' ? 'disabled' : ''}>Remover robô</button>` : ''}</div>${logs ? `<details class="logs"><summary>Últimos eventos (${recentLogs.length})</summary>${logs}</details>` : ''}</article>`;
}

function escapeHtml(text) { const box = document.createElement('span'); box.textContent = text; return box.innerHTML; }

async function loadMeetings() {
  if (meetingsLoading) return;
  meetingsLoading = true;
  try {
    const meetings = await api('/api/meetings');
    activeBot = meetings.some(meeting => !meeting.recording && active(meeting.status));
    updateSendButton();
    meetingsEl.innerHTML = meetings.length ? meetings.map(card).join('') : '<div class="empty">Ainda não há robôs enviados.</div>';
  } catch (error) { toast(error.message, true); }
  finally { meetingsLoading = false; }
}

function updateSendButton() {
  sendButton.disabled = !runtimeReady || activeBot;
  sendButton.title = !runtimeReady ? 'O ambiente ainda não está pronto.' : activeBot ? 'Remova o robô atual antes de enviar outro.' : '';
}

async function loadReadiness() {
  const box = $('#readiness');
  try {
    const health = await api('/api/health');
    runtimeReady = health.ready;
    const problems = health.checks.filter(check => !check.ok);
    box.className = `readiness ${health.ready ? 'ready' : 'not-ready'}`;
    box.innerHTML = health.ready
      ? '<b>Ambiente pronto</b><span>Chromium, áudio, FFmpeg, persistência e Whisper disponíveis.</span>'
      : `<b>Ambiente incompleto</b><span>${problems.map(item => escapeHtml(item.detail)).join(' ')}</span>`;
  } catch (error) {
    runtimeReady = false;
    box.className = 'readiness not-ready';
    box.innerHTML = `<b>Falha na verificação</b><span>${escapeHtml(error.message)}</span>`;
  }
  updateSendButton();
}

sendForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const formElement = event.currentTarget;
  const form = new FormData(formElement);
  const payload = Object.fromEntries(form.entries());
  payload.consent = form.get('consent') === 'on';
  const button = event.submitter || sendButton;
  button.disabled = true;
  try {
    await api('/api/meetings', { method: 'POST', body: JSON.stringify(payload) });
    formElement.reset();
    activeBot = true;
    toast('Robô enviado. Acompanhe os eventos abaixo.');
    await loadMeetings();
  } catch (error) { toast(error.message, true); }
  finally { updateSendButton(); }
});

meetingsEl.addEventListener('click', async (event) => {
  const id = event.target.dataset.stop;
  if (!id) return;
  event.target.disabled = true;
  try { await api(`/api/meetings/${id}/stop`, { method: 'POST', body: '{}' }); toast('Remoção solicitada.'); loadMeetings(); }
  catch (error) { toast(error.message, true); event.target.disabled = false; }
});

async function loadRecordings() {
  try {
    const recordings = await api('/api/recordings');
    recordingsCache = recordings;
    $('#recording-select').innerHTML = recordings.length ? '<option value="">Selecione uma gravação</option>' + recordings.map(file => `<option value="${escapeHtml(file.path)}">${escapeHtml(file.name)} — ${prettyDate(file.modified_at)}</option>`).join('') : '<option value="">Nenhuma gravação encontrada</option>';
    $('#recording-artifacts').innerHTML = '';
    if (!recordings.length) toast('Nenhum áudio/vídeo foi encontrado na pasta configurada.', true);
  } catch (error) { toast(error.message, true); }
}

$('#recording-select').addEventListener('change', (event) => {
  const recording = recordingsCache.find(file => file.path === event.target.value);
  const artifacts = recording?.artifacts || [];
  $('#recording-artifacts').innerHTML = artifacts.length
    ? `<span>Arquivos prontos:</span>${artifacts.map(file => `<a href="/files/${file.path.split('/').map(encodeURIComponent).join('/')}" download>${escapeHtml(file.type)}</a>`).join('')}`
    : '';
});

$('#load-recordings').addEventListener('click', loadRecordings);
$('#refresh').addEventListener('click', loadMeetings);
$('#transcribe').addEventListener('click', async () => {
  const recording = $('#recording-select').value;
  if (!recording) return toast('Selecione uma gravação primeiro.', true);
  const button = $('#transcribe'); button.disabled = true;
  try { await api('/api/transcriptions', { method: 'POST', body: JSON.stringify({ recording }) }); toast('Transcrição iniciada localmente.'); loadMeetings(); }
  catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});

loadMeetings();
loadReadiness();
setInterval(loadMeetings, 5000);
setInterval(loadReadiness, 60000);
