import { Loader2, Volume2, VolumeX } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '@/components/ui/button'
import { synthesizeSpeechUrl } from '@/lib/api'
import { cn } from '@/lib/utils'

/** Manual "read this answer aloud" control -- independent of the
 * *automatic* hands-free playback `useVoiceConversation`/`chatStore`
 * already wire up for a voice-*originated* turn (see `TurnCard.tsx`'s
 * own `entry.spokenAudioUrl` replay `<audio>`). That automatic path only
 * ever fires once, right after a question asked by voice succeeds, in
 * the same live session it was asked in -- never for a typed question,
 * and never for a turn reloaded from server-side chat history
 * (`spokenAudioUrl` is session-only state, never persisted; see
 * CLAUDE.md's "Universal server-side chat history" known limitations).
 *
 * This button calls the exact same `/voice/synthesize` endpoint
 * on demand, from nothing but the answer text already rendered on
 * screen -- which is why it works identically for a live turn and for
 * one reloaded from a past conversation: both already have that text.
 *
 * Click to speak; click again while loading or playing to stop. A fresh
 * request is synthesized every click (no caching) -- this is a
 * low-frequency, explicitly user-initiated action, not the hands-free
 * loop's own latency-sensitive path. */
export function ReadAloudButton({ text }: { text: string }) {
  const { t } = useTranslation()
  const [status, setStatus] = useState<'idle' | 'loading' | 'playing' | 'error'>('idle')
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const urlRef = useRef<string | null>(null)

  const cleanup = () => {
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current.onended = null
      audioRef.current = null
    }
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current)
      urlRef.current = null
    }
  }

  // Stops playback and releases the blob URL if the turn unmounts (e.g.
  // switching conversations) mid-read -- never leaves audio playing for a
  // turn that's no longer on screen.
  useEffect(() => cleanup, [])

  const handleClick = async () => {
    if (status === 'loading' || status === 'playing') {
      cleanup()
      setStatus('idle')
      return
    }
    setStatus('loading')
    try {
      const url = await synthesizeSpeechUrl(text)
      cleanup()
      urlRef.current = url
      const audio = new Audio(url)
      audio.onended = () => {
        cleanup()
        setStatus('idle')
      }
      audioRef.current = audio
      await audio.play()
      setStatus('playing')
    } catch {
      cleanup()
      setStatus('error')
      window.setTimeout(() => setStatus('idle'), 2000)
    }
  }

  const isBusy = status === 'loading' || status === 'playing'
  const label = status === 'error' ? t('common.readAloudFailed') : isBusy ? t('common.stopReading') : t('common.readAloud')

  return (
    <Button size="sm" variant="ghost" onClick={() => void handleClick()} aria-label={label}>
      {status === 'loading' ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      ) : status === 'playing' ? (
        <VolumeX className="h-3.5 w-3.5" />
      ) : (
        <Volume2 className={cn('h-3.5 w-3.5', status === 'error' && 'text-[var(--danger)]')} />
      )}
      {label}
    </Button>
  )
}
