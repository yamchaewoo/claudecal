// ClaudeCal 휴대폰판 — 한 줄 적으면 구글 캘린더 일정으로. 서버 없이 휴대폰이 구글·Claude에 직접 연결한다.
//  · 구글: OAuth 암묵적 흐름(리디렉션)으로 1시간짜리 토큰. 만료되면 조용히 다시 받아 온다(prompt=none).
//  · Claude: 사용자가 넣은 본인 API 키로 브라우저에서 바로 호출 (키는 이 휴대폰 localStorage에만).
//  · 안드로이드: 홈 화면에 추가하면 공유 목록에 뜬다 (카톡 메시지 공유 → 바로 등록).
//  · 위쪽 [일정 | 할 일]: '할 일'은 날짜 없는 일 — 구글 할 일(Tasks) 기본 목록. 체크 = 완료, 줄 누르기 = 진행 중('▶ ' 제목 앞).
import { SYSTEM_PROMPT, SCHEMA, MODELS } from "./prompt.js";
import * as C from "./cal.js";

const GOOGLE_CLIENT_ID = "560537057198-3jls07opathhum0n6neh21gt171v0dv7.apps.googleusercontent.com";   // 구글 클라우드 '웹 애플리케이션' 클라이언트 ID (공개돼도 되는 값)
const SCOPE_CAL = "https://www.googleapis.com/auth/calendar.events";
const SCOPE_TASKS = "https://www.googleapis.com/auth/tasks";
const BASES = {cal: "https://www.googleapis.com/calendar/v3/calendars/primary/", tasks: "https://tasks.googleapis.com/tasks/v1/lists/@default/"};
const DOING = "▶ ";
const TASKS_SCOPE_MSG = "할 일을 쓰려면 구글 권한을 하나 더 받아야 해요.";
const REDIRECT = new URL("./", location.href).href;   // 구글 클라이언트에 등록한 리디렉션 주소와 같아야 함
const SDK_URL = "https://cdn.jsdelivr.net/npm/@anthropic-ai/sdk@0.134.0/+esm";
const NET_MSG = "인터넷에 연결하지 못했어요. 연결을 확인해 주세요.";
const DEMO = new URLSearchParams(location.search).get("demo");   // 시험용: ?demo=1 (구글·Claude 가짜) / ?demo=g (구글만 가짜)

const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const store = {   // 이 휴대폰에만 남는 값 (사생활 보호 모드 등에선 안 남을 수 있음)
  get(k, def = null) { try { const v = localStorage.getItem("cc_" + k); return v === null ? def : JSON.parse(v); } catch { return def; } },
  set(k, v) { try { v === null ? localStorage.removeItem("cc_" + k) : localStorage.setItem("cc_" + k, JSON.stringify(v)); } catch { /* 저장 불가 */ } },
};

const S = {
  key: store.get("key", ""), model: store.get("model", "sonnet"), tok: store.get("tok"), email: store.get("email", ""),
  events: [], query: null, undo: [], busy: false, pending: null, authError: null, installEvt: null, loadedAt: 0, hist: [], hIdx: -1,
  tab: store.get("tab", "cal"), scopes: store.get("scopes", ""), tasks: [], tasksErr: null, tasksNeedScope: false,
};
class NeedAuth extends Error {}
class NeedTasksScope extends Error {}
const tokenOk = () => !!DEMO || (S.tok && S.tok.exp > Date.now() + 60e3);
const connected = () => !!DEMO || tokenOk() || !!S.email;   // 한 번이라도 연결했으면 (토큰은 만료됐을 수 있음)
const hasTasksScope = () => !!DEMO || S.scopes.includes("auth/tasks");
function setTok(t) { S.tok = t; store.set("tok", t); }

// ---------------- 구글 로그인 (리디렉션) ----------------
function authRedirect(silent) {   // 구글로 넘어가면 true
  if (DEMO) return false;
  if (!GOOGLE_CLIENT_ID.endsWith(".apps.googleusercontent.com")) { setStatus("구글 연결 설정이 아직 안 돼 있어요.", "err"); return false; }
  if (silent && (S.authError || Date.now() - store.get("authTry", 0) < 15e3)) {   // 방금 실패했으면 [다시 연결] 버튼으로
    render();
    if (!["scope", "access_denied"].includes(S.authError)) setStatus("구글 로그인이 풀렸어요. 위의 [다시 연결]을 눌러 주세요.", "warn");
    return false;
  }
  const state = Array.from(crypto.getRandomValues(new Uint8Array(12)), b => b.toString(16).padStart(2, "0")).join("");
  store.set("state", state);
  store.set("authTry", Date.now());
  // 조용히 다시 받을 땐 이미 받은 권한만 (새 권한을 달면 동의 화면이 필요해서 실패한다)
  const scope = silent ? (S.scopes || SCOPE_CAL) : `${SCOPE_CAL} ${SCOPE_TASKS}`;
  const p = new URLSearchParams({client_id: GOOGLE_CLIENT_ID, redirect_uri: REDIRECT, response_type: "token", scope,
                                 include_granted_scopes: "true", state, prompt: silent ? "none" : "select_account"});
  if (S.email) p.set("login_hint", S.email);
  location.assign("https://accounts.google.com/o/oauth2/v2/auth?" + p);
  return true;
}

function takeAuthResult() {   // 구글에서 돌아오면 주소 # 뒤에 토큰(또는 오류)이 붙어 온다
  if (!/access_token=|error=/.test(location.hash)) return;
  const h = new URLSearchParams(location.hash.slice(1));
  history.replaceState(null, "", location.pathname + location.search);
  if (h.get("state") !== store.get("state")) return;
  store.set("state", null);
  if (h.get("error")) { S.authError = h.get("error"); return; }
  if (!(h.get("scope") || "").includes("calendar.events")) { S.authError = "scope"; return; }
  setTok({token: h.get("access_token"), exp: Date.now() + Number(h.get("expires_in") || 3600) * 1000});
  S.scopes = h.get("scope");
  store.set("scopes", S.scopes);
  S.authError = null;
}

function disconnect() {
  setTok(null);
  S.email = ""; store.set("email", null);
  S.scopes = ""; store.set("scopes", null);
  S.tasks = [];
  S.events = []; S.undo = [];
  render();
  setStatus("이 휴대폰에서 구글 연결을 끊었어요.", "ok");
}

