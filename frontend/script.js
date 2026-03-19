// =============================================================================
//  TRADUTOR NEURAL BIDIRECIONAL — script.js
// =============================================================================

// ── Lista de idiomas disponíveis ──────────────────────────────────────────────
// Sincronizado com VOICE_MAP e LANG_LABEL_MAP do ai_services.py
const LANGUAGES = [
    { code: 'pt', label: 'Português', flag: '🇧🇷' },
    { code: 'en', label: 'English',   flag: '🇺🇸' },
    { code: 'de', label: 'Deutsch',   flag: '🇩🇪' },
    { code: 'es', label: 'Español',   flag: '🇪🇸' },
    { code: 'fr', label: 'Français',  flag: '🇫🇷' },
    { code: 'it', label: 'Italiano',  flag: '🇮🇹' },
    { code: 'ja', label: '日本語',    flag: '🇯🇵' },
    { code: 'zh', label: '中文',      flag: '🇨🇳' },
    { code: 'ko', label: '한국어',    flag: '🇰🇷' },
    { code: 'ru', label: 'Русский',   flag: '🇷🇺' },
    { code: 'ar', label: 'العربية',   flag: '🇸🇦' },
    { code: 'hi', label: 'हिन्दी',   flag: '🇮🇳' },
    { code: 'nl', label: 'Nederlands',flag: '🇳🇱' },
    { code: 'pl', label: 'Polski',    flag: '🇵🇱' },
    { code: 'tr', label: 'Türkçe',    flag: '🇹🇷' },
];

// ── Elementos do DOM ──────────────────────────────────────────────────────────
const statusEl      = document.getElementById('status');
const ledRec        = document.getElementById('led-rec');
const ledProc       = document.getElementById('led-proc');
const ledSpeak      = document.getElementById('led-speak');
const txtOriginal   = document.getElementById('txt-original');
const txtTranslated = document.getElementById('txt-translated');
const langAEl       = document.getElementById('lang-a');
const langBEl       = document.getElementById('lang-b');
const btnA          = document.getElementById('btn-a');
const btnB          = document.getElementById('btn-b');
const pulseA        = document.getElementById('pulse-a');
const pulseB        = document.getElementById('pulse-b');

// Painel lateral
const settingsBtn  = document.getElementById('settings-btn');
const sidePanel    = document.getElementById('side-panel');
const panelOverlay = document.getElementById('panel-overlay');
const panelClose   = document.getElementById('panel-close');
const selectA      = document.getElementById('select-a');
const selectB      = document.getElementById('select-b');
const pairStatus   = document.getElementById('pair-status');
const applyBtn     = document.getElementById('apply-btn');
const detectResultA = document.getElementById('detect-result-a');
const detectResultB = document.getElementById('detect-result-b');

// ── Estado global ─────────────────────────────────────────────────────────────
let ws           = null;
let audioContext = null;
let processor    = null;
let input        = null;
let stream       = null;
let isRecording  = false;
let activePerson = null;
let pingInterval = null;

// Par de idiomas da sessão — pode vir da detecção automática OU da seleção manual
// { a: 'pt', b: 'de' } — null = ainda não configurado
let langPair = { a: null, b: null };

// URL automática: local → ws://, nuvem → wss://
const WS_URL = location.hostname === 'localhost'
    ? 'ws://localhost:8000/ws/translate'
    : `wss://${location.host}/ws/translate`;

// =============================================================================
//  PAINEL LATERAL — inicialização e controles
// =============================================================================

// Popula os selects com todos os idiomas disponíveis
function populateSelects() {
    LANGUAGES.forEach(lang => {
        [selectA, selectB].forEach(sel => {
            const opt = document.createElement('option');
            opt.value = lang.code;
            opt.textContent = `${lang.flag}  ${lang.label}`;
            sel.appendChild(opt);
        });
    });
}

