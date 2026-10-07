import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ReadAloudButton } from './ReadAloudButton'

const { synthesizeSpeechUrl } = vi.hoisted(() => ({ synthesizeSpeechUrl: vi.fn() }))

vi.mock('@/lib/api', () => ({ synthesizeSpeechUrl }))

describe('ReadAloudButton', () => {
  beforeEach(() => {
    synthesizeSpeechUrl.mockReset()
    // jsdom's media elements don't implement real playback -- stub both so
    // `await audio.play()` resolves instead of logging a "not implemented"
    // error, and so clicking "Stop reading" has something real to call.
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(() => Promise.resolve())
    vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
  })

  it('synthesizes speech for the given text and starts playing it on click', async () => {
    synthesizeSpeechUrl.mockResolvedValue('blob:mock-audio')
    render(<ReadAloudButton text="The answer is 42." />)

    await userEvent.click(screen.getByRole('button', { name: 'Read aloud' }))

    await waitFor(() => {
      expect(synthesizeSpeechUrl).toHaveBeenCalledWith('The answer is 42.')
    })
    expect(await screen.findByRole('button', { name: 'Stop reading' })).toBeInTheDocument()
  })

  it('stops playback and returns to idle when clicked again while playing', async () => {
    synthesizeSpeechUrl.mockResolvedValue('blob:mock-audio')
    render(<ReadAloudButton text="Hello" />)

    await userEvent.click(screen.getByRole('button', { name: 'Read aloud' }))
    await screen.findByRole('button', { name: 'Stop reading' })

    await userEvent.click(screen.getByRole('button', { name: 'Stop reading' }))
    expect(screen.getByRole('button', { name: 'Read aloud' })).toBeInTheDocument()
  })

  it('shows a transient error state when synthesis fails, then recovers to idle', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    synthesizeSpeechUrl.mockRejectedValue(new Error('boom'))
    render(<ReadAloudButton text="Hello" />)

    await userEvent.setup({ delay: null }).click(screen.getByRole('button', { name: 'Read aloud' }))

    await vi.waitFor(() => {
      expect(screen.getByRole('button', { name: "Couldn't read this aloud" })).toBeInTheDocument()
    })

    act(() => {
      vi.advanceTimersByTime(2000)
    })
    expect(screen.getByRole('button', { name: 'Read aloud' })).toBeInTheDocument()
    vi.useRealTimers()
  })

  it('works identically for a turn reloaded from history -- it only ever needs the text prop, nothing voice-session-specific', async () => {
    // No `originatedFromVoice`/`spokenAudioUrl` concept reaches this
    // component at all -- a caller passes plain text, same as any other
    // turn. This is the literal fix for "Read aloud" being unavailable
    // once a past conversation is reloaded from server-side history.
    synthesizeSpeechUrl.mockResolvedValue('blob:mock-audio')
    render(<ReadAloudButton text="Found 3 rows." />)

    await userEvent.click(screen.getByRole('button', { name: 'Read aloud' }))

    await waitFor(() => {
      expect(synthesizeSpeechUrl).toHaveBeenCalledWith('Found 3 rows.')
    })
  })
})
