/**
 * Local thread store — used when backend is in local mode (IDENTITY_ENABLED=false)
 * and the /v1/threads API returns 404 or network errors.
 */
import type { Thread, ThreadPage, MessagePage } from '../types/api'

const LOCAL_THREADS_KEY = 'waypoint.local-threads'

function newId(): string {
  return typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : 'local-' + Date.now() + '-' + Math.random().toString(36).slice(2)
}

function now(): string {
  return new Date().toISOString()
}

function load(): Thread[] {
  try {
    const raw = localStorage.getItem(LOCAL_THREADS_KEY)
    if (raw) return JSON.parse(raw) as Thread[]
  } catch {
    // ignore
  }
  return []
}

function save(threads: Thread[]): void {
  try {
    localStorage.setItem(LOCAL_THREADS_KEY, JSON.stringify(threads))
  } catch {
    // ignore
  }
}

export const localThreadStore = {
  list(): ThreadPage {
    return { items: load().slice().reverse(), next_cursor: null }
  },

  create(title?: string | null): Thread {
    const thread: Thread = {
      id: newId(),
      user_id: 'local',
      title: title ?? null,
      locale: 'vi',
      archived: false,
      summary: null,
      summary_version: 0,
      summarized_through_sequence: 0,
      created_at: now(),
      updated_at: now(),
    }
    const threads = load()
    threads.push(thread)
    save(threads)
    return thread
  },

  get(threadId: string): Thread | null {
    return load().find((t) => t.id === threadId) ?? null
  },

  rename(threadId: string, title: string): Thread | null {
    const threads = load()
    const thread = threads.find((t) => t.id === threadId)
    if (!thread) return null
    thread.title = title
    thread.updated_at = now()
    save(threads)
    return thread
  },

  delete(threadId: string): void {
    const threads = load().filter((t) => t.id !== threadId)
    save(threads)
    // Also clear local messages for this thread
    try {
      localStorage.removeItem('waypoint.local-messages.' + threadId)
    } catch {
      // ignore
    }
  },

  listMessages(threadId: string): MessagePage {
    try {
      const raw = localStorage.getItem('waypoint.local-messages.' + threadId)
      if (raw) return JSON.parse(raw) as MessagePage
    } catch {
      // ignore
    }
    return { items: [], next_cursor: null }
  },
}
