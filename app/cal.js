// 날짜·일정 계산 — 위젯의 calcore.py / stores.py(구글 부분)를 그대로 옮긴 것. 화면·네트워크는 app.js.
// 일정 모양: {id, series_id, title, date, time, end_date, end_time, location, note, remind, recurring, done, link}
// date는 'YYYY-MM-DD'(끝 날짜 포함), time은 'HH:MM' 또는 null(종일). 시간대는 이 휴대폰 시간대.

export const WD = ["월", "화", "수", "목", "금", "토", "일"];
const RR_DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"];
export const DONE_KEY = "claudecalDone";
export const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Seoul";

// ---------- 날짜 도우미 ----------
export const pad = n => String(n).padStart(2, "0");
export const isoD = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
export const hm = d => `${pad(d.getHours())}:${pad(d.getMinutes())}`;
export function parseD(s) { const [y, m, d] = s.split("-").map(Number); return new Date(y, m - 1, d); }
export function addDays(s, n) { const d = parseD(s); d.setDate(d.getDate() + n); return isoD(d); }
export const dayDiff = (a, b) => Math.round((parseD(a) - parseD(b)) / 864e5);
export const wday = d => (d.getDay() + 6) % 7;                 // 월=0 … 일=6
export const dlabel = s => { const d = typeof s === "string" ? parseD(s) : s; return `${d.getMonth() + 1}/${d.getDate()}(${WD[wday(d)]})`; };
function at(date, time) { const d = parseD(date); const [h, m] = time.split(":").map(Number); d.setHours(h, m, 0, 0); return d; }

export function validDate(s) {
  const m = typeof s === "string" && s.trim().match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!m) return null;
  const d = new Date(+m[1], +m[2] - 1, +m[3]);
  return d.getMonth() === +m[2] - 1 && d.getDate() === +m[3] ? isoD(d) : null;
}
export function validTime(s) {
  const m = typeof s === "string" && s.trim().match(/^(\d{1,2}):(\d{2})$/);
  if (!m || +m[1] > 23 || +m[2] > 59) return null;
  return `${pad(+m[1])}:${m[2]}`;
}

// ---------- 반복 규칙 ----------
export function normRrule(rule) {
  if (!rule || typeof rule !== "string") return null;
  rule = rule.trim();
  if (/^RRULE:/i.test(rule)) rule = rule.slice(6);
  const parts = {};
  for (const p of rule.split(";")) {
    const i = p.indexOf("=");
    if (i > 0) parts[p.slice(0, i).trim().toUpperCase()] = p.slice(i + 1).trim().toUpperCase();
  }
  if (!["DAILY", "WEEKLY", "MONTHLY", "YEARLY"].includes(parts.FREQ)) return null;
  if (parts.UNTIL) {
    const u = parts.UNTIL, m = u.match(/^(\d{8})(?:T(\d{6})(Z)?)?$/);
    if (!m) delete parts.UNTIL;
    else if (m[3]) {   // UTC 시각 → 이 지역 날짜
      const dt = new Date(Date.UTC(+u.slice(0, 4), +u.slice(4, 6) - 1, +u.slice(6, 8), +u.slice(9, 11), +u.slice(11, 13), +u.slice(13, 15)));
      parts.UNTIL = isoD(dt).replace(/-/g, "");
    } else parts.UNTIL = m[1];
  }
  const freq = parts.FREQ;
  delete parts.FREQ;
  return [`FREQ=${freq}`, ...Object.keys(parts).sort().map(k => `${k}=${parts[k]}`)].join(";");
}

// 구글용: 시간 있는 반복은 UNTIL을 UTC 시각으로 (RFC 5545 규칙)
export function googleRrule(rule, timed) {
  return "RRULE:" + rule.replace(/UNTIL=(\d{8})(?![T\d])/, (_, d) => {
    if (!timed) return `UNTIL=${d}`;
    const end = new Date(+d.slice(0, 4), +d.slice(4, 6) - 1, +d.slice(6, 8), 23, 59, 59);
    return "UNTIL=" + end.toISOString().replace(/\.\d{3}/, "").replace(/[-:]/g, "");
  });
}