// Abre/fecha o painel
function openPanel()  {
    sidePanel.classList.add('open');
    panelOverlay.classList.add('open');
}
function closePanel() {
    sidePanel.classList.remove('open');
    panelOverlay.classList.remove('open');
    // Para qualquer detecção em andamento ao fechar
    if (isRecording) stopAudio();
}

settingsBtn.addEventListener('click', openPanel);
panelClose.addEventListener('click',  closePanel);
panelOverlay.addEventListener('click', closePanel);

// Atualiza o status do par no painel
function updatePairStatus() {
    const a = langPair.a ? LANGUAGES.find(l => l.code === langPair.a) : null;
    const b = langPair.b ? LANGUAGES.find(l => l.code === langPair.b) : null;

    if (a && b) {
        pairStatus.textContent = `${a.flag} ${a.label}  ⇄  ${b.flag} ${b.label}`;
        pairStatus.classList.add('ready');
    } else if (a) {
        pairStatus.textContent = `${a.flag} ${a.label}  ⇄  ? (aguardando Pessoa B)`;
        pairStatus.classList.remove('ready');
    } else {
        pairStatus.textContent = 'Par de idiomas não configurado';
        pairStatus.classList.remove('ready');
    }
}

// Aplica seleção manual e fecha painel
applyBtn.addEventListener('click', () => {
    const valA = selectA.value;
    const valB = selectB.value;

    if (valA) langPair.a = valA;
    if (valB) langPair.b = valB;

    updatePairStatus();
    updateMainLangDisplay();
    closePanel();

    // Se par completo, atualiza status principal
    if (langPair.a && langPair.b) {
        const a = LANGUAGES.find(l => l.code === langPair.a);
        const b = LANGUAGES.find(l => l.code === langPair.b);
        statusEl.textContent = `Par configurado: ${a.flag} ⇄ ${b.flag}`;
        setTimeout(() => setState('idle'), 2000);
    }
});

// Sincroniza select → langPair em tempo real (feedback imediato)
selectA.addEventListener('change', () => {
    if (selectA.value) {
        langPair.a = selectA.value;
        setDetectState('a', 'detected', selectA.value);
        updatePairStatus();
    }
});
selectB.addEventListener('change', () => {
    if (selectB.value) {
        langPair.b = selectB.value;
        setDetectState('b', 'detected', selectB.value);
        updatePairStatus();
    }
});

// =============================================================================
//  DETECÇÃO DE IDIOMA NO PAINEL (botão "falar para detectar")
// =============================================================================

// Atualiza resultado visual da detecção no painel
function setDetectState(person, state, detectedCode = null) {
    const resultEl  = person === 'a' ? detectResultA : detectResultB;
    const sel       = person === 'a' ? selectA        : selectB;
    const startBtn  = document.getElementById('detect-start-' + person);
    const stopBtn   = document.getElementById('detect-stop-'  + person);

    if (state === 'idle') {
        resultEl.textContent = '—';
        resultEl.classList.remove('found');
        startBtn.disabled = false;
        startBtn.classList.remove('detecting');
        stopBtn.disabled  = true;
    } else if (state === 'detecting') {
        resultEl.textContent = 'Ouvindo...';
        resultEl.classList.remove('found');
    } else if (state === 'detected' && detectedCode) {
        const lang = LANGUAGES.find(l => l.code === detectedCode);
        resultEl.textContent = lang ? `${lang.flag}  ${lang.label} detectado` : detectedCode.toUpperCase();
        resultEl.classList.add('found');
        // Sincroniza o select e reseta botões
        sel.value = detectedCode;
        startBtn.disabled = false;
        startBtn.classList.remove('detecting');
        stopBtn.disabled  = true;
    }
}

// Captura rápida para detecção de idioma no painel
let detectStream = null, detectContext = null, detectProcessor = null;
let detectingPerson = null;

