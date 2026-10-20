// Decoy call screen: talk to the chosen decoy persona by voice or text.
let personaList = [], persona = null, callInProgress = false;
let callId = null, transcript = [], recognition = null, sending = false;
let wantMic = false;          // the user has switched the microphone on
let dorothySpeaking = false;  // pause listening while Dorothy talks, so she doesn't hear herself
const $ = id => document.getElementById(id);

// ---------------------------------------------------------------------------
// The decoy's voice: pick a voice matching the persona (female by default, male for Robert).
// Browsers load voices asynchronously; calling getVoices() too early returns an
// empty list and the browser falls back to its default voice (often male).
// ---------------------------------------------------------------------------
const FEMALE = /(female|woman|zira|aria|jenny|michelle|ava|emma|samantha|karen|moira|tessa|fiona|victoria|susan|hazel|libby|sonia|serena|allison|joanna|kendra|kimberly|salli|ivy|catherine|martha|kate|google us english|google uk english female)/i;
const MALE = /(\bmale\b|david|mark|guy|daniel|alex\b|fred|george|ryan|thomas|james|eric|christopher|roger|ralph|brian|andrew|steffan|davis|tony|jason|google uk english male)/i;
let voices = [];

function wantedGender() { return persona && persona.voice_gender === 'male' ? 'male' : 'female'; }

function rankVoice(v) {
  if (!/^en/i.test(v.lang)) return -1;
  const male = wantedGender() === 'male';
  if (male ? FEMALE.test(v.name) : MALE.test(v.name)) return -1;
  let score = 0;
  if ((male ? MALE : FEMALE).test(v.name)) score += 10;
  if (/natural|online|neural|enhanced|premium/i.test(v.name)) score += 3;  // smoother voices
  if (/en[-_](US|GB)/i.test(v.lang)) score += 1;
  return score;
}

function loadVoices() {
  voices = window.speechSynthesis ? window.speechSynthesis.getVoices() : [];
  const select = $('voiceSelect');
  if (!select || !voices.length) return;
  const english = voices.filter(v => /^en/i.test(v.lang));
  const sorted = english.slice().sort((a, b) => rankVoice(b) - rankVoice(a));
  let saved = null;
  try { saved = localStorage.getItem('decoyVoice_' + wantedGender()); } catch (e) {}
  select.innerHTML = sorted.map(v =>
    `<option value="${esc(v.name)}">${esc(v.name)}${rankVoice(v) >= 10 ? ' ★' : ''}</option>`).join('');
  const best = sorted.find(v => v.name === saved) || sorted.find(v => rankVoice(v) >= 0) || sorted[0];
  if (best) select.value = best.name;
  if (best && rankVoice(best) < 10) {
    $('voiceHint').textContent = `No ${wantedGender()} voice found on this computer. Pick one from the list, or use Chrome / Edge.`;
  } else {
    $('voiceHint').textContent = '';
  }
}

function dorothyVoice() {
  const name = $('voiceSelect') && $('voiceSelect').value;
  return voices.find(v => v.name === name) || voices.find(v => rankVoice(v) >= 10) || null;
}

function speak(text) {
  if (!('speechSynthesis' in window) || !text) return;
  window.speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  const v = dorothyVoice();
  if (v) { u.voice = v; u.lang = v.lang; }
  const male = wantedGender() === 'male';
  u.rate = male ? 0.95 : 0.82;   // Dorothy speaks slowly; Robert at a normal pace
  u.pitch = male ? 0.9 : 1.05;
  pauseListening();
  dorothySpeaking = true;
  const done = () => { if (!dorothySpeaking) return; dorothySpeaking = false; resumeListening(); };
  u.onend = done;
  u.onerror = done;
  // Safety net: some browsers never fire onend.
  setTimeout(done, 1500 + text.split(/\s+/).length * 550);
  window.speechSynthesis.speak(u);
  setMicStatus();
}

if ('speechSynthesis' in window) {
  loadVoices();
  window.speechSynthesis.onvoiceschanged = loadVoices;
}

