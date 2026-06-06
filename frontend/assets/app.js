// Ocean Digital Earth — Vue 3 + Windy rendering + pixel-level land mask
'use strict';
window.CESIUM_BASE_URL = 'https://cdn.jsdelivr.net/npm/cesium@1.122/Build/Cesium/';
const GEOSERVER_WMS_URL = '/geoserver/wms';
const GEOSERVER_WFS_URL = '/geoserver/ows';
const GEOSERVER_BASE_LAYER = 'ne:world';
const GEOSERVER_LAND_LAYER = 'ne:countries';
const LOCAL_EARTH_TEXTURE_URL = '/assets/earth-local.png';

// ── WINDY COLOR SCALES ────────────────────────────────────────────────────────
const WINDY_SCALES = {
  temperature:[
    [0.00,[36,40,148]],[0.10,[62,120,210]],[0.22,[90,185,240]],
    [0.35,[140,230,200]],[0.50,[240,245,110]],[0.65,[255,185,50]],
    [0.78,[255,110,30]],[0.90,[220,45,20]],[1.00,[150,15,10]]],
  salinity:[
    [0.00,[50,100,200]],[0.25,[70,160,230]],[0.50,[150,210,210]],
    [0.75,[220,180,110]],[1.00,[170,80,30]]],
  chlorophyll:[
    [0.00,[13,8,60]],[0.20,[10,60,120]],[0.40,[0,120,100]],
    [0.60,[30,180,60]],[0.80,[180,230,30]],[1.00,[240,240,40]]],
  wave:[
    [0.00,[10,20,80]],[0.25,[30,80,180]],[0.50,[80,160,240]],
    [0.75,[180,220,255]],[1.00,[255,255,255]]],
  wind:[
    [0.00,[35,23,60]],[0.10,[22,52,120]],[0.22,[0,100,180]],
    [0.35,[0,160,200]],[0.50,[0,220,180]],[0.65,[100,240,80]],
    [0.75,[220,230,30]],[0.85,[255,160,0]],[0.95,[240,60,20]],[1.00,[180,0,80]]],
  relief:[
    [0.00,[8,24,80]],[0.25,[20,80,160]],[0.50,[60,140,200]],
    [0.75,[140,200,220]],[1.00,[220,240,250]]],
  default:[
    [0.00,[29,78,216]],[0.25,[8,145,178]],[0.50,[34,197,94]],
    [0.75,[253,224,71]],[1.00,[220,38,38]]],
};
function pickScale(d){
  if(!d) return WINDY_SCALES.default;
  const t=`${d.dataset||''} ${d.variable||''} ${d.long_name||''} ${d.units||''}`.toLowerCase();
  if(d.category==='relief') return WINDY_SCALES.relief;
  if(/sst|temperature|temp|thetao|analysed/.test(t)) return WINDY_SCALES.temperature;
  if(/salinity|sss|psal/.test(t)) return WINDY_SCALES.salinity;
  if(/chlor|chl/.test(t)) return WINDY_SCALES.chlorophyll;
  if(/wave|swh|vhm0|significant/.test(t)) return WINDY_SCALES.wave;
  if(/wind|speed|current|uwnd|vwnd|water_u|water_v/.test(t)) return WINDY_SCALES.wind;
  return WINDY_SCALES.default;
}
function sampleScale(sc,t){
  t=Math.max(0,Math.min(1,t));
  for(let i=0;i<sc.length-1;i++){
    const [t0,c0]=sc[i],[t1,c1]=sc[i+1];
    if(t<=t1){const f=(t-t0)/Math.max(1e-9,t1-t0);return c0.map((v,k)=>Math.round(v+(c1[k]-v)*f));}
  }
  return sc[sc.length-1][1];
}
const colorRgb=(t,d)=>sampleScale(pickScale(d),t);
const colorAlpha=(t,a,d)=>{const [r,g,b]=colorRgb(t,d);return `rgba(${r},${g},${b},${a})`;};
function legendCss(d){
  const sc=pickScale(d);
  return 'linear-gradient(90deg,'+sc.map(([t,[r,g,b]])=>`rgb(${r},${g},${b}) ${(t*100).toFixed(0)}%`).join(',')+')';
}

// ── GRID SAMPLING + PIXEL-LEVEL LAND MASK ────────────────────────────────────
function isNull(v,d){
  if(v===null||v===undefined||Number.isNaN(Number(v))) return true;
  if(d?.category==='relief'&&v>0) return true;
  return false;
}
/**
 * land_mask_hires: row 0=south, row N=north (backend convention).
 * Canvas: ny=0=top=north → must flip: lm_row = (1-ny)*(rows-1)
 */
function isHiresLand(d,nx,ny){
  const lm=d?.land_mask_hires;
  if(!lm||!lm.length) return false;
  const rows=lm.length,cols=lm[0]?.length||0;
  if(!rows||!cols) return false;
  const li=Math.round(Math.max(0,Math.min(rows-1,(1-ny)*(rows-1))));
  const lj=Math.round(Math.max(0,Math.min(cols-1,nx*(cols-1))));
  return lm[li]?.[lj]===true;
}
function sampleGrid(values,nx,ny,d){
  if(isHiresLand(d,nx,ny)) return null;
  const rows=values.length,cols=values[0]?.length||0;
  if(!rows||!cols) return null;
  const x=Math.max(0,Math.min(cols-1,nx*(cols-1)));
  const y=Math.max(0,Math.min(rows-1,ny*(rows-1)));
  const ni=Math.round(y),nj=Math.round(x);
  const vNN=values[ni]?.[nj];
  if(isNull(vNN,d)) return null;
  const j0=Math.floor(x),j1=Math.min(cols-1,j0+1);
  const i0=Math.floor(y),i1=Math.min(rows-1,i0+1);
  const v00=values[i0]?.[j0],v10=values[i0]?.[j1],v01=values[i1]?.[j0],v11=values[i1]?.[j1];
  if([v00,v10,v01,v11].some(v=>isNull(v,d))) return vNN;
  const fx=x-j0,fy=y-i0;
  return (v00*(1-fx)+v10*fx)*(1-fy)+(v01*(1-fx)+v11*fx)*fy;
}
function sampleGridNN(values,nx,ny){
  const rows=values.length,cols=values[0]?.length||0;
  if(!rows||!cols) return null;
  const i=Math.round(Math.max(0,Math.min(rows-1,ny*(rows-1))));
  const j=Math.round(Math.max(0,Math.min(cols-1,nx*(cols-1))));
  return values[i]?.[j]??null;
}

// ── CANVAS RENDERERS ──────────────────────────────────────────────────────────
const mkCanvas=(w,h)=>{const c=document.createElement('canvas');c.width=w;c.height=h;return c;};

