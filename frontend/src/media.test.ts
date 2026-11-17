import { describe, expect, it } from "vitest";
import { validImageFile } from "./media";

describe("reference-image validation", () => {
  it("accepts supported nonempty images", () => {
    for (const type of ["image/png", "image/jpeg", "image/webp"])
      expect(validImageFile({ type, size: 1024 })).toBeNull();
  });
  it("rejects SVG and oversized input before upload", () => {
    expect(validImageFile({ type: "image/svg+xml", size: 100 })).not.toBeNull();
    expect(
      validImageFile({ type: "image/png", size: 9 * 1024 * 1024 }),
    ).not.toBeNull();
    expect(validImageFile({ type: "image/png", size: 0 })).not.toBeNull();
  });
});