function onVoiceChange() {
  try { localStorage.setItem('decoyVoice_' + wantedGender(), $('voiceSelect').value); } catch (e) {}
}

function testVoice() {
  speak(persona ? persona.opening_line : 'Hello?');
}

// ---------------------------------------------------------------------------
// Microphone: keep listening until the user presses Stop.
// Chrome ends speech recognition by itself after a few seconds of silence,
// so we restart it automatically.
// ---------------------------------------------------------------------------
function setMicStatus() {
  const btn = $('micBtn');
  if (!btn) return;
  btn.textContent = wantMic ? 'Stop microphone' : 'Start microphone';
  btn.classList.toggle('mic-on', wantMic);
  if (!wantMic) return;
  $('decoyNotice').textContent = dorothySpeaking ? 'Dorothy is talking…' : (sending ? 'Dorothy is thinking…' : 'Listening… speak as the caller.');
}

function buildRecognition() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return null;
  const r = new SR();
  r.lang = 'en-US';
  r.continuous = true;
  r.interimResults = false;
  r.onresult = e => {
    for (let i = e.resultIndex; i < e.results.length; i++) {
      if (e.results[i].isFinal) {
        const said = e.results[i][0].transcript.trim();
        if (said) sendMessage(said);
      }
    }
  };
  r.onerror = e => {
    if (e.error === 'not-allowed' || e.error === 'service-not-allowed' || e.error === 'audio-capture') {
      wantMic = false;
      $('decoyNotice').textContent = 'Microphone permission was blocked. Allow the microphone in the address bar, or type instead.';
      setMicStatus();
    }
    // 'no-speech', 'aborted' and 'network' are normal; onend restarts listening.
  };
  r.onend = () => {
    r._running = false;
    if (wantMic && !dorothySpeaking) setTimeout(resumeListening, 250);
  };
  return r;
}

function resumeListening() {
  if (!wantMic || dorothySpeaking || !recognition || recognition._running) { setMicStatus(); return; }
  try { recognition.start(); recognition._running = true; } catch (e) { /* already started */ }
  setMicStatus();
}

function pauseListening() {
  if (recognition && recognition._running) {
    try { recognition.abort(); } catch (e) {}
    recognition._running = false;
  }
}

function toggleMic() {
  if (wantMic) {
    wantMic = false;
    pauseListening();
    setMicStatus();
    $('decoyNotice').textContent = 'Microphone off. You can keep typing.';
    return;
  }
  if (!recognition) recognition = buildRecognition();
  if (!recognition) {
    $('decoyNotice').textContent = 'Speech recognition is not available in this browser. Use Chrome or Edge, or type instead.';
    return;
  }
  wantMic = true;
  resumeListening();
}

// ---------------------------------------------------------------------------
// Call flow
// ---------------------------------------------------------------------------
function renderTranscript() {
  const el = $('transcript');
  el.innerHTML = transcript.map(m =>
    `<div class="bubble ${m.speaker === 'Dorothy' ? 'dorothy' : 'caller'}"><strong>${esc(m.name || m.speaker)}</strong><p>${esc(m.text)}</p></div>`).join('');
  el.scrollTop = el.scrollHeight;
}

async function startCall() {
  try {
    const r = await fetch('/api/decoy/start', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ persona_id: persona ? persona.id : 'dorothy' })
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Request failed');
    callId = d.call_id;
    setCallInProgress(true);
    transcript = d.transcript || [];
    renderTranscript();
    $('callStatus').textContent = 'Call in progress';
    $('startBtn').disabled = true;
    $('endBtn').disabled = false;
    $('micBtn').disabled = false;
    $('decoyNotice').textContent = 'Call started. Press "Start microphone" and speak as the caller, or type below.';
    $('extractionPanel').classList.add('hidden');
    if (d.greeting) speak(d.greeting);
  } catch (e) {
    $('decoyNotice').textContent = 'Could not start call. Refresh and try again.';
  }
}

