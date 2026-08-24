#!/usr/bin/env node
/**
 * Headless Xueqiu targeted stock-discussion export.
 *
 * Fallback when opencli Browser Bridge is unavailable. Uses Playwright with
 * a persistent storage state (login cookies) to call the stock-discussion
 * API directly from within a browser context.
 *
 * Usage:
 *   node scripts/xueqiu_targeted_export.mjs --symbol SH600519 --limit 20 --out /tmp/export.json
 *
 * Environment:
 *   NEWS_HARNESS_XUEQIU_STORAGE_STATE_FILE  Path to Playwright storage state JSON
 */

import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";

function arg(name, fallback = null) {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1] ? process.argv[index + 1] : fallback;
}

function fail(code, message) {
  process.stdout.write(JSON.stringify({ code, message }) + "\n");
  process.exit(1);
}

const symbol = arg("--symbol", "");
const limit = Math.min(100, Math.max(1, Number(arg("--limit", "20"))));
const minComments = Math.max(0, Number(arg("--min-comments", "10")));
const maxAgeHours = Math.max(0, Number(arg("--max-age-hours", "48")));
const out = arg("--out");
const storageStatePath = arg("--storage-state") || process.env.NEWS_HARNESS_XUEQIU_STORAGE_STATE_FILE;

if (!symbol || !/^[A-Z]{2}\d{5,6}$/.test(symbol)) {
  fail("invalid_symbol", `Expected symbol like SH600519, got: ${symbol}`);
}
if (!out) fail("missing_out", "--out is required");

let chromium;
try {
  ({ chromium } = await import("playwright"));
} catch {
  try {
    ({ chromium } = await import("playwright-core"));
  } catch {
    fail("playwright_unavailable", "Playwright is required for headless xueqiu export");
  }
}

async function firstExisting(paths) {
  for (const p of paths) {
    try { await fs.access(p); return p; } catch {}
  }
  return undefined;
}

const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
  || await firstExisting(["/usr/bin/chromium-browser", "/usr/bin/chromium", "/usr/bin/google-chrome"]);

const launchOptions = {
  headless: true,
  args: ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
};
if (executablePath) launchOptions.executablePath = executablePath;

const browser = await chromium.launch(launchOptions);

