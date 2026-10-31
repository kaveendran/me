import { useState } from "react";
import { api } from "../api";
import { useToast } from "../toast";
import { Linkify, Spinner } from "./ui";

const MOODS: ReadonlyArray<{ v: number; word: string }> = [
  { v: 1, word: "rough" },
  { v: 2, word: "low" },
  { v: 3, word: "okay" },
  { v: 4, word: "good" },
  { v: 5, word: "great" },
];

/** Five-button mood check-in. Any returned `message` (may be support resources) is shown prominently. */
export function MoodCheckin({ onLogged, compact }: { onLogged?: () => void; compact?: boolean }) {
  const [busy, setBusy] = useState<number | null>(null);
  const [picked, setPicked] = useState<number | null>(null);
  const [note, setNote] = useState("");
  const [showNote, setShowNote] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const toast = useToast();

  const send = async (mood: number) => {
    setBusy(mood);
    setError(null);
    setMessage(null);
    try {
      const trimmed = note.trim();
      const res = await api.checkin(trimmed ? { mood, note: trimmed } : { mood });
      setPicked(mood);
      setNote("");
      setShowNote(false);
      if (res.message) setMessage(res.message);
      else toast.show("Check-in saved. Thanks for pausing for a second.", "ok");
      onLogged?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't save the check-in.");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className={`checkin ${compact ? "compact" : ""}`}>
      <p className="checkin-q" id="checkin-q">
        How are you feeling right now?
      </p>
      <div className="mood-row" role="group" aria-labelledby="checkin-q">
        {MOODS.map((m) => (
          <button
            key={m.v}
            type="button"
            className={`mood-btn mood-${m.v} ${picked === m.v ? "is-picked" : ""}`}
            onClick={() => void send(m.v)}
            disabled={busy !== null}
            aria-label={`${m.v} of 5, ${m.word}`}
            aria-pressed={picked === m.v}
          >
            {busy === m.v ? <Spinner size={14} /> : <span className="mood-num">{m.v}</span>}
            <span className="mood-word">{m.word}</span>
          </button>
        ))}
      </div>
      {showNote ? (
        <label className="checkin-note">
          <span className="sr-only">Optional note</span>
          <input
            type="text"
            value={note}
            maxLength={500}
            placeholder="Anything on your mind? (optional, stays private)"
            onChange={(e) => setNote(e.target.value)}
          />
        </label>
      ) : (
        <button type="button" className="link-btn" onClick={() => setShowNote(true)}>
          + add a note
        </button>
      )}
      {error ? (
        <p className="inline-error" role="alert">
          {error}
        </p>
      ) : null}
      {message ? (
        <div className="checkin-message" role="alert">
          <div className="checkin-message-text">
            <Linkify text={message} />
          </div>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setMessage(null)}>
            Close
          </button>
        </div>
      ) : null}
    </div>
  );
}
