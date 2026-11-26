/* WingBench project page. All scientific values come from the public release. */
(() => {
  'use strict';
  const $ = (s) => document.querySelector(s);
  const $$ = (s) => [...document.querySelectorAll(s)];
  const motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)');
  const svgNS = 'http://www.w3.org/2000/svg';
  const svg = (tag, attrs = {}, text = '') => {
    const node = document.createElementNS(svgNS, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, String(value));
    if (text) node.textContent = text;
    return node;
  };

  function createExplorer(selector, config) {
    const root = $(selector);
    const find = role => root.querySelector(`[data-role="${role}"]`);
    const all = selector => [...root.querySelectorAll(selector)];
    const {methods, checkpoints, ticks} = config;
    const state = {task:'nominal', metric:'quality', index:3, data:null};

    function render() {
      if (!state.data) return;
      const data = state.data[state.task];
      const query = checkpoints[state.index];
      const reliability = state.metric === 'reliability';
      const queryText = `${query} CFD ${query === 1 ? 'query' : 'queries'}`;
      find('query-output').textContent = queryText;
      find('query-budget').setAttribute('aria-valuetext', queryText);
      find('checkpoint').textContent = query;
      find('metric-label').textContent = reliability ? 'Valid recommendations (%)' : 'Mean lift-to-drag ratio';
      find('chart-title').textContent = `${config.label} recommendation ${state.metric}, ${state.task} load`;
      const chart = find('chart');
      [...chart.children].filter(n => !['title','desc'].includes(n.tagName)).forEach(n => n.remove());
      const compact = window.matchMedia('(max-width:480px)').matches;
      const W=compact?400:620, H=compact?280:310, L=46, R=22, T=23, B=35;
      chart.setAttribute('viewBox', `0 0 ${W} ${H}`);
      const qualityValues = methods.flatMap(m => data[m.key].map(p => p.quality)).filter(Number.isFinite);
      const lo = reliability ? 0 : qualityValues.length ? Math.floor(Math.min(...qualityValues)-.65) : 0;
      const hi = reliability ? 100 : qualityValues.length ? Math.ceil(Math.max(...qualityValues)+.65) : 1;
      const px = x => L+(W-L-R)*x/checkpoints[checkpoints.length-1];
      const py = y => H-B-(H-B-T)*(y-lo)/(hi-lo);
      const value = p => reliability ? p.valid/p.runs*100 : p.quality;
      for (let i=0; i<=4; i++) {
        const y=lo+(hi-lo)*i/4;
        chart.append(svg('line',{x1:L,x2:W-R,y1:py(y),y2:py(y),stroke:'#dfe4e8','stroke-width':1}));
        chart.append(svg('text',{x:L-12,y:py(y)+4,'text-anchor':'end'},reliability?`${Math.round(y)}`:y.toFixed(hi-lo>10?0:1)));
      }
      ticks.forEach(x => chart.append(svg('text',{x:px(x),y:H-9,'text-anchor':'middle'},String(x))));
      chart.append(svg('line',{x1:px(query),x2:px(query),y1:T-6,y2:H-B+3,stroke:'#97a5b0','stroke-dasharray':'3 5'}));
      for (const method of methods) {
        let path='', active=false;
        for (const point of data[method.key]) {
          const y=value(point);
          if (y===null) {active=false;continue;}
          path+=`${active?'L':'M'}${px(point.query).toFixed(2)},${py(y).toFixed(2)} `;
          active=true;
        }
        chart.append(svg('path',{d:path,fill:'none',stroke:method.color,'stroke-width':2.3,'stroke-linejoin':'round','stroke-linecap':'round','stroke-dasharray':method.dash}));
        for (const point of data[method.key]) {
          const y=value(point);
          if (y===null) continue;
          const selected=point.query===query;
          const mark=svg('circle',{cx:px(point.query),cy:py(y),r:selected?5:2.7,fill:selected?method.color:'#fff',stroke:method.color,'stroke-width':selected?2:1.6});
          mark.append(svg('title',{},`${method.name}: query ${point.query}, ${point.valid}/${point.runs} valid, mean L/D ${point.quality===null?'undefined':point.quality.toFixed(2)}`));
          chart.append(mark);
        }
      }
      const summaries=find('summaries');
      summaries.replaceChildren();
      const current=methods.map(method => ({...method,...data[method.key].find(p => p.query===query)}));
      for (const method of current) {
        const row=document.createElement('div');
        row.className='method-summary';
        row.innerHTML=`<p class="method-summary-title"><svg class="series-swatch" viewBox="0 0 28 10" aria-hidden="true"><path d="M1 5h26" stroke="${method.color}" stroke-width="2.4" stroke-dasharray="${method.dash}"/></svg>${method.name}</p><div class="summary-numbers"><span class="summary-quality">${method.quality===null?'—':method.quality.toFixed(2)}<small>L/D</small></span><span class="summary-valid">${method.valid}/${method.runs} valid</span></div>`;
        summaries.append(row);
      }
      const scored=current.filter(m => m.quality!==null);
      if (scored.length) {
        const max=Math.max(...scored.map(m => m.quality));
        const best=scored.filter(m => m.quality===max);
        find('insight').textContent=best.length===1
          ? `At ${query} ${query===1?'query':'queries'}, ${best[0].name} has the highest mean L/D (${max.toFixed(2)}), among its ${best[0].valid} valid recommendations out of 10. Compare validity alongside quality: each mean uses only that method’s successful runs.`
          : `At ${query} ${query===1?'query':'queries'}, ${best.map(m=>m.name).join(' and ')} share the highest reported mean L/D (${max.toFixed(2)}). Each mean uses only that method’s valid recommendations.`;
      } else {
        find('insight').textContent='No method returned a valid recommendation at this checkpoint, so mean L/D is undefined.';
      }
      find('table-caption').textContent=`${state.task[0].toUpperCase()+state.task.slice(1)} load · ten runs per method`;
      const body=find('table-body');body.replaceChildren();
      for (const q of checkpoints) for (const method of methods) {
        const point=data[method.key].find(p=>p.query===q), row=document.createElement('tr');
        for (const text of [q,method.name,`${point.valid}/${point.runs}`,point.quality===null?'Undefined (no valid recommendations)':point.quality.toFixed(2)]) {
          const cell=document.createElement('td');cell.textContent=text;row.append(cell);
        }
        body.append(row);
      }
    }
    all('[data-task]').forEach(button=>button.addEventListener('click',()=>{
      state.task=button.dataset.task;
      all('[data-task]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
      render();
    }));
    all('[data-metric]').forEach(button=>button.addEventListener('click',()=>{
      state.metric=button.dataset.metric;
      all('[data-metric]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
      render();
    }));
    find('query-budget').addEventListener('input',event=>{state.index=Number(event.target.value);render();});
    let resizeTimer;
    window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(render,100);});
    fetch(config.file).then(response=>{if(!response.ok)throw new Error('Results unavailable');return response.json();}).then(data=>{state.data=data;render();}).catch(()=>{
      find('insight').textContent='The interactive data could not load. Download the result table below to view the recorded values.';
      find('query-budget').disabled=true;
    });
  }

  createExplorer('#results', {
    label:'Baseline', file:'./assets/results.json', checkpoints:[0,1,2,4,8,16,32], ticks:[0,4,8,16,32],
    methods:[
      {key:'CMA-ES no feedback',name:'CMA-ES · fixed archive',color:'#7c8790',dash:'6 5'},
      {key:'CMA-ES selection only',name:'CMA-ES · archive reordering',color:'#48535d',dash:'2 4'},
      {key:'CMA-ES feedback-guided',name:'CMA-ES · feedback-guided',color:'#365f80',dash:''},
      {key:'DE feedback-guided',name:'Differential evolution',color:'#597763',dash:'10 4'},
      {key:'SMAC3',name:'SMAC3',color:'#986b43',dash:'9 3 2 3'}
    ]
  });
  createExplorer('#agent-results', {
    label:'Agent', file:'./assets/agent-results.json', checkpoints:[0,1,2,4], ticks:[0,1,2,4],
    methods:[
      {key:'Open-ended research',name:'Open-ended research',color:'#365f80',dash:''},
      {key:'Reflection',name:'Explicit reflection',color:'#48535d',dash:'6 5'},
      {key:'Planning',name:'Contingent planning',color:'#597763',dash:'2 4'}
    ]
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
      const targetCl={low:0.15,nominal:0.41,high:0.66}[task];
      $('#wing-method').textContent=`${task[0].toUpperCase()+task.slice(1)} load · ${method} · Query ${mesh.query}`;
      canvas.setAttribute('aria-label',`Highest-scoring ${task}-load wing found across all methods, target lift coefficient ${targetCl}. ${method}, query ${mesh.query}, L/D ${mesh.ld.toFixed(2)}. Sweep ${mesh.sweep.toFixed(1)} degrees, aspect ratio ${mesh.aspectRatio.toFixed(2)}. Drag or use arrow keys to rotate.`);
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
