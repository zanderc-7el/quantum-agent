const render = state => {document.getElementById('message').textContent = state.message || '正在连接…'; document.body.classList.toggle('error', state.phase === 'error'); document.querySelector('.actions').hidden = state.phase !== 'error';};
window.qcalDesktop.onStatus(render);
window.qcalDesktop.status().then(render);
document.getElementById('retry').addEventListener('click', () => window.qcalDesktop.retry().then(render));
document.getElementById('choose').addEventListener('click', () => window.qcalDesktop.chooseProject().then(render));
