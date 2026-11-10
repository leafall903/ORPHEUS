import {test} from 'node:test';
import assert from 'node:assert/strict';
import {NpcConsoleClient} from '../ow174/dashboard/web/npc-console.mjs';

test('fast menu then numeric key waits for the acknowledged menu state', async () => {
  const requests = [];
  const client = new NpcConsoleClient(async (url, request) => {
    assert.equal(url, '/api/npc_console');
    const payload = JSON.parse(request.body);
    requests.push(payload);
    return {ok: true, json: async () => payload.key === '=' ?
      {handled: true, action: 'menu', menu_open: true, choices: []} :
      {handled: true, action: 'spawn', entity: 1}};
  });
  const menu = client.send('=', 'A');
  const spawn = client.send('1', 'A');
  assert.equal(client.menuRequested, true);
  await Promise.all([menu, spawn]);
  assert.deepEqual(requests, [
    {key: '=', name: 'A', menu_open: false}, {key: '1', name: 'A', menu_open: true},
  ]);
  assert.equal(client.menuRequested, false);
});

test('refused request is not retried and does not poison later stop command', async () => {
  const keys = [];
  const client = new NpcConsoleClient(async (_url, request) => {
    const {key} = JSON.parse(request.body);
    keys.push(key);
    return key === '=' ? {ok: false, json: async () => ({error: 'Catalog unavailable'})} :
      {ok: true, json: async () => ({action: 'stop', count: 2})};
  });
  await assert.rejects(client.send('='), /Catalog unavailable/);
  assert.equal(client.menuOpen, false);
  assert.equal(client.menuRequested, false);
  assert.equal((await client.send('{')).count, 2);
  assert.deepEqual(keys, ['=', '{']);
});

test('unknown keys never issue requests', async () => {
  let count = 0;
  const client = new NpcConsoleClient(async () => {count++;});
  assert.deepEqual(await client.send('w'), {handled: false});
  assert.equal(count, 0);
});

test('stop supersedes queued attacks while waiting for an in-flight attack', async () => {
  const keys = [];
  let release;
  const inFlight = new Promise(resolve => { release = resolve; });
  const client = new NpcConsoleClient(async (_url, request) => {
    keys.push(JSON.parse(request.body).key);
    if (keys.length === 1) await inFlight;
    return {ok: true, json: async () => ({handled: true})};
  });
  const first = client.send('0');
  await new Promise(resolve => setImmediate(resolve));
  const superseded = client.send('0');
  const stop = client.send('{');
  const restart = client.send('0');
  assert.deepEqual(keys, ['0']);
  release();
  await Promise.all([first, stop, restart]);
  assert.deepEqual(await superseded, {handled: false, canceled: true});
  assert.deepEqual(keys, ['0', '{', '0']);
});
