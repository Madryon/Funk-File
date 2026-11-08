"""
captcha/audio.py
Synthesizes WAV audio challenges in pure Python for users who choose the audio alternative.
Uses acoustic formant and harmonic frequency synthesis to produce clear spoken digit audio.
No external dependencies required.
"""

import io
import math
import struct
import wave
import random

SAMPLE_RATE = 22050

# Approximate vowel formant frequencies (F1, F2, F3) in Hz for English digit sounds
DIGIT_FORMANTS = {
    "0": [(270, 2290), (500, 840)],         # "zero" (ee -> oh)
    "1": [(640, 1190), (300, 1800)],        # "one"  (w-uh -> n)
    "2": [(300, 870)],                      # "two"  (oo)
    "3": [(270, 2290)],                     # "three" (ee)
    "4": [(500, 840)],                      # "four" (or)
    "5": [(660, 1200), (270, 2200)],        # "five" (ah -> ee)
    "6": [(400, 1990)],                     # "six"  (ih)
    "7": [(530, 1840), (450, 1700)],        # "seven" (eh -> eh)
    "8": [(530, 1840)],                     # "eight" (ay)
    "9": [(660, 1200), (270, 2200)],        # "nine" (ah -> ee)
}

# Standard DTMF fallback tones (low_freq, high_freq) for telephone/number clarity
DTMF_FREQUENCIES = {
    "1": (697, 1209),
    "2": (697, 1336),
    "3": (697, 1477),
    "4": (770, 1209),
    "5": (770, 1336),
    "6": (770, 1477),
    "7": (852, 1209),
    "8": (852, 1336),
    "9": (852, 1477),
    "0": (941, 1336),
}


def synthesize_digit_waveform(digit: str, duration: float = 0.45) -> list[int]:
    """Generates audio samples for a single digit with acoustic formants and harmonics."""
    num_samples = int(SAMPLE_RATE * duration)
    samples = []
    f1, f2 = DTMF_FREQUENCIES.get(digit, (600, 1200))
    formants = DIGIT_FORMANTS.get(digit, [(400, 1500)])

    f_base = 130.0  # Base vocal pitch (male/neutral fundamental frequency)

    for i in range(num_samples):
        t = i / SAMPLE_RATE
        # Envelope: smooth attack and decay
        if i < int(SAMPLE_RATE * 0.05):
            env = i / (SAMPLE_RATE * 0.05)
        elif i > num_samples - int(SAMPLE_RATE * 0.08):
            env = (num_samples - i) / (SAMPLE_RATE * 0.08)
        else:
            env = 1.0

        # Fundamental + formants
        val = 0.35 * math.sin(2 * math.pi * f1 * t)
        val += 0.35 * math.sin(2 * math.pi * f2 * t)
        val += 0.20 * math.sin(2 * math.pi * f_base * t)
        for vf1, vf2 in formants:
            val += 0.15 * math.sin(2 * math.pi * vf1 * t)
            val += 0.10 * math.sin(2 * math.pi * vf2 * t)

        # Subtle white noise (comfort noise)
        val += random.uniform(-0.02, 0.02)

        sample = int(32767 * 0.6 * env * val)
        sample = max(-32768, min(32767, sample))
        samples.append(sample)

    return samples


def generate_audio_challenge(code: str) -> bytes:
    """
    Synthesizes a full WAV audio track reading out the digits in `code`
    separated by pauses, with an initial intro chime.
    """
    all_samples = []

    # Intro chime (2 short tones)
    for chime_f in [523.25, 659.25]:  # C5, E5
        chime_samples = int(SAMPLE_RATE * 0.15)
        for i in range(chime_samples):
            t = i / SAMPLE_RATE
            env = math.exp(-6 * t)
            val = int(32767 * 0.4 * env * math.sin(2 * math.pi * chime_f * t))
            all_samples.append(val)
        # Brief pause
        all_samples.extend([0] * int(SAMPLE_RATE * 0.05))

    # Pause before digits
    all_samples.extend([0] * int(SAMPLE_RATE * 0.25))

    # Digits
    for char in str(code):
        if char.isdigit():
            digit_samples = synthesize_digit_waveform(char, duration=0.42)
            all_samples.extend(digit_samples)
            # 0.3s pause between digits
            all_samples.extend([0] * int(SAMPLE_RATE * 0.30))

    # Final small tail
    all_samples.extend([0] * int(SAMPLE_RATE * 0.1))

    # Build WAV bytes
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)        # Mono
        wav.setsampwidth(2)        # 16-bit
        wav.setframerate(SAMPLE_RATE)
        packed = b"".join(struct.pack("<h", s) for s in all_samples)
        wav.writeframes(packed)

    return buf.getvalue()
