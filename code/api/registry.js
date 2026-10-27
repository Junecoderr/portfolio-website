// The TB Package Index API: /api/registry/<route> (vercel.json rewrites every
// /api/registry/* path here). Routes, storage and review rules are described in
// api/_lib/registry/app.js and CONTRIBUTING.md ("Package index").

import { handleRegistry } from "./_lib/registry/app.js";

const handler = (request) => handleRegistry(request);

export { handler as GET, handler as POST, handler as PUT, handler as DELETE, handler as PATCH };
