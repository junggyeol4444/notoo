// AI Novel Factory 관리자 화면 (기획안 48~50번).
// 빌드 도구 없이 도는 단일 파일이다. 데이터는 전부 textContent로 넣는다
// (원고에 < 같은 글자가 있어도 HTML로 해석되지 않게).
"use strict";

const ASPECT_LABELS = {
  pacing: "전개 속도",
  episode_shape: "회차 구조",
  cliffhanger: "클리프행어",
  foreshadowing: "복선 방식",
  relationship: "캐릭터 관계",
  character_structure: "캐릭터 구조",
  event_interval: "사건 주기",
  emotion: "감정곡선",
  dialogue: "대사 비율",
  style: "문체",
};

const NOVEL_TABS = [
  ["overview", "작품"],
  ["bible", "Novel Bible"],
  ["characters", "Characters"],
  ["world", "World"],
  ["timeline", "Timeline"],
  ["relationships", "Relationships"],
  ["foreshadowing", "Foreshadowing"],
  ["references", "Reference Novels"],
  ["patterns", "Reference Patterns"],
  ["episodes", "Episodes"],
  ["similarity", "Similarity Reports"],
  ["publication", "Publication"],
];

// ---------------------------------------------------------------------------
// 도우미
// ---------------------------------------------------------------------------
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else el.setAttribute(k, v === true ? "" : String(v));
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

// DOM의 append는 null을 "null" 글자로 넣는다. 조건부로 빠지는 부분이 많아서 걸러 넣는다.
function put(root, ...children) {
  root.append(...children.flat().filter((c) => c !== null && c !== undefined && c !== false));
}

function badge(value) {
  return h("span", { class: `badge v-${value}` }, value ?? "-");
}

function pct(x, digits = 0) {
  return x === null || x === undefined ? "-" : `${(Number(x) * 100).toFixed(digits)}%`;
}

function num(x, digits = 1) {
  return x === null || x === undefined ? "-" : Number(x).toFixed(digits);
}

let toastTimer = null;
function toast(message, isError = false) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.className = `show${isError ? " err" : ""}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.className = ""), isError ? 8000 : 3000);
}

async function api(method, path, body, { form = false } = {}) {
  const opts = { method, headers: {} };
  if (form) opts.body = body;
  else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    const detail = data && data.detail ? data.detail : text;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

async function act(button, fn, okMessage) {
  button.disabled = true;
  try {
    const r = await fn();
    if (okMessage) toast(okMessage);
    return r;
  } catch (e) {
    toast(e.message, true);
    return undefined;
  } finally {
    button.disabled = false;
  }
}

function table(columns, rows, empty = "없음") {
  if (!rows || rows.length === 0) return h("p", { class: "muted" }, empty);
  return h("div", { class: "table-wrap" },
    h("table", {},
      h("thead", {}, h("tr", {}, columns.map(([label]) => h("th", {}, label)))),
      h("tbody", {}, rows.map((r) => h("tr", {}, columns.map(([, get]) => {
        const v = get(r);
        return h("td", {}, v instanceof Node ? v : (v ?? "-"));
      }))))));
}

function stats(items) {
  return h("div", { class: "cards" }, items.map(([k, v]) =>
    h("div", { class: "stat" }, h("div", { class: "k" }, k), h("div", { class: "v" }, v ?? "-"))));
}

function bars(dist, format = pct) {
  const entries = Object.entries(dist || {});
  if (entries.length === 0) return h("p", { class: "muted" }, "없음");
  const max = Math.max(...entries.map(([, v]) => Number(v))) || 1;
  return h("div", {}, entries.map(([k, v]) => h("div", { class: "bar" },
    h("span", {}, k),
    h("div", { class: "track" }, h("div", { class: "fill", style: `width:${(Number(v) / max) * 100}%` })),
    h("span", { class: "muted" }, format(v)))));
}

function lineChart(series, { yMin = -1, yMax = 1, label = "" } = {}) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", "chart");
  svg.setAttribute("viewBox", "0 0 600 180");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", label);
  const pad = 24;
  const x = (i) => pad + (i / Math.max(series.length - 1, 1)) * (600 - 2 * pad);
  const y = (v) => 180 - pad - ((v - yMin) / (yMax - yMin || 1)) * (180 - 2 * pad);
  const mk = (tag, attrs) => {
    const el = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    svg.append(el);
    return el;
  };
  mk("line", { class: "axis", x1: pad, x2: 600 - pad, y1: y(0), y2: y(0) });
  if (series.length > 0) {
    mk("polyline", { class: "line", points: series.map((v, i) => `${x(i)},${y(Number(v))}`).join(" ") });
  }
  const t1 = mk("text", { x: pad, y: 170 });
  t1.textContent = "1화";
  const t2 = mk("text", { x: 600 - pad - 30, y: 170 });
  t2.textContent = `${series.length}화`;
  return svg;
}

function field(label, input) {
  return [h("label", {}, label), input];
}

// ---------------------------------------------------------------------------
// 화면: 작품 목록
// ---------------------------------------------------------------------------
async function viewNovels(root) {
  const novels = await api("GET", "/novels?limit=500");
  put(root,
    h("h1", {}, "작품"),
    h("p", {}, h("a", { href: "#/create" }, "+ 작품 만들기 (기획안 57번)")),
    table(
      [
        ["제목", (n) => h("a", { href: `#/novel/${n.slug}/overview` }, n.title)],
        ["slug", (n) => n.slug],
        ["장르", (n) => n.genre],
        ["목표", (n) => `${n.planned_episodes}화 · ${n.target_chars_per_episode.toLocaleString()}자`],
        ["상태", (n) => badge(n.status)],
      ],
      novels,
      "아직 작품이 없습니다."));
}

