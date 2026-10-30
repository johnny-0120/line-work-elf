"use strict";
let ticket=location.hash.slice(1), model=null, filter="all", timer=null;
history.replaceState(null,"",location.pathname);
const $=id=>document.getElementById(id);
function node(tag,text,cls){const e=document.createElement(tag);e.textContent=text;if(cls)e.className=cls;return e;}
function clear(){model=null;$("summaries").replaceChildren();$("tasks").replaceChildren();$("workspace").hidden=true;$("gate").hidden=false;clearInterval(timer);}
function fail(text){clear();$("error").textContent=text;}
function draw(){if(!model)return;const q=$("search").value.trim().toLowerCase();
 const summaries=model.summaries.filter(x=>(filter==="all"||x.kind===filter)&&[x.text,...(x.actions||[])].join(" ").toLowerCase().includes(q));
 $("summaries").replaceChildren();summaries.forEach(x=>{const card=node("article","","card");card.append(node("p",(x.kind==="work"?"工作群組":"貼上的對話")+" · "+new Date(x.created*1000).toLocaleString("zh-TW",{timeZone:"Asia/Taipei"}),"eyebrow"));card.append(node("p",x.text,"content"));if(x.actions&&x.actions.length){card.append(node("h3","建議下一步（尚未執行）"));const ul=node("ul","");x.actions.forEach(a=>ul.append(node("li",a)));card.append(ul);}$("summaries").append(card);});
 if(!summaries.length)$("summaries").append(node("p",q?"沒有符合搜尋的整理。":"目前沒有整理結果。請回 LINE 按「立即整理」，或等待處理完成後更新。","empty"));
 $("tasks").replaceChildren();const tasks=model.tasks.filter(x=>!x.done&&x.text.toLowerCase().includes(q));tasks.forEach(x=>$("tasks").append(node("p",x.text,"task")));if(!tasks.length)$("tasks").append(node("p","目前沒有符合條件的待辦。","empty"));
 $("summaryCount").textContent=model.summaries.length;$("taskCount").textContent=model.tasks.filter(x=>!x.done).length;$("pendingCount").textContent=model.pending;
 $("status").textContent=model.pending?"整理中，頁面每三十秒更新一次；不必一直重按。":model.failed?"部分貼上對話整理失敗，請回 LINE 重試。":"已更新 · "+new Date(model.updated*1000).toLocaleTimeString("zh-TW",{timeZone:"Asia/Taipei"});
}
const sample={summaries:[{kind:"work",created:1793379600,text:"【活動企劃群】\n共識：報名頁先用簡單表單，等文案確認後再公開。\n待釐清：主視覺定稿時間，以及報名欄位要不要收集飲食需求。\n後續可以做：請負責文案的人回覆預計定稿時間。"},{kind:"conversation",created:1793376000,text:"設計夥伴希望先確認標題與日期，再輸出主視覺。雙方同意先交文字草稿。",actions:["確認活動標題","把文字草稿提供給設計夥伴"]}],tasks:[{text:"回覆設計夥伴：文案何時定稿",done:false},{text:"確認報名表需要哪些欄位",done:false}],pending:0,failed:0,updated:1793380200};
function refresh(){model=sample;$("gate").hidden=true;$("workspace").hidden=false;draw();$("status").textContent="合成資料示範，非真實使用紀錄";}
$("enter").onclick=refresh;$("refresh").onclick=refresh;$("search").oninput=draw;$("logout").onclick=()=>location.href="index.html";document.querySelectorAll("[data-filter]").forEach(b=>b.onclick=()=>{filter=b.dataset.filter;draw();document.querySelectorAll("[data-filter]").forEach(x=>{x.setAttribute("aria-pressed",String(x===b));x.className=x===b?"":"secondary";});});
