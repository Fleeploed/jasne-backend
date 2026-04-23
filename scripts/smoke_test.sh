#!/usr/bin/env bash
# Post-deploy smoke test — minimal checks that a deployment is alive.
# Usage: ./scripts/smoke_test.sh https://jasne-backend-staging.up.railway.app

set -euo pipefail

BASE_URL="${1:?usage: smoke_test.sh <base-url>}"
FAIL=0

check() {
    local name="$1"
    local expected="$2"
    local actual="$3"
    if [[ "$actual" == "$expected" ]]; then
        echo "  ✓ $name ($actual)"
    else
        echo "  ✗ $name (expected $expected, got $actual)"
        FAIL=1
    fi
}

echo "Smoke-testing $BASE_URL"

# 1. Health endpoint should return 200
status=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/health")
check "GET /health" 200 "$status"

# 2. Health body should have status=ok
body=$(curl -s "$BASE_URL/health")
if echo "$body" | grep -q '"status":"ok"'; then
    echo "  ✓ /health body contains status=ok"
else
    echo "  ✗ /health body missing status=ok: $body"
    FAIL=1
fi

# 3. Protected endpoint without token → 401/403
status=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/profile")
if [[ "$status" == "401" || "$status" == "403" ]]; then
    echo "  ✓ GET /profile unauthenticated ($status)"
else
    echo "  ✗ GET /profile unauthenticated (expected 401/403, got $status)"
    FAIL=1
fi

# 4. Protected endpoint with bogus token → 401
status=$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer bogus" "$BASE_URL/profile")
check "GET /profile bogus token" 401 "$status"

if [[ $FAIL -eq 0 ]]; then
    echo ""
    echo "All smoke checks passed."
    exit 0
else
    echo ""
    echo "Smoke test FAILED"
    exit 1
fi
