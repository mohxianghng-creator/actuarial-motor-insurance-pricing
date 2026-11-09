const fs=require('fs'),path=require('path'),{pathToFileURL}=require('url');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const repo=path.resolve(__dirname,'..'),scratch=process.env.PRICING_VERIFY_WORK||path.join(repo,'.browser_work');
fs.mkdirSync(scratch,{recursive:true});
const assert=(condition,message)=>{if(!condition)throw Error(message)};
const cases=[{name:'synthetic',file:path.join(repo,'demo/DASHBOARD.html'),count:600}];
if(process.env.REAL_DASHBOARD_PATH)cases.push({name:'real',file:process.env.REAL_DASHBOARD_PATH,count:10000});
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_EXECUTABLE||(process.platform==='win32'?'C:/Program Files/Google/Chrome/Application/chrome.exe':undefined),args:['--no-sandbox'],downloadsPath:scratch,env:{...process.env,TEMP:scratch,TMP:scratch}});
 const reports=[];
 try{
 for(const item of cases){
  const context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});await context.setOffline(true);
  const page=await context.newPage(),errors=[],external=[];page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(/^https?:/.test(r.url()))external.push(r.url())});
  await page.goto(pathToFileURL(item.file).href);await page.waitForFunction(()=>window.PricingLab?.ready);
  assert(await page.evaluate(()=>window.PricingLab.getState().rows.length)===item.count,'Initial population '+item.name);
  const totals=await page.evaluate(()=>{
   const w=window.PricingLab,d=w.data;let comparisons=0,maxError=0;
   for(const r of d.quotes){const p=w.price(r.pure_premium,d.config.pricing,d.summary.thresholds);for(const key of ['expected_loss','money_margin','cost_floor','technical_premium','quote','expected_contribution']){maxError=Math.max(maxError,Math.abs(p[key]-r[key]));comparisons++}}
   return {comparisons,maxError};
  });assert(totals.maxError<1e-8,'Stored/default financial parity');
  // Independent Python reference cases are optional and supplied only in the local review.
  let referenceChecks=0;
  if(item.name==='real'&&process.env.FINANCIAL_CASES){
   const expected=JSON.parse(fs.readFileSync(process.env.FINANCIAL_CASES,'utf8'));
   referenceChecks=await page.evaluate(cases=>{let n=0;const w=window.PricingLab;
    const check=(actual,expected)=>{for(const [key,value] of Object.entries(expected)){if(typeof value==='number'){if(Math.abs(actual[key]-value)>1e-8*Math.max(1,Math.abs(value)))throw Error('Python reference mismatch: '+key)}else if(actual[key]!==value)throw Error('Python reference flag mismatch');n++}};
    for(const t of cases.pricing)check(w.price(t.pure,t.config,t.thresholds),t.expected);
    for(const t of cases.market)check(w.marketCalc(w.data.quotes,t.policy,t.assumptions,t.adjustment),t.expected);return n;
   },expected);
  }
  // The reported four-filter empty combination, or a generated empty combination.
  let filters;
  if(item.name==='real')filters={region:'Basse-Normandie',gas:'Regular',brand:'Opel, General Motors or Ford',age:'25-34'};
  else filters=await page.evaluate(()=>{const q=window.PricingLab.data.quotes,age=['18-24','25-34','35-49','50-64','65-120'];for(const region of [...new Set(q.map(r=>r.Region))])for(const gas of [...new Set(q.map(r=>r.Gas))])for(const brand of [...new Set(q.map(r=>r.Brand))])for(const band of age){const [a,b]=band.split('-').map(Number);if(!q.some(r=>r.Region===region&&r.Gas===gas&&r.Brand===brand&&r.DriverAge>=a&&r.DriverAge<=b))return {region,gas,brand,age:band}}return null});
  // Synthetic combinations may all be represented; a nonmatching PolicyID tests its empty view.
  if(filters){
   for(const [key,value] of Object.entries(filters))await page.locator('#filter-'+key).selectOption(value);
   await page.waitForFunction(()=>window.PricingLab.getState().rows.length===0);
   assert(await page.locator('#kpi-quote').textContent()==='—','Empty mean quote must be unavailable');assert(await page.locator('#kpi-cr').textContent()==='—','Empty combined ratio');
   await page.locator('[data-tab="commercial"]').click();assert(await page.locator('#market-cr').textContent()==='—','Empty market ratio');assert(await page.locator('#market-acceptance').textContent()==='—','Empty acceptance');assert(await page.locator('#stress-heatmap svg').count()===0,'Empty heatmap must not show 0%');
   await page.locator('[data-tab="quotes"]').click();assert(await page.locator('#export-quotes').isDisabled(),'Empty export');assert(await page.locator('#trace-title').textContent()==='Policy quote trace','Empty quote title');
   await page.locator('#clear-filters').click();await page.waitForFunction(n=>window.PricingLab.getState().rows.length===n,item.count);
  }
  await page.locator('[data-tab="quotes"]').click();await page.locator('#quote-search').fill('999999999999');assert(await page.locator('#export-quotes').isDisabled(),'No-search-results export');assert(await page.locator('#trace-title').textContent()==='Policy quote trace','No-search-results stale title');await page.locator('#quote-search').fill('');
  // Nonempty small cohorts and individual empty bands.
  const options=await page.evaluate(()=>({region:[...document.querySelector('#filter-region').options].map(o=>o.value).filter(Boolean),gas:[...document.querySelector('#filter-gas').options].map(o=>o.value).filter(Boolean)}));
  let combinations=0;
  for(const region of options.region.slice(0,4))for(const gas of options.gas){
   await page.locator('#filter-region').selectOption(region);await page.locator('#filter-gas').selectOption(gas);
   await page.waitForFunction(({region,gas})=>{const w=window.PricingLab,expected=w.data.quotes.filter(r=>r.Region===region&&r.Gas===gas);return w.getState().rows.length===expected.length&&w.getState().rows.every(r=>r.Region===region&&r.Gas===gas)},{region,gas});combinations++;
  }
  if(item.name==='real'){
   const one=await page.evaluate(()=>{const q=window.PricingLab.data.quotes,groups=new Map();for(const r of q){const age=r.DriverAge<25?'18-24':r.DriverAge<35?'25-34':r.DriverAge<50?'35-49':r.DriverAge<65?'50-64':'65-120';const v={region:r.Region,gas:r.Gas,brand:r.Brand,age},key=JSON.stringify(v);groups.set(key,(groups.get(key)||0)+1)}for(const [key,n] of groups)if(n===1)return JSON.parse(key)});
   assert(one,'Singleton test combination');for(const [key,value] of Object.entries(one))await page.locator('#filter-'+key).selectOption(value);await page.waitForFunction(()=>window.PricingLab.getState().rows.length===1);
   assert((await page.locator('#cohort-notice').textContent()).includes('Small cohort'),'Small cohort warning');
   const bands=await page.locator('#burden-table tbody tr').allTextContents();assert(bands.filter(x=>x.includes('—')).length===2,'Empty individual bands must not show zero means');
  }
  await page.locator('#reset-all').click();await page.locator('[data-tab="margins"]').click();await page.locator('#policy-mode').selectOption('flat_constant');await page.locator('#policy-flat').fill('75');await page.waitForFunction(()=>window.PricingLab.getState().valid&&window.PricingLab.getState().policy.flat_risk_margin===75);
  const saved=await page.evaluate(()=>{const w=window.PricingLab,d=w.data,s=w.getState();return {schema_version:'2.0.0',input_sha256:d.manifest.input_sha256,config_sha256:d.manifest.config_sha256,model_fingerprint:d.manifest.fit_cache_fingerprint,model_candidate:d.summary.decision.technical_candidate,policy:s.policy,market:s.market,filters:Object.fromEntries(['filter-region','filter-gas','filter-brand','filter-age'].map(id=>[id,document.getElementById(id).value]))}});
  await page.locator('#policy-fixed').fill('');await page.waitForFunction(()=>!window.PricingLab.getState().valid);assert(await page.locator('#kpi-quote').textContent()==='—','Invalid inputs must hide stale means');assert(await page.locator('#save-scenario').isDisabled(),'Invalid save disabled');assert(!await page.locator('#load-scenario').isDisabled(),'Valid scenario recovery must remain possible');
  await page.evaluate(s=>window.PricingLab.applyImport(s),saved);assert(await page.evaluate(()=>window.PricingLab.getState().valid),'Recover invalid controls by import');
  // Rejected imports must not disturb a valid display.
  for(const kind of ['identity','tier_string','negative_target','out_of_bounds','unknown_filter']){
   const rejected=await page.evaluate(({saved,kind})=>{const w=window.PricingLab,s=structuredClone(saved);if(kind==='identity')s.input_sha256='bad';if(kind==='tier_string')s.policy.tier_risk_margins=['10',20,35];if(kind==='negative_target')s.market.minimum_expected_policies=-1;if(kind==='out_of_bounds')s.policy.flat_risk_margin=1001;if(kind==='unknown_filter')s.filters['filter-region']='UNKNOWN';try{w.applyImport(s);return false}catch(e){return w.getState().valid&&w.getState().policy.flat_risk_margin===75}},{saved,kind});assert(rejected,'Bad scenario accepted or altered state '+kind);
  }
  await page.locator('#policy-variable').fill('70');await page.locator('#policy-tax').fill('35');await page.waitForFunction(()=>!window.PricingLab.getState().valid);assert((await page.locator('#input-error').textContent()).includes('below 100%'),'Expense + tax check');await page.locator('#reset-all').click();
  await page.locator('[data-tab="commercial"]').click();await page.locator('#market-capacity').fill('0');await page.waitForFunction(()=>window.PricingLab.getState().valid&&window.PricingLab.getState().market.expected_capacity===0);assert(await page.locator('#market-volume').textContent()==='0.0','Zero capacity volume is actually zero');assert(await page.locator('#market-cr').textContent()==='—','No accepted premium ratio undefined');assert(await page.locator('#market-acceptance').textContent()==='0.0%','Zero capacity acceptance');
  const nonfinite=await page.evaluate(()=>{const w=window.PricingLab,s=w.getState();for(const key of ['risk_tilt','expected_capacity','minimum_expected_policies']){try{w.marketCalc(s.rows,s.policy,{...s.market,[key]:NaN});return false}catch(e){}}return true});assert(nonfinite,'NaN market input rejected');
  await page.locator('#reset-all').click();await page.locator('[data-tab="quotes"]').click();const [download]=await Promise.all([page.waitForEvent('download'),page.locator('#export-quotes').click()]);const file=path.join(scratch,item.name+'_quotes.csv');await download.saveAs(file);assert(fs.readFileSync(file,'utf8').trim().split(/\r?\n/).length===item.count+1,'Export row count');
  let graphs=0;
  for(const [tab,ids] of Object.entries({portfolio:['quote-histogram'],evidence:['cv-chart','pdp-chart'],margins:['margin-curves'],commercial:['tradeoff-chart','stress-heatmap']})){
   await page.locator(`[data-tab="${tab}"]`).click();for(const id of ids){assert(await page.locator('#'+id+' svg').isVisible(),'Missing graph '+id);assert(!/NaN|Infinity/.test(await page.locator('#'+id).innerHTML()),'Invalid chart coordinates');graphs++}
  }
  await page.locator('[data-tab="portfolio"]').click();await page.screenshot({path:path.join(scratch,item.name+'_desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),'Mobile overflow');assert(await page.locator('#quote-histogram svg').isVisible(),'Mobile graph');
  assert(!errors.length,'Runtime errors '+JSON.stringify(errors));assert(!external.length,'External requests');
  reports.push({dataset:item.name,risks:item.count,financial_comparisons:totals.comparisons,max_absolute_error:totals.maxError,python_reference_checks:referenceChecks,empty_cohort:!!filters,empty_search:true,filter_combinations:combinations,invalid_inputs_hide_stale_results:true,scenario_recovery:true,rejected_import_cases:5,zero_capacity:true,nonfinite_rejection:true,csv_rows:item.count,visible_graphs:graphs,mobile:true,browser_errors:errors,external_requests:external.length});await context.close();
 }
 const result={all_passed:true,offline:true,reports};fs.writeFileSync(path.join(scratch,'DASHBOARD_REGRESSION_CHECKS.json'),JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify(result,null,2));
 }finally{await browser.close()}
})().catch(e=>{console.error(e.stack);process.exit(1)});