// ---------------- 구글 캘린더 ----------------
async function realGoogle(method, path, {query, body, api = "cal"} = {}) {   // api: cal(캘린더) | tasks(할 일)
  if (!tokenOk()) throw new NeedAuth("구글 로그인이 필요해요.");
  if (api === "tasks" && !hasTasksScope()) throw new NeedTasksScope(TASKS_SCOPE_MSG);
  const url = new URL(BASES[api] + path);
  for (const [k, v] of Object.entries(query || {})) if (v !== null && v !== undefined) url.searchParams.set(k, v);
  let r;
  try {
    r = await fetch(url, {method, body: body ? JSON.stringify(body) : undefined,
                          headers: {Authorization: `Bearer ${S.tok.token}`, ...(body ? {"Content-Type": "application/json"} : {})}});
  } catch { throw new Error(NET_MSG); }
  if (r.status === 401) { setTok(null); throw new NeedAuth("구글 로그인이 만료됐어요."); }
  if (r.status === 204) return null;
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const msg = data.error?.message || "";
    if (api === "tasks" && r.status === 403 && /insufficient|scope/i.test(msg)) throw new NeedTasksScope(TASKS_SCOPE_MSG);
    if (r.status === 403 && /has not been used|disabled/i.test(msg)) throw new Error("구글 클라우드에서 이 기능(API)이 꺼져 있어요. 배포한 사람이 켜야 해요.");
    const err = new Error(r.status === 403 && /insufficient|scope/i.test(msg) ? "캘린더 권한이 없어요. 구글을 다시 연결하고 캘린더 권한에 체크해 주세요."
      : r.status === 404 || r.status === 410 ? "일정을 찾지 못했어요 (이미 지워졌을 수 있어요)." : `구글 오류(${r.status}) ${msg}`.slice(0, 200));
    err.status = r.status;
    throw err;
  }
  return data;
}
let google = realGoogle;
const evPath = id => `events/${encodeURIComponent(id)}`;

async function listRange(f, t) {
  const items = [];
  let pageToken = null;
  do {
    const res = await google("GET", "events", {query: {timeMin: C.parseD(f).toISOString(), timeMax: C.parseD(C.addDays(t, 1)).toISOString(),
                                                     singleEvents: "true", orderBy: "startTime", maxResults: 250, pageToken}});
    items.push(...(res.items || []));
    if (res.summary && res.summary !== S.email && !DEMO) { S.email = res.summary; store.set("email", S.email); }   // 기본 캘린더 이름 = 계정 메일
    pageToken = res.nextPageToken;
  } while (pageToken && items.length <= 2000);
  return items.filter(e => e.status !== "cancelled").map(C.fromGoogle).sort((a, b) => C.sortKey(a) < C.sortKey(b) ? -1 : 1);
}

async function getEvent(id) {
  const ev = await google("GET", evPath(id));
  const u = C.fromGoogle(ev);
  const master = ev.recurringEventId ? await google("GET", evPath(ev.recurringEventId)) : ev;
  u.rrule = C.normRrule((master.recurrence || []).find(r => r.toUpperCase().startsWith("RRULE:")));
  const ms = master.start || {};
  u.rrule_text = C.describeRrule(u.rrule, (ms.dateTime || ms.date || u.date).slice(0, 10));
  return u;
}

// 바꾸는 함수는 되돌리기 기록(list)을 돌려준다 — 위젯의 GoogleStore와 같은 규칙
async function gAdd(spec) {
  const c = await google("POST", "events", {body: C.toGoogle(spec)});
  return [{t: "delete", id: c.id}];
}
async function gUpdate(eid, spec, scope) {
  const ev = await google("GET", evPath(eid));
  const mid = ev.recurringEventId;
  if (mid && scope === "all") {
    const master = await google("GET", evPath(mid));
    if (!spec.rrule) {   // 반복 끄기: 반복 전체를 지우고 단일 일정 하나로
      const rec = await gAdd(spec);
      await google("DELETE", evPath(mid));
      return [...rec, {t: "undelete", id: mid, body: C.snapshot(master)}];
    }
    // 고른 회차에서 옮긴 만큼 반복 시작일도 옮긴다
    const shift = C.dayDiff(spec.date, C.gDay(ev.originalStartTime || ev.start));
    const mDate = C.addDays(C.gDay(master.start), shift);
    const mspec = {...spec, date: mDate, end_date: C.addDays(mDate, C.dayDiff(spec.end_date, spec.date))};
    await google("PATCH", evPath(mid), {body: C.toGoogle(mspec, true)});
    return [{t: "patch", id: mid, body: C.snapshot(master)}];
  }
  if (mid) {   // 반복의 한 회차만
    await google("PATCH", evPath(eid), {body: C.toGoogle(spec, true, false)});
    return [{t: "patch", id: eid, body: C.snapshot(ev, false)}];
  }
  const body = C.toGoogle(spec, true);
  if (!spec.rrule && ev.recurrence) body.recurrence = null;
  await google("PATCH", evPath(eid), {body});
  const snap = C.snapshot(ev);
  if (!("recurrence" in snap)) snap.recurrence = null;
  return [{t: "patch", id: eid, body: snap}];
}
async function gDelete(eid, scope) {
  const ev = await google("GET", evPath(eid));
  const mid = ev.recurringEventId;
  const [target, snap] = mid && scope === "all" ? [mid, C.snapshot(await google("GET", evPath(mid)))] : [eid, C.snapshot(ev, !mid)];
  await google("DELETE", evPath(target));
  return [{t: "undelete", id: target, body: snap}];
}
async function gSetDone(eid, done) {
  await google("PATCH", evPath(eid), {body: C.doneBody(done)});
  return [{t: "patch", id: eid, body: C.doneBody(!done)}];
}
async function gUndo(records) {
  for (const r of [...records].reverse()) {
    if (r.t === "delete") {
      try { await google("DELETE", evPath(r.id)); } catch (e) { if (![404, 410].includes(e.status)) throw e; }
    } else if (r.t === "patch") {
      await google("PATCH", evPath(r.id), {body: r.body});
    } else if (r.t === "undelete") {
      try {   // 지운 일정은 '취소됨' 상태로 남아 있어서 되살릴 수 있다
        await google("PATCH", evPath(r.id), {body: {status: "confirmed", ...r.body}});
      } catch (e) {
        if (e instanceof NeedAuth) throw e;
        await google("POST", "events", {body: C.clean(r.body)});
      }
    } else if (r.t === "task_delete") {
      try { await google("DELETE", `tasks/${encodeURIComponent(r.id)}`, {api: "tasks"}); } catch (e) { if (![404, 410].includes(e.status)) throw e; }
    } else if (r.t === "task_patch") {
      await google("PATCH", `tasks/${encodeURIComponent(r.id)}`, {api: "tasks", body: r.body});
    } else if (r.t === "task_insert") {
      await google("POST", "tasks", {api: "tasks", body: C.clean(r.body)});
    }
  }
}