// ---------------------------------------------------------------------------
// 화면: 작품 만들기 (기획안 57번)
// ---------------------------------------------------------------------------
async function viewCreate(root) {
  const refs = await api("GET", "/references?limit=500");
  const text = h("textarea", { rows: 12, placeholder:
    "현대판타지 소설을 만들어줘.\n\n참고소설:\nA\nB\n\nA에서는 빠른 전개만 참고.\nB에서는 복선 구조 참고.\n\n250화.\n회차당 약 5,000자." });
  const slug = h("input", { placeholder: "비우면 자동" });
  const mode = h("select", {}, h("option", { value: "automatic" }, "automatic (자동 출판)"),
    h("option", { value: "manual" }, "manual"));
  const perRun = h("input", { type: "number", min: 1, max: 50, value: 1 });
  const continuous = h("input", { type: "checkbox" });
  const startNow = h("input", { type: "checkbox" });
  const preview = h("div");
  const parseBtn = h("button", { class: "ghost" }, "요청 해석만 보기");
  const createBtn = h("button", {}, "작품 만들기");
  parseBtn.addEventListener("click", async () => {
    const r = await act(parseBtn, () => api("POST", "/projects/parse", { text: text.value }));
    if (!r) return;
    preview.replaceChildren(h("div", { class: "panel" },
      stats([["장르", r.genre || "?"], ["회차", r.episodes ?? "?"], ["회차당", r.chars_per_episode ?? "?"],
        ["LLM 사용", r.used_llm ? "예" : "아니오"]]),
      table([["참고작", (x) => x.name],
        ["참고 항목", (x) => (x.aspects ? x.aspects.map((a) => ASPECT_LABELS[a] || a).join(", ") : "전부")]],
      r.references, "참고작 없음"),
      r.warnings.length ? h("ul", {}, r.warnings.map((w) => h("li", { class: "muted" }, w))) : null));
  });
  createBtn.addEventListener("click", async () => {
    const r = await act(createBtn, () => api("POST", "/projects", {
      text: text.value, slug: slug.value.trim(), publishing_mode: mode.value,
      episodes_per_run: Number(perRun.value) || 1, continuous: continuous.checked,
      start_now: startNow.checked,
    }), "작품을 만들었습니다.");
    if (r) location.hash = `#/novel/${r.slug}/overview`;
  });
  put(root,
    h("h1", {}, "작품 만들기"),
    h("p", { class: "muted" }, "참고소설 분석 → 공통 패턴 추출 → 세계관·캐릭터 생성 → 전체 스토리 → 표지 → 집필 예약. LLM이 필요합니다."),
    h("div", { class: "panel" },
      h("div", { class: "form-grid" },
        field("요청", text),
        field("slug", slug),
        field("출판 모드", mode),
        field("예약 1회당 회차", perRun),
        field("멈출 때까지 계속 쓰기", continuous),
        field("만든 뒤 바로 쓰기", startNow)),
      h("div", { class: "row", style: "margin-top:12px" }, parseBtn, createBtn)),
    preview,
    h("h2", {}, "등록된 참고소설"),
    table([["reference_id", (r) => r.reference_id], ["제목", (r) => r.title], ["상태", (r) => badge(r.status)]],
      refs, "참고소설이 없습니다. 먼저 참고소설 화면에서 올리세요."));
}

