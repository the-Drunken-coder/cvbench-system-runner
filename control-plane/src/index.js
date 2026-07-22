import { createApp } from "./app.js";
import { D1Store } from "./store.js";

export default {
  fetch(request, env) {
    const app = createApp({
      store: new D1Store(env.DB),
      assets: env.ASSETS,
      submissionKeys: env.SUBMISSION_API_KEYS || "",
      runnerToken: env.RUNNER_TOKEN || "",
      operatorToken: env.OPERATOR_API_TOKEN || "",
      maxSubmissionsPerHour: env.MAX_SUBMISSIONS_PER_HOUR,
      leaseSeconds: env.LEASE_SECONDS,
    });
    return app.fetch(request);
  },
};
