#!/usr/bin/env bash
set -euo pipefail

SECRETS_DIR="${NEWS_HARNESS_LOCAL_SECRETS_DIR:-/tmp/news-harness-secrets}"
ENV_FILE="${SECRETS_DIR}/news_harness.env"
DEEPSEEK_FILE="${SECRETS_DIR}/deepseek_api_key.txt"
X_COOKIE_FILE="${SECRETS_DIR}/x_cookie.txt"
REDDIT_COOKIE_FILE="${SECRETS_DIR}/reddit_cookie.txt"

umask 077
mkdir -p "${SECRETS_DIR}"
chmod 700 "${SECRETS_DIR}"

echo "News Harness local secret setup"
echo "Secrets directory: ${SECRETS_DIR}"
echo "Values are written outside the repo and are not echoed."
echo

read -r -s -p "Paste DeepSeek API key, then press Enter: " DEEPSEEK_KEY
echo
if [[ -z "${DEEPSEEK_KEY}" ]]; then
  echo "DeepSeek API key cannot be empty." >&2
  exit 1
fi
printf "%s\n" "${DEEPSEEK_KEY}" > "${DEEPSEEK_FILE}"
unset DEEPSEEK_KEY

read -r -s -p "Paste X cookie, then press Enter: " X_COOKIE
echo
if [[ -z "${X_COOKIE}" ]]; then
  echo "X cookie cannot be empty." >&2
  exit 1
fi
printf "%s\n" "${X_COOKIE}" > "${X_COOKIE_FILE}"
unset X_COOKIE

read -r -s -p "Paste Reddit cookie, or leave blank and press Enter to skip: " REDDIT_COOKIE
echo
if [[ -n "${REDDIT_COOKIE}" ]]; then
  printf "%s\n" "${REDDIT_COOKIE}" > "${REDDIT_COOKIE_FILE}"
  unset REDDIT_COOKIE
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

chmod 600 "${DEEPSEEK_FILE}" "${X_COOKIE_FILE}" "${ENV_FILE}"
if [[ -f "${REDDIT_COOKIE_FILE}" ]]; then
  chmod 600 "${REDDIT_COOKIE_FILE}"
fi

echo
echo "Created:"
echo "  ${DEEPSEEK_FILE}"
echo "  ${X_COOKIE_FILE}"
echo "  ${REDDIT_COOKIE_FILE} (if provided)"
echo "  ${ENV_FILE}"
echo
echo "Load these values before a manual smoke:"
echo "  set -a"
echo "  source ${ENV_FILE}"
echo "  set +a"
echo
echo "No secret values were printed."
