const { app, BrowserWindow, ipcMain, net, shell } = require('electron')
const path = require('node:path')
const fs = require('node:fs')
const crypto = require('node:crypto')

const DEV_SERVER_URL = process.env.VITE_DEV_SERVER_URL || 'http://localhost:5173'
// 打包安装后没有环境变量，靠 app.isPackaged 判定；开发态仍可用 ELECTRON_LOAD_DIST=1 强制走 dist
const LOAD_DIST = app.isPackaged || process.env.ELECTRON_LOAD_DIST === '1'

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

// 判断是否为 HTTP/HTTPS URL（渲染进程传来的下载链接）
function isHttpUrl(target) {
  return typeof target === 'string' && /^https?:\/\//i.test(target.trim())
}

// 通过 Electron net.fetch 将 HTTP URL 下载到临时目录，返回本地路径
async function downloadToTemp(url) {
  const resp = await net.fetch(url)
  if (!resp.ok) {
    throw new Error(`HTTP ${resp.status}: ${resp.statusText}`)
  }
  const buf = Buffer.from(await resp.arrayBuffer())
  // 从 URL 提取文件名；提取不到就用随机名
  let filename = 'download.bin'
  try {
    const parsed = new URL(url)
    const last = parsed.pathname.split('/').filter(Boolean).pop()
    if (last && /\.[a-z0-9]+$/i.test(last)) filename = last
    // 加一层 hash 避免不同 bid_id 同名文件互相覆盖
    const hash = crypto.createHash('md5').update(parsed.pathname + (parsed.search || '')).digest('hex').slice(0, 8)
    filename = `${hash}-${filename}`
  } catch { /* 忽略 URL 解析失败 */ }
  const dest = path.join(app.getPath('temp'), filename)
  fs.writeFileSync(dest, buf)
  return dest
}

ipcMain.handle('desktop:open-path', async (_event, target) => {
  if (isHttpUrl(target)) {
    try {
      const local = await downloadToTemp(target)
      const openError = await shell.openPath(local)
      return openError ? { ok: false, error: openError } : { ok: true, path: local }
    } catch (exc) {
      return { ok: false, error: `下载失败：${exc.message}` }
    }
  }
  // 本地路径仍走白名单校验
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
