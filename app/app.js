// ClaudeCal 휴대폰판 — 한 줄 적으면 구글 캘린더 일정으로. 서버 없이 휴대폰이 구글·Claude에 직접 연결한다.
//  · 구글: OAuth 암묵적 흐름(리디렉션)으로 1시간짜리 토큰. 만료되면 조용히 다시 받아 온다(prompt=none).
//  · Claude: 사용자가 넣은 본인 API 키로 브라우저에서 바로 호출 (키는 이 휴대폰 localStorage에만).
//  · 안드로이드: 홈 화면에 추가하면 공유 목록에 뜬다 (카톡 메시지 공유 → 바로 등록).
//  · 위쪽 [일정 | 할 일]: '할 일'은 날짜 없는 일 — 구글 할 일(Tasks) 기본 목록. 체크 = 완료, 줄 누르기 = 수정(이름·메모·삭제).
import { SYSTEM_PROMPT, SCHEMA, MODELS } from "./prompt.js";
import * as C from "./cal.js";

const GOOGLE_CLIENT_ID = "560537057198-3jls07opathhum0n6neh21gt171v0dv7.apps.googleusercontent.com";   // 구글 클라우드 '웹 애플리케이션' 클라이언트 ID (공개돼도 되는 값)
const SCOPE_CAL = "https://www.googleapis.com/auth/calendar.events";
const SCOPE_TASKS = "https://www.googleapis.com/auth/tasks";
const SCOPE_CALLIST = "https://www.googleapis.com/auth/calendar.calendarlist";   // 구글 캘린더 기본 알림 바꾸기
const BASES = {cal: "https://www.googleapis.com/calendar/v3/calendars/primary/", tasks: "https://tasks.googleapis.com/tasks/v1/lists/@default/",
               callist: "https://www.googleapis.com/calendar/v3/users/me/calendarList/"};
const DOING = "▶ ";   // 예전 '진행 중' 표시 — 이제 없는 기능이라 읽을 때 떼고, 고칠 때 지운다
const TASKS_SCOPE_MSG = "할 일을 쓰려면 구글 권한을 하나 더 받아야 해요.";
const CALLIST_SCOPE_MSG = "기본 알림을 바꾸려면 구글 권한을 하나 더 받아야 해요. 아래 [구글 다시 연결]을 눌러 주세요.";
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
  gRemind: store.get("gRemind"),   // 구글 캘린더 기본 알림(분, -1=없음) — PC 위젯·구글 캘린더 앱과 같은 값
  events: [], query: null, undo: [], busy: false, pending: null, authError: null, installEvt: null, loadedAt: 0, hist: [], hIdx: -1,
  tab: store.get("tab", "cal"), scopes: store.get("scopes", ""), tasks: [], tasksErr: null, tasksNeedScope: false,
};
class NeedAuth extends Error {}
class NeedTasksScope extends Error {}
class NeedCalListScope extends Error {}
const tokenOk = () => !!DEMO || (S.tok && S.tok.exp > Date.now() + 60e3);
const connected = () => !!DEMO || tokenOk() || !!S.email;   // 한 번이라도 연결했으면 (토큰은 만료됐을 수 있음)
const hasTasksScope = () => !!DEMO || S.scopes.includes("auth/tasks");
const hasCalListScope = () => !!DEMO || S.scopes.includes("calendar.calendarlist");
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
  const scope = silent ? (S.scopes || SCOPE_CAL) : `${SCOPE_CAL} ${SCOPE_TASKS} ${SCOPE_CALLIST}`;
  const p = new URLSearchParams({client_id: GOOGLE_CLIENT_ID, redirect_uri: REDIRECT, response_type: "token", scope,
                                 include_granted_scopes: "true", state, prompt: silent ? "none" : "select_account"});
  if (S.email) p.set("login_hint", S.email);
  S.leaving = true;
  document.body.classList.add("booting");   // 넘어가는 동안 지금 화면이 비치지 않게
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
  if (api === "callist" && !hasCalListScope()) throw new NeedCalListScope(CALLIST_SCOPE_MSG);
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
    if (api === "callist" && r.status === 403 && /insufficient|scope/i.test(msg)) throw new NeedCalListScope(CALLIST_SCOPE_MSG);
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
    if (res.defaultReminders) { S.gRemind = C.popupMin(res.defaultReminders); store.set("gRemind", S.gRemind); }   // 구글 기본 알림
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

async function newSeriesColor() {   // 새 반복 일정 색: 지난 1주~앞으로 8주의 다른 반복 일정과 안 겹치게 (PC와 같은 규칙)
  try {
    const today = C.isoD(new Date()), seen = new Map();
    let pageToken = null;
    do {
      const res = await google("GET", "events", {query: {timeMin: C.parseD(C.addDays(today, -7)).toISOString(), timeMax: C.parseD(C.addDays(today, 56)).toISOString(),
        singleEvents: "true", orderBy: "startTime", maxResults: 250, pageToken, fields: "nextPageToken,items(recurringEventId,colorId,status)"}});
      for (const e of res.items || []) if (e.recurringEventId && e.status !== "cancelled" && !seen.has(e.recurringEventId)) seen.set(e.recurringEventId, e.colorId || null);
      pageToken = res.nextPageToken;
    } while (pageToken);
    return C.pickSeriesColor([...seen.values()].filter(Boolean));
  } catch (e) {
    if (e instanceof NeedAuth) throw e;
    return null;   // 색은 못 정해도 일정은 넣는다
  }
}

