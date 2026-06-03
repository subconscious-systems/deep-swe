#!/usr/bin/env bash
# Quick checks for Kimi inference DNS + HTTP.
set -euo pipefail

HOST="${KIMI_HOST:-kimi.subconscious.dev}"
IP="${KIMI_IP:-34.174.29.125}"
BASE_URL="http://${HOST}/v1"
OK=true

echo "=== DNS ==="
echo -n "  Public (1.1.1.1):  "
pub=$(dig @1.1.1.1 +short "${HOST}" A 2>/dev/null | head -1)
if [[ -n "${pub}" ]]; then echo "${pub}"; else echo "FAIL"; OK=false; fi

echo -n "  System resolver:   "
sys=$(dig +short "${HOST}" A 2>/dev/null | head -1)
if [[ -n "${sys}" ]]; then
  echo "${sys}"
else
  echo "FAIL (Tailscale/local cache still returning NXDOMAIN)"
  echo "         Fix: sudo dscacheutil -flushcache && sudo killall -HUP mDNSResponder"
  echo "         Then toggle Tailscale off/on if that doesn't help"
  OK=false
fi

echo ""
echo "=== HTTP ==="
echo -n "  Direct IP (http://${IP}/v1/...):  "
ip_code=$(curl -sS -o /dev/null -w "%{http_code}" --connect-timeout 5 \
  -H "Authorization: Bearer not-used" \
  -H "Content-Type: application/json" \
  -d '{"model":"subconscious/tim-kimi2.6","messages":[{"role":"user","content":"hi"}],"max_tokens":5}' \
  "http://${IP}/v1/chat/completions" 2>/dev/null || echo "000")
echo "HTTP ${ip_code}"
[[ "${ip_code}" != "200" ]] && OK=false

echo -n "  Hostname (${BASE_URL}/...):       "
h_code=$(curl -sS -o /tmp/kimi-check-body.txt -w "%{http_code}" --connect-timeout 5 \
  -H "Authorization: Bearer not-used" \
  -H "Content-Type: application/json" \
  -d '{"model":"subconscious/tim-kimi2.6","messages":[{"role":"user","content":"hi"}],"max_tokens":5}' \
  "${BASE_URL}/chat/completions" 2>/dev/null || echo "000")
echo "HTTP ${h_code}"
[[ "${h_code}" != "200" ]] && OK=false
if [[ -s /tmp/kimi-check-body.txt ]] && head -1 /tmp/kimi-check-body.txt | grep -qi '<html'; then
  echo "         WARN: got HTML (Squid error page?), not JSON"
  OK=false
fi

echo ""
echo "=== Docker DNS (what Pier's Squid proxy will use) ==="
docker_dns=$(docker run --rm alpine sh -c "nslookup ${HOST} 2>/dev/null | grep -A1 'Name:' | tail -1" 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' || echo "")
if [[ -n "${docker_dns}" ]]; then
  echo "  ${HOST} -> ${docker_dns}"
else
  echo "  FAIL: Docker can't resolve ${HOST}"
  OK=false
fi

echo ""
if ${OK}; then
  echo "=== ALL CHECKS PASSED ==="
else
  echo "=== SOME CHECKS FAILED ==="
fi
echo "  OPENAI_BASE_URL=${BASE_URL}"
