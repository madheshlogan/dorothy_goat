async function loadAnalyst(){const note=document.getElementById('analystNotice');try{const r=await fetch('/api/analyst');const d=await r.json();const s=d.stats;document.getElementById('stats').innerHTML=[['Calls handled',s.calls_handled],['Talk time',Math.round(s.talk_time_seconds/60)+' min'],['Payments blocked',s.payments_blocked],['Dollars protected',money(s.dollars_protected)]].map(x=>`<div class="stat"><span>${esc(x[0])}</span><strong>${esc(x[1])}</strong></div>`).join('');const pending=d.candidates.filter(x=>x.status==='pending' && x.item_type !== 'bank_account');document.getElementById('candidateEmpty').style.display=pending.length?'none':'block';document.getElementById('candidateRows').innerHTML=pending.map(x=>`<tr><td><strong>${esc(x.item_type)}</strong><br><code>${esc(x.item_type==='bank_account'?x.value.replace('|',' / '):x.value)}</code>${x.bank_name?'<br>'+esc(x.bank_name):''}</td><td>${esc(x.scam_type||'unknown')}</td><td><span class="risk">${x.risk_score}/100</span></td><td>${esc(((((d.calls.find(c=>c.id===x.source_call_id)||{}).extraction||{}).red_flags||[])).join(', '))}</td><td><button class="text-button" onclick="viewCall('${esc(x.source_call_id||'')}')">View transcript</button></td><td><div class="review-buttons"><button onclick="reviewCandidate(${x.id},'approve')">Approve</button><button class="danger" onclick="reviewCandidate(${x.id},'reject')">Reject</button></div></td></tr>`).join('');document.getElementById('scripts').innerHTML=d.latest_scripts.length?d.latest_scripts.map(x=>`<li>“${esc(x)}”</li>`).join(''):'<li class="muted">No pressure phrases extracted yet.</li>';document.getElementById('calls').innerHTML=d.calls.map(c=>`<details><summary>${esc(fmtDate(c.started_at))} · ${esc(c.id.slice(0,8))} · ${c.extraction?c.extraction.risk_score+' risk':'not extracted'}${repeatBadge(c.extraction)} <button class="text-button" onclick="event.preventDefault();event.stopPropagation();downloadReport('${esc(c.id)}')">Download Report</button></summary><pre>${esc(c.transcript.map(m=>(m.name||m.speaker)+': '+m.text).join('\n'))}</pre></details>`).join('')||'<p class="muted">No calls recorded yet.</p>';note.textContent='Dashboard refreshed.';}catch(e){note.textContent='Could not load dashboard. Please refresh.';}loadNetwork();}
async function reviewCandidate(id,action){try{const r=await fetch(`/api/watchlist/${id}/${action}`,{method:'POST'});const d=await r.json();if(!r.ok)throw new Error(d.detail);document.getElementById('analystNotice').textContent=`Candidate ${action}d.`;loadAnalyst();}catch(e){document.getElementById('analystNotice').textContent='Review action failed. Refresh and try again.';}}
async function viewCall(id){try{const d=await (await fetch('/api/analyst')).json();const c=d.calls.find(x=>x.id===id);if(c)alert(c.transcript.map(m=>(m.name||m.speaker)+': '+m.text).join('\n\n'));}catch(e){alert('Transcript unavailable.');}}
function fmtDate(iso){return iso?iso.slice(0,10)+' '+iso.slice(11,16)+' UTC':'unknown date';}
function repeatBadge(ex){return ex&&(ex.red_flags||[]).includes('repeat_caller')?' <span class="badge-repeat">⚠ Repeat caller</span>':'';}
function downloadReport(callId){window.location.href='/api/analyst/report/'+encodeURIComponent(callId);}

// ---- Scammer network graph (vis.js, loaded only on this page) ----
let networkInstance=null;
function showNetworkMessage(text){const box=document.getElementById('scammer-network'),m=document.getElementById('networkEmpty');m.textContent=text;m.classList.toggle('hidden',!text);return box;}
function showNetworkNode(n){
  document.getElementById('networkDetail').innerHTML=`<h3>${esc(n.date)} · ${esc(n.scam_type||'unknown')}</h3><p><span class="risk" style="background:${esc(n.color)};color:#fff">${esc(n.risk_score)}/100 risk</span>${n.repeat_caller?' <span class="badge-repeat">⚠ Repeat caller</span>':''}</p><p>${esc(n.summary||'No summary available.')}</p><p class="muted">Call ${esc(n.call_id.slice(0,8))}</p><div class="button-row"><button class="secondary" onclick="viewCall('${esc(n.call_id)}')">View transcript</button><button onclick="downloadReport('${esc(n.call_id)}')">Download Report</button></div>`;
}
async function loadNetwork(){
  const box=document.getElementById('scammer-network');
  if(!box)return;
  try{
    const d=await (await fetch('/api/analyst/network')).json();
    if(networkInstance){networkInstance.destroy();networkInstance=null;}
    document.getElementById('networkDetail').innerHTML='<p class="muted">Click a call to see its summary.</p>';
    if(d.nodes.length<2){box.classList.add('hidden');showNetworkMessage('Not enough data: fewer than 2 analysed calls so far. No connections found yet — run more calls.');return;}
    if(typeof vis==='undefined'){box.classList.add('hidden');showNetworkMessage('The graph library could not be loaded (it needs internet access to cdnjs.cloudflare.com).');return;}
    box.classList.remove('hidden');
    showNetworkMessage(d.edges.length?'':'No connections found yet — run more calls. Each call is shown as an isolated point.');
    const byId={};d.nodes.forEach(n=>byId[n.id]=n);
    const pairCount={};
    const nodes=new vis.DataSet(d.nodes.map(n=>({id:n.id,label:n.label,shape:'dot',size:14+Math.round(n.risk_score/10),color:{background:n.color,border:n.color,highlight:{background:n.color,border:'#182230'}}})));
    const edges=new vis.DataSet(d.edges.map(e=>{const k=e.from+'|'+e.to,i=pairCount[k]=(pairCount[k]||0)+1;return{from:e.from,to:e.to,label:e.label,font:{size:9,align:'middle'},smooth:{type:'curvedCW',roundness:i===1?0:0.2*i}};}));
    networkInstance=new vis.Network(box,{nodes,edges},{physics:{barnesHut:{gravitationalConstant:-5000,springLength:200},stabilization:{iterations:150}},interaction:{hover:true},nodes:{font:{size:11}},edges:{color:{color:'#98a2b3'},width:2}});
    networkInstance.on('click',p=>{if(p.nodes.length)showNetworkNode(byId[p.nodes[0]]);});
  }catch(e){box.classList.add('hidden');showNetworkMessage('Could not load the scammer network. Please refresh.');}
}
loadAnalyst();
