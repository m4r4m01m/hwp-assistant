import {useEffect,useLayoutEffect,useRef,useState} from 'react';
import {textDifference} from './text-diff';
type Box={x:number;y:number;width:number;height:number};
const union=(a:Box,b:Box):Box=>({x:Math.min(a.x,b.x),y:Math.min(a.y,b.y),width:Math.max(a.x+a.width,b.x+b.width)-Math.min(a.x,b.x),height:Math.max(a.y+a.height,b.y+b.height)-Math.min(a.y,b.y)});
export function CoeditFocus({svg,paragraph,baselineText,focusKey,fullPage,resetToken=0,following=true,onManual,onPageStep}:{svg:string;paragraph:string|null;baselineText:string|null;focusKey:string;fullPage:boolean;resetToken?:number;following?:boolean;onManual?:(kind:'pan'|'zoom')=>void;onPageStep?:(delta:number)=>void}){
  const host=useRef<HTMLDivElement>(null),memory=useRef<{key:string;baseline:string;box:Box|null}|null>(null);
  const [removed,setRemoved]=useState(''),[focused,setFocused]=useState(false);
  const [manual,setManual]=useState(false);
  const viewport=useRef<{key:string;box:Box}|null>(null),bounds=useRef<Box>({x:0,y:0,width:800,height:1100});
  const zoomSize=useRef<{width:number;height:number}|null>(null),zoomReset=useRef(`${fullPage}:${resetToken}`);
  const callbacks=useRef({onManual,onPageStep});callbacks.current={onManual,onPageStep};
  const viewportKey=`${focusKey}:${fullPage}:${resetToken}`;
  function readBox():Box|null{const values=host.current?.querySelector('svg')?.getAttribute('viewBox')?.split(/[ ,]+/).map(Number);return values?.length===4?{x:values[0],y:values[1],width:values[2],height:values[3]}:null;}
  function move(box:Box,kind:'pan'|'zoom'='pan'){const full=bounds.current;const width=Math.min(full.width,Math.max(80,box.width)),height=Math.min(full.height,Math.max(60,box.height));const next={x:Math.max(full.x,Math.min(box.x,full.x+full.width-width)),y:Math.max(full.y,Math.min(box.y,full.y+full.height-height)),width,height};viewport.current={key:viewportKey,box:next};if(kind==='zoom')zoomSize.current={width:width/full.width,height:height/full.height};host.current?.querySelector('svg')?.setAttribute('viewBox',`${next.x} ${next.y} ${next.width} ${next.height}`);setManual(true);callbacks.current.onManual?.(kind);}
  function zoom(factor:number,point?:{x:number;y:number}){const box=readBox();if(!box)return;const full=bounds.current;const ratio=Math.min(full.width/box.width,full.height/box.height,Math.max(80/box.width,60/box.height,factor));const center=point||{x:box.x+box.width/2,y:box.y+box.height/2};move({x:center.x-(center.x-box.x)*ratio,y:center.y-(center.y-box.y)*ratio,width:box.width*ratio,height:box.height*ratio},'zoom');}
  useEffect(()=>{
    const el=host.current;if(!el)return;
    let drag:{id:number;x:number;y:number;box:Box;scaleX:number;scaleY:number;key:string}|null=null;
    const point=(x:number,y:number)=>{const matrix=el.querySelector('svg')?.getScreenCTM();return matrix?new DOMPoint(x,y).matrixTransform(matrix.inverse()):undefined;};
    const wheel=(event:WheelEvent)=>{event.preventDefault();zoom(Math.exp(Math.max(-120,Math.min(120,event.deltaY*(event.deltaMode===1?16:1)))*.003),point(event.clientX,event.clientY));};
    const down=(event:PointerEvent)=>{if(event.button!==0)return;const box=readBox(),matrix=el.querySelector('svg')?.getScreenCTM();if(!box||!matrix)return;event.preventDefault();el.focus();el.setPointerCapture(event.pointerId);const inverse=matrix.inverse();drag={id:event.pointerId,x:event.clientX,y:event.clientY,box,scaleX:inverse.a,scaleY:inverse.d,key:viewportKey};};
    const pointerMove=(event:PointerEvent)=>{if(!drag||drag.id!==event.pointerId||drag.key!==viewportKey)return;move({...drag.box,x:drag.box.x-(event.clientX-drag.x)*drag.scaleX,y:drag.box.y-(event.clientY-drag.y)*drag.scaleY});};
    const up=(event:PointerEvent)=>{if(!drag||drag.id!==event.pointerId)return;const dy=event.clientY-drag.y,full=bounds.current;const attempted=drag.box.y-dy*drag.scaleY;const direction=dy < -80&&attempted>full.y+full.height-drag.box.height+40?1:dy>80&&attempted<full.y-40?-1:0;drag=null;if(el.hasPointerCapture(event.pointerId))el.releasePointerCapture(event.pointerId);if(direction)callbacks.current.onPageStep?.(direction);};
    const cancel=()=>{drag=null;};
    const key=(event:KeyboardEvent)=>{const box=readBox();if(!box)return;if(['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','+','=','-','PageDown','PageUp'].includes(event.key))event.preventDefault();if(event.key==='+'||event.key==='=')zoom(.8);else if(event.key==='-')zoom(1.25);else if(event.key==='PageDown'||event.key==='PageUp')callbacks.current.onPageStep?.(event.key==='PageDown'?1:-1);else if(event.key.startsWith('Arrow'))move({...box,x:box.x+(event.key==='ArrowRight'?40:event.key==='ArrowLeft'?-40:0),y:box.y+(event.key==='ArrowDown'?40:event.key==='ArrowUp'?-40:0)});};
    el.addEventListener('wheel',wheel,{passive:false});el.addEventListener('pointerdown',down);el.addEventListener('pointermove',pointerMove);el.addEventListener('pointerup',up);el.addEventListener('pointercancel',cancel);el.addEventListener('keydown',key);
    return()=>{el.removeEventListener('wheel',wheel);el.removeEventListener('pointerdown',down);el.removeEventListener('pointermove',pointerMove);el.removeEventListener('pointerup',up);el.removeEventListener('pointercancel',cancel);el.removeEventListener('keydown',key);};
  },[viewportKey]);
  useLayoutEffect(()=>{
    // The caller supplies safeSvg output. Own this isolated read-only SVG DOM:
    // React state updates must not restore the original full-page viewBox.
    if(host.current)host.current.innerHTML=svg;
    const root=host.current?.querySelector('svg');if(!root)return;
    root.querySelectorAll('[data-live-mark]').forEach(node=>node.remove());
    const full=root.dataset.fullView||(root.dataset.fullView=root.getAttribute('viewBox')||`0 0 ${parseFloat(root.getAttribute('width')||'794')} ${parseFloat(root.getAttribute('height')||'1123')}`);
    const [fx,fy,fw,fh]=full.split(/[ ,]+/).map(Number);
    if(![fx,fy,fw,fh].every(Number.isFinite)||fw<=0||fh<=0)return;
    bounds.current={x:fx,y:fy,width:fw,height:fh};
    if(zoomReset.current!==`${fullPage}:${resetToken}`){zoomReset.current=`${fullPage}:${resetToken}`;zoomSize.current=null;}
    if(viewport.current?.key!==viewportKey){viewport.current=null;setManual(false);}
    let offset=0;
    const nodes=Array.from(root.querySelectorAll('text')).map(node=>{const text=(node.textContent||'').replace(/\s/g,'');const start=offset;offset+=text.length;return {node,text,start,end:offset};});
    const current=nodes.map(item=>item.text).join('');
    if(current.length>20000){setRemoved('');setFocused(false);return;}
    if(!memory.current||memory.current.key!==focusKey)memory.current={key:focusKey,baseline:baselineText??current,box:null};
    const baseline=baselineText??memory.current.baseline;
    const marks:{start:number;end:number;kind:'added'|'removed'}[]=[];let cursor=0,deleted='';
    // Full saved-page text gives exact additions. Without it, compare only with
    // the first observed frame and label that scope in the UI.
    for(const part of textDifference(baseline,current)){
      if(part.kind==='removed'){deleted+=part.text;marks.push({start:Math.min(cursor,Math.max(0,current.length-1)),end:Math.min(current.length,cursor+1),kind:'removed'});}
      else {if(part.kind==='added')marks.push({start:cursor,end:cursor+part.text.length,kind:'added'});cursor+=part.text.length;}
    }
    setRemoved(deleted);
    const group=document.createElementNS('http://www.w3.org/2000/svg','g');group.setAttribute('data-live-mark','true');group.setAttribute('pointer-events','none');
    let target:Box|null=null;
    const box=(node:SVGTextElement):Box|null=>{try{const b=node.getBBox(),base=root.getCTM(),m=node.getCTM();if(!base||!m)return null;const transform=base.inverse().multiply(m);const points=[[b.x,b.y],[b.x+b.width,b.y],[b.x,b.y+b.height],[b.x+b.width,b.y+b.height]].map(([x,y])=>new DOMPoint(x,y).matrixTransform(transform));const xs=points.map(p=>p.x),ys=points.map(p=>p.y);return{x:Math.min(...xs),y:Math.min(...ys),width:Math.max(...xs)-Math.min(...xs),height:Math.max(...ys)-Math.min(...ys)};}catch{return null;}};
    for(const item of nodes){const matching=marks.filter(mark=>item.start<mark.end&&item.end>mark.start);if(!matching.length)continue;const b=box(item.node);if(!b)continue;target=target?union(target,b):b;const rect=document.createElementNS('http://www.w3.org/2000/svg','rect');rect.setAttribute('x',String(b.x-1));rect.setAttribute('y',String(b.y-1));rect.setAttribute('width',String(Math.max(2,b.width+2)));rect.setAttribute('height',String(b.height+2));rect.setAttribute('fill',matching.some(mark=>mark.kind==='added')?'#34c77c':'#ec6b6b');rect.setAttribute('fill-opacity','.28');group.append(rect);}
    if(!target&&paragraph){const text=paragraph.replace(/\s/g,'');const start=text?current.indexOf(text):-1;if(start>=0&&current.indexOf(text,start+1)<0)for(const item of nodes.filter(item=>item.start<start+text.length&&item.end>start)){const b=box(item.node);if(b)target=target?union(target,b):b;}}
    if(target)memory.current.box=memory.current.box?union(memory.current.box,target):target;
    root.append(group);
    const area=memory.current.box;
    if(!viewport.current&&following&&zoomSize.current){const width=fw*zoomSize.current.width,height=fh*zoomSize.current.height;const center=area?{x:area.x+area.width/2,y:area.y+area.height/2}:{x:fx+fw/2,y:fy+height/2};viewport.current={key:viewportKey,box:{x:Math.max(fx,Math.min(center.x-width/2,fx+fw-width)),y:Math.max(fy,Math.min(center.y-height/2,fy+fh-height)),width,height}};setManual(true);}
    // Keep short edits close to normal reading size, with surrounding context.
    // The fixed panel still zooms out when the changed bounds grow.
    if(viewport.current){const b=viewport.current.box;root.setAttribute('viewBox',`${b.x} ${b.y} ${b.width} ${b.height}`);}else if(!fullPage&&area){const width=Math.min(fw,Math.max(320,area.width+64)),height=Math.min(fh,Math.max(180,area.height+64));const x=Math.max(fx,Math.min(area.x-(width-area.width)/2,fx+fw-width)),y=Math.max(fy,Math.min(area.y-(height-area.height)/2,fy+fh-height));root.setAttribute('viewBox',`${x} ${y} ${width} ${height}`);setFocused(true);}else{root.setAttribute('viewBox',full);setFocused(false);}
  },[svg,paragraph,baselineText,focusKey,fullPage,viewportKey,following]);
  return <><p className="coedit-focus-caption">{manual?(following?'설정한 배율로 따라가는 중':'직접 이동/확대 중'):fullPage?'쪽 전체':focused?'수정 영역 자동 확대':'위치를 확인할 수 없어 쪽 전체 표시'} · {baselineText===null?'처음 표시한 화면 이후':'기준 저장본 대비'}</p><div ref={host} className="coedit-live-paper coedit-focus-paper" tabIndex={0} aria-label="작성 화면 이동 및 확대"/><div className="coedit-page-nav"><button aria-label="미리보기 축소" onClick={()=>zoom(1.25)}>−</button><span>드래그 · 휠</span><button aria-label="미리보기 확대" onClick={()=>zoom(.8)}>＋</button></div><p className="coedit-focus-legend"><span>초록: 추가</span><span>빨강: 삭제 위치</span></p>{removed&&<p className="coedit-removed">삭제한 내용: <del>{removed}</del></p>}</>;
}
