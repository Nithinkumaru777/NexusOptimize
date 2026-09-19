// Compact HUD pinned to the top-right corner of a display's work area.
const OVERLAY_SIZE = { width: 196, height: 30 };
const MARGIN = 8;

function overlayBounds(workArea) {
  return {
    ...OVERLAY_SIZE,
    x: workArea.x + workArea.width - OVERLAY_SIZE.width - MARGIN,
    y: workArea.y + MARGIN
  };
}

module.exports = { overlayBounds, OVERLAY_SIZE };
