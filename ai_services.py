import io
import os
import wave
import struct
import asyncio
from groq import AsyncGroq
import edge_tts
import miniaudio

# Inicializa o cliente da Groq (Crie sua chave gratuita em console.groq.com)
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
groq_client = AsyncGroq(api_key=GROQ_API_KEY)


# ==========================================
# FUNÇÃO AUXILIAR: DETECÇÃO DE SILÊNCIO
# ==========================================
def has_speech(pcm_data: bytes, threshold: float = 0.01) -> bool:
    """
    Verifica se o áudio contém fala real medindo o volume RMS.

    Evita enviar silêncio ao Whisper, que alucina texto quando
    não detecta fala (ex: retorna 'Legenda Adriana Zanotto').

    Parâmetros:
        pcm_data  — Bytes PCM 16-bit mono
        threshold — Volume mínimo normalizado (0.0 a 1.0).
                    0.01 = 1% do volume máximo. Aumente para
                    ambientes mais ruidosos (0.02, 0.03...).
    Retorno:
        True se há fala detectável, False se for silêncio/ruído.
    """
    if len(pcm_data) < 2:
        return False
    num_samples = len(pcm_data) // 2
    samples = struct.unpack(f'{num_samples}h', pcm_data[:num_samples * 2])
    rms = (sum(s * s for s in samples) / num_samples) ** 0.5
    normalized = rms / 32768.0
    return normalized > threshold


# ==========================================
# FUNÇÃO AUXILIAR: PCM PARA WAV
# ==========================================
def pcm_to_wav(pcm_data, sample_rate=16000):
    """
    Transforma áudio PCM cru em arquivo .wav na memória.
    A API Groq Whisper exige formato de arquivo — não aceita PCM direto.
    """
    wav_io = io.BytesIO()
    with wave.open(wav_io, 'wb') as wav_file:
        wav_file.setnchannels(1)       # Mono
        wav_file.setsampwidth(2)       # 16-bit
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_data)
    wav_io.seek(0)
    wav_io.name = "audio.wav"
    return wav_io


# ==========================================
# 1. STT (SPEECH-TO-TEXT) - API GROQ
# ==========================================
async def transcribe_audio(pcm_data):
    """
    Envia áudio PCM para o Groq Whisper Large v3 e retorna o texto.

    Rejeita silêncio antes de chamar a API para evitar alucinações
    do Whisper (que inventa texto quando não há fala detectável).
    """
    try:
        # Guarda de segurança: não envia silêncio ao Whisper
        if not has_speech(pcm_data):
            print("🔇 Silêncio detectado — ignorando")
            return ""

        print("🎙️ Enviando áudio para transcrição...")
        wav_file = pcm_to_wav(pcm_data)

        transcription = await groq_client.audio.transcriptions.create(
            file=("audio.wav", wav_file.read()),
            model="whisper-large-v3",
            response_format="text",
            language="pt"
        )
        print(f"✅ Texto reconhecido: {transcription}")
        return transcription

    except Exception as e:
        print(f"❌ Erro na transcrição: {e}")
        return ""


# ==========================================
# 2. TRADUÇÃO - API GROQ (LLaMA 3.1)
# ==========================================
async def translate_text(text, target_lang="Inglês"):
    """
    Usa o LLaMA-3.1 da Groq para traduzir o texto.

    Nota: modelo atualizado de llama3-8b-8192 (desativado)
    para llama-3.1-8b-instant (substituto oficial da Groq).
    """
    try:
        print(f"🧠 Traduzindo para {target_lang}...")
        chat_completion = await groq_client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"Você é um tradutor simultâneo. "
                        f"Traduza o texto para {target_lang}. "
                        f"Responda APENAS com a tradução, sem notas, "
                        f"sem aspas e sem explicações."
                    )
                },
                {"role": "user", "content": text}
            ],
            model="llama-3.1-8b-instant",
        )
        traducao = chat_completion.choices[0].message.content
        print(f"✅ Tradução: {traducao}")
        return traducao

    except Exception as e:
        print(f"❌ Erro na tradução: {e}")
        return ""


# ==========================================
# 3. TTS (TEXT-TO-SPEECH) - EDGE TTS
# ==========================================
async def generate_speech(text, voice="en-US-ChristopherNeural"):
    """
    Gera áudio via Microsoft Edge TTS (gratuito, sem chave de API)
    e converte MP3 -> PCM 16kHz mono 16-bit usando miniaudio.

    Não requer FFmpeg — toda conversão acontece em memória.
    """
    try:
        print("🔊 Gerando áudio da tradução...")
        communicate = edge_tts.Communicate(text, voice)

        # Coleta chunks de áudio MP3 da Microsoft
        audio_mp3_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_mp3_data += chunk["data"]

        if not audio_mp3_data:
            print("❌ Edge TTS não retornou áudio")
            return b""

        # Decodifica MP3 -> PCM 16kHz mono 16-bit direto em memória
        decoded = miniaudio.decode(
            audio_mp3_data,
            nchannels=1,
            sample_rate=16000
        )

        print("✅ Áudio PCM 16kHz pronto para envio!")
        return decoded.samples.tobytes()

    except Exception as e:
        print(f"❌ Erro na geração de voz: {e}")
        return b""