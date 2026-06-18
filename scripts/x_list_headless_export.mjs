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

const url = arg("--url", "https://x.com/i/lists/2056032482127175889?s=20");
const limit = Math.min(10, Number(arg("--limit", "10")));
const cookieFile = arg("--cookie-file");
const out = arg("--out");
if (!cookieFile) fail("secret_env_missing", "--cookie-file is required");
if (!out) fail("x_headless_export_missing_out", "--out is required");

function normalizedListUrl(value) {
  try {
    const parsed = new URL(value);
    if (/\/i\/lists\/\d+/.test(parsed.pathname)) parsed.search = "";
    return parsed.href;
  } catch {
    return value;
  }
}

let chromium;
try {
  ({ chromium } = await import("playwright"));
} catch {
  try {
    ({ chromium } = await import("playwright-core"));
  } catch {
    fail("playwright_unavailable", "Playwright is required for X list headless export");
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
  const allowed = new Set(["auth_token", "ct0", "twid", "kdt", "att", "guest_id", "guest_id_ads", "guest_id_marketing", "personalization_id"]);
  return raw
    .split(";")
    .map((part) => part.trim())
    .filter(Boolean)
    .flatMap((part) => {
      const eq = part.indexOf("=");
      if (eq <= 0) return [];
      const name = part.slice(0, eq).trim();
      const value = part.slice(eq + 1).trim();
      if (!allowed.has(name) || !value) return [];
      return ["https://x.com", "https://twitter.com"].map((cookieUrl) => ({
        name,
        value,
        url: cookieUrl,
      }));
    });
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

function tweetIdFromUrl(value) {
  const match = String(value || "").match(/\/status\/(\d+)/);
  return match ? match[1] : "";
}

async function collectRows(page, maxRows) {
  const rows = [];
  for (let attempt = 0; attempt < 8 && rows.length < maxRows; attempt += 1) {
    const batch = await page.evaluate(() => {
      const abs = (href) => {
        try {
          return new URL(href, location.href).href.replace("twitter.com", "x.com");
        } catch {
          return "";
        }
      };
      const textOf = (root) => (root.innerText || "").replace(/\s+/g, " ").trim();
      const stripQuotedText = (text) => text.replace(/\s+(引用|Quote)\s+[\s\S]*$/i, "").trim();
      const tweetTextOf = (root) => stripQuotedText(Array.from(root.querySelectorAll('[data-testid="tweetText"]'))
        .map((node) => (node.innerText || "").trim())
        .filter(Boolean)
        .join("\n")
        .replace(/\n{3,}/g, "\n\n")
        .trim());
      const statusUrls = (root) => {
        const urls = Array.from(root.querySelectorAll('a[href*="/status/"]'))
          .map((a) => abs(a.getAttribute("href")))
          .map((href) => {
            const match = href.match(/^(https:\/\/x\.com\/[^/]+\/status\/\d+)/);
            return match ? match[1] : "";
          })
          .filter(Boolean);
        return Array.from(new Set(urls));
      };
      const profileOf = (root, statusUrl) => {
        const fromUrl = statusUrl.match(/^https:\/\/x\.com\/([^/]+)\/status\//)?.[1] || "";
        const userName = root.querySelector('[data-testid="User-Name"]') || root;
        const spans = Array.from(userName.querySelectorAll("span"))
          .map((span) => (span.innerText || "").replace(/\s+/g, " ").trim())
          .filter(Boolean);
        const handleText = spans.find((text) => /^@[\w_]{1,30}$/.test(text)) || (fromUrl ? `@${fromUrl}` : "");
        const displayName = spans.find((text) =>
          text &&
          text !== handleText &&
          !text.startsWith("@") &&
          !/^·$/.test(text) &&
          !/^\d+[smhd]$/.test(text) &&
          !/(分钟|小时|天|分钟前|小时前)/.test(text)
        ) || fromUrl || "X";
        const avatar = Array.from(root.querySelectorAll("img"))
          .map((img) => img.currentSrc || img.src || "")
          .find((src) => /profile_images/.test(src)) || "";
        const handle = (handleText || fromUrl).replace(/^@/, "");
        return { author: handle || "x_list", handle, display_name: displayName, avatar_url: avatar };
      };
      const imagesOf = (root) => Array.from(root.querySelectorAll("img"))
        .map((img) => ({ url: img.currentSrc || img.src, width: img.naturalWidth || undefined, height: img.naturalHeight || undefined }))
        .filter((img) => /^https?:\/\/pbs\.twimg\.com\/media\//.test(img.url));
      return Array.from(document.querySelectorAll('article[data-testid="tweet"]')).map((article) => {
        const urls = statusUrls(article);
        const profile = profileOf(article, urls[0] || "");
        const socialContext = textOf(article.querySelector('[data-testid="socialContext"]') || "");
        return {
          text: tweetTextOf(article),
          url: urls[0] || "",
          tweet_id: (urls[0] || "").match(/\/status\/(\d+)/)?.[1] || "",
          ...profile,
          published_at: article.querySelector("time")?.getAttribute("datetime") || "",
          images: imagesOf(article),
          is_quote: urls.length > 1,
          is_repost: /reposted|retweeted|转发|转推|转帖/i.test(socialContext),
        };
      }).filter((row) => row.text.length >= 20 && row.url && !row.is_quote && !row.is_repost);
    });
    rows.push(...batch);
    await page.mouse.wheel(0, 1800).catch(() => {});
    await page.waitForTimeout(900);
  }
  return uniqRows(rows).slice(0, maxRows);
}

async function fetchDetailRow(context, row) {
  const page = await context.newPage();
  try {
    await page.goto(row.url, { waitUntil: "domcontentloaded", timeout: 20000 });
    await page.waitForLoadState("networkidle", { timeout: 7000 }).catch(() => {});
    await page.locator('article[data-testid="tweet"]').first().waitFor({ timeout: 10000 }).catch(() => {});
    const detail = await page.evaluate((expectedId) => {
      const abs = (href) => {
        try {
          return new URL(href, location.href).href.replace("twitter.com", "x.com");
        } catch {
          return "";
        }
      };
      const stripQuotedText = (text) => text.replace(/\s+(引用|Quote)\s+[\s\S]*$/i, "").trim();
      const tweetTextOf = (root) => stripQuotedText(Array.from(root.querySelectorAll('[data-testid="tweetText"]'))
        .map((node) => (node.innerText || "").trim())
        .filter(Boolean)
        .join("\n")
        .replace(/\n{3,}/g, "\n\n")
        .trim());
      const statusUrls = (root) => Array.from(root.querySelectorAll('a[href*="/status/"]'))
        .map((a) => abs(a.getAttribute("href")))
        .map((href) => {
          const match = href.match(/^(https:\/\/x\.com\/[^/]+\/status\/\d+)/);
          return match ? match[1] : "";
        })
        .filter(Boolean);
      const profileOf = (root, statusUrl) => {
        const fromUrl = statusUrl.match(/^https:\/\/x\.com\/([^/]+)\/status\//)?.[1] || "";
        const userName = root.querySelector('[data-testid="User-Name"]') || root;
        const spans = Array.from(userName.querySelectorAll("span"))
          .map((span) => (span.innerText || "").replace(/\s+/g, " ").trim())
          .filter(Boolean);
        const handleText = spans.find((text) => /^@[\w_]{1,30}$/.test(text)) || (fromUrl ? `@${fromUrl}` : "");
        const displayName = spans.find((text) =>
          text &&
          text !== handleText &&
          !text.startsWith("@") &&
          !/^·$/.test(text) &&
          !/^\d+[smhd]$/.test(text) &&
          !/(分钟|小时|天|分钟前|小时前)/.test(text)
        ) || fromUrl || "X";
        const avatar = Array.from(root.querySelectorAll("img"))
          .map((img) => img.currentSrc || img.src || "")
          .find((src) => /profile_images/.test(src)) || "";
        const handle = (handleText || fromUrl).replace(/^@/, "");
        return { author: handle || "x_list", handle, display_name: displayName, avatar_url: avatar };
      };
      const imagesOf = (root) => Array.from(root.querySelectorAll("img"))
        .map((img) => ({ url: img.currentSrc || img.src, width: img.naturalWidth || undefined, height: img.naturalHeight || undefined }))
        .filter((img) => /^https?:\/\/pbs\.twimg\.com\/media\//.test(img.url));
      const articles = Array.from(document.querySelectorAll('article[data-testid="tweet"]'));
      const article = articles.find((candidate) => statusUrls(candidate).some((url) => url.includes(`/status/${expectedId}`))) || articles[0];
      if (!article) return null;
      const urls = Array.from(new Set(statusUrls(article)));
      const url = urls.find((candidate) => candidate.includes(`/status/${expectedId}`)) || urls[0] || "";
      return {
        text: tweetTextOf(article),
        url,
        tweet_id: expectedId,
        ...profileOf(article, url),
        published_at: article.querySelector("time")?.getAttribute("datetime") || "",
        images: imagesOf(article),
        is_quote: urls.length > 1,
      };
    }, tweetIdFromUrl(row.url));
    if (!detail || !detail.text) {
      return { ...row, detail_fetch_status: "detail_text_unavailable", full_text_observed: false };
    }
    const text = detail.text.length >= row.text.length ? detail.text : row.text;
    return {
      ...row,
      ...detail,
      text,
      images: detail.images?.length ? detail.images : row.images,
      detail_fetch_status: "full_text_observed",
      full_text_observed: true,
    };
  } catch (error) {
    return {
      ...row,
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
  const page = await context.newPage();
  const targetUrl = normalizedListUrl(url);
  await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 20000 });
  await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
  await page.waitForTimeout(2500);
  await page.locator('article[data-testid="tweet"]').first().waitFor({ timeout: 15000 }).catch(() => {});
  const rows = await collectRows(page, limit);
  if (!rows.length) {
    const body = await page.locator("body").innerText({ timeout: 3000 }).catch(() => "");
    if (/login|sign in|captcha|challenge|verify|请登录|验证码/i.test(body)) fail("auth_or_challenge_required", "X page indicates auth/challenge/login state");
    fail("x_headless_export_no_rows", "X list page produced no tweet rows");
  }
  const detailedRows = [];
  for (const row of rows) {
    detailedRows.push(await fetchDetailRow(context, row));
  }
  await fs.mkdir(path.dirname(out), { recursive: true });
  await fs.writeFile(out, JSON.stringify({
    export_schema_version: "x_list_headless_dom.v2",
    source_url: targetUrl,
    sources: { x_list: detailedRows },
  }, null, 2));
  process.stdout.write(JSON.stringify({
    status: "ok",
    source: "x_list",
    rows: detailedRows.length,
    full_text_rows: detailedRows.filter((row) => row.full_text_observed).length,
  }) + "\n");
} finally {
  await browser.close().catch(() => {});
}
