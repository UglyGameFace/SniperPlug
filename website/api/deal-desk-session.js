import {
  clearLoginFailures,
  clearSessionCookie,
  createSessionCookie,
  getLoginThrottle,
  handleError,
  isAuthenticated,
  json,
  methodNotAllowed,
  registerLoginFailure,
  requireSameOrigin,
  verifyPassword,
} from '../server/deal-desk.js';

export default {
  async fetch(request) {
    try {
      if (request.method === 'GET') {
        return json({ authenticated: isAuthenticated(request) });
      }

      if (request.method === 'POST') {
        requireSameOrigin(request);
        const throttle = getLoginThrottle(request);
        if (throttle.blocked) {
          return json(
            { error: 'Too many failed unlock attempts. Wait before trying again.' },
            429,
            { 'retry-after': String(throttle.retryAfter) },
          );
        }

        const body = await request.json().catch(() => ({}));
        if (!verifyPassword(body.password)) {
          const failure = registerLoginFailure(request);
          await new Promise((resolve) => setTimeout(resolve, 650));
          if (failure.blocked) {
            return json(
              { error: 'Too many failed unlock attempts. Wait before trying again.' },
              429,
              { 'retry-after': String(failure.retryAfter) },
            );
          }
          return json({ error: 'Incorrect Control Center password.' }, 401);
        }

        clearLoginFailures(request);
        return json(
          { authenticated: true },
          200,
          { 'set-cookie': createSessionCookie() },
        );
      }

      if (request.method === 'DELETE') {
        requireSameOrigin(request);
        return json(
          { authenticated: false },
          200,
          { 'set-cookie': clearSessionCookie() },
        );
      }

      return methodNotAllowed(['GET', 'POST', 'DELETE']);
    } catch (error) {
      return handleError(error);
    }
  },
};
