const FALLBACK_FEED = {
  auto_refresh: { enabled: true, poll_interval_seconds: 60 },
  feed_id: "radar_timeline_feed_fixture_001",
  fixture_only: true,
  generated_at: "2026-06-15T01:00:00Z",
  view_config: {
    default_recent_hours: 120,
    supported_recent_hours: [12, 24, 48, 72, 120],
    default_sort: "hotness",
    supported_sorts: [
      { id: "hotness", field: "hotness_score", direction: "desc" },
      { id: "published_at", field: "published_at", direction: "desc" },
    ],
  },
  items: [
    {
      id: "radar_timeline_fixture_001",
      source_label: "牛子强示例",
      source: "fixture_source",
      author: "示例作者",
      published_at: "2026-06-14T00:00:00Z",
      copy_text: "一条新的政策解读正在加速扩散，适合放进 4 小时回访窗口观察。",
      image_status: "available",
      image_refs: [{ image_ref_id: "image_fixture_001" }],
      hotness_score: 0.71,
      hotness_series: [0.18, 0.22, 0.31, 0.39, 0.52, 0.63, 0.71],
    },
  ],
};

const feedCandidates = [
  "/api/timeline",
  "./timeline_feed.json",
];

const state = {
  feed: FALLBACK_FEED,
  loadedFrom: "embedded fixture",
  recentHours: 120,
  sortMode: "hotness",
  sourceFilter: "all",
  languageFilter: "all",
  renderedItems: [],
  refreshTimer: null,
  filterTimer: null,
  motionObserver: null,
  restoringHash: false,
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function assetUrl(assetRef) {
  const ref = String(assetRef || "");
  if (!ref) return "";
  if (ref.startsWith("http://") || ref.startsWith("https://") || ref.startsWith("file://")) return ref;
  if (ref.startsWith("artifacts/")) return `../../${ref}`;
  return ref;
}

function publicUrl(value) {
  const url = String(value || "");
  return url.startsWith("http://") || url.startsWith("https://") ? url : "";
}

function isDemoFeed(feed, loadedFrom) {
  const smoke = feed.manual_smoke || {};
  const scoring = smoke.scoring || {};
  const runtimeStage = String(feed.rolling_runtime?.runtime_stage || "");
  return Boolean(
    feed.status === "demo" ||
      feed.fixture_only ||
      feed.no_real_source_access ||
      loadedFrom.includes("fixture") ||
      loadedFrom.includes("embedded") ||
      smoke.backend === "fixture" ||
      scoring.fallback_used === "fixture_scoring" ||
      runtimeStage.includes("fixture")
  );
}

function compactCount(value) {
  const number = Number(value || 0);
  if (!Number.isFinite(number)) return "0";
  if (number >= 10000) return `${(number / 10000).toFixed(1).replace(/\.0$/, "")}万`;
  if (number >= 1000) return `${(number / 1000).toFixed(1).replace(/\.0$/, "")}千`;
  return String(number);
}

function percent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

function previewCopy(text, limit = 220) {
  const cleaned = String(text || "")
    .replace(/https?:\/\/\S+/g, "")
    .replace(/www\.\S+/g, "")
    .replace(/\s+/g, " ")
    .trim();
  if (cleaned.length <= limit) return cleaned;
  return `${cleaned.slice(0, limit).trim()}...`;
}

function readableTitle(item) {
  const copy = previewCopy(item.copy_text, 96);
  const hook = String(item.topic_or_hook || "").trim();
  const hookLooksInternal = !hook || hook.includes("_") || /^[a-z0-9 -]{4,48}$/i.test(hook);
  if (copy) return copy.replace(/[。.!?？].*$/, (match) => (match.length <= 2 ? match : match.slice(0, 1)));
  return hookLooksInternal ? "原文线索" : hook;
}

async function loadFeed() {
  for (const url of feedCandidates) {
    try {
      const response = await fetch(url, { cache: "no-store" });
      if (response.ok) return { feed: await response.json(), loadedFrom: url };
    } catch {
      // ponytail: static local previews can use the embedded fixture; deployed HTTP cannot.
    }
  }
  if (!["localhost", "127.0.0.1", ""].includes(window.location.hostname)) {
    return {
      feed: {
        auto_refresh: { enabled: true, poll_interval_seconds: 60 },
        generated_at: new Date().toISOString(),
        items: [],
        status: "blocked",
        view_config: FALLBACK_FEED.view_config,
      },
      loadedFrom: "no live feed",
    };
  }
  return { feed: FALLBACK_FEED, loadedFrom: "embedded fixture" };
}

function clamp01(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.min(1, number));
}

