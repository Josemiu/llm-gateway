export const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
export const API_KEY = __ENV.API_KEY || 'devkey1';

export function chatUrl() {
  return `${BASE_URL}/v1/chat/completions`;
}

export function authHeaders() {
  return {
    'Content-Type': 'application/json',
    'X-API-Key': API_KEY,
  };
}

// Short prompts route "auto" to Gemini; prompts >= 200 chars or containing
// one of the routing keywords ("code", "analyze", "explain in detail") route
// to OpenAI - see app/routing/selector.py. Mixing both keeps traffic
// realistic instead of only exercising one provider path.
export const SIMPLE_PROMPTS = [
  'What is the capital of France?',
  'Give me a one-line summary of what HTTP is.',
  'Say hello in three different languages.',
];

export const COMPLEX_PROMPTS = [
  'Please analyze the trade-offs between SQL and NoSQL databases for a high-write workload.',
  'Write some code that reverses a linked list in Python and explain how it works.',
  'Can you explain in detail how TCP congestion control avoids network collapse?',
];

export function randomPrompt() {
  const pool = Math.random() < 0.5 ? SIMPLE_PROMPTS : COMPLEX_PROMPTS;
  return pool[Math.floor(Math.random() * pool.length)];
}

export function chatPayload(prompt, model = 'auto') {
  return JSON.stringify({
    model,
    messages: [{ role: 'user', content: prompt }],
  });
}
