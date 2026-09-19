const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('nom', {
  minimize: () => ipcRenderer.send('window:minimize'),
  maximize: () => ipcRenderer.send('window:maximize'),
  close: () => ipcRenderer.send('window:close'),
  getBackendPort: () => ipcRenderer.invoke('backend:port'),
  getToken: () => ipcRenderer.invoke('backend:token'),
  onBackendStatus: (callback) => {
    ipcRenderer.on('backend-status', (_event, status) => callback(status));
  },
  toggleOverlay: () => ipcRenderer.invoke('overlay:toggle'),
  onOverlayState: (callback) => {
    ipcRenderer.on('overlay-state', (_event, visible) => callback(visible));
  },
  pickFiles: (kind, defaultPath) => ipcRenderer.invoke('dialog:pick', kind, defaultPath),
  onNxmLink: (callback) => {
    ipcRenderer.on('nxm-link', (_event, url) => callback(url));
  },
  nxmStatus: () => ipcRenderer.invoke('nxm:status'),
  nxmRegister: (on) => ipcRenderer.invoke('nxm:register', !!on),
  openNexus: (url) => ipcRenderer.invoke('nexus:open', url),
  platform: process.platform
});
