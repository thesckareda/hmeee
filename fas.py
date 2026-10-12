from uuid import uuid4
from soniox import SonioxClient
from soniox.types import RealtimeTTSConfig

client = SonioxClient()
config = RealtimeTTSConfig(
    stream_id=f"tts-{uuid4()}",
    model="tts-rt-v1",
    language="en",
    voice="Adrian",
    audio_format="wav",
)

audio_chunks: list[bytes] = []
with client.realtime.tts.connect(config=config) as session:
    session.send_text_chunks(["Hello from Soniox real-time TTS."], terminate=True)
    for chunk in session.receive_audio_chunks():
        audio_chunks.append(chunk)

with open("hello-realtime.wav", "wb") as f:
    f.write(b"".join(audio_chunks))