async function sendMessage(text) {
  if (!callId || sending || !text.trim()) return;
  sending = true;
  setMicStatus();
  $('messageInput').value = '';
  transcript.push({ speaker: 'Caller', text });
  renderTranscript();
  try {
    const r = await fetch('/api/decoy/message', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ call_id: callId, text })
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Request failed');
    transcript = d.transcript;
    renderTranscript();
    sending = false;
    speak(d.reply);
  } catch (e) {
    $('decoyNotice').textContent = 'Message failed. You can keep typing or load the sample call.';
  } finally {
    sending = false;
    setMicStatus();
  }
}

$('messageForm').addEventListener('submit', e => { e.preventDefault(); sendMessage($('messageInput').value); });

async function endCall() {
  if (!callId) return;
  wantMic = false;
  pauseListening();
  if (window.speechSynthesis) window.speechSynthesis.cancel();
  setMicStatus();
  try {
    const r = await fetch('/api/decoy/end/' + encodeURIComponent(callId), { method: 'POST' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail);
    transcript = d.transcript;
    renderTranscript();
    $('extractionResult').textContent = JSON.stringify(d.extraction, null, 2);
    $('extractionPanel').classList.remove('hidden');
    $('callStatus').textContent = 'Call ended · extracted';
    $('decoyNotice').textContent = d.message;
    $('startBtn').disabled = false;
    $('endBtn').disabled = true;
    $('micBtn').disabled = true;
    setCallInProgress(false);
  } catch (e) {
    $('decoyNotice').textContent = 'Could not end call cleanly. Try again.';
  }
}

async function loadSample() {
  try {
    const r = await fetch('/api/decoy/sample', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ persona_id: persona ? persona.id : 'dorothy' })
    });
    const d = await r.json();
    callId = d.call_id;
    transcript = d.transcript;
    renderTranscript();
    $('extractionResult').textContent = JSON.stringify(d.extraction, null, 2);
    $('extractionPanel').classList.remove('hidden');
    $('callStatus').textContent = 'Sample call loaded';
    $('decoyNotice').textContent = d.message;
    $('startBtn').disabled = false;
    $('endBtn').disabled = true;
    $('micBtn').disabled = true;
  } catch (e) {
    $('decoyNotice').textContent = 'Could not load sample call. Refresh and try again.';
  }
}

// ---------------------------------------------------------------------------
// Persona selector (locked while a call is in progress)
// ---------------------------------------------------------------------------
function renderPersonaCards() {
  $('personaCards').innerHTML = personaList.map(p => `
    <button type="button" class="persona-card${persona && p.id === persona.id ? ' selected' : ''}" role="radio"
            aria-checked="${persona && p.id === persona.id}" data-id="${esc(p.id)}" ${callInProgress ? 'disabled' : ''}>
      <strong>${esc(p.name)}, ${esc(p.age)}</strong>
      ${esc(p.background)}
      <span class="attracts">Attracts: ${esc(p.scam_types_attracted.join(', '))}</span>
    </button>`).join('');
  document.querySelectorAll('.persona-card').forEach(b => b.addEventListener('click', () => selectPersona(b.dataset.id)));
}

function selectPersona(id) {
  if (callInProgress) return;
  persona = personaList.find(p => p.id === id) || persona;
  $('heroTitle').textContent = 'Talk to ' + persona.name;
  $('voiceLabel').textContent = persona.name.split(' ')[0] + "'s voice";
  renderPersonaCards();
  loadVoices();  // re-rank voices for the persona's gender
}

function setCallInProgress(on) {
  callInProgress = on;
  renderPersonaCards();
  $('personaLock').classList.toggle('hidden', !on);
}

async function loadPersonas() {
  try {
    const d = await (await fetch('/api/personas')).json();
    personaList = d.personas;
    persona = personaList.find(p => p.id === d.default) || personaList[0];
    selectPersona(persona.id);
  } catch (e) {
    $('personaCards').innerHTML = '<p class="muted">Could not load personas. Refresh the page. Dorothy will be used.</p>';
  }
}
loadPersonas();