function gridCanvas(data,W,H,alphaScale=1.0){
  const c=mkCanvas(W,H),ctx=c.getContext('2d');
  const img=ctx.createImageData(W,H),px=img.data;
  const {min,max}=data.stats,range=Math.max(1e-9,max-min);
  for(let y=0;y<H;y++){
    const ny=H<=1?0:y/(H-1);
    for(let x=0;x<W;x++){
      const nx=W<=1?0:x/(W-1);
      const v=sampleGrid(data.values,nx,ny,data);
      if(v===null) continue;
      const [r,g,b]=colorRgb((v-min)/range,data),k=(y*W+x)*4;
      px[k]=r;px[k+1]=g;px[k+2]=b;px[k+3]=Math.floor(200*alphaScale);
    }
  }
  ctx.putImageData(img,0,0);return c;
}

function pointCanvas(data,W,H){
  const c=mkCanvas(W,H),ctx=c.getContext('2d');
  const rows=data.values.length,cols=data.values[0]?.length||0;
  const {min,max}=data.stats,range=Math.max(1e-9,max-min);
  const skip=Math.max(1,Math.ceil(Math.max(rows,cols)/50));
  for(let i=0;i<rows;i+=skip) for(let j=0;j<cols;j+=skip){
    const nx=cols<=1?0:j/(cols-1),ny=rows<=1?0:i/(rows-1);
    if(isHiresLand(data,nx,ny)) continue;
    const v=data.values[i][j];if(isNull(v,data)) continue;
    const t=(v-min)/range,r=2.5+t*8;
    ctx.beginPath();ctx.fillStyle=colorAlpha(t,0.78,data);
    ctx.arc(((j+.5)/cols)*W,((i+.5)/rows)*H,r,0,Math.PI*2);
    ctx.fill();ctx.strokeStyle='rgba(255,255,255,0.38)';ctx.lineWidth=0.8;ctx.stroke();
  }
  return c;
}

function contourCanvas(data,W,H){
  const c=gridCanvas(data,W,H),ctx=c.getContext('2d');
  const rows=data.values.length,cols=data.values[0]?.length||0;
  const {min,max}=data.stats,cw=W/Math.max(1,cols-1),ch=H/Math.max(1,rows-1);
  ctx.lineWidth=1.5;ctx.shadowColor='rgba(0,0,0,.6)';ctx.shadowBlur=2;
  for(let k=1;k<=9;k++){
    const level=min+(max-min)*(k/10);
    ctx.strokeStyle=k%2?'rgba(238,253,251,.78)':'rgba(70,240,222,.85)';
    for(let i=0;i<rows-1;i++) for(let j=0;j<cols-1;j++){
      const v=[data.values[i][j],data.values[i][j+1],data.values[i+1]?.[j+1],data.values[i+1]?.[j]];
      if(v.some(x=>isNull(x,data))) continue;
      const pts=marchingCell(v[0],v[1],v[2],v[3],level,j*cw,i*ch,cw,ch);
      for(let p=0;p<pts.length;p+=2){
        ctx.beginPath();ctx.moveTo(pts[p].x,pts[p].y);ctx.lineTo(pts[p+1].x,pts[p+1].y);ctx.stroke();
      }
    }
  }
  ctx.shadowBlur=0;return c;
}

function marchingCell(v00,v10,v11,v01,level,x,y,w,h){
  const ip=(a,b,ax,ay,bx,by)=>{const t=(level-a)/Math.max(1e-9,b-a);return{x:ax+(bx-ax)*t,y:ay+(by-ay)*t};};
  const pts=[];
  if((v00<level)!==(v10<level)) pts.push(ip(v00,v10,x,y,x+w,y));
  if((v10<level)!==(v11<level)) pts.push(ip(v10,v11,x+w,y,x+w,y+h));
  if((v11<level)!==(v01<level)) pts.push(ip(v11,v01,x+w,y+h,x,y+h));
  if((v01<level)!==(v00<level)) pts.push(ip(v01,v00,x,y+h,x,y));
  return pts.length===2?pts:pts.length===4?[pts[0],pts[1],pts[2],pts[3]]:[];
}

function createFallbackEarthTexture(){
  const c=mkCanvas(2048,1024),ctx=c.getContext('2d');
  const g=ctx.createLinearGradient(0,0,0,c.height);
  g.addColorStop(0,'#08254f');g.addColorStop(.42,'#0b6f9f');g.addColorStop(.58,'#0b779a');g.addColorStop(1,'#061f48');
  ctx.fillStyle=g;ctx.fillRect(0,0,c.width,c.height);

  // Procedural ocean-only texture. Coarse land polygons look like giant wedges
  // on the globe, so land/sea separation is handled by dataset masks instead.
  const glow=ctx.createRadialGradient(c.width*.52,c.height*.47,80,c.width*.52,c.height*.47,c.width*.62);
  glow.addColorStop(0,'rgba(116,230,230,.22)');
  glow.addColorStop(.55,'rgba(31,158,201,.10)');
  glow.addColorStop(1,'rgba(1,8,20,0)');
  ctx.fillStyle=glow;ctx.fillRect(0,0,c.width,c.height);

  ctx.strokeStyle='rgba(180,240,255,.13)';ctx.lineWidth=1;
  for(let lon=-180;lon<=180;lon+=30){
    const x=((lon+180)/360)*c.width;
    ctx.beginPath();ctx.moveTo(x,0);ctx.lineTo(x,c.height);ctx.stroke();
  }
  for(let lat=-60;lat<=60;lat+=30){
    const y=((90-lat)/180)*c.height;
    ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(c.width,y);ctx.stroke();
  }
  ctx.strokeStyle='rgba(130,220,235,.08)';ctx.lineWidth=3;
  for(let i=0;i<7;i++){
    const y=c.height*(0.18+i*0.11);
    ctx.beginPath();
    for(let x=0;x<=c.width;x+=16){
      const yy=y+Math.sin((x/c.width)*Math.PI*4+i*.8)*18;
      x?ctx.lineTo(x,yy):ctx.moveTo(x,yy);
    }
    ctx.stroke();
  }
  return c.toDataURL('image/png');
}

// ── WINDY PARTICLE SYSTEM ─────────────────────────────────────────────────────
const PARTICLE_COUNT=2400,FADE_FRAMES=18;
let _particles=[],_animId=null,_viewer=null,_olMap=null,_olLabelsLayer=null,_pCanvas=null,_pData=null;

