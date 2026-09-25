// Run after npm run build: node frontend/tests/smoke_sidebar.cjs
const { chromium } = require('playwright');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../dist');
const server = http.createServer((req,res) => {
  const file = path.join(root, req.url === '/' ? 'index.html' : req.url.split('?')[0]);
  res.setHeader('Content-Type', file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
  fs.createReadStream(file).on('error',()=>res.end()).pipe(res);
});
(async()=>{
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 const browser=await chromium.launch({headless:true});
 try {
 const page=await browser.newPage();
 await page.route('**/api/**',route=>{
  const url=route.request().url();
  let body=[];
  if(url.includes('/workspaces')) body=[{id:'00000000-0000-0000-0000-000000000001',name:'Workspace'}];
  if(url.includes('/conversations')) body=Array.from({length:100},(_,i)=>({id:String(i),title:'Conversation '+i}));
  if(url.includes('/health')) body={providers:{openai:true},models:{openai:'test'},max_steps:50};
  return route.fulfill({json:body});
 });
 await page.goto(`http://127.0.0.1:${server.address().port}`);
 await page.waitForSelector('.conversation');
 for(const [width,height] of [[1440,800],[1000,600],[1000,400],[600,400]]){
  await page.setViewportSize({width,height});
  for(const scroll of [false,true]){
   if(scroll) await page.evaluate(()=>{for(const s of document.querySelectorAll('.conversation-list,.sidebar-content')) s.scrollTop=s.scrollHeight;window.scrollTo(0,document.body.scrollHeight)});
   const boxes=await page.evaluate(()=>Object.fromEntries(['.sidebar','.sidebar-bottom','.settings-button','.theme-toggle'].map(s=>{const r=document.querySelector(s).getBoundingClientRect();return [s,{top:r.top,bottom:r.bottom,left:r.left,right:r.right}]})));
   for(const s of ['.settings-button','.theme-toggle']) {assert(boxes[s].top>=0,JSON.stringify(boxes));assert(boxes[s].bottom<=height,JSON.stringify(boxes));assert(boxes[s].right<=boxes['.sidebar'].right)}
   assert(height-boxes['.sidebar-bottom'].bottom<=20,JSON.stringify(boxes));
  }
 }
 await page.locator('.theme-toggle').click();
 assert.equal(await page.locator('html').getAttribute('data-theme'),'dark');
 await page.locator('.settings-button').click();
 assert(await page.getByRole('dialog',{name:'Settings',exact:true}).isVisible());
 console.log('PASS: sidebar footer visible with 100 conversations, page/list scrolling, desktop/mobile/short viewports; settings and theme controls work');
 } finally {await browser.close();server.close()}
})().catch(e=>{console.error(e);server.close();process.exitCode=1});