const ORD = {1: "첫째", 2: "둘째", 3: "셋째", 4: "넷째", 5: "다섯째", "-1": "마지막"};
export function describeRrule(rule, start = null, short = false) {
  if (!rule) return "";
  const p = Object.fromEntries(rule.split(";").filter(x => x.includes("=")).map(x => [x.slice(0, x.indexOf("=")), x.slice(x.indexOf("=") + 1)]));
  const n = Number(p.INTERVAL || 1) || 1;
  let days = (p.BYDAY || "").split(",").filter(x => RR_DAYS.includes(x)).map(x => WD[RR_DAYS.indexOf(x)]);
  const st = start ? parseD(start) : null;
  let s;
  if (p.FREQ === "DAILY") s = n === 1 ? "매일" : `${n}일마다`;
  else if (p.FREQ === "WEEKLY") {
    if (!days.length && st) days = [WD[wday(st)]];
    s = (n === 1 ? "매주" : n === 2 ? "격주" : `${n}주마다`) + (days.length ? " " + days.join("·") : "");
  } else if (p.FREQ === "MONTHLY") {
    s = n === 1 ? "매달" : `${n}달마다`;
    const m = (p.BYDAY || "").match(/^(-?\d)([A-Z]{2})$/);
    if (p.BYMONTHDAY) s += ` ${p.BYMONTHDAY}일`;
    else if (m && RR_DAYS.includes(m[2])) s += ` ${ORD[m[1]] || m[1]} ${WD[RR_DAYS.indexOf(m[2])]}요일`;
    else if (st) s += ` ${st.getDate()}일`;
  } else if (p.FREQ === "YEARLY") s = "매년" + (st ? ` ${st.getMonth() + 1}/${st.getDate()}` : "");
  else s = "반복";
  if (short) return s;
  if (p.UNTIL) s += ` · ${+p.UNTIL.slice(4, 6)}/${+p.UNTIL.slice(6, 8)}까지`;
  else if (p.COUNT) s += ` · ${p.COUNT}번`;
  return s;
}

// '11/20(금) 15:00 조합론 시험' — 반복이면 '매주 화 15:00 수업'
export function describe(ev) {
  const d = validDate(ev.date);
  let when = d ? dlabel(d) : "";
  if (ev.rrule) when = describeRrule(ev.rrule, d, true);
  return [when, ev.time || "", ev.title || ""].filter(Boolean).join(" ");
}

// ---------- 일정 내용 정리 ----------
const SPEC_KEYS = ["title", "date", "time", "end_date", "end_time", "location", "note", "rrule", "remind"];

// 형식을 맞추고 끝이 시작보다 앞서지 않게. 날짜나 제목이 없으면 null. remind: null=기본, -1=없음, 0=정시, N=N분 전
export function normalize(spec) {
  const d = validDate(spec.date), title = String(spec.title || "").trim();
  if (!d || !title) return null;
  const out = {title: title.slice(0, 200), date: d, time: null, end_date: d, end_time: null,
               location: String(spec.location || "").trim(), note: String(spec.note || "").trim(),
               rrule: normRrule(spec.rrule), remind: null};
  const t = validTime(spec.time), ed = validDate(spec.end_date) || d;
  if (t) {
    const start = at(d, t), et = validTime(spec.end_time);
    let end = et ? at(ed, et) : new Date(start.getTime() + 3600e3);
    if (end <= start && et && ed === d) end.setDate(end.getDate() + 1);   // 23:00~01:00 처럼 밤을 넘기는 일정
    if (end <= start) end = new Date(start.getTime() + 3600e3);
    Object.assign(out, {time: t, end_date: isoD(end), end_time: hm(end)});
  } else out.end_date = ed > d ? ed : d;
  const r = spec.remind === null || spec.remind === undefined || spec.remind === "" ? NaN : Number(spec.remind);
  if (Number.isInteger(r) && r >= -1 && r <= 40320) out.remind = r;
  return out;
}

// Claude의 update 액션(바꿀 칸만 값, 나머지 null)을 기존 일정에 겹친다
export function mergeChanges(old, a) {
  const nw = Object.fromEntries(SPEC_KEYS.map(k => [k, old[k] ?? null]));
  for (const k of ["title", "location", "note"]) if (a[k]) nw[k] = a[k];
  const od = validDate(old.date), ot = validTime(old.time);
  const oed = validDate(old.end_date) || od, oet = validTime(old.end_time);
  const nd = validDate(a.date), ned = validDate(a.end_date), nt = validTime(a.time), net = validTime(a.end_time);
  if (nd) { nw.date = nd; nw.end_date = ned || addDays(nd, dayDiff(oed, od)); }
  else if (ned) nw.end_date = ned;
  if (nt) {
    const dur = ot && oet ? at(oed, oet) - at(od, ot) : 3600e3;
    const start = at(nw.date, nt), end = new Date(start.getTime() + dur);
    Object.assign(nw, {time: nt, end_date: isoD(end), end_time: hm(end)});
  }
  if (net) { nw.end_time = net; if (!nt && !ned) nw.end_date = nw.date; }
  if (a.rrule) nw.rrule = a.rrule;
  if (a.remind !== null && a.remind !== undefined) nw.remind = a.remind;
  return nw;
}

// ---------- Claude에게 보낼 글 ----------
export function brief(near) {
  const list = [], sidMap = {};
  near.forEach((ev, i) => {
    const sid = `e${i}`;
    sidMap[sid] = ev;
    const b = {sid, title: ev.title, date: ev.date, time: ev.time};
    if (ev.end_time) b.end_time = ev.end_time;
    if (ev.location) b.loc = ev.location;
    if (ev.recurring) b.repeat = true;
    if (ev.done) b.done = true;
    list.push(b);
  });
  return {list, sidMap};
}