// ---------------------------------------------------------------------------
// 화면: 참고소설 관리 (기획안 49번)
// ---------------------------------------------------------------------------
async function viewReferences(root) {
  const refs = await api("GET", "/references?limit=500");
  const file = h("input", { type: "file", accept: ".txt,.md,.epub,.docx,.pdf,.hwp,.hwpx,.html,.htm" });
  const title = h("input", { placeholder: "소설 A" });
  const genre = h("input", { placeholder: "현대판타지" });
  const up = h("button", {}, "참고소설 추가");
  up.addEventListener("click", async () => {
    if (!file.files[0]) return toast("파일을 고르세요.", true);
    const fd = new FormData();
    fd.append("file", file.files[0]);
    fd.append("title", title.value);
    fd.append("genre", genre.value);
    const r = await act(up, () => api("POST", "/references/upload", fd, { form: true }), "올렸습니다.");
    if (r) render();
  });
  put(root,
    h("h1", {}, "참고소설"),
    h("div", { class: "panel" }, h("h2", {}, "[참고소설 추가]"),
      h("div", { class: "form-grid" }, field("파일 업로드", file), field("제목", title), field("장르", genre)),
      h("div", { style: "margin-top:8px" }, up)),
    table([
      ["reference_id", (r) => h("a", { href: `#/reference/${r.reference_id}` }, r.reference_id)],
      ["제목", (r) => r.title],
      ["장르", (r) => r.genre],
      ["형식", (r) => r.source_format],
      ["회차", (r) => r.episode_count],
      ["상태", (r) => badge(r.status)],
      ["", (r) => {
        const b = h("button", { class: "ghost" }, "분석 시작");
        b.addEventListener("click", async () => {
          const ok = await act(b, () => api("POST", `/references/${r.reference_id}/analyze`, {}), "분석했습니다.");
          if (ok) location.hash = `#/reference/${r.reference_id}`;
        });
        return b;
      }],
    ], refs, "아직 없습니다."));
}

async function viewReference(root, id) {
  const r = await api("GET", `/references/${encodeURIComponent(id)}`);
  const p = r.profile || {};
  put(root, h("h1", {}, `${p.title || id}`), h("p", {}, badge(r.status), " ", h("span", { class: "muted" }, id)));
  if (!p.summary) {
    put(root, h("p", { class: "muted" }, "아직 분석하지 않았습니다. 참고소설 목록에서 [분석 시작]을 누르세요."));
    return;
  }
  const s = p.summary, pace = p.pacing || {}, basic = p.basic_stats || {}, style = p.style || {};
  const fs = p.foreshadowing || {}, cl = p.cliffhanger || {}, ch = p.characters || {}, em = p.emotion || {};
  put(root,
    h("h2", {}, "전개 속도"),
    stats([["전개", pace.plot_speed], ["첫 사건", `${pace.first_event_episode ?? "-"}화`],
      ["첫 보상", `${pace.first_reward_episode ?? "-"}화`], ["절정", `${pace.climax_episode ?? "-"}화`],
      ["휴지 회차 비율", pct(pace.rest_episode_ratio)]]),
    h("h2", {}, "문장 통계"),
    stats([["평균 문장", `${num(style.avg_sentence_chars ?? basic.avg_sentence_chars)}자`],
      ["평균 문단", `${num(style.avg_paragraph_chars ?? basic.avg_paragraph_chars)}자`],
      ["짧은 문장", pct(style.short_sentence_ratio)], ["회차 평균", `${num(basic.avg_episode_chars, 0)}자`],
      ["시점", style.pov]]),
    h("h2", {}, "대사 비율"),
    bars({ 대사: basic.dialogue_ratio, 서술: basic.narration_ratio, 속마음: basic.inner_ratio }),
    h("h2", {}, "복선 패턴"),
    stats([["복선 후보", fs.candidate_count], ["평균 회수 간격", `${num(fs.avg_span)}화`],
      ["설치 위치", pct(fs.setup_position_ratio)], ["회수 위치", pct(fs.payoff_position_ratio)]]),
    h("h2", {}, "클리프행어 패턴"),
    stats([["사용률", pct(cl.rate)]]),
    bars(cl.distribution),
    h("h2", {}, "캐릭터 구조"),
    stats([["주인공", ch.protagonist_count], ["조연", ch.supporting_count], ["적대자", ch.antagonist_count],
      ["이름 있는 인물", ch.total_named_characters], ["재등장 간격", `${num(ch.avg_appearance_gap)}화`]]),
    h("h2", {}, "사건 간격"),
    stats([["중형 사건", `${num(s.minor_event_interval)}화마다`], ["대형 사건", `${num(s.major_event_interval)}화마다`],
      ["대형 사건 회차", (pace.major_event_episodes || []).join(", ")]]),
    h("h2", {}, "감정곡선"),
    em.series ? lineChart(em.series, { label: "회차별 감정 점수" }) : h("p", { class: "muted" }, "없음"),
    em.stage_distribution ? bars(em.stage_distribution) : null,
    h("h2", {}, "회차 구조"),
    bars(p.episode_shape),
    p.warnings && p.warnings.length ? h("ul", {}, p.warnings.map((w) => h("li", { class: "muted" }, w))) : null);
}

