import { api } from './api'

/** Bridge to the HOPS desktop app (desktop/src-tauri). In a normal browser everything falls back
 * to web behaviour, so the same build works in both. */

const tauri = (window as any).__TAURI__

export const isApp: boolean = Boolean(tauri?.core?.invoke)

function invoke<T>(command: string, args?: Record<string, unknown>): Promise<T> {
  return tauri.core.invoke(command, args)
}

/** Native folder dialog (desktop app only). Returns null when cancelled. */
export function pickFolder(start?: string): Promise<string | null> {
  return invoke<string | null>('pick_folder', { start: start ?? null })
}

/** Native "save as" dialog (desktop app only). */
export function pickSaveFile(fileName: string): Promise<string | null> {
  return invoke<string | null>('pick_save_file', { fileName })
}

export function openExternal(url: string) {
  const absolute = new URL(url, window.location.href).href
  if (isApp) invoke('open_external', { url: absolute }).catch(() => window.open(absolute, '_blank', 'noopener'))
  else window.open(absolute, '_blank', 'noopener')
}

/** In the app, links that would open a new tab go to the system instead:
 * result files open in their default application, web links in the browser. */
export function installLinkHandler() {
  if (!isApp) return
  document.addEventListener('click', event => {
    const anchor = (event.target as HTMLElement).closest?.('a') as HTMLAnchorElement | null
    if (!anchor || anchor.target !== '_blank') return
    event.preventDefault()
    const url = new URL(anchor.href)
    if (url.origin === window.location.origin && url.pathname === '/api/file') {
      api.post('/reveal', { folder: url.searchParams.get('path') })
    } else {
      openExternal(anchor.href)
    }
  }, true)
}
