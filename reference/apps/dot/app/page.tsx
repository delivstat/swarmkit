import { BriefView } from "./brief-view";

// Live route (design/details/dot-app.md §Commit split step 4). React Query owns state on the
// client; SSR hands over an empty initial and the client hydrates against /api/brief.
export default function TodaysBrief() {
	return <BriefView initial={null} />;
}