// ---------------------------------------------------------------------------
// 화면: Reference Patterns (전체 라이브러리)
// ---------------------------------------------------------------------------
async function viewPatterns(root) {
  const rows = await api("GET", "/patterns");
  put(root, h("h1", {}, "Reference Patterns"),
    table([["장르", (p) => p.genre], ["항목", (p) => ASPECT_LABELS[p.aspect] || p.aspect],
      ["지시", (p) => p.instruction], ["신뢰도", (p) => num(p.confidence, 2)], ["출처", (p) => p.source]],
    rows, "쌓인 패턴이 없습니다. 참고소설을 집계하면 생깁니다."));
}

// ---------------------------------------------------------------------------
// 화면: 스케줄러
// ---------------------------------------------------------------------------
async function viewScheduler(root) {
  const s = await api("GET", "/scheduler");
  const run = h("button", {}, "지금 실행");
  run.addEventListener("click", async () => {
    await act(run, () => api("POST", "/scheduler/run"), "실행을 시작했습니다.");
    render();
  });
  put(root, h("h1", {}, "자동 집필 스케줄러"),
    stats([["예약", s.enabled ? "켜짐" : "꺼짐"], ["시각", `${s.time} (${s.timezone})`],
      ["다음 실행", s.next_run_at || "-"], ["실행 중", s.running ? "예" : "아니오"]]),
    h("div", { class: "panel" }, run),
    h("h2", {}, "마지막 결과"),
    table([["작품", (r) => r.slug], ["쓴 회차", (r) => (r.written || []).join(", ") || "-"],
      ["건너뜀", (r) => r.skipped], ["멈춤", (r) => r.paused], ["오류", (r) => r.error]],
    s.last_results));
}

// ---------------------------------------------------------------------------
// 화면: 작품 상세 (기획안 48번)
// ---------------------------------------------------------------------------
async function viewNovel(root, slug, tab) {
  const bible = await api("GET", `/novels/${slug}/bible`);
  put(root, h("h1", {}, bible.title, " ", badge(bible.status)),
    h("nav", { class: "tabs" }, NOVEL_TABS.map(([key, label]) =>
      h("a", { href: `#/novel/${slug}/${key}`, class: key === tab ? "on" : "" }, label))));
  const body = h("div");
  put(root, body);
  const views = {
    overview: tabOverview, bible: tabBible, characters: tabCharacters, world: tabWorld,
    timeline: tabTimeline, relationships: tabRelationships, foreshadowing: tabForeshadowing,
    references: tabReferenceSettings, patterns: tabPatterns, episodes: tabEpisodes,
    similarity: tabSimilarity, publication: tabPublication,
  };
  await (views[tab] || tabOverview)(body, slug, bible);
}

async function tabOverview(root, slug) {
  const p = await api("GET", `/projects/${slug}`);
  const sched = p.schedule || {};
  const enable = h("input", { type: "checkbox", checked: sched.enabled });
  const perRun = h("input", { type: "number", min: 1, max: 50, value: sched.episodes_per_run || 1 });
  const cont = h("input", { type: "checkbox", checked: sched.continuous });
  const save = h("button", {}, "저장");
  save.addEventListener("click", () => act(save, () => api("PUT", `/novels/${slug}/schedule`, {
    enabled: enable.checked, episodes_per_run: Number(perRun.value) || 1, continuous: cont.checked,
  }), "저장했습니다."));
  const runNow = h("button", { class: "ghost" }, "지금 쓰기");
  runNow.addEventListener("click", () => act(runNow, () => api("POST", `/novels/${slug}/schedule/run`), "집필을 시작했습니다."));
  const resume = h("button", { class: "ghost" }, "재개");
  resume.addEventListener("click", async () => {
    if (await act(resume, () => api("POST", `/novels/${slug}/schedule/resume`), "재개했습니다.")) render();
  });
  const complete = h("button", { class: "ghost" }, "완결 검사");
  complete.addEventListener("click", async () => {
    const r = await act(complete, () => api("POST", `/novels/${slug}/complete`));
    if (r) { toast(`완결 검사: ${r.verdict}\n${r.summary}`); render(); }
  });
  put(root,
    stats([["진행", `${p.written} / ${p.planned_episodes}화`], ["진행률", pct(p.progress)],
      ["출판 모드", p.publishing_mode], ["완결", p.completion ? p.completion.verdict : "검사 전"]]),
    h("h2", {}, "생성 단계"),
    table([["단계", (s) => s.step], ["상태", (s) => badge(s.status)], ["시각", (s) => s.at]], p.steps, "작품 만들기로 만든 작품이 아닙니다."),
    h("h2", {}, "자동 집필"),
    h("div", { class: "panel" },
      sched.paused
        ? h("p", { class: p.status === "completed" ? "muted" : "v-WARN" }, `멈춤: ${sched.pause_reason}`)
        : null,
      h("div", { class: "form-grid" }, field("켜기", enable), field("1회당 회차", perRun), field("계속 쓰기", cont)),
      h("div", { class: "row", style: "margin-top:8px" }, save, runNow, resume, complete)),
    h("h2", {}, "출판 기록"),
    bars(p.publications || {}, (v) => `${v}건`));
}

