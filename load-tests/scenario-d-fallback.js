import http from 'k6/http';
import { check } from 'k6';
import { Rate } from 'k6/metrics';
import { chatUrl, authHeaders, chatPayload } from './config.js';

// Scenario D: provider failure / fallback. Requires the gateway to be
// running with MOCK_PROVIDER_FAIL=openai (see benchmarks/README.md) so the
// primary provider fails deterministically without touching real credentials
// or real provider APIs. Requests explicitly ask for an OpenAI model so
// every one of them forces the primary-provider-fails-then-fallback path;
// a successful response should come back from the fallback (Gemini) model
// instead.
export const fallbackSucceededRate = new Rate('fallback_succeeded');

export const options = {
  vus: 5,
  duration: '20s',
  thresholds: {
    fallback_succeeded: ['rate>0.95'],
  },
};

export default function () {
  const res = http.post(
    chatUrl(),
    chatPayload('Trigger fallback test', 'gpt-4o-mini'),
    { headers: authHeaders() },
  );

  const usedFallback =
    res.status === 200 && JSON.parse(res.body).model === 'gemini-3.5-flash-lite';
  fallbackSucceededRate.add(usedFallback);

  check(res, {
    'status is 200': (r) => r.status === 200,
    'fallback provider responded': () => usedFallback,
  });
}
