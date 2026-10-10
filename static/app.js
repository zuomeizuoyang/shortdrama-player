"use strict";
const $ = (id) => document.getElementById(id);
const svg = (name) => {const node = document.createElementNS("http://www.w3.org/2000/svg", "svg");const use = document.createElementNS(node.namespaceURI,"use");use.setAttribute("href","#i-"+name);node.append(use);return node;};
const el = (tag, className, text) => {const node=document.createElement(tag);if(className)node.className=className;if(text!=null)node.textContent=text;return node;};
const state = {platform:"hongguo",rankPlatform:"hongguo",language:"zh-CN",mode:"feed",keyword:"",items:[],cursor:null,platforms:[],loading:false,request:0,heroIndex:0,detail:null,chapters:[],currentEpisode:null,detailRequest:0,playRequest:0,hls:null,resume:null,rankCategory:"all",rankBoard:"new_rank"};
const platformRankConfig = {
  hongguo: {
    name: "红果短剧",
    categories: [
      { id: "all", name: "全部" },
      { id: "series", name: "真人剧" },
      { id: "comic_series_rank", name: "漫剧" },
      { id: "ai_series", name: "AI剧" }
    ],
    boards: [
      { id: "new_rank", name: "✨ 新剧榜" },
      { id: "hot_play", name: "🔥 臻果热播榜" },
      { id: "hot_search", name: "🔍 热搜榜" }
    ],
    defaultCat: "all",
    defaultBoard: "new_rank",
    note: "红果官方权威榜单 · 实时热度排名"
  },
  dramabox: {
    name: "DramaBox",
    categories: [],
    boards: [
      { id: "trending", name: "🔥 Most Trending (趋势榜)" },
      { id: "must-sees", name: "🌟 Must-Sees (必看榜)" },
      { id: "hidden-gems", name: "💎 Hidden Gems (精选榜)" }
    ],
    defaultCat: "all",
    defaultBoard: "trending",
    note: "DramaBox 官方排行榜 · 实时播放热度"
  },
  reelshort: {
    name: "ReelShort",
    categories: [],
    boards: [
      { id: "top", name: "🏆 TOP (总排行榜)" },
      { id: "new_release", name: "🚀 New Release (新剧榜)" },
      { id: "reel_original", name: "🎬 Reel Original (原创榜)" }
    ],
    defaultCat: "all",
    defaultBoard: "top",
    note: "ReelShort 官方排行榜 · 真实播放量排行"
  },
  dramawave: {
    name: "DramaWave",
    categories: [],
    boards: [
      { id: "most_trending_rank", name: "🔥 Most Trending (飙升榜)" }
    ],
    defaultCat: "all",
    defaultBoard: "most_trending_rank",
    note: "DramaWave 官方排行榜 · 实时热度排名"
  },
  netshort: {
    name: "NetShort",
    categories: [],
    boards: [
      { id: "trending", name: "🔥 Trending Now (趋势榜)" },
      { id: "new_releases", name: "✨ New Releases (新上线榜)" },
      { id: "watch_now", name: "🎯 Dramas to Watch (必看榜)" },
      { id: "carousel", name: "🎠 Carousel (轮播推荐榜)" }
    ],
    defaultCat: "all",
    defaultBoard: "trending",
    note: "NetShort 官方排行榜 · 实时趋势"
  },
  goodshort: {
    name: "GoodShort",
    categories: [],
    boards: [
      { id: "Most-Trending", name: "🔥 Most Trending (飙升榜)" },
      { id: "Top-in-GoodShort", name: "🏆 Top in GoodShort (总榜)" },
      { id: "Hot-List", name: "🌟 Hot List (热度榜)" },
      { id: "Popular-Now", name: "💖 Popular Now (热门榜)" }
    ],
    defaultCat: "all",
    defaultBoard: "Most-Trending",
    note: "GoodShort 官方排行榜 · 真实播放量排行"
  },
  all: {
    name: "全网聚合榜",
    categories: [],
    boards: [],
    defaultCat: "all",
    defaultBoard: "all",
    note: "全网热度聚合榜 · 汇聚各大短剧平台官方实时排行榜榜首剧目"
  }
};
function updateRankUI(){
  const cfg=platformRankConfig[state.rankPlatform]||platformRankConfig.hongguo;
  document.querySelectorAll(".rank-plat-btn").forEach(btn=>{
    btn.classList.toggle("active",btn.dataset.platform===state.rankPlatform);
  });
  const catRow=$("rank-cat-row");
  const catTabs=$("rank-category-tabs");
  if(cfg.categories.length>0){
    catRow.hidden=false;
    catTabs.replaceChildren(...cfg.categories.map(c=>{
      const btn=el("button","rank-cat-btn"+(c.id===state.rankCategory?" active":""),c.name);
      btn.dataset.cat=c.id;
      btn.onclick=()=>{state.rankCategory=c.id;updateRankUI();loadLibrary();};
      return btn;
    }));
  }else{
    catRow.hidden=true;
  }
  const subRow=$("rank-sub-row");
  const subTabs=$("rank-sub-tabs");
  if(cfg.boards.length>0){
    subRow.hidden=false;
    subTabs.replaceChildren(...cfg.boards.map(b=>{
      const btn=el("button","rank-sub-btn"+(b.id===state.rankBoard?" active":""),b.name);
      btn.dataset.board=b.id;
      btn.onclick=()=>{state.rankBoard=b.id;updateRankUI();loadLibrary();};
      return btn;
    }));
  }else{
    subRow.hidden=true;
  }
  const activeBoard=cfg.boards.find(b=>b.id===state.rankBoard);
  const activeCat=cfg.categories.find(c=>c.id===state.rankCategory);
  let title=cfg.name;
  if(activeCat&&activeCat.id!=="all")title+=" · "+activeCat.name;
  if(activeBoard)title+=" · "+activeBoard.name;
  $("rank-title-text").textContent=title;
  $("rank-desc-pill").textContent=cfg.note;
}
const cacheKeys={favorites:"shortdrama:favorites:v1",history:"shortdrama:history:v1"};
function readSaved(kind){try{const rows=JSON.parse(localStorage.getItem(cacheKeys[kind])||"[]");return Array.isArray(rows)?rows:[];}catch{return [];}}
function saveRows(kind, rows){try{localStorage.setItem(cacheKeys[kind],JSON.stringify(rows.slice(0,200)));}catch{toast("本地存储空间不足，请清理部分记录");}}
function key(row){return row.platform+":"+row.drama_id;}
function isSaved(row){return readSaved("favorites").some(item=>key(item)===key(row));}
function durable(row){return {platform:row.platform,drama_id:row.drama_id,title:row.title,description:row.description,cover:row.cover_source||row.cover,cover_source:row.cover_source||row.cover,episode_count:row.episode_count,tags:row.tags,display_language:row.display_language||state.language};}
let toastTimer;
function toast(message){$("toast").textContent=message;$("toast").hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$("toast").hidden=true,2600);}
function updateFavoriteCount(){$("favorite-count").textContent=readSaved("favorites").length;}
function toggleFavorite(row){let rows=readSaved("favorites");const saved=rows.some(item=>key(item)===key(row));rows=saved?rows.filter(item=>key(item)!==key(row)):[durable(row),...rows];saveRows("favorites",rows);updateFavoriteCount();renderCards();if(state.items.length)updateHero(state.heroIndex);if(state.detail)favoriteButton($("detail-favorite"),state.detail);toast(saved?"已移出收藏":"已加入收藏");if(state.mode==="favorites")loadLibrary();}
function favoriteButton(button,row){const saved=isSaved(row);button.classList.toggle("saved",saved);button.querySelector("span").textContent=saved?"已收藏":"收藏";}
function safeLink(value){if(typeof value!=="string"||!value)return null;try{const parsed=new URL(value,location.origin);return ["http:","https:"].includes(parsed.protocol)?parsed.href:null;}catch{return null;}}
function setImage(image,url,title){image.alt=title||"短剧封面";image.onerror=()=>{image.hidden=true;if(image.parentElement&&image.parentElement.classList.contains("card-cover")){if(!image.parentElement.querySelector(".cover-fallback"))image.parentElement.prepend(el("span","cover-fallback",(title||"S").slice(0,1)));}};const safe=safeLink(url);if(safe){image.hidden=false;image.src=safe;}else{image.hidden=true;}}
async function api(path,data,signal){const options={credentials:"same-origin",signal};if(data!==undefined){options.method="POST";options.headers={"Content-Type":"application/json"};options.body=JSON.stringify(data);}const response=await fetch(path,options);let result;try{result=await response.json();}catch{throw new Error("本地服务返回了无法读取的数据");}if(!response.ok||!result.ok){const error=new Error(result.error?.message||"请求失败，请重试");error.code=result.error?.code;throw error;}return result.data;}
function provider(id=state.platform){return state.platforms.find(row=>row.id===id);}
function renderPlatforms(){const nav=$("platform-nav");nav.replaceChildren();for(const row of state.platforms){const button=el("button","platform-button"+(row.id===state.platform?" active":""));button.style.setProperty("--platform-color",row.color);button.dataset.platform=row.id;button.append(el("span","platform-mark",row.mark),el("span","",row.name),el("span","platform-status"+(!row.configured?" pending":"")));button.setAttribute("aria-label","选择 "+row.name);button.onclick=()=>selectPlatform(row.id);nav.append(button);}}
function renderLanguages(){const options=provider()?.languages||[];$("language").replaceChildren(...options.map(row=>{const option=el("option","",row.name);option.value=row.id;return option;}));$("language").value=state.language;}
function selectPlatform(id){if(!provider(id))return;state.platform=id;state.language=provider(id).default_language;state.mode="feed";state.keyword="";$("search-input").value="";renderPlatforms();renderLanguages();loadLibrary();}
function updateHeading(){const view=state.mode;$("breadcrumb-view").textContent=({feed:state.platform==="hongguo"?"红果新剧榜":"发现片单",rank:"官方排行榜",search:"搜索",favorites:"我的收藏",history:"观看记录"})[view];document.querySelectorAll(".nav-item").forEach(button=>button.classList.toggle("active",button.dataset.view===view));if(view==="rank"){updateRankUI();const cfg=platformRankConfig[state.rankPlatform]||platformRankConfig.hongguo;const activeBoard=cfg.boards.find(b=>b.id===state.rankBoard);const activeCat=cfg.categories.find(c=>c.id===state.rankCategory);let title=cfg.name;if(activeCat&&activeCat.id!=="all")title+=" · "+activeCat.name;if(activeBoard)title+=" · "+activeBoard.name;$("section-title").textContent=title;$("section-eyebrow").textContent="OFFICIAL LEADERBOARD & TRENDING";$("rank-bar").hidden=false;}else{$("rank-bar").hidden=true;$("section-title").textContent=view==="search"?"搜索「"+state.keyword+"」":({feed:state.platform==="hongguo"?"红果新剧榜":"为你发现",favorites:"我的收藏",history:"继续你的故事"})[view];$("section-eyebrow").textContent=({feed:state.platform==="hongguo"?"OFFICIAL NEW RELEASES":"DISCOVER YOUR NEXT STORY",search:"FIND THE STORY YOU LOVE",favorites:"STORIES YOU WANT TO KEEP",history:"PICK UP WHERE YOU LEFT OFF"})[view];}$("platform-chip").textContent=provider()?.name||state.platform;$("language-chip").textContent=provider()?.languages.find(row=>row.id===state.language)?.name||state.language;$("feed-note").textContent=view==="rank"?"红果官方权威榜单 · 实时热度排名":(view==="favorites"||view==="history"?"仅显示当前平台":state.platform==="hongguo"&&view==="feed"?"官方全部新剧榜 · 实时更新":state.platform==="netshort"&&view==="search"?"官网搜索 · 包含推荐回填":"热门与精选推荐");$("refresh").hidden=view==="favorites"||view==="history";$("hero").hidden=view!=="feed";}
function showSkeleton(){const container=$("cards");container.replaceChildren();for(let i=0;i<12;i++){const row=el("div","drama-card skeleton");row.append(el("div","card-cover"),el("div","skeleton-line"),el("div","skeleton-line small"));container.append(row);}$("hero").classList.add("skeleton-hero");$("hero-title").textContent="故事，即将开始。";$("hero-description").textContent="正在为你寻找新的故事…";$("hero-platform").textContent=provider()?.name||state.platform;$("hero-tags").replaceChildren();$("hero-dots").replaceChildren();$("hero-poster").removeAttribute("src");$("hero-backdrop").style.backgroundImage="none";}
function banner(message,error=false){$("status-banner").hidden=!message;$("status-banner").textContent=message||"";$("status-banner").classList.toggle("error",error);}
let listAbort;
async function loadLibrary(append=false){const request=++state.request;if(listAbort)listAbort.abort();listAbort=new AbortController();state.loading=true;updateHeading();$("load-more").hidden=true;$("list-end").hidden=true;$("empty-state").hidden=true;banner("");if(!append){state.items=[];state.cursor=null;state.heroIndex=0;showSkeleton();}$("result-count").textContent="正在获取片单";try{
 let page;if(["favorites","history"].includes(state.mode)){page={items:readSaved(state.mode).filter(row=>row.platform===state.platform),next_cursor:null};}else if(state.mode==="rank"){const p=state.rankPlatform;const lang=p==="hongguo"?"zh-CN":"en";const payload={language:lang,category:state.rankCategory,board:state.rankBoard,cursor:append?state.cursor:null};page=await api("/api/"+p+"/rank",payload,listAbort.signal);}else{const operation=state.mode==="search"?"search":"feed";const payload={language:state.language,cursor:append?state.cursor:null};if(operation==="search")payload.keyword=state.keyword;page=await api("/api/"+state.platform+"/"+operation,payload,listAbort.signal);}
 if(request!==state.request)return;const seen=new Set(state.items.map(key));state.items.push(...page.items.filter(row=>!seen.has(key(row))));state.cursor=page.next_cursor;renderCards();$("result-count").textContent=state.items.length+" 部短剧";$("load-more").hidden=!state.cursor;$("list-end").hidden=!!state.cursor||!state.items.length||["favorites","history"].includes(state.mode);if(state.mode==="rank"&&page.description)banner(page.description);else if(state.mode==="search"&&state.platform==="netshort")banner("NetShort 的官网搜索会混入推荐，结果不代表全部为关键词匹配。"+(page.termination_reason==="adapter_result_limit"?" 本轮已达到 500 部结果上限。":""));else if(state.platform==="hongguo"&&state.items.length)banner(page.notice||"红果官网开放集可播放，其余剧集请前往官方平台。");if(state.items.length&&state.mode==="feed")updateHero(state.heroIndex);if(!state.items.length)showEmpty(state.mode==="favorites"?"还没有收藏":state.mode==="history"?"还没有观看记录":"没有找到这段故事",state.mode==="favorites"?"在片单或详情中点击爱心，喜欢的故事就会留在这里。":state.mode==="history"?"开始观看后，进度会自动保存在当前浏览器。":"换一个关键词，或试试其他内容语言。");
 }catch(error){if(error.name==="AbortError"||request!==state.request)return;banner(error.message,true);if(!append){$("cards").replaceChildren();$("result-count").textContent="暂未加载";$("hero").hidden=true;showEmpty(error.code==="configuration_required"?"连接红果，开始发现":"片单暂时无法加载",error.code==="configuration_required"?"填写红果后端地址与 API Key，即可在这里浏览和观看。":error.message,error.code==="configuration_required");}else{$("load-more").hidden=false;}}
 finally{if(request===state.request){state.loading=false;$("hero").classList.remove("skeleton-hero");$("load-more").disabled=false;}}}