async function tabBible(root, slug, bible) {
  const fields = [["title", "제목"], ["genre", "장르"], ["logline", "로그라인"], ["premise", "핵심 소재"],
    ["mood", "분위기"], ["pov", "시점"], ["target_reader", "독자층"], ["main_conflict", "핵심 갈등"],
    ["ending", "결말"], ["planned_episodes", "목표 회차"], ["target_chars_per_episode", "회차당 글자"]];
  const inputs = {};
  const grid = h("div", { class: "form-grid" });
  for (const [k, label] of fields) {
    const numeric = typeof bible[k] === "number";
    inputs[k] = numeric ? h("input", { type: "number", value: bible[k] })
      : (["logline", "premise", "main_conflict", "ending"].includes(k)
        ? h("textarea", { rows: 3, value: bible[k] || "" }) : h("input", { value: bible[k] || "" }));
    if (inputs[k].tagName === "TEXTAREA") inputs[k].value = bible[k] || "";
    grid.append(...field(label, inputs[k]));
  }
  const save = h("button", {}, "저장");
  save.addEventListener("click", () => act(save, () => api("PATCH", `/novels/${slug}`,
    Object.fromEntries(Object.entries(inputs).map(([k, el]) =>
      [k, el.type === "number" ? Number(el.value) : el.value]))), "저장했습니다."));
  let styleBlock = h("p", { class: "muted" }, "Style Bible 없음");
  try {
    const sb = await api("GET", `/novels/${slug}/style-bible`);
    styleBlock = stats([["문장", `${num(sb.target_sentence_chars)}자`], ["문단", `${num(sb.target_paragraph_chars)}자`],
      ["대사", pct(sb.target_dialogue_ratio)], ["시점", sb.pov], ["금지", sb.forbidden.join(", ") || "-"],
      ["선호", sb.preferred.join(", ") || "-"]]);
  } catch { /* 없음 */ }
  put(root, h("div", { class: "panel" }, grid, h("div", { style: "margin-top:8px" }, save)),
    h("h2", {}, "Style Bible"), styleBlock);
}

async function tabCharacters(root, slug) {
  const rows = await api("GET", `/novels/${slug}/characters`);
  put(root, table([["코드", (c) => c.code], ["이름", (c) => c.name], ["역할", (c) => c.role],
    ["나이", (c) => c.age], ["직업", (c) => c.job], ["첫 등장", (c) => c.first_episode],
    ["생존", (c) => (c.is_alive === false ? `퇴장 ${c.exit_episode ?? ""}화` : "")],
    ["성격", (c) => (c.personality || []).join(", ")]], rows));
}

async function tabWorld(root, slug) {
  const rows = await api("GET", `/novels/${slug}/world`);
  put(root, table([["분류", (w) => w.category], ["이름", (w) => w.name], ["설명", (w) => w.description]], rows));
}

async function tabTimeline(root, slug) {
  const rows = await api("GET", `/novels/${slug}/timeline`);
  put(root, table([["회차", (t) => t.episode_number], ["시점", (t) => t.occurred_at], ["사건", (t) => t.title],
    ["참여", (t) => (t.participants || []).join(", ")], ["중요도", (t) => t.importance]], rows));
}

async function tabRelationships(root, slug) {
  const rows = await api("GET", `/novels/${slug}/relationships`);
  put(root, table([["인물", (r) => `${r.source} → ${r.target}`], ["부터", (r) => `${r.from_episode}화`],
    ["상태", (r) => r.state], ["강도", (r) => num(r.intensity, 2)], ["메모", (r) => r.note]], rows));
}

