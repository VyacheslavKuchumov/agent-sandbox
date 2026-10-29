'use strict';
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('sandbox', Object.freeze({
  snapshot: () => ipcRenderer.invoke('sandbox:snapshot'),
  run: request => ipcRenderer.invoke('sandbox:run', request),
  logs: target => ipcRenderer.invoke('sandbox:logs', target),
  launch: request => ipcRenderer.invoke('sandbox:launch', request),
  openDashboard: target => ipcRenderer.invoke('sandbox:dashboard', target),
}));
