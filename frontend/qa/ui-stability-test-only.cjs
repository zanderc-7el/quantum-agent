const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.QCAL_PLAYWRIGHT_ROOT||'C:/Program Files/WindowsApps/OpenAI.CodexPrimaryRuntime.v26-1112-611-0_26.1112.611.0_x64__3k8sg7r9htsxt/dependencies/node/node_modules/playwright');
const dir=path.resolve(__dirname,'../qa-artifacts/r10-test-only'),server=JSON.parse(fs.readFileSync(path.join(dir,'server.json'),'utf8'));
const base=new URL(server.url).origin,result={mode:'TEST_ONLY synthetic input; current candidate dist and actual read-only report backend; no instrument/controller execution',tests:[],pageErrors:[],externalRequests:[]};
let browser,page;
(async()=>{
 browser=await chromium.launch({executablePath:'C:/Program Files/Google/Chrome/Application/chrome.exe',headless:true});
 page=await browser.newPage({viewport:{width:1440,height:1000},locale:'zh-CN'});
 page.on('pageerror',e=>result.pageErrors.push(String(e)));
 await page.route('**/*',route=>{if(new URL(route.request().url()).origin!==base){result.externalRequests.push(route.request().url());return route.abort();}return route.continue();});
 await page.goto(server.url,{waitUntil:'domcontentloaded'});
 await page.getByRole('button',{name:'长期稳定性',exact:true}).click();
 await page.getByLabel('已有周期计划').selectOption('TEST_ONLY-schedule-a');
 const report=page.getByRole('region',{name:'按观察时段归集稳定性'});
 const schedule=page.locator('article.science-card').filter({has:page.getByRole('heading',{name:'周期定频 → 同轮 T₁',exact:true})});
 const scheduleTable=schedule.locator(':scope > .science-table-wrap table');
 await scheduleTable.locator('tbody tr').nth(2).waitFor();
 assert.equal(await scheduleTable.locator('tbody tr').count(),3);
 assert.match(await scheduleTable.locator('tbody tr').nth(1).innerText(),/失败.*未获得有效频率.*本轮未测/s);
 for(const name of ['频率漂移','本轮 T₁ 趋势']){
  const plot=page.getByRole('img',{name,exact:true});const d=await plot.locator('path').getAttribute('d');
  assert.equal((d.match(/M/g)||[]).length,2);assert.equal((d.match(/L/g)||[]).length,0);assert.equal(await plot.locator('circle').count(),2);
 }
 assert.equal(await page.getByRole('img',{name:'频率漂移',exact:true}).locator('text[x="58"]').textContent(),'MHz');
 result.tests.push('Nonempty schedule has three rows; failed middle frequency/T1 remains null, two separated plotted points and no connecting segment.');
 await page.getByLabel('计划芯片版本').selectOption('TEST_ONLY-chip-b');
 assert.equal(await page.getByLabel('已有周期计划').inputValue(),'');
 assert.equal(await page.getByLabel('已有周期计划').locator('option[value="TEST_ONLY-schedule-a"]').count(),0);
 await page.getByLabel('已有周期计划').selectOption('TEST_ONLY-schedule-b');
 assert.equal(await scheduleTable.locator('tbody tr').count(),3);
 assert.match(await scheduleTable.innerText(),/TEST_ONLY-schedule-b-wf-0/);
 result.tests.push('Switching schedule chip clears previous selection and hides the other chip schedule; chip B renders its own three rows.');
 await report.getByLabel('报告开始日期').fill('2026-11-26');await report.getByLabel('报告结束日期').fill('2026-11-26');
 await report.getByLabel('报告芯片版本').selectOption('TEST_ONLY-chip-a');
 await report.getByRole('button',{name:'按范围查看'}).click();
 await page.waitForFunction(()=>document.querySelector('a[download]')?.getAttribute('href')?.includes('chipVersionId=TEST_ONLY-chip-a'));
 assert.equal(await report.locator('tbody tr').count(),3);
 assert.match(await report.locator('tbody tr').nth(1).innerText(),/失败.*未获得有效频率.*本轮未测或待核查/s);
 const aHref=await report.getByRole('link',{name:'下载当前范围 HTML'}).getAttribute('href');
 const htmlA=await (await fetch(base+aHref)).text();fs.writeFileSync(path.join(dir,'chip-a-report.html'),htmlA);
 assert.ok(htmlA.includes('TEST_ONLY 芯片甲 三轮'));assert.ok(!htmlA.includes('TEST_ONLY 芯片乙 三轮'));assert.ok(htmlA.includes('TEST_ONLY'));
 await report.getByLabel('报告芯片版本').selectOption('TEST_ONLY-chip-b');
 assert.equal(await report.getByRole('link',{name:'下载当前范围 HTML'}).getAttribute('href'),aHref);
 assert.ok((await report.locator('tbody').innerText()).includes('TEST_ONLY 芯片甲 三轮'));
 await report.getByRole('button',{name:'按范围查看'}).click();
 await page.waitForFunction(()=>document.querySelector('a[download]')?.getAttribute('href')?.includes('chipVersionId=TEST_ONLY-chip-b'));
 const bHref=await report.getByRole('link',{name:'下载当前范围 HTML'}).getAttribute('href');
 const htmlB=await (await fetch(base+bHref)).text();fs.writeFileSync(path.join(dir,'chip-b-report.html'),htmlB);
 assert.ok(htmlB.includes('TEST_ONLY 芯片乙 三轮'));assert.ok(!htmlB.includes('TEST_ONLY 芯片甲 三轮'));
 const exported=await browser.newPage({viewport:{width:1440,height:1000},locale:'zh-CN'});
 await exported.goto(base+bHref.replace('&download=1',''),{waitUntil:'domcontentloaded'});
 assert.equal(await exported.locator('tbody tr').count(),3);assert.match(await exported.locator('tbody tr').nth(1).innerText(),/失败.*未获得有效频率/s);
 await exported.screenshot({path:path.join(dir,'export-chip-b.png'),fullPage:true});await exported.close();
 result.tests.push('Applied date+chip report has exactly three rows and preserves failure; dirty chip changes neither rows nor export until applied; actual HTML downloads contain only selected chip schedule.');
 await page.getByLabel('计划芯片版本').selectOption('TEST_ONLY-chip-a');await page.getByLabel('已有周期计划').selectOption('TEST_ONLY-schedule-a');
 const before=await (await fetch(base+'/test-only/state')).json();
 const save=page.getByRole('button',{name:'保存周期计划',exact:true});
 for(const [label,bad,good] of [['轮次间隔','0.01','3'],['最大轮次','2.5','3'],['精扫频宽','999','2000000'],['频率点数','162','101']]){
  const field=schedule.getByLabel(label,{exact:false});await field.fill(bad);assert.equal(await save.isDisabled(),true);await field.fill(good);
 }
 assert.equal(await save.isDisabled(),false);
 const after=await (await fetch(base+'/test-only/state')).json();assert.equal(after.writes.length,before.writes.length);
 result.tests.push('60-second/161-point and integer constraints disable submission for invalid values; no writes occurred.');
 await report.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(dir,'report-desktop.png'),fullPage:true});
 await page.getByRole('img',{name:'频率漂移',exact:true}).scrollIntoViewIfNeeded();await page.screenshot({path:path.join(dir,'three-round-gaps-desktop.png')});
 await page.setViewportSize({width:960,height:760});await page.screenshot({path:path.join(dir,'three-round-gaps-960.png')});
 const geometry=await page.evaluate(()=>({scroll:document.documentElement.scrollWidth,width:innerWidth}));assert.ok(geometry.scroll<=geometry.width+1);
 result.tests.push('1440px desktop and 960px viewport screenshots captured, with no horizontal document overflow at 960px.');
 // Exercise the lower valid boundary against the isolated draft-only handler.
 await page.setViewportSize({width:1440,height:1000});
 await schedule.getByLabel('轮次间隔',{exact:false}).fill(String(1/60));await schedule.getByLabel('频率点数',{exact:false}).fill('4');
 await save.click();await page.getByText('周期计划已保存，尚未启动',{exact:true}).waitFor();
 const final=await (await fetch(base+'/test-only/state')).json();
 assert.equal(final.writes.length,before.writes.length+1);assert.equal(final.writes.at(-1).body.intervalSeconds,60);assert.equal(final.writes.at(-1).body.points,4);
 assert.equal(final.writes.at(-1).path,'/api/stability/schedules');
 result.tests.push('Valid 60-second interval and even four-point count create only a TEST_ONLY draft, with canonical numeric request values; no start action issued.');
 // Restore a useful nonempty entry point for a separate desktop session.
 result.writes=final.writes;result.reads=final.reads;assert.deepEqual(result.pageErrors,[]);assert.deepEqual(result.externalRequests,[]);
})().catch(async e=>{result.error=String(e.stack||e);if(page){fs.writeFileSync(path.join(dir,'failure-dom.txt'),await page.locator('body').innerText().catch(()=>''));await page.screenshot({path:path.join(dir,'failure.png'),fullPage:true}).catch(()=>{});}process.exitCode=1;}).finally(async()=>{fs.writeFileSync(path.join(dir,'result.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));await browser?.close();});
