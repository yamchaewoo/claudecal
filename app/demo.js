// 시험 모드(?demo=1 또는 ?demo=g)용 가짜 구글 캘린더와 가짜 Claude — 진짜 계정은 건드리지 않는다.
import * as C from "./cal.js";

const wait = ms => new Promise(r => setTimeout(r, ms));
const today = C.isoD(new Date());
const D = n => C.addDays(today, n);
const timed = (id, title, day, s, e, extra = {}) => ({id, summary: title, status: "confirmed", htmlLink: "https://calendar.google.com",
  start: {dateTime: `${day}T${s}:00`}, end: {dateTime: `${day}T${e}:00`}, reminders: {useDefault: true}, ...extra});
const allday = (id, title, day, extra = {}) => ({id, summary: title, status: "confirmed", htmlLink: "https://calendar.google.com",
  start: {date: day}, end: {date: C.addDays(day, 1)}, reminders: {useDefault: true}, ...extra});

const events = new Map();
let seq = 1;
for (const ev of [
  timed("d1", "스터디", D(0), "10:00", "12:00"),
  timed("d2", "조합론 시험", D(0), "15:00", "17:00", {location: "공학관 302호"}),
  allday("d3", "PAE 에세이 마감", D(1)),
  timed("d4", "동기 모임", D(2), "19:00", "22:00", {location: "강남역", description: "회비 2만 원"}),
]) events.set(ev.id, ev);
events.set("rm", {id: "rm", summary: "수업", status: "confirmed", recurrence: ["RRULE:FREQ=WEEKLY;BYDAY=TU,TH"],
                  start: {dateTime: `${D(-14)}T13:00:00`}, end: {dateTime: `${D(-14)}T14:30:00`}, reminders: {useDefault: true}});
for (let i = -14; i < 30; i++) {
  const d = C.parseD(D(i));
  if ([1, 3].includes(C.wday(d))) events.set(`rm_${D(i)}`, timed(`rm_${D(i)}`, "수업", D(i), "13:00", "14:30", {recurringEventId: "rm"}));
}

const merge = (a, b) => {
  for (const [k, v] of Object.entries(b)) {
    if (v && typeof v === "object" && !Array.isArray(v) && a[k] && typeof a[k] === "object") merge(a[k], v);
    else a[k] = v;
  }
  return a;
};
const startOf = ev => new Date(ev.start.dateTime || `${ev.start.date}T00:00:00`);
const endOf = ev => new Date(ev.end.dateTime || `${ev.end.date}T00:00:00`);

export async function fakeGoogle(method, path, {query, body} = {}) {
  await wait(250);
  const id = decodeURIComponent(path.split("/")[1] || "");
  if (method === "GET" && path === "events") {
    const f = new Date(query.timeMin), t = new Date(query.timeMax);
    const items = [...events.values()].filter(e => !e.recurrence && e.status !== "cancelled" && startOf(e) < t && endOf(e) > f)
      .sort((a, b) => startOf(a) - startOf(b));
    return {summary: "demo@example.com", items: JSON.parse(JSON.stringify(items))};
  }
  if (method === "POST") {
    const ev = {id: `n${seq++}`, status: "confirmed", htmlLink: "https://calendar.google.com", ...JSON.parse(JSON.stringify(body))};
    events.set(ev.id, ev);
    return ev;
  }
  const ev = events.get(id);
  if (!ev) throw Object.assign(new Error("일정을 찾지 못했어요 (이미 지워졌을 수 있어요)."), {status: 404});
  if (method === "GET") return JSON.parse(JSON.stringify(ev));
  if (method === "PATCH") { merge(ev, JSON.parse(JSON.stringify(body))); return ev; }
  if (method === "DELETE") {
    ev.status = "cancelled";
    if (ev.recurrence) for (const x of events.values()) if (x.recurringEventId === id) x.status = "cancelled";
    return null;
  }
  throw new Error("가짜 구글: 모르는 요청 " + method + " " + path);
}

// 아주 단순한 가짜 해석: "지워/삭제" → 삭제, "일정 보여" → 이번주 조회, 나머지 → 등록
export async function fakeClaude(text, list) {
  await wait(500);
  const blank = {sid: null, scope: null, title: null, date: null, time: null, end_date: null, end_time: null,
                 location: null, note: null, rrule: null, remind: null, from: null, to: null};
  if (/지워|삭제|취소/.test(text)) {
    const hit = list.find(e => text.includes(e.title));
    return hit ? {actions: [{...blank, op: "delete", sid: hit.sid, scope: hit.repeat ? "one" : null}], reply: `${hit.title} 지울게요`}
      : {actions: [], reply: "어떤 일정인지 모르겠어요."};
  }
  if (/보여|뭐 있어/.test(text)) {
    const mon = D(-C.wday(new Date()));
    return {actions: [{...blank, op: "query", from: mon, to: C.addDays(mon, 6)}], reply: "이번주 일정이에요."};
  }
  const day = /모레/.test(text) ? D(2) : /내일/.test(text) ? D(1) : D(0);
  const m = text.match(/(\d{1,2})시(?:\s*(\d{1,2})분|\s*반)?/);
  let time = null;
  if (m) {
    let h = +m[1];
    if (h < 8 || /오후|저녁|밤/.test(text)) h = h % 12 + 12;
    time = `${String(h).padStart(2, "0")}:${/반/.test(m[0]) ? "30" : String(m[2] || 0).padStart(2, "0")}`;
  }
  const title = text.replace(/오늘|내일|모레|오전|오후|저녁|밤|\d{1,2}시(\s*\d{1,2}분|\s*반)?/g, "").replace(/\s+/g, " ").trim() || "새 일정";
  return {actions: [{...blank, op: "add", title, date: day, time}], reply: ""};
}
