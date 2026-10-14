import "@testing-library/jest-dom";

// jsdom does not implement matchMedia; lightweight-charts (via fancy-canvas)
// calls it on chart creation, so polyfill it for component tests.
if (!window.matchMedia) {
  window.matchMedia = (query: string): MediaQueryList => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => undefined,
    removeListener: () => undefined,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    dispatchEvent: () => false,
  });
}

// jsdom does not implement ResizeObserver; lightweight-charts uses it for width.
if (!(window as unknown as { ResizeObserver?: unknown }).ResizeObserver) {
  (window as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

// jsdom does not implement canvas; lightweight-charts measures its price axis
// via 2d context. Stub getContext to a no-op recorder so chart construction
// does not emit uncaught "Value is null" errors (tests themselves pass; the
// unhandled rejection fails the vitest process).
if (!HTMLCanvasElement.prototype.getContext) {
  const noopCtx = new Proxy(
    {},
    { get: () => () => undefined },
  );
  HTMLCanvasElement.prototype.getContext = (() => noopCtx) as unknown as typeof HTMLCanvasElement.prototype.getContext;
}
