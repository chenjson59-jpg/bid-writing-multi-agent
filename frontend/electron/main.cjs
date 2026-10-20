const { app, BrowserWindow } = require('electron')
const path = require('node:path')

const DEV_SERVER_URL = process.env.VITE_DEV_SERVER_URL || 'http://localhost:5173'
const LOAD_DIST = process.env.ELECTRON_LOAD_DIST === '1'

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

app.whenReady().then(() => {
  createWindow()
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})
