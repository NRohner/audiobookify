// The clone-a-voice clip flow: choose a source, record (or pick a file), then edit.
// A reducer so the component stays a thin view; events that don't apply return the same state.

export const START = { stage: "choose" };

const failure = (error) =>
  error?.kind === "permission_denied" ? { stage: "denied" } : { stage: "failed", message: error?.message ?? String(error) };

export function clipFlow(state, event) {
  const { stage } = state;
  const idle = stage === "choose" || stage === "denied" || stage === "failed";
  switch (event.type) {
    case "record":
      return idle ? { stage: "starting" } : state;
    case "picked":
      return idle ? { stage: "editing", path: event.path, recorded: false } : state;
    case "started":
      return stage === "starting" ? { stage: "recording", startedAt: event.at } : state;
    case "stop":
      return stage === "recording" ? { stage: "stopping", startedAt: state.startedAt } : state;
    case "stopped":
      return stage === "stopping" ? { stage: "editing", path: event.path, recorded: true } : state;
    case "error":
      return stage === "starting" || stage === "stopping" ? failure(event.error) : state;
    case "reset":
      return stage === "editing" || stage === "denied" || stage === "failed" ? START : state;
    default:
      return state;
  }
}

export function recordErrorText(error) {
  if (error?.kind === "permission_denied") {
    return {
      title: "Audiobookify needs permission to record",
      body:
        "Open System Settings, go to Privacy & Security, then Screen & System Audio Recording, and turn on " +
        "Audiobookify. Quit and reopen the app, then try again.",
    };
  }
  return { title: "The recording didn't work", body: error?.message ?? String(error) };
}

// Characters macOS or the shell treat specially in a file name become dashes.
export function exportName(name) {
  const base = name.trim().replace(/[/\\:]/g, "-") || "voice clip";
  return `${base}.wav`;
}
