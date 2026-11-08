export class NpcConsoleClient {
  constructor(request = globalThis.fetch, changed = () => {}) {
    this.request = request;
    this.changed = changed;
    this.menuOpen = false;
    this.menuRequested = false;
    this.queue = Promise.resolve();
  }
  send(key, name = '') {
    if (!['=', '-', '0', '{', 'Escape', ...'123456789'].includes(key)) {
      return Promise.resolve({handled: false});
    }
    if (key === '=') this.menuRequested = true;
    const next = this.queue.catch(() => {}).then(async () => {
      const response = await this.request('/api/npc_console', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({key, name, menu_open: this.menuOpen}),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
      if (typeof data.menu_open === 'boolean') this.menuOpen = data.menu_open;
      this.changed(data);
      return data;
    });
    this.queue = next;
    return next.finally(() => {if (key === '=') this.menuRequested = false;});
  }
}

if (typeof document !== 'undefined') {
  const status = document.querySelector('#status');
  const log = document.querySelector('#log');
  const choices = document.querySelector('#choices');
  const account = document.querySelector('#account');
  const write = text => {
    status.textContent = text;
    log.textContent = `${new Date().toLocaleTimeString()} ${text}\n${log.textContent}`.slice(0, 16000);
  };
  const client = new NpcConsoleClient(globalThis.fetch.bind(globalThis), data => {
    if (data.action === 'menu') {
      choices.replaceChildren();
      for (const choice of data.choices) {
        const button = document.createElement('button');
        button.textContent = `${choice.key} · ${choice.name}`;
        button.addEventListener('click', () => send(choice.key));
        choices.append(button);
      }
      choices.hidden = false;
      choices.style.display = 'flex';
      write('选择数字键生成敌人。');
    } else if (data.action === 'close_menu') {
      choices.hidden = true;
      choices.style.display = 'none';
      write('生成菜单已关闭。');
    } else if (data.action === 'spawn') write(`已生成 ${data.name} · ${data.entity}`);
    else if (data.action === 'remove') write(`已移除 ${data.entities.length} 个手动敌人及尸体。`);
    else if (data.action === 'start') write(`敌人 ${data.entity} 已接收开攻指令；实际效果以游戏表现为准。`);
    else if (data.action === 'stop') write(`已向 ${data.count} 个手动敌人发送停止指令。`);
  });
  const send = key => client.send(key, account.value.trim()).catch(error => write(`操作失败：${error.message}`));
  document.querySelectorAll('[data-key]').forEach(button =>
    button.addEventListener('click', () => send(button.dataset.key)));
  document.addEventListener('keydown', event => {
    if (event.repeat || event.ctrlKey || event.altKey || event.metaKey ||
        event.target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(event.target.tagName)) return;
    const key = event.key;
    if (!['=', '-', '0', '{', 'Escape', ...'123456789'].includes(key)) return;
    if (/^[1-9]$/.test(key) && !client.menuOpen && !client.menuRequested) return;
    event.preventDefault();
    send(key);
  });
}
