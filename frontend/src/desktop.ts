// 桌面能力桥：浏览器环境为 null，桌面环境由 preload 注入受限 IPC。
export interface DesktopResult {
  ok: boolean
  error?: string
}

export interface DesktopBridge {
  openPath(target: string): Promise<DesktopResult>
  showItemInFolder(target: string): Promise<DesktopResult>
}

export function getDesktop(): DesktopBridge | null {
  const w = window as unknown as { desktop?: DesktopBridge }
  return w.desktop ?? null
}