// ---------------- 기약 없는 할 일 (구글 할 일 기본 목록) — 위젯의 GoogleStore와 같은 규칙 ----------------
const taskPath = id => `tasks/${encodeURIComponent(id)}`;
function taskFromGoogle(t) {
  const title = t.title || "", doing = title.startsWith(DOING);
  return {id: t.id, title: doing ? title.slice(DOING.length) : title, state: t.status === "completed" ? "done" : doing ? "doing" : "todo",
          notes: t.notes || "", done_at: t.completed || "", pos: t.position || ""};
}
async function listTasks() {
  const items = [];
  let pageToken = null;
  do {
    const res = await google("GET", "tasks", {api: "tasks", query: {showCompleted: "true", showHidden: "false", maxResults: 100, pageToken}});
    items.push(...(res.items || []));
    pageToken = res.nextPageToken;
  } while (pageToken && items.length <= 1000);
  const all = items.filter(t => !t.deleted && (t.title || "").trim()).map(taskFromGoogle);
  const active = all.filter(t => t.state !== "done").sort((a, b) => (a.state !== "doing") - (b.state !== "doing") || (a.pos < b.pos ? -1 : 1));
  const done = all.filter(t => t.state === "done").sort((a, b) => (a.done_at < b.done_at ? 1 : -1)).slice(0, 50);
  return [...active, ...done];
}
async function tAdd(title) {
  const t = await google("POST", "tasks", {api: "tasks", body: {title}});   // 맨 위에 들어간다
  return [{t: "task_delete", id: t.id}];
}
async function tSet(id, state) {
  const old = await google("GET", taskPath(id), {api: "tasks"});
  const body = {title: (state === "doing" ? DOING : "") + taskFromGoogle(old).title, status: state === "done" ? "completed" : "needsAction"};
  if (state !== "done") body.completed = null;
  await google("PATCH", taskPath(id), {api: "tasks", body});
  const back = {title: old.title || "", status: old.status || "needsAction"};
  if (back.status !== "completed") back.completed = null;
  return [{t: "task_patch", id, body: back}];
}
async function tNote(id, notes) {
  const old = await google("GET", taskPath(id), {api: "tasks"});
  await google("PATCH", taskPath(id), {api: "tasks", body: {notes: notes || null}});
  return [{t: "task_patch", id, body: {notes: old.notes ?? null}}];
}
async function tDelete(id) {
  const old = await google("GET", taskPath(id), {api: "tasks"});
  await google("DELETE", taskPath(id), {api: "tasks"});
  return [{t: "task_insert", body: {title: old.title, notes: old.notes ?? null, status: old.status, due: old.due ?? null}}];
}

// ---------------- Claude ----------------
let sdkPromise = null;
const loadSdk = () => (sdkPromise ??= import(SDK_URL).then(m => m.default || m.Anthropic).catch(e => { sdkPromise = null; throw e; }));

function apiErr(e, A) {
  const s = e?.status;
  if (s === 401) return "API 키가 맞지 않아요. ⚙ 설정에서 키를 다시 확인해 주세요.";
  if (s === 403) return "이 API 키로는 이 모델을 쓸 수 없어요. ⚙ 설정에서 다른 모델을 골라 보세요.";
  if (s === 404) return "모델을 찾지 못했어요. ⚙ 설정에서 다른 모델을 골라 보세요.";
  if (s === 429) return "요청이 잠깐 너무 많아요. 조금 뒤에 다시 해 주세요.";
  if (s === 400) {
    const m = String(e.message || "");
    return /credit|balance/i.test(m) ? "API 크레딧이 부족해요. Anthropic 콘솔의 Billing에서 확인해 주세요." : "요청 오류: " + m.slice(0, 160);
  }
  if (s) return `Claude 서버 오류(${s}). 조금 뒤에 다시 해 주세요.`;
  if (!A || e instanceof A.APIConnectionError || e instanceof TypeError) return "Claude에 연결하지 못했어요. 인터넷 연결을 확인해 주세요.";
  return String(e?.message || e).slice(0, 200);
}

async function realClaude(text, list) {
  let A;
  try { A = await loadSdk(); } catch { throw new Error("Claude 연결 부품을 받지 못했어요. 인터넷 연결을 확인해 주세요."); }
  const client = new A({apiKey: S.key, dangerouslyAllowBrowser: true, timeout: 60e3, maxRetries: 2});
  const model = MODELS[S.model] || MODELS.sonnet;
  const kw = {model, max_tokens: 8000, system: SYSTEM_PROMPT,
              messages: [{role: "user", content: C.buildUser(text, list, new Date())}],
              // 짧은 해석이라 effort는 low, 응답은 스키마에 맞는 JSON으로 고정
              output_config: {effort: "low", format: {type: "json_schema", schema: SCHEMA}}};
  let msg;
  try {
    msg = /^claude-(sonnet|opus)/.test(model)
      // 안전 분류기가 오판해 거절하면 서버가 알맞은 다른 모델로 다시 처리 (Haiku는 지원 안 함)
      ? await client.beta.messages.create({...kw, betas: ["server-side-fallback-2026-07-01"], fallbacks: "default"})
      : await client.messages.create(kw);
  } catch (e) { throw new Error(apiErr(e, A)); }
  if (msg.stop_reason === "refusal") throw new Error("Claude가 이 요청은 처리하지 않았어요. 다르게 적어 주세요.");
  if (msg.stop_reason === "max_tokens") throw new Error("한 번에 처리하기엔 내용이 너무 많아요. 나눠서 적어 주세요.");
  return C.parseResponse(msg.content.filter(b => b.type === "text").map(b => b.text).join(""));
}
let claude = realClaude;

