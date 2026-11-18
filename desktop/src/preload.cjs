'use strict';
const {contextBridge, ipcRenderer} = require('electron');
// Only a real DOM user gesture on an ordinary web link can open the system browser.
// Programmatic window.open/location changes never reach this capability.
window.addEventListener('click', event => {
  if (!event.isTrusted || event.defaultPrevented) return;
  const anchor = event.target?.closest?.('a[href]');
  if (!anchor) return;
  let url;
  try {url = new URL(anchor.href);} catch {return;}
  if (!['http:', 'https:'].includes(url.protocol) || url.origin === 'http://127.0.0.1:8765' || url.username || url.password) return;
  event.preventDefault();
  ipcRenderer.send('desktop:external-link', url.href);
}, true);
contextBridge.exposeInMainWorld('qcalDesktop', Object.freeze({
  retry: () => ipcRenderer.invoke('desktop:retry'),
  chooseProject: () => ipcRenderer.invoke('desktop:choose-project'),
  status: () => ipcRenderer.invoke('desktop:status'),
  onStatus: callback => {const handler = (_event, value) => callback(value); ipcRenderer.on('desktop:status', handler); return () => ipcRenderer.removeListener('desktop:status', handler);},
}));