async function startDetection(person) {
    if (isRecording) return;
    detectingPerson = person;
    isRecording = true;
    setDetectState(person, 'detecting');

    // Garante WebSocket para o backend detectar o idioma via Whisper
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        connectWS();
        await waitForWS();
    }

    try {
        detectStream    = await navigator.mediaDevices.getUserMedia({ audio: true });
        detectContext   = new (window.AudioContext || window.webkitAudioContext)();
        const src       = detectContext.createMediaStreamSource(detectStream);
        detectProcessor = detectContext.createScriptProcessor(4096, 1, 1);

        detectProcessor.onaudioprocess = (e) => {
            if (!isRecording) return;
            const pcm16 = convertToPCM16(resample(e.inputBuffer.getChannelData(0), detectContext.sampleRate, 16000));
            if (ws && ws.readyState === WebSocket.OPEN) ws.send(pcm16);
        };

        src.connect(detectProcessor);
        detectProcessor.connect(detectContext.destination);
    } catch(e) {
        isRecording = false;
        detectingPerson = null;
        setDetectState(person, 'idle');
    }
}

function stopDetection() {
    if (!isRecording || !detectingPerson) return;
    isRecording = false;

    if (detectProcessor) { try { detectProcessor.disconnect(); } catch(e){} detectProcessor = null; }
    if (detectStream)    { detectStream.getTracks().forEach(t => t.stop()); detectStream = null; }
    if (detectContext)   { try { detectContext.close(); } catch(e){} detectContext = null; }

    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'end_of_speech' }));
    }
    // O idioma detectado chegará via ws.onmessage → transcript.lang_from
}

// Eventos dos botões de detecção — iniciar e parar separados
['a', 'b'].forEach(p => {
    const startBtn  = document.getElementById('detect-start-' + p);
    const stopBtn   = document.getElementById('detect-stop-'  + p);

    startBtn.addEventListener('click', async () => {
        if (isRecording) return;
        startBtn.disabled = true;
        startBtn.classList.add('detecting');
        stopBtn.disabled  = false;
        await startDetection(p);
    });

    stopBtn.addEventListener('click', () => {
        stopDetection();
        startBtn.disabled = false;
        startBtn.classList.remove('detecting');
        stopBtn.disabled  = true;
    });
});

// =============================================================================
//  MÁQUINA DE ESTADOS VISUAIS
// =============================================================================
function setState(state) {
    [ledRec, ledProc, ledSpeak].forEach(l => l.classList.remove('on'));
    [btnA, btnB].forEach(b => b.classList.remove('active-press'));
    if (pulseA) pulseA.classList.remove('pulsing');
    if (pulseB) pulseB.classList.remove('pulsing');

    const map = {
        'gravando-a': () => {
            ledRec.classList.add('on');
            btnA.classList.add('active-press');
            if (pulseA) pulseA.classList.add('pulsing');
            statusEl.textContent = 'Ouvindo Pessoa A...';
        },
        'gravando-b': () => {
            ledRec.classList.add('on');
            btnB.classList.add('active-press');
            if (pulseB) pulseB.classList.add('pulsing');
            statusEl.textContent = 'Ouvindo Pessoa B...';
        },
        'processando': () => { ledProc.classList.add('on'); statusEl.textContent = 'Traduzindo...'; },
        'falando':     () => { ledSpeak.classList.add('on'); statusEl.textContent = 'Reproduzindo tradução...'; },
        'conectando':  () => { statusEl.textContent = 'Conectando...'; },
        'erro':        () => { statusEl.textContent = '⚠️ Erro — recarregue a página'; },
        'idle':        () => { statusEl.textContent = 'Aguardando...'; },
    };
    (map[state] || map['idle'])();
}

