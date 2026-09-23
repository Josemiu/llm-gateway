import http from 'k6/http';
import { check, sleep } from 'k6';
import { chatUrl, authHeaders, chatPayload, randomPrompt } from './config.js';

// Scenario A: normal traffic. A handful of users sending occasional chat
// requests with realistic "think time" between them - the baseline the
// other scenarios (B: concurrency ramp, C: rate limiting) are compared
// against.
export const options = {
  vus: 5,
  duration: '1m',
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<3000'],
  },
};

export default function () {
  const res = http.post(chatUrl(), chatPayload(randomPrompt()), {
    headers: authHeaders(),
  });

  check(res, {
    'status is 200': (r) => r.status === 200,
    'has content': (r) => JSON.parse(r.body).content !== undefined,
  });

  sleep(Math.random() * 2 + 1); // 1-3s think time between requests
}
