import { useEffect, useRef, useState } from "react";

/**
 * Speaks a simulated call with the browser's built-in voices: no audio leaves the machine and nothing is
 * stored. Agent and patient get different voices; generic system voices only (never a cloned real voice).
 * say() resolves when the line has been spoken, so a caller can show the next line only then.
 */
export function useSpeech(lang: "en" | "es") {
  const supported = typeof window !== "undefined" && "speechSynthesis" in window;
  const [on, setOnState] = useState(false);
  const onRef = useRef(false);                       // read by code running across renders (the line queue)
  const voices = useRef<SpeechSynthesisVoice[]>([]);

  useEffect(() => {
    if (!supported) return;
    const load = () => { voices.current = window.speechSynthesis.getVoices(); };
    load();
    window.speechSynthesis.addEventListener("voiceschanged", load);
    return () => { window.speechSynthesis.removeEventListener("voiceschanged", load); window.speechSynthesis.cancel(); };
  }, [supported]);

  function setOn(v: boolean) {
    onRef.current = v;
    setOnState(v);
    if (!v && supported) window.speechSynthesis.cancel();
  }

  function voice(index: number) {
    const all = voices.current;
    const pool = all.filter((v) => v.lang.toLowerCase().startsWith(lang));
    const list = pool.length ? pool : all;
    return list.length ? list[Math.min(index, list.length - 1)] : undefined;
  }

  function beep(): Promise<void> {
    return new Promise((done) => {
      try {
        const ctx = new AudioContext();
        const osc = ctx.createOscillator();
        osc.frequency.value = 880;
        osc.connect(ctx.destination);
        osc.start();
        osc.stop(ctx.currentTime + 0.35);
        osc.onended = () => { ctx.close(); done(); };
      } catch { done(); /* audio blocked: skip the beep */ }
    });
  }

  /** A short hold chime for a transfer to a nurse ("connecting you, please stay on the line"). */
  function jingle(): Promise<void> {
    if (!onRef.current) return Promise.resolve();
    return new Promise((done) => {
      try {
        const ctx = new AudioContext();
        [523.25, 659.25, 783.99, 659.25].forEach((f, i) => {
          const osc = ctx.createOscillator(), gain = ctx.createGain();
          osc.frequency.value = f;
          gain.gain.setValueAtTime(0.0001, ctx.currentTime + i * 0.35);
          gain.gain.exponentialRampToValueAtTime(0.18, ctx.currentTime + i * 0.35 + 0.03);
          gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + i * 0.35 + 0.32);
          osc.connect(gain).connect(ctx.destination);
          osc.start(ctx.currentTime + i * 0.35);
          osc.stop(ctx.currentTime + i * 0.35 + 0.34);
        });
        window.setTimeout(() => { ctx.close(); done(); }, 4 * 350 + 150);
      } catch { done(); /* audio blocked: skip the chime */ }
    });
  }

  /** Speak one line at conversational pace (rate scales it, e.g. the replay speed); resolves when it ends. */
  async function say(text: string, who: "a" | "p", rate = 1): Promise<void> {
    if (!onRef.current || !supported) return;
    if (/\[beep\]/i.test(text)) await beep();
    const clean = text.replace(/\[[^\]]*\]/g, "").trim();
    if (!clean || !onRef.current) return;
    await new Promise<void>((done) => {
      const u = new SpeechSynthesisUtterance(clean);
      const v = voice(who === "a" ? 0 : 1);
      if (v) u.voice = v;
      u.lang = lang === "es" ? "es-US" : "en-US";
      u.rate = Math.min(2, Math.max(0.7, (who === "a" ? 1.15 : 1.1) * rate));
      u.pitch = who === "a" ? 1.08 : 0.88;
      // Some browsers never fire "end" (or drop long lines): don't let one line stall the call.
      const guard = window.setTimeout(done, 2500 + (clean.length * 85) / u.rate);
      u.onend = u.onerror = () => { window.clearTimeout(guard); done(); };
      window.speechSynthesis.speak(u);
    });
  }

  return { supported, on, setOn, onRef, say, jingle, stop: () => supported && window.speechSynthesis.cancel() };
}
