'use strict';

const crypto = require('crypto');

function verifyHmac(rawBody, providedHex, secret) {
  if (typeof providedHex !== 'string' || providedHex.length === 0) return false;
  if (!/^[0-9a-f]+$/i.test(providedHex)) return false;

  const computed = crypto.createHmac('sha256', secret).update(rawBody).digest();
  let provided;
  try {
    provided = Buffer.from(providedHex, 'hex');
  } catch (_e) {
    return false;
  }
  if (provided.length !== computed.length) return false;
  return crypto.timingSafeEqual(provided, computed);
}

module.exports = { verifyHmac };
