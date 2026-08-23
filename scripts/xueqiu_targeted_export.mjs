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
    try { await fs.access(storageStatePath); contextOptions.storageState = storageStatePath; } catch {}
  }
  const context = await browser.newContext(contextOptions);
  await context.addInitScript("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})");
  const page = await context.newPage();

  // Navigate to Xueqiu homepage to establish cookies
  await page.goto("https://xueqiu.com/", { waitUntil: "domcontentloaded", timeout: 15000 });
  await page.waitForTimeout(1500);

  // Check for auth challenge
  const bodyText = await page.locator("body").innerText({ timeout: 3000 }).catch(() => "");
  if (/登录|验证码|安全验证|访问受限|滑块|captcha|challenge/i.test(bodyText)) {
    fail("auth_or_challenge_required", "Xueqiu page indicates auth/challenge state");
  }

  // Fetch discussions via the symbol API from within the page context
  const rows = [];
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
      break; // Stop pagination but keep what we have
    }

    const list = response.json?.list || response.json?.data?.list || [];
    if (!list.length) break;

    for (const item of list) {
      if (rows.length >= limit) break;
      const user = item.user || {};
      const images = [];
      for (const field of ["firstImg", "cover_pic"]) {
        const val = item[field];
        if (typeof val === "string" && val.startsWith("http")) {
          images.push(...val.split(",").map(u => u.trim()).filter(u => u.startsWith("http")));
        }
      }
      if (Array.isArray(item.image_info_list)) {
        for (const img of item.image_info_list) {
          const url = img?.url || img?.originUrl;
          if (url && url.startsWith("http")) images.push(url);
        }
      }
      rows.push({
        id: String(item.id || ""),
        user_id: String(user.id || ""),
        text: String(item.description || item.text || "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim(),
        created_at: item.created_at,
        reply_count: Number(item.reply_count || 0),
        fav_count: Number(item.fav_count || 0),
        retweet_count: Number(item.retweet_count || 0),
        author: String(user.screen_name || ""),
        url: user.id && item.id ? `https://xueqiu.com/${user.id}/${item.id}` : "",
        images,
      });
    }

    if (list.length < pageSize) break;
    pageNum += 1;
    await page.waitForTimeout(1500 + Math.floor(Math.random() * 2000));
  }

  await fs.mkdir(require("path").dirname(out), { recursive: true });
  await fs.writeFile(out, JSON.stringify({
    export_schema_version: "xueqiu_targeted_headless.v1",
    symbol,
    fetched_at: new Date().toISOString(),
    rows,
  }, null, 2));

  process.stdout.write(JSON.stringify({ status: "ok", symbol, rows: rows.length }) + "\n");
} finally {
  await browser.close().catch(() => {});
}
