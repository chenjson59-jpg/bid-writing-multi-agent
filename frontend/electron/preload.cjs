const { contextBridge } = require('electron')

// 桌面能力占位桥：后续接入「打开文件 / 定位目录」的受限 IPC（仅 upload/ 白名单）。
contextBridge.exposeInMainWorld('desktop', {
  openPath: () => Promise.resolve({ ok: false, error: '未实现' }),
  showItemInFolder: () => Promise.resolve({ ok: false, error: '未实现' }),
})