function particleAlpha(age,maxAge,speedT){
  return Math.min(age/FADE_FRAMES,(maxAge-age)/FADE_FRAMES,1)*(0.55+speedT*0.40);
}
function resetParticle(p,data){
  const b=data.bounds;
  for(let t=0;t<30;t++){
    p.lon=b.west+Math.random()*(b.east-b.west);
    p.lat=b.south+Math.random()*(b.north-b.south);
    if(sampleVec(data,p.lon,p.lat)) break;
  }
  p.age=0;p.maxAge=160+Math.floor(Math.random()*280);p.speed=0.7+Math.random()*1.4;
}
function sampleVec(data,lon,lat){
  if(!data.u_grid||!data.v_grid) return null;
  const b=data.bounds;
  const nx=(lon-b.west)/Math.max(1e-9,b.east-b.west);
  const ny=1-(lat-b.south)/Math.max(1e-9,b.north-b.south);
  if(isHiresLand(data,nx,ny)) return null;
  const u=sampleGridNN(data.u_grid,nx,ny),v=sampleGridNN(data.v_grid,nx,ny);
  if(u===null||v===null) return null;
  return{u,v,speed:Math.sqrt(u*u+v*v)};
}
function geoToScreen(lon,lat){
  if(!_viewer||!_pCanvas) return null;
  try{
    const pos=Cesium.Cartesian3.fromDegrees(lon,lat,0);
    const toWindow=Cesium.SceneTransforms.wgs84ToWindowCoordinates||Cesium.SceneTransforms.worldToWindowCoordinates;
    const sp=toWindow?toWindow(_viewer.scene,pos):null;
    if(!sp) return null;
    const rect=_pCanvas.getBoundingClientRect();
    return{x:sp.x-rect.left,y:sp.y-rect.top};
  }catch{return null;}
}
function startParticleAnimation(data){
  stopParticleAnimation();
  if(!data.u_grid||!data.v_grid) return;
  _pData=data;
  _particles=Array.from({length:PARTICLE_COUNT},()=>{
    const p={lon:0,lat:0,age:0,maxAge:200,speed:1};
    resetParticle(p,data);p.age=Math.floor(Math.random()*p.maxAge);return p;
  });
  const canvas=_pCanvas;if(!canvas) return;
  const sizeCanvas=()=>{
    const r=canvas.parentElement?.getBoundingClientRect();
    if(r&&(canvas.width!==Math.round(r.width)||canvas.height!==Math.round(r.height))){
      canvas.width=Math.round(r.width);canvas.height=Math.round(r.height);
    }
  };
  const ctx=canvas.getContext('2d');
  sizeCanvas();ctx.clearRect(0,0,canvas.width,canvas.height);
  const ws=WINDY_SCALES.wind,sm=Math.max(0.01,data.stats.max);
  const b=data.bounds;
  const tick=()=>{
    sizeCanvas();
    ctx.globalCompositeOperation='destination-in';
    ctx.fillStyle='rgba(255,255,255,0.90)';
    ctx.fillRect(0,0,canvas.width,canvas.height);
    ctx.globalCompositeOperation='source-over';
    for(const p of _particles){
      const vel=sampleVec(data,p.lon,p.lat);
      if(!vel){resetParticle(p,data);continue;}
      const prevSc=geoToScreen(p.lon,p.lat);
      p.lon+=vel.u*0.0018*p.speed;p.lat+=vel.v*0.0018*p.speed;p.age++;
      const nextSc=geoToScreen(p.lon,p.lat);
      if(p.age>p.maxAge||p.lon<b.west||p.lon>b.east||p.lat<b.south||p.lat>b.north
         ||!prevSc||!nextSc||Math.hypot(nextSc.x-prevSc.x,nextSc.y-prevSc.y)>60){
        resetParticle(p,data);continue;
      }
      const t=Math.min(1,vel.speed/sm);
      const alpha=particleAlpha(p.age,p.maxAge,t);
      const [r,g,bv]=sampleScale(ws,t);
      ctx.strokeStyle=`rgba(${r},${g},${bv},${alpha})`;
      ctx.lineWidth=1.0+t*1.6;ctx.lineCap='round';
      ctx.beginPath();ctx.moveTo(prevSc.x,prevSc.y);ctx.lineTo(nextSc.x,nextSc.y);ctx.stroke();
    }
    _animId=requestAnimationFrame(tick);
  };
  _animId=requestAnimationFrame(tick);
}
function stopParticleAnimation(){
  if(_animId){cancelAnimationFrame(_animId);_animId=null;}
  if(_pCanvas){const ctx=_pCanvas.getContext('2d');ctx.clearRect(0,0,_pCanvas.width,_pCanvas.height);}
}

// ── DOMAIN / UI CONFIG ────────────────────────────────────────────────────────
const DOMAINS=[
  {id:'auto',      label:'自动',icon:'🔮',title:'根据问题自动判断',
   suggestions:['请评估当前框选区域的综合海洋环境','该海域近期有哪些主要风险？']},
  {id:'marine',    label:'海洋',icon:'🌊',title:'海表温度、盐度、叶绿素等',
   suggestions:['台湾海峡海表温度异常如何？','这片海域叶绿素浓度偏高吗？','目前海表盐度是否正常？']},
  {id:'stargazing',label:'观星',icon:'🌟',title:'月相、光污染、云量',
   suggestions:['今晚台湾东部适合观星吗？','这片海域光污染程度如何？','当前月相是否影响观星？']},
  {id:'biology',   label:'生物',icon:'🐠',title:'珊瑚白化、赤潮、栖息地',
   suggestions:['这片海域有珊瑚白化风险吗？','目前是否有赤潮风险？','这片海域适合哪些鱼类栖息？']},
  {id:'navigation',label:'航行',icon:'⚓',title:'浪高、风级、航行安全',
   suggestions:['台湾海峡适合小型渔船出海吗？','当前海况对集装箱船只安全吗？','近期浪高风级评估如何？']},
];
const DOMAIN_MAP=Object.fromEntries(DOMAINS.map(d=>[d.id,d]));
const RENDER_MODES=[
  {id:'heatmap',  label:'填色',  title:'Windy 风格连续色带填色'},
  {id:'particles',label:'粒子',  title:'Windy 风格动态粒子流（需矢量场 u/v）'},
  {id:'contour',  label:'等值线',title:'等值线叠加'},
  {id:'points',   label:'点图',  title:'采样点符号图'},
];
const PIPELINE_LABELS={
  intent:'意图',planner:'规划',retrieval:'检索',context:'数据',
  reasoning:'推理',visualization:'渲染',report:'报告',evaluator:'评测'
};

