import { test } from "node:test";
import assert from "node:assert/strict";
import { numericIds } from "./ids.js";

test("string ids become numbers; everything else passes through", () => {
  assert.deepEqual(numericIds({ bookId: "2", chapterId: 5, voiceProfileId: null, term: "12", path: "/a" }), {
    bookId: 2,
    chapterId: 5,
    voiceProfileId: null,
    term: "12",
    path: "/a",
  });
  assert.equal(numericIds(undefined), undefined);
});