async function tabForeshadowing(root, slug) {
  const rows = await api("GET", `/novels/${slug}/foreshadowings`);
  put(root, table([["코드", (f) => f.code], ["내용", (f) => f.description], ["설치", (f) => `${f.setup_episode}화`],
    ["회수 예정", (f) => (f.planned_payoff ? `${f.planned_payoff}화` : "-")], ["상태", (f) => badge(f.status)],
    ["", (f) => {
      if (f.status === "RESOLVED" || f.status === "ABANDONED") return "";
      const b = h("button", { class: "ghost" }, "회수 처리");
      b.addEventListener("click", async () => {
        if (await act(b, () => api("POST", `/novels/${slug}/foreshadowings/${f.code}/resolve`, {}), "회수 처리했습니다.")) render();
      });
      return b;
    }]], rows));
}

// 기획안 50번: 참고 설정 화면
async function tabReferenceSettings(root, slug) {
  const [data, all] = await Promise.all([api("GET", `/novels/${slug}/references`), api("GET", "/references?limit=500")]);
  const linked = new Set(data.links.map((l) => l.reference_id));
  for (const link of data.links) {
    const sliders = {};
    const box = h("div", { class: "panel" }, h("h2", {}, `${link.title} `, h("span", { class: "muted" }, link.reference_id)));
    for (const aspect of data.aspects) {
      const out = h("span", {}, `${Math.round(link.weights[aspect] * 100)}%`);
      const input = h("input", { type: "range", min: 0, max: 100, step: 5, value: Math.round(link.weights[aspect] * 100),
        "aria-label": ASPECT_LABELS[aspect] || aspect });
      input.addEventListener("input", () => (out.textContent = `${input.value}%`));
      sliders[aspect] = input;
      box.append(h("div", { class: "slider" }, h("span", {}, ASPECT_LABELS[aspect] || aspect), input, out));
    }
    const save = h("button", {}, "저장");
    save.addEventListener("click", () => act(save, () => api("POST", `/novels/${slug}/references`, {
      reference_id: link.reference_id,
      default_weight: link.default_weight,
      weights: Object.fromEntries(Object.entries(sliders).map(([k, el]) => [k, Number(el.value) / 100])),
    }), "참고 강도를 저장했습니다."));
    const del = h("button", { class: "ghost" }, "연결 끊기");
    del.addEventListener("click", async () => {
      if (await act(del, () => api("DELETE", `/novels/${slug}/references/${link.reference_id}`), "끊었습니다.") !== undefined) render();
    });
    box.append(h("div", { class: "row", style: "margin-top:8px" }, save, del));
    put(root, box);
  }
  if (data.links.length === 0) put(root, h("p", { class: "muted" }, "연결된 참고소설이 없습니다."));
  const choices = all.filter((r) => !linked.has(r.reference_id));
  if (choices.length) {
    const sel = h("select", {}, choices.map((r) => h("option", { value: r.reference_id }, `${r.title} (${r.reference_id})`)));
    const add = h("button", { class: "ghost" }, "참고소설 연결");
    add.addEventListener("click", async () => {
      if (await act(add, () => api("POST", `/novels/${slug}/references`, { reference_id: sel.value }), "연결했습니다.")) render();
    });
    put(root, h("div", { class: "panel row" }, sel, add));
  }
}

async function tabPatterns(root, slug) {
  const g = await api("GET", `/novels/${slug}/guidance`);
  const guide = g.guidance || {};
  put(root,
    stats([["출처", guide.source], ["클리프행어 사용률", pct(guide.cliffhanger_rate)],
      ["중형 사건", `${(guide.minor_interval || []).join("~")}화`], ["대형 사건", `${(guide.major_interval || []).join("~")}화`],
      ["복선 간격", `${(guide.foreshadow_span || []).join("~")}화`], ["대사 비율", pct(guide.dialogue_ratio)]]),
    h("h2", {}, "회차 구조"), bars(guide.episode_shape),
    h("h2", {}, "클리프행어 배분"), bars(guide.cliffhanger_distribution),
    h("h2", {}, "적용할 패턴"),
    (g.pattern_instructions || []).length
      ? h("ul", {}, g.pattern_instructions.map((p) => h("li", {}, p)))
      : h("p", { class: "muted" }, "없음"));
}

