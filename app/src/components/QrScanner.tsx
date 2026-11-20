import { useEffect, useRef, useState } from 'react'

interface Detector { detect(src: HTMLVideoElement): Promise<{ rawValue: string }[]> }
type DetectorCtor = new (o: { formats: string[] }) => Detector
export const qrSupported = () => typeof window !== 'undefined' && 'BarcodeDetector' in window && !!navigator.mediaDevices?.getUserMedia

/** Camera QR scan using the browser BarcodeDetector (Chrome/Android). Callers keep a typed fallback. */
export function QrScanner({ onText, onError }: { onText: (t: string) => void; onError: () => void }) {
  const video = useRef<HTMLVideoElement>(null)
  const [live, setLive] = useState(false)
  useEffect(() => {
    let stop = false
    let stream: MediaStream | undefined
    const run = async () => {
      try {
        const D = (window as unknown as { BarcodeDetector: DetectorCtor }).BarcodeDetector
        const det = new D({ formats: ['qr_code'] })
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } })
        if (!video.current) return
        video.current.srcObject = stream
        await video.current.play()
        setLive(true)
        while (!stop) {
          const r = await det.detect(video.current).catch(() => [])
          if (r[0]) { onText(r[0].rawValue); return }
          await new Promise((res) => setTimeout(res, 250))
        }
      } catch { onError() }
    }
    void run()
    return () => { stop = true; stream?.getTracks().forEach((t) => t.stop()) }
  }, [onText, onError])
  return <video ref={video} muted playsInline aria-label="QR camera" className={`w-full rounded-xl bg-black ${live ? '' : 'h-40'}`} />
}
