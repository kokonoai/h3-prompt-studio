import { describe, expect, it } from "vitest";
import { parseEpisodeDuration, parseEpisodeDurationParts, splitEpisodeDuration } from "./episodeDuration";

describe("episode duration input", () => {
  it.each([
    ["1分48秒", 108],
    ["0分30秒", 30],
    ["5分0秒", 300],
    ["1 分鐘 48 秒", 108],
    ["１：４８", 108],
    ["0:30", 30],
    ["30秒", 30],
    ["5分", 300],
    ["1m 48s", 108],
    ["1.12", 67],
    [".5", 30],
    ["1.8分钟", 108],
    ["3:00:00", 10800],
  ] as const)("parses %s as %i seconds", (value, seconds) => {
    expect(parseEpisodeDuration(value)).toBe(seconds);
  });

  it.each(["", "1:", "1:90", "1:60:00", "29秒", "180分1秒", "-2", "1 48", "2分多", "1e2"])(
    "rejects incomplete, out-of-range or malformed input %j",
    value => expect(parseEpisodeDuration(value)).toBeNull(),
  );

  it.each([[108, "1", "48"], [30, "0", "30"], [300, "5", "0"]] as const)(
    "round-trips %i seconds through the two fields", (totalSeconds, minutes, seconds) => {
      const parts = splitEpisodeDuration(totalSeconds);
      expect(parts).toEqual({ minutes, seconds });
      expect(parseEpisodeDurationParts(parts)).toBe(totalSeconds);
    },
  );

  it("accepts whole digits or an empty zero field and rejects invalid durations", () => {
    expect(parseEpisodeDurationParts({ minutes: "", seconds: "３０" })).toBe(30);
    expect(parseEpisodeDurationParts({ minutes: "5", seconds: "" })).toBe(300);
    expect(parseEpisodeDurationParts({ minutes: "1", seconds: "60" })).toBeNull();
    expect(parseEpisodeDurationParts({ minutes: "180", seconds: "1" })).toBeNull();
    expect(parseEpisodeDurationParts({ minutes: "0", seconds: "29" })).toBeNull();
    expect(parseEpisodeDurationParts({ minutes: "1.5", seconds: "0" })).toBeNull();
  });
});