async function testKey(key) {   // 토큰을 쓰지 않는 모델 정보 조회로 키가 맞는지만 확인. 문제 없으면 null
  if (DEMO === "1") return null;
  let A;
  try { A = await loadSdk(); } catch { return "Claude 연결 부품을 받지 못했어요. 인터넷 연결을 확인해 주세요."; }
  try {
    await new A({apiKey: key, dangerouslyAllowBrowser: true, timeout: 20e3, maxRetries: 1}).models.retrieve(MODELS[S.model] || MODELS.sonnet);
    return null;
  } catch (e) { return apiErr(e, A); }
}

// ---------------- 해석 → 할 일 ----------------
async function makePlan(data, sidMap) {
  const plan = [];
  let query = null;
  for (const a of data.actions) {
    if (a.op === "add") {
      const spec = C.normalize(a);
      if (spec) plan.push({op: "add", spec, desc: "추가 · " + C.describe(spec)});
    } else if ((a.op === "update" || a.op === "delete") && sidMap[a.sid]) {
      const ev = sidMap[a.sid];
      const scope = ev.recurring && a.scope === "all" ? "all" : "one";
      const tail = scope === "all" ? " (반복 전체)" : "";
      if (a.op === "delete") {
        const shown = scope === "all" ? await getEvent(ev.id) : ev;   // 반복 전체면 '매주 화·목 …'으로 보여 주기
        plan.push({op: "delete", id: ev.id, scope, desc: "삭제 · " + C.describe(shown) + tail});
      } else {
        const old = await getEvent(ev.id);
        if (scope === "one") old.rrule = null;
        const spec = C.normalize(C.mergeChanges(old, a));
        if (spec) plan.push({op: "update", id: ev.id, scope, spec, desc: `변경 · ${C.describe(ev)} → ${C.describe(spec)}${tail}`});
      }
    } else if (a.op === "query") {
      const f = C.validDate(a.from), t = C.validDate(a.to);
      if (f) query = {from: f, to: t && t >= f ? t : f};
    }
  }
  return {plan, query};
}

async function executePlan(plan) {   // 실패하면 그때까지 한 것을 되돌리고 오류
  const batch = [];
  try {
    for (const p of plan)
      batch.push(p.op === "add" ? await gAdd(p.spec) : p.op === "update" ? await gUpdate(p.id, p.spec, p.scope) : await gDelete(p.id, p.scope));
  } catch (e) {
    try { for (const rec of [...batch].reverse()) await gUndo(rec); } catch { /* 되돌리기도 실패하면 원래 오류를 보여 준다 */ }
    throw e;
  }
  return batch;
}

// ---------------- 입력 ----------------
async function submit(text) {
  text = (text ?? $("#q").value).trim();
  if (!text || S.busy) return;
  if (S.tab === "tasks") return addTask(text);   // 할 일은 Claude 없이 그대로 적는다
  if (!S.key) { setStatus("먼저 Claude API 키를 넣어 주세요.", "warn"); render(); return; }
  if (!tokenOk()) {   // 토큰이 만료됐으면 글을 맡겨 두고 구글에 다녀온다
    if (!connected()) { setStatus("먼저 구글 캘린더를 연결해 주세요.", "warn"); render(); return; }
    store.set("pending", {text, at: Date.now()});
    S.authError = null;
    store.set("authTry", 0);
    return authRedirect(true);
  }
  S.busy = true;
  setBusy("Claude가 읽는 중");
  S.hist = [text, ...S.hist.filter(x => x !== text)].slice(0, 30);
  S.hIdx = -1;
  try {
    const today = C.isoD(new Date());
    loadSdk().catch(() => {});
    const near = (await listRange(C.addDays(today, -7), C.addDays(today, 90))).slice(0, 200);
    const {list, sidMap} = C.brief(near);
    const data = await claude(text, list);
    const {plan, query} = await makePlan(data, sidMap);
    setInput("");
    if (query) S.query = query;
    if (!plan.length) {
      setStatus(data.reply || (query ? "" : "할 일을 찾지 못했어요. 날짜와 할 일을 같이 적어 주세요."), query || data.reply ? "ok" : "warn");
      await refresh();
    } else if (plan.some(p => p.op === "delete" || p.scope === "all")) {
      askConfirm(plan, data.reply, text);
    } else {
      await run(plan, data.reply, text);
    }
  } catch (e) {
    handleErr(e, text);
  } finally {
    S.busy = false;
    $("#send").disabled = false;
  }
}

async function run(plan, reply, text) {
  setBusy("구글 캘린더에 넣는 중");
  const batch = await executePlan(plan);
  S.undo.push({desc: text, batch});
  S.undo = S.undo.slice(-30);
  if (!reply)
    reply = plan.map(p => p.desc.slice(p.desc.indexOf(" · ") + 3)).join(" / ") + (plan.every(p => p.op === "add") ? " 등록했어요" : " 처리했어요");
  setStatus(reply, "ok", true);
  await refresh();
}

function askConfirm(plan, reply, text) {
  S.pending = {plan, reply, text};
  $("#cfText").textContent = reply || "이렇게 할까요?";
  $("#cfList").innerHTML = plan.map(p => `<li>${esc(p.desc)}</li>`).join("");
  $("#confirm").hidden = false;
  setStatus("");
}
async function answerConfirm(yes) {
  const job = S.pending;
  S.pending = null;
  $("#confirm").hidden = true;
  if (!job) return;
  if (!yes) { setStatus("취소했어요. 아무것도 바꾸지 않았어요."); return; }
  S.busy = true;
  try { await run(job.plan, job.reply, job.text); } catch (e) { handleErr(e); } finally { S.busy = false; }
}

function handleErr(e, text) {
  if (e instanceof NeedAuth) {
    if (text) store.set("pending", {text, at: Date.now()});
    store.set("authTry", 0);
    return authRedirect(true);
  }
  if (e instanceof NeedTasksScope) Object.assign(S, {tasksNeedScope: true, tasksErr: e.message});
  if (text && !$("#q").value) setInput(text);   // 못 넣은 글은 입력 칸에 돌려놓는다
  setStatus(e.message || String(e), "err");
}

async function undo() {
  const item = S.undo.pop();
  if (!item || S.busy) return;
  S.busy = true;
  setBusy("되돌리는 중");
  try {
    for (const rec of [...item.batch].reverse()) await gUndo(rec);
    setStatus(`되돌렸어요: “${item.desc.slice(0, 30)}”`, "ok", S.undo.length > 0);
    await refresh();
  } catch (e) { handleErr(e); } finally { S.busy = false; }
}

