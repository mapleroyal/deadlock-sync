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
assert.equal(m.pollInterval(cooldown),60000,'waiting mode uses slow local checks');
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
for (const phase of ['off','working','caught_up','attention']) {
  assert.equal(m.scheduledAttempt({...cooldown.recovery,phase},1794768486),false,phase+' has no scheduled retry');
}
assert.equal(m.scheduledAttempt({...cooldown.recovery,phase:'game'},1794768486),true,'queued game can retain a future cooldown');
console.log('Model tests passed');
