#!/usr/bin/env node
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

const subreddits = String(arg("--subreddits", ""))
  .split(",")
  .map((item) => item.trim())
  .filter(Boolean);
const limit = Math.min(30, Number(arg("--limit", "10")));
const perSubreddit = Math.max(1, Math.min(30, Number(arg("--per-subreddit", "3"))));
const cookieFile = arg("--cookie-file");
const out = arg("--out");
if (!subreddits.length) fail("reddit_headless_no_subreddits", "--subreddits is required");
if (!cookieFile) fail("secret_env_missing", "--cookie-file is required");
if (!out) fail("reddit_headless_export_missing_out", "--out is required");

let chromium;
try {
  ({ chromium } = await import("playwright"));
} catch {
  try {
    ({ chromium } = await import("playwright-core"));
  } catch {
    fail("playwright_unavailable", "Playwright is required for Reddit headless export");
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

async function cookiesFromFile(file) {
  const raw = await fs.readFile(file, "utf8");
  return raw
    .split(";")
    .map((part) => part.trim())
    .filter(Boolean)
    .flatMap((part) => {
      const eq = part.indexOf("=");
      if (eq <= 0) return [];
      return [{
        name: part.slice(0, eq).trim(),
        value: part.slice(eq + 1).trim(),
        url: "https://www.reddit.com",
      }];
    });
}

function uniqByUrl(rows) {
  const seen = new Set();
  return rows.filter((row) => {
    if (!row.url || seen.has(row.url)) return false;
    seen.add(row.url);
    return true;
  });
}

async function collectCandidates(page, subreddit) {
  const rows = [];
  for (let attempt = 0; attempt < 5 && rows.length < perSubreddit; attempt += 1) {
    const batch = await page.evaluate((name) => {
      const abs = (href) => {
        try {
          return new URL(href, location.href).href;
        } catch {
          return "";
        }
      };
      const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
      const badTitle = /^(comment|comments|share|award|reply|save|hide|report|view discussions|查看|评论|回复|分享|保存)$/i;
      return Array.from(document.querySelectorAll('a[href*="/comments/"]'))
        .map((anchor) => {
          const url = abs(anchor.getAttribute("href") || "");
          const title = clean(anchor.innerText || anchor.getAttribute("aria-label") || "");
          const match = url.match(/https:\/\/www\.reddit\.com\/r\/([^/]+)\/comments\/([^/?#]+)/i);
          return { url: match ? match[0] + "/" : url, title, subreddit: name };
        })
        .filter((row) =>
          row.url.includes(`/r/${name}/comments/`) &&
          row.title.length >= 8 &&
          !badTitle.test(row.title)
        );
    }, subreddit);
    rows.push(...batch);
    await page.mouse.wheel(0, 1400).catch(() => {});
    await page.waitForTimeout(900);
  }
  return uniqByUrl(rows).slice(0, perSubreddit);
}

async function fetchDetail(context, candidate) {
  const page = await context.newPage();
  try {
    const response = await page.goto(candidate.url, { waitUntil: "domcontentloaded", timeout: 20000 }).catch(() => null);
    await page.waitForLoadState("networkidle", { timeout: 7000 }).catch(() => {});
    await page.waitForTimeout(1500);
    const detail = await page.evaluate((fallback) => {
      const clean = (value) => String(value || "").replace(/\r/g, "").replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
      const post = document.querySelector("shreddit-post") || document.querySelector("article") || document.body;
      const textOf = (selector, root = post) => clean(Array.from(root.querySelectorAll(selector))
        .map((node) => node.innerText || node.textContent || "")
        .filter(Boolean)
        .join("\n\n"));
      const attr = (selector, name) => document.querySelector(selector)?.getAttribute(name) || "";
      const title = clean(
        attr("shreddit-post", "post-title") ||
        textOf('h1, [slot="title"], a[slot="title"]', post) ||
        fallback.title
      );
      const body = clean(
        textOf('[slot="text-body"], div[id*="post-rtjson-content"], [data-click-id="text"], div[data-post-click-location="text-body"]', post)
      );
      const authorHref = attr('a[href*="/user/"]', "href");
      const author = (authorHref.match(/\/user\/([^/?#]+)/) || [])[1] || clean(textOf('[slot="authorName"], a[href*="/user/"]', post)).replace(/^u\//, "");
      const images = Array.from(post.querySelectorAll("img"))
        .map((img) => ({ url: img.currentSrc || img.src || "", width: img.naturalWidth || undefined, height: img.naturalHeight || undefined }))
        .filter((img) => /(?:i|preview|external-preview)\.redd\.it/.test(img.url))
        .filter((img) => Number(img.width || 0) >= 160 || Number(img.height || 0) >= 120);
      return {
        title,
        body,
        text: [title, body].filter(Boolean).join("\n\n"),
        author,
        published_at: attr("time[datetime]", "datetime"),
        images,
      };
    }, candidate);
    if (!detail.text) {
      return { ...candidate, detail_fetch_status: "detail_text_unavailable", full_text_observed: false, http_status: response?.status() || null };
    }
    return {
      ...candidate,
      ...detail,
      text: detail.text,
      detail_fetch_status: "full_text_observed",
      full_text_observed: true,
      http_status: response?.status() || null,
    };
  } catch (error) {
    return {
      ...candidate,
      detail_fetch_status: "detail_fetch_failed",
      detail_error: String(error?.message || error).slice(0, 240),
      full_text_observed: false,
    };
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
    locale: "en-US",
    userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
  });
  await context.addCookies(await cookiesFromFile(cookieFile));
  const rows = [];
  for (const subreddit of subreddits) {
    if (rows.length >= limit) break;
    const page = await context.newPage();
    try {
      await page.goto(`https://www.reddit.com/r/${encodeURIComponent(subreddit)}/hot/`, { waitUntil: "domcontentloaded", timeout: 20000 });
      await page.waitForTimeout(3500);
      const body = await page.locator("body").innerText({ timeout: 3000 }).catch(() => "");
      if (/429|Too Many Requests|rate limit|blocked|captcha/i.test(body)) continue;
      const candidates = await collectCandidates(page, subreddit);
      for (const candidate of candidates) {
        if (rows.length >= limit) break;
        rows.push(await fetchDetail(context, candidate));
      }
    } finally {
      await page.close().catch(() => {});
    }
  }
  const readableRows = rows
    .filter((row) => row.text && row.full_text_observed)
    .filter((row) => row.text.length >= 25 || row.images?.length)
    .slice(0, limit);
  if (!readableRows.length) fail("reddit_headless_export_no_rows", "Reddit browser pages produced no readable post rows");
  await fs.mkdir(path.dirname(out), { recursive: true });
  await fs.writeFile(out, JSON.stringify({
    export_schema_version: "reddit_headless_dom.v1",
    sources: { reddit: readableRows },
  }, null, 2));
  process.stdout.write(JSON.stringify({ status: "ok", source: "reddit", rows: readableRows.length }) + "\n");
} finally {
  await browser.close().catch(() => {});
}