// 완료 체크: 화면은 바로 바꾸고, 저장이 실패하면 되돌린다
const doneBusy = new Set();
function paintDone(id, done) {
  for (const e of S.events) if (e.id === id) e.done = done;
  document.querySelectorAll("#list [data-id]").forEach(el => {
    if (el.dataset.id !== id) return;
    el.classList.toggle("done", done);
    el.querySelector(".ck")?.setAttribute("aria-checked", done);
  });
  if (S.sheetEv?.id === id) { S.sheetEv.done = done; $("#shDone").textContent = done ? "완료 취소" : "완료"; }
}
async function toggleDone(id) {
  const ev = S.events.find(e => e.id === id);
  if (!ev || doneBusy.has(id)) return;
  const done = !ev.done;
  doneBusy.add(id);
  paintDone(id, done);
  try {
    const rec = await gSetDone(id, done);
    S.undo.push({desc: (done ? "완료: " : "완료 취소: ") + ev.title, batch: [rec]});
    setStatus((done ? "완료했어요: " : "완료를 취소했어요: ") + ev.title, "ok", true);
  } catch (e) {
    paintDone(id, !done);
    handleErr(e);
  } finally { doneBusy.delete(id); }
}

// ---------------- 기약 없는 할 일 화면 ----------------
let taskSeq = 0;
async function refreshTasks() {
  if (!tokenOk()) { render(); return; }
  const seq = ++taskSeq;
  $("#reload").classList.add("spin");
  try {
    const tasks = await listTasks();
    if (seq !== taskSeq) return;
    Object.assign(S, {tasks, tasksErr: null, tasksNeedScope: false, tasksAt: Date.now()});
  } catch (e) {
    if (seq !== taskSeq) return;
    if (e instanceof NeedAuth) { authRedirect(true); return; }
    Object.assign(S, {tasksErr: e.message, tasksNeedScope: e instanceof NeedTasksScope});
  } finally { $("#reload").classList.remove("spin"); }
  render();
}
async function addTask(text) {
  const title = text.split(/\s+/).join(" ").slice(0, 300);
  setInput("");
  S.busy = true;
  try {
    const rec = await tAdd(title);
    S.undo.push({desc: "할 일: " + title, batch: [rec]});
    setStatus("할 일 추가: " + title, "ok", true);
    await refreshTasks();
  } catch (e) { handleErr(e, text); render(); } finally { S.busy = false; }
}
async function taskAction(id, kind) {   // kind: check(완료 켜기/끄기) | row(진행 중 켜기/끄기) | delete | clear
  if (S.busy) return;
  const t = S.tasks.find(x => x.id === id);
  let state = null;
  if (kind === "check") state = t.state === "done" ? "todo" : "done";
  else if (kind === "row") { if (t.state === "done") return; state = t.state === "doing" ? "todo" : "doing"; }
  if (state) { t.state = state; render(); }   // 화면은 바로 바꾸고 저장 결과로 다시 그린다
  S.busy = true;
  try {
    let recs, msg;
    if (state) {
      recs = [await tSet(id, state)];
      msg = {done: "완료했어요: ", doing: "진행 중: ", todo: "할 일로 되돌렸어요: "}[state] + t.title;
    } else if (kind === "delete") {
      recs = [await tDelete(id)];
      msg = "지웠어요: " + t.title;
    } else {
      recs = [];
      for (const x of S.tasks.filter(x => x.state === "done")) recs.push(await tDelete(x.id));
      msg = "완료한 할 일을 지웠어요";
    }
    if (recs.length) S.undo.push({desc: msg, batch: recs});
    setStatus(msg, "ok", recs.length > 0);
  } catch (e) { handleErr(e); } finally { S.busy = false; }
  await refreshTasks();
}
function taskRow(t) {
  const first = (t.notes || "").split("\n")[0];
  return `<div class="tk ${t.state}" data-tid="${esc(t.id)}">
    <button class="ck" data-tck="${esc(t.id)}" role="checkbox" aria-checked="${t.state === "done"}" aria-label="완료"></button>
    <div class="tk-b"><div class="tk-t">${esc(t.title)}${t.state === "doing" ? `<span class="tk-badge">진행 중</span>` : ""}</div>
      ${first ? `<div class="tk-n">${esc(first)}</div>` : ""}</div>
    <button class="tk-m${t.notes ? " has" : ""}" data-tmemo="${esc(t.id)}" aria-label="메모">📝</button>
    <button class="tk-x" data-tdel="${esc(t.id)}" aria-label="지우기">✕</button></div>`;
}
function openMemo(id) {
  const t = S.tasks.find(x => x.id === id);
  if (!t) return;
  S.memoTask = t;
  $("#mmTitle").textContent = t.title;
  $("#mmText").value = t.notes || "";
  $("#memo").hidden = false;
  $("#mmText").focus();
}
async function saveMemo() {   // 닫으면 늘 저장 (메모장처럼). 바뀐 게 없으면 그냥 닫는다
  const t = S.memoTask, notes = $("#mmText").value.trim();
  $("#memo").hidden = true;
  S.memoTask = null;
  if (!t || notes === (t.notes || "").trim() || S.busy) return;
  t.notes = notes;
  render();
  S.busy = true;
  try {
    const rec = await tNote(t.id, notes);
    S.undo.push({desc: "메모: " + t.title, batch: [rec]});
    setStatus((notes ? "메모 저장: " : "메모 지움: ") + t.title, "ok", true);
  } catch (e) { handleErr(e); } finally { S.busy = false; }
  await refreshTasks();
}
function renderTasks() {
  if (!tokenOk()) return "";
  if (S.tasksErr)
    return `<div class="card"><p>${esc(S.tasksErr)}</p>` +
      (S.tasksNeedScope ? `<button class="btn primary wide" id="tkScope">구글 다시 연결 (할 일 권한 추가)</button>
        <div class="hint" style="margin-top:8px">"확인되지 않은 앱" → 고급 → ClaudeCal(으)로 이동, 그리고 <b>할 일 권한에 체크</b>해 주세요.</div>` : "") + `</div>`;
  if (!S.tasks.length)
    return `<div class="empty"><p>날짜 없이 언젠가 해야 할 일을 아래 칸에 적어 두세요.</p><p class="hint">☐ 체크 = 완료 · 줄 누르기 = 진행 중</p></div>`;
  const active = S.tasks.filter(t => t.state !== "done"), done = S.tasks.filter(t => t.state === "done");
  return `<div class="dayh">할 일 <span class="rel">${active.length || ""}</span></div>` + active.map(taskRow).join("") +
    (done.length ? `<div class="dayh tk-sec">완료 ${done.length}<button class="link" id="tkClear">모두 지우기</button></div>` + done.map(taskRow).join("") : "");
}
function setTab(tab) {
  if (S.tab === tab) return;
  S.tab = tab;
  store.set("tab", tab);
  setStatus("");
  render();
  if (tab === "tasks") { if (!S.tasksAt || Date.now() - S.tasksAt > 30e3) refreshTasks(); }
  else if (Date.now() - S.loadedAt > 30e3) refresh();
}

