const $ = (selector) => document.querySelector(selector);
const meetingsEl = $('#meetings');
const toastEl = $('#toast');

function toast(message, bad = false) {
  toastEl.textContent = message;
  toastEl.className = bad ? 'show bad' : 'show';
  setTimeout(() => { toastEl.className = ''; }, 4200);
}

async function api(path, options = {}) {
  const response = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...options });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || 'Não foi possível concluir a ação.');
  return body;
}

function prettyDate(value) {
  return new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value));
}

function state(status) {
  const labels = { starting: 'Em execução', in_meeting: 'Na reunião', stopping: 'Removendo', removed: 'Removido', finished: 'Finalizado', failed: 'Falhou', interrupted: 'Interrompido', transcribing: 'Transcrevendo', transcribed: 'Transcrito', transcription_failed: 'Falhou ao transcrever' };
  return labels[status] || status;
}

function active(status) { return ['starting', 'in_meeting', 'stopping'].includes(status); }

function card(meeting) {
  const isBot = !meeting.recording;
  const logs = (meeting.logs || []).slice(-3).map(line => `<div>${escapeHtml(line)}</div>`).join('');
  return `<article class="card"><div class="card-top"><div><span class="status ${meeting.status}"></span><strong>${escapeHtml(meeting.name)}</strong><p>${meeting.recording ? escapeHtml(meeting.recording) : escapeHtml(meeting.url)}</p></div><span class="tag">${state(meeting.status)}</span></div><div class="card-foot"><small>${prettyDate(meeting.created_at)}</small>${isBot && active(meeting.status) ? `<button class="remove" data-stop="${meeting.id}" ${meeting.status === 'stopping' ? 'disabled' : ''}>Remover robô</button>` : ''}</div>${logs ? `<details class="logs"><summary>Últimos eventos</summary>${logs}</details>` : ''}</article>`;
}

function escapeHtml(text) { const box = document.createElement('span'); box.textContent = text; return box.innerHTML; }

async function loadMeetings() {
  try {
    const meetings = await api('/api/meetings');
    meetingsEl.innerHTML = meetings.length ? meetings.map(card).join('') : '<div class="empty">Ainda não há robôs enviados.</div>';
  } catch (error) { toast(error.message, true); }
}

$('#send-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const payload = Object.fromEntries(form.entries());
  payload.consent = form.get('consent') === 'on';
  const button = event.submitter;
  button.disabled = true;
  try {
    await api('/api/meetings', { method: 'POST', body: JSON.stringify(payload) });
    event.currentTarget.reset();
    toast('Robô enviado. Acompanhe os eventos abaixo.');
    loadMeetings();
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
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
    $('#recording-select').innerHTML = recordings.length ? '<option value="">Selecione uma gravação</option>' + recordings.map(file => `<option value="${escapeHtml(file.path)}">${escapeHtml(file.name)} — ${prettyDate(file.modified_at)}</option>`).join('') : '<option value="">Nenhuma gravação encontrada</option>';
    if (!recordings.length) toast('Nenhum áudio/vídeo foi encontrado na pasta configurada.', true);
  } catch (error) { toast(error.message, true); }
}

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
setInterval(loadMeetings, 5000);
