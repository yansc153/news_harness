#!/usr/bin/env node
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";

async function jsonRows(page, maxRows) {
  const items = [];
  let maxId = "-1";
  // Xueqiu 热门不会因刷新或 tab 切换更新；真实分页来自持续下滑。
  for (let i = 0; i < 6; i += 1) {
    await page.mouse.wheel(0, 1100);
    await page.waitForTimeout(500);
  }
  for (let p = 0; p < 5 && items.length < maxRows * 4; p += 1) {
    const url = "https://xueqiu.com/statuses/hot/list.json?since_id=-1&max_id=" + maxId + "&size=20";
    try {
      const result = await page.evaluate(async (fetchUrl) => {
        const r = await fetch(fetchUrl, { credentials: "include", headers: { Referer: "https://xueqiu.com/" } });
        if (!r.ok) return { error: "status_" + r.status };
        return await r.json();
      }, url);
      if (result.error) break;
      const pageItems = Array.isArray(result?.items) ? result.items : [];
      items.push(...pageItems);
      maxId = String(result?.next_max_id || "");
      if (!pageItems.length || !maxId || maxId === "-1") break;
    } catch { break; }
  }
  const rows = items.flatMap((item) => {
    const status = item?.original_status || item?.status || item;
    if (!status?.id || !status?.user_id) return [];
    const user = status.user || {};
    const verifiedInfos = Array.isArray(user.verified_infos) ? user.verified_infos : [];
    const isDaren = verifiedInfos.some((info) => String(info?.verified_type) === "10" || String(info?.verified_desc || "").includes("创作者"));
    if (source === "xueqiu_daren" && !isDaren) return [];
    const images = [];
    for (const value of [status.firstImg, status.cover_pic, status.pic]) {
      if (typeof value === "string") {
        for (const url of value.split(",")) images.push({ url: url.trim() });
      }
    }
    for (const image of Array.isArray(status.image_info_list) ? status.image_info_list : []) {
      const url = image?.url || image?.originUrl || image?.thumbnailUrl;
      if (url) images.push({ url, width: image?.width, height: image?.height });
    }
    const description = stripHtml(status.description || status.text || "");
    return [{
      title: cleanText(status.title || ""),
      text: description,
      url: `https://xueqiu.com/${status.user_id}/${status.id}`,
      author: user.screen_name || "",
      published_at: status.created_at ? new Date(Number(status.created_at)).toISOString() : "",
      images: uniqImages(images).filter((image) => /^https?:/.test(image.url)),
      section_label: SECTIONS[source],
      candidate_source: "hot_list_json",
    }];
  });
  return uniqRows(rows).filter((row) => row.text.length >= 20).slice(0, maxRows);
}

const SECTIONS = {
  xueqiu_hot: "最新",
  xueqiu_daren: "达人",
};
const MIN_CONFIRMED_XUEQIU_ROWS = 5;

