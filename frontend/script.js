// =============================================================================
//  TRADUTOR NEURAL BIDIRECIONAL — script.js
//  Integração completa com o backend FastAPI + WebSocket
//
//  Protocolo backend (websocket_handler.py):
//    ENVIA  bytes PCM 16kHz 16-bit mono     → chunks de áudio
//    ENVIA  JSON { type: "end_of_speech" }  → sinaliza fim de fala
//    ENVIA  JSON { type: "ping" }           → keepalive
//
//    RECEBE JSON { type: "status", message: "processing" }
//    RECEBE JSON { type: "status", message: "no_speech_detected" }
//    RECEBE JSON { type: "transcript", original, translated,
//                  lang_from, lang_to, lang_pair }
//    RECEBE bytes PCM 16kHz 16-bit mono     → áudio traduzido para reproduzir
// =============================================================================

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

// ── Estado global ─────────────────────────────────────────────────────────────
let ws           = null;
let audioContext = null;
let processor    = null;
let input        = null;
let stream       = null;
let isRecording  = false;
let activePerson = null;   // 'a' ou 'b'
let knownLangs   = [];     // par de idiomas detectados na sessão
let pingInterval = null;   // keepalive

// URL automática: local → ws://, nuvem → wss://
const WS_URL = location.hostname === 'localhost'
    ? 'ws://localhost:8000/ws/translate'
    : `wss://${location.host}/ws/translate`;

// =============================================================================
//  MÁQUINA DE ESTADOS VISUAIS
// =============================================================================
function setState(state) {
    // Limpa tudo
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
        'processando': () => {
            ledProc.classList.add('on');
            statusEl.textContent = 'Traduzindo...';
        },
        'falando': () => {
            ledSpeak.classList.add('on');
            statusEl.textContent = 'Reproduzindo tradução...';
        },
        'conectando': () => {
            statusEl.textContent = 'Conectando...';
        },
        'erro': () => {
            statusEl.textContent = '⚠️ Erro — recarregue a página';
        },
        'idle': () => {
            statusEl.textContent = 'Aguardando...';
        },
    };

    (map[state] || map['idle'])();
}

// =============================================================================
//  DISPLAY DE IDIOMAS DETECTADOS
// =============================================================================
function updateLangDisplay(langFrom, langTo, langPair) {
    // Atualiza par conhecido
    if (langPair && langPair.length >= 2) {
        knownLangs = langPair;
        langAEl.textContent = knownLangs[0].toUpperCase();
        langBEl.textContent = knownLangs[1].toUpperCase();
    } else if (langPair && langPair.length === 1) {
        langAEl.textContent = langPair[0].toUpperCase();
        langBEl.textContent = '?';
    }

    // Remove classes de destaque anteriores
    langAEl.classList.remove('active-a', 'active-b');
    langBEl.classList.remove('active-a', 'active-b');

    // Destaca qual idioma está falando agora
    if (knownLangs.length >= 2) {
        if (langFrom === knownLangs[0]) {
            langAEl.classList.add('active-a');
            langBEl.classList.add('active-b');
        } else {
            langAEl.classList.add('active-b');
            langBEl.classList.add('active-a');
        }
    } else {
        langAEl.classList.add('active-a');
    }
}

// =============================================================================
//  WEBSOCKET — conexão, mensagens, reconexão
// =============================================================================
function connectWS() {
    setState('conectando');
    ws = new WebSocket(WS_URL);
    ws.binaryType = 'blob';

    ws.onopen = () => {
        console.log('✅ WebSocket conectado:', WS_URL);
        setState('idle');

        // Keepalive: envia ping a cada 25s para evitar timeout do Railway
        pingInterval = setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(JSON.stringify({ type: 'ping' }));
            }
        }, 25000);
    };

    ws.onmessage = async (msg) => {
        // ── Mensagem JSON de controle ────────────────────────────────────────
        if (typeof msg.data === 'string') {
            let ctrl;
            try { ctrl = JSON.parse(msg.data); }
            catch (e) { return; }

            if (ctrl.type === 'status') {
                if (ctrl.message === 'processing')        setState('processando');
                if (ctrl.message === 'no_speech_detected') setState('idle');
            }

            if (ctrl.type === 'transcript') {
                // Exibe texto original e traduzido com bandeira de idioma
                txtOriginal.textContent   = `[${(ctrl.lang_from || '?').toUpperCase()}]  ${ctrl.original}`;
                txtTranslated.textContent = `[${(ctrl.lang_to   || '?').toUpperCase()}]  ${ctrl.translated}`;
                updateLangDisplay(ctrl.lang_from, ctrl.lang_to, ctrl.lang_pair);
            }

            return;
        }

        // ── Áudio PCM binário → reproduz no browser ──────────────────────────
        setState('falando');
        try {
            const arrayBuffer = await msg.data.arrayBuffer();
            const pcm         = new Int16Array(arrayBuffer);
            const float32     = new Float32Array(pcm.length);

            // Converte Int16 → Float32 normalizado [-1, 1]
            for (let i = 0; i < pcm.length; i++) {
                float32[i] = pcm[i] / 0x7fff;
            }

            const ctx = new AudioContext({ sampleRate: 16000 });
            const buf = ctx.createBuffer(1, float32.length, 16000);
            buf.copyToChannel(float32, 0);

            const src = ctx.createBufferSource();
            src.buffer = buf;
            src.connect(ctx.destination);
            src.start();
            src.onended = () => setState('idle');
        } catch (e) {
            console.error('Erro ao reproduzir áudio:', e);
            setState('idle');
        }
    };

    ws.onerror = (e) => {
        console.error('WebSocket erro:', e);
        _cleanupAfterDisconnect();
        setState('erro');
    };

    ws.onclose = () => {
        console.log('WebSocket fechado');
        _cleanupAfterDisconnect();
        setState('idle');
    };
}