function showEmpty(title,description,config=false){$("empty-state").hidden=false;$("empty-title").textContent=title;$("empty-description").textContent=description;$("empty-action").hidden=!config;}
function renderCards(){const container=$("cards");container.replaceChildren();for(const [index,row] of state.items.entries()){
 const wrapper=el("article","drama-card"+(state.mode==="rank"?" rank-card":""));const open=el("button","card-open");open.setAttribute("aria-label","查看 "+row.title);const cover=el("div","card-cover");const image=el("img");image.loading=index<6?"eager":"lazy";setImage(image,row.cover,row.title);
 cover.append(image);
 if(state.mode==="rank"){
  const rNum=row.rank||(index+1);
  const rClass=rNum===1?"rank-gold":rNum===2?"rank-silver":rNum===3?"rank-bronze":"rank-normal";
  const badge=el("span","card-rank-badge "+rClass,(rNum<10?"0":"")+rNum);
  cover.append(badge);
  cover.append(el("span","card-platform-pill",provider(row.platform)?.name||row.platform));
 }else{
  cover.append(el("span","card-badge",index===0&&state.mode==="feed"?"今日精选":provider(row.platform)?.name||row.platform));
 }
 if(row.episode_count)cover.append(el("span","card-episodes",row.episode_count+" 集"));
 if(row.rec_text)cover.append(el("span","card-heat-pill","🔥 "+row.rec_text));
 const play=el("span","card-play");play.append(svg("play"));cover.append(play);open.append(cover,el("h3","",row.title));
 const subtitle=state.mode==="history"?"上次看到第 "+row.episode_number+" 集 · "+Math.floor(row.progress||0)+" 秒":state.mode==="rank"?(row.sub_title||(row.tags||[]).slice(0,2).join(" · ")||"热度推荐"):(row.tags||[]).slice(0,2).join(" · ")||"一段故事，值得入戏";
 open.append(el("div","card-subtitle",subtitle));open.onclick=()=>openDrama(row);const save=el("button","card-save"+(isSaved(row)?" saved":""));save.setAttribute("aria-label",(isSaved(row)?"取消收藏 ":"收藏 ")+row.title);save.append(svg("heart"));save.onclick=()=>toggleFavorite(row);wrapper.append(open,save);container.append(wrapper);
 }}
