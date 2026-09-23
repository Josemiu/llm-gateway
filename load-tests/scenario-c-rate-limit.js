import http from 'k6/http';
import { check } from 'k6';
import { Rate } from 'k6/metrics';
import { chatUrl, authHeaders, chatPayload } from './config.js';

// Scenario C: rate limiting. Requires RATE_LIMIT_PER_MINUTE at its normal
// default (20, see .env.docker.example) - unlike scenario B, which raises it
// on purpose to take the rate limiter out of the picture. A single VU fires
// requests back-to-back on one API key, well above 20/min, to confirm Redis
// fixed-window rate limiting actually rejects the excess with 429.
export const rateLimitedRate = new Rate('rate_limited');

export const options = {
  vus: 1,
  iterations: 40,
  thresholds: {
    // We expect a meaningful share of requests to be rejected - this
    // threshold just guards against the rate limiter silently not firing.
    rate_limited: ['rate>0.1'],
  },
};

export default function () {
  const res = http.post(chatUrl(), chatPayload('Quick rate limit probe'), {
    headers: authHeaders(),
  });

  rateLimitedRate.add(res.status === 429);

  check(res, {
    'status is 200 or 429': (r) => r.status === 200 || r.status === 429,
  });
}
