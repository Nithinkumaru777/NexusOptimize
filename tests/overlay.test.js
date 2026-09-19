// Overlay HUD placement (run by `npm test` via `node --test`).
const test = require('node:test');
const assert = require('node:assert');
const { overlayBounds, OVERLAY_SIZE } = require('../ui/electron/overlay-bounds');

test('HUD is compact', () => {
  assert.ok(OVERLAY_SIZE.width <= 200 && OVERLAY_SIZE.height <= 40);
});

test('HUD is pinned to the top-right corner of the work area', () => {
  const b = overlayBounds({ x: 0, y: 0, width: 1920, height: 1040 });
  assert.strictEqual(b.x + b.width, 1920 - 8);
  assert.strictEqual(b.y, 8);
});

test('pinning follows a secondary / offset work area', () => {
  const b = overlayBounds({ x: -2560, y: 40, width: 2560, height: 1400 });
  assert.strictEqual(b.x + b.width, -8);
  assert.strictEqual(b.y, 48);
});
