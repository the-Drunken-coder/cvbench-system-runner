export function createLatestScenarioLoader() {
  let generation = 0;
  let controller = null;

  return {
    async load(id, fetchBundle, selectedId, commit) {
      generation += 1;
      const requestGeneration = generation;
      controller?.abort();
      controller = new AbortController();
      const signal = controller.signal;
      try {
        const bundle = await fetchBundle(signal);
        if (signal.aborted || requestGeneration !== generation || selectedId() !== id) return false;
        await commit(bundle);
        return requestGeneration === generation && selectedId() === id;
      } catch (error) {
        if (signal.aborted || requestGeneration !== generation || error?.name === "AbortError") return false;
        throw error;
      }
    },
    cancel() {
      generation += 1;
      controller?.abort();
      controller = null;
    },
  };
}

export function exactFrameFailureMessage(status) {
  return `Exact frame is missing (${status}).`;
}