// =============================================================================
//  DISPLAY DE IDIOMAS NO APP PRINCIPAL
// =============================================================================
function updateMainLangDisplay(langFrom, langTo, serverPair) {
    // Se o servidor mandou um par detectado, atualiza o langPair local
    if (serverPair && serverPair.length >= 2) {
        // Só atualiza se não houver configuração manual
        if (!langPair.a) langPair.a = serverPair[0];
        if (!langPair.b) langPair.b = serverPair[1];
        updatePairStatus();
    }

    const a = langPair.a || (serverPair && serverPair[0]) || null;
    const b = langPair.b || (serverPair && serverPair[1]) || null;

    langAEl.textContent = a ? a.toUpperCase() : '?';
    langBEl.textContent = b ? b.toUpperCase() : '?';

    // Destaque de qual está falando
    langAEl.classList.remove('active-a', 'active-b');
    langBEl.classList.remove('active-a', 'active-b');

    if (langFrom && a && b) {
        if (langFrom === a) { langAEl.classList.add('active-a'); langBEl.classList.add('active-b'); }
        else                { langAEl.classList.add('active-b'); langBEl.classList.add('active-a'); }
    }
}

// =============================================================================
//  WEBSOCKET
// =============================================================================
function connectWS() {
    setState('conectando');
    ws = new WebSocket(WS_URL);
    ws.binaryType = 'blob';

    ws.onopen = () => {
        console.log('✅ WebSocket:', WS_URL);
        setState('idle');
        pingInterval = setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'ping' }));
            }
        }, 25000);
    };

    ws.onmessage = async (msg) => {
        if (typeof msg.data === 'string') {
            let ctrl;
            try { ctrl = JSON.parse(msg.data); } catch(e) { return; }

            if (ctrl.type === 'status') {
                if (ctrl.message === 'processing')         setState('processando');
                if (ctrl.message === 'no_speech_detected') setState('idle');
            }

            // Primeira pessoa falou — aguarda a segunda para completar o par
            if (ctrl.type === 'waiting_pair') {
                const lang = LANGUAGES.find(l => l.code === ctrl.lang_detected);
                const flag = lang ? lang.flag : '🎙️';
                const nome = lang ? lang.label : (ctrl.lang_detected || '?').toUpperCase();
                txtOriginal.textContent   = `${flag}  ${nome} detectado`;
                txtTranslated.textContent = 'Aguardando a outra pessoa falar...';
                setState('idle');
                // Atualiza o display de idiomas com o primeiro idioma
                langAEl.textContent = (ctrl.lang_detected || '?').toUpperCase();
                langBEl.textContent = '?';
                langAEl.classList.add('active-a');
            }

            if (ctrl.type === 'transcript') {
                txtOriginal.textContent   = `[${(ctrl.lang_from||'?').toUpperCase()}]  ${ctrl.original}`;
                txtTranslated.textContent = `[${(ctrl.lang_to  ||'?').toUpperCase()}]  ${ctrl.translated}`;

                // Exibe interpretação contextual (se houver)
                const interpEl = document.getElementById('txt-interpretation');
                if (interpEl) {
                    if (ctrl.interpretation) {
                        interpEl.textContent = '🧠 ' + ctrl.interpretation;
                        interpEl.style.display = 'block';
                    } else {
                        interpEl.style.display = 'none';
                    }
                }

                updateMainLangDisplay(ctrl.lang_from, ctrl.lang_to, ctrl.lang_pair);

                // Se estava detectando no painel, aplica o idioma detectado
                if (detectingPerson) {
                    const detected = ctrl.lang_from;
                    langPair[detectingPerson] = detected;
                    setDetectState(detectingPerson, 'detected', detected);
                    updatePairStatus();
                    detectingPerson = null;
                }
            }
            return;
        }

        // Áudio PCM → reproduz
        setState('falando');
        try {
            const arrayBuffer = await msg.data.arrayBuffer();
            const pcm     = new Int16Array(arrayBuffer);
            const float32 = new Float32Array(pcm.length);
            for (let i = 0; i < pcm.length; i++) float32[i] = pcm[i] / 0x7fff;

            const ctx = new AudioContext({ sampleRate: 16000 });
            const buf = ctx.createBuffer(1, float32.length, 16000);
            buf.copyToChannel(float32, 0);
            const src = ctx.createBufferSource();
            src.buffer = buf;
            src.connect(ctx.destination);
            src.start();
            src.onended = () => setState('idle');
        } catch(e) { setState('idle'); }
    };

    ws.onerror = () => { _cleanupWS(); setState('erro'); };
    ws.onclose = () => { _cleanupWS(); setState('idle'); };
}

