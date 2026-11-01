import QRCode from "qrcode";
import { useEffect, useState } from "react";

/**
 * QR code rendered locally to an SVG string (no network). Always dark-on-white,
 * because many phone scanners can't read inverted codes in dark mode.
 */
/** QR colours come from the `--qr-ink` / `--qr-paper` tokens (always dark on light). */
function qrColors(): { dark: string; light: string } {
  const cs = getComputedStyle(document.documentElement);
  const hex = (name: string, fallback: string) => {
    const v = cs.getPropertyValue(name).trim();
    return /^#[0-9a-f]{6}$/i.test(v) ? v : fallback;
  };
  return { dark: hex("--qr-ink", "#0b1020"), light: hex("--qr-paper", "#ffffff") };
}

export function QrCode({ text, label, size = 168 }: { text: string; label: string; size?: number }) {
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let alive = true;
    setSvg(null);
    setFailed(false);
    QRCode.toString(text, {
      type: "svg",
      errorCorrectionLevel: "M",
      margin: 2,
      color: qrColors(),
    })
      .then((s) => {
        if (alive) setSvg(s);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [text]);

  return (
    <div className="qr" role="img" aria-label={label} style={{ width: size, height: size }}>
      {svg ? (
        // The markup comes from the qrcode library (paths only), never from the server.
        <div className="qr-svg" dangerouslySetInnerHTML={{ __html: svg }} />
      ) : failed ? (
        <span className="qr-fail small">Couldn't draw the code. Add the topic by hand.</span>
      ) : (
        <span className="skeleton qr-skel" />
      )}
    </div>
  );
}