export function buildUser(text, events, now) {
  const today = isoD(now), monday = addDays(today, -wday(now));
  const weeks = ["이번주", "다음주", "다다음주"].map((name, i) => `${name} ${dlabel(addDays(monday, 7 * i))}~${dlabel(addDays(monday, 7 * i + 6))}`).join(" | ");
  const names = {0: "=오늘", 1: "=내일", 2: "=모레"};
  const days = Array.from({length: 35}, (_, i) => dlabel(addDays(today, i)) + (names[i] || "")).join(" ");
  return `현재: ${today} (${WD[wday(now)]}) ${hm(now)}, 올해는 ${now.getFullYear()}년\n` +
    `주: ${weeks}\n날짜표: ${days}\n` +
    `기존 일정: ${JSON.stringify(events)}\n사용자 입력: ${JSON.stringify(text)}`;
}

export function parseResponse(text) {
  const s = text.indexOf("{"), e = text.lastIndexOf("}");
  if (s === -1 || e <= s) throw new Error("Claude 응답을 해석하지 못했어요: " + text.slice(0, 150));
  const data = JSON.parse(text.slice(s, e + 1));
  data.actions = Array.isArray(data.actions) ? data.actions.filter(a => a && typeof a === "object") : [];
  data.reply = String(data.reply || "");
  return data;
}

// ---------- 구글 이벤트 ↔ 일정 ----------
export function fromGoogle(ev) {
  const s = ev.start || {}, e = ev.end || {};
  const out = {id: ev.id, series_id: ev.recurringEventId || null, title: ev.summary || "(제목 없음)",
               location: ev.location || "", note: ev.description || "", link: ev.htmlLink || "",
               recurring: !!(ev.recurringEventId || ev.recurrence), time: null, end_time: null,
               done: ev.extendedProperties?.private?.[DONE_KEY] === "1"};
  if (s.dateTime) {
    const st = new Date(s.dateTime), en = new Date(e.dateTime);
    Object.assign(out, {date: isoD(st), time: hm(st), end_date: isoD(en), end_time: hm(en)});
  } else {
    const ed = e.date ? addDays(e.date, -1) : s.date;   // 종일 일정의 끝 날짜는 미포함이라 하루 뺀다
    Object.assign(out, {date: s.date, end_date: ed > s.date ? ed : s.date});
  }
  const rem = ev.reminders || {};
  if (rem.useDefault ?? true) out.remind = null;
  else {
    const mins = (rem.overrides || []).filter(o => o.method === "popup").map(o => o.minutes);
    out.remind = mins.length ? Math.min(...mins) : -1;
  }
  return out;
}

export const sortKey = ev => `${ev.date} ${ev.time ? "1" + ev.time : "0"} ${ev.title}`;   // 같은 날은 종일 일정 먼저

// 일정 내용 → 구글 이벤트 body. patch=true면 비운 칸은 null로 보내 지운다
export function toGoogle(spec, patch = false, recurrence = true) {
  const body = {summary: spec.title};
  for (const [k, g] of [["location", "location"], ["note", "description"]]) {
    if (spec[k]) body[g] = spec[k];
    else if (patch) body[g] = null;
  }
  if (spec.time) {
    body.start = {dateTime: `${spec.date}T${spec.time}:00`, timeZone: TZ};
    body.end = {dateTime: `${spec.end_date}T${spec.end_time}:00`, timeZone: TZ};
    if (patch) body.start.date = body.end.date = null;
  } else {
    body.start = {date: spec.date};
    body.end = {date: addDays(spec.end_date, 1)};   // 종일 일정은 끝 날짜 미포함
    if (patch) for (const x of [body.start, body.end]) Object.assign(x, {dateTime: null, timeZone: null});
  }
  const r = spec.remind;
  body.reminders = r === null || r === undefined ? {useDefault: true}
    : {useDefault: false, overrides: r < 0 ? [] : [{method: "popup", minutes: r}]};
  if (recurrence && spec.rrule) body.recurrence = [googleRrule(spec.rrule, !!spec.time)];
  return body;
}

// 되돌리기용: 바꿀 수 있는 칸을 통째로 (patch로 되돌릴 수 있게 빈 칸은 null)
export function snapshot(ev, recurrence = true) {
  const snap = {summary: ev.summary ?? null, location: ev.location ?? null, description: ev.description ?? null,
                reminders: ev.reminders || {useDefault: true}};
  for (const k of ["start", "end"]) {
    const x = ev[k] || {};
    snap[k] = {date: x.date ?? null, dateTime: x.dateTime ?? null, timeZone: x.timeZone ?? null};
  }
  if (recurrence && ev.recurrence) snap.recurrence = ev.recurrence;
  return snap;
}

export function clean(body) {   // insert용: null 칸 빼기
  if (body && typeof body === "object" && !Array.isArray(body))
    return Object.fromEntries(Object.entries(body).filter(([, v]) => v !== null).map(([k, v]) => [k, clean(v)]));
  return body;
}

export const doneBody = done => ({extendedProperties: {private: {[DONE_KEY]: done ? "1" : "0"}}});
export const gDay = x => x.date || isoD(new Date(x.dateTime));   // 구글 start/end 칸 → 이 지역 날짜
