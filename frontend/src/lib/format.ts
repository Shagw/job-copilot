import { ApiError } from '../api/client'

/** Readable message from any thrown value. */
export function errorMessage(e: unknown): string {
  if (e instanceof ApiError || e instanceof Error) return e.message
  return 'Something went wrong.'
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function timeLeft(iso: string, now = Date.now()): string {
  const ms = new Date(iso).getTime() - now
  if (ms <= 0) return 'archiving soon'
  const hours = Math.floor(ms / 3_600_000)
  if (hours >= 24) return `${Math.floor(hours / 24)}d ${hours % 24}h left`
  if (hours >= 1) return `${hours}h left`
  return `${Math.max(1, Math.floor(ms / 60_000))}m left`
}

export function scoreClass(score: number): string {
  return score >= 75 ? 'good' : score >= 50 ? 'ok' : 'bad'
}
