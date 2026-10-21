const { app, BrowserWindow, ipcMain, shell } = require('electron')
const path = require('node:path')
const fs = require('node:fs')

const DEV_SERVER_URL = process.env.VITE_DEV_SERVER_URL || 'http://localhost:5173'
const LOAD_DIST = process.env.ELECTRON_LOAD_DIST === '1'

// 白名单根目录：仓库 upload/，可用环境变量覆盖（开发文档 §11.2）。
const UPLOAD_ROOT = process.env.UPLOAD_ROOT || path.resolve(__dirname, '..', '..', 'upload')

function createWindow() {
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  if (LOAD_DIST) {
    win.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))
  } else {
    win.loadURL(DEV_SERVER_URL)
  }
}

// 校验目标路径位于白名单根目录内且真实存在；越界/穿越/跨盘符一律拒绝。
function resolveAllowed(target) {
  if (typeof target !== 'string' || !target.trim()) {
    return { ok: false, error: '无效路径' }
  }
  const abs = path.resolve(target)
  const root = path.resolve(UPLOAD_ROOT)
  const rel = path.relative(root, abs)
  if (rel === '..' || rel.startsWith(`..${path.sep}`) || path.isAbsolute(rel)) {
    return { ok: false, error: '路径不在允许范围内' }
  }
  if (!fs.existsSync(abs)) {
    return { ok: false, error: '文件不存在' }
  }
  return { ok: true, path: abs }
}

ipcMain.handle('desktop:open-path', async (_event, target) => {
  const res = resolveAllowed(target)
  if (!res.ok) return { ok: false, error: res.error }
  const openError = await shell.openPath(res.path)
  return openError ? { ok: false, error: openError } : { ok: true }
})

ipcMain.handle('desktop:show-in-folder', (_event, target) => {
  const res = resolveAllowed(target)
  if (!res.ok) return { ok: false, error: res.error }
  shell.showItemInFolder(res.path)
  return { ok: true }
})

app.whenReady().then(() => {
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
