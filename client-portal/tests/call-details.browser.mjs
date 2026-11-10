// Run against tests/mock/server.py only; requires Playwright in the Node environment.
// PORTAL_TEST_URL can point at another local instance of that fictional server.
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const { chromium } = createRequire(import.meta.url)('playwright');
const base = process.env.PORTAL_TEST_URL || 'http://127.0.0.1:4174';
assert.ok(['127.0.0.1', 'localhost'].includes(new URL(base).hostname), 'Fictional local server only');
const browser = await chromium.launch({headless:true, channel:process.env.PORTAL_BROWSER_CHANNEL || 'chrome'});
const page = await browser.newPage({viewport:{width:1100,height:900}});
const errors=[];
page.on('pageerror',error=>errors.push(error.message));
let signingRequests=0;
page.on('request',request=>{if(request.url().includes('/object/sign/'))signingRequests++;});
try {
  await page.goto(base);
  await page.locator('#email').fill('owner@example.test');
  await page.locator('#password').fill('fictional-password');
  await page.locator('#signinButton').click();
  const card=page.locator('.request').filter({hasText:'FICTIONAL Alex Sample'});
  await card.click();
  await page.locator('#callDetail[open]').waitFor();
  // The regression: opening a call used to hide all captured fields and text.
  assert.match(await page.locator('#callDetail').innerText(),/Studio A/);
  assert.deepEqual(await page.locator('#linkedRequests .facts dd').allTextContents(),[
    'FICTIONAL Alex Sample','(555) 555-0101','Recording session (with engineer)',
    'Saturday at 2pm','2 hours','Studio A, own beats',
  ]);
  assert.equal(await page.locator('#callDetail .turn-text').count(),8,'Every saved fictional conversation turn is rendered');
  assert.ok(await page.locator('#callDetail .turn-text').first().isVisible(),'Call transcript must be visible before loading audio');
  assert.equal(signingRequests,0,'Opening text must not request private audio');
  assert.equal(await page.locator('#callAudio').getAttribute('src'),null);
  assert.equal(await page.locator('#callDetail').getByText('FICTIONAL Other-Business Caller').count(),0);
  const textBefore=await page.locator('#callDetail .turn-text').allTextContents();
  await page.locator('#loadAudio').click();
  await page.locator('#callAudio:not([hidden])').waitFor();
  assert.equal(signingRequests,1);
  assert.deepEqual(await page.locator('#callDetail .turn-text').allTextContents(),textBefore);
  await page.locator('#callAudio').evaluate(async audio=>{await audio.play();});
  await page.waitForFunction(()=>document.getElementById('callAudio').currentTime>0);
  await page.locator('#linkedRequests button').first().click();
  await page.locator('#detail[open]').waitFor();
  assert.ok(await page.locator('#transcript .turn-text').first().isVisible(),'Linked request transcript must start expanded');
  await page.waitForFunction(()=>!document.getElementById('callAudio').hasAttribute('src'));
  assert.equal(await page.locator('#callAudio').getAttribute('src'),null,'Leaving call stops and clears audio');
  await page.locator('#close').click();
  await page.locator('.request').filter({hasText:'Call without a saved request'}).first().click();
  assert.equal(await page.locator('#callDetail .turn-text').count(),0,'Another call must not retain the previous transcript');
  assert.match(await page.locator('#callDetail').innerText(),/No saved transcript/);
  await page.locator('#closeCall').click();
  await page.setViewportSize({width:390,height:844});
  await card.click();
  assert.ok(await page.locator('#callDetail .turn-text').first().isVisible());
  assert.ok(await page.locator('#closeCall').isVisible());
  assert.ok(await page.locator('#callDetail').evaluate(el=>el.scrollWidth<=el.clientWidth),'No horizontal overflow on mobile');
  assert.ok(await page.locator('#linkedRequests button').first().evaluate(el=>el.scrollWidth<=el.clientWidth),'Request button text fits on mobile');
  await page.locator('#callTranscript').evaluate(el=>el.scrollIntoView({block:'start'}));
  if(process.env.PORTAL_SCREENSHOT)await page.screenshot({path:process.env.PORTAL_SCREENSHOT});
  assert.deepEqual(errors,[]);
  console.log('Passed: call fields and transcript, optional audio playback, linked-request transcript, empty-call reset, and mobile layout.');
} finally {await browser.close();}
