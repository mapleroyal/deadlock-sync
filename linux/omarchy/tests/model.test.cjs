const assert = require('node:assert/strict');
const m = require('../plugin/Model.js');
assert.equal(m.gameSession([{lastIpcObject:{class:'steam_app_1422450',pid:123}}]), '123');
assert.equal(m.gameSession([{lastIpcObject:{class:'steam',pid:123,title:'Deadlock'}}]), '');
assert.equal(m.gameSession([{lastIpcObject:{class:'steam_app_1422450',pid:123}}, {lastIpcObject:{class:'steam_app_1422450',pid:123}}]), '123');
assert.equal(m.gameSession([{wayland:{appId:'steam_app_1422450'}, address:'0x123'}]), '0x123');
assert.equal(m.parse('{"live":"<img src=https://bad>","backfill":"inactive"}').live, 'unavailable');
assert.equal(m.parse('invalid').live, 'unavailable');
assert(!m.safeTooltip('<img src="https://bad"> &').includes('<'));
assert(!m.safeTooltip('<img src="https://bad"> &').includes('&'));
assert.equal(m.statusKey({live:'active',backfill:'inactive'},true,false,''), 'state.syncing');
assert.equal(m.statusKey({live:'inactive',backfill:'inactive',paused:true},true,false,''), 'state.paused');
const progress = m.parse(JSON.stringify({live:'inactive', backfill:'activating', progress:{
  mode:'backfill', phase:'recovering', completed:12, total:40, uploaded:10,
  failed:2, history:600, updated_at:1794768000
}})).progress;
assert.equal(m.progressKnown(progress), true);
assert.equal(m.progressRatio(progress), 0.3);
assert.equal(progress.uploaded, 10, 'uploaded is distinct from processed');
assert.equal(m.progressKnown(m.progress({mode:'backfill',phase:'preparing'})), false);
assert.equal(m.progressRatio(m.progress({mode:'backfill',completed:0,total:0})), 0);
assert.equal(m.progressKnown(m.progress({mode:'live',completed:1,total:2})), false);
assert.equal(m.progress({mode:'backfill',completed:8,total:3}).completed, null);
const hostile = m.progress({mode:'<img src=https://bad>',phase:'<img src=https://bad>',
  completed:'12',total:-3,uploaded:Infinity,failed:1.1,history:10000001,updated_at:1e15});
assert.equal(hostile.mode, '');
assert.equal(hostile.phase, 'preparing');
for (const key of ['completed','total','uploaded','failed','history','updated_at']) assert.equal(hostile[key], null);
assert.equal(m.progress(null).uploaded, null);
assert.equal(m.progress({mode:'live'}).phase, 'watching');
const cooldown = m.parse(JSON.stringify({live:'inactive',backfill:'failed',recovery:{
  enabled:true,phase:'cooldown',reason:'rate_limit',next_at:1794854886,skipped:2
}}));
assert.equal(m.statusKey(cooldown,false,false,''), 'state.recovery.cooldown', 'expected cooldown is not a failed service display');
assert.equal(m.shouldPoll(cooldown),true);
assert.equal(m.pollInterval(cooldown),5000,'waiting mode notices Steam account changes promptly');
assert.equal(m.pollInterval({...cooldown,backfill:'activating'}),2000);
assert.equal(m.statusKey({...cooldown,live:'active'},true,false,''),'state.syncing','live sync takes display priority');
assert.equal(m.statusKey({...cooldown,live:'failed'},true,false,''),'state.failed','live failure is not hidden by automatic mode');
assert.equal(m.statusKey({...cooldown,live:'failed'},false,false,''),'state.recovery.cooldown','old live failure does not override recovery after game closes');
assert.equal(m.statusKey({...cooldown,backfill:'active'},false,false,''),'state.backfill');
assert.equal(m.shouldPoll(m.empty()),false,'manual quit leaves no idle timer');
assert.equal(m.recovery({enabled:'true',phase:'cooldown'}).enabled,false);
const invalidRecovery = m.recovery({enabled:true,phase:'<img src=https://bad>',reason:'<img src=https://bad>',next_at:Infinity,skipped:-3});
assert.equal(invalidRecovery.phase,'attention');
assert.equal(invalidRecovery.reason,'unavailable');
assert.equal(invalidRecovery.next_at,null);
assert.equal(invalidRecovery.skipped,0);
assert.equal(m.recovery(null).phase,'off');
assert.equal(m.recovery({repair_required:true}).repair_required,true);
assert.equal(m.recovery({repair_required:'true'}).repair_required,false);
assert.equal(m.scheduledAttempt(cooldown.recovery,1794768486),true);
assert.equal(m.scheduledAttempt(cooldown.recovery,1794854886),false,'elapsed deadline is not a future schedule');
for (const phase of ['off','working','caught_up','waiting_for_account','attention']) {
  assert.equal(m.scheduledAttempt({...cooldown.recovery,phase},1794768486),false,phase+' has no scheduled retry');
}
assert.equal(m.scheduledAttempt({...cooldown.recovery,phase:'game'},1794768486),true,'queued game can retain a future cooldown');
console.log('Model tests passed');

const accountView = m.recovery({enabled:true, current_account:'<img src=https://bad>', active_account:'other', account_count:2,
  accounts:[{name:'<b>Test</b>', current:true, phase:'cooldown', next_at:2000000000}]});
assert.equal(accountView.current_account,'<img src=https://bad>','account names remain plain text, not evaluated markup');
assert.equal(accountView.accounts[0].current,true);
assert.equal(m.recovery({accounts:[{name:42,current:'true',phase:'invalid',next_at:Infinity}]}).accounts[0].phase,'attention');
assert.equal(m.recovery({current_account:'x'.repeat(400)}).current_account.length,80);

const savedRow = (id, progress) => m.recovery({accounts: [{id, progress}]}).accounts[0];
assert.equal(savedRow('4294967295', progress).id, '4294967295');
for (const id of ['0', '01', '4294967296', 42, true, '1e2']) assert.equal(savedRow(id).id, '');
assert.deepEqual(savedRow('1', progress).progress, progress);
assert.equal(savedRow('1', {mode:'backfill', phase:'failed', updated_at:100}).progress, null);
assert.equal(savedRow('1', {mode:'backfill', phase:'preparing', updated_at:100}).progress, null);
assert.equal(savedRow('1', {mode:'live', uploaded:3}).progress, null);
assert.equal(savedRow('1', {mode:'backfill', total:'1', history:-1}).progress, null);
assert.equal(savedRow('1', {mode:'backfill', phase:'complete', history:0}).progress.history, 0);
assert.equal(savedRow('1', {mode:'backfill', phase:'complete', updated_at:100}).progress.updated_at, 100);

const manyAccounts = Array.from({length:64}, (_, i) => ({
  id:String(i+1), name:'😀'.repeat(80), current:i===0, phase:'cooldown', next_at:2000000000, progress
}));
// Match Python json.dumps' ASCII escaping of both UTF-16 units.
const asciiJson = JSON.stringify({live:'inactive', backfill:'inactive', recovery:{accounts:manyAccounts}})
  .replace(/[^\x00-\x7f]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0'));
assert.ok(asciiJson.length > 65536);
assert.equal(m.parse(asciiJson).recovery.accounts[63].progress.uploaded, 10);