// ---------------- 불러오기·그리기 ----------------
let loadSeq = 0;
async function refresh() {
  if (S.tab === "tasks") return refreshTasks();
  if (!tokenOk()) { render(); return; }
  const seq = ++loadSeq, today = C.isoD(new Date());
  const [f, t] = S.query ? [S.query.from, S.query.to] : [today, C.addDays(today, 14)];
  $("#reload").classList.add("spin");
  try {
    const evs = await listRange(f, t);
    if (seq !== loadSeq) return;
    S.events = evs;
    S.loadedAt = Date.now();
  } catch (e) {
    if (seq !== loadSeq) return;
    if (e instanceof NeedAuth) { authRedirect(true); return; }
    setStatus(e.message, "err");
  } finally { $("#reload").classList.remove("spin"); }
  render();
}

const isPast = ev => (ev.time ? new Date(`${ev.end_date}T${ev.end_time || ev.time}`) : C.parseD(C.addDays(ev.end_date, 1))) < new Date();
function remindText(r) {
  if (r === null || r === undefined) return "";
  if (r < 0) return "🔕 알림 없음";
  if (r === 0) return "🔔 시작할 때";
  if (r % 1440 === 0) return `🔔 ${r / 1440}일 전`;
  if (r % 60 === 0) return `🔔 ${r / 60}시간 전`;
  return `🔔 ${r}분 전`;
}
function evRow(ev) {
  const past = isPast(ev);
  const cls = ["ev", ev.time ? "" : "allday", ev.recurring ? "rep" : "", past ? "past" : "", ev.done ? "done" : ""].join(" ");
  const tm = ev.time ? `${ev.time}${ev.end_time && ev.end_date === ev.date ? `<small>~${ev.end_time}</small>` : ""}` : "종일";
  const meta = [ev.location && "📍 " + ev.location, ev.end_date !== ev.date && `~${C.dlabel(ev.end_date)}`, ev.recurring && "🔁 반복", remindText(ev.remind)]
    .filter(Boolean).join(" · ");
  return `<div class="${cls}" data-id="${esc(ev.id)}">
    <button class="ck" data-done="${esc(ev.id)}" role="checkbox" aria-checked="${!!ev.done}" aria-label="완료"></button>
    <div class="bar"></div><div class="tm">${tm}</div>
    <div class="bd"><div class="tt">${esc(ev.title)}</div>${meta ? `<div class="mt">${esc(meta)}</div>` : ""}</div></div>`;
}

const EXAMPLES = ["내일 3시 치과", "금요일 저녁 7시 강남에서 동기 모임", "매주 화목 1시 수업 12월까지", "이번주 일정 보여줘"];
function render() {
  const needGoogle = !connected(), needKey = !S.key;
  $("#setup").hidden = !(needGoogle || needKey);
  $("#stepG").classList.toggle("ok", !needGoogle);
  $("#stepK").classList.toggle("ok", !needKey);
  $("#gBtn").hidden = !needGoogle;
  $("#gDone").textContent = needGoogle ? "" : `연결됨 · ${S.email || "구글 캘린더"}`;
  $("#kForm").hidden = !needKey;
  $("#kDone").textContent = needKey ? "" : "저장됨";
  $("#relogin").hidden = !(connected() && !tokenOk());
  $("#installHint").hidden = !(S.installEvt || isIos());
  document.querySelectorAll("#tabs button").forEach(b => b.classList.toggle("on", b.dataset.tab === S.tab));
  $("#q").placeholder = S.tab === "tasks" ? "날짜 없는 할 일 적고 ➤" : "모레 7시 강남 동기 모임";
  if (S.tab === "tasks") { $("#list").innerHTML = renderTasks(); return; }

  const td = C.isoD(new Date());
  let h = "";
  if (S.query) {
    const same = S.query.from === S.query.to;
    h += `<div class="qhead"><button class="link" id="qBack">← 다가오는 일정</button><b>${same ? C.dlabel(S.query.from) : C.dlabel(S.query.from) + " ~ " + C.dlabel(S.query.to)}</b></div>`;
  }
  if (!tokenOk()) {
    $("#list").innerHTML = h;
  } else if (!S.events.length) {
    h += S.query ? `<div class="empty">일정이 없어요.</div>`
      : `<div class="empty"><p>다가오는 2주 일정이 없어요. 아래 칸에 대충 적어 보세요.</p>` +
        EXAMPLES.map(x => `<button class="ex" data-ex="${esc(x)}">${esc(x)}</button>`).join("") + `</div>`;
    $("#list").innerHTML = h;
  } else {
    const byDay = new Map();
    const from = S.query ? S.query.from : td;
    if (!S.query) byDay.set(td, []);
    for (const ev of S.events) {
      const d = ev.date < from ? from : ev.date;   // 범위 전에 시작해 이어지는 일정은 첫날에
      if (!byDay.has(d)) byDay.set(d, []);
      byDay.get(d).push(ev);
    }
    for (const d of [...byDay.keys()].sort()) {
      const diff = C.dayDiff(d, td), rel = {0: "오늘", 1: "내일", 2: "모레"}[diff] || "";
      const evs = byDay.get(d), dd = C.parseD(d);
      h += `<div class="dayh ${diff === 0 ? "today" : ""} ${C.wday(dd) === 6 ? "sun" : C.wday(dd) === 5 ? "sat" : ""}">${C.dlabel(d)}` +
        `${rel ? ` <span class="rel">${rel}</span>` : ""}</div>` +
        (evs.length ? evs.map(evRow).join("") : `<div class="noev">일정 없음</div>`);
    }
    $("#list").innerHTML = h;
  }
  $("#qBack")?.addEventListener("click", () => { S.query = null; refresh(); });
}

