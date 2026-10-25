"""Voice coach + narrator (ElevenLabs) and Gemini narration/report. All fail-soft.

Everything here is blocking; call it through asyncio.to_thread from the server.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout
from pathlib import Path

log = logging.getLogger("narrator")

HERE = Path(__file__).parent
CACHE = HERE / "audio_cache"
SAMPLE_REPORT = HERE / "sample_report.md"

# key -> text. Fixed clips are cached as <key>.mp3 so they can be committed as offline fallback.
CLIPS = {
    "squeeze_now": "Squeeze... now!",
    "good_timing": "Great timing!",
    "early": "A little early, try waiting a beat.",
    "late": "A bit late, stimulate just before you squeeze.",
    "fatigue_1": "Two more, really squeeze!",
    "fatigue_2": "Let's take a short break.",
    "calibrate": "Calibration: open and close your hand three times.",
    "patient_A": "Patient A: seventy-four years old, stroke in the left motor cortex.",
    "patient_B": "Patient B: forty-five years old, a smaller stroke in the left motor cortex.",
    "fast_forward": "Fast-forwarding ninety days.",
    "recovery_complete": "Recovery complete.",
}

NARRATOR_SYSTEM = ("You are the calm narrator of a stroke-recovery simulator demo. One or two short sentences, "
                   "plain English, no medical advice, refer to it as a simulation.")


def _template_narration(kind: str, data: dict) -> str:
    t = {
        "patient": "A simulated patient has had a stroke. Let's see how their brain can rewire.",
        "day_end": f"Day {data.get('day', '?')} of the simulation is complete.",
        "plan_ready": "The planner has tested therapy plans on copies of this simulated brain.",
        "fatigue": "Effort is dropping in this simulation. A short rest could help.",
        "fast_forward_done": "Ninety simulated days have passed.",
    }
    return t.get(kind, "The simulation continues.")


class Narrator:
    def __init__(self) -> None:
        self.el_key = os.getenv("ELEVENLABS_API_KEY") or ""
        self.voice = os.getenv("ELEVENLABS_VOICE_ID") or ""
        self.el_model = os.getenv("ELEVENLABS_MODEL", "eleven_flash_v2_5")
        self.gem_key = os.getenv("GEMINI_API_KEY") or ""
        self.gem_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        self.now_offset_ms = float(os.getenv("SQUEEZE_NOW_OFFSET_MS", "900"))
        self._el = None
        self._gem = None
        self._last_clip_t = -1e9
        self._last_narr_t = -1e9
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=2)
        CACHE.mkdir(parents=True, exist_ok=True)

    # ---------------- ElevenLabs ----------------
    def _client(self):
        if self._el is None and self.el_key:
            from elevenlabs.client import ElevenLabs
            self._el = ElevenLabs(api_key=self.el_key)
        return self._el

    def _tts_to(self, text: str, path: Path) -> bool:
        try:
            c = self._client()
            if c is None or not self.voice:
                return False
            audio = c.text_to_speech.convert(voice_id=self.voice, model_id=self.el_model, text=text,
                                             output_format="mp3_44100_128")
            data = b"".join(audio) if not isinstance(
                audio, (bytes, bytearray)) else bytes(audio)
            if not data:
                return False
            path.write_bytes(data)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("ElevenLabs failed: %s", e)
            return False

    def pregenerate(self) -> None:
        """Generate any missing fixed clips (background thread at startup)."""
        for key, text in CLIPS.items():
            p = CACHE / f"{key}.mp3"
            if p.exists():
                continue
            if not self._tts_to(text, p):
                log.info("no audio for %s (offline or no key)", key)
                if not self.el_key:
                    return

    def clip_url(self, key: str) -> str | None:
        return f"/audio/{key}.mp3" if (CACHE / f"{key}.mp3").exists() else None

    def coach(self, key: str, *, force: bool = False) -> str | None:
        """Rate-limited (>=4 s) clip url, or None."""
        with self._lock:
            now = time.monotonic()
            if not force and now - self._last_clip_t < 4.0:
                return None
            url = self.clip_url(key)
            if url:
                self._last_clip_t = now
            return url

    def speak(self, text: str) -> str | None:
        """Uncached narration TTS (hash name). Returns url or None."""
        h = hashlib.sha1(text.encode()).hexdigest()[:16]
        p = CACHE / f"{h}.mp3"
        if p.exists() or self._tts_to(text, p):
            return f"/audio/{h}.mp3"
        return None

    # ---------------- Gemini ----------------
    def _gemini(self, prompt: str, timeout: float = 8.0) -> str | None:
        if not self.gem_key:
            return None

        def call():
            if self._gem is None:
                from google import genai
                self._gem = genai.Client(api_key=self.gem_key)
            r = self._gem.models.generate_content(
                model=self.gem_model, contents=prompt)
            return (r.text or "").strip()

        try:
            return self._pool.submit(call).result(timeout=timeout) or None
        except FutTimeout:
            log.warning("Gemini timed out")
        except Exception as e:  # noqa: BLE001
            log.warning("Gemini failed: %s", e)
        return None

    def narrate(self, kind: str, data: dict, force: bool = False) -> dict | None:
        """Returns {"text","audio_url"} or None if rate-limited (max 1 per 10 s unless force)."""
        with self._lock:
            now = time.monotonic()
            if not force and now - self._last_narr_t < 10.0:
                return None
            self._last_narr_t = now
        prompt = f"{NARRATOR_SYSTEM}\n\nEvent: {kind}\nData: {json.dumps(data, default=str)[:1500]}"
        text = self._gemini(prompt) or _template_narration(kind, data)
        text = re.sub(r"\s+", " ", text)[:300]
        return {"text": text, "audio_url": self.speak(text)}

    def report(self, info: dict) -> str:
        """Clinician-style markdown report. info must NOT contain hidden traits."""
        prompt = (
            "Write a structured markdown clinician-style report for this SIMULATED stroke patient. "
            "Sections exactly: Summary, Recovery so far, Effort & engagement, Recommended plan & rationale, "
            "Forecast with uncertainty, Limitations. The Limitations section must say this is a simulation "
            "only and not medical advice. Be concise. Do not invent numbers beyond the data.\n\n"
            f"DATA:\n{json.dumps(info, default=str)[:6000]}")
        text = self._gemini(prompt, timeout=20.0)
        if text:
            return text
        if SAMPLE_REPORT.exists():
            return SAMPLE_REPORT.read_text(encoding="utf-8")
        return "# Report unavailable\n\nSimulation only; not medical advice."


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from dotenv import load_dotenv
    load_dotenv()
    n = Narrator()
    n.pregenerate()
    print({k: n.clip_url(k) for k in CLIPS})
    print(n.narrate("patient", {"card": "B"}))