function arg(name, fallback = null) {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

function fail(code, message) {
  process.stdout.write(JSON.stringify({ code, message }) + "\n");
  process.exit(1);
}

const source = arg("--source", "");
const limit = Math.min(20, Number(arg("--limit", "10")));
const out = arg("--out");
const storageState = arg("--storage-state");
const detailDelayMinMs = Math.max(0, Number(process.env.NEWS_HARNESS_XUEQIU_DETAIL_DELAY_MIN_MS || arg("--detail-delay-min-ms", "1800")));
const detailDelayMaxMs = Math.max(detailDelayMinMs, Number(process.env.NEWS_HARNESS_XUEQIU_DETAIL_DELAY_MAX_MS || arg("--detail-delay-max-ms", "4600")));
const pageSettleMinMs = Math.max(0, Number(process.env.NEWS_HARNESS_XUEQIU_PAGE_SETTLE_MIN_MS || arg("--page-settle-min-ms", "2200")));
const pageSettleMaxMs = Math.max(pageSettleMinMs, Number(process.env.NEWS_HARNESS_XUEQIU_PAGE_SETTLE_MAX_MS || arg("--page-settle-max-ms", "5200")));
if (!SECTIONS[source]) fail("xueqiu_section_backend_unsupported", `No exact read-only headless DOM section for ${source}`);
if (!out) fail("xueqiu_headless_export_missing_out", "--out is required");

let chromium;
try {
  ({ chromium } = await import("playwright"));
} catch {
  try {
    ({ chromium } = await import("playwright-core"));
  } catch {
    fail("playwright_unavailable", "Playwright is required for Xueqiu headless export");
  }
}

async function firstExisting(paths) {
  for (const candidate of paths) {
    try {
      await fs.access(candidate);
      return candidate;
    } catch {}
  }
  return undefined;
}

function uniqRows(rows) {
  const seen = new Set();
  return rows.filter((row) => {
    const key = row.url || row.text;
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function cleanText(text) {
  return String(text || "").replace(/\s+/g, " ").trim();
}

function stripHtml(text) {
  return cleanText(String(text || "").replace(/<[^>]+>/g, " "));
}

function randomInt(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

async function humanDelay(minMs, maxMs) {
  if (maxMs <= 0) return;
  await new Promise((resolve) => setTimeout(resolve, randomInt(minMs, maxMs)));
}

function looksBlocked(text) {
  const lowered = cleanText(text).toLowerCase();
  if (/access verification|slide to complete|slide to verify|traceid|captcha|challenge|risk|waf|verify|验证码|访问受限|访问验证|安全验证|滑动验证|请按住滑块/i.test(lowered)) return true;
  return /login|请登录|登录后|下载app 关于雪球/i.test(lowered) && lowered.length < 800;
}

function looksTruncated(text) {
  const cleaned = cleanText(text);
  if (!cleaned) return true;
  return /(\.{3,}|…|展开全文|阅读全文|查看全文)\s*$/.test(cleaned);
}

function hasConfirmedDetailText(detailText, listText) {
  const detail = cleanText(detailText);
  const list = cleanText(listText);
  if (!detail || looksBlocked(detail)) return false;
  if (looksTruncated(detail)) return false;
  if (detail.length >= list.length + 20) return true;
  if (detail.length >= 120 && !/[.。…]\s*展开/.test(detail.slice(-80))) return true;
  return detail.length >= 60 && list.length < 80;
}

function uniqImages(images) {
  const seen = new Set();
  return (images || []).filter((image) => {
    const url = String(image?.url || "");
    if (!url || seen.has(url)) return false;
    seen.add(url);
    return true;
  });
}


async function sectionRows(page, label, maxRows) {
  await page.goto("https://xueqiu.com/", { waitUntil: "domcontentloaded", timeout: 15000 });
  await page.waitForTimeout(1500);
  // D-15：雪球「最新」必须显式点击 tab 才能加载（非网页刷新 / 非热门 API）。
  // 2026-07-22: 如果已在该 tab 上，再次点击不会刷新。先切到"推荐"tab 再切回目标 tab 强制刷新。
  const otherTab = label === "最新" ? "推荐" : "最新";
  await page.getByText(otherTab, { exact: false }).first().click({ timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(1000);
  await page.getByText(label, { exact: false }).first().click({ timeout: 8000 }).catch(() => {});
  await page.waitForTimeout(2500);
  const rows = await page.evaluate((maxRows) => {
    const abs = (url) => {
      try {
        return new URL(url, location.href).href;
      } catch {
        return "";
      }
    };
    const images = (root) => Array.from(root.querySelectorAll("img"))
      .map((img) => ({ url: img.currentSrc || img.src, width: img.naturalWidth || undefined, height: img.naturalHeight || undefined }))
      .filter((img) => /^https?:/.test(img.url));
    const statusUrl = (root) => {
      for (const a of root.querySelectorAll("a[href]")) {
        const href = abs(a.getAttribute("href"));
        if (/xueqiu\.com\/\d+\/\d+/.test(href) || /\/status(es)?\//.test(href)) return href;
      }
      return "";
    };
    const pickRoot = (node) => {
      let root = node;
      for (let i = 0; i < 6 && root.parentElement; i += 1) {
        root = root.parentElement;
        if ((root.innerText || "").trim().length > 40) return root;
      }
      return node;
    };
    const candidates = Array.from(document.querySelectorAll("a[href]")).map((a) => pickRoot(a));
    return candidates.map((root) => ({
      title: "",
      text: (root.innerText || "").replace(/\s+/g, " ").trim(),
      url: statusUrl(root),
      images: images(root),
      section_label: "",
    })).filter((row) => row.text.length >= 20 && row.url).slice(0, maxRows * 2);
  }, maxRows * 4);
  return uniqRows(rows)
    .filter((row) => !looksBlocked(row.text))
    .map((row) => ({ ...row, section_label: label, candidate_source: "homepage_dom" }))
    .slice(0, maxRows);
}

async function detailRow(context, row) {
  if (!row.url) return { ...row, detail_fetch_status: "detail_url_missing" };
  let statusId = "";
  try {
    const parts = new URL(row.url).pathname.split("/").filter(Boolean);
    statusId = parts.at(-1) || "";
  } catch {}
  if (statusId) {
    const apiUrl = `https://xueqiu.com/statuses/show.json?id=${statusId}`;
    const response = await context.request.get(apiUrl, { headers: { Referer: row.url }, timeout: 6000 }).catch(() => null);
    if (response?.ok()) {
      const status = await response.json().catch(() => null);
      const user = status?.user || {};
      const images = [];
      for (const value of [status?.firstImg, status?.cover_pic, status?.pic]) {
        if (typeof value === "string") {
          for (const url of value.split(",")) images.push({ url: url.trim() });
        }
      }
      for (const image of Array.isArray(status?.image_info_list) ? status.image_info_list : []) {
        const url = image?.url || image?.originUrl || image?.thumbnailUrl;
        if (url) images.push({ url, width: image?.width, height: image?.height });
      }
      const apiText = stripHtml(status?.text || status?.description || "");
      if (hasConfirmedDetailText(apiText, row.text)) {
        return {
          ...row,
          title: cleanText(status?.title || row.title || ""),
          author: user.screen_name || row.author || "",
          published_at: status?.created_at ? new Date(Number(status.created_at)).toISOString() : row.published_at,
          text: cleanText([status?.title || row.title, apiText].filter(Boolean).join("\n")),
          images: uniqImages([...(row.images || []), ...images]).filter((image) => /^https?:/.test(image.url)),
          detail_fetch_status: "api_full_text_observed",
          full_text_observed: true,
        };
      }
    }
    if (row.candidate_source === "hot_list_json") {
      return {
        ...row,
        detail_fetch_status: response ? "api_detail_incomplete" : "api_detail_unavailable",
        full_text_observed: false,
      };
    }
  }
  const page = await context.newPage();
  try {
    await page.goto(row.url, { waitUntil: "domcontentloaded", timeout: 20000 });
    await page.waitForSelector(".article__bd__detail, article.article__bd, article, main, [class*=detail]", { timeout: 7000 }).catch(() => {});
    await humanDelay(pageSettleMinMs, pageSettleMaxMs);
    const detail = await page.evaluate((listText) => {
      const clean = (text) => String(text || "").replace(/\s+/g, " ").trim();
      const preferred = document.querySelector(".article__bd__detail, article.article__bd");
      const roots = Array.from(document.querySelectorAll("article, main, [class*=article], [class*=status], [class*=detail], [class*=content]"))
        .filter((root) => clean(root.innerText).length >= 40);
      const snippet = clean(listText).slice(0, 40);
      const matching = roots.filter((root) => !snippet || clean(root.innerText).includes(snippet));
      const root = preferred || (matching.length ? matching : roots).sort((a, b) => clean(b.innerText).length - clean(a.innerText).length)[0] || document.body;
      return {
        text: clean(root.innerText),
        images: Array.from(root.querySelectorAll("img"))
          .map((img) => ({ url: img.currentSrc || img.src, width: img.naturalWidth || undefined, height: img.naturalHeight || undefined }))
          .filter((img) => /^https?:/.test(img.url)),
      };
    }, row.text);
    if (looksBlocked(detail.text)) {
      return { ...row, detail_fetch_status: "auth_or_challenge_required", full_text_observed: false };
    }
    if (!hasConfirmedDetailText(detail.text, row.text)) {
      return {
        ...row,
        detail_text_length: cleanText(detail.text).length,
        list_text_length: cleanText(row.text).length,
        detail_fetch_status: "detail_text_incomplete",
        full_text_observed: false,
      };
    }
    return {
      ...row,
      text: cleanText([row.title, detail.text].filter(Boolean).join("\n")),
      images: uniqImages([...(row.images || []), ...(detail.images || [])]),
      detail_fetch_status: "full_text_observed",
      full_text_observed: true,
    };
  } catch {
    return { ...row, detail_fetch_status: "detail_attempt_failed" };
  } finally {
    await page.close().catch(() => {});
  }
}

const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
  || await firstExisting(["/usr/bin/chromium-browser", "/usr/bin/chromium", "/usr/bin/google-chrome"]);
const browser = await chromium.launch({
  headless: true,
  executablePath,
  args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
});
try {
  const context = await browser.newContext({
    ...(storageState ? { storageState } : {}),
    locale: "zh-CN",
    userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
  });
  await context.addInitScript("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})");
  const page = await context.newPage();
  const candidateLimit = Math.max(limit * 8, limit);
  // D-15：v2 以「最新」为准 → 优先点击「最新」tab 取 DOM 行；hot list JSON 仅作兜底。
  // 2026-07-22 fix: xueqiu_hot prefer hot/list.json API over DOM scraping
  // DOM-based sectionRows returns stale content (July 21 posts even for "latest" tab)
  let rows;
  if (source === "xueqiu_hot") {
    rows = await jsonRows(page, candidateLimit);
    if (!rows.length) rows = await sectionRows(page, SECTIONS[source], candidateLimit);
  } else {
    rows = await sectionRows(page, SECTIONS[source], candidateLimit);
    if (!rows.length) rows = await jsonRows(page, candidateLimit);
  }
  if (!rows.length) {
    const body = await page.locator("body").innerText({ timeout: 3000 }).catch(() => "");
    if (/登录|验证码|安全|访问受限|访问验证|滑块|risk|captcha/i.test(body)) fail("auth_or_challenge_required", "Xueqiu page indicates auth/challenge/risk-control state");
    fail("xueqiu_section_no_rows", `Xueqiu ${source} section produced no post rows`);
  }
  const detailed = [];
  const rejected = [];
  for (const row of rows.slice(0, Math.min(rows.length, limit * 3))) {
    await humanDelay(detailDelayMinMs, detailDelayMaxMs);
    const detail = await detailRow(context, row);
    if (detail.full_text_observed && !looksTruncated(detail.text)) {
      detailed.push(detail);
    } else {
      rejected.push(detail);
    }
    if (detailed.length >= limit) break;
  }
  const outputRows = detailed.length ? detailed : [];
  if (!outputRows.length && rejected.some((row) => row.detail_fetch_status === "auth_or_challenge_required")) {
    fail("auth_or_challenge_required", "Xueqiu detail pages hit auth/challenge before full text");
  }
  if (!outputRows.length) {
    fail("xueqiu_detail_required", "Xueqiu rows did not confirm second-level full text");
  }
  if (outputRows.length < MIN_CONFIRMED_XUEQIU_ROWS) {
    fail(
      "xueqiu_min_rows_not_met",
      `Xueqiu ${source} confirmed ${outputRows.length} full-text rows; minimum is ${MIN_CONFIRMED_XUEQIU_ROWS}`,
    );
  }
  await fs.mkdir(path.dirname(out), { recursive: true });
  await fs.writeFile(out, JSON.stringify({
    export_schema_version: "xueqiu_headless_dom.v1",
    source_url: "https://xueqiu.com/",
    sources: { [source]: outputRows },
  }, null, 2));
  process.stdout.write(JSON.stringify({ status: "ok", source, rows: outputRows.length, full_text_rows: detailed.length }) + "\n");
} finally {
  await browser.close().catch(() => {});
}