async function tabEpisodes(root, slug) {
  const rows = await api("GET", `/novels/${slug}/episodes`);
  const detail = h("div");
  const number = h("input", { type: "number", min: 1, value: (rows.length ? Math.max(...rows.map((r) => r.number)) : 0) + 1 });
  const gen = h("button", {}, "이 회차 생성");
  gen.addEventListener("click", async () => {
    if (await act(gen, () => api("POST", `/novels/${slug}/episodes/${number.value}/generate`), "생성했습니다.")) render();
  });
  put(root, h("div", { class: "panel row" }, h("span", {}, "회차"), number, gen,
    h("span", { class: "muted" }, "LLM이 필요합니다. 몇 분 걸릴 수 있습니다.")),
  table([["회차", (e) => h("a", { href: "#", onclick: (ev) => { ev.preventDefault(); showEpisode(detail, slug, e.number); } }, `${e.number}화`)],
    ["제목", (e) => e.title], ["글자", (e) => e.char_count?.toLocaleString()], ["훅", (e) => e.hook_type],
    ["상태", (e) => badge(e.status)], ["요약", (e) => e.summary]], rows), detail);
}

async function showEpisode(root, slug, n) {
  const e = await api("GET", `/novels/${slug}/episodes/${n}`);
  const quality = (e.quality_reports || {}).quality;
  const check = h("button", { class: "ghost" }, "품질 검사");
  check.addEventListener("click", async () => {
    const r = await act(check, () => api("POST", `/novels/${slug}/episodes/${n}/check`));
    if (r) showEpisode(root, slug, n);
  });
  const fix = h("button", { class: "ghost" }, "검사 + FAIL 장면 수정");
  fix.addEventListener("click", async () => {
    const r = await act(fix, () => api("POST", `/novels/${slug}/episodes/${n}/check?fix=true`));
    if (r) showEpisode(root, slug, n);
  });
  const finalize = h("button", { class: "ghost" }, "확정 (기억 갱신)");
  finalize.addEventListener("click", async () => {
    if (await act(finalize, () => api("POST", `/novels/${slug}/episodes/${n}/memory`), "확정했습니다.")) render();
  });
  const results = quality ? Object.values(quality.results || {}) : [];
  const issues = results.flatMap((r) => r.issues || []);
  root.replaceChildren(h("div", { class: "panel" },
    h("h2", {}, `${e.number}화 ${e.title} `, badge(e.status)),
    h("div", { class: "row" }, check, e.status !== "final" ? fix : null, e.status !== "final" ? finalize : null),
    quality ? h("div", {}, h("h2", {}, "품질 검사"),
      h("div", { class: "row" }, results.map((r) => h("span", {}, `${r.checker} `, badge(r.verdict)))),
      table([["검사", (i) => i.checker], ["심각도", (i) => badge(i.severity)], ["내용", (i) => i.message],
        ["장면", (i) => (i.scene_index ?? -1) >= 0 ? i.scene_index + 1 : "-"], ["인용", (i) => i.quote]], issues, "지적 없음"))
      : h("p", { class: "muted" }, "품질 검사 전"),
    h("h2", {}, "본문"), h("div", { class: "text" }, e.text || "")));
  root.scrollIntoView({ behavior: "smooth" });
}

async function tabSimilarity(root, slug) {
  const rows = await api("GET", `/novels/${slug}/similarity`);
  put(root, table([["회차", (r) => `${r.number}화`], ["판정", (r) => badge(r.verdict)],
    ["최대 포함률", (r) => pct(r.max_containment, 1)], ["최대 자카드", (r) => pct(r.max_jaccard, 1)],
    ["가장 비슷한 참고작", (r) => (r.hits && r.hits[0] ? `${r.hits[0].reference_id} (${r.hits[0].scope || ""})` : "-")]],
  rows, "유사도 보고가 없습니다."));
}