function updateHero(index=0){const rows=state.items.slice(0,5);if(!rows.length)return;state.heroIndex=Math.min(index,rows.length-1);const row=rows[state.heroIndex];$("hero").hidden=state.mode!=="feed";$("hero-title").textContent=row.title;$("hero-platform").textContent=provider()?.name||state.platform;$("hero-description").textContent=row.description||"新的故事，新的心动。从第一集开始，发现属于你的下一部短剧。";const tags=[];if(row.episode_count)tags.push(row.episode_count+" 集");tags.push(provider()?.languages.find(language=>language.id===state.language)?.name||state.language,...(row.tags||[]).slice(0,2));$("hero-tags").replaceChildren(...tags.map(tag=>el("span","",tag)));setImage($("hero-poster"),row.cover,row.title);const safe=safeLink(row.cover);$("hero-backdrop").style.backgroundImage=safe?'url('+JSON.stringify(safe)+')':"none";$("hero-detail").onclick=()=>openDrama(row);$("hero-favorite").onclick=()=>toggleFavorite(row);favoriteButton($("hero-favorite"),row);$("hero-dots").replaceChildren(...rows.map((item,i)=>{const dot=el("button","hero-dot"+(i===state.heroIndex?" active":""));dot.setAttribute("aria-label","精选 "+(i+1)+"："+item.title);dot.onclick=()=>updateHero(i);return dot;}));}
function stopVideo(){state.playRequest++;if(state.hls){state.hls.destroy();state.hls=null;}const video=$("video");video.pause();video.removeAttribute("src");video.replaceChildren();video.load();$("audio-track").hidden=true;}
async function openDrama(row){const request=++state.detailRequest;stopVideo();state.detail=row;state.chapters=[];state.currentEpisode=null;state.resume=row.episode_id?row:readSaved("history").find(item=>key(item)===key(row));$("detail-platform").textContent=provider(row.platform)?.name||row.platform;$("drama-dialog").showModal();$("detail-loading").hidden=false;$("detail-loading").textContent="正在读取剧目和集数…";$("detail-content").hidden=true;$("player-region").hidden=true;const language=row.display_language||state.language;try{
 const [detail,chapters]=await Promise.all([api("/api/"+row.platform+"/detail",{drama_id:row.drama_id,language}),api("/api/"+row.platform+"/episodes",{drama_id:row.drama_id,language})]);if(request!==state.detailRequest||!$("drama-dialog").open)return;state.detail={...detail,display_language:language};state.chapters=chapters.items;$("detail-platform").textContent=provider(row.platform)?.name||row.platform;$("detail-title").textContent=detail.title;$("detail-description").textContent=detail.description||"这个故事还没有简介，先从第一集开始吧。";setImage($("detail-cover"),detail.cover,detail.title);$("detail-tags").replaceChildren(...[detail.episode_count?detail.episode_count+" 集":"",...(detail.tags||[]).slice(0,5),detail.bonus_episode_count?"另有 "+detail.bonus_episode_count+" 条花絮":""].filter(Boolean).map(tag=>el("span","",tag)));const url=safeLink(detail.official_url);$("official-link").hidden=!url;if(url)$("official-link").href=url;favoriteButton($("detail-favorite"),state.detail);$("episode-count").textContent=chapters.items.length+" 条";$("play-first").disabled=!chapters.items.length;$("play-first").lastChild.textContent=state.resume?"继续观看":"开始观看";$("episode-legend").textContent=row.platform==="hongguo"?"官网开放集可播放，其余剧集请前往官方平台":"锁定剧集需在官方平台解锁";renderEpisodes();$("detail-content").hidden=false;$("detail-loading").hidden=true;
 }catch(error){if(request===state.detailRequest)$("detail-loading").textContent=error.message;}}