// ── VUE 3 APP ─────────────────────────────────────────────────────────────────
const{createApp}=Vue;
createApp({
  data(){return{
    healthText:'服务状态检查中…',
    variableList:[],selectedVar:'',
    renderMode:'heatmap',stepVal:0,maxPoints:9000,
    selectMode:false,selection:{west:119,east:122,south:23,north:26},
    gridData:null,cesiumEntity:null,selectionEntity:null,olLayer:null,
    stats:null,vmin:'-',vmax:'-',legendCssStr:'',
    layerOpacity:0.82,queryLoading:false,
    oceanStatus:'地球可旋转浏览；开启框选后在地球上拖拽选择区域。',
    apiStatus:'接口：等待请求。',
    chatOpen:false,reportVisible:false,reportHtml:'',
    reportMeta:'等待生成。',reportStatus:'等待提问。',
    messages:[{role:'ai',text:'可以询问海洋热浪、酸化、渔业风险，框选区域后 AI 会自动获取数据。'}],
    domain:'auto',question:'请结合海表温度和海洋热浪知识，评估目标海域对珊瑚礁和渔业的风险，并形成规划建议',
    backend:'auto',topk:6,streaming:false,streamEs:null,
    agentTrace:[],agentSummary:null,agentSummaryHtml:'',evidence:[],
    evaluation:null,
    geoApiReady:false,
    pipelineSteps:{intent:'',planner:'',retrieval:'',context:'',reasoning:'',visualization:'',report:'',evaluator:''},
    // ── 知识库管理 ──
    knowledgeOpen:false,
    knowledgeDocs:{local_count:0,ragflow_count:0,local_docs:[],ragflow_docs:[]},
    kbFile:null, kbFileName:'', kbUploading:false, kbUploadMsg:'', kbUploadOk:false,
    // ── 时间轴 / 深度层 ──
    timeIndex:0, depthIndex:0,
    timeCount:0, depthCount:0,
    timeValues:[], depthValues:[], depthUnits:'m',
    playing:false, playTimer:null, playInterval:1000,
    datasetMeta:{},  // dataset_id → {time_count,depth_count,depth_values,time_units,...}
  };},

  computed:{
    domains(){return DOMAINS;},renderModes(){return RENDER_MODES;},
    pipelineLabels(){return PIPELINE_LABELS;},
    suggestions(){return DOMAIN_MAP[this.domain]?.suggestions||[];},
    currentMeta(){return this.variableList.find(v=>v.key===this.selectedVar)?.meta||null;},
    allowedModes(){return new Set(this.currentMeta?.render_modes||['heatmap','contour','points']);},
    legendStyle(){return{background:this.legendCssStr};},
    selectedTimeLabel(){
      if(!this.timeValues||!this.timeValues.length) return '';
      const v=this.timeValues[this.timeIndex];
      if(v===undefined||v===null) return '';
      // 尝试将 "days since ..." 解析为可读日期（简单处理）
      return typeof v==='string' ? v : String(v);
    },
    qualityCards(){
      const m=this.evaluation?.metrics||{};
      const ret=m.retrieval||{}, ev=m.evidence||{}, ans=m.answer||{}, tools=m.tools||{}, sys=m.system||{};
      return [
        {label:'总分',value:this.evaluation?.score??'-',sub:this.evaluation?.grade||'-'},
        {label:'检索',value:`${ret.kept_count??0}/${ret.candidate_count??0}`,sub:ret.backend||'-'},
        {label:'引用覆盖',value:this._pct(ev.citation_coverage),sub:`证据 ${ev.evidence_count??0}`},
        {label:'数据支撑',value:this._pct(ans.data_grounding),sub:`问题 ${ans.critic_issue_count??0}`},
        {label:'工具',value:tools.tool_call_count??0,sub:tools.tool_success_rate==null?'未记录':this._pct(tools.tool_success_rate)},
        {label:'耗时',value:sys.elapsed_ms?`${(sys.elapsed_ms/1000).toFixed(1)}s`:'-',sub:`tokens ${sys.token_usage?.total_tokens??0}`},
      ];
    },
  },

  watch:{
    renderMode(){if(this.gridData) this.applyRender(this.gridData).catch(()=>{});},
    selectedVar(key){
      // 切换数据集时同步 time/depth 元数据
      const dsId=(key||'').split('::')[0];
      const meta=this.datasetMeta[dsId]||{};
      this.timeCount=meta.time_count||0;
      this.depthCount=meta.depth_count||0;
      this.timeValues=meta.time_values||[];
      this.depthValues=meta.depth_values||[];
      this.depthUnits=meta.depth_units||'m';
      this.timeIndex=0; this.depthIndex=0;
      this.stopPlayback();
    },
  },

  mounted(){
    _pCanvas=this.$refs.particleCanvas;
    this.checkHealth();this.checkGeoApi();this.loadDatasets();this.loadKbDocs();
    this.$nextTick(()=>requestAnimationFrame(()=>{this.initCesium();this.initMap();this._drawSelectionRect(this.selection);this.resizeAll();}));
    window.addEventListener('resize',()=>{
      if(_pCanvas){_pCanvas.width=0;_pCanvas.height=0;}
      this.resizeAll();
    });
  },

  methods:{
    // ── Cesium ──
    initCesium(){
      _viewer=new Cesium.Viewer('earth',{
        baseLayer:false,baseLayerPicker:false,geocoder:false,homeButton:false,
        sceneModePicker:false,navigationHelpButton:false,
        animation:false,timeline:false,fullscreenButton:false,
        infoBox:false,selectionIndicator:false,
        terrainProvider:new Cesium.EllipsoidTerrainProvider(),
        requestRenderMode:false,targetFrameRate:60,
      });
      _viewer.imageryLayers.removeAll();
      // Layer 0: 本地离线底图（无 CDN 时保底）
      _viewer.imageryLayers.addImageryProvider(
        new Cesium.SingleTileImageryProvider({url:LOCAL_EARTH_TEXTURE_URL,rectangle:Cesium.Rectangle.MAX_VALUE}));
      // Layer 1: OSM 在线瓦片（有地名文字，无 maximumLevel 限制）
      try{
        _viewer.imageryLayers.addImageryProvider(new Cesium.UrlTemplateImageryProvider({
          url:'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
          credit:'© OpenStreetMap contributors',
          // 不设 maximumLevel，允许任意放大级别加载文字标注
        }));
      }catch(e){console.warn('OSM tiles unavailable, falling back to local texture');}
      _viewer.scene.globe.enableLighting=true;
      _viewer.scene.globe.showGroundAtmosphere=true;
      _viewer.scene.skyAtmosphere.show=true;
      _viewer.scene.screenSpaceCameraController.enableTilt=true;
      _viewer.scene.skyBox.show=false;_viewer.scene.sun.show=false;_viewer.scene.moon.show=false;
      _viewer.scene.backgroundColor=Cesium.Color.fromCssColorString('#020409');
      _viewer.camera.setView({destination:Cesium.Cartesian3.fromDegrees(121,23,16000000)});
      this.resizeAll();
      const h=new Cesium.ScreenSpaceEventHandler(_viewer.canvas);
      let anc=null;
      h.setInputAction(e=>{if(!this.selectMode)return;const g=this._pickGlobe(e.position);if(g)anc=g;},Cesium.ScreenSpaceEventType.LEFT_DOWN);
      h.setInputAction(e=>{if(!this.selectMode||!anc)return;const g=this._pickGlobe(e.endPosition);if(g)this.setSelection(anc,g);},Cesium.ScreenSpaceEventType.MOUSE_MOVE);
      h.setInputAction(()=>{anc=null;},Cesium.ScreenSpaceEventType.LEFT_UP);
    },
    _pickGlobe(pos){
      const ray=_viewer.camera.getPickRay(pos);
      const hit=_viewer.scene.globe.pick(ray,_viewer.scene);
      if(!hit)return null;
      const c=Cesium.Ellipsoid.WGS84.cartesianToCartographic(hit);
      return{lon:Cesium.Math.toDegrees(c.longitude),lat:Cesium.Math.toDegrees(c.latitude)};
    },
    resizeAll(){requestAnimationFrame(()=>{if(_viewer){_viewer.resize();_viewer.scene.requestRender();}if(_olMap)_olMap.updateSize();});},

    // ── OpenLayers ──
    initMap(){
      // CartoDB 分离：底图（无标注） + 数据层（中间） + 标注层（最顶）
      const cartoBase = new ol.layer.Tile({
        source: new ol.source.XYZ({
          url:'https://{a-c}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png',
          attributions:'© OpenStreetMap © CartoDB',
        }),
        zIndex:0,
      });
      _olLabelsLayer = new ol.layer.Tile({
        source: new ol.source.XYZ({
          url:'https://{a-c}.basemaps.cartocdn.com/light_only_labels/{z}/{x}/{y}{r}.png',
          attributions:'© OpenStreetMap © CartoDB',
        }),
        zIndex:100,  // 始终在热力图之上
      });
      _olMap=new ol.Map({
        target:'map',
        layers:[cartoBase, _olLabelsLayer],
        view:new ol.View({center:ol.proj.fromLonLat([121,24]),zoom:7}),
        controls:ol.control.defaults.defaults({zoom:false}),
      });
    },

    // ── Health ──
    async checkHealth(){
      try{const d=await fetch('/api/health').then(r=>r.json());
        this.healthText=`Demo=${d.status} | LLM=${d.llm?.configured?d.llm.model:'本地回退'}`;}
      catch{this.healthText='服务检查失败';}
    },
    async checkGeoApi(){
      try{const d=await fetch('/geo-api/api/health').then(r=>r.json());this.geoApiReady=d.status==='ok';}
      catch{this.geoApiReady=false;}
    },

    // ── Datasets ──
    async loadDatasets(){
      try{
        const d=await fetch('/api/ocean/datasets').then(r=>r.json());
        const list=[];
        const meta={};
        for(const ds of(d.datasets||[])){
          // 存储每个数据集的 time/depth 元数据
          meta[ds.id]={
            time_count:  ds.time_count  || 0,
            depth_count: ds.depth_count || 0,
            time_dim:    ds.time_dim    || '',
            depth_dim:   ds.depth_dim   || '',
            time_values: ds.time_values || [],
            depth_values:ds.depth_values|| [],
            time_units:  ds.time_units  || '',
            depth_units: ds.depth_units || 'm',
          };
          for(const v of(ds.variables||[]))
            list.push({key:`${ds.id}::${v.name}`,label:`${ds.id} / ${v.name} — ${v.long_name||''} (${v.units||''})`,meta:v});
        }
        this.datasetMeta=meta;
        this.variableList=list;
        const preferred=list.find(v=>v.key==='sst_oisst_taiwan_small::sst')
          ||list.find(v=>/_taiwan_small::/.test(v.key))
          ||list.find(v=>!v.key.startsWith('noaa_'))
          ||list[0];
        if(preferred)this.selectedVar=preferred.key;
      }catch(e){this.apiStatus=`数据集加载失败：${e.message}`;}
    },
    async syncData(){
      this.apiStatus='同步中…';
      try{await fetch('/api/sync',{method:'POST'});await this.loadDatasets();this.apiStatus='同步完成。';}
      catch(e){this.apiStatus=`同步失败：${e.message}`;}
    },

    // ── 知识库管理 ──────────────────────────────────────────────────────────
    async loadKbDocs(){
      try{
        const d=await fetch('/api/rag/documents').then(r=>r.json());
        this.knowledgeDocs={
          local_count:  d.local?.count  || 0,
          ragflow_count:d.ragflow?.count || 0,
          local_docs:   d.local?.documents  || [],
          ragflow_docs: d.ragflow?.documents || [],
        };
      }catch(e){/* 静默失败 */}
    },
    onKbFileChange(e){
      const f=e.target.files?.[0];
      if(f){this.kbFile=f;this.kbFileName=f.name;this.kbUploadMsg='';}
    },
    async uploadKbDoc(){
      if(!this.kbFile)return;
      this.kbUploading=true;this.kbUploadMsg='';
      try{
        const fd=new FormData();
        fd.append('file',this.kbFile);
        const res=await fetch('/api/rag/upload',{method:'POST',body:fd});
        const d=await res.json();
        if(!res.ok) throw new Error(d.error||res.statusText);
        this.kbUploadOk=true;
        this.kbUploadMsg=d.message||`上传成功：${d.filename}`;
        this.kbFile=null;this.kbFileName='';
        await this.loadKbDocs();
      }catch(e){
        this.kbUploadOk=false;
        this.kbUploadMsg=`上传失败：${e.message}`;
      }finally{this.kbUploading=false;}
    },
    async syncKbLocal(){
      try{
        const d=await fetch('/api/rag/sync-local',{method:'POST'}).then(r=>r.json());
        this.kbUploadOk=true;
        this.kbUploadMsg=d.message||`扫描完成，共 ${d.local_count} 篇文档。`;
        await this.loadKbDocs();
      }catch(e){this.kbUploadOk=false;this.kbUploadMsg=`扫描失败：${e.message}`;}
    },

    // ── Selection ──
    toggleSelect(){
      this.selectMode=!this.selectMode;
      if(_viewer)_viewer.scene.screenSpaceCameraController.enableRotate=!this.selectMode;
      this.oceanStatus=this.selectMode?'框选模式：在地球上拖拽选择海域。':'地球可旋转浏览。';
    },
    setSelection(a,b){
      this.selection={west:Math.min(a.lon,b.lon),east:Math.max(a.lon,b.lon),south:Math.min(a.lat,b.lat),north:Math.max(a.lat,b.lat)};
      const s=this.selection;
      this.oceanStatus=`已框选：${s.west.toFixed(1)}°–${s.east.toFixed(1)}°E，${s.south.toFixed(1)}°–${s.north.toFixed(1)}°N`;
      this._drawSelectionRect(s);
    },
    _drawSelectionRect(s){
      if(!_viewer)return;
      if(this.selectionEntity)_viewer.entities.remove(this.selectionEntity);
      this.selectionEntity=_viewer.entities.add({rectangle:{
        coordinates:Cesium.Rectangle.fromDegrees(s.west,s.south,s.east,s.north),
        fill:false,outline:true,
        outlineColor:Cesium.Color.fromCssColorString('#f5c84d'),
        outlineWidth:2,height:0,
      }});
    },

    // ── Render mode ──
    setRenderMode(mode){if(this.allowedModes.has(mode))this.renderMode=mode;},

    // ── Query ──
    async queryOcean(){
      if(!this.selectedVar){this.oceanStatus='请先选择数据集/变量。';return;}
      if(!this.selection){this.oceanStatus='请先框选海域。';return;}
      const [dataset,variable]=this.selectedVar.split('::');
      this.oceanStatus='查询海洋要素中…';this.apiStatus='POST /api/ocean/query…';this.queryLoading=true;
      try{
        const t0=Date.now();
        const res=await fetch('/api/ocean/query',{
          method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({dataset,variable,bounds:this.selection,max_points:this.maxPoints,step:this.stepVal||0,time_index:this.timeIndex,depth_index:this.depthIndex}),
        });
        if(!res.ok){const e=await res.json();throw new Error(e.message||e.error||res.statusText);}
        const data=await res.json();
        this.gridData=data;await this.applyRender(data);
        const lm=data.land_mask_hires
          ? '像素掩膜=✓'
          : (data.land_mask_applied>0 ? '格点掩膜=✓ 像素掩膜=后端未返回' : '掩膜=未启用');
        this.apiStatus=`${dataset}/${variable} ✓  ${data.stats.count}格点  ${lm}  ${Date.now()-t0}ms`;
      }catch(e){
        this.oceanStatus=`查询失败：${e.message}`;this.apiStatus=`错误：${e.message}`;
      }finally{this.queryLoading=false;}
    },
    async loadDemoVector(){
      const demo=this.variableList.find(v=>/hycom|uv|water_u|uwnd/.test(v.key.toLowerCase()));
      if(demo){
        this.selectedVar=demo.key;this.renderMode='particles';
        this.selection={west:118,east:126,south:18,north:26};
        this._drawSelectionRect(this.selection);
        await this.queryOcean();
      }else{this.oceanStatus='未找到矢量场数据（HYCOM u/v）。';}
    },

    // ── 时间轴动画 ──────────────────────────────────────────────────────────
    playTimelapse(){
      if(this.timeCount<=1){this.oceanStatus='当前数据集只有单一时间步，无法播放。';return;}
      this.playing=true;
      const step=()=>{
        if(!this.playing)return;
        this.timeIndex=(this.timeIndex+1)%this.timeCount;
        this.queryOcean().then(()=>{
          if(this.playing) this.playTimer=setTimeout(step,this.playInterval);
        }).catch(()=>{this.playing=false;});
      };
      // 立刻渲染当前帧，再开始循环
      this.queryOcean().then(()=>{
        if(this.playing) this.playTimer=setTimeout(step,this.playInterval);
      }).catch(()=>{this.playing=false;});
    },
    stopPlayback(){
      this.playing=false;
      if(this.playTimer){clearTimeout(this.playTimer);this.playTimer=null;}
    },
    onTimeSliderChange(){
      this.stopPlayback();
      this.queryOcean();
    },
    onDepthChange(){
      this.queryOcean();
    },

    // ── Apply render ──
    async applyRender(data){
      stopParticleAnimation();
      const mode=this.allowedModes.has(this.renderMode)?this.renderMode:'heatmap';
      this.stats=data.stats;
      this.vmin=`${+data.stats.min.toFixed(4)} ${data.units||''}`;
      this.vmax=`${+data.stats.max.toFixed(4)} ${data.units||''}`;
      this.legendCssStr=legendCss(data);
      this.oceanStatus=`已渲染 ${data.dataset} / ${data.long_name}  (格点掩膜=${data.land_mask_applied||0})`;
      const W=1024,H=512;
      if(mode==='particles'){
        if(!data.u_grid||!data.v_grid){
          this.oceanStatus='该变量无 u/v 矢量场，粒子流不可用。请选 HYCOM 海流数据或点击「海流样例」。';
          const c=gridCanvas(data,W,H);
          try{
            applyBackendHiresMaskToCanvas(c,data);
            await applyOptionalVectorLandMask(c,data);
          }catch(e){console.warn('[LandMask]',e.message);}
          this.updateCesiumRaster(data,c);this.updateOlRaster(data,c);return;
        }
        // Dim background for Cesium (particles prominent) — use 28% alpha directly
        const bgCesium=gridCanvas(data,W,H,0.28);
        try{
          applyBackendHiresMaskToCanvas(bgCesium,data);
          await applyOptionalVectorLandMask(bgCesium,data);
        }catch(e){console.warn('[LandMask-particle-bg]',e.message);}
        this.updateCesiumRaster(data,bgCesium);
        // OL map shows full heatmap with land mask
        const olHeatmap=gridCanvas(data,W,H);
        try{
          applyBackendHiresMaskToCanvas(olHeatmap,data);
          await applyOptionalVectorLandMask(olHeatmap,data);
        }catch(e){console.warn('[LandMask-particle-ol]',e.message);}
        this.updateOlRaster(data,olHeatmap);
        startParticleAnimation(data);
      }else{
        let c;
        if(mode==='contour') c=contourCanvas(data,W,H);
        else if(mode==='points') c=pointCanvas(data,W,H);
        else c=gridCanvas(data,W,H);
        try{
          applyBackendHiresMaskToCanvas(c,data);
          await applyOptionalVectorLandMask(c,data);
        }catch(e){console.warn('[LandMask]',e.message);}
        this.updateCesiumRaster(data,c);this.updateOlRaster(data,c);
      }
    },
    updateCesiumRaster(data,canvas){
      this.resizeAll();
      if(this.cesiumEntity)_viewer.entities.remove(this.cesiumEntity);
      const b=data.bounds;
      const rect=Cesium.Rectangle.fromDegrees(b.west,b.south,b.east,b.north);
      this.cesiumEntity=_viewer.entities.add({rectangle:{
        coordinates:rect,
        material:new Cesium.ImageMaterialProperty({image:canvas,transparent:true}),
      }});
      _viewer.scene.requestRender();
    },
    updateOlRaster(data,canvas){
      if(this.olLayer)_olMap.removeLayer(this.olLayer);
      const b=data.bounds;
      const ext=ol.proj.transformExtent([b.west,b.south,b.east,b.north],'EPSG:4326','EPSG:3857');
      this.olLayer=new ol.layer.Image({
        source:new ol.source.ImageStatic({url:canvas.toDataURL('image/png'),imageExtent:ext,projection:'EPSG:3857'}),
        opacity:this.layerOpacity,
        zIndex:10,  // 在底图(0)之上、标注层(100)之下
      });
      _olMap.addLayer(this.olLayer);  // zIndex=10 自动排在底图(0)和标注(100)之间
      _olMap.getView().fit(ext,{padding:[20,20,20,20],duration:300});
    },
    updateLayerOpacity(){if(this.olLayer)this.olLayer.setOpacity(this.layerOpacity);},
    clearGrid(){
      stopParticleAnimation();
      if(this.cesiumEntity)_viewer.entities.remove(this.cesiumEntity);
      if(this.selectionEntity)_viewer.entities.remove(this.selectionEntity);
      this.cesiumEntity=null;this.selectionEntity=null;
      if(this.olLayer)_olMap.removeLayer(this.olLayer);
      this.olLayer=null;this.gridData=null;this.stats=null;this.selection=null;
      this.vmin='-';this.vmax='-';this.legendCssStr='';this.oceanStatus='已清除。';
    },

    // ── Chat / SSE ──
    async sendQuery(){
      const q=this.question.trim();if(!q||this.streaming)return;
      this.messages.push({role:'user',text:q});
      this.streaming=true;this.reportStatus='分析中…';
      this.agentTrace=[];this.evidence=[];
      this.evaluation=null;
      Object.keys(this.pipelineSteps).forEach(k=>{this.pipelineSteps[k]='';});
      if(this.geoApiReady) await this._geoStream(q);
      else                 await this._legacyAsk(q);
    },
    async _geoStream(q){
      const bbox=this.selection||{west:119,east:122,south:23,north:26};
      const params=new URLSearchParams({question:q,domain:this.domain,bbox:JSON.stringify(bbox)});
      if(this.streamEs)this.streamEs.close();
      const es=new EventSource(`/geo-api/api/geo/stream?${params}`);
      this.streamEs=es;
      let buf='';
      const msgIdx=this.messages.length;
      this.messages.push({role:'ai',html:'<span class="cursor">▋</span>'});
      const upd=()=>{this.messages.splice(msgIdx,1,{role:'ai',html:this._md(buf)+'<span class="cursor">▋</span>'});this._scrollChat();};
      es.onmessage=(e)=>{
        let msg;try{msg=JSON.parse(e.data);}catch{return;}
        const{type,data:d,content:c}=msg;
        if(type==='domain'){this.pipelineSteps.intent='active';}
        else if(type==='intent'){this.pipelineSteps.intent='done';this.pipelineSteps.planner='active';}
        else if(type==='planner'){this.pipelineSteps.planner='done';this.pipelineSteps.retrieval='active';this.pipelineSteps.context='active';}
        else if(type==='context'){this.pipelineSteps.context='done';this.pipelineSteps.retrieval='done';this.pipelineSteps.reasoning='active';}
        else if(type==='analysis'){
          this.pipelineSteps.reasoning='done';this.pipelineSteps.visualization='done';this.pipelineSteps.report='active';
          const p=d||c||{};
          const kept=p.kept_docs||p.kept_documents||[];
          const passed=p.passed_docs||p.passed_documents||[];
          this.evidence=[
            ...kept.map(x=>({verdict:'keep',title:x.title||x.content?.slice(0,60)||'—',score:(x.decision_score||x.score||0).toFixed(3)})),
            ...passed.map(x=>({verdict:'pass',title:x.title||x.content?.slice(0,60)||'—',score:(x.decision_score||x.score||0).toFixed(3)})),
          ];
        }
        else if(type==='token'){buf+=(d||c||'');upd();}
        else if(type==='done'){
          this.pipelineSteps.report='done';this.pipelineSteps.evaluator='done';
          if(msg.trace&&Array.isArray(msg.trace))this.agentTrace=msg.trace;
          this.evaluation=msg.evaluation||null;
          const fh=this._md(buf);
          this.messages.splice(msgIdx,1,{role:'ai',html:fh});
          this.reportHtml=fh;
          const evalScore=msg.evaluation?.score!=null?` · 评测：${msg.evaluation.score}`:'';
          this.reportMeta=`领域：${msg.domain||this.domain} · 修订：${msg.revisions||0}${evalScore} · ${((msg.elapsed_ms||0)/1000).toFixed(1)}s`;
          buf='';this.streaming=false;this.reportStatus='分析完成。';es.close();this._scrollChat();
        }
        else if(type==='error'){
          this.messages.push({role:'ai',text:`❌ ${msg.message||c||'出错'}`});
          this.streaming=false;this.reportStatus='出错。';es.close();
        }
      };
      es.onerror=()=>{
        this.messages.push({role:'ai',text:'❌ geo-api 连接失败，请确认服务已启动（port 5001）。'});
        this.streaming=false;this.reportStatus='连接失败。';es.close();
      };
    },
    async _legacyAsk(q){
      try{
        const res=await fetch('/api/agents/report',{
          method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({question:q,top_k:this.topk,backend:this.backend,domain:this.domain,region:this.selection,trace:true}),
        });
        const data=await res.json();
        const html=data.report?this._md(data.report):`<em>${data.error||'无报告'}</em>`;
        this.messages.push({role:'ai',html});this.reportHtml=html;
        if(Array.isArray(data.trace))this.agentTrace=data.trace;
        this.evaluation=data.evaluation||null;
        this.reportMeta=`领域：${data.domain||this.domain} · 修订：${data.revisions||0}${data.evaluation?.score!=null?` · 评测：${data.evaluation.score}`:''}`;
      }catch(e){this.messages.push({role:'ai',text:`❌ ${e.message}`});}
      finally{this.streaming=false;this.reportStatus='分析完成。';this._scrollChat();}
    },
    clearChat(){
      if(this.streamEs){this.streamEs.close();this.streamEs=null;}
      this.streaming=false;
      this.messages=[{role:'ai',text:'对话已清空。'}];
      this.reportHtml='';this.agentTrace=[];this.evidence=[];
      this.agentSummary=null;this.agentSummaryHtml='';
      this.evaluation=null;
      Object.keys(this.pipelineSteps).forEach(k=>{this.pipelineSteps[k]='';});
    },
    _scrollChat(){this.$nextTick(()=>{const el=this.$refs.chatMessages;if(el)el.scrollTop=el.scrollHeight;});},

    // ── Helpers ──
    escapeHtml(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');},
    _pct(v){return v==null?'-':`${Math.round(Number(v)*100)}%`;},
    renderSuggestions(){},
    _md(text){
      if(!text)return'';
      let h=text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      h=h.replace(/^### (.+)$/gm,'<h4>$1</h4>');
      h=h.replace(/^## (.+)$/gm,'<h3>$1</h3>');
      h=h.replace(/^# (.+)$/gm,'<h2>$1</h2>');
      h=h.replace(/\*\*(.+?)\*\*/g,'<strong>$1</strong>');
      h=h.replace(/\*(.+?)\*/g,'<em>$1</em>');
      h=h.replace(/`([^`]+)`/g,'<code>$1</code>');
      h=h.replace(/^[*\-] (.+)$/gm,'<li>$1</li>');
      h=h.replace(/(<li>[\s\S]*?<\/li>\n?)+/g,s=>`<ul>${s}</ul>`);
      h=h.replace(/^\d+\. (.+)$/gm,'<li>$1</li>');
      h=h.replace(/^---+$/gm,'<hr>');
      h=h.replace(/\n/g,'<br>');
      return h;
    },
  },
}).mount('#app');

// ── TOPOJSON PIXEL-LEVEL LAND MASK ───────────────────────────────────────────
// Uses Natural Earth 110m coastlines via world-atlas CDN.
// Completely independent of ETOPO — works for any query bbox worldwide.
// ─────────────────────────────────────────────────────────────────────────────

let _landGeoFeatures = null;   // cached parsed fallback GeoJSON features
const _landGeoBboxCache = new Map(); // bbox key -> GeoServer WFS features
const _landMaskCache = new Map(); // "w,e,s,n" → ImageData (reused per bbox)

async function fetchLandGeoJson(url) {
  const res = await fetch(url, {headers:{Accept:'application/json'}});
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const geo = await res.json();
  if (geo?.type === 'FeatureCollection' && Array.isArray(geo.features)) return geo.features;
  if (geo?.type === 'Feature') return [geo];
  throw new Error('invalid GeoJSON response');
}


// ── LAND MASK HELPERS (called from applyRender) ───────────────────────────────

/**
 * Step 1 — Backend hires mask: use the 120×240 boolean grid returned by the
 * API in data.land_mask_hires to zero-out land pixels on the canvas.
 * Synchronous; safe to call even when land_mask_hires is absent (no-op).
 */
function applyBackendHiresMaskToCanvas(canvas, data) {
  if (!data?.land_mask_hires) return;          // no backend mask → skip
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  const imgData = ctx.getImageData(0, 0, W, H);
  const px = imgData.data;

  for (let y = 0; y < H; y++) {
    const ny = H <= 1 ? 0 : y / (H - 1);
    for (let x = 0; x < W; x++) {
      const nx = W <= 1 ? 0 : x / (W - 1);
      if (isHiresLand(data, nx, ny)) {
        px[(y * W + x) * 4 + 3] = 0;           // transparent
      }
    }
  }
  ctx.putImageData(imgData, 0, 0);
}

/**
 * Step 2 — Vector land mask: use GeoServer WFS / CDN TopoJSON polygons.
 * Async; fires applyLandMaskToCanvas which caches the bitmap per bbox.
 * No-op if data.bounds is absent.
 */
async function applyOptionalVectorLandMask(canvas, data) {
  if (!data?.bounds) return;
  await applyLandMaskToCanvas(canvas, data.bounds);
}

/** Prefer local GeoServer Natural Earth polygons; fall back to CDN TopoJSON. */
async function loadLandGeo(bounds) {
  if (bounds) {
    const west = Math.min(bounds.west, bounds.east);
    const east = Math.max(bounds.west, bounds.east);
    const south = Math.min(bounds.south, bounds.north);
    const north = Math.max(bounds.south, bounds.north);
    const key = `${west.toFixed(3)},${east.toFixed(3)},${south.toFixed(3)},${north.toFixed(3)}`;
    if (_landGeoBboxCache.has(key)) return _landGeoBboxCache.get(key);
    const params = new URLSearchParams({
      service:'WFS',
      version:'1.0.0',
      request:'GetFeature',
      typeName:GEOSERVER_LAND_LAYER,
      outputFormat:'application/json',
      srsName:'EPSG:4326',
      bbox:`${west},${south},${east},${north},EPSG:4326`,
    });
    try {
      const features = await fetchLandGeoJson(`${GEOSERVER_WFS_URL}?${params}`);
      _landGeoBboxCache.set(key, features);
      return features;
    } catch (e) {
      console.warn('[LandMask] GeoServer WFS load failed:', e.message, 'using fallback');
    }
  }

  if (_landGeoFeatures) return _landGeoFeatures;
    // CDN TopoJSON fallback
  try {
    const cdnCtrl = new AbortController();
    const cdnTid  = setTimeout(() => cdnCtrl.abort(), 10000); // 10 s CDN timeout
    const topo = await fetch(
      'https://cdn.jsdelivr.net/npm/world-atlas@2/land-110m.json',
      { signal: cdnCtrl.signal }
    ).then(r => { clearTimeout(cdnTid); return r.json(); })
     .catch(e => { clearTimeout(cdnTid); throw e; });
    const geo = topojson.feature(topo, topo.objects.land);
    _landGeoFeatures = geo.features?.length ? geo.features : [geo];
    console.info('[LandMask] Loaded Natural Earth 110m from CDN');
    return _landGeoFeatures;
  } catch (e) {
    console.warn('[LandMask] CDN fallback also failed:', e.message);
    return null;
  }
}

/** Fetch GeoJSON from any URL (GeoServer WFS or CDN), with timeout. */
async function fetchLandGeoJson(url, timeoutMs = 5000) {
  const ctrl = new AbortController();
  const tid  = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const resp = await fetch(url, { signal: ctrl.signal });
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    return (data.features || [data]);
  } finally {
    clearTimeout(tid);
  }
}

/**
 * Build an ImageData bitmap (W×H) where R=255=land, R=0=ocean.
 * Covers geographic bbox. Cached per bbox key.
 */
async function buildLandMaskBitmap(bounds, W = 512, H = 256) {
  const { west, east, south, north } = bounds;
  const key = `${west.toFixed(3)},${east.toFixed(3)},${south.toFixed(3)},${north.toFixed(3)}`;
  if (_landMaskCache.has(key)) return _landMaskCache.get(key);

  const features = await loadLandGeo(bounds);
  if (!features) return null;

  const canvas = mkCanvas(W, H);
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#fff';

  const lonX = lon => ((lon - west) / (east - west)) * W;
  const latY = lat => (1 - (lat - south) / (north - south)) * H;

  ctx.beginPath();
  for (const feat of features) {
    const g = feat.geometry || feat;
    const drawRing = ring => {
      if (!ring.length) return;
      ctx.moveTo(lonX(ring[0][0]), latY(ring[0][1]));
      for (let i = 1; i < ring.length; i++) ctx.lineTo(lonX(ring[i][0]), latY(ring[i][1]));
      ctx.closePath();
    };
    if (g.type === 'Polygon') {
      for (const ring of g.coordinates) drawRing(ring);
    } else if (g.type === 'MultiPolygon') {
      for (const poly of g.coordinates) for (const ring of poly) drawRing(ring);
    }
  }
  ctx.fill('evenodd');

  const imgData = ctx.getImageData(0, 0, W, H);
  _landMaskCache.set(key, imgData);
  return imgData;
}

/**
 * Post-process canvas: zero alpha for every land pixel.
 * Uses same resolution as canvas for 1:1 accuracy.
 */
async function applyLandMaskToCanvas(canvas, bounds) {
  const W = canvas.width, H = canvas.height;
  const lm = await buildLandMaskBitmap(bounds, W, H);
  if (!lm) return;

  const ctx = canvas.getContext('2d');
  const imgData = ctx.getImageData(0, 0, W, H);
  const px = imgData.data, lpx = lm.data;

  for (let i = 0; i < px.length; i += 4) {
    if (lpx[i] > 128) px[i + 3] = 0; // land → transparent
  }
  ctx.putImageData(imgData, 0, 0);
}