async function tabPublication(root, slug) {
  const [info, ebooks, pubs] = await Promise.all([
    api("GET", `/novels/${slug}/publishing`), api("GET", `/novels/${slug}/ebooks`), api("GET", `/novels/${slug}/publications`)]);
  const f = {
    author: h("input", { value: info.author }), publisher: h("input", { value: info.publisher }),
    price: h("input", { type: "number", min: 0, value: info.price }), currency: h("input", { value: info.currency }),
    description: h("textarea", { rows: 4 }), keywords: h("input", { value: (info.keywords || []).join(", ") }),
    categories: h("input", { value: (info.categories || []).join(", ") }), author_note: h("textarea", { rows: 2 }),
    volume_size: h("input", { type: "number", min: 0, value: info.volume_size }),
  };
  f.description.value = info.description || "";
  f.author_note.value = info.author_note || "";
  const mode = h("select", {}, ["manual", "automatic"].map((m) => h("option", { value: m, selected: m === info.publishing_mode }, m)));
  const save = h("button", {}, "저장");
  save.addEventListener("click", () => act(save, () => api("PUT", `/novels/${slug}/publishing`, {
    author: f.author.value, publisher: f.publisher.value, price: Number(f.price.value) || 0,
    currency: f.currency.value, description: f.description.value,
    keywords: f.keywords.value.split(",").map((s) => s.trim()).filter(Boolean),
    categories: f.categories.value.split(",").map((s) => s.trim()).filter(Boolean),
    author_note: f.author_note.value, volume_size: Number(f.volume_size.value) || 0, publishing_mode: mode.value,
  }), "저장했습니다."));
  const coverImg = h("img", { class: "cover", alt: "표지", src: `/novels/${slug}/cover.jpg?t=${Date.now()}` });
  coverImg.addEventListener("error", () => coverImg.replaceWith(h("p", { class: "muted" }, "표지가 아직 없습니다.")));
  const makeCover = h("button", { class: "ghost" }, "표지 만들기");
  makeCover.addEventListener("click", async () => {
    if (await act(makeCover, () => api("POST", `/novels/${slug}/cover`), "표지를 만들었습니다.")) render();
  });
  const makeBooks = h("button", { class: "ghost" }, "전자책 만들기");
  makeBooks.addEventListener("click", async () => {
    const r = await act(makeBooks, () => api("POST", `/novels/${slug}/ebooks`));
    if (r) { toast(r.volumes.length ? `${r.volumes.length}권` : r.warnings.join("\n")); render(); }
  });
  const process = h("button", { class: "ghost" }, "대기열 처리");
  process.addEventListener("click", async () => {
    if (await act(process, () => api("POST", `/publications/process?novel=${encodeURIComponent(slug)}`), "처리했습니다.")) render();
  });
  put(root,
    h("div", { class: "panel" }, h("h2", {}, "출판 정보"),
      h("div", { class: "form-grid" }, field("출판 모드", mode), field("작가명", f.author), field("출판사", f.publisher),
        field("가격", f.price), field("통화", f.currency), field("작품설명", f.description),
        field("키워드 (쉼표)", f.keywords), field("카테고리 (쉼표)", f.categories),
        field("작가의 말", f.author_note), field("권당 회차 (0=완결 뒤 한 권)", f.volume_size)),
      h("div", { style: "margin-top:8px" }, save)),
    h("div", { class: "panel" }, h("h2", {}, "표지"), coverImg, h("div", { style: "margin-top:8px" }, makeCover)),
    h("h2", {}, "전자책"),
    h("div", { class: "row" }, makeBooks),
    table([["권", (b) => b.name], ["제목", (b) => b.title], ["회차", (b) => `${b.start}~${b.end}화`],
      ["파일", (b) => h("span", {}, b.files.map((file) => h("a", { href: `/novels/${slug}/ebooks/${b.name}/${file}`, style: "margin-right:8px" }, file)))]],
    ebooks, "아직 없습니다."),
    h("h2", {}, "출판 기록"),
    h("div", { class: "row" }, process),
    table([["#", (p) => p.id], ["종류", (p) => (p.kind === "episode" ? `${p.episode_number}화` : `${p.volume}권`)],
      ["대상", (p) => p.target], ["상태", (p) => badge(p.status)], ["시도", (p) => p.attempts],
      ["위치", (p) => (p.location ? h("span", { title: p.location }, p.location.split(/[\\/]/).filter(Boolean).pop()) : "-")],
      ["오류", (p) => p.error]], pubs));
}

// ---------------------------------------------------------------------------
// 라우터
// ---------------------------------------------------------------------------
async function render() {
  const root = document.getElementById("app");
  const parts = (location.hash.replace(/^#\/?/, "") || "novels").split("/").map(decodeURIComponent);
  document.querySelectorAll(".main-nav a").forEach((a) => a.classList.toggle("on",
    a.dataset.nav === (parts[0] === "novel" ? "novels" : parts[0] === "reference" ? "references" : parts[0])));
  root.replaceChildren(h("p", { class: "muted" }, "불러오는 중…"));
  const view = h("div");
  try {
    if (parts[0] === "novel") await viewNovel(view, parts[1], parts[2] || "overview");
    else if (parts[0] === "reference") await viewReference(view, parts[1]);
    else if (parts[0] === "references") await viewReferences(view);
    else if (parts[0] === "create") await viewCreate(view);
    else if (parts[0] === "patterns") await viewPatterns(view);
    else if (parts[0] === "scheduler") await viewScheduler(view);
    else await viewNovels(view);
    root.replaceChildren(view);
  } catch (e) {
    root.replaceChildren(h("div", { class: "panel" }, h("p", { class: "v-FAIL" }, e.message)));
  }
}

window.addEventListener("hashchange", render);
render();
