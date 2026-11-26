/* WingBench project page. All scientific values come from the public release. */
(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];
  const motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)');
  const methods = [
    {key:'CMA-ES no feedback', name:'Fixed archive', color:'#7c8790', dash:'5 5'},
    {key:'CMA-ES selection only', name:'Archive reordering', color:'#48535d', dash:'2 4'},
    {key:'CMA-ES feedback-guided', name:'Feedback-guided', color:'#365f80', dash:''}
  ];
  const checkpoints = [0,1,2,4,8,16,32];
  const state = {task:'nominal', metric:'quality', index:3, data:null};
  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = (tag, attrs = {}, text = '') => {
    const node = document.createElementNS(svgNS,tag);
    for (const [k,v] of Object.entries(attrs)) node.setAttribute(k,String(v));
    if(text) node.textContent = text;
    return node;
  };

  function renderResults() {
    if(!state.data) return;
    const task = state.data[state.task];
    const q = checkpoints[state.index];
    const reliability = state.metric === 'reliability';
    $('#query-output').textContent = `${q} CFD ${q === 1 ? 'query' : 'queries'}`;
    $('#query-budget').setAttribute('aria-valuetext',`${q} CFD queries`);
    $('#checkpoint-number').textContent = q;
    $('#chart-metric-label').textContent = reliability ? 'Valid recommendations (%)' : 'Mean lift-to-drag ratio';
    $('#chart-title').textContent = `CMA-ES recommendation ${state.metric}, ${state.task} load`;
    const chart = $('#results-chart');
    [...chart.children].filter(n => !['title','desc'].includes(n.tagName)).forEach(n => n.remove());
    const compact=window.matchMedia('(max-width:480px)').matches;
    const W=compact?400:620,H=compact?280:290,L=compact?42:46,R=22,T=23,B=35;
    chart.setAttribute('viewBox',`0 0 ${W} ${H}`);
    const qualities = methods.flatMap(m=>task[m.key].map(p=>p.quality)).filter(v=>v!==null);
    const lo = reliability ? 0 : Math.floor(Math.min(...qualities)-.65);
    const hi = reliability ? 100 : Math.ceil(Math.max(...qualities)+.65);
    const px = x=>L+(W-L-R)*x/32;
    const py = y=>H-B-(H-B-T)*(y-lo)/(hi-lo);
    const value = p=>reliability ? p.valid/p.runs*100 : p.quality;
    for(let i=0;i<=4;i++) {
      const v=lo+(hi-lo)*i/4;
      chart.append(svg('line',{x1:L,x2:W-R,y1:py(v),y2:py(v),stroke:'#dfe4e8','stroke-opacity':1,'stroke-width':1}));
      chart.append(svg('text',{x:L-12,y:py(v)+4,'text-anchor':'end'},reliability?`${Math.round(v)}`:v.toFixed(hi-lo>10?0:1)));
    }
    [0,4,8,16,32].forEach(x=>chart.append(svg('text',{x:px(x),y:H-9,'text-anchor':'middle'},String(x))));
    chart.append(svg('line',{x1:px(q),x2:px(q),y1:T-6,y2:H-B+3,stroke:'#97a5b0','stroke-opacity':.7,'stroke-dasharray':'3 5'}));
    for(const m of methods) {
      let path='',active=false;
      for(const p of task[m.key]) {
        const v=value(p);
        if(v===null) {active=false;continue;}
        path+=`${active?'L':'M'}${px(p.query).toFixed(2)},${py(v).toFixed(2)} `;active=true;
      }
      chart.append(svg('path',{d:path,fill:'none',stroke:m.color,'stroke-width':2.3,'stroke-linejoin':'round','stroke-linecap':'round','stroke-dasharray':m.dash}));
      for(const p of task[m.key]) {
        const v=value(p);
        if(v===null)continue;
        chart.append(svg('circle',{cx:px(p.query),cy:py(v),r:p.query===q?5.2:2.7,fill:p.query===q?m.color:'#ffffff',stroke:m.color,'stroke-width':p.query===q?2:1.6}));
      }
    }
    const summaries=$('#method-summaries');summaries.replaceChildren();
    for(const m of methods) {
      const p=task[m.key].find(p=>p.query===q);
      const el=document.createElement('div');el.className='method-summary';el.style.setProperty('--method-color',m.color);
      el.innerHTML=`<p class="method-summary-title"><span class="legend-line" aria-hidden="true"></span>${m.name}</p><div class="summary-numbers"><span class="summary-quality">${p.quality===null?'—':p.quality.toFixed(2)}<small>L/D</small></span><span class="summary-valid">${p.valid}/${p.runs} valid</span></div><div class="delivery-dots" aria-hidden="true">${Array.from({length:p.runs},(_,i)=>`<i class="${i<p.valid?'valid':''}"></i>`).join('')}</div>`;
      summaries.append(el);
    }
    let insight;
    if(state.task==='nominal'&&q===32) insight='By 32 queries, feedback-guided CMA-ES reaches a similar mean L/D (33.04), while delivering valid recommendations in 10/10 runs. Both archive variants deliver in 4/10 runs (mean L/D 33.22).';
    else if(state.task==='nominal'&&q===4) insight='At four queries, the archive methods have higher mean L/D among valid recommendations (33.22 and 33.18). Feedback-guided search delivers in all ten runs, with mean L/D 28.73.';
    else if(state.task==='low') insight='At low load, neither archive variant returns a valid recommendation at these checkpoints. Mean L/D is undefined because no recommendations received valid scores.';
    else {
      const feedback=task[methods[2].key][state.index];
      insight=`At ${q} ${q===1?'query':'queries'}, feedback-guided search returns ${feedback.valid} valid recommendations out of 10. Mean L/D is computed separately for each method’s successful runs.`;
    }
    $('#result-insight').textContent=insight;
    $('#data-table-caption').textContent=`${state.task[0].toUpperCase()+state.task.slice(1)} load · ten runs per method`;
    const body=$('#data-table-body');body.replaceChildren();
    for(const query of checkpoints)for(const m of methods){
      const p=task[m.key].find(p=>p.query===query);const tr=document.createElement('tr');
      for(const txt of [query,m.name,`${p.valid}/${p.runs}`,p.quality===null?'Undefined (no valid recommendations)':p.quality.toFixed(2)]){const td=document.createElement('td');td.textContent=txt;tr.append(td);}
      body.append(tr);
    }
  }
  $$('[data-task]').forEach(button=>button.addEventListener('click',()=>{
    state.task=button.dataset.task;
    $$('[data-task]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
    renderResults();
  }));
  $$('[data-metric]').forEach(button=>button.addEventListener('click',()=>{
    state.metric=button.dataset.metric;
    $$('[data-metric]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
    renderResults();
  }));
  $('#query-budget').addEventListener('input',e=>{state.index=Number(e.target.value);renderResults();});
  let resizeTimer;
  window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(renderResults,100);});
  fetch('./assets/results.json').then(r=>{if(!r.ok)throw new Error('Results unavailable');return r.json();}).then(data=>{state.data=data;renderResults();}).catch(()=>{
    $('#result-insight').textContent='The interactive data could not load. The complete result table is available from the download link below.';
    $('#query-budget').disabled=true;
  });

  $('#copy-citation').addEventListener('click',async()=>{
    const text=$('#bibtex').textContent;
    try {
      if(navigator.clipboard?.writeText) await navigator.clipboard.writeText(text);
      else {
        const field=document.createElement('textarea');field.value=text;field.style.position='fixed';field.style.opacity='0';document.body.append(field);field.select();
        const copied=document.execCommand('copy');field.remove();if(!copied)throw new Error('Copy unavailable');
      }
      $('#copy-citation span').textContent='Copied';$('#copy-status').textContent='BibTeX citation copied to clipboard.';
      setTimeout(()=>{$('#copy-citation span').textContent='Copy BibTeX';},2200);
    } catch {
      $('#copy-status').textContent='Clipboard unavailable. Select the citation text to copy it.';
      $('#copy-citation span').textContent='Select text to copy';
    }
  });

  function initWings(meshes) {
    const canvas=$('#wing-canvas'),ctx=canvas.getContext('2d');
    if(!ctx)return;
    const stage=$('#wing-stage');let mesh,vertices,colors;
    let width=0,height=0,visible=true,paused=true,dragging=false;
    let yaw=-1.05,elevation=.70,pointer={x:0,y:0},phase=0,last=0,dirty=true;
    function select(task) {
      mesh=meshes[task];
      const min=[0,1,2].map(k=>Math.min(...mesh.vertices.map(v=>v[k])));
      const max=[0,1,2].map(k=>Math.max(...mesh.vertices.map(v=>v[k])));
      const center=min.map((v,k)=>(v+max[k])/2),scale=Math.max(...min.map((v,k)=>max[k]-v));
      vertices=mesh.vertices.map(v=>v.map((x,k)=>(x-center[k])/scale));
      colors=mesh.faces.map((f,i)=>{
        const a=vertices[f[0]],b=vertices[f[1]],c=vertices[f[2]];
        const u=b.map((v,k)=>v-a[k]),v=c.map((v,k)=>v-a[k]);
        let nx=u[1]*v[2]-u[2]*v[1],ny=u[2]*v[0]-u[0]*v[2],nz=u[0]*v[1]-u[1]*v[0];
        const len=Math.hypot(nx,ny,nz)||1;nx/=len;ny/=len;nz/=len;
        const upper=i<mesh.faces.length/2;
        const light=Math.max(0,Math.min(1,.35+.53*Math.abs(nz)+.15*nx-.10*ny));
        return upper?`rgb(${Math.round(53+light*78)},${Math.round(89+light*81)},${Math.round(117+light*82)})`:`rgb(${Math.round(38+light*46)},${Math.round(70+light*57)},${Math.round(95+light*63)})`;
      });
      $('#wing-sweep').textContent=`${mesh.sweep.toFixed(1)}°`;
      $('#wing-aspect').textContent=mesh.aspectRatio.toFixed(2);
      $('#wing-score').textContent=mesh.ld.toFixed(2);
      const method=mesh.method==='CMA-ES feedback-guided'?'Feedback-guided CMA-ES':mesh.method;
      $('#wing-method').textContent=`${task[0].toUpperCase()+task.slice(1)} load · ${method} · Query ${mesh.query}`;
      canvas.setAttribute('aria-label',`Highest-scoring ${task}-load wing found across all methods. ${method}, query ${mesh.query}, L/D ${mesh.ld.toFixed(2)}. Sweep ${mesh.sweep.toFixed(1)} degrees, aspect ratio ${mesh.aspectRatio.toFixed(2)}. Drag or use arrow keys to rotate.`);
      dirty=true;
    }
    function resize() {
      const rect=canvas.getBoundingClientRect();width=rect.width;height=rect.height;
      const ratio=Math.min(window.devicePixelRatio||1,2);
      canvas.width=Math.round(width*ratio);canvas.height=Math.round(height*ratio);ctx.setTransform(ratio,0,0,ratio,0,0);dirty=true;
    }
    function draw() {
      if(width===0||height===0||!vertices)return;
      ctx.clearRect(0,0,width,height);
      const angle=yaw+(!paused&&!dragging?Math.sin(phase)*.12:0);
      const elev=elevation+(!paused&&!dragging?Math.sin(phase*.65)*.025:0);
      const ca=Math.cos(angle),sa=Math.sin(angle),ce=Math.cos(elev),se=Math.sin(elev);
      const raw=vertices.map(([x,y,z])=>{const xx=x*ca-y*sa,yy=x*sa+y*ca;return [xx,yy*se-z*ce,yy*ce+z*se];});
      const xs=raw.map(v=>v[0]),ys=raw.map(v=>v[1]);
      const minx=Math.min(...xs),maxx=Math.max(...xs),miny=Math.min(...ys),maxy=Math.max(...ys);
      const factor=Math.min(width*.90/(maxx-minx),height*.62/(maxy-miny));
      const midx=(minx+maxx)/2,midy=(miny+maxy)/2;
      const points=raw.map(([x,y,z])=>[(x-midx)*factor+width*.50,(y-midy)*factor+height*.46,z]);
      const shadow=ctx.createRadialGradient(width*.52,height*.76,0,width*.52,height*.76,width*.38);
      shadow.addColorStop(0,'rgba(38,66,89,.10)');shadow.addColorStop(1,'rgba(38,66,89,0)');
      ctx.save();ctx.translate(width*.52,height*.76);ctx.scale(1,.12);ctx.translate(-width*.52,-height*.76);ctx.fillStyle=shadow;ctx.beginPath();ctx.arc(width*.52,height*.76,width*.38,0,Math.PI*2);ctx.fill();ctx.restore();
      const faces=mesh.faces.map((f,i)=>({f,i,z:f.reduce((s,k)=>s+points[k][2],0)/f.length})).sort((a,b)=>a.z-b.z);
      ctx.lineJoin='round';ctx.lineWidth=.38;
      for(const {f,i} of faces) {
        ctx.beginPath();f.forEach((k,j)=>{const p=points[k];if(j===0)ctx.moveTo(p[0],p[1]);else ctx.lineTo(p[0],p[1]);});ctx.closePath();
        ctx.fillStyle=colors[i];ctx.fill();ctx.strokeStyle='rgba(34,71,101,.22)';ctx.stroke();
      }
      stage.classList.add('canvas-ready');dirty=false;
    }
    function frame(now) {
      const delta=Math.min((now-last)||0,60);
      if(visible&&!document.hidden&&now-last>32) {
        if(!paused&&!dragging) {phase+=delta*.00017;dirty=true;}
        if(dirty) draw();last=now;
      }
      requestAnimationFrame(frame);
    }
    function motionLabel(){
      const label=paused?'Play wing animation':'Pause wing animation';
      $('#toggle-motion').setAttribute('aria-label',label);$('#toggle-motion').title=label;
      $('#toggle-motion').innerHTML=paused?'<svg viewBox="0 0 20 20" aria-hidden="true"><path d="m7 4 9 6-9 6Z" fill="currentColor"/></svg>':'<svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7 5v10M13 5v10" stroke="currentColor" stroke-width="2"/></svg>';
      dirty=true;
    }
    $('#toggle-motion').addEventListener('click',()=>{paused=!paused;motionLabel();});
    motionPreference.addEventListener('change',e=>{if(e.matches)paused=true;motionLabel();});
    $$('[data-wing]').forEach(button=>button.addEventListener('click',()=>{
      $$('[data-wing]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));select(button.dataset.wing);
    }));
    canvas.addEventListener('pointerdown',e=>{dragging=true;pointer={x:e.clientX,y:e.clientY};canvas.setPointerCapture(e.pointerId);canvas.classList.add('dragging');});
    canvas.addEventListener('pointermove',e=>{if(!dragging)return;yaw+=(e.clientX-pointer.x)*.007;elevation=Math.max(-.45,Math.min(1.25,elevation+(e.clientY-pointer.y)*.006));pointer={x:e.clientX,y:e.clientY};dirty=true;});
    const end=()=>{dragging=false;canvas.classList.remove('dragging');};canvas.addEventListener('pointerup',end);canvas.addEventListener('pointercancel',end);
    canvas.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(e.key))return;e.preventDefault();if(e.key==='ArrowLeft')yaw-=.1;if(e.key==='ArrowRight')yaw+=.1;if(e.key==='ArrowUp')elevation=Math.min(1.25,elevation+.1);if(e.key==='ArrowDown')elevation=Math.max(-.45,elevation-.1);dirty=true;});
    new ResizeObserver(resize).observe(canvas);
    new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;if(visible)dirty=true;},{rootMargin:'80px'}).observe(stage);
    select('nominal');resize();motionLabel();draw();requestAnimationFrame(frame);
  }
  fetch('./assets/wings.json').then(r=>{if(!r.ok)throw new Error('Wings unavailable');return r.json();}).then(initWings).catch(()=>{
    $('#toggle-motion').hidden=true;$$('[data-wing]').forEach(b=>b.disabled=true);$('.stage-caption').textContent='Recorded nominal-load wing surface';
  });
})();
