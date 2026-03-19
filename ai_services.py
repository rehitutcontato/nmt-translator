import io
import os
import wave
import struct
import asyncio
from groq import AsyncGroq
import edge_tts
import miniaudio

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
groq_client = AsyncGroq(api_key=GROQ_API_KEY)

# Mapa: código ISO → voz Edge TTS mais natural
# Usado pelo TTS automático baseado no idioma detectado
VOICE_MAP = {
    "pt": "pt-BR-FranciscaNeural",
    "en": "en-US-ChristopherNeural",
    "de": "de-DE-ConradNeural",
    "es": "es-ES-AlvaroNeural",
    "fr": "fr-FR-HenriNeural",
    "it": "it-IT-DiegoNeural",
    "ja": "ja-JP-KeitaNeural",
    "zh": "zh-CN-YunxiNeural",
    "ko": "ko-KR-InJoonNeural",
    "ru": "ru-RU-DmitryNeural",
    "ar": "ar-SA-HamedNeural",
    "hi": "hi-IN-MadhurNeural",
    "nl": "nl-NL-MaartenNeural",
    "pl": "pl-PL-MarekNeural",
    "tr": "tr-TR-AhmetNeural",
}

# Mapa: código ISO → nome por extenso para o prompt do LLaMA
LANG_LABEL_MAP = {
    "pt": "Portuguese",
    "en": "English",
    "de": "German",
    "es": "Spanish",
    "fr": "French",
    "it": "Italian",
    "ja": "Japanese",
    "zh": "Chinese",
    "ko": "Korean",
    "ru": "Russian",
    "ar": "Arabic",
    "hi": "Hindi",
    "nl": "Dutch",
    "pl": "Polish",
    "tr": "Turkish",
}


# ==========================================
# FUNÇÃO AUXILIAR: DETECÇÃO DE SILÊNCIO
# ==========================================
def has_speech(pcm_data: bytes, threshold: float = 0.01) -> bool:
    """
    Mede o volume RMS do áudio. Retorna False se for silêncio,
    evitando alucinações do Whisper com áudio vazio.
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
    """Converte PCM cru para WAV em memória. Whisper exige arquivo."""
    wav_io = io.BytesIO()
    with wave.open(wav_io, 'wb') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_data)
    wav_io.seek(0)
    wav_io.name = "audio.wav"
    return wav_io


# ==========================================
# 1. STT — DETECÇÃO AUTOMÁTICA DE IDIOMA
# ==========================================
async def transcribe_audio(pcm_data) -> tuple[str, str]:
    """
    Transcreve áudio PCM e detecta o idioma automaticamente.

    Retorno:
        Tupla (texto, idioma_iso) — ex: ("Olá mundo", "pt")
        Retorna ("", "") se silêncio ou falha.

    MUDANÇA: removido language="pt" fixo. O Whisper agora detecta
    automaticamente e retorna o idioma junto com a transcrição.
    """
    try:
        if not has_speech(pcm_data):
            print("🔇 Silêncio detectado — ignorando")
            return "", ""

        print("🎙️ Transcrevendo + detectando idioma...")
        wav_file = pcm_to_wav(pcm_data)

        # verbose_json retorna o idioma detectado além do texto
        response = await groq_client.audio.transcriptions.create(
            file=("audio.wav", wav_file.read()),
            model="whisper-large-v3",
            response_format="verbose_json",  # ← retorna idioma detectado
        )

        texto = response.text.strip()
        idioma = response.language or "en"  # fallback "en" se não detectar

        print(f"✅ Texto: '{texto}' | Idioma detectado: {idioma}")
        return texto, idioma

    except Exception as e:
        print(f"❌ Erro na transcrição: {e}")
        return "", ""


# ==========================================
# 2. TRADUÇÃO — IDIOMA DESTINO DINÂMICO
# ==========================================
async def translate_text(text: str, target_lang: str = "English") -> str:
    """
    Traduz texto para o idioma destino usando LLaMA-3.1.

    Parâmetros:
        text        — Texto a traduzir
        target_lang — Nome do idioma destino em inglês (ex: "German")
    """
    try:
        print(f"🧠 Traduzindo para {target_lang}...")
        chat_completion = await groq_client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are a translation engine. "
                        f"Translate the following text to {target_lang}. "
                        f"Output ONLY the translated text. "
                        f"Do NOT answer questions, do NOT add explanations, "
                        f"do NOT add notes. Just translate word by word."
                    )
                },
                {"role": "user", "content": text}
            ],
            model="llama-3.1-8b-instant",
        )
        traducao = chat_completion.choices[0].message.content.strip()
        print(f"✅ Tradução: {traducao}")
        return traducao

    except Exception as e:
        print(f"❌ Erro na tradução: {e}")
        return ""


# ==========================================
# 3. TTS — VOZ AUTOMÁTICA POR IDIOMA
# ==========================================
async def generate_speech(text: str, voice: str = "en-US-ChristopherNeural") -> bytes:
    """
    Gera áudio PCM 16kHz via Edge TTS.
    A voz é selecionada automaticamente pelo pipeline com base no idioma destino.
    """
    try:
        print(f"🔊 Gerando áudio | voz: {voice}")
        communicate = edge_tts.Communicate(text, voice)

        audio_mp3_data = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_mp3_data += chunk["data"]

        if not audio_mp3_data:
            print("❌ Edge TTS não retornou áudio")
            return b""

        decoded = miniaudio.decode(
            audio_mp3_data,
            nchannels=1,
            sample_rate=16000
        )

        print("✅ Áudio PCM 16kHz pronto!")
        return decoded.samples.tobytes()

    except Exception as e:
        print(f"❌ Erro no TTS: {e}")
        return b""