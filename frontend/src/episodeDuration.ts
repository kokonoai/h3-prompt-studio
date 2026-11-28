const minuteUnit = "(?:分钟|分鐘|分|minutes?|mins?|m)";
const secondUnit = "(?:秒钟|秒鐘|秒|seconds?|secs?|s)";
const unitDuration = new RegExp(`^(?:(\\d+)\\s*${minuteUnit}\\s*)?(?:(\\d+)\\s*${secondUnit})?$`, "i");
const decimalMinutes = new RegExp(`^(\\d+(?:\\.\\d+)?|\\.\\d+)\\s*(?:${minuteUnit})?$`, "i");

export type EpisodeDurationParts = { minutes: string; seconds: string };

function validEpisodeSeconds(seconds: number): number | null {
  // Match the stored episode_minutes field's range of 0.5–180 minutes.
  return seconds >= 30 && seconds <= 180 * 60 ? Math.round(seconds) : null;
}

export function splitEpisodeDuration(totalSeconds: number): EpisodeDurationParts {
  return {
    minutes: String(Math.floor(totalSeconds / 60)),
    seconds: String(totalSeconds % 60),
  };
}

export function parseEpisodeDurationParts(parts: EpisodeDurationParts): number | null {
  const minutes = parts.minutes.normalize("NFKC").trim();
  const seconds = parts.seconds.normalize("NFKC").trim();
  if (!/^\d*$/.test(minutes) || !/^\d*$/.test(seconds) || Number(seconds) >= 60) return null;
  return validEpisodeSeconds(Number(minutes) * 60 + Number(seconds));
}

/** Parse the production target to whole seconds without silently clamping it. */
export function parseEpisodeDuration(value: string): number | null {
  const text = value.normalize("NFKC").trim();
  const clock = /^(?:(\d+)\s*:\s*)?(\d+)\s*:\s*([0-5]?\d)$/.exec(text);
  const units = unitDuration.exec(text);
  const decimal = decimalMinutes.exec(text);
  let seconds: number;

  if (clock) {
    const minutes = Number(clock[2]);
    if (clock[1] !== undefined && minutes >= 60) return null;
    seconds = Number(clock[1] ?? 0) * 3600 + minutes * 60 + Number(clock[3]);
  } else if (units && (units[1] !== undefined || units[2] !== undefined)) {
    seconds = Number(units[1] ?? 0) * 60 + Number(units[2] ?? 0);
  } else if (decimal) {
    seconds = Number(decimal[1]) * 60;
  } else {
    return null;
  }

  return validEpisodeSeconds(seconds);
}
