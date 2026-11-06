const { contextBridge, ipcRenderer } = require('electron')

// 暴露受限桌面能力：打开文件 / 定位目录，均走主进程白名单校验（docs/api-contract.md §11）。
contextBridge.exposeInMainWorld('desktop', {
  openPath: (target) => ipcRenderer.invoke('desktop:open-path', target),
  showItemInFolder: (target) => ipcRenderer.invoke('desktop:show-in-folder', target),
})