// ---------------- 상태 줄 ----------------
let busyTimer = null;
let statusTimer = null;
function setStatus(text, kind = "dim") {
  clearInterval(busyTimer);
  clearTimeout(statusTimer);
  $("#status").className = "status " + kind;
  $("#status").hidden = !text;
  $("#stText").textContent = text || "";
  $("#send").classList.remove("busy");
  if (text && kind !== "busy")   // 안내는 2초, 오류·주의는 읽을 수 있게 6초 뒤 사라진다
    statusTimer = setTimeout(() => setStatus(""), kind === "err" || kind === "warn" ? 6e3 : 2e3);
}
function setBusy(text) {
  const t0 = Date.now();
  setStatus(text + "…", "busy");
  $("#send").classList.add("busy");
  busyTimer = setInterval(() => {
    const s = Math.floor((Date.now() - t0) / 1000);
    $("#stText").textContent = text + "…" + (s >= 2 ? ` ${s}초` : "");
  }, 500);
}
function setInput(v) {
  $("#q").value = v;
  store.set("draft", v || null);
  autosize();
}
function autosize() {
  const q = $("#q");
  q.style.height = "auto";
  q.style.height = Math.min(q.scrollHeight, 120) + "px";
}

// ---------------- 일정 상세 ----------------
async function openSheet(id) {
  const ev = S.events.find(e => e.id === id);
  if (!ev) return;
  S.sheetEv = ev;
  $("#shTitle").textContent = ev.title;
  const when = ev.time ? `${C.dlabel(ev.date)} ${ev.time}~${ev.end_date !== ev.date ? C.dlabel(ev.end_date) + " " : ""}${ev.end_time || ""}`
    : C.dlabel(ev.date) + (ev.end_date !== ev.date ? ` ~ ${C.dlabel(ev.end_date)}` : "") + " 종일";
  $("#shWhen").textContent = when;
  $("#shRule").textContent = ev.recurring ? "🔁 반복 일정" : "";
  $("#shLoc").textContent = ev.location ? "📍 " + ev.location : "";
  $("#shNote").textContent = ev.note || "";
  $("#shDone").textContent = ev.done ? "완료 취소" : "완료";
  $("#shDel").textContent = "삭제";
  $("#shDel").classList.remove("armed");
  $("#shScope").hidden = true;
  $("#shOpen").hidden = !ev.link;
  $("#shOpen").href = ev.link || "#";
  $("#sheet").hidden = false;
  if (ev.recurring && !DEMO) {
    try { const full = await getEvent(id); if (S.sheetEv === ev) $("#shRule").textContent = "🔁 " + (full.rrule_text || "반복 일정"); } catch { /* 반복 설명은 없어도 된다 */ }
  }
}
function closeSheet() { $("#sheet").hidden = true; S.sheetEv = null; }
async function deleteFromSheet(scope) {
  const ev = S.sheetEv;
  if (!ev || S.busy) return;
  if (!scope) {
    if (ev.recurring) { $("#shScope").hidden = false; return; }
    if (!$("#shDel").classList.contains("armed")) {
      $("#shDel").classList.add("armed");
      $("#shDel").textContent = "한 번 더 누르면 삭제";
      return;
    }
    scope = "one";
  }
  closeSheet();
  S.busy = true;
  setBusy("지우는 중");
  try {
    const rec = await gDelete(ev.id, scope);
    S.undo.push({desc: "삭제: " + ev.title, batch: [rec]});
    setStatus(`삭제했어요: ${ev.title}` + (scope === "all" ? " (반복 전체)" : ""), "ok", true);
    await refresh();
  } catch (e) { handleErr(e); } finally { S.busy = false; }
}

// ---------------- 설정 ----------------
function openSettings() {
  $("#sKey").value = "";
  $("#sKey").placeholder = S.key ? `저장됨: ${S.key.slice(0, 7)}…${S.key.slice(-4)} (바꾸려면 새 키 붙여넣기)` : "sk-ant-로 시작하는 키 붙여넣기";
  document.querySelectorAll("#sModels button").forEach(b => b.classList.toggle("on", b.dataset.m === S.model));
  $("#sAcct").textContent = DEMO ? "시험 모드" : connected() ? `연결됨 · ${S.email || "구글 캘린더"}` : "연결 안 됨";
  $("#sConnect").textContent = connected() ? "다시 연결" : "구글 캘린더 연결";
  $("#sDisconnect").hidden = !connected() || !!DEMO;
  $("#sInstall").hidden = !S.installEvt;
  $("#settings").hidden = false;
}
async function saveKey(inputSel) {
  const key = $(inputSel).value.trim();
  const msg = $(inputSel === "#kInput" ? "#kMsg" : "#sMsg");
  if (!key) return;
  if (!key.startsWith("sk-ant-")) { msg.textContent = "Anthropic API 키는 sk-ant- 로 시작해요."; return; }
  msg.textContent = "키 확인 중…";
  const problem = await testKey(key);
  if (problem) { msg.textContent = problem; return; }
  S.key = key;
  store.set("key", key);
  msg.textContent = "저장했어요.";
  $(inputSel).value = "";
  render();
  if (!S.events.length) refresh();
  resumePending();
}
const isIos = () => /iPhone|iPad|iPod/.test(navigator.userAgent) && !navigator.standalone;

// ---------------- 공유로 받은 글 ----------------
function takeShared() {   // 안드로이드 공유 목록에서 오면 ?title=&text=&url= 로 열린다
  const p = new URLSearchParams(location.search);
  const parts = ["title", "text", "url"].map(k => (p.get(k) || "").trim()).filter(Boolean);
  const uniq = parts.filter((x, i) => !parts.some((y, j) => j !== i && (y === x ? j < i : y.includes(x))));   // 겹치는 건 한 번만
  if (!uniq.length) return;
  store.set("pending", {text: uniq.join("\n").slice(0, 3000), at: Date.now()});
  history.replaceState(null, "", location.pathname + (DEMO ? `?demo=${DEMO}` : ""));
  S.tab = "cal";   // 공유받은 글은 일정으로 (Claude가 날짜를 읽는다)
  store.set("tab", "cal");
}
function resumePending() {   // 공유받았거나 구글 로그인 다녀오느라 맡겨 둔 글이 있으면 이어서 등록
  const p = store.get("pending");
  if (!p) return;
  if (Date.now() - p.at > 10 * 60e3) { store.set("pending", null); return; }
  setInput(p.text);
  if (!S.key || !connected()) { setStatus("공유받은 글을 넣어 뒀어요. 위 설정을 마치면 등록할게요.", "warn"); return; }
  store.set("pending", null);
  submit(p.text);
}

