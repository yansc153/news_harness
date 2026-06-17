#!/usr/bin/env bash
set -euo pipefail

SECRETS_DIR="${NEWS_HARNESS_LOCAL_SECRETS_DIR:-/tmp/news-harness-secrets}"
ENV_FILE="${SECRETS_DIR}/news_harness.env"
DEEPSEEK_FILE="${SECRETS_DIR}/deepseek_api_key.txt"
X_COOKIE_FILE="${SECRETS_DIR}/x_cookie.txt"
REDDIT_COOKIE_FILE="${SECRETS_DIR}/reddit_cookie.txt"

if ! command -v pbpaste >/dev/null 2>&1; then
  echo "pbpaste is required on macOS for clipboard import." >&2
  exit 1
fi

umask 077
mkdir -p "${SECRETS_DIR}"
chmod 700 "${SECRETS_DIR}"

echo "News Harness clipboard secret setup"
echo "Secrets directory: ${SECRETS_DIR}"
echo "Secret values will be read from the macOS clipboard and will not be printed."
echo

read -r -p "Copy the DeepSeek API key to clipboard, then press Enter here: "
pbpaste > "${DEEPSEEK_FILE}"
if [[ ! -s "${DEEPSEEK_FILE}" ]]; then
  echo "Clipboard was empty; DeepSeek key file was not populated." >&2
  exit 1
fi
chmod 600 "${DEEPSEEK_FILE}"
echo "DeepSeek key captured to ${DEEPSEEK_FILE} ($(wc -c < "${DEEPSEEK_FILE}" | tr -d ' ') bytes)."
echo

read -r -p "Copy the full X cookie to clipboard, then press Enter here: "
pbpaste > "${X_COOKIE_FILE}"
if [[ ! -s "${X_COOKIE_FILE}" ]]; then
  echo "Clipboard was empty; X cookie file was not populated." >&2
  exit 1
fi
chmod 600 "${X_COOKIE_FILE}"
echo "X cookie captured to ${X_COOKIE_FILE} ($(wc -c < "${X_COOKIE_FILE}" | tr -d ' ') bytes)."
echo

read -r -p "Copy the full Reddit cookie to clipboard, or leave clipboard unchanged and type skip: " REDDIT_ACTION
if [[ "${REDDIT_ACTION}" != "skip" ]]; then
  pbpaste > "${REDDIT_COOKIE_FILE}"
  if [[ ! -s "${REDDIT_COOKIE_FILE}" ]]; then
    echo "Clipboard was empty; Reddit cookie file was not populated." >&2
    exit 1
  fi
  chmod 600 "${REDDIT_COOKIE_FILE}"
  echo "Reddit cookie captured to ${REDDIT_COOKIE_FILE} ($(wc -c < "${REDDIT_COOKIE_FILE}" | tr -d ' ') bytes)."
  echo
fi

cat > "${ENV_FILE}" <<EOF
DEEPSEEK_API_KEY_FILE=${DEEPSEEK_FILE}
NEWS_HARNESS_X_COOKIE_FILE=${X_COOKIE_FILE}
NEWS_HARNESS_X_LIST_SECRET_REF=secret_ref:x_list_reader_cookie_v1
NEWS_HARNESS_X_LIST_SESSION_REF=session_ref:x_list_small_account_v1
NEWS_HARNESS_MANUAL_SMOKE_ACK=I_UNDERSTAND_THIS_IS_READ_ONLY_MANUAL_SMOKE
NEWS_HARNESS_REAL_SOURCE_SMOKE=1
NEWS_HARNESS_DEEPSEEK_SMOKE=1
EOF
if [[ -f "${REDDIT_COOKIE_FILE}" ]]; then
  echo "NEWS_HARNESS_REDDIT_COOKIE_FILE=${REDDIT_COOKIE_FILE}" >> "${ENV_FILE}"
fi
chmod 600 "${ENV_FILE}"

echo "Environment file created at ${ENV_FILE}."
echo
echo "Load it with:"
echo "  set -a"
echo "  source ${ENV_FILE}"
echo "  set +a"
echo
echo "Only byte counts were printed; secret values were not printed."
