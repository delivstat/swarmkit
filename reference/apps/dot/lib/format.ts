// Small pure helpers used by the brief cards. Kept out of components so vitest can hit them.

export function formatBriefDate(iso: string, now = new Date()): string {
	const d = new Date(iso);
	const sameDay =
		d.getUTCFullYear() === now.getUTCFullYear() &&
		d.getUTCMonth() === now.getUTCMonth() &&
		d.getUTCDate() === now.getUTCDate();
	const fmt = new Intl.DateTimeFormat("en-US", {
		weekday: "long",
		month: "short",
		day: "numeric",
		timeZone: "UTC",
	});
	return sameDay ? `Today · ${fmt.format(d)}` : fmt.format(d);
}

export function formatTimeShort(iso: string): string {
	return new Intl.DateTimeFormat("en-US", {
		hour: "numeric",
		minute: "2-digit",
		timeZone: "UTC",
	}).format(new Date(iso));
}

export function relativeShort(iso: string, now = new Date()): string {
	const diffMs = new Date(iso).getTime() - now.getTime();
	const mins = Math.round(diffMs / 60_000);
	const abs = Math.abs(mins);
	if (abs < 60) return mins >= 0 ? `in ${abs}m` : `${abs}m ago`;
	const hours = Math.round(abs / 60);
	if (hours < 24) return mins >= 0 ? `in ${hours}h` : `${hours}h ago`;
	const days = Math.round(hours / 24);
	return mins >= 0 ? `in ${days}d` : `${days}d ago`;
}