function _cleanupAfterDisconnect() {
    stopAudio();
    isRecording = false;
    if (pingInterval) { clearInterval(pingInterval); pingInterval = null; }
}

// =============================================================================
//  CAPTURA DE ÁUDIO — microfone → PCM → WebSocket
// =============================================================================
async function startAudio(person) {
    if (isRecording) return;
    isRecording  = true;
    activePerson = person;

    // Garante conexão WebSocket ativa
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        connectWS();
        // Aguarda conexão abrir antes de gravar
        await new Promise((resolve) => {
            const check = setInterval(() => {
                if (ws && ws.readyState === WebSocket.OPEN) {
                    clearInterval(check);
                    resolve();
                }
            }, 100);
        });
    }

    try {
        stream       = await navigator.mediaDevices.getUserMedia({ audio: true });
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
        input        = audioContext.createMediaStreamSource(stream);
        processor    = audioContext.createScriptProcessor(4096, 1, 1);

        processor.onaudioprocess = (e) => {
            if (!isRecording) return;
            const floatData = e.inputBuffer.getChannelData(0);
            const pcm16     = convertToPCM16(resample(floatData, audioContext.sampleRate, 16000));
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(pcm16);
            }
        };

        input.connect(processor);
        processor.connect(audioContext.destination);
        setState(`gravando-${person}`);

    } catch (e) {
        console.error('Erro ao acessar microfone:', e);
        isRecording  = false;
        activePerson = null;
        setState('erro');
        statusEl.textContent = '⚠️ Permita o acesso ao microfone';
    }
}

function stopAudio() {
    if (!isRecording) return;
    isRecording  = false;
    activePerson = null;

    // Para captura
    if (processor) { try { processor.disconnect(); } catch(e){} processor = null; }
    if (input)     { try { input.disconnect();     } catch(e){} input     = null; }
    if (stream)    { stream.getTracks().forEach(t => t.stop()); stream = null; }
    if (audioContext) {
        try { audioContext.close(); } catch(e){}
        audioContext = null;
    }

    // Sinaliza fim de fala ao servidor — dispara pipeline imediatamente
    // sem esperar o timeout de silêncio de 1.5s
    if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'end_of_speech' }));
    }

    setState('processando');
}

// =============================================================================
//  CONVERSÃO DE ÁUDIO
// =============================================================================

/**
 * Converte Float32Array (Web Audio API) para PCM 16-bit little-endian.
 * Formato que o backend (Whisper) espera.
 */
function convertToPCM16(float32) {
    const buffer = new ArrayBuffer(float32.length * 2);
    const view   = new DataView(buffer);
    for (let i = 0; i < float32.length; i++) {
        const clamped = Math.max(-1, Math.min(1, float32[i]));
        view.setInt16(i * 2, clamped * 0x7fff, true);
    }
    return buffer;
}

/**
 * Reamostrage linear: converte sample rate do microfone (ex: 44100 ou 48000)
 * para 16000 Hz que o Whisper exige.
 */
function resample(data, inRate, outRate) {
    if (inRate === outRate) return data;
    const ratio  = inRate / outRate;
    const newLen = Math.round(data.length / ratio);
    const result = new Float32Array(newLen);
    for (let i = 0; i < newLen; i++) {
        result[i] = data[Math.floor(i * ratio)];
    }
    return result;
}

// =============================================================================
//  EVENT LISTENERS DOS BOTÕES
// =============================================================================
function addBtnListeners(btn, person) {
    // Mouse (desktop)
    btn.addEventListener('mousedown', async () => await startAudio(person));
    btn.addEventListener('mouseup',   ()        => stopAudio());
    // Se arrastar o mouse pra fora do botão sem soltar
    btn.addEventListener('mouseleave', () => {
        if (isRecording && activePerson === person) stopAudio();
    });

    // Touch (mobile / tablet)
    btn.addEventListener('touchstart', async (e) => {
        e.preventDefault();
        await startAudio(person);
    });
    btn.addEventListener('touchend', (e) => {
        e.preventDefault();
        stopAudio();
    });
}

addBtnListeners(btnA, 'a');
addBtnListeners(btnB, 'b');

// =============================================================================
//  INICIALIZAÇÃO
// =============================================================================
connectWS();

// Verifica permissão de microfone antecipadamente
navigator.mediaDevices.getUserMedia({ audio: true })
    .then(s => {
        // Libera imediatamente — só queria verificar a permissão
        s.getTracks().forEach(t => t.stop());
    })
    .catch(() => {
        setState('erro');
        statusEl.textContent = '⚠️ Permita o acesso ao microfone nas configurações';
    });