// Vercel serverless function: sends the contact form through Resend.
// Returns 501 when RESEND_API_KEY is not set so the client falls back to mailto.
const TO = 'tanishabrahma26@gmail.com';
const WINDOW_MS = 10 * 60 * 1000;
const LIMIT = 5;
const hits = new Map();

function limited(ip) {
  const now = Date.now();
  const list = (hits.get(ip) || []).filter((t) => now - t < WINDOW_MS);
  list.push(now);
  hits.set(ip, list);
  return list.length > LIMIT;
}

export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ ok: false });
  if (!process.env.RESEND_API_KEY) return res.status(501).json({ ok: false, reason: 'not-configured' });

  const ip = (req.headers['x-forwarded-for'] || '').split(',')[0].trim() || 'unknown';
  if (limited(ip)) return res.status(429).json({ ok: false, reason: 'rate-limited' });

  const { email = '', name = '', reason = '', message = '', website = '' } = req.body || {};
  if (website) return res.status(200).json({ ok: true }); // honeypot: pretend success, drop silently
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email) || !name.trim() || !message.trim()) {
    return res.status(400).json({ ok: false, reason: 'invalid' });
  }

  const r = await fetch('https://api.resend.com/emails', {
    method: 'POST',
    headers: { authorization: `Bearer ${process.env.RESEND_API_KEY}`, 'content-type': 'application/json' },
    body: JSON.stringify({
      from: process.env.CONTACT_FROM || 'Portfolio <onboarding@resend.dev>',
      to: [TO],
      reply_to: email,
      subject: `Portfolio contact — ${reason.trim().slice(0, 120) || 'no subject'}`,
      text: `From: ${name.trim()} <${email}>\nReason: ${reason.trim()}\n\n${message.trim()}`,
    }),
  });
  if (!r.ok) return res.status(502).json({ ok: false, reason: 'send-failed' });
  return res.status(200).json({ ok: true });
}
