const sampleStocks = [
  {name:'N R Agarwal Inds',symbol:'NRAIL',price:584,ma50:571.2,ma200:548.6,change:2.14,pe:15.05,roce:8.49,marketCap:993.92,volume:165937,average:19558,catalyst:'Profit growth +111% · paper demand',sector:'Paper & Packaging',crossed:'12 days ago'},
  {name:'Nexus Select',symbol:'NXST',price:165.89,ma50:161.4,ma200:157.8,change:1.27,pe:57.06,roce:5.83,marketCap:25132.34,volume:4966636,average:610789,catalyst:'Sales growth +10.9% · yield 1.47%',sector:'Real Estate',crossed:'26 days ago'},
  {name:'Nitiraj Engineer',symbol:'NITIRAJ',price:219.19,ma50:207.6,ma200:191.2,change:-0.64,pe:93.62,roce:1.79,marketCap:224.69,volume:38842,average:4359,catalyst:'Profit growth +419% · order momentum',sector:'Engineering',crossed:'8 days ago'}
];
let stocks = structuredClone(sampleStocks);
let loadedGeneratedData = false;
const $ = selector => document.querySelector(selector);
const money = value => '₹' + Number(value).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});
const compact = value => value >= 1000 ? '₹' + (value/1000).toFixed(1) + 'k Cr' : '₹' + value.toFixed(0) + ' Cr';
const clean = value => String(value || '').toLowerCase().replace(/[^a-z0-9]/g,'');
function csvRows(text) {
  const rows=[]; let row=[], cell='', quoted=false;
  for (let i=0;i<text.length;i++) { const c=text[i], next=text[i+1]; if(c==='"'&&quoted&&next==='"'){cell+='"';i++;} else if(c==='"'){quoted=!quoted;} else if(c===','&&!quoted){row.push(cell.trim());cell='';} else if((c==='\n'||c==='\r')&&!quoted){if(c==='\r'&&next==='\n')i++;row.push(cell.trim());if(row.some(Boolean))rows.push(row);row=[];cell='';} else cell+=c; }
  if(cell||row.length){row.push(cell.trim());rows.push(row)} return rows;
}
const number = value => Number(String(value || '').replace(/[₹,%\s,]/g,'')); 
function parseCsv(text) {
  const rows=csvRows(text); if(rows.length<2) throw new Error('CSV has no data rows.');
  const headers=rows.shift().map(clean);
  const get=(row,names)=>{const i=headers.findIndex(h=>names.includes(h));return i>=0?row[i]:''};
  const missing=[]; if(!headers.some(h=>['stock','name','stockname','company'].includes(h)))missing.push('stock name');
  if(!headers.some(h=>['cmp','currentprice','price'].includes(h)))missing.push('current price');
  if(missing.length) throw new Error('Missing required column: '+missing.join(', ')+'.');
  return rows.map((row,index)=>{
    const price=number(get(row,['cmp','currentprice','price'])), name=get(row,['stock','name','stockname','company']);
    if(!name||!price) throw new Error('Invalid stock name or price on CSV row '+(index+2)+'.');
    const ma200=number(get(row,['ma200','200dma','dma200','200daymovingaverage']))||price*.93;
    const ma50=number(get(row,['ma50','50dma','dma50','50daymovingaverage']))||price*.98;
    return {name,symbol:get(row,['nsecode','symbol','code'])||name.slice(0,8).toUpperCase(),price,ma50,ma200,change:number(get(row,['change','changepercent','pricechange']))||0,pe:number(get(row,['pe','peratio']))||null,roce:number(get(row,['roce']))||null,marketCap:number(get(row,['marketcap','marketcapitalisation']))||0,volume:number(get(row,['volume','volume1d']))||0,average:number(get(row,['averagevolume1month','averagevolume1mth','volume1mth']))||0,catalyst:'Imported Screener fundamentals',sector:get(row,['sector'])||'Unclassified',crossed:'Imported row'};
  }).filter(stock=>stock.ma50>=stock.ma200);
}
function decorate(stock,index){const distance=(stock.price/stock.ma200-1)*100;return {...stock,distance,rank:index+1,fresh:stock.crossed.includes('days')&&parseInt(stock.crossed)<30}}
function render(){
  const query=$('#searchInput').value.toLowerCase(), sector=$('#sectorFilter').value, sort=$('#sortSelect').value;
  let visible=stocks.map((s,i)=>decorate(s,i)).filter(s=>(s.name+' '+s.symbol).toLowerCase().includes(query)&&(sector==='ALL'||s.sector===sector));
  visible.sort((a,b)=>sort==='name'?a.name.localeCompare(b.name):sort==='distance'?b.distance-a.distance:sort==='change'?b.change-a.change:a.pe-b.pe);
  $('#stockRows').innerHTML=visible.map((s,i)=>`<tr><td><div class="stock"><span class="rank">#${i+1}</span><div><strong>${escapeHtml(s.name)}</strong><span class="symbol">${escapeHtml(s.symbol)} · ${escapeHtml(s.sector)}</span></div></div></td><td class="mono">${money(s.price)}<small class="${s.change>=0?'up':'down'}">${s.change>=0?'+':''}${s.change.toFixed(2)}% today</small></td><td class="mono">${money(s.ma50)}</td><td class="mono">${money(s.ma200)}</td><td><span class="distance up">+${s.distance.toFixed(1)}%</span><small class="distance">above 200D · ${escapeHtml(s.crossed)}</small></td><td><div class="fundamentals"><span><b>${s.pe?s.pe.toFixed(1):'—'}</b><small>P/E</small></span><span><b>${s.roce?s.roce.toFixed(1)+'%':'—'}</b><small>ROCE</small></span><span><b>${s.marketCap?compact(s.marketCap):'—'}</b><small>CAP</small></span></div></td><td class="mono">${s.volume&&s.average?(s.volume/s.average).toFixed(1)+'x':'—'}<small class="distance">volume ratio</small></td><td class="catalyst">${escapeHtml(s.catalyst)}</td><td><span class="badge ${s.fresh?'buy':'watch'}">${s.fresh?'FRESH CROSS':'ABOVE MA'}</span></td></tr>`).join('');
  $('#emptyState').hidden=visible.length>0; $('#resultLabel').textContent=visible.length?'· '+visible.length+' shown':'';
  $('#signalCount').textContent=stocks.length; $('#averageDistance').textContent=stocks.length?(stocks.reduce((a,s)=>a+(s.price/s.ma200-1)*100,0)/stocks.length).toFixed(1)+'%':'—'; $('#freshCount').textContent=stocks.filter(s=>s.crossed.includes('days')&&parseInt(s.crossed)<30).length; $('#topRank').textContent=stocks.length?'#1':'—';
}
function escapeHtml(value){return String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[c]))}
async function refresh(){const button=$('#refreshButton');button.disabled=true;button.innerHTML='Loading…';try{await loadGeneratedData(true);$('#refreshStatus').textContent='Latest generated scan loaded';}catch(error){$('#refreshStatus').textContent='Live data unavailable; showing current data.';}finally{button.disabled=false;button.innerHTML='Refresh scan <span>↻</span>';render();}}
async function loadGeneratedData(force=false){const response=await fetch('data/nse_golden_cross.json'+(force?'?t='+Date.now():''));if(!response.ok)throw new Error('Generated data unavailable');const payload=await response.json();if(!Array.isArray(payload.stocks)||(!payload.stocks.length&&!payload.generatedAt))throw new Error('Generated data is empty');stocks=payload.stocks.map(s=>({...s,crossed:s.crossed||'trend intact'}));loadedGeneratedData=true;$('#dataStatus').textContent='Automated data · '+new Date(payload.generatedAt).toLocaleDateString('en-IN');setSectors();render();if(payload.warnings&&payload.warnings.length)$('#refreshStatus').textContent='Partial update: '+payload.warnings.length+' symbol(s) unavailable';}
['searchInput','sectorFilter','sortSelect'].forEach(id=>$('#'+id).addEventListener('input',render));
$('#refreshButton').addEventListener('click',refresh);
$('#screenerFile').addEventListener('change',event=>{const file=event.target.files[0];if(!file)return;const reader=new FileReader();reader.onload=()=>{try{stocks=parseCsv(reader.result);$('#dataStatus').textContent='Imported CSV';$('#refreshStatus').textContent=stocks.length+' qualifying rows loaded';setSectors();render()}catch(error){$('#refreshStatus').textContent='Import error: '+error.message}};reader.readAsText(file)});
function setSectors(){const select=$('#sectorFilter'), current=select.value;select.innerHTML='<option value="ALL">All sectors</option>'+[...new Set(stocks.map(s=>s.sector))].sort().map(s=>`<option>${escapeHtml(s)}</option>`).join('');if([...select.options].some(o=>o.value===current))select.value=current}
setSectors();render();
loadGeneratedData().catch(()=>{$('#dataStatus').textContent='Sample data · generated feed unavailable';$('#refreshStatus').textContent='Using clearly labelled sample data. Import a Screener CSV or retry later.';});