// ---------------- 시작 ----------------
async function init() {
  if (DEMO) {
    const demo = await import("./demo.js");
    google = demo.fakeGoogle;
    if (DEMO === "1") { claude = demo.fakeClaude; S.key ||= "sk-ant-demo"; }
  }
  takeAuthResult();
  takeShared();
  const draft = store.get("draft");
  if (draft && !$("#q").value) { $("#q").value = draft; autosize(); }
  bind();
  render();
  if (S.authError === "scope") setStatus("캘린더 권한에 체크해야 쓸 수 있어요. 다시 연결해 주세요.", "warn");
  else if (S.authError === "access_denied") setStatus("구글 연결을 취소했어요.", "warn");
  if (connected() && !tokenOk() && authRedirect(true)) return;
  if (S.key) loadSdk().catch(() => {});   // Claude 부품 미리 받아 두기
  await refresh();
  resumePending();
  if ("serviceWorker" in navigator && !DEMO) navigator.serviceWorker.register("./sw.js").catch(() => {});
}

function bind() {
  const q = $("#q");
  q.addEventListener("input", () => { autosize(); store.set("draft", q.value || null); });
  q.addEventListener("keydown", e => {
    if (e.isComposing || e.keyCode === 229) return;   // 한글 조합 중 Enter는 무시
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); }
    else if (e.key === "ArrowUp" && S.hist.length && (!q.value || S.hIdx >= 0)) {
      e.preventDefault(); S.hIdx = Math.min(S.hIdx + 1, S.hist.length - 1); setInput(S.hist[S.hIdx]);
    } else if (e.key === "ArrowDown" && S.hIdx >= 0) {
      e.preventDefault(); S.hIdx--; setInput(S.hIdx >= 0 ? S.hist[S.hIdx] : "");
    }
  });
  $("#send").onclick = () => submit();
  $("#tabs").addEventListener("click", e => { const b = e.target.closest("[data-tab]"); if (b) setTab(b.dataset.tab); });
  $("#cfYes").onclick = () => answerConfirm(true);
  $("#cfNo").onclick = () => answerConfirm(false);
  $("#reload").onclick = () => { S.authError = null; tokenOk() ? refresh() : connected() ? (store.set("authTry", 0), authRedirect(true)) : render(); };
  $("#gear").onclick = openSettings;
  $("#gBtn").onclick = () => authRedirect(false);
  $("#relogin").onclick = () => authRedirect(false);
  $("#kSave").onclick = () => saveKey("#kInput");
  $("#sSave").onclick = () => saveKey("#sKey");
  $("#sConnect").onclick = () => authRedirect(false);
  $("#sDisconnect").onclick = () => { disconnect(); $("#settings").hidden = true; };
  $("#sModels").addEventListener("click", e => {
    const b = e.target.closest("[data-m]");
    if (!b) return;
    S.model = b.dataset.m;
    store.set("model", S.model);
    document.querySelectorAll("#sModels button").forEach(x => x.classList.toggle("on", x === b));
  });
  const install = async () => {
    if (S.installEvt) { S.installEvt.prompt(); await S.installEvt.userChoice.catch(() => {}); S.installEvt = null; render(); $("#sInstall").hidden = true; }
  };
  $("#sInstall").onclick = install;
  $("#installBtn").onclick = install;
  document.querySelectorAll("[data-close]").forEach(b => b.onclick = () => { $("#" + b.dataset.close).hidden = true; S.sheetEv = null; });
  for (const id of ["sheet", "settings"]) $("#" + id).addEventListener("click", e => { if (e.target.id === id) { $("#" + id).hidden = true; S.sheetEv = null; } });
  $("#memo").addEventListener("click", e => { if (e.target.id === "memo") saveMemo(); });   // 바깥을 눌러 닫아도 저장
  $("#mmDone").onclick = saveMemo;
  $("#shDone").onclick = () => S.sheetEv && toggleDone(S.sheetEv.id);
  $("#shDel").onclick = () => deleteFromSheet(null);
  $("#shScope").addEventListener("click", e => { const b = e.target.closest("[data-s]"); if (b) b.dataset.s ? deleteFromSheet(b.dataset.s) : ($("#shScope").hidden = true); });
  $("#list").addEventListener("click", e => {
    if (e.target.id === "tkScope") return authRedirect(false);
    if (e.target.id === "tkClear") return taskAction(null, "clear");
    const tck = e.target.closest("[data-tck]"), tdel = e.target.closest("[data-tdel]"), trow = e.target.closest("[data-tid]");
    const tmemo = e.target.closest("[data-tmemo]");
    if (tmemo) return openMemo(tmemo.dataset.tmemo);
    if (tck) return taskAction(tck.dataset.tck, "check");
    if (tdel) return taskAction(tdel.dataset.tdel, "delete");
    if (trow) return taskAction(trow.dataset.tid, "row");
    const ck = e.target.closest("[data-done]");
    if (ck) { toggleDone(ck.dataset.done); return; }
    const ex = e.target.closest("[data-ex]");
    if (ex) { setInput(ex.dataset.ex); q.focus(); return; }
    const row = e.target.closest("[data-id]");
    if (row) openSheet(row.dataset.id);
  });
  window.addEventListener("beforeinstallprompt", e => { e.preventDefault(); S.installEvt = e; render(); });
  document.addEventListener("visibilitychange", () => {   // 다시 열었을 때: 토큰이 만료됐으면 받아 오고, 오래됐으면 새로고침
    if (document.visibilityState !== "visible" || S.busy) return;
    if (connected() && !tokenOk() && !S.authError) authRedirect(true);
    else if (tokenOk() && Date.now() - S.loadedAt > 60e3) refresh();
  });
}

init();
