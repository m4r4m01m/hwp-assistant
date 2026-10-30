import {useEffect,useRef,useState,type RefObject} from 'react';
import {createPortal} from 'react-dom';
import {api,jsonRequest,type Document} from './api';
import {type EditorAdapter} from './editor';
import {safeSvg} from './block-insert';
import {CoeditFocus} from './coedit-focus';

type Peer={id:string;user_id:string;typing:boolean;draft?:boolean;page:number;page_count?:number;available:boolean};
type Live={peers:Peer[];requested_pages?:number[];refreshing?:boolean;preview:(Peer&{svg:string;paragraph?:string|null;baseline_text?:string|null;sequence:number;base_version:string})|null};
export function CoeditLive({document:doc,adapter,token,version,epoch,lastEdit,dirty,baseline,enabled,controlsTarget}:{document:Document;adapter:RefObject<EditorAdapter|null>;token:RefObject<string>;version:RefObject<string>;epoch:RefObject<number|null>;lastEdit:RefObject<number>;dirty:RefObject<boolean>;baseline:RefObject<{base:string;page:number;text:string}|null>;enabled:boolean;controlsTarget?:HTMLElement|null}) {
  const [live,setLive]=useState<Live>({peers:[],preview:null}),[watch,setWatch]=useState(''),[error,setError]=useState('');
  const [watchUser,setWatchUser]=useState(''),[reconnected,setReconnected]=useState(false);
  const [fullPage,setFullPage]=useState(false);
  const [browsePage,setBrowsePage]=useState<number|null>(null),[followToken,setFollowToken]=useState(0);
  const wanted=useRef(''),wantedPage=useRef<number|null>(null),sequence=useRef(0);wanted.current=watch;wantedPage.current=browsePage;
  useEffect(()=>{
    if(!enabled){setLive({peers:[],preview:null});return;}
    let alive=true,busy=false,requested:number[]=[],turn=0;
    let published:{page:number;revision:number;base:string}|null=null;
    const cache=new Map<number,{revision:number;base:string;svg:string;paragraph:string|null}>();
    const controller=new AbortController();
    async function tick(){
      if(busy||!alive||!adapter.current||!token.current)return;busy=true;
      try {
        const editor=adapter.current,base=version.current,state=editor.bridge.documentState();
        if(state.documentEpoch!==epoch.current)return;
        const typing=Date.now()-lastEdit.current<1600;
        const revision=editor.bridge.revision();
        const selection=await editor.sdk.getSelectionContext(),page=selection.page;
        const count=await editor.sdk.pageCount();
        const available=[...new Set([page,...requested.filter(value=>value>=1&&value<=count)])];
        // Publish a new writing page before servicing older browse requests.
        const moved=!published||published.page!==page||published.base!==base;
        const renderedPage=moved?page:available[turn++%available.length];
        let svg:string|null=null,paragraph:string|null=null;
        if(typing||dirty.current||requested.length){
          let cached=cache.get(renderedPage);
          if(!cached||cached.revision!==revision||cached.base!==base){
            const rendered=await editor.sdk.getPageSvg(renderedPage-1);
            // An async render crossing a reload/edit is discarded; never attach
            // an unrelated page to this document or invent intermediate typing.
            if(editor.bridge.revision()!==revision||version.current!==base||editor.bridge.documentState().documentEpoch!==epoch.current)return;
            let text:string|null=null;
            try { if(renderedPage===page)text=editor.bridge.snapshot().evidence.text.slice(0,2000); } catch { /* Complex objects use the actual page view. */ }
            cached={revision,base,svg:rendered.length<=600000?rendered:'',paragraph:text};
            cache.delete(renderedPage);cache.set(renderedPage,cached);
            while(cache.size>4||[...cache.values()].reduce((sum,value)=>sum+value.svg.length,0)>600000)cache.delete(cache.keys().next().value!);
          }
          svg=cached.svg||null;
          // Cursor movement does not change the document revision. Refresh its
          // paragraph even when the actual page SVG can be reused.
          if(renderedPage===page)try{paragraph=editor.bridge.snapshot().evidence.text.slice(0,2000);}catch{paragraph=null;}
        }
        if(editor.bridge.revision()!==revision||version.current!==base||editor.bridge.documentState().documentEpoch!==epoch.current)return;
        const selected=wanted.current,selectedPage=wantedPage.current;
        const result=await api<Live>(`/documents/${doc.id}/coedit/live`,{...jsonRequest('POST',{base_version:base,sequence:++sequence.current,typing,draft:dirty.current,page,page_count:count,rendered_page:renderedPage,revision,svg,paragraph,baseline_text:baseline.current?.base===base&&baseline.current.page===renderedPage?baseline.current.text:null,watch:selected||null,watch_page:selectedPage}),headers:{'Content-Type':'application/json','X-Coedit-Session':token.current},signal:controller.signal,timeoutMs:5000});
        if(!alive)return;
        published={page,revision,base};
        requested=result.requested_pages||[];
        // This preview is read-only; it never reloads or modifies my editor.
        if(result.preview)result.preview.svg=safeSvg(result.preview.svg);
        setLive(previous=>{const same=selected===wanted.current&&selectedPage===wantedPage.current;const peer=result.peers.find(value=>value.id===selected);const previousFrame=previous.preview?.id===selected?previous.preview:null;return {...result,preview:same?(result.preview||(peer?.available?previousFrame:null)):null,refreshing:same&&!result.preview&&!!peer?.available};});setError('');
      }catch{if(alive){setLive({peers:[],preview:null});setError('작성 화면 연결을 다시 확인하고 있습니다. 문서 저장과는 별도입니다.');}}
      finally{busy=false;}
    }
    const timer=setInterval(()=>void tick(),600);void tick();
    return()=>{alive=false;clearInterval(timer);controller.abort();};
  },[doc.id,enabled]);
  const peer=live.peers.find(item=>item.id===watch);
  const replacements=live.peers.filter(item=>item.user_id===watchUser);
  useEffect(()=>{
    if(!watch||!watchUser||live.peers.some(item=>item.id===watch))return;
    const matches=live.peers.filter(item=>item.user_id===watchUser);
    if(matches.length!==1)return; // Multiple tabs require an explicit choice.
    setWatch(matches[0].id);setBrowsePage(null);setFullPage(false);setFollowToken(value=>value+1);
    setLive(value=>({...value,preview:null}));setReconnected(true);
  },[live.peers,watch,watchUser]);
  const page=browsePage??peer?.page??1,count=peer?.page_count??peer?.page??1;
  useEffect(()=>{if(browsePage!==null&&browsePage>count)setBrowsePage(count);},[browsePage,count]);
  function follow(){setBrowsePage(null);setFullPage(false);setFollowToken(value=>value+1);}
  function changePage(delta:number){setBrowsePage(Math.max(1,Math.min(count,page+delta)));setFullPage(true);setLive(value=>({...value,preview:null}));}
  const controls=<div className="coedit-live-bar" aria-label="참여자 작성 상태">
      <span>함께 작성</span>{live.peers.length?live.peers.map(item=><button key={item.id} className={item.typing?'is-typing':''} aria-pressed={watch===item.id} onClick={()=>{setWatch(watch===item.id?'':item.id);setWatchUser(watch===item.id?'':item.user_id);setReconnected(false);follow();setLive(value=>({...value,preview:null}));}}>{item.user_id} · {item.typing?`${item.page}쪽 작성 중`:item.draft?'미반영 변경':'참여 중'} <span>{watch===item.id?'닫기':'작성 화면 보기'}</span></button>):<span>다른 참여자의 작성 상태가 여기에 표시됩니다.</span>}
      {error&&<span className="coedit-live-warning">{error}</span>}
    </div>;
  return <>
    {controlsTarget?createPortal(controls,controlsTarget):controls}
    {watch&&<aside className="coedit-live-view" aria-label="실시간 작성 화면"><header><strong>{peer?.user_id||watchUser||'참여자'} · {page} / {count}쪽</strong><button onClick={()=>{setWatch('');setWatchUser('');setReconnected(false);}}>닫기</button></header>
      {!peer&&<p>{replacements.length>1?'같은 계정의 창이 여러 개입니다. 참여자 목록에서 볼 창을 선택하세요.':`${watchUser}의 재접속을 기다리고 있습니다.`}</p>}
      {peer&&reconnected&&<p>{watchUser}의 새 접속에 다시 연결했습니다.</p>}
      <div className="coedit-page-nav"><button aria-label="이전 쪽" disabled={page<=1} onClick={()=>changePage(-1)}>‹</button><span>{browsePage===null?'작성 위치 따라가는 중':`직접 보기 · 작성 위치 ${peer?.page||1}쪽`}</span><button aria-label="다음 쪽" disabled={page>=count} onClick={()=>changePage(1)}>›</button></div>
      <p>휠은 배율만 변경 · 드래그하면 따라가기 멈춤</p>
      {live.refreshing&&<p>이 화면을 최신 내용으로 갱신 중…</p>}
      {(!live.preview||live.preview.page!==page)&&<p>참여자의 {page}쪽 화면을 기다리고 있습니다.</p>}
      {live.preview&&<div hidden={live.preview.page!==page}><CoeditFocus svg={live.preview.svg} paragraph={live.preview.paragraph||null} baselineText={live.preview.baseline_text??null} focusKey={`${watch}:${live.preview.base_version}:${live.preview.page}`} fullPage={fullPage} resetToken={followToken} following={browsePage===null} onManual={kind=>{if(kind==='pan')setBrowsePage(page);}} onPageStep={changePage}/></div>}
      <div className="coedit-page-nav"><button onClick={follow}>작성 위치 따라가기</button><button onClick={()=>{setFullPage(true);setBrowsePage(page);setFollowToken(value=>value+1);}}>쪽 전체 보기</button></div>
    </aside>}
  </>;
}