function score(item) {
  const raw = Number(item.radar_score ?? item.hotness_score ?? 0);
  if (!Number.isFinite(raw)) return 0;
  return Math.round(raw > 1 ? raw : raw * 100);
}

function itemSeries(item) {
  const series = Array.isArray(item.hotness_series) ? item.hotness_series.map(clamp01) : [];
  return series.length ? series : [0.12, 0.28, 0.46, clamp01(score(item) / 100)];
}

function aggregateSeries(items) {
  const seriesList = items.map(itemSeries);
  if (!seriesList.length) return [0.08, 0.15, 0.22, 0.2, 0.18, 0.16, 0.14];
  const maxLength = Math.max(...seriesList.map((series) => series.length));
  return Array.from({ length: maxLength }, (_, index) => {
    const sum = seriesList.reduce((total, series) => total + (series[index] ?? series.at(-1) ?? 0), 0);
    return clamp01(sum / seriesList.length);
  });
}

function seriesFromRatio(ratio, length = 9) {
  const base = clamp01(ratio);
  return Array.from({ length }, (_, index) => {
    const wave = Math.sin((index / Math.max(1, length - 1)) * Math.PI) * 0.22;
    return clamp01(base * 0.74 + wave + index * 0.015);
  });
}

function lineSvg(points, className = "sparkline", tone = "green", width = 160, height = 48) {
  const values = Array.isArray(points) && points.length ? points.map(clamp01) : [0, 0.5, 1];
  const step = values.length > 1 ? width / (values.length - 1) : width;
  const coords = values.map((value, index) => {
    const x = index * step;
    const y = height - value * (height - 10) - 5;
    return [x, y];
  });
  const path = coords.map(([x, y], index) => `${index === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const length = coords.slice(1).reduce((total, point, index) => {
    const prev = coords[index];
    return total + Math.hypot(point[0] - prev[0], point[1] - prev[1]);
  }, 0);
  const last = coords.at(-1);
  return `
    <svg class="${className} ${tone === "orange" ? "orange" : ""}" viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" aria-hidden="true" style="--path-length:${Math.ceil(length)}">
      <path d="${path}"></path>
      <circle cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="3"></circle>
    </svg>
  `;
}

function radarGraphic(value = 0.68) {
  const radius = 20 + clamp01(value) * 34;
  return `
    <svg class="radar" viewBox="0 0 120 120" aria-hidden="true">
      <circle cx="60" cy="60" r="24"></circle>
      <circle cx="60" cy="60" r="40"></circle>
      <circle cx="60" cy="60" r="56"></circle>
      <line x1="60" y1="10" x2="60" y2="110"></line>
      <line x1="10" y1="60" x2="110" y2="60"></line>
      <path class="radar-fill" d="M60 60 L78 34 A${radius} ${radius} 0 0 1 86 71 Z"></path>
      <line class="radar-sweep" x1="60" y1="60" x2="84" y2="28"></line>
      <circle class="radar-dot" cx="60" cy="60" r="8"></circle>
    </svg>
  `;
}

function armMotion() {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  document.documentElement.classList.add("motion-ready");
  if (!state.motionObserver) {
    state.motionObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        entry.target.classList.add("in-view");
        state.motionObserver.unobserve(entry.target);
      }
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
  }

  const items = document.querySelectorAll(".signal-board, .signal-board .stat, .language-button, .chart-panel, .timeline-card");
  items.forEach((item, index) => {
    item.classList.add("motion-item");
    item.style.setProperty("--motion-delay", `${Math.min((index % 8) * 55, 330)}ms`);
    state.motionObserver.observe(item);
  });
}

function animateCounts() {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const startedAt = performance.now();
  const elements = document.querySelectorAll("[data-count]");
  function step(now) {
    const progress = Math.min(1, (now - startedAt) / 720);
    const eased = 1 - Math.pow(1 - progress, 3);
    elements.forEach((element) => {
      const target = Number(element.dataset.count || 0);
      if (!Number.isFinite(target)) return;
      element.textContent = compactCount(Math.round(target * eased));
    });
    if (progress < 1) window.requestAnimationFrame(step);
  }
  window.requestAnimationFrame(step);
}

function flashUpdate() {
  if (state.filterTimer) window.clearTimeout(state.filterTimer);
  const nodes = [document.getElementById("quickStats"), document.getElementById("telemetryPanels"), document.getElementById("timeline")].filter(Boolean);
  for (const node of nodes) node.classList.add("is-filtering");
  state.filterTimer = window.setTimeout(() => {
    for (const node of nodes) node.classList.remove("is-filtering");
  }, 260);
}

function bestAsset(item) {
  const assets = Array.isArray(item.asset_refs) ? item.asset_refs.filter((asset) => asset && asset.asset_ref) : [];
  if (!assets.length) return null;
  return [...assets].sort((left, right) => {
    const leftArea = Number(left.dimensions?.width || 0) * Number(left.dimensions?.height || 0);
    const rightArea = Number(right.dimensions?.width || 0) * Number(right.dimensions?.height || 0);
    return rightArea - leftArea;
  })[0];
}

function firstOriginalImageRef(item) {
  const refs = Array.isArray(item.image_refs) ? item.image_refs : [];
  return refs.find((ref) => ref && (ref.original_url || ref.image_url || ref.url || ref.thumbnail_url)) || refs[0] || null;
}

function imageLabel(item) {
  if (bestAsset(item)) return "有图";
  if (item.image_status === "available") return "保留原图";
  if (item.image_status === "image_unavailable") return "图片不可用";
  if (item.image_status === "auth_gated") return "需登录";
  return "无图";
}

function imagePreview(item) {
  const asset = bestAsset(item);
  if (asset) {
    const width = Number(asset.dimensions?.width || 0);
    const height = Number(asset.dimensions?.height || 0);
    return `
      <figure class="image-preview has-image">
        <img src="${escapeHtml(assetUrl(asset.asset_ref))}" alt="${escapeHtml(item.topic_or_hook || item.copy_text || "原文图片")}" loading="lazy" />
        <figcaption>${width && height ? `${width}×${height}` : "原图"}</figcaption>
      </figure>
    `;
  }
  const ref = firstOriginalImageRef(item);
  if (item.image_status === "available" && ref) return `<div class="image-preview ref-only"><span>保留原图链接</span></div>`;
  if (item.image_status === "image_unavailable") return `<div class="image-preview unavailable"><span>图片不可用</span></div>`;
  return `<div class="image-preview empty"><span>无图片</span></div>`;
}

function outcomeLabel(item) {
  const status = String(item.outcome_status || item.revisit_status || "");
  if (status.includes("24h") || status.includes("collected")) return "已看结果";
  if (status.includes("pending") || status.includes("not_revisited")) return "等结果";
  return status ? "有结果记录" : "等结果";
}

function evalLabel(item) {
  const status = String(item.eval_status || "");
  if (status.includes("joined")) return "已验真";
  if (status.includes("pending")) return "待验真";
  return status ? "有验真记录" : "待验真";
}

function sourceQualityLabel(item) {
  const value = item.source_quality_status || item.source_material_role || item.quality_status;
  const map = {
    source_row_observed: "原文已观察",
    original_source_candidate: "原文线索",
    quoted_original_traced: "已追到引用原文",
    summary_or_list_excerpt_only: "仅摘要",
    candidate: "线索",
  };
  return map[value] || (value ? String(value).replaceAll("_", " ") : "证据待确认");
}

function sortItems(items, sortMode) {
  return [...items].sort((left, right) => {
    if (sortMode === "published_at") return String(right.published_at || "").localeCompare(String(left.published_at || ""));
    const delta = score(right) - score(left);
    return delta || String(right.published_at || "").localeCompare(String(left.published_at || ""));
  });
}

function filterRecent(items, generatedAt, recentHours) {
  const anchor = Date.parse(generatedAt || "");
  if (!Number.isFinite(anchor)) return items;
  const cutoff = anchor - Number(recentHours || 120) * 60 * 60 * 1000;
  return items.filter((item) => {
    const published = Date.parse(item.published_at || "");
    return Number.isFinite(published) && published >= cutoff;
  });
}

function optionLabel(value, type) {
  if (type === "hours") return `${value} 小时`;
  if (value === "published_at") return "发布时间";
  return "可能爆分";
}

function detectLanguage(text) {
  const value = String(text || "");
  const zh = (value.match(/[\u4e00-\u9fff]/g) || []).length;
  const latin = (value.match(/[A-Za-z]/g) || []).length;
  if (zh > 0) return "zh";
  if (latin > 24) return "en";
  return "zh";
}

function languageLabel(language) {
  if (language === "zh") return "中文";
  if (language === "en") return "英文";
  return "全部";
}

function sourceKey(item) {
  return item.source_label || item.source || "unknown";
}

function sourceDisplayName(value) {
  const key = String(value || "").trim();
  const map = {
    "Fixture Source": "牛子强示例",
    fixture_source: "牛子强示例",
  };
  return map[key] || key || "来源";
}

function readableFlag(value) {
  const key = String(value || "").trim();
  const map = {
    low_base: "样本太小",
    no_image: "无图",
    image_unavailable: "图片不可用",
    auth_gated: "需登录",
    summary_or_list_excerpt_only: "仅摘要",
    fixture_scoring: "示例打分",
    shadow_fixture_outcome_not_ground_truth: "示例结果",
    revisit_pending_fixture_not_ground_truth: "等结果",
    expired_fixture_not_exported: "已过期",
  };
  return map[key] || "风险标记";
}

function metricLabel(value) {
  const key = String(value || "");
  const map = {
    replies: "回复",
    reposts: "转发",
    likes: "点赞",
    comments: "评论",
    views: "浏览",
    saves: "收藏",
    shares: "分享",
  };
  return map[key] || "指标";
}

function formatTime(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).replace("T", " ").replace("Z", "");
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function syncControls(feed) {
  const viewConfig = feed.view_config || {};
  const hours = Array.isArray(viewConfig.supported_recent_hours) && viewConfig.supported_recent_hours.length
    ? viewConfig.supported_recent_hours
    : [12, 24, 48, 72, 120];
  const sorts = Array.isArray(viewConfig.supported_sorts) && viewConfig.supported_sorts.length
    ? viewConfig.supported_sorts.map((sort) => sort.id)
    : ["hotness", "published_at"];

  if (!hours.includes(state.recentHours)) state.recentHours = Number(viewConfig.default_recent_hours || 120);
  if (!sorts.includes(state.sortMode)) state.sortMode = viewConfig.default_sort || "hotness";

  document.getElementById("recentHours").innerHTML = hours
    .map((value) => `<option value="${value}"${Number(value) === state.recentHours ? " selected" : ""}>${optionLabel(value, "hours")}</option>`)
    .join("");
  document.getElementById("sortMode").innerHTML = sorts
    .map((value) => `<option value="${escapeHtml(value)}"${value === state.sortMode ? " selected" : ""}>${optionLabel(value, "sort")}</option>`)
    .join("");

  const sourceOptions = ["all", ...Array.from(new Set((feed.items || []).map(sourceKey))).sort()];
  if (!sourceOptions.includes(state.sourceFilter)) state.sourceFilter = "all";
  document.getElementById("sourceFilter").innerHTML = sourceOptions
    .map((value) => `<option value="${escapeHtml(value)}"${value === state.sourceFilter ? " selected" : ""}>${value === "all" ? "全部来源" : escapeHtml(sourceDisplayName(value))}</option>`)
    .join("");
}

function visibleItems(feed) {
  const recentItems = filterRecent(feed.items || [], feed.generated_at, state.recentHours);
  const sourceItems = recentItems.filter((item) => state.sourceFilter === "all" || sourceKey(item) === state.sourceFilter);
  const languageItems = sourceItems.filter((item) => state.languageFilter === "all" || detectLanguage(item.copy_text) === state.languageFilter);
  return sortItems(languageItems, state.sortMode);
}

function renderStats(feed, items, loadedFrom) {
  const sourceCount = new Set(items.map((item) => item.source_label || item.source)).size;
  const imageCount = items.filter((item) => bestAsset(item) || item.image_status === "available").length;
  const revisited = items.filter((item) => outcomeLabel(item).includes("已")).length;
  const evaluated = items.filter((item) => evalLabel(item).includes("已")).length;
  const avgScore = items.length ? items.reduce((total, item) => total + score(item), 0) / items.length : 0;
  const isFixture = isDemoFeed(feed, loadedFrom);
  const feedStatus = document.getElementById("feedStatus");
  feedStatus.textContent = isFixture ? "演示数据" : "已连接";
  feedStatus.className = `feed-pill ${isFixture ? "fixture" : "live"}`;

  const avgSeries = aggregateSeries(items);
  const stages = [
    {
      index: "1",
      title: "找爆点",
      sub: `${items.length} 条可能爆`,
      metrics: [["扫到", `${compactCount(items.length)} 条`], ["来自", `${compactCount(sourceCount)} 个来源`], ["重点看", "4 小时内"]],
      series: avgSeries,
      tone: "green",
    },
    {
      index: "2",
      title: "猜热度",
      sub: `${Math.round(avgScore)} 分可能会爆`,
      metrics: [["短线", "1 小时"], ["长线", "4 小时"], ["冷门", "自动降噪"]],
      series: avgSeries.map((value, index) => clamp01(value * (0.78 + index * 0.04))),
      tone: "orange",
    },
    {
      index: "3",
      title: "回头看",
      sub: `${revisited} 条看过结果`,
      metrics: [["先看", "4 小时后"], ["再看", "24 小时后"], ["完成", percent(items.length ? revisited / items.length : 0)]],
      series: seriesFromRatio(items.length ? revisited / items.length : 0),
      tone: "green",
    },
    {
      index: "4",
      title: "验真假",
      sub: `${evaluated} 条有结果`,
      metrics: [["猜对没", "自己算"], ["没爆", "记下来"], ["下轮", "自动改"]],
      series: seriesFromRatio(items.length ? evaluated / items.length : 0.12),
      tone: "orange",
    },
    {
      index: "5",
      title: "留素材",
      sub: `${imageCount} 个图文线索`,
      metrics: [["原文", "保留"], ["图片", `${imageCount} 个`], ["分数", "只自己看"]],
      series: seriesFromRatio(items.length ? imageCount / items.length : 0.08),
      tone: "green",
    },
    {
      index: "6",
      title: "自我进化",
      sub: isFixture ? "演示数据" : "正在学习",
      metrics: [["下一轮", "更会挑"], ["状态", isFixture ? "演示" : "在线"], ["更新", formatTime(feed.generated_at)]],
      series: avgSeries.map((value, index) => clamp01(0.18 + (index % 3) * 0.08 + value * 0.42)),
      tone: "orange",
    },
  ];

  document.getElementById("quickStats").innerHTML = `
    <article class="stat">
      <div class="stat-label">全网扫描</div>
      ${radarGraphic(avgScore / 100)}
      <div>
        <div class="stat-note">现在有这些线索</div>
        <div class="stat-value" data-count="${Number(items.length || 0)}">${compactCount(items.length)}</div>
      </div>
      <div class="stat-note">${compactCount(sourceCount)} 个来源</div>
    </article>
    ${stages.map((stage) => `
      <article class="stat compact">
        <div class="stat-index">${stage.index}</div>
        <div>
          <div class="stat-label">${escapeHtml(stage.title)}</div>
          <div class="stat-note">${escapeHtml(stage.sub)}</div>
        </div>
        <div class="stat-body">
          ${stage.metrics.map(([label, value]) => `
            <div class="metric-pair">
              <span class="metric-label">${escapeHtml(label)}</span>
              <span class="metric-value">${escapeHtml(value)}</span>
            </div>
          `).join("")}
        </div>
        ${lineSvg(stage.series, "sparkline", stage.tone)}
      </article>
    `).join("")}
    <article class="stat">
      <div class="stat-label">可用素材</div>
      ${radarGraphic(items.length ? imageCount / items.length : 0.2)}
      <div>
        <div class="stat-note">能拿去复盘</div>
        <div class="stat-value" data-count="${Number(imageCount || 0)}">${compactCount(imageCount)}</div>
      </div>
      <div class="stat-note">${compactCount(evaluated)} 条已验真</div>
    </article>
  `;
}

function renderTelemetryPanels(items) {
  const sourceCount = new Set(items.map((item) => item.source_label || item.source)).size;
  const imageCount = items.filter((item) => bestAsset(item) || item.image_status === "available").length;
  const revisited = items.filter((item) => outcomeLabel(item).includes("已")).length;
  const evaluated = items.filter((item) => evalLabel(item).includes("已")).length;
  const avgScore = items.length ? items.reduce((total, item) => total + score(item), 0) / items.length : 0;
  const panels = [
    ["扫到的爆点", items.length, "条", "线索", `${sourceCount} 个来源`, aggregateSeries(items), "green"],
    ["可能会爆", Math.round(avgScore), "分", "判断", `${evaluated} 条已验真`, aggregateSeries(items).map((value) => clamp01(value * 0.92 + 0.04)), "orange"],
    ["看结果", revisited, "条", "闭环", `${items.length ? Math.round((revisited / items.length) * 100) : 0}% 有结果`, seriesFromRatio(items.length ? revisited / items.length : 0), "orange"],
    ["可用素材", imageCount, "个", "图文", `${items.length ? Math.round((imageCount / items.length) * 100) : 0}% 有图`, seriesFromRatio(items.length ? imageCount / items.length : 0), "green"],
  ];

  document.getElementById("telemetryPanels").innerHTML = panels.map(([title, value, unit, kicker, foot, series, tone]) => `
    <article class="chart-panel">
      <div class="chart-head">
        <h2 class="chart-title">${escapeHtml(title)}</h2>
        <span class="chart-kicker">${escapeHtml(kicker)}</span>
      </div>
      <div class="chart-value">
        <strong data-count="${Number(value || 0)}">${escapeHtml(compactCount(value))}</strong>
        <span>${escapeHtml(unit)}</span>
      </div>
      <div class="delta ${tone === "orange" ? "down" : ""}">${tone === "orange" ? "- " : "+ "}${escapeHtml(foot)}</div>
      <div class="chart-stage">
        <div class="chart-axis"><span>高</span><span>中</span><span>低</span></div>
        <div class="chart-line">
          <span class="chart-band ${tone === "orange" ? "orange" : ""}"></span>
          <span class="chart-mid ${tone === "orange" ? "orange" : ""}"></span>
          ${lineSvg(series, "panel-chart", tone, 640, 188)}
        </div>
      </div>
      <div class="chart-foot"><span>峰值记录</span><span>现在</span></div>
    </article>
  `).join("");
}

function renderLanguageRadar(itemsBeforeLanguage) {
  const counts = { all: itemsBeforeLanguage.length, zh: 0, en: 0 };
  for (const item of itemsBeforeLanguage) counts[detectLanguage(item.copy_text)] += 1;
  document.getElementById("languageRadar").innerHTML = ["all", "zh", "en"]
    .map((language) => `
      <button class="language-button ${state.languageFilter === language ? "active" : ""}" type="button" data-language="${escapeHtml(language)}">
        <span>${escapeHtml(languageLabel(language))}</span>
        <strong>${compactCount(counts[language] || 0)}</strong>
      </button>
    `)
    .join("");
}

function itemChips(item) {
  const chips = [
    { text: sourceQualityLabel(item), tone: "soft" },
    { text: imageLabel(item), tone: item.image_status === "available" || bestAsset(item) ? "good" : "soft" },
    { text: outcomeLabel(item), tone: outcomeLabel(item).includes("已") ? "good" : "soft" },
    { text: evalLabel(item), tone: evalLabel(item).includes("已") ? "good" : "soft" },
  ];
  const risks = Array.isArray(item.source_quality_risk_flags) ? item.source_quality_risk_flags : [];
  for (const risk of risks.slice(0, 2)) chips.push({ text: readableFlag(risk), tone: "risk" });
  if (item.non_investment_advice) chips.push({ text: "仅作素材观察", tone: "soft" });
  return chips;
}

function render(feed, loadedFrom) {
  syncControls(feed);
  const recentItems = filterRecent(feed.items || [], feed.generated_at, state.recentHours);
  const sourceItems = recentItems.filter((item) => state.sourceFilter === "all" || sourceKey(item) === state.sourceFilter);
  renderLanguageRadar(sourceItems);

  const items = visibleItems(feed);
  state.renderedItems = items;
  renderStats(feed, items, loadedFrom);
  renderTelemetryPanels(items);

  document.getElementById("timeline").innerHTML = items
    .map((item, index) => {
      const title = readableTitle(item);
      const chips = itemChips(item);
      const tone = index % 2 ? "orange" : "green";
      return `
        <article class="timeline-card" data-testid="radar-card">
          ${imagePreview(item)}
          <div class="card-body">
            <div class="card-meta">
              <span>${String(index + 1).padStart(2, "0")}</span>
              <span>${escapeHtml(sourceDisplayName(item.source_label || item.source))}</span>
              <span>${escapeHtml(formatTime(item.published_at))}</span>
            </div>
            <h2>${escapeHtml(title)}</h2>
            <p>${escapeHtml(previewCopy(item.copy_text))}</p>
            <div class="chip-row">
              ${chips.map((chip) => `<span class="chip ${escapeHtml(chip.tone)}">${escapeHtml(chip.text)}</span>`).join("")}
            </div>
          </div>
          <div class="card-score">
            <span>可能爆分</span>
            <strong data-count="${score(item)}">${score(item)}</strong>
            ${lineSvg(itemSeries(item), "sparkline", tone)}
            <button class="detail-button" type="button" data-item-index="${index}">查看线索</button>
          </div>
        </article>
      `;
    })
    .join("") || `<div class="empty-state">当前没有线索。换个时间试试。</div>`;
  animateCounts();
  armMotion();
  flashUpdate();
  openHashItem();
}

function renderDialog(item) {
  const asset = bestAsset(item);
  const originalImageRef = firstOriginalImageRef(item);
  const metrics = item.engagement_snapshot?.metrics || {};
  const predictionScores = item.prediction_scores || {};
  const sourceRows = [
    ["来源", sourceDisplayName(item.source_label || item.source)],
    ["作者", item.author || ""],
    ["发布时间", formatTime(item.published_at)],
    ["原文链接", publicUrl(item.source_url || item.canonical_url)],
  ].filter(([, value]) => String(value || "").trim());
  const imageRows = [
    ["图片状态", imageLabel(item)],
    ["原图链接", originalImageRef?.original_url || originalImageRef?.image_url || originalImageRef?.url || ""],
    ["出现位置", originalImageRef?.context_position || originalImageRef?.context || ""],
    ["尺寸", asset?.dimensions?.width && asset?.dimensions?.height ? `${asset.dimensions.width}x${asset.dimensions.height}` : ""],
  ].filter(([, value]) => String(value || "").trim());
  const scoreRows = [
    ["可能爆分", score(item)],
    ["回头看", outcomeLabel(item)],
    ["猜对没", evalLabel(item)],
    ["可信状态", sourceQualityLabel(item)],
  ];
  const reasonRows = [
    ["15 分钟", predictionScores["15m"]],
    ["1 小时", predictionScores["1h"]],
    ["3 小时", predictionScores["3h"]],
    ["6 小时", predictionScores["6h"]],
    ["24 小时", predictionScores["24h"]],
  ].filter(([, value]) => Number.isFinite(Number(value)));
  const metricRows = Object.entries(metrics)
    .filter(([, value]) => Number.isFinite(Number(value)) && Number(value) > 0)
    .slice(0, 4);

  document.getElementById("dialogBody").innerHTML = `
    <article class="detail-view">
      <header class="detail-header">
        <span>${escapeHtml(sourceDisplayName(item.source_label || item.source))}</span>
        <h2>${escapeHtml(readableTitle(item))}</h2>
        <p>${escapeHtml(item.copy_text || "")}</p>
      </header>
      <section class="detail-grid">
        <div>
          ${imagePreview(item)}
          ${publicUrl(item.source_url) ? `<a class="source-link" href="${escapeHtml(publicUrl(item.source_url))}" target="_blank" rel="noreferrer">打开原文</a>` : ""}
          <section class="reader-section">
            <h3>这条为什么值得看</h3>
            <div class="chip-row detail-chips">
              ${itemChips(item).map((chip) => `<span class="chip ${escapeHtml(chip.tone)}">${escapeHtml(chip.text)}</span>`).join("")}
            </div>
          </section>
          <dl class="evidence-list">
            ${sourceRows.map(([label, value]) => `
              <div>
                <dt>${escapeHtml(label)}</dt>
                <dd>${escapeHtml(value)}</dd>
              </div>
            `).join("")}
          </dl>
        </div>
        <aside>
          <div class="score-panel">
            <span>可能爆分</span>
            <strong>${score(item)}</strong>
            ${lineSvg(itemSeries(item), "sparkline", "orange")}
          </div>
          <dl class="evidence-list compact">
            ${scoreRows.map(([label, value]) => `
              <div>
                <dt>${escapeHtml(label)}</dt>
                <dd>${escapeHtml(value)}</dd>
              </div>
            `).join("")}
          </dl>
          ${reasonRows.length ? `
            <section class="reader-section side">
              <h3>什么时候看结果</h3>
              <dl class="evidence-list compact">
                ${reasonRows.map(([label, value]) => `
                  <div>
                    <dt>${escapeHtml(label)}</dt>
                    <dd>${Math.round(Number(value || 0) * 100)}</dd>
                  </div>
                `).join("")}
              </dl>
            </section>
          ` : ""}
          <section class="reader-section side">
            <h3>图片与来源</h3>
            <dl class="evidence-list compact">
              ${imageRows.map(([label, value]) => `
                <div>
                  <dt>${escapeHtml(label)}</dt>
                  <dd>${escapeHtml(value)}</dd>
                </div>
              `).join("") || "<div><dt>图片</dt><dd>这条没有可展示的图片信息</dd></div>"}
            </dl>
          </section>
          ${metricRows.length ? `
            <section class="reader-section side">
              <h3>公开互动</h3>
              <dl class="evidence-list compact">
                ${metricRows.map(([label, value]) => `
                  <div>
                    <dt>${escapeHtml(metricLabel(label))}</dt>
                    <dd>${escapeHtml(value)}</dd>
                  </div>
                `).join("")}
              </dl>
            </section>
          ` : ""}
        </aside>
      </section>
    </article>
  `;
  document.getElementById("itemDialog").showModal();
}

function detailHash(item) {
  return `item=${encodeURIComponent(item.id || "")}`;
}

function itemIdFromHash() {
  const hash = window.location.hash.replace(/^#/, "");
  const params = new URLSearchParams(hash);
  return params.get("item") || "";
}

function openHashItem() {
  const itemId = itemIdFromHash();
  if (!itemId) return;
  const item = (state.feed.items || []).find((candidate) => String(candidate.id || "") === itemId);
  const dialog = document.getElementById("itemDialog");
  if (item && !dialog.open) {
    state.restoringHash = true;
    renderDialog(item);
    state.restoringHash = false;
  }
}

async function refreshFeed() {
  const { feed, loadedFrom } = await loadFeed();
  state.feed = feed;
  state.loadedFrom = loadedFrom;
  render(feed, loadedFrom);
  const refresh = feed.auto_refresh || {};
  if (state.refreshTimer) window.clearInterval(state.refreshTimer);
  if (refresh.enabled !== false) {
    const seconds = Math.max(15, Number(refresh.poll_interval_seconds || 60));
    state.refreshTimer = window.setInterval(refreshFeed, seconds * 1000);
  }
}

document.getElementById("recentHours").addEventListener("change", (event) => {
  state.recentHours = Number(event.target.value || 120);
  render(state.feed, state.loadedFrom);
});

document.getElementById("sortMode").addEventListener("change", (event) => {
  state.sortMode = event.target.value || "hotness";
  render(state.feed, state.loadedFrom);
});

document.getElementById("sourceFilter").addEventListener("change", (event) => {
  state.sourceFilter = event.target.value || "all";
  render(state.feed, state.loadedFrom);
});

document.getElementById("languageFilter").addEventListener("change", (event) => {
  state.languageFilter = event.target.value || "all";
  render(state.feed, state.loadedFrom);
});

document.getElementById("languageRadar").addEventListener("click", (event) => {
  const button = event.target.closest(".language-button");
  if (!button) return;
  state.languageFilter = button.dataset.language || "all";
  document.getElementById("languageFilter").value = state.languageFilter;
  render(state.feed, state.loadedFrom);
});

document.getElementById("timeline").addEventListener("click", (event) => {
  const button = event.target.closest(".detail-button");
  if (!button) return;
  const item = state.renderedItems[Number(button.dataset.itemIndex)];
  if (!item) return;
  if (window.location.hash !== `#${detailHash(item)}`) {
    window.history.pushState(null, "", `#${detailHash(item)}`);
  }
  renderDialog(item);
});

document.getElementById("dialogClose").addEventListener("click", () => {
  document.getElementById("itemDialog").close();
  if (!state.restoringHash && itemIdFromHash()) {
    window.history.pushState(null, "", window.location.pathname + window.location.search);
  }
});

document.getElementById("itemDialog").addEventListener("close", () => {
  if (!state.restoringHash && itemIdFromHash()) {
    window.history.pushState(null, "", window.location.pathname + window.location.search);
  }
});

window.addEventListener("hashchange", () => {
  const dialog = document.getElementById("itemDialog");
  if (itemIdFromHash()) {
    openHashItem();
  } else if (dialog.open) {
    dialog.close();
  }
});

refreshFeed();