try {
  const contextOptions = {
    locale: "zh-CN",
    userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36",
  };
  if (storageStatePath) {
    try {
      await fs.access(storageStatePath);
      contextOptions.storageState = storageStatePath;
    } catch {
      fail("storage_state_unreadable", `Configured storage state is not readable: ${storageStatePath}`);
    }
  }
  const context = await browser.newContext(contextOptions);
  await context.addInitScript("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})");
  const page = await context.newPage();

  // Navigate to Xueqiu homepage to establish cookies
  await page.goto("https://xueqiu.com/", { waitUntil: "domcontentloaded", timeout: 15000 });
  await page.waitForTimeout(1500);

  // Check for auth challenge
  const bodyText = await page.locator("body").innerText({ timeout: 3000 }).catch(() => "");
  if (/验证码|安全验证|访问受限|滑块|captcha|challenge/i.test(bodyText)) {
    fail("auth_or_challenge_required", "Xueqiu page indicates auth/challenge state");
  }

  // Fetch discussions via the symbol API from within the page context
  const rows = [];
  let rawRowCount = 0;
  let thresholdPassCount = 0;
  let pageNum = 1;
  const pageSize = Math.min(limit, 20);

  while (rows.length < limit && pageNum <= 3) {
    const apiUrl = new URL("https://xueqiu.com/query/v1/symbol/search/status");
    apiUrl.searchParams.set("symbol", symbol);
    apiUrl.searchParams.set("count", String(pageSize));
    apiUrl.searchParams.set("page", String(pageNum));
    apiUrl.searchParams.set("sort", "time");

    const response = await page.evaluate(async (url) => {
      try {
        const r = await fetch(url, {
          credentials: "include",
          headers: {
            "accept": "application/json, text/plain, */*",
            "x-requested-with": "XMLHttpRequest",
          },
          referrer: `https://xueqiu.com/S/${new URL(url).searchParams.get("symbol")}`,
        });
        const contentType = r.headers.get("content-type") || "";
        const text = await r.text();
        if (!contentType.includes("application/json")) return { error: "non_json_response", status: r.status, snippet: text.slice(0, 500) };
        return { status: r.status, json: JSON.parse(text) };
      } catch (e) {
        return { error: "fetch_exception", status: 0, snippet: e.message };
      }
    }, apiUrl.toString());

    if (response.error === "non_json_response" && /captcha|challenge|waf/i.test(response.snippet || "")) {
      fail("auth_or_challenge_required", `Anti-bot detected on page ${pageNum}`);
    }
    if (response.status === 401 || response.status === 403) {
      fail("auth_required", `HTTP ${response.status}: login cookies may be expired`);
    }
    if (response.error || response.status !== 200) {
      fail(
        response.error || "http_error",
        `page=${pageNum} status=${response.status} ${(response.snippet || "").slice(0, 200)}`,
      );
    }

    const list = response.json?.list || response.json?.data?.list || [];
    if (!list.length) break;
    rawRowCount += list.length;

    for (const item of list) {
      if (rows.length >= limit) break;
      const replyCount = Number(item.reply_count || 0);
      if (replyCount < minComments) continue;
      const createdAtRaw = Number(item.created_at || item.createdAt || 0);
      const createdAtMs = createdAtRaw > 1e12 ? createdAtRaw : createdAtRaw * 1000;
      if (maxAgeHours > 0 && createdAtMs > 0 && Date.now() - createdAtMs > maxAgeHours * 3600 * 1000) continue;
      thresholdPassCount += 1;

      const detailResponse = await page.evaluate(async (statusId) => {
        try {
          const url = new URL("https://xueqiu.com/statuses/show.json");
          url.searchParams.set("id", statusId);
          const r = await fetch(url, {
            credentials: "include",
            headers: { "accept": "application/json, text/plain, */*", "x-requested-with": "XMLHttpRequest" },
          });
          const text = await r.text();
          return { status: r.status, text };
        } catch (e) {
          return { status: 0, error: "detail_fetch_exception", text: e.message };
        }
      }, String(item.id || ""));
      if (detailResponse.status === 401 || detailResponse.status === 403) {
        fail("auth_required", `Detail HTTP ${detailResponse.status} for status ${item.id}`);
      }
      if (detailResponse.error || detailResponse.status !== 200) {
        fail(detailResponse.error || "detail_http_error", `status=${detailResponse.status} id=${item.id}`);
      }
      let detail;
      try {
        detail = JSON.parse(detailResponse.text);
      } catch {
        fail("detail_non_json_response", `Cannot parse detail for status ${item.id}`);
      }
      if (detail?.error_code || (detail?.errcode && String(detail.errcode) !== "0")) {
        fail("detail_business_error", `Detail API rejected status ${item.id}`);
      }

      const full = detail?.status || detail?.data || detail || item;
      let fullText = String(full.description || full.text || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
      let detailStatus = "api_full_text_observed";
      // Long posts are truncated by the list/show APIs (they end with an
      // ellipsis, or just stop mid-sentence). Open the canonical post page in
      // the same logged-in context and read the rendered article body so the
      // export keeps the full text.
      const user = full.user || item.user || {};
      const looksTruncated = /\.{3,}|…$|展开全文|阅读全文|查看全文/.test(fullText) || /[，,：:、]$/.test(fullText) || fullText.length < 220;
      if (looksTruncated) {
        const postUrl = user.id && item.id ? `https://xueqiu.com/${user.id}/${item.id}` : "";
        if (postUrl) {
          await page.goto(postUrl, { waitUntil: "domcontentloaded", timeout: 20000 });
          await page.waitForTimeout(800 + Math.floor(Math.random() * 700));
          const articleText = await page.evaluate(() => {
            const candidates = [
              document.querySelector(".article__bd__detail"),
              document.querySelector(".article__bd"),
              document.querySelector(".status-content"),
            ].filter(Boolean);
            const pick = (el) => (el ? el.innerText || "" : "");
            const texts = candidates.map(pick);
            const imageText = candidates.flatMap((el) =>
              [...el.querySelectorAll("img")]
                .map((img) => img.getAttribute("alt") || img.getAttribute("title") || "")
                .filter((t) => t && !/^\[[^\]]{1,10}\]$/.test(t.trim())),
            );
            return [...texts, ...imageText].join("\n").trim();
          });
          const cleaned = String(articleText || "")
            .split("\n")
            .map((line) => line.trim())
            .filter((line) => !/^(分享到微信|微信分享|微信|朋友圈|雪球|descrption fold|展开全文|查看全文|阅读全文)$/.test(line))
            .join("\n")
            .replace(/\[[^\]]{1,10}\]/g, " ")
            .replace(/\s+/g, " ")
            .trim();
          if (cleaned.length > fullText.length) {
            fullText = cleaned;
            detailStatus = "page_full_text_observed";
          }
          await page.goBack({ waitUntil: "domcontentloaded", timeout: 15000 }).catch(() => {});
          await page.waitForTimeout(600);
        }
      }
      if (!fullText) fail("detail_text_missing", `Detail API returned no full text for status ${item.id}`);
      const images = [];
      for (const field of ["firstImg", "cover_pic"]) {
        const val = full[field] || item[field];
        if (typeof val === "string" && val.startsWith("http")) {
          images.push(...val.split(",").map(u => u.trim()).filter(u => u.startsWith("http")));
        }
      }
      const imageInfoList = full.image_info_list || item.image_info_list;
      if (Array.isArray(imageInfoList)) {
        for (const img of imageInfoList) {
          const url = img?.originUrl || img?.url;
          if (url && url.startsWith("http")) images.push(url);
        }
      }
      rows.push({
        id: String(item.id || ""),
        user_id: String(user.id || ""),
        text: fullText,
        created_at: full.created_at || item.created_at,
        reply_count: Number(full.reply_count ?? replyCount),
        fav_count: Number(full.fav_count ?? item.fav_count ?? 0),
        retweet_count: Number(full.retweet_count ?? item.retweet_count ?? 0),
        author: String(user.screen_name || ""),
        url: user.id && item.id ? `https://xueqiu.com/${user.id}/${item.id}` : "",
        images,
        detail_fetch_status: detailStatus,
        full_text_status: detailStatus === "page_full_text_observed" ? "page_full_text_observed" : "full_text_observed",
      });
    }

    if (list.length < pageSize) break;
    pageNum += 1;
    await page.waitForTimeout(1500 + Math.floor(Math.random() * 2000));
  }

  await fs.mkdir(path.dirname(out), { recursive: true });
  await fs.writeFile(out, JSON.stringify({
    export_schema_version: "xueqiu_targeted_headless.v1",
    symbol,
    fetched_at: new Date().toISOString(),
    collection: {
      raw_row_count: rawRowCount,
      threshold_pass_count: thresholdPassCount,
      comment_threshold: minComments,
      max_age_hours: maxAgeHours,
    },
    rows,
  }, null, 2));

  process.stdout.write(JSON.stringify({ status: "ok", symbol, rows: rows.length }) + "\n");
} finally {
  await browser.close().catch(() => {});
}