function renderEpisodes(){const container=$("episodes");container.replaceChildren();for(const row of state.chapters){const button=el("button","episode-button"+(row.locked?" locked":"")+(row.is_blooper?" bonus":"")+(row.episode_id===state.currentEpisode?.episode_id?" current":""));button.textContent=row.is_blooper?"花絮 "+row.index:row.index===0?"预告":String(row.index);button.setAttribute("aria-label",row.label+(row.locked?"（锁定）":""));button.title=row.title+(row.is_blooper?" · "+(row.blooper_title||"花絮"):"");if(row.locked)button.append(svg("lock"));if(row.official_only){button.classList.add("official-only");button.append(svg("globe"));button.setAttribute("aria-label",row.label+"（官方 App）");}button.onclick=()=>playEpisode(row);container.append(button);}}
function playbackMessage(message,link=null,lock=false){const area=$("playback-message");area.replaceChildren(svg(lock?"lock":"play"),el("span","",message));const safe=safeLink(link);if(safe){const anchor=el("a","","前往官方页面 ↗");anchor.href=safe;anchor.target="_blank";anchor.rel="noopener noreferrer";area.append(anchor);}area.hidden=false;}
async function playEpisode(chapter){stopVideo();const request=state.playRequest;state.currentEpisode=chapter;renderEpisodes();$("player-region").hidden=false;$("now-playing").textContent=chapter.label+" · "+state.detail.title;$("playback-note").textContent="正在解析播放方式…";playbackMessage("正在准备这一集…");$("player-region").scrollIntoView({behavior:"smooth",block:"nearest"});const last=state.chapters.findIndex(row=>row.episode_id===chapter.episode_id)===state.chapters.length-1;$("next-episode").hidden=last;try{
 const result=await api("/api/"+state.detail.platform+"/resolvePlayback",{episode_id:chapter.episode_id,language:state.detail.display_language});if(request!==state.playRequest||!$("drama-dialog").open)return;const url=safeLink(result.url);if(result.status!=="playable"||!url){$("playback-note").textContent="";playbackMessage(result.reason==="official_web_episode_limit"?"这一集需要在红果官方 App 中观看。":result.status==="locked"?"这一集需要在官方平台解锁。":result.status==="login_required"?"请先在官方平台登录。":"这一集需要使用官方播放器。",result.official_url,result.status==="locked");return;}
 const video=$("video");$("playback-message").hidden=true;$("playback-note").textContent=result.format==="hls"?"HLS · 可在播放器中选择字幕，支持多音轨的剧目可切换音轨":"MP4 · 播放进度自动保存在本机";for(const sub of result.subtitles||[]){const subUrl=safeLink(sub.url);if(!subUrl)continue;const track=el("track");track.kind="subtitles";track.src=subUrl;track.srclang=sub.language||"en";track.label=sub.language||"字幕";video.append(track);}
 if(result.format==="hls"&&!video.canPlayType("application/vnd.apple.mpegurl")){if(!window.Hls?.isSupported()){playbackMessage("当前浏览器不支持此播放格式。",result.official_url);return;}const hls=new Hls({maxBufferLength:20,maxMaxBufferLength:40,backBufferLength:15});state.hls=hls;hls.loadSource(url);hls.attachMedia(video);hls.on(Hls.Events.MANIFEST_PARSED,()=>{if(request!==state.playRequest)return;const audio=$("audio-track");const tracks=hls.audioTracks||[];audio.replaceChildren(...tracks.map((track,i)=>{const option=el("option","",track.name||track.lang||"音轨 "+(i+1));option.value=i;return option;}));audio.hidden=tracks.length<2;audio.onchange=()=>{hls.audioTrack=Number(audio.value);};video.play().catch(()=>{$("playback-note").textContent="视频已就绪，点击播放器开始观看。";});});hls.on(Hls.Events.ERROR,(_,data)=>{if(request!==state.playRequest||!data.fatal)return;if(data.type===Hls.ErrorTypes.MEDIA_ERROR){hls.recoverMediaError();}else{hls.destroy();if(state.hls===hls)state.hls=null;playbackMessage("播放连接中断，可以重新选集或前往官方页面。",result.official_url);}});
 }else{video.src=url;video.play().catch(()=>{$("playback-note").textContent="视频已就绪，点击播放器开始观看。";});}
 video.onloadedmetadata=()=>{if(request!==state.playRequest)return;if(state.resume?.episode_id===chapter.episode_id&&state.resume.progress>1&&Number.isFinite(video.duration)){video.currentTime=Math.min(state.resume.progress,Math.max(0,video.duration-2));state.resume=null;}};
 video.onerror=()=>{if(request===state.playRequest)playbackMessage("浏览器无法播放这条视频，可以重新选集或前往官方页面。",result.official_url);};rememberProgress(0);
 }catch(error){if(request===state.playRequest){$("playback-note").textContent="";playbackMessage(error.message);}}}
