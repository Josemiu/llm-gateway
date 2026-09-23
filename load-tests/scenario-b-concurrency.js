import http from 'k6/http';
import { check } from 'k6';
import { chatUrl, authHeaders, chatPayload, randomPrompt } from './config.js';

// Scenario B: concurrency ramp. Steps VUs 10 -> 50 -> 100 -> 250 to find
// where the gateway's own overhead (auth, rate limiting, routing, fallback
// bookkeeping, async Postgres writes) starts degrading, independent of any
// real provider's throughput limits (see DECISIONS.md - MockProvider is
// always used for load testing, its simulated latency isn't a factor being
// scaled here).
export const options = {
  stages: [
    { duration: '30s', target: 10 },
    { duration: '30s', target: 50 },
    { duration: '30s', target: 100 },
    { duration: '30s', target: 250 },
    { duration: '15s', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.05'],
  },
};

export default function () {
  const res = http.post(chatUrl(), chatPayload(randomPrompt()), {
    headers: authHeaders(),
  });

  check(res, {
    'status is 200': (r) => r.status === 200,
  });
}