function _cleanupWS() {
    if (isRecording) stopAudio();
    isRecording = false;
    if (pingInterval) { clearInterval(pingInterval); pingInterval = null; }
}

function waitForWS() {
    return new Promise(resolve => {
        const check = setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) { clearInterval(check); resolve(); }
        }, 100);
    });
}

// =============================================================================
//  CAPTURA DE ÁUDIO — botões principais
// =============================================================================
async function startAudio(person) {
    if (isRecording) return;
    isRecording  = true;
    activePerson = person;

    if (!ws || ws.readyState !== WebSocket.OPEN) { connectWS(); await waitForWS(); }

    try {
        stream       = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
        input        = audioContext.createMediaStreamSource(stream);
        processor    = audioContext.createScriptProcessor(4096, 1, 1);

        processor.onaudioprocess = (e) => {
            if (!isRecording) return;
            const pcm16 = convertToPCM16(resample(e.inputBuffer.getChannelData(0), audioContext.sampleRate, 16000));
            if (ws && ws.readyState === WebSocket.OPEN) ws.send(pcm16);
        };

        input.connect(processor);
        processor.connect(audioContext.destination);
        setState(`gravando-${person}`);
    } catch(e) {
        isRecording = false; activePerson = null;
        setState('erro');
        statusEl.textContent = '⚠️ Permita o acesso ao microfone';
    }
}

function stopAudio() {
    if (!isRecording) return;
    isRecording = false; activePerson = null;

    if (processor)    { try { processor.disconnect(); }    catch(e){} processor    = null; }
    if (input)        { try { input.disconnect(); }        catch(e){} input        = null; }
    if (stream)       { stream.getTracks().forEach(t => t.stop()); stream = null; }
    if (audioContext) { try { audioContext.close(); }      catch(e){} audioContext = null; }

    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'end_of_speech' }));
    }
    setState('processando');
}

// =============================================================================
//  CONVERSÃO DE ÁUDIO
// =============================================================================
function convertToPCM16(float32) {
    const buffer = new ArrayBuffer(float32.length * 2);
    const view   = new DataView(buffer);
    for (let i = 0; i < float32.length; i++) {
        view.setInt16(i * 2, Math.max(-1, Math.min(1, float32[i])) * 0x7fff, true);
    }
    return buffer;
}

function resample(data, inRate, outRate) {
    if (inRate === outRate) return data;
    const ratio = inRate / outRate;
    const result = new Float32Array(Math.round(data.length / ratio));
    for (let i = 0; i < result.length; i++) result[i] = data[Math.floor(i * ratio)];
    return result;
}

// =============================================================================
//  EVENT LISTENERS DOS BOTÕES PRINCIPAIS
// =============================================================================
function addBtnListeners(btn, person) {
    btn.addEventListener('mousedown',  async () => await startAudio(person));
    btn.addEventListener('mouseup',    ()        => stopAudio());
    btn.addEventListener('mouseleave', ()        => { if (isRecording && activePerson === person) stopAudio(); });
    btn.addEventListener('touchstart', async (e) => { e.preventDefault(); await startAudio(person); });
    btn.addEventListener('touchend',   (e)       => { e.preventDefault(); stopAudio(); });
}

addBtnListeners(btnA, 'a');
addBtnListeners(btnB, 'b');

// =============================================================================
//  INICIALIZAÇÃO
// =============================================================================
populateSelects();
connectWS();
updatePairStatus();

navigator.mediaDevices.getUserMedia({ audio: true })
    .then(s => s.getTracks().forEach(t => t.stop()))
    .catch(() => {
        setState('erro');
        statusEl.textContent = '⚠️ Permita o acesso ao microfone';
    });