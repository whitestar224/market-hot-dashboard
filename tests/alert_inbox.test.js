const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {safeLink}=require('../alert-inbox.js');
test('untrusted source URLs cannot execute script',()=>{
  assert.equal(safeLink('javascript:alert(1)'),'');
  assert.equal(safeLink('data:text/html,hi'),'');
  assert.equal(safeLink('https://example.com/a'),'https://example.com/a');
});
test('one event stream has no source category tabs and embeds delivery records',()=>{
  const html=fs.readFileSync(require.resolve('../event-flow.html'),'utf8');
  assert.doesNotMatch(html,/data-flow-filter/);
  assert.match(html,/id="eventFlowList"/);
  assert.match(html,/id="alertArchive"/);
  assert.doesNotMatch(html,/alert-inbox\.html/);
});
