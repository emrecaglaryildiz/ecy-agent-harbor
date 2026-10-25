const assert = require('node:assert/strict');
const {execFileSync} = require('node:child_process');
const time = require('../static/time.js');
assert.equal(time.scheduleEpoch('2026-10-25T09:30'), Date.parse('2026-10-25T06:30:00Z')/1000);
assert.throws(()=>time.scheduleEpoch('bad'));
for(const TZ of ['UTC','America/New_York','Asia/Tokyo','Europe/Istanbul']) {
 const result=execFileSync(process.execPath,['-e',"process.stdout.write(String(require('./static/time.js').scheduleEpoch('2026-10-25T09:30')))"],{env:{...process.env,TZ}}).toString();
 assert.equal(Number(result),1792909800);
}
console.log('Istanbul schedule parsing is independent of the device timezone.');
