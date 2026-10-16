import { useEffect, useRef, useState } from "react";

/**
 * Speaks a simulated call with the browser's built-in voices: no audio leaves the machine and nothing is
 * stored. Agent and patient get different voices; generic system voices only (never a cloned real voice).
 */
export function useSpeech(lang: "en" | "es") {
  const supported = typeof window !== "undefined" && "speechSynthesis" in window;
  const [on, setOn] = useState(false);
  const voices = useRef<SpeechSynthesisVoice[]>([]);

  useEffect(() => {
    if (!supported) return;
    const load = () => { voices.current = window.speechSynthesis.getVoices(); };
    load();
    window.speechSynthesis.addEventListener("voiceschanged", load);
    return () => { window.speechSynthesis.removeEventListener("voiceschanged", load); window.speechSynthesis.cancel(); };
  }, [supported]);

  useEffect(() => { if (!on && supported) window.speechSynthesis.cancel(); }, [on, supported]);

  function voice(index: number) {
    const all = voices.current;
    const pool = all.filter((v) => v.lang.toLowerCase().startsWith(lang));
    const list = pool.length ? pool : all;
    return list.length ? list[Math.min(index, list.length - 1)] : undefined;
  }

  function beep() {
    try {
      const ctx = new AudioContext();
      const osc = ctx.createOscillator();
      osc.frequency.value = 880;
      osc.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + 0.35);
    } catch { /* audio blocked: skip the beep */ }
  }

  function say(text: string, who: "a" | "p") {
    if (!on || !supported) return;
    const clean = text.replace(/\[beep\]/gi, "").replace(/\[[^\]]*\]/g, "").trim();
    if (/\[beep\]/i.test(text)) beep();
    if (!clean) return;
    const u = new SpeechSynthesisUtterance(clean);
    const v = voice(who === "a" ? 0 : 1);
    if (v) u.voice = v;
    u.lang = lang === "es" ? "es-US" : "en-US";
    u.rate = who === "a" ? 1.02 : 0.95;
    u.pitch = who === "a" ? 1.08 : 0.88;
    window.speechSynthesis.speak(u);
  }

  return { supported, on, setOn, say, stop: () => supported && window.speechSynthesis.cancel() };
}