let lastProgress=0;
function rememberProgress(progress){if(!state.detail||!state.currentEpisode)return;const entry={...durable(state.detail),episode_id:state.currentEpisode.episode_id,episode_number:state.currentEpisode.index,progress,watched_at:Date.now()};const history=readSaved("history").filter(row=>key(row)!==key(entry));saveRows("history",[entry,...history]);}
$("video").addEventListener("timeupdate",()=>{if(Date.now()-lastProgress>3000&&$("video").currentTime>0){lastProgress=Date.now();rememberProgress($("video").currentTime);}});
$("video").addEventListener("pause",()=>{if($("video").currentTime>0)rememberProgress($("video").currentTime);});
$("video").addEventListener("ended",()=>{rememberProgress($("video").currentTime);const index=state.chapters.findIndex(row=>row.episode_id===state.currentEpisode?.episode_id);if(state.chapters[index+1]&&!state.chapters[index+1].locked&&!state.chapters[index+1].official_only)playEpisode(state.chapters[index+1]);});
$("next-episode").onclick=()=>{const index=state.chapters.findIndex(row=>row.episode_id===state.currentEpisode?.episode_id);if(state.chapters[index+1])playEpisode(state.chapters[index+1]);};
$("play-first").onclick=()=>{const resume=state.resume&&state.chapters.find(row=>row.episode_id===state.resume.episode_id);const first=resume||state.chapters.find(row=>row.index>0&&!row.locked&&!row.official_only&&!row.is_blooper)||state.chapters[0];if(first)playEpisode(first);};
$("detail-favorite").onclick=()=>{if(state.detail)toggleFavorite(state.detail);};
$("detail-close").onclick=()=>$("drama-dialog").close();$("drama-dialog").addEventListener("close",()=>{state.detailRequest++;stopVideo();});
$("settings-close").onclick=()=>$("settings-dialog").close();$("settings-open").onclick=openSettings;$("empty-action").onclick=openSettings;
async function openSettings(){$("settings-status").textContent="";$("settings-dialog").showModal();try{const value=await api("/api/config");$("backend-url").value=value.hongguo_base_url;$("backend-key").value="";$("key-status").textContent=value.backend_type==="bundled_official_web"?"已连接随包启动的官网后端，内存密钥由服务自动管理。":"密钥仅保存在当前本地服务内存，重启后可重新填写。";$("backend-key").placeholder=value.has_api_key?"已设置，留空保留现有密钥":"输入后端提供的 API Key";}catch(error){$("settings-status").textContent=error.message;}}
$("config-form").onsubmit=async(event)=>{event.preventDefault();const submit=event.submitter;submit.disabled=true;try{const payload={hongguo_base_url:$("backend-url").value.trim()};if($("backend-key").value)payload.api_key=$("backend-key").value;await api("/api/config",payload);state.platforms=await api("/api/platforms");renderPlatforms();$("backend-key").value="";$("settings-status").textContent="已保存。点击测试连接检查后端地址与密钥。";if(state.platform==="hongguo")loadLibrary();}catch(error){$("settings-status").textContent=error.message;}finally{submit.disabled=false;}};
$("test-connection").onclick=async()=>{const button=$("test-connection");button.disabled=true;$("settings-status").textContent="正在检查后端连接…";try{await api("/api/config/test",{});$("settings-status").textContent="连接成功，红果已可以读取片单。";}catch(error){$("settings-status").textContent=error.message;}finally{button.disabled=false;}};
$("search-form").onsubmit=(event)=>{event.preventDefault();const keyword=$("search-input").value.trim();if(!keyword){state.mode="feed";state.keyword="";}else{state.mode="search";state.keyword=keyword;}loadLibrary();};
$("language").onchange=()=>{state.language=$("language").value;loadLibrary();};$("refresh").onclick=()=>loadLibrary();$("load-more").onclick=()=>{if(!state.loading&&state.cursor){$("load-more").disabled=true;loadLibrary(true);}};
document.querySelectorAll(".nav-item").forEach(button=>button.onclick=()=>{
  state.mode=button.dataset.view;
  state.keyword="";
  $("search-input").value="";
  if(state.mode==="rank"){
    updateRankUI();
  }
  loadLibrary();
});
document.querySelectorAll(".rank-plat-btn").forEach(btn=>{
  btn.onclick=()=>{
    const p=btn.dataset.platform;
    if(state.rankPlatform===p)return;
    state.rankPlatform=p;state.language=p==="hongguo"?"zh-CN":"en";
    const cfg=platformRankConfig[p]||platformRankConfig.hongguo;
    state.rankCategory=cfg.defaultCat;
    state.rankBoard=cfg.defaultBoard;
    updateRankUI();
    loadLibrary();
  };
});
async function init(){
  updateFavoriteCount();
  updateRankUI();
  try{
    state.platforms=await api("/api/platforms");
    renderPlatforms();
    renderLanguages();
    loadLibrary();
  }catch(error){
    banner(error.message,true);
    $("result-count").textContent="服务连接失败";
  }
}
init();
