import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Unmount React trees between tests so component state / SWR caches don't leak across cases.
afterEach(() => {
	cleanup();
});