// 바꾸는 함수는 되돌리기 기록(list)을 돌려준다 — 위젯의 GoogleStore와 같은 규칙
async function gAdd(spec) {
  const body = C.toGoogle(spec);
  if (spec.rrule) { const color = await newSeriesColor(); if (color) body.colorId = color; }
  const c = await google("POST", "events", {body});
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
  if (spec.rrule && !ev.recurrence && !ev.colorId) { const color = await newSeriesColor(); if (color) body.colorId = color; }   // 단일 → 반복이면 색도
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

async function setDefaultRemind(minutes) {   // 구글 캘린더 기본 알림 바꾸기 (PC 위젯·구글 캘린더 앱도 같이). 메일 알림은 그대로
  const entry = await google("GET", "primary", {api: "callist"});   // 읽기는 'primary'로 되지만 바꾸기는 진짜 id(계정 메일)여야 한다
  const body = {defaultReminders: [...(entry.defaultReminders || []).filter(r => r.method !== "popup"), ...(minutes < 0 ? [] : [{method: "popup", minutes}])]};
  await google("PATCH", encodeURIComponent(entry.id || "primary"), {api: "callist", body});
  S.gRemind = minutes;
  store.set("gRemind", minutes);
}

// ---------------- 기약 없는 할 일 (구글 할 일 기본 목록) — 위젯의 GoogleStore와 같은 규칙 ----------------
const taskPath = id => `tasks/${encodeURIComponent(id)}`;
function taskFromGoogle(t) {
  const title = t.title || "";
  return {id: t.id, title: title.startsWith(DOING) ? title.slice(DOING.length) : title, state: t.status === "completed" ? "done" : "todo",
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
  const active = all.filter(t => t.state !== "done").sort((a, b) => (a.pos < b.pos ? -1 : 1));
  const done = all.filter(t => t.state === "done").sort((a, b) => (a.done_at < b.done_at ? 1 : -1)).slice(0, 50);
  return [...active, ...done];
}
async function tAdd(title, notes = "", done = false) {
  const body = {title};
  if (notes) body.notes = notes;
  if (done) body.status = "completed";
  const t = await google("POST", "tasks", {api: "tasks", body});   // 맨 위에 들어간다
  return [{t: "task_delete", id: t.id}];
}
async function tSet(id, state) {
  const old = await google("GET", taskPath(id), {api: "tasks"});
  const body = {title: taskFromGoogle(old).title, status: state === "done" ? "completed" : "needsAction"};
  if (state !== "done") body.completed = null;
  await google("PATCH", taskPath(id), {api: "tasks", body});
  const back = {title: old.title || "", status: old.status || "needsAction"};
  if (back.status !== "completed") back.completed = null;
  return [{t: "task_patch", id, body: back}];
}
async function tEdit(id, title, notes) {   // 제목·메모 고치기
  const old = await google("GET", taskPath(id), {api: "tasks"});
  await google("PATCH", taskPath(id), {api: "tasks", body: {title, notes: notes || null}});
  return [{t: "task_patch", id, body: {title: old.title || "", notes: old.notes ?? null}}];
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

async function doAll(steps) {   // 차례로 하다가 실패하면 그때까지 한 것을 되돌리고 오류
  const batch = [];
  try {
    for (const step of steps) batch.push(await step());
  } catch (e) {
    try { for (const rec of [...batch].reverse()) await gUndo(rec); } catch { /* 되돌리기도 실패하면 원래 오류를 보여 준다 */ }
    throw e;
  }
  return batch;
}
const executePlan = plan => doAll(plan.map(p => () =>
  p.op === "add" ? gAdd(p.spec) : p.op === "update" ? gUpdate(p.id, p.spec, p.scope) : gDelete(p.id, p.scope)));

// 꾹 눌러 옮기기 — 위젯의 move_event / event_to_task / task_to_event와 같은 규칙
async function gMove(id, shift) {   // shift일만큼 옮긴다. 시간·길이는 그대로, 반복 일정은 이 회차만
  const u = C.fromGoogle(await google("GET", evPath(id)));
  const spec = C.normalize({...u, rrule: null, date: C.addDays(u.date, shift), end_date: C.addDays(u.end_date, shift)});
  return gUpdate(id, spec, "one");
}
function eventToTask(ev) {   // 장소·메모는 할 일 메모로, 완료 체크도 그대로. 일정은 지운다
  const notes = [ev.location && "📍 " + ev.location, ev.note].filter(Boolean).join("\n");
  return doAll([() => tAdd(ev.title, notes, ev.done), () => gDelete(ev.id, "one")]);
}
function taskToEvent(t, spec) {   // 할 일 → 그날 종일 일정 (메모는 일정 메모로). 할 일 목록에서는 빠진다
  let id = null;
  const steps = [async () => { const rec = await gAdd(spec); id = rec[0].id; return rec; }];
  if (t.state === "done") steps.push(() => gSetDone(id, true));
  steps.push(() => tDelete(t.id));
  return doAll(steps);
}

// ---------------- 입력 ----------------
async function submit(text) {
  text = (text ?? $("#q").value).trim();
  if (!text || S.busy) return;
  if (S.tab === "tasks") return addTask(text);   // 할 일은 Claude 없이 그대로 적는다
  const quick = C.quickParse(text);   // ⚡ '내일 3시 치과' 같은 단순한 등록은 Claude 없이 바로 (PC와 같은 규칙)
  if (!quick && !S.key) { setStatus("이건 Claude가 읽어야 해요. 먼저 Claude API 키를 넣어 주세요. (‘내일 3시 치과’ 같은 단순한 등록은 키 없이도 돼요)", "warn"); render(); return; }
  if (!tokenOk()) {   // 토큰이 만료됐으면 글을 맡겨 두고 구글에 다녀온다
    if (!connected()) { setStatus("먼저 구글 캘린더를 연결해 주세요.", "warn"); render(); return; }
    store.set("pending", {text, at: Date.now()});
    S.authError = null;
    store.set("authTry", 0);
    return authRedirect(true);
  }
  S.busy = true;
  S.hist = [text, ...S.hist.filter(x => x !== text)].slice(0, 30);
  S.hIdx = -1;
  if (quick) {
    try {
      const {plan} = await makePlan({actions: [quick], reply: ""}, {});
      setInput("");
      await run(plan, "", text, true);
    } catch (e) {
      handleErr(e, text);
    } finally {
      S.busy = false;
      $("#send").disabled = false;
    }
    return;
  }
  setBusy("Claude가 읽는 중");
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

async function run(plan, reply, text, quick = false) {
  setBusy("구글 캘린더에 넣는 중");
  const batch = await executePlan(plan);
  S.undo.push({desc: text, batch});
  S.undo = S.undo.slice(-30);
  if (!reply)
    reply = plan.map(p => p.desc.slice(p.desc.indexOf(" · ") + 3)).join(" / ") + (plan.every(p => p.op === "add") ? " 등록했어요" : " 처리했어요");
  setStatus((quick ? "⚡ " : "") + reply, "ok", true);
  await refresh();
}

function askConfirm(plan, reply, text) {
  S.pending = {plan, reply, text};
  $("#cfText").textContent = reply || "이렇게 할까요?";
  $("#cfYes").textContent = "그렇게 해 줘";
  $("#cfList").innerHTML = plan.map(p => `<li>${esc(p.desc)}</li>`).join("");
  $("#confirm").hidden = false;
  setStatus("");
}
function askDelete(items) {   // 지우기 전 확인 카드 — 답을 true/false로
  if (S.pending) answerConfirm(false);
  return new Promise(resolve => {
    S.pending = {resolve};
    $("#cfYes").textContent = "삭제";
    $("#cfText").textContent = "삭제할까요?";
    $("#cfList").innerHTML = items.map(x => `<li>${esc(x)}</li>`).join("");
    $("#confirm").hidden = false;
  });
}
async function answerConfirm(yes) {
  const job = S.pending;
  S.pending = null;
  $("#confirm").hidden = true;
  if (!job) return;
  if (job.resolve) return job.resolve(yes);
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
async function taskAction(id, kind) {   // kind: check(완료 켜기/끄기) | delete | clear
  if (S.busy) return;
  const t = S.tasks.find(x => x.id === id);
  let state = null;
  if (kind === "check") state = t.state === "done" ? "todo" : "done";
  if (kind === "delete" && !(await askDelete([t.title]))) return;
  if (kind === "clear") {
    const n = S.tasks.filter(x => x.state === "done").length;
    if (!n || !(await askDelete([`완료한 할 일 ${n}개`]))) return;
  }
  if (state) { t.state = state; render(); }   // 화면은 바로 바꾸고 저장 결과로 다시 그린다
  S.busy = true;
  try {
    let recs, msg;
    if (state) {
      recs = [await tSet(id, state)];
      msg = (state === "done" ? "완료했어요: " : "할 일로 되돌렸어요: ") + t.title;
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
    <div class="tk-b"><div class="tk-t">${esc(t.title)}</div>
      ${first ? `<div class="tk-n">${esc(first)}</div>` : ""}</div>
    <button class="tk-x" data-tdel="${esc(t.id)}" aria-label="지우기">✕</button></div>`;
}
function openMemo(id) {
  const t = S.tasks.find(x => x.id === id);
  if (!t) return;
  S.memoTask = t;
  $("#mmTitle").value = t.title;
  $("#mmText").value = t.notes || "";
  $("#memo").hidden = false;   // 키보드가 바로 올라오지 않게 자동으로 누르지 않는다
}
async function saveMemo() {   // 닫으면 늘 저장 (메모장처럼). 바뀐 게 없거나 제목을 비웠으면 그냥 닫는다
  const t = S.memoTask, title = $("#mmTitle").value.split(/\s+/).join(" ").trim(), notes = $("#mmText").value.trim();
  $("#memo").hidden = true;
  S.memoTask = null;
  if (!t || !title || (title === t.title && notes === (t.notes || "").trim()) || S.busy) return;
  Object.assign(t, {title, notes});
  render();
  S.busy = true;
  try {
    const rec = await tEdit(t.id, title, notes);
    S.undo.push({desc: "할 일 수정: " + title, batch: [rec]});
    setStatus("저장했어요: " + title, "ok");
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
    return `<div class="empty"><p>날짜 없이 언젠가 해야 할 일을 아래 칸에 적어 두세요.</p><p class="hint">☐ 체크 = 완료 · 누르면 수정 (이름·메모·삭제) · 꾹 누르면 날짜에 옮겨 일정으로</p></div>`;
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
  // 다가오는 2주 + 지난 30일 중 아직 완료 안 한 일정 (반복 일정의 지난 회차는 빼고)
  const [f, t] = S.query ? [S.query.from, S.query.to] : [C.addDays(today, -30), C.addDays(today, 14)];
  $("#reload").classList.add("spin");
  try {
    const evs = await listRange(f, t);
    if (seq !== loadSeq) return;
    S.events = S.query ? evs : evs.filter(e => e.end_date >= today || (!e.done && !e.recurring));
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
  const cls = ["ev", ev.time ? "" : "allday", ev.recurring ? "rep" : "", past ? "past" : "", ev.done ? "done" : "", ev.color ? "cc" : ""].join(" ");
  const tm = ev.time ? `${ev.time}${ev.end_time && ev.end_date === ev.date && !C.isPoint(ev) ? `<small>~${ev.end_time}</small>` : ""}` : "📌";
  const meta = [ev.location && "📍 " + ev.location, ev.end_date !== ev.date && `~${C.dlabel(ev.end_date)}`, ev.recurring && "🔁 반복", remindText(ev.remind)]
    .filter(Boolean).join(" · ");
  return `<div class="${cls}" data-id="${esc(ev.id)}"${ev.color ? ` style="--c:${ev.color}"` : ""}>
    <button class="ck" data-done="${esc(ev.id)}" role="checkbox" aria-checked="${!!ev.done}" aria-label="완료"></button>
    <div class="bar"></div><div class="tm">${tm}</div>
    <div class="bd"><div class="tt">${esc(ev.title)}</div>${meta ? `<div class="mt">${esc(meta)}</div>` : ""}</div>
    <button class="tk-x" data-evdel="${esc(ev.id)}" aria-label="삭제">✕</button></div>`;
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
      let d = ev.date;   // 범위 전에 시작해 이어지는 일정은 첫날(다가오는 일정이면 오늘)에, 이미 끝난 지난 일정은 제날짜에
      if (S.query) { if (d < from) d = from; }
      else if (ev.end_date >= td && d < td) d = td;
      if (!byDay.has(d)) byDay.set(d, []);
      byDay.get(d).push(ev);
    }
    for (const d of [...byDay.keys()].sort()) {
      const diff = C.dayDiff(d, td), rel = {"-1": "어제 · 안 끝냄", 0: "오늘", 1: "내일", 2: "모레"}[diff] || (diff < 0 ? "지남 · 안 끝냄" : "");
      const evs = byDay.get(d), dd = C.parseD(d);
      h += `<div class="lday" data-dday="${d}">` +   // 하루 묶음 = 꾹 눌러 끈 일정을 놓는 자리
        `<div class="dayh ${diff === 0 ? "today" : ""} ${diff < 0 ? "past" : ""} ${C.wday(dd) === 6 ? "sun" : C.wday(dd) === 5 ? "sat" : ""}">${C.dlabel(d)}` +
        `${rel ? ` <span class="rel">${rel}</span>` : ""}</div>` +
        (evs.length ? evs.map(evRow).join("") : `<div class="noev">일정 없음</div>`) + `</div>`;
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
  const when = ev.time ? `${C.dlabel(ev.date)} ${ev.time}` + (C.isPoint(ev) ? "" : `~${ev.end_date !== ev.date ? C.dlabel(ev.end_date) + " " : ""}${ev.end_time || ""}`)
    : C.dlabel(ev.date) + (ev.end_date !== ev.date ? ` ~ ${C.dlabel(ev.end_date)}` : "") + " 📌";
  $("#shWhen").textContent = when;
  $("#shRule").textContent = ev.recurring ? "🔁 반복 일정" : "";
  $("#shLoc").textContent = ev.location ? "📍 " + ev.location : "";
  $("#shNote").textContent = ev.note || "";
  $("#shDone").textContent = ev.done ? "완료 취소" : "완료";
  $("#shScope").hidden = true;
  $("#shOpen").hidden = !ev.link;
  $("#shOpen").href = ev.link || "#";
  $("#sheet").hidden = false;
  if (ev.recurring && !DEMO) {
    try { const full = await getEvent(id); if (S.sheetEv === ev) $("#shRule").textContent = "🔁 " + (full.rrule_text || "반복 일정"); } catch { /* 반복 설명은 없어도 된다 */ }
  }
}
function closeSheet() { $("#sheet").hidden = true; S.sheetEv = null; }
async function deleteEvent(id) {   // 일정 줄의 ✕: 묻고 지운다 (반복이면 상세 창에서 '어디까지' 묻기)
  const ev = S.events.find(e => e.id === id);
  if (!ev || S.busy) return;
  if (ev.recurring) { await openSheet(id); return deleteFromSheet(null); }
  if (!(await askDelete([`${C.dlabel(ev.date)} ${ev.time || "📌"} ${ev.title}`]))) return;
  S.busy = true;
  setBusy("지우는 중");
  try {
    const rec = await gDelete(ev.id, "one");
    S.undo.push({desc: "삭제: " + ev.title, batch: [rec]});
    setStatus("삭제했어요: " + ev.title, "ok");
    await refresh();
  } catch (e) { handleErr(e); } finally { S.busy = false; }
}
async function deleteFromSheet(scope) {
  const ev = S.sheetEv;
  if (!ev || S.busy) return;
  if (!scope) {   // 먼저 묻는다: 반복이면 어디까지, 아니면 삭제할까요?
    $("#shScope").innerHTML = ev.recurring
      ? `<p>반복 일정이에요. 어디까지 삭제할까요?</p><div class="row"><button class="btn" data-s="one">이 일정만</button>` +
        `<button class="btn danger" data-s="all">반복 전체</button><button class="btn" data-s="">취소</button></div>`
      : `<p>삭제할까요?</p><div class="row"><button class="btn" data-s="">취소</button><button class="btn danger armed" data-s="one">삭제</button></div>`;
    $("#shScope").hidden = false;
    return;
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

// ---------------- 꾹 눌러 옮기기 ----------------
// 일정·할 일을 꾹 누르면 잡히고 아래에 날짜 띠가 뜬다. 끌어서 놓거나, 손을 떼고 날짜를 눌러도 된다. 다른 곳 = 취소.
// 일정 → 목록의 다른 날 / 띠의 날짜 (시간·길이 그대로, 반복이면 이 회차만) · [할 일로] (날짜 없는 할 일)
// 할 일 → 띠의 날짜 (그날 종일 일정) · ◀ ▶ 위에 머무르면(또는 누르면) 한 주씩 넘긴다
const PRESS_MS = 380;
const DRAG = {press: null, mouse: null, on: false, pick: false, moved: false, src: null, tgt: null, x: 0, y: 0, sx: 0, sy: 0,
              week: null, flip: null, scroller: null, eatUntil: 0, touchAt: 0};
function dragSource(target) {
  if (S.busy || !target?.closest || target.closest("button, a, input, textarea")) return null;
  const evEl = target.closest("#list [data-id]");
  if (evEl) {
    const ev = S.events.find(x => x.id === evEl.dataset.id);
    return ev ? {kind: "ev", ev, title: ev.title, from: evEl.closest("[data-dday]")?.dataset.dday || ev.date} : null;
  }
  const tkEl = target.closest("#list [data-tid]");
  if (tkEl) {
    const t = S.tasks.find(x => x.id === tkEl.dataset.tid);
    return t ? {kind: "task", task: t, title: t.title} : null;
  }
  return null;
}
function dropTarget(x, y) {
  const el = document.elementFromPoint(x, y), s = DRAG.src;
  if (!el || !s) return null;
  const nav = el.closest("[data-dnav]");
  if (nav) { const dir = +nav.dataset.dnav; return {el: nav, kind: "nav", dir, label: dir < 0 ? "◀ 앞 주로 넘기는 중" : "다음 주로 넘기는 중 ▶"}; }
  const tk = el.closest("[data-dtask]");
  if (tk) {
    if (s.kind !== "ev") return null;
    return s.ev.recurring ? {el: tk, kind: "none", label: "반복 일정은 할 일로 못 옮겨요"} : {el: tk, kind: "tasks", label: "→ 날짜 없는 할 일로"};
  }
  const d = el.closest("#dropbar [data-dday], #list [data-dday]");
  if (!d) return null;
  const date = d.dataset.dday;
  if (s.kind === "ev") {   // 띠의 날짜 = 그날 시작, 목록의 하루 묶음 = 잡은 날에서 놓은 날만큼 (여러 날 일정은 통째로)
    const shift = C.dayDiff(date, d.closest("#dropbar") ? s.ev.date : s.from);
    return {el: d, kind: "move", shift, label: shift ? `→ ${C.dlabel(C.addDays(s.ev.date, shift))} ${s.ev.time || "📌"}` : "제자리 (안 옮겨요)"};
  }
  return {el: d, kind: "task", date, label: `→ ${C.dlabel(date)} 📌 일정으로`};
}
function markSrc(on) {
  const s = DRAG.src;
  if (!s) return;
  const sel = s.kind === "ev" ? `#list [data-id="${CSS.escape(s.ev.id)}"]` : `#list [data-tid="${CSS.escape(s.task.id)}"]`;
  document.querySelectorAll(sel).forEach(el => el.classList.toggle("drag-src", on));
}
function paintBar() {   // 날짜 띠: DRAG.week부터 7일
  const s = DRAG.src, td = C.isoD(new Date()), cur = s.kind === "ev" ? s.ev.date : null;
  $("#dbTask").hidden = s.kind !== "ev";
  $("#dbDays").innerHTML = Array.from({length: 7}, (_, i) => {
    const d = C.addDays(DRAG.week, i), dd = C.parseD(d), wd = C.wday(dd), md = i === 0 || dd.getDate() === 1;
    return `<div class="db-d ${d === td ? "today" : ""} ${d === cur ? "src" : ""} ${wd === 6 ? "sun" : wd === 5 ? "sat" : ""}" data-dday="${d}">` +
      `<small>${d === td ? "오늘" : C.WD[wd]}</small><b class="${md ? "m" : ""}">${md ? `${dd.getMonth() + 1}/${dd.getDate()}` : dd.getDate()}</b></div>`;
  }).join("");
  $("#dbRange").textContent = `${C.dlabel(DRAG.week)} ~ ${C.dlabel(C.addDays(DRAG.week, 6))}`;
}
function paintDrag() {
  const s = DRAG.src, t = DRAG.tgt, g = $("#ghost");
  $("#dbLabel").textContent = t ? t.label : DRAG.pick ? "옮길 날짜를 누르세요 · 다른 곳을 누르면 취소"
    : s.kind === "ev" ? "날짜에 놓으면 옮겨요 · 딴 데 놓으면 취소" : "날짜에 놓으면 그날 일정으로 · 딴 데 놓으면 취소";
  $("#dbLabel").classList.toggle("no", t?.kind === "none");
  g.hidden = DRAG.pick;
  if (DRAG.pick) return;
  g.innerHTML = `<div class="g-t">${esc(s.title)}</div>${t ? `<div class="g-w">${esc(t.label)}</div>` : ""}`;
  g.classList.toggle("no", t?.kind === "none");
  const w = g.offsetWidth, h = g.offsetHeight;   // 손가락에 가리지 않게 위쪽에
  g.style.left = Math.max(6, Math.min(DRAG.x - w / 2, innerWidth - w - 6)) + "px";
  g.style.top = (DRAG.y - h - 34 > 6 ? DRAG.y - h - 34 : DRAG.y + 34) + "px";
}
function setTarget(t) {
  const old = DRAG.tgt;
  if (old?.el !== t?.el) {
    old?.el.classList.remove("drop-on", "drop-no");
    t?.el.classList.add(t.kind === "none" ? "drop-no" : "drop-on");
    if (t?.kind === "nav" && !DRAG.pick) startFlip(t.dir); else stopFlip();
  }
  DRAG.tgt = t;
  paintDrag();
}
const updateDrag = () => setTarget(dropTarget(DRAG.x, DRAG.y));
function flipWeek(dir) { DRAG.week = C.addDays(DRAG.week, 7 * dir); paintBar(); }
function stopFlip() { clearTimeout(DRAG.flip); DRAG.flip = null; }
function startFlip(dir) {   // ◀ ▶ 위에 머무르면 0.45초 뒤부터 0.7초마다 한 주씩
  stopFlip();
  const step = wait => DRAG.flip = setTimeout(() => {
    if (!DRAG.on || DRAG.pick) return;
    flipWeek(dir);
    step(700);
  }, wait);
  step(450);
}
function dragScroll() {   // 목록 위·아래 끝으로 가져가면 저절로 굴린다 (잡은 동안 16ms마다)
  if (!DRAG.on || DRAG.pick) return;
  const m = $("main"), r = m.getBoundingClientRect(), bottom = Math.min(r.bottom, $("#dropbar").getBoundingClientRect().top);
  const dy = DRAG.y < r.top + 48 ? -9 : DRAG.y > bottom - 48 && DRAG.y < bottom ? 9 : 0;
  if (!dy) return;
  const before = m.scrollTop;
  m.scrollTop += dy;
  if (m.scrollTop !== before) updateDrag();
}
function clearPress() { if (DRAG.press) { clearTimeout(DRAG.press.timer); DRAG.press = null; } }
function beginDrag(src, x, y) {
  Object.assign(DRAG, {src, on: true, pick: false, moved: false, tgt: null, x, y, sx: x, sy: y, week: C.isoD(new Date())});
  document.activeElement?.blur?.();   // 키보드가 떠 있으면 내린다
  try { if (navigator.userActivation?.hasBeenActive ?? true) navigator.vibrate?.(12); } catch { /* 진동 없는 기기 */ }   // 잡혔다는 짧은 진동
  document.body.classList.add("dragging");
  markSrc(true);
  paintBar();
  $("#dropbar").hidden = false;
  updateDrag();
  clearInterval(DRAG.scroller);
  DRAG.scroller = setInterval(dragScroll, 16);
}
function enterPick() {   // 꾹 누르고 그냥 뗐으면: 띠를 띄워 둔 채 날짜를 누르게
  DRAG.pick = true;
  setTarget(null);
}
function endDrag(drop) {
  const s = DRAG.src, t = DRAG.tgt;
  stopFlip();
  clearPress();
  clearInterval(DRAG.scroller);
  markSrc(false);
  t?.el.classList.remove("drop-on", "drop-no");
  Object.assign(DRAG, {on: false, pick: false, src: null, tgt: null, mouse: null});
  document.body.classList.remove("dragging");
  $("#dropbar").hidden = $("#ghost").hidden = true;
  if (drop && s && t) dropIt(s, t);
}

async function dropIt(s, t) {   // 화면은 바로 바꾸고, 저장한 뒤 다시 불러온다 (실패하면 오류를 보이고 원래대로)
  const move = s.kind === "ev" && t.kind === "move" && t.shift, toTask = s.kind === "ev" && t.kind === "tasks",
        toEvent = s.kind === "task" && t.kind === "task";
  if (!(move || toTask || toEvent)) return;
  if (S.busy) return setStatus("하던 걸 끝내는 중이에요. 잠깐 뒤에 다시 해 주세요.", "warn");
  S.busy = true;
  try {
    if (move) {
      const ev = s.ev;
      Object.assign(ev, {date: C.addDays(ev.date, t.shift), end_date: C.addDays(ev.end_date, t.shift)});
      S.events.sort((a, b) => C.sortKey(a) < C.sortKey(b) ? -1 : 1);
      render();
      setBusy("옮기는 중");
      const rec = await gMove(ev.id, t.shift);
      S.undo.push({desc: "옮기기: " + ev.title, batch: [rec]});
      setStatus(`옮겼어요: ${C.dlabel(ev.date)} ${ev.time || "📌"} ${ev.title}` + (ev.recurring ? " (반복 중 이 회차만)" : ""), "ok");
    } else if (toTask) {
      const ev = s.ev;
      S.events = S.events.filter(x => x.id !== ev.id);
      render();
      setBusy("할 일로 옮기는 중");
      S.undo.push({desc: "할 일로: " + ev.title, batch: await eventToTask(ev)});
      S.tasksAt = 0;   // 할 일 탭을 열면 새로 불러오게
      setStatus("할 일로 옮겼어요: " + ev.title, "ok");
    } else {
      const tk = s.task, spec = C.normalize({title: tk.title, date: t.date, note: tk.notes || ""});
      S.tasks = S.tasks.filter(x => x.id !== tk.id);
      render();
      setBusy("일정으로 옮기는 중");
      S.undo.push({desc: "일정으로: " + tk.title, batch: await taskToEvent(tk, spec)});
      S.loadedAt = 0;   // 일정 탭을 열면 새로 불러오게
      setStatus("일정으로 옮겼어요: " + C.describe(spec), "ok");
    }
  } catch (e) { handleErr(e); } finally { S.busy = false; }
  await refresh();
}

function bindDrag() {
  const list = $("#list");
  // 손가락: 꾹(0.38초) 누르면 잡힌다. 그 전에 움직이면 그냥 굴리기
  document.addEventListener("touchstart", e => {
    DRAG.touchAt = Date.now();
    if (e.touches.length > 1) { clearPress(); if (DRAG.on && !DRAG.pick) endDrag(false); return; }
    if (DRAG.on) return;   // 날짜를 고르는 중엔 보통처럼 누르고 굴린다
    const src = list.contains(e.target) && dragSource(e.target);
    if (!src) return;
    const p = e.touches[0], x = p.clientX, y = p.clientY;
    clearPress();
    DRAG.press = {x, y, timer: setTimeout(() => { DRAG.press = null; beginDrag(src, x, y); }, PRESS_MS)};
  }, {passive: true});
  document.addEventListener("touchmove", e => {
    const p = e.touches[0];
    if (!p) return;
    if (DRAG.press) { if (Math.hypot(p.clientX - DRAG.press.x, p.clientY - DRAG.press.y) > 10) clearPress(); return; }
    if (!DRAG.on || DRAG.pick) return;
    e.preventDefault();   // 잡은 동안은 화면이 굴러가지 않게
    DRAG.x = p.clientX; DRAG.y = p.clientY;
    if (Math.hypot(DRAG.x - DRAG.sx, DRAG.y - DRAG.sy) > 10) DRAG.moved = true;
    updateDrag();
  }, {passive: false});
  document.addEventListener("touchend", e => {
    clearPress();
    if (!DRAG.on || DRAG.pick) return;
    e.preventDefault();   // 뗀 자리가 눌리지 않게
    DRAG.eatUntil = Date.now() + 400;
    if (DRAG.moved) endDrag(true); else enterPick();
  }, {passive: false});
  document.addEventListener("touchcancel", () => { clearPress(); if (DRAG.on && !DRAG.pick) endDrag(false); });
  $("main").addEventListener("scroll", () => { if (DRAG.press) clearPress(); }, {passive: true});
  // 마우스(컴퓨터 브라우저): 누른 채 살짝 끌면 바로 잡힌다
  document.addEventListener("mousedown", e => {
    if (Date.now() - DRAG.touchAt < 1500 || e.button !== 0 || DRAG.on) return;
    const src = list.contains(e.target) && dragSource(e.target);
    DRAG.mouse = src ? {x: e.clientX, y: e.clientY, src} : null;
  });
  document.addEventListener("mousemove", e => {
    const m = DRAG.mouse;
    if (!m) return;
    if (!(e.buttons & 1)) { DRAG.mouse = null; if (DRAG.on && !DRAG.pick) endDrag(false); return; }
    if (DRAG.on) { DRAG.x = e.clientX; DRAG.y = e.clientY; DRAG.moved = true; return updateDrag(); }
    if (Math.hypot(e.clientX - m.x, e.clientY - m.y) >= 6) { beginDrag(m.src, m.x, m.y); DRAG.mouse = m; DRAG.x = e.clientX; DRAG.y = e.clientY; DRAG.moved = true; updateDrag(); }
  });
  document.addEventListener("mouseup", () => {
    if (!DRAG.mouse) return;
    DRAG.mouse = null;
    if (DRAG.on && !DRAG.pick) { DRAG.eatUntil = Date.now() + 400; endDrag(true); }
  });
  // 놓은 뒤 따라오는 클릭은 먹고, 날짜를 고르는 중이면 누른 곳으로 옮긴다
  document.addEventListener("click", e => {
    if (Date.now() < DRAG.eatUntil) { e.preventDefault(); e.stopPropagation(); return; }
    if (!DRAG.pick) return;
    e.preventDefault(); e.stopPropagation();
    const t = dropTarget(e.clientX, e.clientY);
    if (t?.kind === "nav") return flipWeek(t.dir);
    if (t?.kind === "none") return setTarget(t);   // '반복 일정은 못 옮겨요'를 보여 주고 계속 고르게
    if (!t && e.target.closest?.("#dropbar")) return;   // 띠의 빈 곳은 무시
    DRAG.tgt = t;
    endDrag(true);
  }, true);
  document.addEventListener("contextmenu", e => { if (DRAG.on || DRAG.press || e.target.closest?.("#list [data-id], #list [data-tid]")) e.preventDefault(); });
  document.addEventListener("keydown", e => { if (DRAG.on && e.key === "Escape") { e.preventDefault(); endDrag(false); } }, true);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState !== "visible" && DRAG.on) endDrag(false); });
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
  paintRemind();
  $("#settings").hidden = false;
}
function paintRemind(msg) {   // 설정 창 '알림' 칸
  const v = S.gRemind ?? 30, sel = $("#sRemind");
  sel.value = String(v);
  if (sel.value !== String(v)) { sel.add(new Option(`${v}분 전`, String(v))); sel.value = String(v); }   // 목록에 없는 값 (구글에서 직접 고른 값)
  sel.disabled = !connected();
  $("#sRemindMsg").textContent = msg || (!connected() ? "구글을 연결하면 보여요." : !hasCalListScope()
    ? "바꾸려면 구글 권한을 하나 더 받아야 해요." : "");
  $("#sRemindFix").hidden = !connected() || hasCalListScope();
}
async function changeRemind(v) {
  const before = S.gRemind;
  paintRemind("바꾸는 중…");
  try {
    await setDefaultRemind(v);
    paintRemind("바꿨어요. PC 위젯·구글 캘린더 앱도 같이 바뀌어요.");
  } catch (e) {
    S.gRemind = before;
    if (e instanceof NeedAuth) { store.set("authTry", 0); return authRedirect(true); }
    paintRemind(e instanceof NeedCalListScope ? CALLIST_SCOPE_MSG : e.message);
    if (e instanceof NeedCalListScope) $("#sRemindFix").hidden = false;
  }
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
  if ((!S.key && !C.quickParse(p.text)) || !connected()) { setStatus("공유받은 글을 넣어 뒀어요. 위 설정을 마치면 등록할게요.", "warn"); return; }
  store.set("pending", null);
  submit(p.text);
}

// ---------------- 시작 ----------------
const unboot = () => { if (!S.leaving) document.body.classList.remove("booting"); };
function fitViewport() {   // 키보드가 화면을 줄이지 않는 브라우저에서도 입력칸이 키보드 위에 오게
  const vv = window.visualViewport;
  if (!vv) return;
  const covered = Math.max(0, Math.round(innerHeight - vv.height - vv.offsetTop));
  document.body.style.bottom = covered ? covered + "px" : "";
}

async function init() {
  setTimeout(unboot, 4000);   // 무슨 일이 있어도 4초 뒤엔 화면을 보여 준다
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
  await Promise.race([refresh(), new Promise(r => setTimeout(r, 2500))]);   // 목록을 그린 뒤 (느리면 2.5초까지만) 보여 준다
  unboot();
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
  $("#sRemind").onchange = e => changeRemind(Number(e.target.value));
  $("#sRemindFix").onclick = () => authRedirect(false);
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
  $("#mmDel").onclick = () => {   // 수정 화면의 [삭제] — 창을 닫고 '삭제할까요?'를 묻는다
    const t = S.memoTask;
    $("#memo").hidden = true;
    S.memoTask = null;
    if (t) taskAction(t.id, "delete");
  };
  $("#shDone").onclick = () => S.sheetEv && toggleDone(S.sheetEv.id);
  $("#shDel").onclick = () => deleteFromSheet(null);
  $("#shScope").addEventListener("click", e => { const b = e.target.closest("[data-s]"); if (b) b.dataset.s ? deleteFromSheet(b.dataset.s) : ($("#shScope").hidden = true); });
  $("#list").addEventListener("click", e => {
    if (e.target.id === "tkScope") return authRedirect(false);
    if (e.target.id === "tkClear") return taskAction(null, "clear");
    const tck = e.target.closest("[data-tck]"), tdel = e.target.closest("[data-tdel]"), trow = e.target.closest("[data-tid]");
    if (tck) return taskAction(tck.dataset.tck, "check");
    if (tdel) return taskAction(tdel.dataset.tdel, "delete");
    if (trow) return openMemo(trow.dataset.tid);   // 줄을 누르면 수정 화면
    const evDel = e.target.closest("[data-evdel]");
    if (evDel) return deleteEvent(evDel.dataset.evdel);
    const ck = e.target.closest("[data-done]");
    if (ck) { toggleDone(ck.dataset.done); return; }
    const ex = e.target.closest("[data-ex]");
    if (ex) { setInput(ex.dataset.ex); q.focus(); return; }
    const row = e.target.closest("[data-id]");
    if (row) openSheet(row.dataset.id);
  });
  bindDrag();
  window.visualViewport?.addEventListener("resize", fitViewport);
  window.visualViewport?.addEventListener("scroll", fitViewport);
  window.addEventListener("pageshow", fitViewport);
  window.addEventListener("beforeinstallprompt", e => { e.preventDefault(); S.installEvt = e; render(); });
  document.addEventListener("visibilitychange", () => {   // 다시 열었을 때: 토큰이 만료됐으면 받아 오고, 오래됐으면 새로고침
    if (document.visibilityState !== "visible" || S.busy) return;
    if (connected() && !tokenOk() && !S.authError) authRedirect(true);
    else if (tokenOk() && Date.now() - S.loadedAt > 60e3) refresh();
  });
}

init();
